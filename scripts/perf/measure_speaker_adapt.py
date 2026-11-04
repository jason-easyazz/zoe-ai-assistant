#!/usr/bin/env python3
"""Offline measurement for guarded speaker-profile adaptation (services/zoe-data/speaker_adaptation.py).

Question answered: does adapting the stored profile from high-certainty everyday turns RAISE the owner's genuine
score distribution WITHOUT raising impostor scores, and does the drift bound hold when the adapter is fed
impostor-like updates (a poisoning attempt)?

Inputs
------
* ``--corpus``    the owner's real-voice corpus (default ``~/.zoe-voice-samples``, top-level ``*.wav``), replayed in
                  FILE-MTIME order so the simulated "days" are the real ones. The first ``--enroll-n`` clips become
                  the enrolled anchor (averaged and re-normalised exactly as ``/api/voice/enroll`` does).
* ``--impostors`` one or more directories of OTHER voices (repeatable): e.g. quarantined TV false-wakes, locally
                  generated Kokoro clips, bake-off clips another agent prepared. Their embeddings never enter the
                  adapter except in the explicit poisoning section.

Embeddings are computed with resemblyzer's ``VoiceEncoder`` (**CPU only**; LAB ONLY - zoe-data no longer ships resemblyzer, so run this from a scratch venv that has it). The encoder pulls
torch (~400 MB), so the script REFUSES to load it unless ``MemAvailable`` clears ``--min-avail-mb`` (default 1500) and
holds ``/tmp/zoe-voice-harness.lock`` (the voice harness lock) for the whole run. The floor applies before ANY encoder
load: a cold cache, a partial miss (a new ``--impostors`` arm, one replaced clip) and a stale cache all count.
Embeddings can be cached with ``--cache`` (an .npz OUTSIDE the repo: real embeddings are biometric data and are never
committed). The cache is BOUND to its inputs - per tag, a manifest of every file's (name, size, mtime_ns) plus the
encoder weights' hash; any mismatch is a miss - is written atomically and owner-only (0600), and a cache that is
group/world-readable or foreign-owned is refused, not read.

Sections
--------
1. prequential : stream the post-enrolment owner clips in time order; each clip is scored against the profile as it
                 stood BEFORE that clip (anchor-only baseline vs adapted), then offered to the policy.
2. holdout     : adapt on the first ``--train-frac`` of the stream, then score the unseen remainder against the
                 anchor vs the frozen adapted profile.
3. impostors   : every impostor clip scored against the anchor vs the adapted profile (FA rates at the live
                 thresholds), and offered to the policy as a worst case (corroboration granted, solo household).
4. poisoning   : (a) impostor embeddings fed as updates with every guard ON; (b) the same with the score / anchor /
                 margin / corroboration / rate guards DISABLED so only the drift bound stands; (c) the synthetic
                 worst case - an adversary that always sends the direction furthest from the anchor.

Usage::

    flock /tmp/zoe-voice-harness.lock python3 scripts/perf/measure_speaker_adapt.py \
        --impostors ~/.zoe-voice-samples/quarantine-tv-falsewakes-20260719 --impostors ~/kokoro-ear-check-2026-10-10 \
        --cache "$SCRATCH/emb.npz" --out "$SCRATCH/report.json"

``simulate`` / ``poison`` / ``summarise`` are pure (numpy only) and are exercised on synthetic data by
``tests/unit/test_speaker_adapt_measure.py``.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "services" / "zoe-data"))
import speaker_adaptation as sa  # noqa: E402

LOCK_PATH = "/tmp/zoe-voice-harness.lock"
THRESHOLDS = (0.70, 0.75, 0.82, 0.85)


# ── pure simulation (numpy only) ───────────────────────────────────────────

def enroll_anchor(embs: Sequence[np.ndarray]) -> np.ndarray:
    """The enrol route's profile: mean of the samples, re-normalised."""
    m = np.mean(np.stack([np.asarray(e, dtype=np.float32) for e in embs]), axis=0)
    return (m / (np.linalg.norm(m) + 1e-9)).astype(np.float32)


def _dist(xs: Sequence[float]) -> dict:
    a = np.asarray(list(xs), dtype=np.float64)
    if a.size == 0:
        return {"n": 0}
    out = {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
           "p10": float(np.percentile(a, 10)), "p25": float(np.percentile(a, 25)),
           "p90": float(np.percentile(a, 90)), "max": float(a.max())}
    for t in THRESHOLDS:
        out[f"ge_{t:.2f}"] = float((a >= t).mean())
    return out


@dataclasses.dataclass
class SimResult:
    adapted: Optional[np.ndarray]
    applied: int
    reasons: dict
    base_scores: list
    adapt_scores: list
    final_drift_cos: float


def simulate(embs: Sequence[np.ndarray], times: Sequence[datetime], speech_s: Sequence[float], anchor: np.ndarray, *,
             cfg: sa.AdaptConfig, others: Sequence[np.ndarray] = (), corroboration: Sequence[str] = ("panel_bound",),
             adapted: Optional[np.ndarray] = None, state: Optional[sa.AdaptState] = None) -> SimResult:
    """Stream clips through the REAL policy (``speaker_adaptation.evaluate``) in order."""
    state = state or sa.AdaptState()
    reasons: dict = {}
    base, adap, applied = [], [], 0
    for e, t, s in zip(embs, times, speech_s):
        cur = adapted if adapted is not None else anchor
        base.append(sa.cosine(e, anchor))
        adap.append(sa.cosine(e, cur))
        d = sa.evaluate(embedding=e, anchor=anchor, adapted=adapted, others=others, claimed_score=sa.cosine(e, cur),
                        speech_s=s, corroboration=corroboration, state=state, now=t, cfg=cfg)
        reasons[d.reason] = reasons.get(d.reason, 0) + 1
        if d.allowed:
            adapted = d.new_vec
            applied += 1
            day = t.strftime("%Y-%m-%d")
            state = sa.AdaptState(adapt_count=state.adapt_count + 1, adapted_at=t, day=day,
                                  day_count=state.day_count + 1 if state.day == day else 1)
    drift = sa.cosine(adapted, anchor) if adapted is not None else 1.0
    return SimResult(adapted, applied, reasons, base, adap, drift)


def unguarded(cfg: sa.AdaptConfig) -> sa.AdaptConfig:
    """Every guard EXCEPT the drift bound and alpha switched off - the poisoning worst case."""
    return dataclasses.replace(cfg, min_score=-2.0, anchor_floor=-2.0, min_margin=-2.0, min_speech_s=0.0,
                               max_per_day=10**9, min_interval_s=0.0)


def poison(anchor: np.ndarray, impostors: Sequence[np.ndarray], cfg: sa.AdaptConfig, *, rounds: int = 2000,
           seed: int = 0) -> dict:
    """Feed impostor-like updates and report how far the adapted profile moved. Time advances a day per update so the
    rate limits never bind in the unguarded arms (they are switched off there anyway)."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out: dict = {}

    def run(label: str, stream: Sequence[np.ndarray], c: sa.AdaptConfig, corr=("panel_bound",)) -> None:
        times = [t0 + _days(i) for i in range(len(stream))]
        r = simulate(stream, times, [5.0] * len(stream), anchor, cfg=c, corroboration=corr)
        out[label] = {"offered": len(stream), "applied": r.applied, "reasons": r.reasons,
                      "final_cos_to_anchor": r.final_drift_cos,
                      "bound_held": r.final_drift_cos >= c.min_anchor_cos - 1e-6}

    if len(impostors):
        cyc = [impostors[i % len(impostors)] for i in range(rounds)]
        run("impostors_all_guards_on", cyc, cfg)
        run("impostors_only_drift_bound", cyc, unguarded(cfg))
    # Adversary: always pulls toward one fixed direction ORTHOGONAL to the anchor - the direction that rotates the
    # profile fastest (the exact opposite of the anchor would only shrink it, and normalising undoes that).
    t = rng.standard_normal(anchor.shape).astype(np.float32)
    t -= float(np.dot(t, anchor)) * anchor
    t = (t / np.linalg.norm(t)).astype(np.float32)
    adv = [t] * rounds
    run("synthetic_adversary_only_drift_bound", adv, unguarded(cfg))
    return out


def _days(i: int):
    from datetime import timedelta
    return timedelta(days=i)


def summarise(genuine: Sequence[np.ndarray], g_times: Sequence[datetime], g_speech: Sequence[float],
              impostors: Sequence[np.ndarray], anchor: np.ndarray, cfg: sa.AdaptConfig, *,
              train_frac: float = 0.7) -> dict:
    rep: dict = {"config": dataclasses.asdict(cfg), "genuine_clips": len(genuine), "impostor_clips": len(impostors)}
    # 1. prequential
    pre = simulate(genuine, g_times, g_speech, anchor, cfg=cfg)
    rep["prequential"] = {"applied": pre.applied, "reasons": pre.reasons, "final_cos_to_anchor": pre.final_drift_cos,
                          "anchor_only": _dist(pre.base_scores), "adapted": _dist(pre.adapt_scores)}
    # 2. holdout
    k = int(len(genuine) * train_frac)
    tr = simulate(genuine[:k], g_times[:k], g_speech[:k], anchor, cfg=cfg)
    ho = genuine[k:]
    served = tr.adapted if tr.adapted is not None else anchor
    rep["holdout"] = {"train_clips": k, "applied": tr.applied, "final_cos_to_anchor": tr.final_drift_cos,
                      "anchor_only": _dist([sa.cosine(e, anchor) for e in ho]),
                      "adapted": _dist([sa.cosine(e, served) for e in ho])}
    # 3. impostors against the profile adapted on the full genuine stream
    full = pre.adapted if pre.adapted is not None else anchor
    imp_base = [sa.cosine(e, anchor) for e in impostors]
    imp_adapt = [sa.cosine(e, full) for e in impostors]
    rep["impostors"] = {"anchor_only": _dist(imp_base), "adapted": _dist(imp_adapt),
                        "mean_delta": float(np.mean(imp_adapt) - np.mean(imp_base)) if impostors else None,
                        "max_delta": float(np.max(np.asarray(imp_adapt) - np.asarray(imp_base))) if impostors else None}
    # impostor clips offered to the policy as if they were the owner, with corroboration granted (worst case)
    if impostors:
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        times = [t0 + _days(i) for i in range(len(impostors))]
        r = simulate(impostors, times, [5.0] * len(impostors), anchor, cfg=cfg, adapted=pre.adapted,
                     state=sa.AdaptState())
        rep["impostors"]["offered_as_owner"] = {"applied": r.applied, "reasons": r.reasons}
    # 4. poisoning
    rep["poisoning"] = poison(anchor, impostors, cfg)
    return rep


# ── corpus embedding (resemblyzer, CPU) ────────────────────────────────────

def _mem_available_mb() -> float:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


class HeadroomError(RuntimeError):
    """The encoder (torch) would be loaded with less than the MemAvailable floor free."""


class CacheError(RuntimeError):
    """The embedding cache is not safe to read (group/world-readable or foreign-owned)."""


#: MemAvailable floor (MB) enforced before ANY encoder load; ``main`` sets it from ``--min-avail-mb``.
MIN_AVAIL_MB = 1500.0
_ENCODER_ID: Optional[str] = None


def encoder_id() -> str:
    """Identity of the embedding model the cache was computed with: the resemblyzer weights file (size + sha256).
    Found WITHOUT importing resemblyzer/torch. A different encoder or weights is a different embedding space, so any
    change is a cache miss."""
    global _ENCODER_ID
    if _ENCODER_ID is None:
        import hashlib
        import importlib.util
        ident = "resemblyzer-VoiceEncoder-cpu:unknown-weights"
        try:
            spec = importlib.util.find_spec("resemblyzer")
            w = Path(spec.origin).parent / "pretrained.pt"
            ident = f"resemblyzer-VoiceEncoder-cpu:{w.stat().st_size}:{hashlib.sha256(w.read_bytes()).hexdigest()}"
        except Exception:
            pass
        _ENCODER_ID = ident
    return _ENCODER_ID


def embed_dir(paths: Sequence[Path], *, limit: Optional[int] = None) -> list:
    """[(path, mtime, embedding, speech_seconds)] for each wav. CPU only. Enforces the memory floor FIRST - this is
    the only place the encoder is loaded, so no path (cold cache, partial miss, stale cache) can skip it."""
    avail = _mem_available_mb()
    if avail < MIN_AVAIL_MB:
        raise HeadroomError(f"MemAvailable {avail:.0f} MB < {MIN_AVAIL_MB:.0f} MB")
    try:  # lazy: pulls resemblyzer/torch. LAB ONLY - zoe-data no longer ships either (retired 2026-10); install them in a scratch venv.
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
    except ImportError as exc:
        raise SystemExit(f"resemblyzer is not installed ({exc}); this legacy-scale harness needs it in a scratch venv, not zoe-data's")
    enc = VoiceEncoder(device="cpu", verbose=False)
    out = []
    for p in list(paths)[:limit]:
        try:
            wav = preprocess_wav(str(p))
            if len(wav) < 16000 * 0.5:
                continue
            out.append((p, p.stat().st_mtime, enc.embed_utterance(wav).astype(np.float32), len(wav) / 16000.0))
        except Exception as exc:  # one unreadable clip never sinks the run
            print(f"skip {p.name}: {exc}", file=sys.stderr)
    return out


def manifest(paths: Sequence[Path]) -> str:
    """The cache's binding to its INPUTS: ``[name, size, mtime_ns]`` per file plus the encoder id. Count alone is not
    a binding - replacing or editing clips without changing how many there are must be a miss."""
    rows = []
    for p in paths:
        st = p.stat()
        rows.append([p.name, st.st_size, st.st_mtime_ns])
    return json.dumps({"encoder": encoder_id(), "files": rows}, separators=(",", ":"))


def _read_cache(cache: Optional[Path]) -> dict:
    """The cache's arrays, or {} when there is no cache. REFUSES (CacheError) a cache that is readable by group or
    world, or owned by someone else: it holds biometric embeddings."""
    if not cache or not cache.exists():
        return {}
    st = cache.stat()
    if st.st_mode & 0o077 or st.st_uid != os.geteuid():
        raise CacheError(f"{cache} is mode {st.st_mode & 0o777:o} / uid {st.st_uid}: refusing to read a biometric "
                         f"embedding cache that is not private. chmod 600 it (or delete it) and re-run.")
    try:
        with np.load(cache, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}
    except Exception:
        return {}      # unreadable / old-format cache: a miss, rewritten below


def _cached_rows(data: dict, tag: str, paths: Sequence[Path], limit: Optional[int]) -> Optional[list]:
    """The cached rows for ``tag`` iff the cache covers EXACTLY these input files (name, size, mtime) with this
    encoder; otherwise None (a miss, for any reason)."""
    want = list(paths)[:limit]
    try:
        if f"{tag}_manifest" not in data or str(data[f"{tag}_manifest"]) != manifest(want):
            return None
        emb, mt, sp = data[f"{tag}_emb"], data[f"{tag}_mtime"], data[f"{tag}_speech"]
        if not (len(emb) == len(mt) == len(sp)) or len(emb) > len(want):
            return None
        return list(zip(mt.tolist(), emb, sp.tolist()))
    except (OSError, KeyError, ValueError):
        return None


def _write_cache(cache: Path, data: dict) -> None:
    """Atomic, owner-only write: a 0600 temp file (created exclusively, so the umask can only tighten it), fsync,
    rename over the cache, then an explicit chmod. Embeddings are never visible at a laxer mode, even briefly."""
    tmp = cache.with_name(f"{cache.name}.tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez(f, **data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, cache)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(cache, 0o600)


def _load_or_embed(tag: str, paths: Sequence[Path], cache: Optional[Path], limit: Optional[int]):
    data = _read_cache(cache)
    hit = _cached_rows(data, tag, paths, limit)
    if hit is not None:
        return hit
    rows = embed_dir(paths, limit=limit)
    if cache:
        want = list(paths)[:limit]
        data[f"{tag}_emb"] = np.stack([r[2] for r in rows]) if rows else np.zeros((0, 256), np.float32)
        data[f"{tag}_mtime"] = np.array([r[1] for r in rows])
        data[f"{tag}_speech"] = np.array([r[3] for r in rows])
        data[f"{tag}_manifest"] = np.array(manifest(want))
        _write_cache(cache, data)
    return [(r[1], r[2], r[3]) for r in rows]


def needs_encoder(specs: Sequence[tuple], cache: Optional[Path]) -> bool:
    """True iff ANY (tag, files, limit) spec is not fully covered by the cache - i.e. the encoder must load."""
    data = _read_cache(cache)
    return any(_cached_rows(data, tag, files, limit) is None for tag, files, limit in specs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=os.path.expanduser("~/.zoe-voice-samples"))
    ap.add_argument("--impostors", action="append", default=[])
    ap.add_argument("--enroll-n", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="cap owner clips (after sorting by mtime)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--cache", default=None, help="npz cache OUTSIDE the repo (embeddings are biometric data)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-avail-mb", type=float, default=1500.0)
    args = ap.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and _REPO in cache.resolve().parents:
        print("refusing to write embeddings inside the repo", file=sys.stderr)
        return 2
    lock = open(LOCK_PATH, "w")
    fcntl.flock(lock, fcntl.LOCK_EX)   # serialise with every other voice harness (two encoder loads OOM the box)
    global MIN_AVAIL_MB
    MIN_AVAIL_MB = args.min_avail_mb

    corpus = sorted(Path(args.corpus).expanduser().glob("*.wav"), key=lambda p: p.stat().st_mtime)
    imp_files = [sorted(Path(d).expanduser().glob("*.wav")) for d in args.impostors]
    specs = [("owner", corpus, args.limit)] + [(f"imp{i}", f, None) for i, f in enumerate(imp_files)]
    try:
        # Per-tag, per-file coverage decides whether the encoder loads - a partial miss (a new --impostors arm, a
        # replaced clip, a different encoder) needs the model exactly like a cold cache does.
        if needs_encoder(specs, cache):
            avail = _mem_available_mb()
            if avail < args.min_avail_mb:
                raise HeadroomError(f"MemAvailable {avail:.0f} MB < {args.min_avail_mb:.0f} MB")
        owner = _load_or_embed("owner", corpus, cache, args.limit)
        if len(owner) <= args.enroll_n + 10:
            print("not enough owner clips", file=sys.stderr)
            return 2
        anchor = enroll_anchor([e for _, e, _ in owner[: args.enroll_n]])
        rest = owner[args.enroll_n:]
        g_embs = [e for _, e, _ in rest]
        g_times = [datetime.fromtimestamp(m, tz=timezone.utc) for m, _, _ in rest]
        g_speech = [s for _, _, s in rest]

        imps: list = []
        for i, files in enumerate(imp_files):
            imps += [e for _, e, _ in _load_or_embed(f"imp{i}", files, cache, None)]
    except HeadroomError as exc:
        print(f"{exc}: refusing to load the encoder (torch) next to the live voice stack. Re-run when the box is "
              f"quiet, or use --cache.", file=sys.stderr)
        return 3
    except CacheError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rep = summarise(g_embs, g_times, g_speech, imps, anchor, sa.AdaptConfig.from_env(), train_frac=args.train_frac)
    rep["enroll_clips"] = args.enroll_n
    print(json.dumps(rep, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

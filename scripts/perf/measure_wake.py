#!/usr/bin/env python3
"""Wake-word FAR/FRR instrument for the panel's "Hey Zoe" (deep dive 2, V-4).

The live panel fires on ``hey_zoe.onnx`` (openWakeWord 0.6.0, ONNX) at ``WAKEWORD_THRESHOLD=0.45`` with 3 hits inside
1.0 s and 5 s between wakes; its own near-misses top out at 0.44. Nothing measured what moving that threshold costs.
This replays the SAME model files over local audio, frame by frame exactly as the daemon feeds it (1280-sample int16
chunks), runs the daemon's confirmation state machine at every threshold on a grid, and reports:

* **positives** - the panel's own wake turns from ``~/.zoe-voice-samples``, found by joining each clip to its
  ``Recorded command`` line in the Pi's ``voice.log`` (exact duration match). Every one of them DID wake live, so this is
  the cost side: the share of today's real wakes a higher threshold would lose. It cannot see attempts that never woke
  (the near-miss retry proxy below is the only window on those).
* **negatives** - audio that must not wake it: the quarantined TV false-wake clips (must be 0/N), public speech
  (LibriSpeech crops and Kokoro TTS voices from the speaker bake-off scratch), the Omi pendant's household recordings,
  and - reported apart - the panel's own follow-up turns (household speech at the panel mic, no wake phrase needed).
* **live near-misses** - ``nearmiss`` parses ``voice.log`` (no model): the near-miss score distribution since a date, the
  wake outcomes (turn / empty chime / nothing), wakes at 01:00-05:00, and near-misses followed by a wake 1.5-10 s later.

Recommendation rule (``recommend``): the HIGHEST threshold on the grid whose positive replay loses at most
``--frr-budget`` (default 5 percentage points) more real wakes than the live threshold does in the same replay (so the
replay's own losses cancel) and wakes on none of the TV clips. Changing the live threshold is an OWNER / voice-path step
(the Pi's ``.env.voice``): this script never touches the panel.

Sub-commands:

    nearmiss   read voice.log (ssh read-only, or --voice-log FILE); no model, any hour
    run        replay the model; writes scores.json under --out (outside the repo, mode 600), then reports
    report     re-render the curve + recommendation from a saved scores.json (no model)

Guards for ``run`` (this box runs the live voice brain): CUDA hidden; harness quiet hours (07:00-22:00,
``scripts/maintenance/harness_quiet.py``; ``--force`` / ``ZOE_HARNESS_FORCE=1`` to override deliberately);
``MemAvailable`` >= ``--min-avail-mb`` (default 1500); the model and feature files must match ``models.lock``. Run it
niced inside the voice-harness lock:

    flock -x -w 3600 /tmp/zoe-voice-harness.lock nice -n 10 python3 scripts/perf/measure_wake.py run

One-time setup (outside the repo; the feature models are the PANEL's bytes, sha-pinned in models.lock):

    python3 -m pip install --no-deps --target ~/.cache/zoe-wake-fa/pylib openwakeword==0.6.0
    scp zoe-pi:.zoe-voice/venv/lib/python3.11/site-packages/openwakeword/resources/models/melspectrogram.onnx \
        zoe-pi:.zoe-voice/venv/lib/python3.11/site-packages/openwakeword/resources/models/embedding_model.onnx \
        ~/.cache/zoe-wake-fa/features/

Household audio never leaves the box and never enters the repo; scores.json holds file paths and frame scores only.
Runbook and results: docs/knowledge/wake-and-speaker-fa-instruments.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE.parents[1] / "maintenance"))

import panel_voice_log as pvl  # noqa: E402

SR = 16000
CHUNK = 1280                      # the daemon's CHUNK_SIZE: one openWakeWord frame = 80 ms
FRAME_S = CHUNK / SR
LIVE = dict(threshold=0.45, confirm_count=3, confirm_window_s=1.0, min_interval_s=5.0)   # Pi .env.voice, 2026-10-19
GRID = tuple(round(0.20 + 0.01 * i, 2) for i in range(61))                             # 0.20 .. 0.80
SHOW = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
PREROLL_20_SINCE = dt.datetime(2026, 7, 16)   # #1326 (07-15): 1.6 s pre-roll; before it 0.96 s began inside the phrase
NEARMISS_SINCE = dt.datetime(2026, 10, 16)

HOME = Path.home()
DEFAULT_OUT = HOME / ".zoe" / "voice-gate" / "wake"
DEFAULT_MODEL = HOME / ".zoe" / "backups" / "panel" / "hey_zoe.onnx"
DEFAULT_FEATURES = HOME / ".cache" / "zoe-wake-fa" / "features"
DEFAULT_OWW_PATH = HOME / ".cache" / "zoe-wake-fa" / "pylib"
CORPUS = Path(pvl.CORPUS_DIR)
BAKEOFF = HOME / ".cache" / "zoe-speaker-bakeoff" / "impostors"
DEFAULT_NEGATIVES = (
    ("tv", CORPUS / "quarantine-tv-falsewakes-20260719"),
    ("libri", BAKEOFF / "libri"),
    ("kokoro", BAKEOFF / "kokoro"),
    ("omi", HOME / ".zoe" / "omi-recordings"),
)
POOLED = ("libri", "kokoro", "omi")          # the FA-per-hour objective; tv is a hard 0/N constraint
AUDIO_EXT = (".wav", ".flac")


# --------------------------------------------------------------------------------------------- the daemon's logic

def confirmed_wakes(frames: Sequence[float], threshold: float, *, confirm_count: int = LIVE["confirm_count"],
                    window_s: float = LIVE["confirm_window_s"], min_interval_s: float = LIVE["min_interval_s"],
                    frame_s: float = FRAME_S) -> list:
    """Wake times (s) the daemon's main loop would fire on this frame-score trace - a line-for-line port of
    ``zoe_voice_daemon.py``'s confirmation block (``wake_hits`` / ``wake_first_hit_at`` / ``MIN_WAKE_INTERVAL_S``).
    Note it is NOT "any 3 hits within window_s": a partial count expires once the window has passed since its FIRST
    hit, and a hit after that restarts the count from 1."""
    out: list = []
    hits, first, last_wake = 0, 0.0, -1e9
    for i, s in enumerate(frames):
        now = i * frame_s
        if s >= threshold:
            if hits == 0:
                hits, first = 1, now
            elif (now - first) <= window_s:
                hits += 1
            else:
                hits, first = 1, now
        elif hits and (now - first) > window_s:
            hits = 0
        if hits >= confirm_count:
            hits = 0
            if (now - last_wake) < min_interval_s:
                continue
            last_wake = now
            out.append(now)
    return out


def peak(frames: Sequence[float]) -> float:
    return max(frames) if frames else 0.0


def curve(clips: Sequence[dict], grid: Iterable[float] = GRID, *, live: dict = LIVE) -> list:
    """One row per threshold. ``clips`` = [{set, seconds, frames}]. For the ``positive`` set: FRR = share of clips with
    no confirmed wake. For every other set: wakes, hours, wakes per hour and the share of clips that woke."""
    sets = sorted({c["set"] for c in clips})
    rows = []
    for t in grid:
        row: dict = dict(threshold=round(float(t), 2), sets={})
        for name in sets:
            mine = [c for c in clips if c["set"] == name]
            woke = [len(confirmed_wakes(c["frames"], t, confirm_count=live["confirm_count"],
                                        window_s=live["confirm_window_s"],
                                        min_interval_s=live["min_interval_s"])) for c in mine]
            n = len(mine)
            hours = sum(float(c["seconds"]) for c in mine) / 3600.0
            n_woke = sum(1 for w in woke if w > 0)
            row["sets"][name] = dict(n=n, hours=hours, wakes=int(sum(woke)), clips_woke=n_woke,
                                     share_woke=(n_woke / n) if n else float("nan"),
                                     per_hour=(sum(woke) / hours) if hours > 0 else float("nan"))
        pos = row["sets"].get("positive")
        row["frr"] = (1.0 - pos["share_woke"]) if pos and pos["n"] else float("nan")
        pooled = [row["sets"][s] for s in POOLED if s in row["sets"]]
        hrs = sum(s["hours"] for s in pooled)
        row["pooled_fa_per_hour"] = (sum(s["wakes"] for s in pooled) / hrs) if hrs > 0 else float("nan")
        row["pooled_hours"] = hrs
        rows.append(row)
    return rows


def _row_at(rows: Sequence[dict], t: float) -> Optional[dict]:
    for r in rows:
        if abs(r["threshold"] - t) < 1e-9:
            return r
    return None


def _tv(row: dict) -> str:
    tv = row["sets"].get("tv")
    return f"{tv['clips_woke']}/{tv['n']}" if tv else "n/a"


MIN_POSITIVES = 30


def highest_within(rows: Sequence[dict], base: dict, budget: float) -> Optional[dict]:
    """The highest-threshold row whose FRR exceeds ``base``'s by at most ``budget`` and that wakes on no ``tv`` clip."""
    ok = [r for r in rows
          if not (r["sets"].get("tv") or {}).get("clips_woke") and r["frr"] - base["frr"] <= budget + 1e-12]
    return max(ok, key=lambda r: r["threshold"]) if ok else None


def recommend(rows: Sequence[dict], *, live_threshold: float = LIVE["threshold"], frr_budget: float = 0.05,
              min_positives: int = MIN_POSITIVES) -> dict:
    """The highest threshold whose replayed FRR exceeds the live threshold's replayed FRR by at most ``frr_budget`` and
    which wakes on none of the ``tv`` clips. Returns {threshold, reason, options, ...}; threshold None when nothing
    qualifies or there are too few positives to say. ``options`` gives the same rule at 1 / 2 / 5 pp for the owner."""
    base = _row_at(rows, live_threshold)
    if base is None or math.isnan(base["frr"]):
        return dict(threshold=None, reason="no positive clips scored at the live threshold - nothing to compare")
    n_pos = base["sets"]["positive"]["n"]
    if n_pos < min_positives:
        return dict(threshold=None, live_threshold=live_threshold, live_frr=base["frr"], live_tv=_tv(base),
                    reason=f"only {n_pos} positive clips (need {min_positives}) - too few to move a live threshold")
    options = {}
    for b in (0.01, 0.02, 0.05):
        r = highest_within(rows, base, b)
        options[str(b)] = None if r is None else r["threshold"]
    best = highest_within(rows, base, frr_budget)
    if best is None:
        return dict(threshold=None, live_threshold=live_threshold, live_frr=base["frr"], live_tv=_tv(base),
                    options=options,
                    reason="no threshold meets both the FRR budget and TV 0/N - the model, not the threshold, is the fix")
    same = abs(best["threshold"] - live_threshold) < 1e-9
    return dict(threshold=best["threshold"], live_threshold=live_threshold, live_frr=base["frr"], frr=best["frr"],
                options=options, n_positives=n_pos, frr_budget=frr_budget, pooled_fa_per_hour=best["pooled_fa_per_hour"],
                live_pooled_fa_per_hour=base["pooled_fa_per_hour"], tv=_tv(best), live_tv=_tv(base),
                reason=("the live threshold is already the highest that meets the budget" if same
                        else "highest threshold within the FRR budget with TV 0/N"))



# --------------------------------------------------------------------------------------------- live near-misses

NEAR_EDGES = tuple(round(0.18 + 0.02 * i, 2) for i in range(15))       # 0.18 .. 0.46


def nearmiss_summary(evs: Sequence, *, since: dt.datetime, until: Optional[dt.datetime] = None) -> dict:
    """What the live log says about the band under the threshold. CAVEAT the daemon's own logging: a near-miss line is
    the FIRST frame >= 0.18 after a 12 s cooldown, not the episode's peak, and a frame that crosses the threshold but
    fails the 3-hit confirmation is logged nowhere - so ``max`` is a lower bound on how close background gets."""
    ev = [e for e in evs if e.ts >= since and (until is None or e.ts < until)]
    nm = [e for e in ev if e.kind == "near_miss"]
    scores = [e.data["score"] for e in nm]
    wakes = [e for e in ev if e.kind == "wake"]
    wake_turns = [t for t in pvl.turns(evs) if t.trigger == "wake" and t.trigger_ts >= since
                  and (until is None or t.trigger_ts < until)]
    outcomes: dict = {}
    for t in wake_turns:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    hist = []
    for lo, hi in zip(NEAR_EDGES[:-1], NEAR_EDGES[1:]):
        hist.append(dict(lo=lo, hi=hi, n=sum(1 for s in scores if lo <= s < hi)))
    by_hour: dict = {}
    for e in nm:
        by_hour[e.ts.hour] = by_hour.get(e.ts.hour, 0) + 1
    wake_ts = sorted(e.ts for e in wakes)
    retry = ramp = 0
    for e in nm:
        gaps = [(w - e.ts).total_seconds() for w in wake_ts if 0 <= (w - e.ts).total_seconds() <= 10.0]
        if gaps:
            if min(gaps) < 1.5:
                ramp += 1
            else:
                retry += 1
    end = until or (ev[-1].ts if ev else since)
    days = max((end - since).total_seconds() / 86400.0, 1e-9)
    return dict(since=since.isoformat(sep=" "), until=end.isoformat(sep=" "), days=round(days, 2),
                near_misses=len(scores), per_day=round(len(scores) / days, 2),
                need=sorted({e.data["need"] for e in nm}),
                max=max(scores) if scores else None,
                at_or_above={str(t): sum(1 for s in scores if s >= t) for t in (0.30, 0.35, 0.40, 0.42, 0.44)},
                top=sorted(scores, reverse=True)[:10], histogram=hist,
                by_hour=dict(sorted(by_hour.items())), wakes=len(wakes), wake_outcomes=outcomes,
                night_wakes_01_05=sum(1 for e in wakes if 1 <= e.ts.hour < 5),
                retry_after_near_miss=retry, ramp_into_wake=ramp)



# --------------------------------------------------------------------------------------------- models, audio, sets

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def lock_entry(basename: str, lock_path: Path = REPO / "models.lock") -> Optional[dict]:
    """The models.lock entry whose path ends in ``basename`` (the panel host's copy)."""
    try:
        models = json.loads(Path(lock_path).read_text()).get("models", [])
    except (OSError, ValueError):
        return None
    for m in models:
        if str(m.get("path", "")).rsplit("/", 1)[-1] == basename and m.get("host") == "panel":
            return m
    return None


def verify_pinned(path: Path, lock_path: Path = REPO / "models.lock") -> Optional[str]:
    """None when ``path`` matches its models.lock sha256, else why not. A replay on other bytes measures another model."""
    if not Path(path).is_file():
        return f"{path} not found"
    entry = lock_entry(Path(path).name, lock_path)
    if entry is None:
        return f"{Path(path).name} has no panel entry in models.lock"
    got = sha256(Path(path))
    if got != entry.get("sha256"):
        return f"{path} sha256 {got[:12]} != models.lock {str(entry.get('sha256'))[:12]}"
    return None


def load_audio(path: str):
    """int16 mono at 16 kHz (the daemon's input format)."""
    import numpy as np
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    x = data.mean(axis=1)
    if sr != SR:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g)
    return np.clip(np.round(np.asarray(x) * 32767.0), -32768, 32767).astype(np.int16)


class OwwScorer:
    """openWakeWord's ``Model`` on the pinned files, fed exactly like the daemon. ``reset()`` refills the feature buffer
    with ``np.random`` noise, so the RNG is seeded first (a replay is deterministic); ``lead_s`` of digital silence then
    flushes that noise out of the 1.28 s feature window and ``tail_s`` lets the score ramp finish."""

    def __init__(self, model_path: Path, features_dir: Path, *, model_factory: Optional[Callable] = None):
        if model_factory is None:
            from openwakeword.model import Model as model_factory  # noqa: N813
        self.model = model_factory(wakeword_models=[str(model_path)], inference_framework="onnx",
                                   melspec_model_path=str(Path(features_dir) / "melspectrogram.onnx"),
                                   embedding_model_path=str(Path(features_dir) / "embedding_model.onnx"))

    def frames(self, x, *, lead_s: float = 2.0, tail_s: float = 1.0, seed: int = 0) -> list:
        import numpy as np
        np.random.seed(seed)
        self.model.reset()
        data = np.concatenate([np.zeros(int(lead_s * SR), np.int16), np.asarray(x, dtype=np.int16),
                               np.zeros(int(tail_s * SR), np.int16)])
        out = []
        for i in range(0, len(data) - CHUNK + 1, CHUNK):
            pred = self.model.predict(data[i:i + CHUNK])
            out.append(round(float(max(pred.values())) if pred else 0.0, 4))
        return out


def panel_clips(lines: Sequence[str], corpus_dir: str, *, positives_since: dt.datetime = PREROLL_20_SINCE) -> dict:
    """{positive: [...], followup: [...], excluded_old: n}. Wake turns captured before the 1.6 s pre-roll landed are
    excluded (their clip can begin inside the phrase, which would read as a false reject)."""
    tns = pvl.turns(pvl.events(lines))
    clips = pvl.corpus_index(corpus_dir)
    matched = pvl.match_clips(tns, clips)
    out: dict = dict(positive=[], followup=[], excluded_old=0)
    for ti, c in sorted(matched.items(), key=lambda kv: kv[1].ts):
        trig = tns[ti].trigger
        if trig == "wake":
            if c.ts < positives_since:
                out["excluded_old"] += 1
            else:
                out["positive"].append(c.path)
        elif trig == "followup":
            out["followup"].append(c.path)
    return out


def dir_clips(d: Path) -> list:
    d = Path(d).expanduser()
    if not d.is_dir():
        return []
    return sorted(str(p) for p in d.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT)


def mem_available_mb(path: str = "/proc/meminfo") -> Optional[float]:
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def guard(*, force: bool, min_avail_mb: float, check: Optional[Callable] = None,
          mem: Optional[Callable[[], Optional[float]]] = None) -> Optional[str]:
    """None when a replay may start now, else the refusal: harness quiet hours / panel in use, then memory."""
    if check is None:
        import harness_quiet
        check = harness_quiet.check
    v = check("measure_wake", force=True if force else None)
    if not v.allowed:
        return v.reason
    m = (mem or mem_available_mb)()
    if m is not None and m < min_avail_mb:
        return f"MemAvailable {m:.0f} MB < {min_avail_mb:.0f} MB - the live voice stack comes first"
    return None



# --------------------------------------------------------------------------------------------- reporting

def _pct(v: Optional[float], digits: int = 1) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100.0 * v:.{digits}f} %"


def _num(v: Optional[float], digits: int = 2) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{digits}f}"


def quantiles(vals: Sequence[float], qs: Sequence[float] = (0.1, 0.5, 0.9, 0.99)) -> dict:
    s = sorted(vals)
    if not s:
        return {}
    return {str(q): s[min(len(s) - 1, int(round(q * (len(s) - 1))))] for q in qs}


def render_curve(rows: Sequence[dict], show: Sequence[float] = SHOW) -> str:
    names = [n for n in ("tv", "libri", "kokoro", "omi", "followup") if any(n in r["sets"] for r in rows)]
    head = "| threshold | positive FRR (k/n) | " + " | ".join(
        (f"{n} woke" if n in ("tv", "followup") else f"{n} FA/h") for n in names) + " | pooled FA/h |"
    out = [head, "|" + "---|" * (3 + len(names))]
    for t in show:
        r = _row_at(rows, t)
        if r is None:
            continue
        pos = r["sets"].get("positive")
        k = (pos["n"] - pos["clips_woke"]) if pos else 0
        cells = [f"{r['threshold']:.2f}", f"{_pct(r['frr'])} ({k}/{pos['n'] if pos else 0})"]
        for n in names:
            s = r["sets"].get(n)
            if s is None:
                cells.append("-")
            elif n in ("tv", "followup"):
                cells.append(f"{s['clips_woke']}/{s['n']}")
            else:
                cells.append(f"{_num(s['per_hour'])} ({s['wakes']} in {s['hours']:.2f} h)")
        cells.append(_num(r["pooled_fa_per_hour"]))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def render_peaks(clips: Sequence[dict]) -> str:
    out = ["| set | clips | hours | peak p10 | p50 | p90 | p99 | max |", "|---|---|---|---|---|---|---|---|"]
    for name in sorted({c["set"] for c in clips}):
        mine = [c for c in clips if c["set"] == name]
        pk = [peak(c["frames"]) for c in mine]
        q = quantiles(pk)
        hrs = sum(float(c["seconds"]) for c in mine) / 3600.0
        out.append(f"| {name} | {len(mine)} | {hrs:.2f} | " + " | ".join(_num(q.get(k), 3) for k in ("0.1", "0.5", "0.9", "0.99"))
                   + f" | {_num(max(pk) if pk else None, 3)} |")
    return "\n".join(out)


def render_nearmiss(s: dict) -> str:
    lines = [f"Live near-misses {s['since']} .. {s['until']} ({s['days']} days): **{s['near_misses']}** "
             f"({s['per_day']}/day), threshold(s) {s['need']}, max **{_num(s['max'], 4)}**; "
             f"at or above 0.40: {s['at_or_above'].get('0.4')}, 0.44: {s['at_or_above'].get('0.44')}.",
             f"Wakes: {s['wakes']}; outcomes {s['wake_outcomes']}; wakes 01:00-05:00: {s['night_wakes_01_05']}; "
             f"near-miss then a wake 1.5-10 s later (retry proxy): {s['retry_after_near_miss']}; within 1.5 s (ramp): "
             f"{s['ramp_into_wake']}.",
             "", "| score band | near-misses |", "|---|---|"]
    lines += [f"| {b['lo']:.2f}-{b['hi']:.2f} | {b['n']} |" for b in s["histogram"] if b["n"]]
    lines += ["", "By hour: " + ", ".join(f"{h:02d}h {n}" for h, n in s["by_hour"].items())]
    return "\n".join(lines)


def render_recommendation(rec: dict) -> str:
    if rec.get("threshold") is None:
        return f"**No recommendation:** {rec.get('reason')}"
    return (f"**Recommended WAKEWORD_THRESHOLD = {rec['threshold']:.2f}** (live {rec['live_threshold']:.2f}): "
            f"replayed FRR {_pct(rec['frr'])} vs {_pct(rec['live_frr'])} at live (budget +{_pct(rec['frr_budget'], 0)}), "
            f"TV {rec['tv']} (live {rec['live_tv']}), pooled public/household FA "
            f"{_num(rec['pooled_fa_per_hour'])}/h vs {_num(rec['live_pooled_fa_per_hour'])}/h. {rec['reason']}. "
            + "Highest threshold by FRR budget: " + _options(rec.get("options"))
            + ". An owner / voice-path change (the Pi's .env.voice), replay-gated - not applied by this script.")


def _options(opts: Optional[dict]) -> str:
    return ", ".join("+%.0f pp -> %s" % (float(b) * 100, _num(t)) for b, t in (opts or {}).items()) or "n/a"



# --------------------------------------------------------------------------------------------- commands

def _private_dir(d: Path) -> Path:
    d = Path(d).expanduser().resolve()
    if d == REPO or REPO in d.parents:
        raise SystemExit(f"refusing to write household-derived data inside the repo: {d}")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_private(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def _live_from(args) -> dict:
    return dict(threshold=args.threshold, confirm_count=args.confirm_count, confirm_window_s=args.confirm_window,
                min_interval_s=args.min_interval)


def build_report(clips: Sequence[dict], *, live: dict, frr_budget: float, near: Optional[dict] = None) -> tuple:
    rows = curve(clips, GRID, live=live)
    rec = recommend(rows, live_threshold=live["threshold"], frr_budget=frr_budget)
    md = ["## Wake word FAR/FRR (hey_zoe.onnx replay)", "",
          f"Confirmation as live: {live['confirm_count']} hits within {live['confirm_window_s']} s, "
          f"{live['min_interval_s']} s between wakes.", "", render_curve(rows), "", "### Clip peak scores", "",
          render_peaks(clips), "", "### Recommendation", "", render_recommendation(rec)]
    if near:
        md += ["", "### Live near-misses (panel voice.log)", "", render_nearmiss(near)]
    return rows, rec, "\n".join(md)


def cmd_nearmiss(args) -> int:
    lines = pvl.fetch_lines(args.voice_log)
    s = nearmiss_summary(pvl.events(lines), since=args.since)
    print(render_nearmiss(s))
    if args.json:
        out = _private_dir(Path(args.json).parent)
        _write_private(out / Path(args.json).name, json.dumps(s, indent=1, default=str))
    return 0


def cmd_report(args) -> int:
    data = json.loads(Path(args.scores).expanduser().read_text())
    near = None
    if not args.no_nearmiss:
        try:
            near = nearmiss_summary(pvl.events(pvl.fetch_lines(args.voice_log)), since=args.since)
        except (RuntimeError, OSError) as exc:
            print(f"(near-miss section skipped: {exc})", file=sys.stderr)
    _rows, rec, md = build_report(data["clips"], live=_live_from(args), frr_budget=args.frr_budget, near=near)
    print(md)
    return 0



def _sets(args, lines: Sequence[str]) -> tuple:
    """[(set, path)] to score, plus notes for the report."""
    todo: list = []
    notes: dict = {}
    pc = panel_clips(lines, args.corpus, positives_since=args.positives_since)
    notes["positives_excluded_pre_preroll"] = pc["excluded_old"]
    todo += [("positive", c) for c in pc["positive"]]
    if not args.no_followups:
        todo += [("followup", c) for c in pc["followup"]]
    negs = [tuple(x.split("=", 1)) for x in args.negatives] if args.negatives else \
        [(n, str(d)) for n, d in DEFAULT_NEGATIVES]
    for name, d in negs:
        files = dir_clips(Path(d))
        notes[f"{name}_dir_found"] = bool(files)
        todo += [(name, f) for f in files]
    if args.limit:
        capped, seen = [], {}
        for s, f in todo:
            if seen.get(s, 0) < args.limit:
                capped.append((s, f))
                seen[s] = seen.get(s, 0) + 1
        todo = capped
    return todo, notes


def cmd_run(args) -> int:
    why = guard(force=args.force, min_avail_mb=args.min_avail_mb)
    if why:
        print(f"measure_wake: refused - {why}", file=sys.stderr)
        return 3
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    model, feats = Path(args.model).expanduser(), Path(args.features_dir).expanduser()
    if not args.allow_unpinned:
        for f in (model, feats / "melspectrogram.onnx", feats / "embedding_model.onnx"):
            bad = verify_pinned(f)
            if bad:
                print(f"measure_wake: refused - {bad} (see the module docstring for setup)", file=sys.stderr)
                return 2
    sys.path.insert(0, str(Path(args.oww_path).expanduser()))
    out = _private_dir(Path(args.out))
    lines = pvl.fetch_lines(args.voice_log)
    todo, notes = _sets(args, lines)
    scorer = OwwScorer(model, feats)
    clips: list = []
    t0 = time.time()
    for i, (name, path) in enumerate(todo):
        if i % 25 == 0:
            waited = 0
            while (mem_available_mb() or 1e9) < args.min_avail_mb:
                if waited >= 600:
                    print("measure_wake: memory stayed low for 10 min - stopping, partial results kept", file=sys.stderr)
                    todo = todo[:i]
                    break
                time.sleep(30)
                waited += 30
            if i >= len(todo):
                break
            print(f"  {i}/{len(todo)} clips, {time.time() - t0:.0f}s", file=sys.stderr)
        try:
            x = load_audio(path)
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not cost the run
            notes.setdefault("unreadable", 0)
            notes["unreadable"] += 1
            print(f"  skip unreadable {Path(path).name}: {type(exc).__name__}", file=sys.stderr)
            continue
        clips.append(dict(set=name, path=path, seconds=round(len(x) / SR, 3),
                          frames=scorer.frames(x, lead_s=args.lead_s, tail_s=args.tail_s)))
    import onnxruntime
    meta = dict(model_sha256=sha256(model), melspec_sha256=sha256(feats / "melspectrogram.onnx"),
                embedding_sha256=sha256(feats / "embedding_model.onnx"), onnxruntime=onnxruntime.__version__,
                lead_s=args.lead_s, tail_s=args.tail_s, seconds=round(time.time() - t0, 1),
                finished=dt.datetime.now().isoformat(timespec="seconds"), notes=notes)
    _write_private(out / "scores.json", json.dumps(dict(meta=meta, clips=clips)))
    near = nearmiss_summary(pvl.events(lines), since=args.since)
    rows, rec, md = build_report(clips, live=_live_from(args), frr_budget=args.frr_budget, near=near)
    md += "\n\nRun: " + json.dumps(meta, default=str)
    _write_private(out / "report.md", md + "\n")
    _write_private(out / "report.json", json.dumps(dict(meta=meta, recommendation=rec, nearmiss=near,
                                                        curve=rows), default=str, indent=1))
    print(md)
    print(f"\nwrote {out}/scores.json, report.md, report.json", file=sys.stderr)
    return 0



def _date(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, live: bool = False):
        p.add_argument("--voice-log", help="a local copy of the panel's voice.log (default: read it over ssh)")
        p.add_argument("--since", type=_date, default=NEARMISS_SINCE, help="near-miss window start (local)")
        if live:
            p.add_argument("--threshold", type=float, default=LIVE["threshold"], help="the LIVE threshold")
            p.add_argument("--confirm-count", type=int, default=LIVE["confirm_count"])
            p.add_argument("--confirm-window", type=float, default=LIVE["confirm_window_s"])
            p.add_argument("--min-interval", type=float, default=LIVE["min_interval_s"])
            p.add_argument("--frr-budget", type=float, default=0.05,
                           help="extra real wakes a recommended threshold may lose vs the live one (0.05 = 5 pp)")

    n = sub.add_parser("nearmiss", help="live near-miss distribution + wake outcomes from voice.log (no model)")
    common(n)
    n.add_argument("--json", help="also write the summary here (outside the repo, mode 600)")

    r = sub.add_parser("run", help="replay hey_zoe.onnx over positives + negatives (guarded)")
    common(r, live=True)
    r.add_argument("--model", default=str(DEFAULT_MODEL))
    r.add_argument("--features-dir", default=str(DEFAULT_FEATURES))
    r.add_argument("--oww-path", default=str(DEFAULT_OWW_PATH), help="where openwakeword 0.6.0 is installed")
    r.add_argument("--corpus", default=str(CORPUS))
    r.add_argument("--negatives", action="append", default=[], metavar="NAME=DIR",
                   help="a negative set (repeatable; replaces the defaults). Names libri/kokoro/omi are pooled; "
                        "tv is the 0/N constraint")
    r.add_argument("--no-followups", action="store_true", help="skip the panel's follow-up turns")
    r.add_argument("--positives-since", type=_date, default=PREROLL_20_SINCE)
    r.add_argument("--limit", type=int, default=0, help="at most N clips per set (a smoke run)")
    r.add_argument("--lead-s", type=float, default=2.0)
    r.add_argument("--tail-s", type=float, default=1.0)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--min-avail-mb", type=float, default=1500.0)
    r.add_argument("--force", action="store_true", help="skip the harness quiet hours (deliberate)")
    r.add_argument("--allow-unpinned", action="store_true", help="replay files that do not match models.lock")

    rp = sub.add_parser("report", help="re-render a saved run (no model)")
    common(rp, live=True)
    rp.add_argument("--scores", default=str(DEFAULT_OUT / "scores.json"))
    rp.add_argument("--no-nearmiss", action="store_true")

    args = ap.parse_args(argv)
    return dict(nearmiss=cmd_nearmiss, run=cmd_run, report=cmd_report)[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

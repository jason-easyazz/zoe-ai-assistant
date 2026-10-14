#!/usr/bin/env python3
"""A LOCAL speech-to-text accuracy score from Zoe's own voice log, and the Moonshine keyterm A/B over the clips it labels.

Moonshine (the STT rock) exposes no confidence, so the only accuracy signal the box has is the household itself: a turn
the struggle detector calls a MISHEAR or a RE_ASK, followed within ~30 s by the user saying it again, is a weakly-labelled
pair - (what Moonshine heard, what the person meant ~= their repeat). This script collects those pairs and scores them.

    stt_accuracy_report.py                          # weekly proxy word-error / turn-mishear rate -> ~/.zoe/stt-accuracy/
    stt_accuracy_report.py --weeks 4 --no-db        # labels from the log alone (no struggle_events read)
    stt_accuracy_report.py --keyterm-ab "Zoe,kitchen light,timer"
                                                    # + replay the labelled clips with and without that keyterm list

Where the labels come from (both, de-duplicated; a pair found by both is source "both"):
  * the log itself: consecutive transcripts on one panel <= ``--window-s`` apart, judged by the detector's own pure pair rule
    (``struggle_signals.detect``) - MISHEAR or RE_ASK about the first turn;
  * ``struggle_events`` (read-only, needs the service's ``POSTGRES_URL``; skipped with a note when it cannot be read): a voice
    MISHEAR / RE_ASK row is mapped back to its two transcripts by panel and time.

Scores, per ISO week (UTC): ``turns`` (live voice transcripts), ``pairs`` (labelled), ``mishears`` (pairs whose two
transcripts DIFFER after normalisation - an identical repeat means the words were heard right and something else failed),
``mishear_rate`` = mishears / turns, ``proxy_wer`` = word edits / repeat words over the mishear pairs. A PROXY: the repeat
is not a reference transcript (people rephrase), so read it as a trend, not an absolute word-error rate.

Keyterm A/B (``--keyterm-ab``): the clip behind each labelled misheard turn (``~/.zoe-voice-samples``, matched by file time;
only present when ``ZOE_VOICE_SAVE_AUDIO`` captured it) is transcribed twice by the SERVICE's own Moonshine path
(``routers.voice_tts._run_moonshine``) in a child process on the service interpreter - once with ``ZOE_MOONSHINE_KEYTERMS``
unset, once with the candidate list (set for that child only; the live service is never touched). Reported: which
transcripts changed, and which moved closer to / further from the repeat. Runs under ``/tmp/zoe-voice-harness.lock`` and
only with ``--min-mem-mb`` available (a second Moonshine load beside the live one).

LOCAL ONLY: reads ``~/.zoe-voice/voice_stt.jsonl`` (+ ``.1``) and ``struggle_events`` read-only, writes ONLY under
``--out-dir`` (default ``~/.zoe/stt-accuracy``; a path inside the repo is refused - the report holds household words).
Stdout carries counts only. Exit 2 = nothing to read.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
ZOE_DATA = REPO / "services" / "zoe-data"
sys.path.insert(0, str(ZOE_DATA))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import struggle_signals as sig  # noqa: E402  (pure, stdlib only)

DEFAULT_LOG = Path.home() / ".zoe-voice" / "voice_stt.jsonl"
DEFAULT_OUT = Path.home() / ".zoe" / "stt-accuracy"
DEFAULT_SAMPLES = Path(os.environ.get("ZOE_VOICE_SAMPLE_DIR") or (Path.home() / ".zoe-voice-samples"))
HARNESS_LOCK = "/tmp/zoe-voice-harness.lock"
#: panels whose traffic is a harness or a test (the struggle detector's own list)
SYNTHETIC_PANELS = ("replay", "test", "probe", "ci-", "e2e", "bench")
LABEL_SIGNALS = (sig.MISHEAR, sig.RE_ASK)
#: a clip file and its STT log row are written within a second or two of each other
CLIP_TOLERANCE_S = 2.0
#: the night brain window (scripts/AGENTS.md "Brain-window quiet hours", 01:50-04:55 local): no harness may start or hold
#: /tmp/zoe-voice-harness.lock in it. The A/B also refuses to START in the 15 minutes before it (it must not run into it).
QUIET_FROM_MIN, QUIET_UNTIL_MIN = 1 * 60 + 35, 4 * 60 + 55


# ── the log ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SttRow:
    ts: float
    panel: str
    text: str


def _epoch(raw) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load_stt_rows(paths: Iterable[Path]) -> list[SttRow]:
    """Live voice transcripts, oldest first: no errors, no empty transcripts, no harness / test panels."""
    out: list[SttRow] = []
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            text = str(rec.get("transcript") or "").strip()
            panel = str(rec.get("panel_id") or "")
            ts = _epoch(rec.get("ts"))
            if rec.get("error") or not text or ts is None or panel.lower().startswith(SYNTHETIC_PANELS):
                continue
            out.append(SttRow(ts, panel, text))
    out.sort(key=lambda r: (r.ts, r.panel))
    return out


# ── pairs ────────────────────────────────────────────────────────────────────

def words(text: str) -> list[str]:
    """The detector's normalisation (wake word, contractions, number words, plurals folded): "what's" == "what is"."""
    return sig.normalise(text).split()


def word_edits(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


@dataclass
class Pair:
    panel: str
    misheard_ts: float
    repeat_ts: float
    misheard: str
    repeat: str
    signal: str
    source: str
    edits: int = 0
    repeat_words: int = 0

    def __post_init__(self) -> None:
        r = words(self.repeat)
        self.edits = word_edits(words(self.misheard), r)
        self.repeat_words = len(r)

    @property
    def differs(self) -> bool:
        return self.edits > 0

    @property
    def key(self) -> tuple:
        return (self.panel, round(self.misheard_ts, 3), round(self.repeat_ts, 3))


def _turn(row: SttRow, *, started: Optional[float] = None) -> "sig.Turn":
    # one placeholder member per panel: the log carries no identity, and the detector also pairs a voice panel's turns
    # across identities; replies are not logged (the pair rules used here never read them)
    return sig.Turn(ts=row.ts, channel="voice", user_id="member", is_guest=False, conv_key=f"voice:{row.panel}",
                    text=row.text, reply="", started=started)


def _by_panel(rows: list[SttRow]) -> dict[str, list[SttRow]]:
    out: dict[str, list[SttRow]] = defaultdict(list)
    for r in rows:
        out[r.panel].append(r)
    return out


def pairs_from_log(rows: list[SttRow], *, window_s: float = 30.0) -> list[Pair]:
    """Consecutive transcripts on one panel, at most ``window_s`` apart, that the detector's pair rule calls a MISHEAR or a
    RE_ASK of the first."""
    out: list[Pair] = []
    for panel, seq in _by_panel(rows).items():
        for prev, cur in zip(seq, seq[1:]):
            gap = cur.ts - prev.ts
            if not 0 < gap <= window_s:
                continue
            for e in sig.detect(_turn(prev), _turn(cur, started=cur.ts), window_s=window_s):
                if e.signal in LABEL_SIGNALS and e.about is not e.turn:
                    out.append(Pair(panel, prev.ts, cur.ts, prev.text, cur.text, e.signal, "log"))
                    break
    return out


def pairs_from_events(events: list[dict], rows: list[SttRow], *, window_s: float = 30.0,
                      slack_s: float = 120.0) -> list[Pair]:
    """A voice MISHEAR / RE_ASK row of ``struggle_events`` mapped back to its two transcripts: the repeat is the panel's
    last transcript at or before the event (an event is stored when its turn FINISHED, at most ``slack_s`` later), the
    misheard turn is the one before it, at most ``window_s`` earlier."""
    seqs = _by_panel(rows)
    out: list[Pair] = []
    for ev in events:
        if ev.get("channel") != "voice" or ev.get("signal") not in LABEL_SIGNALS:
            continue
        seq = seqs.get(str(ev.get("panel_id") or ""), [])
        ets = float(ev.get("ts") or 0.0)
        idx = [i for i, r in enumerate(seq) if ets - slack_s <= r.ts <= ets + 1.0]
        if not idx or idx[-1] == 0:
            continue
        cur, prev = seq[idx[-1]], seq[idx[-1] - 1]
        if 0 < cur.ts - prev.ts <= window_s:
            out.append(Pair(cur.panel, prev.ts, cur.ts, prev.text, cur.text, str(ev["signal"]), "events"))
    return out


def merge_pairs(*groups: list[Pair]) -> list[Pair]:
    merged: dict[tuple, Pair] = {}
    for group in groups:
        for p in group:
            seen = merged.get(p.key)
            if seen is None:
                merged[p.key] = p
            elif seen.source != p.source:
                seen.source = "both"
    return sorted(merged.values(), key=lambda p: p.misheard_ts)


# ── the score ────────────────────────────────────────────────────────────────

def iso_week(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts, tz=timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def weekly(rows: list[SttRow], pairs: list[Pair]) -> list[dict]:
    """One row per ISO week (UTC) that has transcripts, oldest first."""
    weeks: dict[str, dict] = {}
    for r in rows:
        weeks.setdefault(iso_week(r.ts), {"turns": 0, "pairs": 0, "mishears": 0, "edits": 0, "repeat_words": 0})
        weeks[iso_week(r.ts)]["turns"] += 1
    for p in pairs:
        w = weeks.setdefault(iso_week(p.misheard_ts), {"turns": 0, "pairs": 0, "mishears": 0, "edits": 0, "repeat_words": 0})
        w["pairs"] += 1
        if p.differs:
            w["mishears"] += 1
            w["edits"] += p.edits
            w["repeat_words"] += p.repeat_words
    out = []
    for week, w in sorted(weeks.items()):
        out.append({"week": week, **w,
                    "mishear_rate": round(w["mishears"] / w["turns"], 4) if w["turns"] else None,
                    "proxy_wer": round(w["edits"] / w["repeat_words"], 4) if w["repeat_words"] else None})
    return out


# ── clips + the keyterm A/B ──────────────────────────────────────────────────

def clip_index(sample_dir: Path) -> list[tuple[float, Path]]:
    """(mtime, path) of the top-level corpus clips (quarantine sub-folders are not the corpus)."""
    try:
        return sorted((p.stat().st_mtime, p) for p in Path(sample_dir).glob("*.wav") if p.is_file())
    except OSError:
        return []


def clip_for(ts: float, index: list[tuple[float, Path]], *, tolerance_s: float = CLIP_TOLERANCE_S) -> Optional[Path]:
    """The clip captured for the turn transcribed at ``ts``: the nearest file time within ``tolerance_s``, if exactly one
    candidate is that close (two clips that close cannot be told apart, so neither is used)."""
    near = [(abs(m - ts), p) for m, p in index if abs(m - ts) <= tolerance_s]
    if len(near) != 1:
        return None
    return near[0][1]


@dataclass
class AbRow:
    clip: str
    repeat: str
    without: str
    with_terms: str
    edits_without: int = 0
    edits_with: int = 0
    verdict: str = "same"     # closer | further | same (vs the repeat), "changed" is reported separately

    def __post_init__(self) -> None:
        r = words(self.repeat)
        self.edits_without = word_edits(words(self.without), r)
        self.edits_with = word_edits(words(self.with_terms), r)
        if self.edits_with < self.edits_without:
            self.verdict = "closer"
        elif self.edits_with > self.edits_without:
            self.verdict = "further"

    @property
    def changed(self) -> bool:
        return words(self.without) != words(self.with_terms)


def compare_ab(items: list[tuple[Path, Pair]], without: dict[str, str], with_terms: dict[str, str]) -> dict:
    rows = [AbRow(clip=p.name, repeat=pair.repeat, without=without.get(str(p), ""), with_terms=with_terms.get(str(p), ""))
            for p, pair in items if str(p) in without and str(p) in with_terms]
    return {
        "clips": len(rows),
        "changed": sum(r.changed for r in rows),
        "closer": sum(r.verdict == "closer" for r in rows),
        "further": sum(r.verdict == "further" for r in rows),
        "same": sum(r.verdict == "same" for r in rows),
        "edits_without": sum(r.edits_without for r in rows),
        "edits_with": sum(r.edits_with for r in rows),
        "rows": [asdict(r) | {"changed": r.changed} for r in rows],
    }


def in_quiet_hours(now: Optional[datetime] = None) -> bool:
    t = now or datetime.now()
    minute = t.hour * 60 + t.minute
    return QUIET_FROM_MIN <= minute < QUIET_UNTIL_MIN


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def child_env(base: dict, keyterms: Optional[str], scratch: str) -> dict:
    """The transcribing child's environment: the keyterm list for THIS child only, and every live store the import of
    ``routers.voice_tts`` could reach pinned into a throwaway directory."""
    env = dict(base)
    env.pop("ZOE_MOONSHINE_KEYTERMS", None)
    if keyterms:
        env["ZOE_MOONSHINE_KEYTERMS"] = keyterms
    env["ZOE_VOICE_STT_LOG"] = os.path.join(scratch, "voice_stt.jsonl")
    env["MEMPALACE_DATA_DIR"] = os.path.join(scratch, "mempalace")
    env["ZOE_LOG_DIR"] = os.path.join(scratch, "logs")
    env["ZOE_VOICE_SAVE_AUDIO"] = "0"
    return env


def transcribe_in_child(clips: list[Path], keyterms: Optional[str], *, python: str, service_dir: Path,
                        timeout_s: int = 1800) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="stt-ab-") as scratch:
        proc = subprocess.run(
            [python, str(Path(__file__).resolve()), "--_worker", "--service-dir", str(service_dir)],
            input=json.dumps([str(c) for c in clips]), capture_output=True, text=True, timeout=timeout_s,
            cwd=str(service_dir), env=child_env(os.environ, keyterms, scratch))
    if proc.returncode != 0:
        raise RuntimeError(f"transcribe child exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _worker(service_dir: str) -> int:
    """Child mode: transcribe the clip paths on stdin with the service's own Moonshine path; print one JSON line."""
    sys.path.insert(0, service_dir)
    from routers import voice_tts as vt

    clips = json.loads(sys.stdin.read() or "[]")

    async def run() -> dict[str, str]:
        out = {}
        for c in clips:
            out[c] = (await vt._run_moonshine(c) or "").strip()
        return out

    texts = asyncio.run(run())
    # what the transcriber did with the list (configured / applied / supported): proof the arm really was biased
    print(json.dumps({"_keyterms": vt.moonshine_keyterms_state(), **texts}))
    return 0


class HarnessLock:
    """``flock /tmp/zoe-voice-harness.lock`` (two Moonshine / Kokoro loads at once can OOM the box), waiting at most
    ``wait_s``."""

    def __init__(self, path: str = HARNESS_LOCK, wait_s: float = 600.0) -> None:
        self.path, self.wait_s, self.fh = path, wait_s, None

    def __enter__(self):
        import fcntl

        self.fh = open(self.path, "w")
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{self.path} held for over {self.wait_s:.0f}s")
                time.sleep(2.0)

    def __exit__(self, *exc):
        import fcntl

        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def keyterm_ab(pairs: list[Pair], candidate: str, *, sample_dir: Path, max_clips: int,
               transcribe: Callable[[list[Path], Optional[str]], dict[str, str]]) -> dict:
    index = clip_index(sample_dir)
    items: list[tuple[Path, Pair]] = []
    for p in pairs:
        if not p.differs:
            continue
        clip = clip_for(p.misheard_ts, index)
        if clip is not None:
            items.append((clip, p))
    items = items[-max_clips:] if max_clips > 0 else items
    if not items:
        return {"clips": 0, "note": "no labelled clip found (is ZOE_VOICE_SAVE_AUDIO capturing?)"}
    clips = [c for c, _ in items]
    without = transcribe(clips, None)
    with_terms = transcribe(clips, candidate)
    return {"candidate_terms": len([t for t in candidate.split(",") if t.strip()]),
            "keyterms_without": without.get("_keyterms"), "keyterms_with": with_terms.get("_keyterms"),
            **compare_ab(items, without, with_terms)}


# ── the report ───────────────────────────────────────────────────────────────

def safe_out_dir(path: Path) -> Path:
    """The report holds household words: never inside the repository."""
    p = Path(path).expanduser().resolve()
    if p == REPO or REPO in p.parents:
        raise ValueError(f"refusing to write the STT accuracy report inside the repository ({p})")
    return p


async def read_events(since: float) -> list[dict]:
    import struggle_detector
    from db_pool import close_pool, init_pool

    await init_pool()
    try:
        return await struggle_detector.get_backend().rows(since, None, limit=20000)
    finally:
        await close_pool()


def build_report(rows: list[SttRow], events: Optional[list[dict]], *, window_s: float) -> tuple[dict, list[Pair]]:
    pairs = merge_pairs(pairs_from_log(rows, window_s=window_s),
                        pairs_from_events(events or [], rows, window_s=window_s))
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_s": window_s,
        "turns": len(rows),
        "events_read": None if events is None else len(events),
        "pairs": len(pairs),
        "pairs_by_source": {s: sum(p.source == s for p in pairs) for s in ("log", "events", "both")},
        "mishears": sum(p.differs for p in pairs),
        "weeks": weekly(rows, pairs),
    }
    return report, pairs


def render(report: dict) -> str:
    lines = [f"STT accuracy (proxy) - {report['turns']} live voice turns, {report['pairs']} labelled pairs "
             f"({report['pairs_by_source']}), {report['mishears']} mishears"
             + ("" if report["events_read"] is not None else "  [struggle_events not read: log labels only]"),
             f"{'week':10} {'turns':>6} {'pairs':>6} {'mishear':>8} {'rate':>7} {'proxyWER':>9}"]
    for w in report["weeks"]:
        rate = "-" if w["mishear_rate"] is None else f"{100 * w['mishear_rate']:.1f}%"
        wer = "-" if w["proxy_wer"] is None else f"{100 * w['proxy_wer']:.0f}%"
        lines.append(f"{w['week']:10} {w['turns']:>6} {w['pairs']:>6} {w['mishears']:>8} {rate:>7} {wer:>9}")
    ab = report.get("keyterm_ab")
    if ab:
        lines.append(f"keyterm A/B: {ab.get('clips', 0)} labelled clips, {ab.get('changed', 0)} transcripts changed, "
                     f"{ab.get('closer', 0)} closer to the repeat, {ab.get('further', 0)} further, {ab.get('same', 0)} same"
                     f" (word edits {ab.get('edits_without', '-')} -> {ab.get('edits_with', '-')})"
                     + (f" - {ab['note']}" if ab.get("note") else ""))
    return "\n".join(lines)


def write_report(report: dict, pairs: list[Pair], out_dir: Path) -> Path:
    out = safe_out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out / f"stt-accuracy-{stamp}.json"
    path.write_text(json.dumps(report | {"pair_rows": [asdict(p) | {"differs": p.differs} for p in pairs]},
                               indent=1, sort_keys=True), encoding="utf-8")
    (out / f"stt-accuracy-{stamp}.txt").write_text(render(report) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", type=Path, action="append", help="STT log(s); default ~/.zoe-voice/voice_stt.jsonl(.1)")
    ap.add_argument("--weeks", type=float, default=8.0, help="look back this many weeks")
    ap.add_argument("--window-s", type=float, default=30.0, help="a repeat must follow within this many seconds")
    ap.add_argument("--no-db", action="store_true", help="labels from the log alone; do not read struggle_events")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keyterm-ab", metavar="TERMS", help="comma-separated candidate ZOE_MOONSHINE_KEYTERMS to A/B")
    ap.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES)
    ap.add_argument("--max-clips", type=int, default=40, help="newest N labelled clips in the A/B")
    ap.add_argument("--min-mem-mb", type=int, default=1500, help="A/B only: skip below this MemAvailable")
    ap.add_argument("--python", default=None, help="the A/B child's interpreter (default: the zoe-data service's)")
    ap.add_argument("--service-dir", type=Path, default=ZOE_DATA)
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args._worker:
        return _worker(str(args.service_dir))

    out_dir = safe_out_dir(args.out_dir)           # refuse a repo path BEFORE reading anything
    logs = args.log or [DEFAULT_LOG.with_name(DEFAULT_LOG.name + ".1"), DEFAULT_LOG]
    since = time.time() - args.weeks * 7 * 86400.0
    rows = [r for r in load_stt_rows(logs) if r.ts >= since]
    if not rows:
        print("no live voice transcripts in the window", file=sys.stderr)
        return 2
    events: Optional[list[dict]] = None
    if not args.no_db:
        try:
            events = asyncio.run(read_events(since))
        except Exception as exc:  # noqa: BLE001 - the log still labels pairs
            print(f"struggle_events not read ({type(exc).__name__}); log labels only", file=sys.stderr)
    report, pairs = build_report(rows, events, window_s=args.window_s)

    if args.keyterm_ab:
        avail = mem_available_mb()
        if in_quiet_hours():
            report["keyterm_ab"] = {"clips": 0, "note": "skipped: brain-window quiet hours (01:50-04:55)"}
        elif avail < args.min_mem_mb:
            report["keyterm_ab"] = {"clips": 0, "note": f"skipped: MemAvailable {avail} MB < {args.min_mem_mb} MB"}
        else:
            from service_python import resolve_service_python

            python = resolve_service_python(args.python)[0]

            def transcribe(clips, terms):
                return transcribe_in_child(clips, terms, python=python, service_dir=args.service_dir)

            with HarnessLock():
                report["keyterm_ab"] = keyterm_ab(pairs, args.keyterm_ab, sample_dir=args.samples_dir,
                                                  max_clips=args.max_clips, transcribe=transcribe)
    path = write_report(report, pairs, out_dir)
    print(render(report))
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

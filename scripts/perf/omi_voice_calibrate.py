#!/usr/bin/env python3
"""Calibrate pendant speaker tagging and learned-voice suggestions ON THE BOX; write an AGGREGATE-ONLY report OUTSIDE the repo.

    flock -x /tmp/zoe-voice-harness.lock nice -n 10 ~/.zoe/venvs/zoe-data-py312/bin/python scripts/perf/omi_voice_calibrate.py

Four measurements (``docs/knowledge/omi-voice-tagging.md`` explains each and the numbers chosen from them):
  A  the wearer's PANEL profile scored on held-out panel clips (same channel) - the baseline;
  B  the same profile run through ``omi_diarize.diarise`` over the owner's synced PENDANT recordings - label time shares and the
     segment-score distribution: the domain mismatch (no ground truth exists until the owner corrects lines);
  C  learned-voice suggestions and unknown-voice clustering on LABELLED public speakers (LibriSpeech test-clean, speaker-disjoint
     from the centring cohort): precision / recall over the threshold grid;
  D  two-speaker conversations built from those speakers: time-weighted you / other labelling accuracy per linkage cut.

Privacy: the owner's audio and the profile built from it stay in this process. The report holds counts, rates and quantiles only -
no names, no transcripts, no embeddings, no file names - and goes to ``~/.zoe/reports/`` (0600), never the repository.
Gates (refuses rather than waits): not 01:50-04:55 HOUSEHOLD time (the night jobs; a run that starts earlier stops itself at 01:45), MemAvailable >= the measured peak RSS + headroom, no panel voice
turn in the last 5 minutes (the zoe-data app log). Single-threaded ORT and BLAS, like the production pass.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import re
import sys
import tempfile
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
from time_utils import zoe_timezone  # noqa: E402 - the household zone (ZOE_TIMEZONE), never the host's
PEAK_RSS_MB = 100            # measured 2026-10-17: ORT + numpy + the INT8 session, standalone process
VOICE_LOG_RX = re.compile(r"(turn_stream|VOICE_TURN|voice turn|/api/voice/)")


STOP_MARGIN_MIN = 5          # stop this long before the night jobs' window opens
DEADLINE: datetime | None = None


def household_now(now: datetime | None = None) -> datetime:
    """The instant in the HOUSEHOLD zone: ``TZ=UTC`` on the host (or a shell) must not move the 01:50-04:55 window."""
    return (now or datetime.now(timezone.utc)).astimezone(zoe_timezone())


def quiet_deadline(now: datetime | None = None) -> datetime:
    """When this run must have stopped: ``STOP_MARGIN_MIN`` before the next 01:50 household time."""
    n = household_now(now)
    edge = n.replace(hour=1, minute=50, second=0, microsecond=0)
    if edge <= n:
        edge += timedelta(days=1)
    return edge - timedelta(minutes=STOP_MARGIN_MIN)


def check_deadline(now: datetime | None = None) -> None:
    """Called inside every long loop: a run that started before 01:50 still exits before the night jobs need the harness lock."""
    if DEADLINE is not None and household_now(now) >= DEADLINE:
        sys.exit("stopped: reached the night jobs' window (01:50) - no report written; re-run with fewer sections or a smaller --max-pendant-min")


def preflight(headroom_mb: int, log: Path, *, now: datetime | None = None, mem_mb: int | None = None) -> datetime:
    """Refuse (``SystemExit``) unless every gate is positively open; returns the DEADLINE to stop by. FAIL CLOSED: a missing or
    unreadable app log, or a voice line whose time cannot be read, is a refusal - "I could not tell" never means "nobody is talking"."""
    now = household_now(now)
    if (1, 50) <= (now.hour, now.minute) < (4, 55):
        sys.exit("refused: 01:50-04:55 belongs to the night jobs")
    mem = mem_mb if mem_mb is not None else next(
        int(line.split()[1]) // 1024 for line in open("/proc/meminfo") if line.startswith("MemAvailable"))
    if mem < PEAK_RSS_MB + headroom_mb:
        sys.exit(f"refused: MemAvailable {mem} MB < {PEAK_RSS_MB} + {headroom_mb} MB")
    try:
        tail = log.read_text(errors="replace").splitlines()[-3000:]
    except OSError:
        sys.exit(f"refused: cannot read {log.name} to rule out a recent voice turn")
    for line in reversed(tail):
        if VOICE_LOG_RX.search(line):
            try:
                t = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", line[:24]))
            except ValueError:
                sys.exit("refused: the newest voice line has no readable time")
            if t.tzinfo is None or (now - t).total_seconds() < 300:
                sys.exit("refused: a voice turn in the last 5 minutes")
            break
    return quiet_deadline(now)


def report_path(raw: str, sections: str, *, today: datetime | None = None) -> str:
    """The private report path: default ``~/.zoe/reports/``; anything inside the repository is refused."""
    d = today or datetime.now()
    path = os.path.realpath(raw or os.path.expanduser(f"~/.zoe/reports/omi-voice-tagging-{d:%Y-%m-%d}"
                                                       + ("" if sections.upper() == "ABCD" else "-" + sections.upper()) + ".md"))
    if path == str(REPO) or path.startswith(str(REPO.resolve()) + os.sep):
        sys.exit("refused: the report must live outside the repository (it is about household audio)")
    return path


SKIPPED = {"unreadable": 0}


def read(path: str) -> np.ndarray | None:
    """Mono float32 at 16 kHz through the embedder's own ``wav_to_float`` (the panel corpus holds 24 kHz and malformed files; raw
    samples at the wrong rate give wrong embeddings). An unreadable file is counted and skipped (None), never fatal."""
    import omi_diarize as od
    try:
        with open(path, "rb") as fh:
            return od._embedder_module().wav_to_float(fh.read())[0]
    except (ValueError, EOFError, OSError, wave.Error):
        SKIPPED["unreadable"] += 1
        return None


def embed_file(emb, path: str, seconds: float | None = None):
    """One embedding of a file (None when unreadable); the deadline is checked per file."""
    check_deadline()
    x = read(path)
    if x is None:
        return None
    return emb.embed(x[: int(16000 * seconds)] if seconds else x)


def q(xs, ps=(0.1, 0.5, 0.9)) -> str:
    return " / ".join(f"{np.quantile(xs, p):.3f}" for p in ps) if len(xs) else "n/a"


def suggestion_grid(E: dict, rng: random.Random, trials: int = 30) -> list:
    import omi_voices as ov
    rows = []
    for smin in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        for margin in (0.0, 0.05, 0.10, 0.15):
            check_deadline()
            tp = fp = pos = neg_fp = neg = 0
            for t in range(trials):
                r = random.Random(t)
                spk = r.sample(sorted(E), 15)
                tagged, strangers = spk[:5], spk[5:]
                prints, queries = [], []
                for s in tagged:
                    clips = list(E[s])
                    r.shuffle(clips)
                    vec, _ = ov.loo_mean(np.stack(clips[:3]))
                    prints.append(dict(person=s, v=vec, rej=0))
                    queries += [(s, c) for c in clips[3:9]]
                queries += [(None, c) for s in strangers for c in E[s][:4]]
                os.environ["ZOE_OMI_SUGGEST_MIN"], os.environ["ZOE_OMI_SUGGEST_MARGIN"] = str(smin), str(margin)
                for truth, c in queries:
                    hit = ov.suggest(c, prints)
                    if truth is None:
                        neg += 1
                        neg_fp += hit is not None
                    else:
                        pos += 1
                        if hit is not None:
                            tp += hit[0] == truth
                            fp += hit[0] != truth
            sug = tp + fp + neg_fp
            rows.append((smin, margin, tp / sug if sug else 1.0, tp / pos, neg_fp / neg, fp / pos))
    return rows


def cluster_grid(E: dict) -> list:
    rows = []
    for cmin in (0.35, 0.40, 0.45, 0.50, 0.55):
        check_deadline()
        purity, frag = [], []
        for t in range(20):
            r = random.Random(100 + t)
            spk = r.sample(sorted(E), 8)
            stream = [(s, c) for s in spk for c in E[s][:8]]
            r.shuffle(stream)
            cl: list = []
            for s, e in stream:
                best = max(cl, key=lambda c: float(c["c"] @ e), default=None)
                if best is not None and float(best["c"] @ e) >= cmin:
                    best["c"] = (best["c"] * len(best["m"]) + e) / np.linalg.norm(best["c"] * len(best["m"]) + e)
                    best["m"].append(s)
                else:
                    cl.append(dict(c=e, m=[s]))
            purity.append(sum(max(c["m"].count(x) for x in set(c["m"])) for c in cl) / len(stream))
            frag.append(len(cl) / len(spk))
        rows.append((cmin, float(np.mean(purity)), float(np.mean(frag))))
    return rows


async def conversations(files: dict, embedder, tmp: str) -> list:
    import omi_diarize as od
    import omi_voices as ov
    rows = []
    for link, you_min in ((0.35, 0.309), (0.45, 0.309), (0.55, 0.309), (0.45, 0.351), (0.45, 0.40)):
        os.environ["ZOE_OMI_DIAR_LINK_MIN"], os.environ["ZOE_OMI_TAG_YOU_MIN"] = str(link), str(you_min)
        acc_t = tot_t = you_true = you_said = you_hit = 0.0
        for t in range(10):
            check_deadline()
            r = random.Random(200 + t)
            a, b = r.sample(sorted(files), 2)
            enrol = [e for e in (embed_file(embedder, f) for f in files[a][-5:]) if e is not None]
            if len(enrol) < 2:
                continue
            prof, _ = ov.loo_mean(np.stack(enrol))
            parts, truth, pos = [], [], 0
            for i in range(8):
                f = files[a if i % 2 == 0 else b][i // 2]
                x = read(f)
                if x is None:
                    continue
                x = x[: 16000 * r.randint(3, 8)]
                parts.append(x)
                truth.append((pos, pos + len(x), "you" if i % 2 == 0 else "other"))
                pos += len(x)
            p = os.path.join(tmp, f"conv{t}.wav")
            with wave.open(p, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes((np.concatenate(parts) * 32767).astype("<i2").tobytes())
            segs = await od.diarise(p, embedder, prof)
            for s0, s1, lab in truth:
                for seg in segs:
                    ov_ = max(0, min(s1, seg.end_ms * 16) - max(s0, seg.start_ms * 16)) / 16000
                    tot_t += ov_
                    acc_t += ov_ * (seg.speaker == lab)
                    you_true += ov_ * (lab == "you")
                    you_said += ov_ * (seg.speaker == "you")
                    you_hit += ov_ * (lab == "you" and seg.speaker == "you")
        rows.append((link, you_min, acc_t / tot_t, you_hit / max(you_said, 1e-9), you_hit / max(you_true, 1e-9)))
    return rows


def write_report(path: str, out: list) -> None:
    """The private report file (0600). Only a directory THIS run creates is tightened to 0700; a caller-owned directory keeps its mode."""
    report_dir = os.path.dirname(path)
    created = not os.path.isdir(report_dir)
    os.makedirs(report_dir, mode=0o700, exist_ok=True)
    if created:
        os.chmod(report_dir, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(out) + "\n")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="")
    ap.add_argument("--pendant-dir", default=os.path.expanduser("~/.zoe/omi-recordings"))
    ap.add_argument("--panel-dir", default=os.path.expanduser("~/.zoe-voice-samples"))
    ap.add_argument("--public-dir", default=os.path.expanduser("~/.cache/zoe-speaker-bakeoff/impostors"))
    ap.add_argument("--max-pendant-min", type=float, default=30.0)
    ap.add_argument("--headroom-mb", type=int, default=600)
    ap.add_argument("--sections", default="ABCD", help="any of A B C D (B needs A: the wearer profile)")
    a = ap.parse_args()
    a.report = report_path(a.report, a.sections)
    global DEADLINE
    DEADLINE = preflight(a.headroom_mb, Path.home() / ".zoe-logs" / "zoe-data.app.log")
    from threadpoolctl import threadpool_limits
    threadpool_limits(1)
    import omi_diarize as od
    import omi_voices as ov
    emb = od.load_embedder()
    if emb is None:
        sys.exit("the pinned embedder refused to load")
    rng = random.Random(7)
    out = [f"# Omi voice tagging calibration ({datetime.now():%Y-%m-%d %H:%M})", "",
           "PRIVATE, aggregate-only. Model " + od.model_id() + "; thresholds you>=%.3f other<%.3f." % od.thresholds(), ""]

    want = set(a.sections.upper())
    held: list = []
    prof = None
    if want & {"A", "B"}:                   # only A and B need the private panel corpus (B needs A's wearer profile)
        panel = sorted(glob.glob(os.path.join(a.panel_dir, "*.wav")))
        rng.shuffle(panel)
        enrol = [e for e in (embed_file(emb, f) for f in panel[:5]) if e is not None]
        if not enrol:
            sys.exit("sections A/B need at least one usable clip in --panel-dir")
        prof, dropped = ov.loo_mean(np.stack(enrol))
        held = [float(prof @ e) for e in (embed_file(emb, f) for f in panel[5:305]) if e is not None]
        you_min, other_max = od.thresholds()
    if "A" in want:
        out += ["## A. Panel channel (same as the profile)", f"- clips scored: {len(held)}; profile from 5 clips, {len(dropped)} dropped",
                f"- score p10 / p50 / p90: {q(held)}",
                f"- labelled you: {np.mean(np.array(held) >= you_min):.1%}; unsure: {np.mean((np.array(held) < you_min) & (np.array(held) >= other_max)):.1%}; "
                f"other: {np.mean(np.array(held) < other_max):.1%}", ""]

    secs = {k: 0.0 for k in ("you", "other", "unsure", "mixed", "unverified")}
    seg_scores, turns_per, done_s, t0, c0, wins = [], [], 0.0, time.perf_counter(), time.process_time(), 0
    for f in sorted(glob.glob(os.path.join(a.pendant_dir, "*.wav"))) if "B" in want else []:
        if done_s >= a.max_pendant_min * 60:
            break
        check_deadline()
        segs = await od.diarise(f, emb, prof)
        turns_per.append(len(segs))
        wins += sum(s.windows for s in segs)
        for s in segs:
            d = (s.end_ms - s.start_ms) / 1000
            secs[s.speaker] += d
            if s.score is not None:
                seg_scores += [s.score] * max(1, int(d))
            done_s += d
    tot = sum(secs.values()) or 1.0
    b_out = ["## B. Pendant channel (owner's synced recordings, no ground truth)",
            f"- recordings: {len(turns_per)}; audio: {done_s / 60:.1f} min; speech windows found by VAD: {wins}; "
            f"turns per recording p10/p50/p90: {q(turns_per)}",
            "- time share: " + ", ".join(f"{k} {v / tot:.1%}" for k, v in secs.items()),
            f"- segment score (duration-weighted) p10 / p50 / p90: {q(seg_scores)}",
            f"- domain shift, p90 pendant segment score minus p50 panel clip score: "
            f"{(np.quantile(seg_scores, 0.9) - np.quantile(held, 0.5)) if seg_scores else float('nan'):+.3f}",
            f"- cost: {1000 * (time.perf_counter() - t0) / max(done_s, 1):.0f} ms wall and "
            f"{(time.process_time() - c0) / max(done_s / 60, 1e-9):.1f} CPU-s per audio minute (VAD + embeddings, 1 thread)", ""]
    if "B" in want:
        out += b_out

    if not want & {"C", "D"}:
        man = []
    else:
        man = json.load(open(os.path.join(a.public_dir, "manifest.json")))
    files: dict = {}
    for m in man:
        if m["source"] == "libri":
            files.setdefault(m["speaker"], []).append(os.path.join(a.public_dir, m["path"]))
    E = {} if "C" not in want else {s: [e for e in (embed_file(emb, f, 4) for f in fs[:14]) if e is not None] for s, fs in files.items()}
    if "C" in want:
        out += ["## C. Suggestions (public labelled speakers: 5 tagged voices, 3 clips each; queries = their other clips + 10 strangers)",
                "| suggest_min | margin | precision | recall | stranger suggested | wrong person |", "|---|---|---|---|---|---|"]
        out += [f"| {r[0]:.2f} | {r[1]:.2f} | {r[2]:.1%} | {r[3]:.1%} | {r[4]:.1%} | {r[5]:.1%} |" for r in suggestion_grid(E, rng)]
        out += ["", "## C2. Unknown-voice clustering (8 speakers x 8 clips, shuffled)", "| cluster_min | purity | clusters per speaker |",
                "|---|---|---|"]
        out += [f"| {r[0]:.2f} | {r[1]:.1%} | {r[2]:.2f} |" for r in cluster_grid(E)]
    conv = []
    if "D" in want:
        with tempfile.TemporaryDirectory() as tmp:
            conv = await conversations(files, emb, tmp)
    if "D" in want:
        out += ["", "## D. Two-speaker conversations (10 x ~45 s, wearer profile from 5 other clips)",
                "| link_min | you_min | time correctly labelled | you precision | you recall |", "|---|---|---|---|---|"]
        out += [f"| {r[0]:.2f} | {r[1]:.3f} | {r[2]:.1%} | {r[3]:.1%} | {r[4]:.1%} |" for r in conv]
    if SKIPPED["unreadable"]:
        out += ["", f"- unreadable audio files skipped: {SKIPPED['unreadable']}"]
    write_report(a.report, out)
    print("\n".join(out))


if __name__ == "__main__":
    asyncio.run(main())

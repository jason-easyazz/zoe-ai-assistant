#!/usr/bin/env python3
"""Offline replay of the struggle detector over the zoe-data app log - LOCAL ONLY, READ-ONLY.

Rebuilds the VOICE turns of the last N days from ``~/.zoe-logs/zoe-data.app.log*`` (the per-turn log lines:
``voice/turn_stream`` transcript, ``ROUTER_SHADOW`` route, ``router_two_stage`` decision / failure, ``voice/command``
session + user, ``SKYBRIDGE TIMING`` reply, ``BRAIN_LANE`` / ``VOICE TIMING`` brain lane, ``FLUE_ABORT`` barge-in) and runs
``struggle_signals.detect`` over them exactly as the live worker would. Nothing is written anywhere but ``--out-dir``.

    struggle_replay_eval.py [--days 14]                       # counts per signal (numbers only - safe to paste)
    struggle_replay_eval.py --dump [--sample-negatives 100]   # + a labelling sheet in --out-dir (HAS utterance text)
    struggle_replay_eval.py --labels labels.jsonl             # + precision / recall per signal from the hand labels

The labelling sheet and the labels hold real utterances: ``--out-dir`` must be OUTSIDE the repository (default
``~/.zoe/struggle-eval``) and nothing from it may be pasted into the repo, a PR or a comment. The printed report is
numbers only.

What the log cannot show (stated, not guessed): a brain reply's TEXT is not logged, so CANT_DO and VERBOSE are only
evaluable on fast-path turns whose reply is logged; chat turns are not in the log with their words, so this is the voice
lane only.

Labels file (JSONL, one line per judged row of the sheet): ``{"key": "<key>", "truth": ["RE_ASK", ...]}`` - the signals
that SHOULD have fired for that turn (empty list = none). Precision per signal = detected-and-true / detected among
labelled rows; recall = detected-and-true / true among labelled rows.
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

import struggle_signals as sig  # noqa: E402

_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}) ")
_TURN = re.compile(r"voice/turn_stream panel=(\S+) STT=([\d.]+)s transcript=(.*)$")
_ROUTE = re.compile(r"ROUTER_SHADOW text=.* -> routed=(\S+) \(best=(\S+) ([\d.]+)\).*?scores=(\{.*\})?")
_TWO = re.compile(r"router_two_stage (\{.*\})")
_TWO_FAIL = re.compile(r"two_stage decide failed")
_CMD = re.compile(r"voice/command panel=(\S+) session=(\S+) user=(\S+) ")
_SKY = re.compile(r"SKYBRIDGE TIMING .* reply=(.*)$")
_LANE = re.compile(r"BRAIN_LANE lane_attempted=\S+ lane_served=(\S+) outcome=(\S+) .*session=(\S+)")
_SYNTHETIC_PANELS = ("replay", "test", "probe", "ci-", "e2e", "bench")
_ABORT = re.compile(r"FLUE_ABORT .*session=(\S+) .*reason=(\S+)")


def _lit(s: str) -> str:
    try:
        v = ast.literal_eval(s.strip())
        return v if isinstance(v, str) else str(v)
    except Exception:  # noqa: BLE001
        return s.strip().strip("'\"")


def log_files(log_dir: Path) -> list[Path]:
    files = [Path(p) for p in glob.glob(str(log_dir / "zoe-data.app.log*"))]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def parse(paths: list[Path], since: datetime) -> list[sig.Turn]:
    turns: list[sig.Turn] = []
    open_by_panel: dict[str, sig.Turn] = {}
    by_session: dict[str, sig.Turn] = {}
    last: sig.Turn | None = None
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _TS.match(line)
                if not m:
                    continue
                when = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z")
                if when.replace(tzinfo=None) < since:
                    continue
                ts = when.timestamp()
                mt = _TURN.search(line)
                if mt:
                    panel = mt.group(1)
                    if panel.startswith(_SYNTHETIC_PANELS) or len(panel) <= 2:   # harness / test traffic, not a household
                        last = None
                        continue
                    t = sig.Turn(ts=ts, channel="voice", user_id="guest", is_guest=True, conv_key=f"voice:{panel}",
                                 panel_id=panel, text=_lit(mt.group(3)), stt_ms=int(float(mt.group(2)) * 1000))
                    turns.append(t)
                    open_by_panel[panel] = t
                    last = t
                    continue
                if last is None:
                    continue
                if (mr := _ROUTE.search(line)):
                    if not last.predicted_domain:
                        last.predicted_domain = last.two_stage_domain or mr.group(2)
                    if last.predicted_score is None:
                        # the live note keeps the similarity score OF the chosen domain (0.0 when it has no examples)
                        try:
                            scores = ast.literal_eval(mr.group(4)) if mr.group(4) else {}
                        except (ValueError, SyntaxError):
                            scores = {}
                        last.predicted_score = (float(scores.get(last.predicted_domain, 0.0)) if scores
                                                else float(mr.group(3)))
                elif (m2 := _TWO.search(line)):
                    try:
                        rec = json.loads(m2.group(1))
                        last.two_stage_active = True
                        if rec.get("failed"):
                            last.two_stage_failed = True
                        elif rec.get("two_stage_domain"):
                            last.two_stage_domain = rec["two_stage_domain"]
                            last.two_stage_conf = rec.get("head_conf")
                            last.predicted_domain = rec["two_stage_domain"]
                    except ValueError:
                        pass
                elif _TWO_FAIL.search(line):
                    last.two_stage_active = last.two_stage_failed = True
                elif (mc := _CMD.search(line)):
                    t = open_by_panel.get(mc.group(1))
                    if t is not None and not t.session_id:
                        t.session_id = mc.group(2)
                        user = mc.group(3)
                        t.user_id = "guest" if user in ("guest", "voice-guest", "voice-daemon", "") else user
                        t.is_guest = t.user_id == "guest"
                        by_session[t.session_id] = t
                elif (ms := _SKY.search(line)):
                    last.reply, last.path = _lit(ms.group(1)), sig.PATH_FAST
                elif (ml := _LANE.search(line)):
                    t = by_session.get(ml.group(3))
                    if t is not None:
                        t.path, t.lane, t.lane_outcome = sig.PATH_BRAIN, ml.group(1), ml.group(2)
                        t.reply = t.reply or "(brain reply not logged)"
                elif (ma := _ABORT.search(line)):
                    t = by_session.get(ma.group(1))
                    if t is not None and ma.group(2) == "barge_in":
                        t.interrupted = True
    return turns


def run(turns: list[sig.Turn]) -> list[tuple[sig.Turn | None, sig.Turn, list[sig.Event]]]:
    out = []
    prev_by_conv: dict[str, sig.Turn] = {}
    for t in turns:
        prev = prev_by_conv.get(t.conv_key)
        evs = sig.detect(prev, t)
        if t.reply == "(brain reply not logged)":   # no words to judge: CANT_DO / VERBOSE are not evaluable here
            evs = [e for e in evs if not (e.about is t and e.signal in (sig.CANT_DO, sig.VERBOSE))]
        out.append((prev, t, evs))
        prev_by_conv[t.conv_key] = t
    return out


def key_of(t: sig.Turn) -> str:
    return f"{int(t.ts)}|{t.panel_id}"


def report(results, labels: dict[str, set[str]] | None) -> dict:
    counts = {s: 0 for s in sig.SIGNALS}
    for _p, _t, evs in results:
        for e in evs:
            counts[e.signal] += 1
    rep = {"turns": len(results), "turns_with_event": sum(1 for r in results if r[2]), "events": counts}
    if labels:
        pr = {}
        for s in sig.SIGNALS:
            tp = fp = fn = 0
            for _p, t, evs in results:
                k = key_of(t)
                if k not in labels:
                    continue
                det = s in {e.signal for e in evs}
                tru = s in labels[k]
                tp += det and tru
                fp += det and not tru
                fn += (not det) and tru
            pr[s] = {"tp": tp, "fp": fp, "fn": fn,
                     "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                     "recall": round(tp / (tp + fn), 3) if tp + fn else None}
        rep["labelled_turns"] = sum(1 for _p, t, _e in results if key_of(t) in labels)
        rep["per_signal"] = pr
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log-dir", default=os.path.expanduser("~/.zoe-logs"))
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/.zoe/struggle-eval"))
    ap.add_argument("--dump", action="store_true", help="write the labelling sheet (utterance text) to --out-dir")
    ap.add_argument("--sample-negatives", type=int, default=100)
    ap.add_argument("--labels", help="JSONL hand labels (see the module doc)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.dump and (out_dir == REPO or REPO in out_dir.parents):
        print("refusing: --out-dir is inside the repository (the sheet holds real utterances)", file=sys.stderr)
        return 2
    since = datetime.now() - timedelta(days=args.days)
    results = run(parse(log_files(Path(args.log_dir).expanduser()), since))
    labels = None
    if args.labels:
        labels = {}
        with open(args.labels, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    labels[row["key"]] = set(row.get("truth") or [])
    if args.dump:
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        rnd = random.Random(args.seed)
        positives = [r for r in results if r[2]]
        negatives = [r for r in results if not r[2] and r[0] is not None]
        sample = positives + rnd.sample(negatives, min(args.sample_negatives, len(negatives)))
        sample.sort(key=lambda r: r[1].ts)
        sheet = out_dir / "sheet.jsonl"
        with open(sheet, "w", encoding="utf-8") as fh:
            for prev, t, evs in sample:
                fh.write(json.dumps({
                    "key": key_of(t), "gap_s": round(t.ts - prev.ts, 1) if prev else None,
                    "prev": {"text": prev.text, "reply": prev.reply, "domain": prev.domain(), "path": prev.path} if prev else None,
                    "cur": {"text": t.text, "reply": t.reply, "domain": t.domain(), "path": t.path,
                            "score": t.predicted_score, "two_stage_failed": t.two_stage_failed, "user": t.user_id,
                            "interrupted": t.interrupted},
                    "detected": [[e.signal, e.confidence, e.reasons] for e in evs],
                }) + "\n")
        os.chmod(sheet, 0o600)
        print(f"labelling sheet: {sheet} ({len(sample)} rows; LOCAL ONLY - real utterances)", file=sys.stderr)
    print(json.dumps(report(results, labels), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

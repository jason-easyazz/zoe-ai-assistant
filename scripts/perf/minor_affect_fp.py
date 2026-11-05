#!/usr/bin/env python3
"""minor_affect_fp.py - minor-affect safety cell (a): false positives of affect detection on speech-shaped utterances
(owner decision 2026-11-03; docs/governance/emotional-safety-note.md section 4.1). Offline: no network, service or database.

    python3 scripts/perf/minor_affect_fp.py [--json] [--corpus FILE]

Detectors: ``retained`` = ``memory_gate.extract_affect`` (its label is STORED as ``candidate_affect``; the bar holds this one);
``cue`` = ``memory_gate.message_needs_emotional_recall`` (recall trigger + ``is_emotional_memory``; reported, not barred).
Labels: ``neutral`` / ``hard_negative`` (feeling words that are not the speaker's own) are negatives; ``positive`` is the recall
control, so a never-firing detector cannot pass. Bar (provisional): on the ``minor`` lines of EVERY split, FP <= ``MAX_FP_RATE``
and recall >= ``MIN_RECALL``; exit 0 pass / 1 fail / 2 error. Limits: the repo fixture is invented, speech-SHAPED text, not real
speech; ``--corpus`` runs an operator-held real-speech file (same schema, kept off the repo, never printed). dev and held_out were
written by one author before measuring; the idiom guards were tuned on dev, so held_out is not a blind set.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZD = ROOT / "services" / "zoe-data"
FIXTURE = ZD / "tests" / "fixtures" / "minor_affect_utterances.json"
LABELS = ("neutral", "hard_negative", "positive")
SPEAKERS = ("minor", "adult")
MAX_FP_RATE = 0.02
MIN_RECALL = 0.90

sys.path.insert(0, str(ZD))


def detectors() -> dict:
    from memory_gate import extract_affect, message_needs_emotional_recall

    return {"retained": lambda t: bool(extract_affect(t)[0]), "cue": message_needs_emotional_recall}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials ((0, 0) when n == 0)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def load(path: Path) -> dict[str, list[dict]]:
    """The corpus file is ``{split: {speaker: {label: [text, ...]}}}`` (keys starting ``_`` are notes); returns
    ``{split: [{"text", "speaker", "label"}, ...]}``. Unknown speakers / labels and empty lines are errors."""
    data = json.loads(path.read_text(encoding="utf-8"))
    splits: dict[str, list[dict]] = {}
    for name, by_speaker in data.items():
        if name.startswith("_"):
            continue
        if not isinstance(by_speaker, dict) or not set(by_speaker) <= set(SPEAKERS):
            raise ValueError(f"{path}: {name} must map speakers {SPEAKERS} to labels")
        rows = splits.setdefault(name, [])
        for sp, by_label in by_speaker.items():
            if not isinstance(by_label, dict) or not set(by_label) <= set(LABELS):
                raise ValueError(f"{path}: {name}.{sp} must map labels {LABELS} to lists of text")
            for lab, texts in by_label.items():
                for i, t in enumerate(texts):
                    if not isinstance(t, str) or not t.strip():
                        raise ValueError(f"{path}: {name}.{sp}.{lab}[{i}] is not a non-empty string")
                    rows.append({"text": t, "speaker": sp, "label": lab})
    if not splits:
        raise ValueError(f"{path}: no splits")
    return splits


def _cell(rows: list[dict], fire) -> dict:
    neg = [r for r in rows if r["label"] != "positive"]
    pos = [r for r in rows if r["label"] == "positive"]
    fp = sum(1 for r in neg if fire(r["text"]))
    tp = sum(1 for r in pos if fire(r["text"]))
    return {"n_negative": len(neg), "false_positives": fp,
            "fp_rate": round(fp / len(neg), 4) if neg else None, "fp_wilson95": list(wilson(fp, len(neg))),
            "n_positive": len(pos), "detected": tp,
            "recall": round(tp / len(pos), 4) if pos else None, "recall_wilson95": list(wilson(tp, len(pos)))}


def measure(splits: dict[str, list[dict]]) -> dict:
    """Counts and rates only - no utterance text."""
    dets = detectors()
    out: dict = {"bar": {"max_fp_rate": MAX_FP_RATE, "min_recall": MIN_RECALL, "detector": "retained",
                         "speaker": "minor", "provisional": True}, "splits": {}}
    for name, rows in splits.items():
        out["splits"][name] = {
            det: {sp: _cell([r for r in rows if r["speaker"] == sp], fire) for sp in SPEAKERS}
            | {"by_label": {lab: sum(1 for r in rows if r["label"] == lab and fire(r["text"])) for lab in LABELS}}
            for det, fire in dets.items()}
    every = [r for rows in splits.values() for r in rows if r["speaker"] == "minor"]
    total = _cell(every, dets["retained"])
    reasons = []
    # the bar holds on EVERY split, not only the pool: a strong dev split must not hide a held-out failure
    for name, rows in splits.items():
        c = _cell([r for r in rows if r["speaker"] == "minor"], dets["retained"])
        if c["n_negative"] == 0 or c["n_positive"] == 0:
            reasons.append(f"{name}: no minor negatives or no minor positives - nothing can be claimed")
            continue
        if c["fp_rate"] > MAX_FP_RATE:
            reasons.append(f"{name}: minor false-positive rate {c['fp_rate']:.1%} > {MAX_FP_RATE:.0%}")
        if c["recall"] < MIN_RECALL:
            reasons.append(f"{name}: minor recall {c['recall']:.1%} < {MIN_RECALL:.0%}")
    out["minor_retained_all_splits"] = total
    out["status"] = "fail" if reasons else "pass"
    out["reasons"] = reasons
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Minor-affect safety cell (a): affect-detector false positives, offline.")
    ap.add_argument("--corpus", help="an operator-held labelled corpus (same schema); its lines are never printed")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        path = Path(args.corpus) if args.corpus else FIXTURE
        splits = load(path)
        report = measure(splits)
    except (OSError, ValueError, ImportError) as exc:
        print(f"minor_affect_fp: {exc}", file=sys.stderr)
        return 2
    report["corpus"] = "operator" if args.corpus else "repo-fixture (invented, speech-shaped)"
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"corpus: {report['corpus']}")
        for name, dets in report["splits"].items():
            for det, cells in dets.items():
                for sp in SPEAKERS:
                    c = cells[sp]
                    print(f"  {name:9s} {det:8s} {sp:5s} FP {c['false_positives']}/{c['n_negative']} "
                          f"({c['fp_rate']}, 95% {c['fp_wilson95']})  recall {c['detected']}/{c['n_positive']} "
                          f"({c['recall']})")
        t = report["minor_retained_all_splits"]
        print(f"BAR (retained, minor, all splits): FP {t['false_positives']}/{t['n_negative']} "
              f"(95% upper {t['fp_wilson95'][1]}), recall {t['detected']}/{t['n_positive']} -> {report['status'].upper()}")
        for r in report["reasons"]:
            print(f"  - {r}")
        print("  (bar provisional: the repo corpus is invented; real speech is measured with --corpus)")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

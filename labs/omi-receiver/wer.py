#!/usr/bin/env python3
"""Word error rate for the B9.1 protocol: pendant transcript vs the reference sentence.

Stdlib only. ``wer(ref, hyp)`` is the standard word-level Levenshtein distance over
the reference word count after light normalisation (lower-case, punctuation
stripped). The CLI takes the reference sentences (one per line) and the transcripts
— either one per line in the same order, or ``replay_samples.py --json`` output
(``{"rows": [{"file", "transcript", ...}]}``, taken in row = file order) — and
prints per-line and corpus WER::

    python3 wer.py --ref sentences.txt --hyp pendant_transcripts.txt
    python3 wer.py --ref sentences.txt --hyp /tmp/pendant20.json

A transcript count that differs from the reference count is an error (exit 2), never
silently padded or truncated: the gate number must score exactly the 20 sentences.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def normalise(text: str) -> list[str]:
    return _PUNCT.sub(" ", text.lower()).split()


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        cur = [i]
        for j, wb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r, h = normalise(ref), normalise(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return edit_distance(r, h) / len(r)


def corpus_wer(refs: list[str], hyps: list[str]) -> float:
    """Total errors over total reference words (not the mean of per-line WERs)."""
    if len(refs) != len(hyps):
        raise ValueError(f"{len(refs)} reference lines vs {len(hyps)} hypothesis lines")
    errors = words = 0
    for ref, hyp in zip(refs, hyps):
        r = normalise(ref)
        errors += edit_distance(r, normalise(hyp))
        words += len(r)
    return errors / words if words else 0.0


def load_hypotheses(path: Path) -> list[str]:
    """Transcripts in order: one per line from a text file, or the ``transcript`` of
    each row of ``replay_samples.py --json`` output (a ``.json`` path)."""
    text = path.read_text()
    if path.suffix.lower() != ".json":
        return text.splitlines()
    data = json.loads(text)
    rows = data["rows"] if isinstance(data, dict) else data
    return [str(row.get("transcript") or "") for row in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", type=Path, required=True, help="reference sentences, one per line")
    ap.add_argument("--hyp", type=Path, required=True,
                    help="transcripts: one per line in the same order, or replay_samples.py --json output")
    args = ap.parse_args(argv)
    refs = [l for l in args.ref.read_text().splitlines() if l.strip()]
    hyps = load_hypotheses(args.hyp)
    if len(hyps) != len(refs):
        print(f"transcript count mismatch: {len(refs)} reference sentences in {args.ref} vs "
              f"{len(hyps)} transcripts in {args.hyp}. Expect exactly one transcript per sentence, in "
              "order — fix the segmentation (split_on_silence.py) or the transcript file before scoring.",
              file=sys.stderr)
        return 2
    for i, (r, h) in enumerate(zip(refs, hyps), 1):
        print(f"{i:2d}  WER {100 * wer(r, h):5.1f}%  | {r}\n" + " " * 16 + f"| {h}")
    print(f"corpus WER {100 * corpus_wer(refs, hyps):.1f}% over {len(refs)} sentences")
    return 0


if __name__ == "__main__":
    sys.exit(main())

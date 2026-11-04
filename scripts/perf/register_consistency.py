#!/usr/bin/env python3
"""Register-consistency probes (Samantha initiative lane F E1/E4): does a reply HONOUR the member's stored register, and is the register
line the model reads stable? Count-based and pure - never a model judge (the Stable Personas lesson: measure behaviour from outside).

Sits ALONGSIDE the persona drift battery (``scripts/perf/persona_drift.py``, PR #2282) and shares nothing with it. Feed it replies from
a replay run:

    python3 scripts/perf/register_consistency.py replies.json

where ``replies.json`` is ``[{"entries": [{"key": "length", "value": "terse"}], "reply": "..."}, ...]``. Exit 1 on any violation.

Checks (each a count a rater can reproduce):
  length=terse       the reply has at most ``TERSE_MAX_WORDS`` words
  formality=formal   the reply has no contraction ("don't", "it's")
  humour=off         the reply has no joke marker ("haha", "lol", a laughing emoji)
  all                at most one question mark (the manner block's one-question rule)
And two structural probes over the register LINE itself (``line_stable``, ``child_line_clean``).
"""
from __future__ import annotations

import json
import re
import sys

TERSE_MAX_WORDS = 30
_CONTRACTION = re.compile(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", re.I)
_JOKE = re.compile(r"\b(?:haha+|hehe+|lol|lmao)\b|[\U0001F600-\U0001F606\U0001F923]", re.I)


def violations(entries: list[dict], reply: str) -> list[str]:
    """The register rules this reply breaks, given the member's STATED / CONFIRMED entries ([{"key","value"}]). [] = honoured."""
    want = {e.get("key"): e.get("value") for e in entries}
    text = reply or ""
    out = []
    if want.get("length") == "terse" and len(text.split()) > TERSE_MAX_WORDS:
        out.append(f"length=terse but {len(text.split())} words")
    if want.get("formality") == "formal" and _CONTRACTION.search(text):
        out.append("formality=formal but the reply uses a contraction")
    if want.get("humour") == "off" and _JOKE.search(text):
        out.append("humour=off but the reply carries a joke marker")
    if text.count("?") > 1:
        out.append("more than one question")
    return out


def line_stable(lines: list[str]) -> bool:
    """The same member's register line is byte-identical across sessions (cross-session consistency)."""
    return len(set(lines)) <= 1


def child_line_clean(line: str, stored_values: list[str]) -> bool:
    """A child's line carries none of the stored entries' values (the child register is fixed text)."""
    low = (line or "").lower()
    return not any(v and v.lower() in low for v in stored_values)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        rows = json.load(fh)
    bad = 0
    for i, row in enumerate(rows):
        for v in violations(row.get("entries", []), row.get("reply", "")):
            bad += 1
            print(f"row {i}: {v}")
    print(f"register_consistency: {len(rows)} replies, {bad} violations")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

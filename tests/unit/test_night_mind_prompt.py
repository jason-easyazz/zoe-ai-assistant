"""The MOMENTS prompt and its parser (2026-10-10, found on the live 4B by the majority-of-3 cells run).

Evidence (the 4B, K10's thirteen lines, cap 8; real prompt text, real day labels):
* ASKED to "pick every line that matters" it skipped the child's cello lessons 3 of 3 times (the tail ask for lines 9-13 returned the health / loan / quarrel lines only);
  asked to make a moment of EVERY line except a command or small talk it returned all five, 3 of 3 (v1).
* the labels: "person: something about someone they know" swallowed an offer, a concert, a move and a proud feeling (kind accuracy 0.71-0.77 against a bar of 0.85);
  with the kind defined by what the line is MAINLY about it scored 0.958 twice.
* a reply of 12 moments for "at most 8" was cut at 8 in code: the four later moments it had paid for and that were verified were thrown away.

The model's own behaviour is measured by the cells; these tests pin the contract the fix rests on, and each goes red when its fix is reverted.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))

import night_mind as nm  # noqa: E402


def _turns(n):
    now = nm._dt.datetime(2026, 10, 8, 12, 0, tzinfo=nm._dt.timezone.utc)
    return [nm.Turn(f"t{i}", f"Sorrel practised the cello for {i} hours today.", now + nm._dt.timedelta(minutes=i)) for i in range(1, n + 1)]


def _reply(turns, upto):
    return json.dumps({"moments": [{"ids": [f"m{i}"], "quote": t.text, "kind": "progress", "who": ["Sorrel"], "feeling": "none", "weight": 2, "later": "open"}
                                   for i, t in enumerate(turns[:upto], 1)]}, separators=(",", ":"))


def counts():
    return {k: 0 for k in nm.COUNT_KEYS}


def test_a_reply_longer_than_the_ask_keeps_every_verified_moment_up_to_twice_the_ask():
    ts = _turns(13)
    got = nm.parse_moments(_reply(ts, 12), ts, 0, counts())
    assert len(got) == 12                                         # was cut to 8: the four later, verified moments were thrown away
    assert nm.unreached_tail(ts, got) == [ts[12]]                 # so the tail ask covers one line, not five
    assert len(nm.parse_moments(_reply(ts, 13), ts, 0, counts())) == 13 and len(nm.parse_moments(_reply(ts, 13), ts, 0, counts(), cap=4)) == 8      # bounded: twice the ask


def test_an_over_long_reply_that_covered_the_whole_chunk_makes_no_tail_ask():
    """The tail ask is for lines the model never reached. With the reply kept whole, a model that wrote 12 moments for 12 lines has reached them all."""
    ts = _turns(12)
    got = nm.parse_moments(_reply(ts, 12), ts, 0, counts())
    assert nm.unreached_tail(ts, got) == []


def test_every_value_the_parser_accepts_is_named_in_the_prompt_and_the_prompt_defines_the_kinds_by_what_the_line_is_mainly_about():
    text = nm.MOMENTS_USER.format(lines="[m1] Sat 3 Oct: x", cap=8)
    for enum in (nm.KINDS, nm.FEELINGS, nm.LATER):
        for v in enum:
            assert re.search(rf"\b{v}\b", text), v                # a value the parser takes but the prompt never offers is dead; one the prompt offers and the parser drops is a silent 'other'
    assert "the main thing the line is about" in text and "person (only a plain fact about someone they know)" in text
    assert "EVERY line except a command" in text and "At most 8" in text and "keep the 8 that matter most" in text


def test_the_prompt_is_one_template_that_formats_and_asks_for_one_line_of_json():
    text = nm.MOMENTS_USER.format(lines="[m1] Sat 3 Oct: x", cap=8)
    assert "{lines}" not in text and "ONE line" in text
    example = re.search(r"\(two shown\): (\{.*?\}), at most 8", text, re.S).group(1)
    assert len(json.loads(example)["moments"]) == 2                 # the example in the prompt is itself valid JSON of the shape the parser reads


def test_the_prompt_carries_no_detector_that_only_works_in_english():
    """Cue words live in ``lexicons_data/<lang>.json``; the prompt is an instruction to a model that reads any language, and no regex rides on top of it."""
    assert not re.search(r"re\.compile", nm.MOMENTS_USER)
    src = Path(nm.__file__).read_text()
    block = src[src.index("def parse_moments"):src.index("def unreached_tail")]
    assert "re.compile" not in block and "lower().split" not in block

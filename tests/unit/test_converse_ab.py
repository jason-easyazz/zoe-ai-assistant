"""converse_ab.py - the mirror-rate / conversation-quality A/B: its scorers, its keep rule, and the integrity of what it saves.

Negative controls (each is a reply or a table that MUST fail; flip the scorer or the rule and the test goes red):
* an "It sounds like ..." opener, a mirror followed by a generic question, a generic closer after substance, two questions, a sympathy-only
  reply, a monologue, boilerplate / an invented apology for feedback, a repeat of the previous answer: all FAIL the composite;
* decide(): identical arms never KEEP, a kind that separates down or fewer answered controls blocks a KEEP, a missing comparison is DO NOT KEEP;
* the frozen split and the fingerprint: editing the shipped text, the scorer lexicon or a held-out ask makes old rows unusable.

No network, no live service, no flag. UNMARKED (Jetson full-directory lane): it imports services/zoe-data (mirror_guard, the Flue client), which the
slim GitHub lane does not carry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
if str(PERF) not in sys.path:
    sys.path.insert(0, str(PERF))

import converse_ab as cab  # noqa: E402
import manner_block_ab as mab  # noqa: E402


def test_the_selftest_passes(capsys):
    assert cab.selftest() == 0, capsys.readouterr().out


def test_importing_the_harness_does_not_touch_the_manner_ab():
    """The shared rig is re-pointed by ``configure_rig`` (once per CLI run), never at import: a unit test importing this file must not change
    the other A/B's cache, ledger, budget or variants."""
    assert mab.HARNESS_VERSION == "manner-ab-2" and mab.TOTAL_BUDGET_S == 1500.0
    assert "LEGACY" not in mab.VARIANTS and "C1" not in mab.VARIANTS


def test_the_legacy_text_is_the_one_that_shipped_and_tells_the_model_to_mirror():
    assert "first say back what you heard" in cab.LEGACY
    assert all("first say back" not in t and "say back what you heard" not in t for t in cab.CANDIDATES.values())


def test_the_shipped_text_has_no_mirror_instruction_and_the_legacy_text_is_the_control():
    """Positive control for the detector: it flags the legacy text, so 'the shipped text has none' means something."""
    import re

    bad = re.compile(r"say back|say it back|(?<!never )(?<!not )restate|paraphrase|reflect (?:back|what)|repeat (?:back|what they)|mirror", re.I)
    cab.zoe_path()
    import manner_block

    assert bad.search(cab.LEGACY)
    assert not bad.search(manner_block.block_text("en")), "the manner block tells the model to restate what the person said"


def test_every_candidate_fits_the_wire_and_the_budget():
    cab.zoe_path()
    import manner_block

    for name, text in cab.CANDIDATES.items():
        assert text and "\n" not in text, name
        assert len(text) <= manner_block.WIRE_MAX_CHARS, (name, len(text))
        assert manner_block.estimate_tokens(text) <= manner_block.MAX_BLOCK_TOKENS, (name, manner_block.estimate_tokens(text))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(mab, "CACHE", tmp_path)
    monkeypatch.setattr(cab, "CACHE", tmp_path)
    return tmp_path


def _rows(phase, arm, fp, replies_by_ask, plan_digest="p"):
    out = []
    for aid, kind, turns, _nd in cab.corpus(phase):
        for s in range(cab.SAMPLES[phase]):
            out.append({"arm": arm, "phase": phase, "ask": aid, "sample": s, "inv": "i", "fp": fp, "plan": plan_digest, "kind": kind,
                        "replies": replies_by_ask(aid, kind, turns)})
    return out


def _save(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_report_refuses_rows_saved_under_another_text(scratch):
    fp_none = cab.fingerprint("none")
    _save(scratch / "r.jsonl", _rows("dev", "none", "not-the-current-fingerprint", lambda *a: ["Eight legs."]) + _rows("dev", "on:V0", cab.fingerprint("on:V0"), lambda *a: ["x"]))
    out = cab.report("r", "dev", ["none", "on:V0"], "on:V0")
    assert out.startswith("REFUSED") and "different text" in out
    assert fp_none != cab.fingerprint("on:LEGACY")                      # the arm is part of the fingerprint
    assert cab.fingerprint("on:C1") != cab.fingerprint("on:C2")         # and so is the text


def test_the_report_demands_every_planned_ask_in_every_arm(scratch):
    ok = lambda aid, kind, turns: ["Eight legs, two more than an insect."] * len(turns)    # noqa: E731
    plan = lambda phase, arm: _rows(phase, arm, cab.fingerprint(arm), ok)    # noqa: E731
    rows = plan("heldout", "none") + plan("heldout", "on:V0")[:-3]            # the shipped arm is missing its last ask
    _save(scratch / "r.jsonl", rows)
    out = cab.report("r", "heldout", ["none", "on:V0"], "on:V0")
    assert "planned asks have a row" in out and "DO NOT KEEP" in out


def test_a_complete_run_is_scored_and_a_better_arm_is_kept(scratch):
    """End to end on synthetic rows: 'none' mirrors, 'on:V0' converses. The report must show it AND the keep rule must agree; swap the arms
    and it must not."""
    def mirror(aid, kind, turns):
        if kind == "followup":
            return ["The sea is salty because rivers carry minerals to it.", "The sea is salty because rivers carry minerals to it."]
        if kind == "control":
            return ["It sounds like you are curious. Spiders have eight legs."]
        return ["It sounds like such a lot to deal with. How are you holding up?"]

    def converse(aid, kind, turns):
        if kind == "followup":
            return ["The sea is salty because rivers carry minerals to it.", "The Dead Sea is ten times saltier because nothing drains out of it."]
        if kind == "control":
            return ["Spiders have eight legs, two more than insects do."]
        if kind == "feedback":
            nd = {a: n for a, _k, _t, n in cab.corpus("heldout")}[aid]
            return [f"Fair point about the {nd[0]}. I will fix that next time round."]
        return ["Late buses are the worst, so ask if they can move it to tomorrow and bring a book."]

    _save(scratch / "r.jsonl", _rows("heldout", "none", cab.fingerprint("none"), mirror) + _rows("heldout", "on:V0", cab.fingerprint("on:V0"), converse))
    out = cab.report("r", "heldout", ["none", "on:V0"], "on:V0")
    assert "VERDICT (on:V0 vs none, heldout): KEEP" in out, out
    _save(scratch / "r2.jsonl", _rows("heldout", "none", cab.fingerprint("none"), converse) + _rows("heldout", "on:V0", cab.fingerprint("on:V0"), mirror))
    assert "DO NOT KEEP" in cab.report("r2", "heldout", ["none", "on:V0"], "on:V0")


def test_the_post_guard_figure_applies_the_real_trim():
    raw = ["It sounds like you had a frustrating morning. Late buses are the worst, so ask if they can move it to tomorrow."]
    assert cab.post_guard(raw) == ["Late buses are the worst, so ask if they can move it to tomorrow."]
    assert cab.features(raw[0], "my bus was late")["mirror_open"] is True
    assert cab.features(cab.post_guard(raw)[0], "my bus was late")["mirror_open"] is False

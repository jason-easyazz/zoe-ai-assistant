"""Unit tests for the router self-training miner (labs/router-selftrain).

Covers the three properties the autonomous train→promote loop depends on:
  1. the HELD-OUT GUARD aborts loudly (and writes nothing) on any collision with
     the frozen eval corpus — the loop's whole safety property;
  2. dedup drops repeats within a round and anything already in the train sets;
  3. each mining reason produces the right shape, and the oracle contract
     (grammar-constrained, off-domain/unclean labels dropped) holds.

Slim-dep-green: the miner's only import is the stdlib-only router_two_stage;
httpx/asyncpg are lazy, and the brain is mocked here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

pytestmark = pytest.mark.ci_safe

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MINER_PY = os.path.join(REPO, "labs", "router-selftrain", "mine_candidates.py")


def _load_miner():
    spec = importlib.util.spec_from_file_location("mine_candidates", MINER_PY)
    mod = importlib.util.module_from_spec(spec)
    # register before exec: @dataclass resolves its module out of sys.modules
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


miner = _load_miner()


# ── fixtures ────────────────────────────────────────────────────────────────
def _rec(utt_text, actual, ts_domain, *, tool="x", gated=False, mode="active"):
    return {
        "ts": 1783992283.0,
        "mode": mode,
        "utt": miner.utt_hash(utt_text),
        "utt_text": utt_text,
        "two_stage_tool": tool,
        "two_stage_domain": ts_domain,
        "gated": gated,
        "failed": False,
        "actual_routed": actual,
    }


@pytest.fixture
def shadow():
    return [
        # disagreement: live routed calendar, two-stage said reminders
        _rec("put dinner with mum in the diary friday", "calendar", "reminders",
             tool="add_reminder"),
        # abstention: gated, but the live route was a real tool domain
        _rec("chuck oat milk on the shopping list", "lists", None,
             tool=None, gated=True),
        # chat-negative (agreement): both said chat
        _rec("what do you reckon about the footy", "chat", "chat", tool=None),
        # chat-negative (false positive): live chatted, two-stage fired a tool
        _rec("tell me a joke", "chat", "timers", tool="set_timer"),
        # agreement on a tool — nothing to learn, must NOT be mined
        _rec("what is the time", "time", "time", tool="get_time"),
        # head-shadow-only record (no two-stage decision) — must be ignored
        {"ts": 1783992283.0, "utt": miner.utt_hash("ignored"),
         "utt_text": "ignored", "actual_routed": "lists"},
    ]


# ── 1. the held-out guard ───────────────────────────────────────────────────
def test_holdout_guard_aborts_on_frozen_corpus_collision(tmp_path):
    holdout = {miner.normalize("what time is it")}
    # normalisation must catch case/punctuation-shifted variants, not just exact text
    cands = [miner.Candidate(text="What time is it?", reason="disagreement",
                             domain="time")]
    with pytest.raises(miner.HoldoutViolation) as exc:
        miner.assert_held_out(cands, holdout)
    assert "HELD-OUT GUARD TRIPPED" in str(exc.value)

    # and nothing is written: the guard runs before write_outputs, so the output
    # dir stays empty when the abort fires
    out = tmp_path / "router_selftrain"
    assert not out.exists()


def test_holdout_guard_passes_on_clean_set():
    holdout = {miner.normalize("what time is it")}
    clean = [miner.Candidate(text="add milk to the list", reason="abstention",
                             domain="lists")]
    miner.assert_held_out(clean, holdout)  # must not raise


def test_real_frozen_corpus_loads_and_is_normalized():
    holdout = miner.load_holdout()
    assert len(holdout) >= 50
    assert "what time is it" in holdout       # normalised form of a real entry
    assert all(h == miner.normalize(h) for h in holdout)


# ── 2. dedup ────────────────────────────────────────────────────────────────
def test_dedup_drops_existing_and_intra_round_repeats():
    existing = {miner.normalize("set a timer for 5 minutes")}
    cands = [
        miner.Candidate(text="Set a timer for 5 minutes!", reason="disagreement",
                        domain="timers"),          # already in the train sets
        miner.Candidate(text="add eggs to the list", reason="abstention",
                        domain="lists"),
        miner.Candidate(text="Add eggs to the list.", reason="abstention",
                        domain="lists"),           # intra-round repeat
    ]
    out = miner.dedup(cands, existing)
    assert [c.text for c in out] == ["add eggs to the list"]


def test_load_existing_texts_reads_the_real_train_sets():
    existing = miner.load_existing_texts()
    assert len(existing) > 1000                    # ~4k committed examples
    assert all(t == miner.normalize(t) for t in existing)


# ── 3. mining reasons ───────────────────────────────────────────────────────
def test_mine_produces_each_reason_with_the_right_shape(shadow):
    texts = miner.hash_to_text(shadow, {})
    cands = miner.mine(shadow, texts)
    by_text = {c.text: c for c in cands}

    assert by_text["put dinner with mum in the diary friday"].reason == "disagreement"
    assert by_text["put dinner with mum in the diary friday"].domain == "calendar"

    assert by_text["chuck oat milk on the shopping list"].reason == "abstention"
    assert by_text["chuck oat milk on the shopping list"].domain == "lists"

    assert by_text["what do you reckon about the footy"].reason == "chat-negative"
    assert by_text["tell me a joke"].reason == "chat-negative"

    # agreement on a tool, and the head-shadow-only record, are NOT mined
    assert "what is the time" not in by_text
    assert "ignored" not in by_text


def test_mine_prioritises_false_positives_when_the_negative_cap_bites(shadow):
    texts = miner.hash_to_text(shadow, {})
    cands = miner.mine(shadow, texts, max_chat_negatives=1)
    negs = [c for c in cands if c.reason == "chat-negative"]
    # the measured mistake (live chat, two-stage fired a tool) outranks plain
    # reinforcement when only one negative slot is left
    assert [c.text for c in negs] == ["tell me a joke"]


def test_mine_caps_total(shadow):
    assert len(miner.mine(shadow, miner.hash_to_text(shadow, {}), max_total=2)) == 2


def test_mine_skips_utterances_whose_hash_never_resolved():
    # no utt_text, no chat_messages hit → nothing to train on, must be skipped
    rec = {"ts": 1.0, "mode": "active", "utt": "deadbeefcafe",
           "two_stage_domain": "reminders", "two_stage_tool": "add_reminder",
           "actual_routed": "calendar"}
    assert miner.mine([rec], {}) == []


# ── the hash join ───────────────────────────────────────────────────────────
def test_utt_hash_matches_the_router_and_joins_chat_messages():
    text = "Add dentist appointment tomorrow at 3pm"
    # byte-identical to semantic_router's `utt` field (sha256[:12]) — this is the
    # contract the whole BOOTSTRAP join rests on
    import hashlib
    assert miner.utt_hash(text) == hashlib.sha256(text.encode()).hexdigest()[:12]

    rec = {"ts": 1.0, "mode": "active", "utt": miner.utt_hash(text),
           "two_stage_domain": "reminders", "two_stage_tool": "add_reminder",
           "actual_routed": "calendar"}
    texts = miner.hash_to_text([rec], {miner.utt_hash(text): text})
    cands = miner.mine([rec], texts)
    assert [(c.text, c.reason) for c in cands] == [(text, "disagreement")]


def test_forward_utt_text_wins_without_a_db_join():
    rec = _rec("remind me to call dad", "reminders", None, tool=None, gated=True)
    texts = miner.hash_to_text([rec], {})      # empty chat_messages
    assert texts[rec["utt"]] == "remind me to call dad"


# ── the oracle contract ─────────────────────────────────────────────────────
def test_label_schema_constrains_to_legal_names_plus_none():
    schema = miner.label_schema(["set_timer", "add_reminder"])
    assert schema["properties"]["tool"]["enum"] == ["add_reminder", "set_timer", "none"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["tool", "args"]


def test_label_fills_gold_call_and_emits_the_train_row_shape():
    cand = miner.Candidate(text="chuck oat milk on the shopping list",
                           reason="abstention", domain="lists")

    def fake_brain(text, names):
        # the oracle is INDEPENDENT: always the full menu, never told the live route
        assert set(names) == set(miner.TOOL_DOMAIN)
        return {"tool": "shopping_list_add", "args": {"item": "oat milk", "bogus": 1}}

    out = miner.label([cand], fake_brain)
    assert len(out) == 1
    assert out[0].as_train_row() == {
        "text": "chuck oat milk on the shopping list",
        "tool": "shopping_list_add",
        "args": {"item": "oat milk"},          # illegal arg key dropped
        "source": "selftrain-abstention",
    }


def test_label_keeps_a_chat_negative_only_when_the_oracle_also_declines():
    keep = miner.Candidate(text="what do you reckon", reason="chat-negative",
                           domain="chat")
    drop = miner.Candidate(text="set a timer", reason="chat-negative", domain="chat")

    def fake_brain(text, names):
        return ({"tool": "none", "args": {}} if text == "what do you reckon"
                else {"tool": "set_timer", "args": {"minutes": 5}})

    out = miner.label([keep, drop], fake_brain)
    assert [c.as_train_row() for c in out] == [{
        "text": "what do you reckon", "tool": None, "args": {},
        "source": "selftrain-chat-negative",
    }]


def test_label_drops_the_live_routers_own_misroutes():
    """The load-bearing guard: the live route is NOT trusted on its own.

    The first dry-run turned up a real case of the live similarity router sending
    a smart-home utterance to the `timers` domain. Modelled here with a synthetic
    equivalent: the oracle independently says `home` (smart_home), the domains
    disagree, so the row is DROPPED rather than teaching the router to set a timer
    for a light.
    """
    cands = [
        miner.Candidate(text="brain failed", reason="disagreement", domain="calendar"),
        miner.Candidate(text="oracle declines", reason="disagreement", domain="calendar"),
        miner.Candidate(text="switch on the porch lamp", reason="disagreement",
                        domain="timers"),
    ]

    def fake_brain(text, names):
        if text == "brain failed":
            return None                                  # unusable answer → skip
        if text == "oracle declines":
            return {"tool": "none", "args": {}}          # won't complete it → skip
        return {"tool": "home", "args": {"action": "on"}}  # off-domain → skip

    assert miner.label(cands, fake_brain) == []


def test_label_keeps_a_row_when_oracle_and_live_route_agree():
    cand = miner.Candidate(text="put dinner in the diary friday",
                           reason="disagreement", domain="calendar")

    def fake_brain(text, names):
        return {"tool": "add_calendar_event",
                "args": {"title": "dinner", "date": "friday"}}

    out = miner.label([cand], fake_brain)
    assert out[0].tool == "add_calendar_event"
    assert out[0].as_train_row()["source"] == "selftrain-disagreement"


# ── output contract (what lane B consumes) ──────────────────────────────────
def test_write_outputs_emits_the_train_lora_shape_and_meta(tmp_path):
    cands = [
        miner.Candidate(text="add milk", reason="abstention", domain="lists",
                        tool="shopping_list_add", args={"item": "milk"}),
        miner.Candidate(text="hiya", reason="chat-negative", domain="chat"),
    ]
    meta = {"counts": {"total": 2}, "held_out_guard": {"result": "pass"}}
    jsonl, metaf = miner.write_outputs(cands, meta, str(tmp_path), stamp="20260714T000000Z")

    assert os.path.basename(jsonl) == "candidate_20260714T000000Z.jsonl"
    assert os.path.basename(metaf) == "candidate_20260714T000000Z.meta.json"

    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    assert rows[0] == {"args": {"item": "milk"}, "source": "selftrain-abstention",
                       "text": "add milk", "tool": "shopping_list_add"}
    assert rows[1]["tool"] is None                       # no-tool gold
    assert set(rows[0]) == {"text", "tool", "args", "source"}
    assert json.load(open(metaf, encoding="utf-8"))["held_out_guard"]["result"] == "pass"

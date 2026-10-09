"""The personalisation hop (Samantha day-sim S9a / S9b, ZOE_PERSONALISATION_HOP).

The miss (day-sim 2026-10-03, "no-card baselines all FAIL"): a user who told Zoe on day 1 that they work night shifts
and walk the dog at 6am got generic night-time sleep advice and "fine without a jacket" days later. Neither question is
a recall question, so no packet was fetched; the facts share no words with the questions, so no search would find them;
the card that is meant to carry them is not served to synthetic users. The hop reads the owner's DURABLE facts (no decay,
#1911) on a generic-advice request and puts the <= 2 that change the answer into the packet under "Shape the answer by".

Groups: the request shape, the fact selection (and what must NOT be shown), the store read (durable only, owner only, no
decay), the packet (for-prompt), the Flue seam (end to end, one block per turn), and the flag-off controls.

Synthetic data, fake Chroma (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import memory_service
import personalisation_hop as hop
import zoe_flue_client as zc
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
NIGHT = "I work night shifts in the hospital pharmacy, so I sleep during the day."
WALK = "Every morning at 6am I walk our kelpie Juniper along the river before I go to bed."
FISH = "I'm pescatarian, so fish is fine but I don't eat any meat."
SLEEP_ASK = "Any tips for sleeping better?"
COLD_ASK = "What should I wear tomorrow? It's meant to be really cold."
COOK_ASK = "What should I cook tonight?"


def row(i, text, **meta):
    return SimpleNamespace(id=f"mem{i}", text=text, metadata=meta)


@pytest.fixture
def svc(monkeypatch):
    for k in (hop.ENV, "ZOE_MEMORY_AUTHORITY", "ZOE_RECALL_DURABLE_NO_DECAY", "ZOE_SEAM_RECALL_INJECT",
              "ZOE_SEAM_CONTINUITY_INJECT"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-hop")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def put(svc, text, user=UID, source="voice_fact", **kw):
    return asyncio.run(svc.ingest(text, user_id=user, source=source, status="approved", **kw))


def seed_week(svc):
    for t in (NIGHT, WALK, FISH):
        put(svc, t)


@pytest.fixture
def seeded(svc):
    """The week's three facts, written BEFORE an async test's loop starts."""
    seed_week(svc)
    return svc


def build(svc, message, user=UID):
    return asyncio.run(hop.build(user, message, svc=svc))


# ── the request shape ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg, topic", [
    (SLEEP_ASK, "sleep"),
    (COLD_ASK, "clothing"),
    (COOK_ASK, "food"),
    ("Can you suggest something for dinner?", "food"),
    ("Any ideas for a birthday present for my kid?", "kids"),
    ("What should I pack for the trip?", "travel"),
    ("Any advice on running a 10k?", "activity"),
    ("Tips for staying focused while I study?", "routine"),
    ("How can I sleep better?", "sleep"),
])
def test_a_generic_advice_request_has_its_topic(msg, topic):
    assert hop.is_advice_request(msg) and topic in hop.advice_topics(msg)


@pytest.mark.parametrize("msg", [
    "What's my favourite tea?", "When did I tell you about the dentist?", "Do you remember what I said about sleep?",
    "What time is it?", "Add milk to the shopping list", "Tell me a joke", "It's cold today",
    "I slept badly last night", "Who is my dentist?", "",
    "Any tips?",   # advice-shaped but no topic: nothing to hop on
])
def test_everything_else_is_not_a_hop_turn(msg):
    assert hop.advice_topics(msg) == ()


# ── which facts qualify ───────────────────────────────────────────────────────────────────

def test_the_night_shift_answers_the_sleep_ask_and_not_the_clothing_ask():
    rows = [row(1, NIGHT), row(2, WALK), row(3, FISH)]
    assert [f.id for f in hop.select(SLEEP_ASK, rows).facts] == ["mem1"]
    assert [f.id for f in hop.select(COLD_ASK, rows).facts] == ["mem2"]
    assert [f.id for f in hop.select(COOK_ASK, rows).facts] == ["mem3"]


def test_at_most_two_facts_most_specific_first_and_bounded():
    rows = [row(1, NIGHT, added_ts=1.0),
            row(2, "I have a newborn.", added_ts=2.0),
            row(3, "I'm on call every third weekend and a light sleeper.", added_ts=3.0),
            row(4, "I snore a lot.", added_ts=4.0)]
    got = hop.select(SLEEP_ASK, rows)
    assert len(got.facts) == hop.MAX_FACTS == 2
    assert got.facts[0].id == "mem1"          # night shifts + sleeping during the day: two constraints beat one
    long = row(9, "I work night shifts. " + "x" * 400)
    assert all(len(f.text) <= hop.FACT_CHARS for f in hop.select(SLEEP_ASK, [long]).facts)


@pytest.mark.parametrize("text", [
    "I no longer work night shifts.",                 # dropped
    "I used to work night shifts in the pharmacy.",
    "I gave up the night shift last month.",
    "My mum works night shifts at the hospital.",     # somebody else's
    "User's sister works night shifts.",
    "Dana works night shifts in Hobart.",             # a named third person
    "My wife sleeps during the day after her night shift.",
])
def test_a_fact_that_is_not_the_owners_current_state_is_not_shown(text):
    assert hop.select(SLEEP_ASK, [row(1, text)]).facts == ()


def test_a_childs_age_is_the_owners_fact_for_the_kids_lens():
    got = hop.select("Any ideas for a birthday present for my son?", [row(1, "My son Rowan is 4 years old.")])
    assert [f.text for f in got.facts] == ["My son Rowan is 4 years old."]


def test_the_section_is_two_bullets_at_most_and_one_rule_line():
    sec = hop.select(SLEEP_ASK, [row(1, NIGHT)]).section()
    lines = sec.splitlines()
    assert lines[0] == hop.HEADING and lines[-1] == hop.RULE and len(lines) == 3
    assert lines[1] == f"- {NIGHT} [mem:mem1]" and "\n" not in hop.RULE
    assert hop.select(COOK_ASK, []).section() == "" and not hop.select(COOK_ASK, [])


# ── the store read ────────────────────────────────────────────────────────────────────────

def test_the_hop_finds_the_facts_for_the_day_sim_asks(svc):
    seed_week(svc)
    assert [f.text for f in build(svc, SLEEP_ASK).facts] == [NIGHT]
    assert [f.text for f in build(svc, COLD_ASK).facts] == [WALK]


# ── S9b 2026-10-09: the live stored wording of the seeded facts (rows=3 facts=0) ──────────────

#: EXACTLY what the turn digest stored (``MEMORY_ROW ... class=user_stated_derived promoted=no basis=anchored_user_turn``) for
#: WALK ("... walk OUR kelpie ...": the paraphrase changed a word, so it is a derived row, rank 3, not promoted to user_stated)
LIVE_WALK = "User walks their kelpie Juniper along the river every morning at 6am."
LIVE_PET = "User's kelpie is named Juniper."
LIVE_SHIFT = "User works night shifts in the hospital pharmacy"
LIVE_SLEEPS = "User sleeps during the day"


def put_digest(svc, text, anchor, user=UID):
    """A row as the live per-turn digest writes it: a model paraphrase anchored on the owner's own turn."""
    return asyncio.run(svc.ingest(text, user_id=user, source="turn_digest", status="approved", anchor_text=anchor))


def test_the_live_walk_row_is_a_derived_row_the_hop_must_read(svc):
    import memory_authority as ma

    ref = put_digest(svc, LIVE_WALK, WALK)
    # the instrument: the row really is the live class (derived, not promoted) - else this test proves nothing
    assert ma.row_class(ref.metadata, LIVE_WALK) == ma.USER_STATED_DERIVED
    assert ma.row_rank(ref.metadata, LIVE_WALK) == ma.DERIVED_RANK < ma.USER_RANK
    assert [r.text for r in asyncio.run(svc.load_durable_for_hop(UID))] == [LIVE_WALK]
    assert [f.text for f in build(svc, COLD_ASK).facts] == [LIVE_WALK]


def test_the_live_week_of_rows_the_cold_ask_finds_the_walk_and_the_sleep_ask_the_shift(svc):
    for t, a in ((LIVE_SHIFT, NIGHT), (LIVE_SLEEPS, NIGHT), (LIVE_WALK, WALK), (LIVE_PET, WALK)):
        put_digest(svc, t, a)
    assert [f.text for f in build(svc, COLD_ASK).facts] == [LIVE_WALK]            # the pet's NAME is not a clothing constraint
    assert LIVE_SHIFT in [f.text for f in build(svc, SLEEP_ASK).facts]


def test_relevance_is_the_fact_content_for_the_clothing_lens():
    cold = hop.select(COLD_ASK, [row(1, LIVE_WALK)]).facts
    assert [f.text for f in cold] == [LIVE_WALK]
    control = hop.select(COLD_ASK, [row(2, "User likes jazz."), row(3, LIVE_PET)]).facts
    assert control == ()
    assert hop.select(SLEEP_ASK, [row(4, "User likes jazz.")]).facts == ()


@pytest.mark.parametrize("text", [
    "User takes their dog out along the river every morning at 6am.",             # no "walk" word at all
    "User cycles to the depot at 5:30am.",
    "User goes surfing before dawn every day.",
    "User jogs the beach loop at first light.",
    "User drives to the quarry site at 5am each day.",
    "Routine: dog walk, 6am daily.",                                              # a structural / role style row
    "User is a dog-walker, out the door at 6am.",
])
def test_an_outdoor_routine_at_a_fixed_time_is_clothing_relevant_whatever_the_wording(text):
    assert [f.text for f in hop.select(COLD_ASK, [row(1, text)]).facts] == [text]


@pytest.mark.parametrize("text", [
    "User likes jazz.",
    "User runs a bakery every day.",                       # "runs" + a daily cue is a job, not an outing
    "User eats fish every day.",
    "User's kelpie is named Juniper.",
    "User has a meeting at 6am.",                          # a time with no going-out verb
])
def test_a_time_or_a_verb_alone_is_not_a_clothing_constraint(text):
    assert hop.select(COLD_ASK, [row(1, text)]).facts == ()


def test_a_model_only_row_and_an_unverified_speaker_are_still_not_durable(svc):
    put(svc, "User walks the dog at 6am every morning.", source="digest")            # a nightly model's inference: no anchor
    put_digest(svc, "User walks the dog at 6am every morning.", "What a lovely day it is today.", user=UID)  # anchor does not support
    assert asyncio.run(svc.load_durable_for_hop(UID)) == []


def test_a_derived_row_of_another_member_is_not_read(svc):
    put_digest(svc, LIVE_WALK, WALK, user=OTHER)
    assert asyncio.run(svc.load_durable_for_hop(UID)) == []


def test_the_rows_the_hop_served_are_recorded_for_why_did_you_say_that(svc):
    """The hop puts a durable fact in front of the brain without the recall tool: provenance must know, on the Flue path and on the packet path."""
    import memory_provenance as mp
    from routers import memories

    put(svc, NIGHT)
    mp.reset() if hasattr(mp, "reset") else mp._STATES.clear()
    mp.begin_turn(UID)
    asyncio.run(zc._hop_context_block(SLEEP_ASK, UID))
    assert [t for _ts, rows, _xw in mp._STATES[UID].served for _i, t in rows] == [NIGHT]
    mp._STATES.clear()
    mp.begin_turn(UID)
    asyncio.run(memories.memory_for_prompt(user_id=UID, message=SLEEP_ASK, limit=12, _=None))
    assert NIGHT in [t for _ts, rows, _xw in mp._STATES[UID].served for _i, t in rows]


ALLERGY = "I'm allergic to peanuts, so nothing with nuts please."


def test_a_sensitive_fact_is_not_put_in_front_of_the_brain_for_an_unconfirmed_voice(svc, monkeypatch):
    import restraint

    put(svc, ALLERGY)
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    assert [f.text for f in build(svc, COOK_ASK).facts] == [ALLERGY]                 # no verdict (typed / gate off): the owner's own advice request pulls it
    restraint.bind_verdict(True)
    try:
        assert [f.text for f in build(svc, COOK_ASK).facts] == [ALLERGY]             # a confirmed member: delivered
        restraint.bind_verdict(False)
        assert build(svc, COOK_ASK).facts == ()                                      # the gate did NOT confirm the speaker: withheld
        monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
        assert [f.text for f in build(svc, COOK_ASK).facts] == [ALLERGY]             # control: shadow only logs
    finally:
        restraint.bind_verdict(None)


def test_durable_facts_do_not_decay_a_year_old_fact_is_still_shown(svc):
    put(svc, NIGHT)
    (rid, (doc, meta)), = svc._col.rows.items()
    meta["added_at"] = "2025-10-01T00:00:00Z"
    meta["added_ts"] = time.time() - 365 * 86400
    svc._col.rows[rid] = (doc, meta)
    assert [f.text for f in build(svc, SLEEP_ASK).facts] == [NIGHT]
    # while the ranked read the packet uses would have buried it under a 70-day half-life
    assert asyncio.run(svc.load_for_prompt(UID, limit=20))[0].text == NIGHT   # (one row: only the order differs)


def test_only_what_the_owner_stated_is_durable(svc):
    put(svc, "I work night shifts at the cannery.", source="digest")                  # a nightly model's inference
    put(svc, "User feels exhausted after night shifts.", source="turn_digest",
        memory_type="emotional_moment")                                                # a mood
    put(svc, "My brother works night shifts too.")                                    # somebody else
    assert build(svc, SLEEP_ASK).facts == ()
    put(svc, NIGHT)
    assert [f.text for f in build(svc, SLEEP_ASK).facts] == [NIGHT]


def test_only_the_owners_own_rows_are_read(svc):
    put(svc, NIGHT, user=OTHER)
    assert build(svc, SLEEP_ASK).facts == ()
    # a family-visible row another member wrote is not this member's fact
    put(svc, "I work night shifts at the cannery.", user=OTHER, scope="shared")
    assert build(svc, SLEEP_ASK, user=UID).facts == ()


def test_a_superseded_or_rejected_row_is_not_shown(svc):
    ref = put(svc, NIGHT)
    asyncio.run(svc.review(ref.id, decision="reject", actor=UID, note="forget_last"))
    assert build(svc, SLEEP_ASK).facts == ()


def test_a_pasted_row_is_never_a_fact(svc):
    put(svc, NIGHT, metadata={"pasted": True, "pasted_content": True}, source="pasted_content")
    assert build(svc, SLEEP_ASK).facts == ()


def test_a_guest_and_a_non_advice_turn_read_nothing(svc, monkeypatch):
    seed_week(svc)
    reads = []
    orig = svc.load_durable_for_hop

    async def spy(u):
        reads.append(u)
        return await orig(u)

    monkeypatch.setattr(svc, "load_durable_for_hop", spy)
    assert not build(svc, SLEEP_ASK, user="guest") and not build(svc, "What's my favourite tea?")
    assert reads == []                                   # no read at all: the turn costs nothing
    assert build(svc, SLEEP_ASK) and reads == [UID]


def test_a_slow_store_costs_the_block_never_the_turn(svc, monkeypatch):
    seed_week(svc)
    monkeypatch.setattr(hop, "TIMEOUT_S", 0.05)

    async def slow(_u):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(svc, "load_durable_for_hop", slow)
    t0 = time.monotonic()
    assert not build(svc, SLEEP_ASK) and time.monotonic() - t0 < 0.5


def test_a_failing_store_is_no_hop(svc, monkeypatch):
    async def boom(_u):
        raise RuntimeError("chroma is down")

    monkeypatch.setattr(svc, "load_durable_for_hop", boom)
    assert not build(svc, SLEEP_ASK)


# ── flag off: negative controls ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
def test_flag_off_the_hop_is_empty(svc, monkeypatch, value):
    seed_week(svc)
    monkeypatch.setenv(hop.ENV, value)
    assert not build(svc, SLEEP_ASK) and not build(svc, COLD_ASK)


def test_flag_default_is_on(monkeypatch):
    monkeypatch.delenv(hop.ENV, raising=False)
    assert hop.enabled() is True


# ── the packet (for-prompt) ───────────────────────────────────────────────────────────────

def for_prompt(svc, message, user=UID):
    import routers.memories as rm
    monkeypatch_free = rm
    return asyncio.run(monkeypatch_free.memory_for_prompt(
        user_id=user, message=message, limit=12, mode="relevance", focus_people=None, force_recall=False, _=None))


def test_the_packet_carries_the_fact_with_one_rule_line(svc, monkeypatch):
    import routers.memories as rm
    monkeypatch.setattr(rm, "_svc", lambda: svc)
    seed_week(svc)
    out = for_prompt(svc, SLEEP_ASK)
    pkt = out["packet"]
    assert hop.HEADING in pkt and NIGHT in pkt and pkt.rstrip().endswith(hop.RULE) and out["hop"] == 1
    assert WALK not in pkt.split(hop.HEADING)[1]       # the section holds only the fact that changes the answer
    assert hop.HEADING in for_prompt(svc, COLD_ASK)["packet"].split("## What I know about you")[1]


def test_a_non_advice_message_gets_the_packet_it_always_got(svc, monkeypatch):
    import routers.memories as rm
    monkeypatch.setattr(rm, "_svc", lambda: svc)
    seed_week(svc)
    out = for_prompt(svc, "What's my favourite tea?")
    assert hop.HEADING not in out["packet"] and "hop" not in out


def test_packet_with_the_flag_off_has_no_section_negative_control(svc, monkeypatch):
    import routers.memories as rm
    monkeypatch.setattr(rm, "_svc", lambda: svc)
    monkeypatch.setenv(hop.ENV, "0")
    seed_week(svc)
    out = for_prompt(svc, SLEEP_ASK)
    assert hop.HEADING not in out["packet"] and "hop" not in out and hop.RULE not in out["packet"]


# ── the Flue seam, end to end ─────────────────────────────────────────────────────────────

class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": "ok"}}


class _Client:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _Resp()


async def _send(monkeypatch, message, uid=UID, **kwargs):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    # the wrapper adds the brief / raise blocks itself; a test that hands them in goes to the turn directly
    turn = zc._run_flue_brain_streaming_turn if kwargs else zc.run_flue_brain_streaming
    [c async for c in turn(message, "s9", uid, **kwargs)]
    return json.loads(_Client.captured["content"])["message"]


async def test_the_seam_sends_the_fact_after_the_users_words(seeded, monkeypatch):
    sent = await _send(monkeypatch, SLEEP_ASK)
    assert hop.HEADING in sent and NIGHT in sent and hop.RULE in sent
    assert sent.index(SLEEP_ASK) < sent.index(zc._HOP_BLOCK_OPEN) < sent.index(zc._RECALL_BLOCK_CLOSE)
    assert WALK not in sent and FISH not in sent
    sent = await _send(monkeypatch, COLD_ASK)
    assert WALK in sent and NIGHT not in sent


async def test_the_seam_is_byte_identical_when_there_is_nothing_to_add(seeded, monkeypatch):
    plain = await _send(monkeypatch, "Tell me a fun fact about octopuses.")
    assert hop.HEADING not in plain and zc._HOP_BLOCK_OPEN not in plain
    monkeypatch.setenv(hop.ENV, "0")
    off = await _send(monkeypatch, SLEEP_ASK)
    assert hop.HEADING not in off and zc._HOP_BLOCK_OPEN not in off and off.endswith(SLEEP_ASK)


async def test_one_block_per_turn_a_raise_or_a_brief_owns_the_turn(seeded, monkeypatch):
    for kw in ({"raise_block": "[RAISE] ask how the dentist went"}, {"day_brief_block": "[BRIEF] today: proofs"}):
        sent = await _send(monkeypatch, SLEEP_ASK, **kw)
        assert hop.HEADING not in sent, kw


async def test_a_recall_shaped_turn_keeps_its_recall_block_and_gets_no_second_block(seeded, monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")

    async def fetch(user_id, msg, focus=None):     # the recall packet, without the module-level service binding
        return "## What I know about you\n- I work night shifts in the hospital pharmacy [mem:a]"

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fetch)
    sent = await _send(monkeypatch, "What did I tell you about my work?")
    assert sent.count("[MEMORY CONTEXT") == 1 and zc._HOP_BLOCK_OPEN not in sent


async def test_a_guest_turn_never_reads_the_store(seeded, monkeypatch):
    sent = await _send(monkeypatch, SLEEP_ASK, uid="")
    assert hop.HEADING not in sent


# ── where the facts ride on the wire (ZOE_PERSONALISATION_HOP_PLACEMENT) ─────────────────────

def test_placement_default_values_and_a_typo_degrades_to_the_default(monkeypatch):
    monkeypatch.delenv(hop.PLACEMENT_ENV, raising=False)
    assert hop.placement() == hop.DEFAULT_PLACEMENT
    for v in hop.PLACEMENTS:
        monkeypatch.setenv(hop.PLACEMENT_ENV, v.upper())
        assert hop.placement() == v
    for junk in ("", "  ", "system", "after"):
        monkeypatch.setenv(hop.PLACEMENT_ENV, junk)
        assert hop.placement() == hop.DEFAULT_PLACEMENT


def test_the_suffix_is_one_parenthetical_line_without_a_heading_a_rule_or_an_id():
    h = hop.select(SLEEP_ASK, [row(1, NIGHT)])
    line = h.suffix_line()
    assert line.startswith(hop.SUFFIX_OPEN) and line.endswith(")") and "\n" not in line
    assert "night shifts" in line and hop.HEADING not in line and hop.RULE not in line and "[mem:" not in line
    assert hop.select(COOK_ASK, []).suffix_line() == ""
    nasty = hop.select(SLEEP_ASK, [row(1, "I work night shifts (nights) and\nsleep days.) (you know this about me: x")])
    assert nasty.suffix_line().count("\n") == 0 and nasty.suffix_line().count(")") == 1   # only the closing paren


def test_the_suffix_prefix_is_pinned_to_the_sidecars_elision_rule():
    import re
    from pathlib import Path

    ts = (Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/context-blocks.ts").read_text()
    m = re.search(r"HOP_NOTE_PREFIX = '([^']*)'", ts)
    assert m and m.group(1) == hop.SUFFIX_OPEN   # a drift is silent: every stale note would be replayed forever


async def test_block_placement_is_what_it_was(seeded, monkeypatch):
    monkeypatch.setenv(hop.PLACEMENT_ENV, "block")
    sent = await _send(monkeypatch, SLEEP_ASK)
    assert sent.index(SLEEP_ASK) < sent.index(zc._HOP_BLOCK_OPEN) < sent.index(hop.HEADING) < sent.index(hop.RULE)
    assert hop.SUFFIX_OPEN not in sent


async def test_suffix_placement_is_one_line_after_the_words_and_no_block(seeded, monkeypatch):
    monkeypatch.setenv(hop.PLACEMENT_ENV, "suffix")
    sent = await _send(monkeypatch, COLD_ASK)
    assert sent.endswith(COLD_ASK + "\n" + hop.select(COLD_ASK, [row(1, WALK)]).suffix_line())
    assert zc._HOP_BLOCK_OPEN not in sent and hop.HEADING not in sent and hop.RULE not in sent
    assert NIGHT not in sent and FISH not in sent


async def test_preamble_placement_is_the_block_before_the_words(seeded, monkeypatch):
    monkeypatch.setenv(hop.PLACEMENT_ENV, "preamble")
    sent = await _send(monkeypatch, SLEEP_ASK)
    assert sent.index(zc._HOP_BLOCK_OPEN) < sent.index(hop.HEADING) < sent.index(zc._RECALL_BLOCK_CLOSE) < sent.index(SLEEP_ASK)
    assert sent.count(hop.HEADING) == 1 and sent.endswith(SLEEP_ASK) and hop.SUFFIX_OPEN not in sent


@pytest.mark.parametrize("placement", hop.PLACEMENTS)
async def test_no_placement_touches_the_users_own_words(seeded, monkeypatch, placement):
    """The own-words wall: the hop text reaches the BRAIN's copy of the turn only. Everything that records or quotes what
    the owner said (exact words, quote-backed retirement, recall evidence, restraint) is handed the raw message."""
    import exact_words
    import memory_retire
    import recall_evidence
    import restraint

    monkeypatch.setenv(hop.PLACEMENT_ENV, placement)
    seen: list[tuple[str, str]] = []
    for mod in (exact_words, memory_retire, recall_evidence, restraint):
        monkeypatch.setattr(mod, "note_turn", lambda uid, msg, *a, _m=mod.__name__, **k: seen.append((_m, msg)))
    sent = await _send(monkeypatch, COLD_ASK)
    assert "kelpie Juniper" in sent and "kelpie" not in COLD_ASK              # the hop did ride the wire ...
    assert seen and all(msg == COLD_ASK for _m, msg in seen), seen             # ... and only the wire
    assert {m for m, _ in seen} == {"exact_words", "memory_retire", "recall_evidence", "restraint"}


async def test_suffix_is_never_written_into_the_stored_chat_transcript(seeded, monkeypatch):
    """The wire copy carries the suffix; the sidecar's session store then holds it - so the elision rule must drop it
    from every OLDER turn (ZOE_BRAIN_ELIDE_STALE_BLOCKS). The suffix is exactly the one-line shape that rule matches."""
    monkeypatch.setenv(hop.PLACEMENT_ENV, "suffix")
    sent = await _send(monkeypatch, COLD_ASK)
    last = sent.splitlines()[-1]
    assert last.startswith(hop.SUFFIX_OPEN) and last.endswith(")")
    assert sent.splitlines()[-2] == COLD_ASK


@pytest.mark.parametrize("placement", hop.PLACEMENTS)
async def test_a_non_advice_turn_is_byte_identical_under_every_placement(seeded, monkeypatch, placement):
    """S1-S8 / day-sim 1r / 7r are not advice requests: the placement flag must not move one byte of their wire message."""
    monkeypatch.setenv(hop.PLACEMENT_ENV, "block")
    base = {m: await _send(monkeypatch, m) for m in ("Who is flying in on Thursday?", "Tell me a fun fact about octopuses.",
                                                      "I've been feeling a bit on edge today.")}
    monkeypatch.setenv(hop.PLACEMENT_ENV, placement)
    for m, was in base.items():
        assert await _send(monkeypatch, m) == was


# ── an advice request the owner's facts change is the brain's, not a domain expert's (day-sim S9b) ────────────────────────────

def _tiers(monkeypatch, dispatch):
    import expert_dispatch
    import intent_router
    import semantic_router

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(intent_router, "detect_intent", lambda text, log_miss=False: None)
    monkeypatch.setattr(expert_dispatch, "dispatch", dispatch)
    return expert_dispatch


def _stub_build(monkeypatch, facts):
    async def _build(user_id, message, *, svc=None):
        return hop.select(message, [row(i, t) for i, t in enumerate(facts)])

    monkeypatch.setattr(hop, "build", _build)


async def _resolve(text):
    import fast_tiers

    return await fast_tiers.resolve(text, UID, "s", channel="chat", router_decision={"domain": "weather", "score": 0.95})


async def test_the_weather_expert_does_not_answer_an_advice_request_a_durable_fact_changes(monkeypatch):
    """Day-sim S9b 2026-10-09: 'What should I wear tomorrow? It's meant to be really cold.' (6 am dog walker) was answered by the
    weather expert - 'Yes, I'd take a jacket, it's around 19 degrees' - and the brain, with the fact in front of it, never ran."""
    ran = []

    async def dispatch(domain, text, ctx, *, write_ok=True):
        ran.append(domain)
        return ed.DispatchResult(domain=domain, reply="Yes, I'd take a jacket, it's around 19 degrees.")

    ed = _tiers(monkeypatch, dispatch)
    _stub_build(monkeypatch, [WALK])
    assert await _resolve(COLD_ASK) is None and ran == []


async def test_without_a_relevant_fact_or_without_an_advice_shape_the_expert_still_answers(monkeypatch):
    ran = []

    async def dispatch(domain, text, ctx, *, write_ok=True):
        ran.append(domain)
        return ed.DispatchResult(domain=domain, reply="Take a jacket.")

    ed = _tiers(monkeypatch, dispatch)
    _stub_build(monkeypatch, [NIGHT])                      # a durable fact, but not a constraint on clothing
    assert await _resolve(COLD_ASK) is not None and ran == ["weather"]
    ran.clear()
    _stub_build(monkeypatch, [WALK])
    assert await _resolve("What's the weather tomorrow?") is not None and ran == ["weather"]   # not an advice request


async def test_a_failing_hop_check_is_no_deferral(monkeypatch):
    async def dispatch(domain, text, ctx, *, write_ok=True):
        return ed.DispatchResult(domain=domain, reply="Take a jacket.")

    ed = _tiers(monkeypatch, dispatch)

    async def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(hop, "build", boom)
    assert await _resolve(COLD_ASK) is not None


# ── the request shapes the P-family diet asks use (P2.b was 8/20: only 2 of its 5 phrasings were hop turns) ──────────────────────

@pytest.mark.parametrize("ask", ["What should I cook tonight?", "Any dinner ideas?", "What's a good dinner for tonight?",
                                 "I can't decide what to make for tea.", "Suggest something for dinner."])
def test_every_phrasing_of_the_p2b_diet_asks_is_a_food_advice_request(ask):
    assert hop.advice_topics(ask) == ("food",), ask
    assert [f.id for f in hop.select(ask, [row(3, FISH)]).facts] == ["mem3"]


@pytest.mark.parametrize("msg", ["Any news today?", "Any updates on dinner?", "What's a good book", "What's for dinner at the Hendersons?"])
def test_the_wider_shapes_do_not_make_everything_an_advice_request(msg):
    assert hop.advice_topics(msg) == ()

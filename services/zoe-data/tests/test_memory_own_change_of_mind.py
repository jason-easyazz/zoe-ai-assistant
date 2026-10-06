"""ZMB C1 / C5 / I2 / A3 (Zoe Memory Bench, PR #1893): the owner's own change of mind updates the record,
an unverified speaker's self-fact is a candidate, and every row written from a user turn carries its evidence.

Every test copies the INPUTS of the bench cell it answers (scripts/perf/zmb/scenarios/*.json on
bench/zmb-axes-temporal-recall-poisoning) into the real ``MemoryService`` choke point over the where-honouring
Chroma stand-in; when #1893 merges, the cells flip from target to graded. Synthetic names only, no network, no model.

  C1.update_via_turn_digest    the typed "I live in X", then the per-turn digest's "User lives in Y" anchored on the
                               owner's "I moved to Y": the current fact must win (nightly conflict pass retires X)
  C5.retracted_via_turn_digest the taught "User's dentist is D", then the digest's "User no longer sees D" anchored on
                               "I no longer see D": the retracted fact is not served
  I2.third_party_fragment.panel_unverified
                               an unverified voice turn's self-fact is a pending candidate, never approved, never recalled
  A3.taught_rows / nightly_digest_rows / user_turn_rows_rate
                               source_excerpt + user_turn_id + authority_class on the teach lane and the nightly digest

Each fix has its break-the-fix control (patch the new rule out: the cell goes red) and a control that the rule does
NOT over-reach: a paraphrase the owner's turn does not plainly entail, a relative's move, a hedge, a speaker the
lane rejected, and the whole-day (nightly) writers are held back exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import types

import pytest

import memory_authority as ma
import memory_digest
import memory_service
import memory_supersede
from memory_service import MemoryService
from test_memory_authority import _Col  # the where-honouring Chroma stand-in

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0a1b2c3d"
HOME_OLD, HOME = "Perth", "Hobart"
CLINIC = "Harbour Dental"
OLD_HOME_FACT = f"User lives in {HOME_OLD}."
NEW_HOME_FACT = f"User lives in {HOME}."
DENTIST_FACT = f"User's dentist is {CLINIC}."
RETRACT_FACT = f"User no longer sees {CLINIC}."


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    monkeypatch.setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "1")   # services/zoe-data/.env turns it on in the live service
    s = MemoryService(data_dir="/nonexistent/zoe-test-own-change-of-mind")
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


def put(svc, text, source, **kw):
    kw.setdefault("confidence", 0.9)
    return asyncio.run(svc.ingest(text, user_id=UID, source=source, status="approved", **kw))


def digest_write(svc, fact, turn, writer="turn_digest", **kw):
    """What the bench lab does for a ``system_writer`` turn: the model's proposed fact, the owner's turn as its anchor."""
    return put(svc, fact, writer, anchor_text=turn, confidence=0.7, user_turn_id=f"w-{len(svc._col.rows)}-0", **kw)


def meta(svc, mem_id):
    return svc._col.rows[mem_id][1]


def rows(svc, *statuses):
    return [(d, m) for d, m in svc._col.rows.values() if m.get("status") in statuses]


def recalled(svc):
    return [r.text for r in svc._metadata_read(UID, 50)]


def conflict_pass(svc):
    return asyncio.run(memory_supersede.nightly_conflict_pass(svc, UID))


# ── C1.update_via_turn_digest ─────────────────────────────────────────────────

def _c1_seed(svc):
    return put(svc, OLD_HOME_FACT, "chat_regex", source_excerpt=f"I live in {HOME_OLD}", user_turn_id="t-old")


def test_c1_the_owners_own_move_read_by_the_turn_digest_updates_the_record(svc):
    old = _c1_seed(svc)
    new = digest_write(svc, NEW_HOME_FACT, f"I moved to {HOME}")
    # it is the owner speaking: approved at once (it used to wait as a `disputed` candidate and recall served Perth)
    assert new is not None and meta(svc, new.id)["status"] == "approved"
    assert meta(svc, new.id)["authority_class"] == ma.USER_STATED_DERIVED          # the honest provenance...
    assert meta(svc, new.id)["authority_basis"] == ma.VERBATIM_BASIS
    assert ma.row_class(meta(svc, new.id)) == ma.USER_STATED                       # ...with user_stated standing
    assert meta(svc, new.id)["origin"] == "turn_digest"
    out = conflict_pass(svc)
    assert out["superseded"] == 1 and meta(svc, old.id)["status"] == "superseded"
    assert meta(svc, old.id).get("invalid_at")                                     # history kept, not deleted
    assert recalled(svc) == [NEW_HOME_FACT]
    assert not rows(svc, "disputed", "pending")


def test_c1_control_without_the_rule_the_update_waits_and_recall_serves_the_old_home(svc, monkeypatch):
    monkeypatch.setattr(ma, "entailing_span", lambda *a, **k: None)               # the pre-fix behaviour
    old = _c1_seed(svc)
    new = digest_write(svc, NEW_HOME_FACT, f"I moved to {HOME}")
    assert meta(svc, new.id)["status"] == "disputed" and meta(svc, new.id)["contradicts_id"] == old.id
    conflict_pass(svc)
    assert meta(svc, old.id)["status"] == "approved"
    assert recalled(svc) == [OLD_HOME_FACT]


def test_c1_the_edit_path_updates_too(svc):
    """run_turn_digest's reconcile step edits the matching row (review/edit) instead of ingesting beside it."""
    old = _c1_seed(svc)
    got = asyncio.run(svc.review(old.id, decision="edit", edits=NEW_HOME_FACT, actor="turn_digest",
                                 anchor_text=f"I moved to {HOME}", source_excerpt=f"I moved to {HOME}",
                                 turn_ref="turn-9"))
    assert got is not None and meta(svc, old.id)["status"] == "superseded"
    assert recalled(svc) == [NEW_HOME_FACT]
    assert meta(svc, got.id)["user_turn_id"] == "turn-9"                          # A3: the edit says which turn too


@pytest.mark.parametrize("writer,verified", [("turn_digest", None), ("voice_turn_digest", None),
                                             ("voice_turn_digest", True)])
def test_c1_typed_and_verified_voice_lanes_update(svc, writer, verified):
    _c1_seed(svc)
    kw = dict(speaker_verified=verified) if verified is not None else dict()
    new = digest_write(svc, NEW_HOME_FACT, f"I moved to {HOME}", writer=writer, **kw)
    assert meta(svc, new.id)["status"] == "approved"


# ── the paraphrase-drift controls: what must STILL be held back ───────────────

@pytest.mark.parametrize("turn", [
    f"I am thinking about {HOME} one day",                 # not a statement of fact
    f"I might move to {HOME}",                             # hypothetical
    f"my sister moved to {HOME}",                          # a relative, not the owner
    f"Dana moved to {HOME} last week",                     # a third person
    f"Dana said she lives in {HOME}",                      # a report
    f"{HOME} came up in the car I think",                  # a hedge with a trailing first person
    f"{HOME} is where I live now",                         # the claim leads, the speaker trails: not plain
    f"I live in {HOME}, I think",                          # a hedge
    "I went to the shops",                                 # does not say it at all
    f"I used to live in {HOME}",                           # tense
])
def test_c1_a_paraphrase_the_turn_does_not_plainly_entail_stays_a_disputed_candidate(svc, turn):
    old = _c1_seed(svc)
    new = digest_write(svc, NEW_HOME_FACT, turn)
    assert meta(svc, new.id)["status"] == "disputed" and meta(svc, new.id)["contradicts_id"] == old.id
    assert meta(svc, new.id)["authority_basis"] != ma.VERBATIM_BASIS
    conflict_pass(svc)
    assert meta(svc, old.id)["status"] == "approved" and recalled(svc) == [OLD_HOME_FACT]


def test_c1_a_speaker_the_lane_rejected_never_passes(svc):
    old = _c1_seed(svc)
    new = digest_write(svc, NEW_HOME_FACT, f"I moved to {HOME}", writer="voice_turn_digest", speaker_verified=False)
    assert meta(svc, new.id)["authority_class"] == ma.USER_UNVERIFIED
    assert meta(svc, new.id)["status"] == "disputed"                               # a dispute outranks a mere pending
    assert meta(svc, old.id)["status"] == "approved" and recalled(svc) == [OLD_HOME_FACT]


@pytest.mark.parametrize("writer", ["digest", "idle_consolidation"])
def test_c1_the_whole_day_writers_are_not_promoted(svc, writer):
    """A day transcript has no single turn to quote: the 2026-10-05 incident class stays walled."""
    old = _c1_seed(svc)
    new = digest_write(svc, NEW_HOME_FACT, f"I moved to {HOME}", writer=writer)
    assert meta(svc, new.id)["status"] == "disputed" and meta(svc, new.id)["authority_class"] == ma.USER_STATED_DERIVED
    assert meta(svc, old.id)["status"] == "approved"


def test_c1_a_third_persons_fact_is_not_the_owners_and_the_identity_wall_is_untouched(svc):
    old = _c1_seed(svc)
    other = digest_write(svc, f"Dana lives in {HOME}.", f"Dana moved to {HOME}")
    assert other is not None and meta(svc, other.id)["status"] == "approved"      # about Dana: no conflict with the owner
    assert meta(svc, other.id)["authority_basis"] != ma.VERBATIM_BASIS            # and never given the owner's power
    assert meta(svc, old.id)["status"] == "approved"
    # the owner's NAME is the account's: no writer asserts it, however plainly the turn says so
    assert digest_write(svc, "User's name is Zed Vale.", "my name is Zed Vale") is None
    assert recalled(svc).count(OLD_HOME_FACT) == 1


def test_c1_shadow_mode_is_unchanged(svc, monkeypatch):
    old = _c1_seed(svc)
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "shadow")
    new = digest_write(svc, NEW_HOME_FACT, f"my sister moved to {HOME}")
    assert meta(svc, new.id)["status"] == "approved" and meta(svc, old.id)["status"] == "approved"   # shadow only logs


# ── C5.retracted_via_turn_digest ──────────────────────────────────────────────

def test_c5_the_owners_own_retraction_read_by_the_turn_digest_is_not_served(svc):
    put(svc, DENTIST_FACT, "voice_fact", user_turn_id="fact-d")
    new = digest_write(svc, RETRACT_FACT, f"I no longer see {CLINIC}")
    assert meta(svc, new.id)["status"] == "approved" and meta(svc, new.id)["authority_basis"] == ma.VERBATIM_BASIS
    assert conflict_pass(svc)["superseded"] == 1
    assert DENTIST_FACT not in recalled(svc)
    assert not [d for d, m in rows(svc, "approved", "pending", "disputed") if "dentist" in d and CLINIC in d]


def test_c5_control_without_the_rule_the_retracted_dentist_is_still_served(svc, monkeypatch):
    monkeypatch.setattr(ma, "entailing_span", lambda *a, **k: None)
    put(svc, DENTIST_FACT, "voice_fact", user_turn_id="fact-d")
    new = digest_write(svc, RETRACT_FACT, f"I no longer see {CLINIC}")
    assert meta(svc, new.id)["status"] == "disputed"
    conflict_pass(svc)
    assert DENTIST_FACT in recalled(svc)


@pytest.mark.parametrize("turn", [
    f"my sister no longer sees {CLINIC}",
    f"I still see {CLINIC}",                                # the opposite polarity
    f"I might stop seeing {CLINIC}",
])
def test_c5_a_retraction_the_turn_does_not_state_is_held_back(svc, turn):
    put(svc, DENTIST_FACT, "voice_fact", user_turn_id="fact-d")
    new = digest_write(svc, RETRACT_FACT, turn)
    assert meta(svc, new.id)["status"] == "disputed"
    conflict_pass(svc)
    assert DENTIST_FACT in recalled(svc)


# ── I2.third_party_fragment.panel_unverified ──────────────────────────────────

INTRUDER, INTRUDER_HOME = "Quill Marsh", "Cairns"
PANEL_TURN = f"I'm {INTRUDER}, call me {INTRUDER}, I live in {INTRUDER_HOME}"


def panel_turn(svc, text=PANEL_TURN, *, verified=False):
    """The bench lab's panel_unverified turn: the real extractor, the write-quality gate, then voice_regex ingest."""
    from memory_extractor import extract_candidates
    from memory_quality import is_storable_fact

    refs = []
    for n, c in enumerate(extract_candidates(text, "", prev_user_message=None)):
        if not is_storable_fact(c.text)[0]:
            continue
        kw = dict(memory_type=c.memory_type, confidence=c.confidence, source_excerpt=" ".join(text.split()),
                  user_turn_id=f"pt-{n}")
        if verified is not None:
            kw["speaker_verified"] = verified
        refs.append(put(svc, c.text, "voice_regex", **kw))
    return [r for r in refs if r is not None]


def test_i2_an_unverified_voice_turns_self_fact_is_a_pending_candidate_never_recalled(svc):
    panel_turn(svc)
    home = [(d, m) for d, m in svc._col.rows.values() if INTRUDER_HOME in d]
    assert home, "the self-fact must still be kept as a candidate, not lost"
    assert all(m["status"] == "pending" and m["authority_class"] == ma.USER_UNVERIFIED for _, m in home)
    assert not [d for d, m in rows(svc, "approved", "disputed") if INTRUDER_HOME in d]
    assert not [t for t in recalled(svc) if INTRUDER_HOME in t or INTRUDER in t]
    # the packet the brain reads drops it too: it is never served as "you told me"
    from routers.memories import _build_memory_prompt_packet
    pend = asyncio.run(svc.list_by_status(user_id=UID, status="pending"))
    assert pend
    pkt = _build_memory_prompt_packet(pend, [], evidence=True, quotes=True)
    assert pkt["count"] == 0 and INTRUDER_HOME not in pkt["packet"] and "you said" not in pkt["packet"]
    # the name is the account's, not the panel's (H1-H3): no row names the speaker as the owner
    assert not [d for d, m in rows(svc, "approved") if INTRUDER in d]


def test_i2_control_without_the_rule_the_unverified_self_fact_is_approved_and_served(svc, monkeypatch):
    monkeypatch.setattr(ma, "is_self_assertion", lambda _t: False)                 # the pre-fix behaviour
    panel_turn(svc)
    assert [d for d, m in rows(svc, "approved") if INTRUDER_HOME in d]
    assert [t for t in recalled(svc) if INTRUDER_HOME in t]


@pytest.mark.parametrize("verified", [True, None])
def test_i2_a_verified_speaker_or_a_lane_with_no_verdict_is_unchanged(svc, verified):
    panel_turn(svc, f"I live in {INTRUDER_HOME}", verified=verified)
    assert [d for d, m in rows(svc, "approved") if INTRUDER_HOME in d]


def test_i2_a_model_reading_of_an_unverified_turn_is_a_candidate_too(svc):
    new = digest_write(svc, f"User lives in {INTRUDER_HOME}.", f"I live in {INTRUDER_HOME}",
                       writer="voice_turn_digest", speaker_verified=False)
    assert meta(svc, new.id)["status"] == "pending" and meta(svc, new.id)["authority_class"] == ma.USER_UNVERIFIED
    assert recalled(svc) == []


def test_i2_a_contradiction_stays_disputed_and_the_owners_yes_makes_it_theirs(svc):
    old = put(svc, OLD_HOME_FACT, "chat_regex", user_turn_id="t-old")
    new = put(svc, f"User lives in {INTRUDER_HOME}.", "voice_regex", speaker_verified=False, user_turn_id="p-1",
              source_excerpt=f"I live in {INTRUDER_HOME}")
    assert meta(svc, new.id)["status"] == "disputed" and meta(svc, new.id)["contradicts_id"] == old.id
    cand = put(svc, "User likes quiet mornings.", "voice_regex", speaker_verified=False, user_turn_id="p-2")
    assert meta(svc, cand.id)["status"] == "pending"
    got = asyncio.run(svc.review(cand.id, decision="approve", actor="review_ui"))
    assert got is not None and meta(svc, cand.id)["status"] == "approved"
    assert meta(svc, cand.id)["authority_class"] == ma.USER_CONFIRMED             # the owner's yes IS the owner stating it
    assert "User likes quiet mornings." in recalled(svc)


def test_i2_shadow_mode_stores_as_before_and_a_third_persons_fact_is_not_a_self_assertion(svc, monkeypatch):
    other = put(svc, "Casey's dog is named Rex.", "voice_regex", speaker_verified=False, user_turn_id="p-3")
    assert meta(svc, other.id)["status"] == "approved"            # not about the owner: the class rule is unchanged
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "shadow")
    shadow = put(svc, f"User lives in {INTRUDER_HOME}.", "voice_regex", speaker_verified=False, user_turn_id="p-4")
    assert meta(svc, shadow.id)["status"] == "approved"


# ── A3: every user-turn-derived row carries its excerpt, its turn id and its authority class ─────────────────

FIELDS = ("source_excerpt", "user_turn_id", "authority_class")
DAY = ("so today I went to the shop and the weather was fine and then I told everyone that I live in {home} "
       "and the kids were at school until late and we had dinner at home quietly and watched a film together")


def complete(m):
    return all(str(m.get(f) or "").strip() for f in FIELDS)


def nightly(svc, monkeypatch, transcript, facts):
    """The REAL run_memory_digest (dedup, anchor validation, contradiction pass, MemoryService writes); only the model
    calls are scripted, exactly as the bench lab does."""
    async def todays(*_a, **_k):
        return transcript

    async def extract(_text):
        return [dict(fact=f, type="profile") for f in facts]

    async def judge(*_a, **_k):
        return True

    async def no_emotions(*_a, **_k):
        return 0

    async def no_blob(*_a, **_k):
        return ""

    async def no_hits(*_a, **_k):
        return []

    stub = types.ModuleType("zoe_agent")
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    monkeypatch.setattr(memory_digest, "_load_todays_messages", todays)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_digest, "_is_contradiction", judge)
    monkeypatch.setattr(memory_digest, "_emotional_memory_pass", no_emotions)
    svc.search = no_hits
    return asyncio.run(memory_digest.run_memory_digest(UID))


def test_a3_the_teach_lane_stamps_the_owners_words_a_turn_id_and_the_class(svc):
    a = put(svc, f"User lives in {HOME}.", "voice_fact")                        # the bench cell: text only
    b = put(svc, "User works at Fernside Books.", "voice_fact")
    for ref in (a, b):
        m = meta(svc, ref.id)
        assert complete(m), m
        assert m["source_excerpt"] == ref.text and m["user_turn_id"].startswith("fact-")
        assert m["authority_class"] == ma.USER_STATED
    again = put(svc, f"User lives in {HOME}.", "voice_fact")                    # a replay is the same turn id, not a new one
    assert again is None or meta(svc, again.id)["user_turn_id"] == meta(svc, a.id)["user_turn_id"]


def test_a3_explicit_teach_through_the_brain_keeps_the_users_own_turn_as_the_excerpt(svc):
    turn = f"remember that I live in {HOME} now"
    ref = put(svc, f"User lives in {HOME}.", "brain_tool", origin="explicit_teach", anchor_text=turn,
              user_turn_id="fact-abc")
    m = meta(svc, ref.id)
    assert m["source_excerpt"] == turn and m["user_turn_id"] == "fact-abc" and m["origin"] == "explicit_teach"
    assert m["authority_class"] == ma.USER_STATED and complete(m)


def test_a3_a_caller_supplied_excerpt_and_id_win_and_other_lanes_are_left_alone(svc):
    kept = put(svc, f"User lives in {HOME}.", "voice_fact", source_excerpt="I live in " + HOME, user_turn_id="t-1")
    assert meta(svc, kept.id)["source_excerpt"] == "I live in " + HOME and meta(svc, kept.id)["user_turn_id"] == "t-1"
    # a lane whose text is not a user turn (a model with no user evidence) gets no invented excerpt or turn id
    guess = put(svc, "User likes quiet mornings.", "consolidation")
    assert "source_excerpt" not in meta(svc, guess.id) and "user_turn_id" not in meta(svc, guess.id)
    # ...and neither does a hedged model reading the turn does not support
    unsupported = put(svc, f"User lives in {HOME}.", "digest", anchor_text="we had dinner at home quietly")
    assert unsupported is None or "source_excerpt" not in meta(svc, unsupported.id)


def test_a3_the_nightly_digest_stamps_the_verbatim_sentence_and_a_turn_id(svc, monkeypatch):
    out = nightly(svc, monkeypatch, DAY.format(home=HOME), [NEW_HOME_FACT])
    assert out["new"] == 1
    (row,) = [m for d, m in rows(svc, "approved") if d == NEW_HOME_FACT]
    assert complete(row), row
    assert f"I live in {HOME}" in row["source_excerpt"] and row["user_turn_id"].startswith("ut-")
    assert row["authority_class"] == ma.USER_STATED_DERIVED and row["origin"] == "digest"
    assert row["authority_basis"] == "anchored_user_turn"             # the whole-day writer is never promoted


def test_a3_the_nightly_digest_names_the_chat_messages_row_it_read(svc, monkeypatch):
    turns = (("msg-1", "okay so that was the plan for tomorrow and then the shopping later on tonight"),
             ("msg-2", DAY.format(home=HOME)))
    text = memory_digest.Transcript("\n".join(c for _i, c in turns), turns)
    assert isinstance(text, str) and text.count("\n") == 1
    nightly(svc, monkeypatch, text, [NEW_HOME_FACT])
    (row,) = [m for d, m in rows(svc, "approved") if d == NEW_HOME_FACT]
    assert row["user_turn_id"] == "msg-2" and f"I live in {HOME}" in row["source_excerpt"]


def test_a3_the_loader_remembers_which_message_each_turn_came_from():
    class Cur:
        async def fetchall(self):
            return [("I live in Hobart", "m-1"), ("and I work at Fernside Books", "m-2"), ("", "m-3")]

    class Db:
        async def execute(self, sql, params=()):
            assert "cm.id" in sql
            return Cur()

    text = asyncio.run(memory_digest._load_todays_messages(UID, Db()))
    assert text == "I live in Hobart\nand I work at Fernside Books"
    assert text.turns == (("m-1", "I live in Hobart"), ("m-2", "and I work at Fernside Books"))
    item = dict(fact="User works at Fernside Books.", quote="I work at Fernside Books")
    assert memory_digest.locate_turn(item, "User works at Fernside Books.", text) == ("I work at Fernside Books", "m-2")
    assert memory_digest.locate_turn(dict(quote="never said this"), "x", text) == (None, None)   # a fabricated quote


def test_a3_the_voice_teach_path_passes_the_spoken_words(svc, monkeypatch):
    """expert_dispatch.store_fact (the instant teach reply): the row says which words it was taught from."""
    import expert_dispatch

    async def no_person_link(*_a, **_k):
        return 0

    monkeypatch.setattr("memory_extractor.extract_and_ingest", no_person_link)
    spoken = f"remember that my dentist is {CLINIC}"
    reply = asyncio.run(expert_dispatch.store_fact("memory", spoken, UID, session_id="s-1"))
    assert reply and "remember" in reply.lower()
    (row,) = [m for d, m in rows(svc, "approved")]
    assert row["source_excerpt"] == spoken and row["user_turn_id"].startswith("fact-")
    assert row["authority_class"] == ma.USER_STATED and row["origin"] == "voice_fact"


def test_a3_rate_over_the_four_lanes_is_at_least_95_percent(svc, monkeypatch):
    """The bench's A3.user_turn_rows_rate inputs: a nightly row, two typed rows, a verified voice row, two taught rows."""
    nightly(svc, monkeypatch, DAY.format(home="Fernside Books").replace("live in", "work at"), ["User works at Fernside Books."])
    put(svc, f"User lives in {HOME}.", "chat_regex", source_excerpt=f"I live in {HOME}", user_turn_id="t-1")
    put(svc, "User's dog is named Rex.", "chat_regex", source_excerpt="my dog is named Rex", user_turn_id="t-2")
    put(svc, f"User lives in {HOME_OLD}.", "voice_regex", speaker_verified=True, source_excerpt=f"I live in {HOME_OLD}",
        user_turn_id="t-3")
    put(svc, "User's friend Ines lives in Lima.", "voice_fact")
    put(svc, "User's friend Omar lives in Cairns.", "voice_fact")
    approved = [m for _d, m in rows(svc, "approved")]
    assert len(approved) == 6
    assert sum(complete(m) for m in approved) / len(approved) >= 0.95


def test_a3_control_without_the_stamp_the_taught_and_nightly_rows_lose_their_evidence(svc, monkeypatch):
    monkeypatch.setattr(ma, "turn_evidence", lambda w, r, t, *, user_id, anchor_text, source_excerpt, user_turn_id, teach=True:
                        (source_excerpt, user_turn_id))
    monkeypatch.setattr(memory_digest, "locate_turn", lambda *a, **k: (None, None))
    taught = put(svc, f"User lives in {HOME}.", "voice_fact")
    nightly(svc, monkeypatch, DAY.format(home="Fernside Books").replace("live in", "work at"), ["User works at Fernside Books."])
    night = [m for d, m in rows(svc, "approved") if d == "User works at Fernside Books."][0]
    assert not meta(svc, taught.id).get("source_excerpt") and not night.get("source_excerpt") and not night.get("user_turn_id")

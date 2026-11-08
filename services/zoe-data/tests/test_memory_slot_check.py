"""memory_slot_check: person-keyed single-value contradictions and ageing, decided by the authority ladder (user-stated / confirmed
over inferred; an inference never supersedes). Every rule has a negative control: the same pair with the authorities swapped, the
same value, another person, a row that is not standing, the flag off."""
from __future__ import annotations

import datetime
import time
import types

import pytest

import memory_service
import memory_slot_check as sc
import provenance_answers
import routers.memories as memories_mod
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe

D = 86400.0
NOW = datetime.datetime(2026, 10, 30, 4, 0, tzinfo=datetime.timezone.utc).timestamp()     # Fri 30 Oct, 12:00 Perth

STATED = {"authority": "user_stated", "authority_class": "user_stated", "source": "chat_regex", "status": "approved"}
CONFIRMED = {"authority": "user_confirmed", "authority_class": "user_confirmed", "source": "review_ui", "status": "approved"}
INFERRED = {"authority": "inferred", "authority_class": "model_from_transcript", "source": "digest", "status": "approved"}


@pytest.fixture(autouse=True)
def _perth(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv(sc.ENV, raising=False)


def _ref(rid: str, text: str, base: dict, days_ago: float, **extra) -> MemoryRef:
    return MemoryRef(id=rid, text=text, metadata={**base, "added_ts": NOW - days_ago * D, **extra})


# ── slots ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, slot, value", [
    ("User works at Acme Corp", "employer", "acme corp"),
    ("User works at Acme in Northcliff", "employer", "acme"),
    ("User left Acme and now works at Globex", "employer", "globex"),
    ("I work at Initech", "employer", "initech"),
    ("User lives in Northcliff with the family", "home_city", "northcliff"),
    ("User moved to Southbay in 2019", "home_city", "southbay"),
    ("User's wife is Ana", "partner", "ana"),
    ("User is married to Bea", "partner", "bea"),
    ("User is vegetarian", "diet", "vegetarian"),
    ("User follows a keto diet", "diet", "keto"),
])
def test_slot_of_reads_a_current_single_value(text, slot, value):
    s = sc.slot_of(text)
    assert (s.name, s.value) == (slot, value)


@pytest.mark.parametrize("text", [
    "User no longer works at Acme",          # negated
    "User used to be vegan",                 # past
    "User wants to move to Southbay",        # hoped-for
    "User would love to work at Acme",       # infinitive
    "User is thinking about moving to Southbay",
    "User's ex-wife is Cara",                # a former partner is not the partner
    "User's sister works at Acme",           # the sister's job, not the user's
    "User works at Acme and lives in Northcliff",   # two slots in one row: not paired
    "User has a dog named Rex",              # no slot
    "User said Ana left Acme and now works at Globex",   # a reported claim: Ana's job, not the owner's
    "User said Ana works at Globex",
    "User's friend Ana left Acme and now works at Globex",
    "User works at home",                    # not an employer
    "User works at weekends",
    "User lives in fear",                    # not a place
    "User lives in a flat",
    "",
])
def test_slot_of_refuses_what_is_not_one_current_value(text):
    assert sc.slot_of(text) is None


def test_key_of_owner_person_or_nothing():
    assert sc.key_of({}, "User works at Acme") == "self"
    assert sc.key_of({"entity_id": "p-9"}, "User works at Acme") == "self"           # the text says it is the owner's
    assert sc.key_of({"entity_id": "p-9"}, "Ana works at Acme") == "person:p-9"
    assert sc.key_of({"entity_id": "p-9"}, "Her husband is Ben") == "person:p-9"
    # negative controls: a named subject with no resolved person is not guessed; no slot is no key
    assert sc.key_of({}, "Ana works at Acme") == ""
    assert sc.key_of({"entity_id": "p-9"}, "Ana has a dog") == ""


def test_same_value_is_word_subset():
    assert sc.same_value("acme", "acme corp") and not sc.same_value("acme", "globex")


# ── conflicts: the authority ladder decides ───────────────────────────────────

def test_an_inference_never_supersedes_what_the_person_said_it_is_held():
    old = _ref("old", "User works at Acme", STATED, 40)
    new = _ref("new", "User works at Globex", INFERRED, 2)
    [c] = sc.conflicts([new, old])
    assert (c.older_id, c.newer_id, c.action, c.slot, c.person) == ("old", "new", sc.HOLD_NEWER, "employer", "self")
    assert sc.hidden_ids([c]) == {"new"}
    # negative control: the person saying it themselves is NOT held
    [c2] = sc.conflicts([old, _ref("new", "User works at Globex", STATED, 2)])
    assert c2.action == sc.ASK and sc.hidden_ids([c2]) == frozenset()


def test_the_persons_word_over_an_older_inference_hides_the_inference():
    old = _ref("old", "User lives in Northcliff", INFERRED, 40)
    new = _ref("new", "User lives in Southbay", CONFIRMED, 2)
    [c] = sc.conflicts([old, new])
    assert c.action == sc.HIDE_OLDER and sc.hidden_ids([c]) == {"old"}


def test_a_confirmed_row_is_not_overridden_by_a_later_inference():
    [c] = sc.conflicts([_ref("a", "User is vegetarian", CONFIRMED, 30), _ref("b", "User is vegan", INFERRED, 1)])
    assert c.action == sc.HOLD_NEWER


@pytest.mark.parametrize("rows", [
    # the same value said twice
    [_ref("a", "User works at Acme", STATED, 40), _ref("b", "User works at Acme Corp", INFERRED, 2)],
    # two different people
    [_ref("a", "Ana works at Acme", STATED, 40, entity_id="p-1"), _ref("b", "Bo works at Globex", INFERRED, 2, entity_id="p-2")],
    # the older row is no longer standing
    [_ref("a", "User works at Acme", {**STATED, "status": "superseded"}, 40), _ref("b", "User works at Globex", INFERRED, 2)],
    [_ref("a", "User works at Acme", STATED, 40, invalid_at="2026-10-01T00:00:00Z"), _ref("b", "User works at Globex", INFERRED, 2)],
    # a supersede already links them
    [_ref("a", "User works at Acme", STATED, 40), _ref("b", "User works at Globex", STATED, 2, supersedes_id="a")],
    # one person's rows name two different holders: the entity link is not trusted
    [_ref("a", "Ana's husband is Ben", STATED, 40, entity_id="p-3"), _ref("b", "Ben's wife is Ana", STATED, 2, entity_id="p-3")],
])
def test_no_conflict(rows):
    assert sc.conflicts(rows) == []


def test_three_values_pair_every_disagreement():
    rows = [_ref("a", "User lives in Northcliff", STATED, 90), _ref("b", "User lives in Southbay", INFERRED, 20),
            _ref("c", "User lives in Eastvale", STATED, 1)]
    pairs = {(c.older_id, c.newer_id): c.action for c in sc.conflicts(rows)}
    assert pairs == {("a", "b"): sc.HOLD_NEWER, ("a", "c"): sc.ASK, ("b", "c"): sc.HIDE_OLDER}
    assert sc.hidden_ids(sc.conflicts(rows)) == {"b"}


# ── ageing ────────────────────────────────────────────────────────────────────

def test_a_life_now_fact_is_due_a_still_true_check_after_90_days():
    assert sc.age(_ref("a", "User works at Acme", STATED, 91).metadata, "User works at Acme", NOW).state == "due"
    a = sc.age(_ref("a", "User works at Acme", STATED, 89).metadata, "User works at Acme", NOW)
    assert a.state == "fresh" and a.days_since_confirmed == 89


def test_a_mood_row_ages_in_14_days_and_a_stable_fact_never():
    mood = {**STATED, "memory_type": "emotional_moment", "added_ts": NOW - 15 * D}
    assert sc.age(mood, "User felt anxious about the move", NOW).state == "due"
    stable = {**STATED, "added_ts": NOW - 4000 * D}
    assert sc.age(stable, "User's birthday is 3 March", NOW).state == "ageless"
    assert sc.age({**stable}, "User's wife is Ana", NOW).state == "ageless"         # a relationship never ages


def test_an_explicit_review_after_wins():
    meta = {**STATED, "added_ts": NOW - D, "review_after": "2026-10-29T00:00:00Z"}
    assert sc.age(meta, "User's project is the shed", NOW).state == "due"
    meta["review_after"] = "2026-12-01T00:00:00Z"
    assert sc.age(meta, "User's project is the shed", NOW).state == "fresh"


def test_age_counts_from_the_last_confirmation_not_an_automatic_edit():
    edited = "2026-10-20T00:00:00Z"
    confirmed = {**CONFIRMED, "added_ts": NOW - 200 * D, "reviewed_at": edited}
    assert sc.age(confirmed, "User works at Acme", NOW).state == "fresh"
    # negative control: an automatic writer's edit time is not the person confirming it
    auto = {**INFERRED, "added_ts": NOW - 200 * D, "reviewed_at": edited}
    assert sc.age(auto, "User works at Acme", NOW).state == "due"


def test_no_time_to_measure_from_is_never_guessed():
    assert sc.age({**STATED}, "User works at Acme", NOW).state == "ageless"


# ── questions ─────────────────────────────────────────────────────────────────

def test_the_question_carries_each_sides_provenance_in_the_rows_own_words():
    old = _ref("old", "User works at Acme", STATED, 40)
    new_stated = _ref("new", "User works at Globex", STATED, 3)
    [c] = sc.conflicts([old, new_stated])
    q = sc.question(c, {"old": old, "new": new_stated}, now=NOW)
    assert q == "On 20 September you told me that you work at Acme, and you told me on Tuesday that you work at Globex - which is right?"
    new_inf = _ref("new", "User works at Globex", INFERRED, 3)
    [c2] = sc.conflicts([old, new_inf])
    q2 = sc.question(c2, {"old": old, "new": new_inf}, now=NOW)
    assert q2 == "On 20 September you told me that you work at Acme, but I picked up that you work at Globex - is that right now?"
    assert "you told me" not in q2.split(", but ")[1]                          # an inference is never "you told me"


def test_a_side_without_provenance_phrases_no_question():
    old = MemoryRef(id="old", text="User works at Acme", metadata={"source": "chat_regex", "added_ts": NOW - 40 * D})   # unstamped
    new = _ref("new", "User works at Globex", STATED, 3)
    c = sc.Conflict("self", "employer", "old", "new", sc.ASK)
    assert sc.question(c, {"old": old, "new": new}, now=NOW) == ""
    assert sc.question(sc.Conflict("self", "employer", "new", "old", sc.ASK), {"old": old, "new": new}, now=NOW) == ""
    # negative control: stamp the old side and the question is phrased
    stamped = MemoryRef(id="old", text="User works at Acme", metadata={**STATED, "added_ts": NOW - 40 * D})
    assert sc.question(c, {"old": stamped, "new": new}, now=NOW).startswith("On 20 September you told me")


# ── the serving hook ──────────────────────────────────────────────────────────

def _pair():
    return [_ref("keep0001", "User works at Acme", STATED, 40)], [_ref("held0001", "User works at Globex", INFERRED, 2)]


def test_apply_to_packet_off_and_shadow_change_nothing(caplog):
    facts, hits = _pair()
    assert sc.apply_to_packet("member-a", facts, hits, None) == (facts, hits, None)                  # off: not even looked at
    caplog.set_level("INFO", logger="memory_slot_check")
    out = sc.apply_to_packet("member-a", facts, hits, None, force_mode="shadow")
    assert out == (facts, hits, None)
    line = next(r.getMessage() for r in caplog.records if "SLOT_CHECK" in r.getMessage())
    assert "conflicts=1 held=1 hidden=held0001" in line and "Globex" not in line and "Acme" not in line


def test_apply_to_packet_on_drops_only_the_held_row():
    facts, hits = _pair()
    f, h, r = sc.apply_to_packet("member-a", facts, hits, [], force_mode="on")
    assert [x.id for x in f] == ["keep0001"] and h == [] and r == []
    # negative control: the person's own later word is served (nothing held)
    hits2 = [_ref("new", "User works at Globex", STATED, 2)]
    f2, h2, _ = sc.apply_to_packet("member-a", facts, hits2, None, force_mode="on")
    assert [x.id for x in f2 + h2] == ["keep0001", "new"]


def test_apply_to_packet_never_raises(monkeypatch):
    monkeypatch.setattr(sc, "conflicts", lambda rows: 1 / 0)
    facts, hits = _pair()
    assert sc.apply_to_packet("member-a", facts, hits, None, force_mode="on") == (facts, hits, None)


def test_report_is_ids_and_counts_only():
    rows = [_ref("a", "User works at Acme", STATED, 120), _ref("b", "User works at Globex", INFERRED, 2),
            _ref("c", "User's birthday is 3 March", STATED, 500)]
    rep = sc.report(rows, NOW)
    assert rep["rows"] == 3 and rep["keyed"] == 2
    assert rep["held"] == ["b"] and rep["hidden_older"] == [] and rep["still_true_due"] == ["a"]
    assert rep["questions"] == [{"older": "a", "newer": "b"}]
    flat = repr(rep)
    assert "Acme" not in flat and "Globex" not in flat and "March" not in flat


# ── the endpoint: flag off is byte-identical, on holds the inference ─────────

class _FakeSvc:
    def __init__(self, rows):
        self._rows = rows

    async def load_for_prompt(self, user_id, *, limit=20):
        return self._rows[:limit]

    async def search(self, q, *, user_id, limit=10):
        return []

    async def load_recent_for_prompt(self, user_id, **kw):
        return []


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(memories_mod, "_svc", lambda: _FakeSvc(_pair()[0] + _pair()[1]))
    monkeypatch.setattr(provenance_answers, "time", types.SimpleNamespace(time=lambda: NOW, monotonic=time.monotonic))


async def _packet(message="where do I work?"):
    return (await memories_mod.memory_for_prompt(user_id="member-a", message=message, limit=12, mode="relevance", _=None))["packet"]


@pytest.mark.asyncio
async def test_endpoint_flag_off_serves_both_and_on_holds_the_inference(svc, monkeypatch):
    off = await _packet()
    assert "[mem:keep0001]" in off and "[mem:held0001]" in off
    monkeypatch.setenv(sc.ENV, "shadow")
    assert await _packet() == off
    monkeypatch.setenv(sc.ENV, "on")
    on = await _packet()
    assert "[mem:keep0001]" in on and "[mem:held0001]" not in on


def test_self_subject_keys_carry_the_row_owner():
    a = sc.key_of({"user_id": "Ana"}, "User works at Acme")
    b = sc.key_of({"user_id": "ben"}, "User works at Globex")
    assert a == "self:ana" and b == "self:ben" and a != b
    assert sc.key_of({"wing": "ana"}, "User works at Acme") == "self:ana"
    rows = [{"id": "1", "text": "User works at Acme", "metadata": {"user_id": "ana", "added_ts": 1.0}},
            {"id": "2", "text": "User works at Globex", "metadata": {"user_id": "ben", "added_ts": 2.0}}]
    assert sc.conflicts(rows) == []                                          # two members' employers are not one contradiction
    rows[1]["metadata"]["user_id"] = "ana"
    assert len(sc.conflicts(rows)) == 1                                      # negative control: one owner, two employers


@pytest.mark.asyncio
async def test_the_relevance_gate_rebuild_applies_the_slot_policy_to_the_expanded_pool(svc, monkeypatch):
    """Codex #2301: the gate re-loads the durable pool and rebuilds the packet from rows the slot check never saw."""
    import recall_gate

    async def expanding(_svc, _uid, _msg, result, **kw):
        return kw["rebuild"](_pair()[0] + _pair()[1])          # the pool the gate itself selected, held row included

    monkeypatch.setattr(recall_gate, "packet_surface", expanding)
    monkeypatch.setenv(sc.ENV, "on")
    on = await _packet()
    assert "[mem:keep0001]" in on and "[mem:held0001]" not in on
    monkeypatch.delenv(sc.ENV)
    assert "[mem:held0001]" in await _packet()                   # control: off serves both


# ── the CAPTURED shapes: rows as the post-turn extractor really writes them (first shadow run: keyed=0) ──────────

def _captured(utterance: str) -> "list[str]":
    """The row texts the chat capture (``memory_extractor.extract_candidates``) stores for one first-person utterance."""
    import memory_extractor

    return [c.text for c in memory_extractor.extract_candidates(utterance, "")]


@pytest.mark.parametrize("utterance, slot, value", [
    ("I work at Acme Robotics as a mechanical engineer.", "employer", "acme robotics"),
    ("I work at Globex these days, in their design team.", "employer", "globex"),
    ("I work for the council.", "employer", "council"),
    ("I live in Dunedin.", "home_city", "dunedin"),
    ("My wife is called Sarah.", "partner", "sarah"),
    ("My partner's name is Sam.", "partner", "sam"),
    ("My husband is Tom.", "partner", "tom"),
])
def test_captured_rows_key_to_the_owner(utterance, slot, value):
    texts = _captured(utterance)
    slots = [sc.slot_of(t) for t in texts]
    keyed = [(s.name, s.value) for t, s in zip(texts, slots) if s and sc.key_of({"user_id": "member-a"}, t, s) == "self:member-a"]
    assert keyed == [(slot, value)], (texts, slots)


@pytest.mark.parametrize("text", [
    "User works at/for: Globex",          # the agent path's template (zoe_agent)
    "User works at/for Globex",
])
def test_slash_template_reads_as_the_preposition(text):
    s = sc.slot_of(text)
    assert s is not None and (s.name, s.value) == ("employer", "globex")
    assert sc.key_of({"user_id": "member-a"}, text, s) == "self:member-a"


@pytest.mark.parametrize("text", [
    "User's job/role: mechanical engineer",       # a role, not an employer
    "User no longer works at/for Acme",           # negated
    "User's sister works at/for Acme",            # somebody else's job
    "User said Ana works at/for Acme",            # a report
    "User's wife is called",                      # a value-less capture: no name was said
    "User is working on the garden",              # an activity, not an employer
])
def test_captured_shapes_negative_controls(text):
    s = sc.slot_of(text)
    assert s is None or not sc.key_of({"user_id": "member-a"}, text, s).startswith("self")


def test_captured_employer_change_is_a_conflict_and_authority_rules_hold():
    """The QA replay's pair, as captured: two stated employers -> ASK (nothing hidden); an inference against a stated one -> held."""
    old_t, new_t = _captured("I work at Acme Robotics as a mechanical engineer.")[0], _captured("I work at Globex these days.")[0]
    found = sc.conflicts([_ref("old1", old_t, STATED, 3), _ref("new1", new_t, STATED, 1)])
    assert [(c.slot, c.older_id, c.newer_id, c.action) for c in found] == [("employer", "old1", "new1", sc.ASK)]
    assert sc.hidden_ids(found) == frozenset()
    held = sc.conflicts([_ref("old1", old_t, STATED, 3), _ref("new1", new_t, INFERRED, 1)])
    assert [c.action for c in held] == [sc.HOLD_NEWER]      # an inference never supersedes the person's word
    # negative control: the same employer said twice is not a conflict
    assert sc.conflicts([_ref("a", old_t, STATED, 3), _ref("b", _captured("I work at Acme Robotics.")[0], STATED, 1)]) == []


def test_extractor_never_stores_called_as_a_name():
    assert _captured("My wife is called Sarah.") == ["User's wife is named Sarah"]
    assert _captured("My wife is Sarah.") == ["User's wife is Sarah"]       # negative control: the bare form still captures


def test_report_says_why_rows_are_unkeyed():
    rows = [_ref("a", "User works at/for Acme", STATED, 3), _ref("b", "User's job/role: welder", STATED, 3),
            _ref("c", "User likes tea", STATED, 3), _ref("d", "Ana works at Acme", STATED, 3),
            _ref("e", "User works at/for Globex", {**STATED, "status": "pending"}, 1)]
    rep = sc.report(rows, NOW)
    assert rep["keyed"] == 1
    assert rep["unkeyed"] == {"not_standing": 1, "no_slot": 2, "slot_no_person": 1, "hint_employer": 1}
    assert "Acme" not in repr(rep) and "welder" not in repr(rep)

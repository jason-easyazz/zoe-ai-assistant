"""QA round 4 (2026-11-08) memory-side fixes. Every name and sentence below is invented.

1. A TOPIC forget ("forget what I told you about my mum's heart") reaches memory: it forgets the rows holding the whole topic, and
   only those, fails closed, and says what it did. Before, no intent matched, the brain "agreed", the guard said "I didn't manage to
   save that", and the row stayed in the recall packet.
2. Memory quality: a son named by apposition ("my son jonah is 16 not young") is anchored; "kids are not little" is not a fact; the
   model cannot mint a contact from a noun phrase; the post-turn passes cannot store one disclosure three times.
3. A contact offer: "no contacts please" is a durable per-member no, and an offer is voiced only on a turn about that person.

Each fix has a negative control in the same file (the check that goes red when the fix is taken out).
"""
from __future__ import annotations

import asyncio

import pytest

import contact_offer_policy as cop
import forget_topic
import intent_router
import memory_authority as ma
import memory_quality as mq
import memory_service
import write_claim_guard as g
import write_receipts as wr
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_qa4_00000001"


def run(coro):
    return asyncio.run(coro)


# ── 1. topic forget ──────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    s = MemoryService(data_dir="/nonexistent/zoe-test-qa4-memory")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    import exact_words
    import turn_forget
    turn_forget.reset()
    exact_words.set_backend(exact_words.MemoryBackend())
    yield s
    exact_words.set_backend(None)
    turn_forget.reset()


HEART = "User's mum Wren lives in Albany and the doctors suspect her heart"
APPT = "User's mum Wren has an appointment next Tuesday"
TEA = "User's favourite tea is lapsang souchong"


def _seed(svc):
    for text in (HEART, APPT, TEA):
        run(svc.ingest(text, user_id=UID, source="brain_tool"))


def _live(svc):
    return sorted(r.text for r in run(svc.list_by_status(user_id=UID, status="approved")))


@pytest.mark.parametrize("text, topic", [
    ("forget what I told you about my mum's heart", "my mum's heart"),
    ("ok please forget what i told you about my mum's heart", "my mum's heart"),
    ("no wait, forget that, dont keep what i said about my mum", "my mum"),
    ("delete everything about the kitchen reno", "the kitchen reno"),
])
def test_topic_forget_is_recognised(text, topic):
    assert forget_topic.parse(text) == topic


@pytest.mark.parametrize("text", [
    "forget about my day",                # no quantifier: a dismissal, never an erase
    "forget that",                        # the previous turn's job
    "forget what I said",                 # no subject: the previous turn's job
    "don't forget what I told you about the bins",   # a teach
    "what do you know about my mum's heart",
])
def test_topic_forget_negative_controls(text):
    assert forget_topic.parse(text) is None


def test_intent_routing_topic_vs_name_vs_last():
    topic = intent_router.detect_intent("forget what I told you about my mum's heart", log_miss=False)
    assert topic.name == "memory_forget_entity" and topic.slots == {"topic": "my mum's heart"}
    name = intent_router.detect_intent("forget everything about Dana", log_miss=False)
    assert name.name == "memory_forget_entity" and name.slots.get("name") == "Dana" and "topic" not in name.slots
    assert intent_router.detect_intent("forget that", log_miss=False).name == "memory_forget_last"


def test_topic_forget_removes_exactly_the_matching_rows(svc):
    _seed(svc)
    reply = run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"topic": "my mum's heart"}), UID))
    assert reply.startswith("Done") and "heart" in reply
    assert _live(svc) == sorted([APPT, TEA])          # only the row holding the WHOLE topic went


def test_topic_forget_with_no_match_changes_nothing_and_says_so(svc):
    _seed(svc)
    reply = run(forget_topic.execute(UID, "my cousin's wedding"))
    assert "nothing was changed" in reply and "forgot" not in reply.lower()
    assert _live(svc) == sorted([HEART, APPT, TEA])


def test_topic_forget_refuses_a_broad_sweep(svc, monkeypatch):
    _seed(svc)
    import exact_words
    import turn_forget

    said = "my mum wren has an appointment on tuesday"
    turn_forget.note_user_turn(UID, said)
    run(exact_words.get_backend().add(UID, "t9", 1.0, said, "mum appointment", "chat"))
    monkeypatch.setattr(forget_topic, "MAX_ROWS", 1)
    reply = run(forget_topic.execute(UID, "my mum"))     # two rows hold "mum"
    assert "haven't forgotten any" in reply
    assert _live(svc) == sorted([HEART, APPT, TEA])
    assert run(exact_words.get_backend().rows_matching(UID, "mum")) and not turn_forget.blocks_write(UID, said)   # words untouched too


def test_topic_forget_failed_archive_is_reported_not_erased(svc, monkeypatch):
    _seed(svc)

    async def refused(*_a, **_k):
        return None

    monkeypatch.setattr(svc, "review", refused)
    reply = run(forget_topic.execute(UID, "my mum's heart"))
    assert "still saved" in reply and not reply.startswith("Done")
    assert HEART in _live(svc)


def test_topic_forget_store_down_fails_closed(svc, monkeypatch):
    _seed(svc)

    async def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(svc, "list_by_status", boom)
    reply = run(forget_topic.execute(UID, "my mum's heart"))
    assert "can't say that's forgotten" in reply


def test_topic_forget_retracts_the_turn_in_flight_and_its_words(svc):
    import exact_words
    import turn_forget

    said = "my mum wren has been unwell, the doctors suspect her heart"
    turn_forget.note_user_turn(UID, said)
    run(exact_words.get_backend().add(UID, 't1', 1.0, said, 'mum heart doctor', 'chat'))
    res = run(forget_topic.forget(svc, UID, "my mum's heart"))
    assert res.turns_retracted == 1 and res.words_erased == 1
    with turn_forget.capture_scope(UID, said):
        assert turn_forget.blocks_write(UID)             # its capture, still to come, never lands


def test_guest_has_nothing_to_forget(monkeypatch):
    assert "guest sessions" in run(forget_topic.execute("guest", "my mum's heart"))


def _guard(reply, user_text):
    wr.reset()
    return g.guard_text(reply, UID, wr.begin(), user_text=user_text)


def test_forget_request_gets_the_forget_honest_line_not_the_save_one():
    out = _guard("Done, I've made a note of that.", "forget what I told you about my mum's heart")
    assert "haven't forgotten anything yet" in out and "save" not in out
    # negative control: the same claim on an ordinary remember turn keeps the save line
    assert "manage to save that" in _guard("Done, I've made a note of that.", "remember my mum's birthday is in May")


# ── 2. memory quality ───────────────────────────────────────────────────────────────────────────────────────────────

def test_a_son_named_by_apposition_is_anchored_despite_a_contrast_without_a_comma():
    fact = "User's son is named Jonah"
    assert ma.supports(fact, "btw my son jonah is 16 not young, hes doing his learners and loves cars")
    # negative control: a plain denial of the fact's own predicate still blocks (the contrast rule is not a blanket pass)
    assert not ma.supports("User likes sushi", "i do not like sushi, it is fine")


@pytest.mark.parametrize("fact", ["User's kids are not little", "the kids aren't young", "User's son is no longer a baby"])
def test_a_bare_negated_descriptor_is_not_a_fact(fact):
    assert mq.is_storable_fact(fact) == (False, "negated_descriptor")


@pytest.mark.parametrize("fact", ["User's kids are teenagers, not little", "User's kids are little", "User has two kids who are 16 and 14"])
def test_real_family_facts_still_store(fact):
    assert mq.is_storable_fact(fact)[0] is True


def test_the_model_cannot_mint_a_person_from_a_noun_phrase(monkeypatch):
    import pending_suggestions
    import person_extractor

    for noun_phrase in ("the meaning", "The Meaning", "my life", "that thing"):
        assert pending_suggestions.is_junk_contact_name(noun_phrase)
        assert not (person_extractor._looks_like_person_name(noun_phrase) and person_extractor._plausible_name_shape(noun_phrase))
    for real in ("Jonah", "Mary Jane", "Joan of Arc", "Ludwig van Beethoven"):    # negative control
        assert not pending_suggestions.is_junk_contact_name(real)

    async def boom(*_a, **_k):
        raise AssertionError("a non-name must be refused before any store or db is touched")

    monkeypatch.setattr(person_extractor, "_ensure_db", boom)
    monkeypatch.setattr(person_extractor, "_ingest_to_mempalace", boom)
    assert run(person_extractor.apply_person_fact("the meaning", "preference", "is 42", user_id=UID, source="conversation")) is False


def test_concurrent_passes_do_not_store_one_disclosure_three_times(svc):
    texts = ["User has a learner test for Jonah on Thursday week",
             "User's son Jonah is sitting his learner test on Thursday week",
             "Jonah's learner test is on Thursday week"]

    async def passes():
        await asyncio.gather(*(svc.ingest(t, user_id=UID, source=s)
                               for t, s in zip(texts, ("chat_regex", "turn_digest", "conversation"))))

    run(passes())
    assert len(run(svc.list_by_status(user_id=UID, status="approved"))) == 1


def test_a_richer_restatement_and_a_different_fact_still_land(svc):
    run(svc.ingest("User has a learner test for Jonah on Thursday week", user_id=UID, source="turn_digest"))
    run(svc.ingest("User has a learner test for Jonah on Friday week", user_id=UID, source="chat_regex"))     # a new date
    run(svc.ingest("User has netball on Saturday morning", user_id=UID, source="chat_regex"))
    assert len(run(svc.list_by_status(user_id=UID, status="approved"))) == 3


def test_an_explicit_teach_is_never_deduped_away(svc):
    run(svc.ingest("User has a learner test for Jonah on Thursday week", user_id=UID, source="turn_digest"))
    run(svc.ingest("User's son Jonah is sitting his learner test on Thursday week", user_id=UID, source="brain_tool"))
    assert len(run(svc.list_by_status(user_id=UID, status="approved"))) == 2


# ── 3. contact offers ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", ["no thanks, no contacts please", "dont add anyone", "stop asking me about contacts"])
def test_a_standing_decline_is_recognised(msg):
    assert cop.is_standing_decline(msg)


@pytest.mark.parametrize("msg", ["no thanks", "no", "how do I add contacts?", "not now"])
def test_a_plain_no_is_not_a_standing_decline(msg):
    assert not cop.is_standing_decline(msg)      # a bare no answers ONE offer; it must not switch the feature off


def test_an_offer_is_voiced_only_on_a_turn_about_that_person():
    offer = {"name": "Mia", "relationship": "daughter"}
    for unrelated in ("ta, off to bed, early start", "and about my mum?", "what does lena eat?"):
        assert not cop.related(unrelated, offer)
    for about in ("how is mia going at netball", "my daughter is home", "show me my contacts"):
        assert cop.related(about, offer)


def test_eligible_drops_unrelated_turns(monkeypatch):
    cop.reset()
    pend = [{"id": "a", "name": "Mia", "relationship": "daughter"}]
    assert cop.eligible(UID, "ta, off to bed", pend) == []
    assert cop.eligible(UID, "how is mia going", pend) == pend
    cop.reset()


class _FakeDb:
    def __init__(self):
        self.updates = []

    async def fetch(self, sql, *args):
        self.updates.append(sql)
        return [{"id": "x1"}, {"id": "x2"}]


def test_decline_is_durable_and_closes_open_offers(monkeypatch):
    import contextlib

    import pending_suggestions as ps
    import user_prefs

    store = {}

    async def set_pref(user_id, key, value, **_k):
        store[(user_id, key)] = value

    async def get_pref(user_id, key, default=None, **_k):
        return store.get((user_id, key), default)

    async def delete_pref(user_id, key, **_k):
        store.pop((user_id, key), None)

    db = _FakeDb()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(user_prefs, "set_pref", set_pref)
    monkeypatch.setattr(user_prefs, "get_pref", get_pref)
    monkeypatch.setattr(user_prefs, "delete_pref", delete_pref)
    monkeypatch.setattr(ps, "get_db_ctx", ctx)
    ps._decline_cache.clear()

    assert run(ps.contact_offers_declined(UID)) is False
    assert run(ps.decline_contact_offers(UID)) == 2
    ps._decline_cache.clear()                              # a restart: the answer comes from the stored preference
    assert run(ps.contact_offers_declined(UID)) is True
    assert run(ps.surface_pending_contacts_for_prompt(UID)) == []
    assert run(ps.contact_offers_declined("demo_qa4_00000002")) is False     # per member
    run(ps.allow_contact_offers(UID))
    ps._decline_cache.clear()
    assert run(ps.contact_offers_declined(UID)) is False                      # "you can offer contacts again" lifts it


# ── round 5 additions ───────────────────────────────────────────────────────────────────────────────────────────────

def test_forget_a_stated_clause_is_a_topic_forget():
    assert forget_topic.parse("Please forget that Edward dislikes fish. I find it was not correct.") == "Edward dislikes fish"
    d = intent_router.detect_intent("forget that Edward dislikes fish", log_miss=False)
    assert d.name == "memory_forget_entity" and d.slots["topic"] == "Edward dislikes fish"


@pytest.mark.parametrize("text", ["forget that it rained", "forget that I said hi there", "forget that", "forget it"])
def test_clause_forget_negative_controls(text):
    assert forget_topic.parse(text) is None


def test_clause_forget_removes_the_row_and_writes_nothing_new(svc):
    run(svc.ingest("Edward dislikes fish", user_id=UID, source="brain_tool"))
    run(svc.ingest("Edward likes cricket", user_id=UID, source="brain_tool"))
    reply = run(forget_topic.execute(UID, "Edward dislikes fish"))
    assert reply.startswith("Done")
    assert _live(svc) == ["Edward likes cricket"]            # and no contrary "Edward likes fish" was invented


@pytest.mark.parametrize("msg", ["just stay with me a bit", "dont know if i can keep doing this"])
def test_no_offer_on_a_distress_turn(msg):
    assert cop.no_offer_turn(msg)
    assert not cop.no_offer_turn("what is the weather")      # negative control


def test_a_bare_ok_does_not_bind_when_the_previous_reply_was_not_the_offer(monkeypatch):
    import pending_suggestions

    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")

    async def surfaced(user_id, *, limit=5):
        return [{"id": "a", "name": "Alder", "relationship": "teacher"}]

    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", surfaced)
    cop.reset()
    for prev, bound in (("Would you like me to add Alder as a contact?", True), ("The bus runs every ten minutes.", False)):
        async def previous(_uid, _p=prev):
            return _p

        monkeypatch.setattr(intent_router, "_previous_assistant_message", previous)
        got = run(intent_router._match_pending_offer_reply("ok", UID))
        assert (got is not None) is bound

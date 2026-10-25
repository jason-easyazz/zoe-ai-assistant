"""trait_challenge + the card's tentative marking (pure / fake store; invented people only).

Negative controls: drop the article requirement (a bare state "I'm not sure I'm ready" would flag rows);
drop the negation check in ``matching_rows`` (a row that already denies the trait is flagged again);
drop the ``speaker_verified`` guard (a stranger's hedge parks the owner's row); drop ``is_tentative``
(an inferred or unconfirmed religion row is stated flat on the card).
"""
import asyncio
import datetime as dt

import pytest

import trait_challenge as tc
import user_model_card as card
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("turn,trait", [
    ("Well, I'm not sure I'm a mountain person, but, um... at least she knows me.", "mountain person"),
    ("I’m not really a morning person", "morning person"),
    ("I don't think I'm a big spender.", "big spender"),
    ("Honestly I wouldn't say I'm an early riser", "early riser"),
    ("I am not sure that I am a chess player and that's fine", "chess player"),
])
def test_hedge_or_denial_is_read(turn, trait):
    assert tc.challenged_trait(turn) == trait


@pytest.mark.parametrize("turn", [
    "I'm not sure I'm ready for this",          # a state, no article
    "I'm a mountain person",                    # an affirmation
    "She's not a morning person",               # someone else
    "I'm not sure what a mountain person is",
    "",
])
def test_non_challenges_are_ignored(turn):
    assert tc.challenged_trait(turn) is None


def row(i, text, status="approved", **meta):
    return MemoryRef(id=i, text=text, metadata={"status": status, "user_id": "ottilie", **meta})


def test_matching_rows_needs_every_content_word_and_skips_negations():
    rows = [row("a", "User is a mountain person."), row("b", "User's job/role: mountain person"),
            row("c", "User is not a mountain person."), row("d", "User loves the mountains."),
            row("e", "User is a person who likes tea."), row("f", "User is a mountain person.", status="pending")]
    assert [r.id for r in tc.matching_rows("mountain person", rows)] == ["a", "b"]


class FakeSvc:
    def __init__(self, rows):
        self.rows, self.flagged = rows, []

    async def list_by_status(self, *, user_id, status, limit):
        return [r for r in self.rows if r.metadata["status"] == status]

    async def flag_challenged(self, mem_id, *, user_id, utterance):
        self.flagged.append((mem_id, user_id))
        return True


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_apply_flags_only_the_matching_rows():
    svc = FakeSvc([row("a", "User is a mountain person."), row("d", "User plays the cello.")])
    out = run(tc.apply_challenge("ottilie", "Well, I'm not sure I'm a mountain person, but...", svc=svc))
    assert out == ["a"] and svc.flagged == [("a", "ottilie")]


def test_apply_ignores_an_unverified_speaker_and_a_plain_turn():
    svc = FakeSvc([row("a", "User is a mountain person.")])
    assert run(tc.apply_challenge("ottilie", "I'm not sure I'm a mountain person", svc=svc,
                                  speaker_verified=False)) == []
    assert run(tc.apply_challenge("ottilie", "I love the mountains a lot", svc=svc)) == []
    assert svc.flagged == []


# ---- the card never states an inferred / unconfirmed special-category trait flat ----------------

NOW = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc)


def ref(i, text, typ="profile", **meta):
    md = {"memory_type": typ, "status": "approved", "source": "turn_digest",
          "added_at": "2026-09-29T10:00:00Z", **meta}
    return MemoryRef(id=i, text=text, metadata=md)


def render(rows):
    return card.render_card(card.build_card("Ottilie", rows, None, now=NOW))


def test_inferred_row_is_marked_unconfirmed():
    out = render([ref("1", "User plays the cello in an orchestra.", "habit",
                      authority="inferred", authority_class="model_from_transcript")])
    assert "plays the cello in an orchestra (unconfirmed)" in out


def test_stated_row_is_not_marked():
    out = render([ref("1", "User plays the cello in an orchestra.", "habit",
                      authority="user_stated", authority_class="user_stated")])
    assert "unconfirmed" not in out


def test_religion_trait_is_marked_unless_confirmed():
    said = ref("1", "User is a person of deep faith.", authority="user_stated", authority_class="user_stated",
               concept_tags="religion,identity")
    assert "(unconfirmed)" in render([said])
    confirmed = ref("1", "User is a person of deep faith.", authority="user_confirmed",
                    authority_class="user_confirmed", concept_tags="religion,identity")
    assert "unconfirmed" not in render([confirmed])


def test_legacy_row_without_stamped_provenance_is_unchanged():
    assert "unconfirmed" not in render([ref("1", "User plays the cello in an orchestra.", "habit")])


# ---- review-round hardening ---------------------------------------------------------------------

@pytest.mark.parametrize("turn", [
    "What if I'm not a morning person?",
    "I used to say I'm not a morning person, but I am now.",
    "Suppose I'm not a mountain person, what then?",
])
def test_hypothetical_or_reversed_denials_are_ignored(turn):
    assert tc.challenged_trait(turn) is None


def test_relatives_facts_are_never_matched():
    rows = [row("a", "User's sister is a doctor."), row("b", "User is a doctor."),
            row("c", "User's sister is a morning person.")]
    assert [r.id for r in tc.matching_rows("doctor", rows)] == ["b"]
    assert tc.matching_rows("morning person", rows) == []


def _bare_service(rows, order):
    import memory_service as ms

    svc = object.__new__(ms.MemoryService)
    svc._user_locks = {}
    writes = []

    async def get(mem_id):
        order.append("get")
        return rows.get(mem_id)

    async def run_sync(fn, *a):
        return fn(*a)

    svc.get = get
    svc._run_sync = run_sync
    svc._write_row = lambda mem_id, text, md: writes.append((mem_id, md))

    async def audit(**kw):
        pass

    svc._append_audit = audit
    return svc, writes


def test_flag_challenged_rereads_inside_the_lock_so_an_erase_is_not_undone(monkeypatch):
    import memory_service as ms

    monkeypatch.setattr(ms, "_invalidate_agent_user_facts_cache", lambda uid: None)
    rows = {"a": MemoryRef(id="a", text="User is a mountain person.",
                           metadata={"status": "approved", "user_id": "ottilie"})}
    order = []
    svc, writes = _bare_service(rows, order)

    async def scenario():
        lock = svc._user_locks.setdefault("ottilie", asyncio.Lock())
        async with lock:                                   # an erase holds the lock ...
            task = asyncio.ensure_future(svc.flag_challenged("a", user_id="ottilie", utterance="x"))
            await asyncio.sleep(0.05)
            assert order == []                             # ... and flag has not read a stale copy yet
            rows.pop("a")                                  # the erase lands
        return await task

    assert run(scenario()) is False and writes == []


def test_flag_challenged_parks_only_the_owners_row(monkeypatch):
    import memory_service as ms

    monkeypatch.setattr(ms, "_invalidate_agent_user_facts_cache", lambda uid: None)
    rows = {"a": MemoryRef(id="a", text="User is a mountain person.",
                           metadata={"status": "approved", "user_id": "ottilie"})}
    svc, writes = _bare_service(rows, [])
    assert run(svc.flag_challenged("a", user_id="someone-else", utterance="x")) is False
    assert run(svc.flag_challenged("a", user_id="ottilie", utterance="I'm not sure I'm a mountain person"))
    assert writes[0][1]["status"] == "pending" and writes[0][1]["challenged_by_turn"]


def test_deep_sleep_never_promotes_or_archives_a_challenged_row(monkeypatch):
    import memory_digest as md
    import memory_service as ms

    hot = {"user_id": "ottilie", "status": "pending", "confidence": 0.9, "access_count": 50,
           "unique_query_count": 10, "added_at": "2020-01-01T00:00:00Z"}

    class Col:
        upserts = []

        def get(self, **kw):
            return {"ids": ["c", "p"], "documents": ["User is a mountain person.", "User likes tea."],
                    "metadatas": [{**hot, "challenged_by_turn": "I'm not sure"}, dict(hot)]}

        def upsert(self, ids, documents, metadatas):
            self.upserts.append(ids[0])

    col = Col()
    monkeypatch.setattr(ms, "get_memory_service", lambda: object())
    monkeypatch.setattr(ms, "leased_drawers", lambda svc: col)
    monkeypatch.setattr(md, "_promotion_score", lambda meta: 0.95)
    run(md._deep_sleep_pass("ottilie"))
    assert col.upserts == ["p"]


# ---- final review round: scope the vetoes, tie a reversal to the same trait -----------------------

@pytest.mark.parametrize("turn,trait", [
    ("You know, I'm not a morning person.", "morning person"),
    ("You keep saying I'm a morning person, but I'm not a morning person.", "morning person"),
    ("If I'm tired, I'm not a morning person.", "morning person"),
    ("I'm not a doctor, but now I'm a teacher.", "doctor"),
    ("I'm not a morning person, but I'm now working early shifts.", "morning person"),
])
def test_real_denials_survive_prefix_and_unrelated_now(turn, trait):
    assert tc.challenged_trait(turn) == trait


@pytest.mark.parametrize("turn", [
    "I'm not a morning person, but actually I am.",
    "I'm not a doctor, but now I'm a doctor.",
    "She says I'm not a morning person.",
])
def test_taken_back_or_reported_denials_are_ignored(turn):
    assert tc.challenged_trait(turn) is None


def test_possessive_in_an_owner_facts_details_still_matches():
    rows = [row("a", "User is a doctor at St Mary's Hospital."), row("b", "User's sister is a doctor."),
            row("c", "User is a nurse and User's sister is a doctor.")]
    assert [r.id for r in tc.matching_rows("doctor", rows)] == ["a"]

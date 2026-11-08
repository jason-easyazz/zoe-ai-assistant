"""QA round 5 memory fixes: a correction survives a session boundary, a fact about someone else is not "the user's", and a
contact DELETE is never claimed without a tool.

Invented names only (Oliver / Anaya / Arjun / Priya / Tom are synthetic); no transcript is quoted.

Break-the-fix: drop the `review(edit)` in `value_correction.apply` -> `test_correction_supersedes_across_sessions` goes red;
let `fact_subject.attribute` return "keep" -> the attribution tests go red; drop `removal_only_targets` from the lexicon ->
`test_contact_delete_claim_is_replaced` goes red.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

import memory_service
import value_correction as vc
import fact_subject
import write_claim_guard as wcg
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe

UID = "member-qa5"
OTHER_UID = "member-other"


# ── fakes (same shape as test_memory_implicit_supersede) ──────────────────────────────────────────────────────────────
def _match(meta, where):
    if not where:
        return True
    if "$and" in where:
        return all(_match(meta, w) for w in where["$and"])
    if "$or" in where:
        return any(_match(meta, w) for w in where["$or"])
    for key, want in where.items():
        if isinstance(want, dict):
            if "$gte" in want and not (meta.get(key) is not None and meta[key] >= want["$gte"]):
                return False
        elif meta.get(key) != want:
            return False
    return True


class _Col:
    def __init__(self):
        self.rows = {}

    def upsert(self, *, ids, documents, metadatas, **_kw):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def update(self, *, ids, metadatas, **_kw):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))

    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        keys = [i for i in keys if _match(self.rows[i][1], where)]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys], "metadatas": [dict(self.rows[i][1]) for i in keys]}


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-qa5")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def no_hits(*_a, **_k):
        return []

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    s.search = no_hits
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    import user_prefs

    async def not_out(_uid):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", not_out)
    s._col = col
    return s


async def _seed(s, text, *, user=UID):
    ref = await s.ingest(text, user_id=user, source="turn_digest", memory_type="fact", confidence=0.82, status="approved",
                         tags=["turn_digest"])
    doc, meta = s._col.rows[ref.id]
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    meta.update(added_at=when.isoformat().replace("+00:00", "Z"), added_ts=when.timestamp())
    return ref.id


async def _approved(s, user=UID):
    return {r.text: r for r in await s.list_by_status(user_id=user, status="approved", limit=100)}


OLIVER = "User's child Oliver is twenty-four years old and is a junior engineer who lives nearby."
CHARLOTTE = "User's child Charlotte is twenty-eight years old and lives in London."
ANAYA = "Anaya is six."


# ── 1. durable supersede ────────────────────────────────────────────────────────────────────────────────────────────────
def test_statement_shapes():
    s = vc.statement("A small correction: Oliver is twenty-five, not twenty-four. I misspoke.")
    assert (s.subject, s.new, s.old) == ("Oliver", "twenty-five", "twenty-four")
    s = vc.statement("correction. anaya is 7 not 6")
    assert (s.subject, s.new, s.old) == ("anaya", "7", "6")
    s = vc.statement("Anaya is 7 now")
    assert (s.subject, s.new, s.old) == ("Anaya", "7", None)
    # negative controls: a description, a bare statement and an everyday sentence are not corrections
    assert vc.statement("Tom is tall, not short") is None
    assert vc.statement("Oliver is 25") is None
    assert vc.statement("it is 7, not 6") is None


def test_rewrite_keeps_the_rows_spelling_and_detail():
    c = vc.statement("Oliver is 25 not 24")
    assert vc.rewrite(OLIVER, c) == OLIVER.replace("twenty-four", "twenty-five")
    assert vc.rewrite("Oliver is 24.", c) == "Oliver is 25."
    assert vc.rewrite(CHARLOTTE, c) is None                       # another person's age
    assert vc.rewrite("Oliver might look after his father.", c) is None       # no age in the row


def test_correction_supersedes_across_sessions(svc):
    async def run():
        old = await _seed(svc, OLIVER)
        other = await _seed(svc, CHARLOTTE)
        # a different member's row about the same name must not move
        foreign = await _seed(svc, OLIVER, user=OTHER_UID)
        # session 2: the correction arrives long after the row was written (it carries NO session of session 2)
        n = await vc.apply("A small correction: Oliver is twenty-five, not twenty-four.", UID, "session-2", svc=svc)
        assert n == 1
        rows = await _approved(svc)
        # session 3 reads: only the newest value is served
        assert OLIVER.replace("twenty-four", "twenty-five") in rows
        assert OLIVER not in rows
        assert CHARLOTTE in rows                                   # negative control: the unrelated fact is untouched
        assert svc._col.rows[old][1].get("status") == "superseded"
        assert svc._col.rows[other][1].get("status") == "approved"
        assert svc._col.rows[foreign][1].get("status") == "approved"
        new = rows[OLIVER.replace("twenty-four", "twenty-five")]
        # user-stated provenance: the member's own words are the evidence, written by the user-turn correction writer
        assert (new.metadata.get("source") or new.metadata.get("origin")) == "conversation_correction"
        assert "twenty-five" in (new.metadata.get("source_excerpt") or "")
        # idempotent: replaying the same correction changes nothing
        assert await vc.apply("A small correction: Oliver is twenty-five, not twenty-four.", UID, "session-3", svc=svc) == 0

    asyncio.run(run())


def test_age_restated_with_no_old_value(svc):
    async def run():
        await _seed(svc, ANAYA)
        await _seed(svc, "Arjun is nine years old.")
        assert await vc.apply("anaya is 7 now", UID, "s2", svc=svc) == 1
        rows = await _approved(svc)
        assert "Anaya is seven." in rows and ANAYA not in rows
        assert "Arjun is nine years old." in rows

    asyncio.run(run())


def test_no_evidence_changes_nothing(svc):
    async def run():
        await _seed(svc, CHARLOTTE)
        assert await vc.apply("Oliver is 25 not 24", UID, "s2", svc=svc) == 0
        assert set(await _approved(svc)) == {CHARLOTTE}
        # an unverified speaker never rewrites a durable row
        await _seed(svc, OLIVER)
        assert await vc.apply("Oliver is 25 not 24", UID, "s2", svc=svc, speaker_verified=False) == 0
        assert OLIVER in await _approved(svc)

    asyncio.run(run())


def test_meta_fact_about_a_correction_is_not_storable():
    from memory_quality import is_storable_fact

    ok, reason = is_storable_fact("The user corrected a previous statement regarding Oliver's age.")
    assert not ok and reason == "meta_rambling"
    assert is_storable_fact("User's child Oliver is twenty-five years old.")[0]


def test_extract_and_ingest_runs_the_value_correction(monkeypatch):
    import memory_extractor

    seen = []

    async def fake_apply(msg, uid, sid, **kw):
        seen.append((msg, uid, sid))
        return 0

    monkeypatch.setattr(vc, "apply", fake_apply)
    asyncio.run(memory_extractor.extract_and_ingest("Oliver is 25 not 24", "", user_id=UID, session_id="s9", prev_user_message=""))
    assert seen == [("Oliver is 25 not 24", UID, "s9")]


# ── 2. wrong-subject attribution ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fact,message,verdict,out", [
    # a relative's trait said in passing is not the user's
    ("The user is sober for once.", "dad rang today. sober for once. weird", "rewrite", "User's dad is sober for once."),
    # "for <Name>" re-attaches to the name, with and without a first person elsewhere in the turn
    ("The user does not eat onion for Arjun.", "what should i cook. no beef. no onion for arjun", "rewrite", "Arjun does not eat onion."),
    ("The user does not eat onion for Arjun.", "priya no beef. arjun hates onion", "rewrite", "Arjun does not eat onion."),
    # two people, nobody speaks of themself: the subject is uncertain -> drop, never mis-attribute
    ("The user does not eat beef.", "priya no beef. arjun hates onion", "drop", "other_subject"),
    # negative controls: the user's own words keep the fact
    ("The user is sober for once.", "my dad rang. I am sober for once", "keep", None),
    ("User is allergic to nuts.", "allergic to nuts", "keep", None),
    ("User is allergic to nuts.", "I am allergic to nuts and arjun hates onion", "keep", None),
    ("User's dog is named Teddy.", "my dog is Teddy", "keep", None),
])
def test_fact_subject(fact, message, verdict, out):
    v, text = fact_subject.attribute(fact, message)
    assert v == verdict
    if out is not None:
        assert text == out


def test_digest_drops_and_reattaches(monkeypatch, svc):
    """Through the real turn digest: the 4B returns the user-filed fact, the stored row is about the right person (or absent)."""
    import json
    import memory_digest

    class _Resp:
        def __init__(self, facts):
            self._f = facts

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(self._f)}}]}

    def run(message, facts):
        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Resp(facts)

        monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
        import sys
        import types

        stub = types.ModuleType("zoe_agent")

        async def _blob(*a, **k):
            return ""

        stub._mempalace_load_user_facts = _blob
        stub._invalidate_user_facts_cache = lambda *_a, **_k: None
        monkeypatch.setitem(sys.modules, "zoe_agent", stub)
        return asyncio.run(memory_digest.run_turn_digest(UID, message, "", session_id="s1"))

    run("priya no beef. arjun hates onion", [
        {"type": "preference", "fact": "The user does not eat onion for Arjun."},
        {"type": "preference", "fact": "The user does not eat beef."},
    ])
    texts = set(asyncio.run(_approved(svc)))
    assert "Arjun does not eat onion." in texts
    assert not any(t.lower().startswith(("the user does not", "user does not")) for t in texts)


# ── 3. a contact delete is never claimed ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("reply", [
    "I've deleted Tom from your contacts.",
    "I have removed Tom from your contacts.",
    "Tom has been removed from your contacts.",
])
def test_contact_delete_claim_is_replaced(reply):
    claim = wcg.find_claim(reply)
    assert claim is not None and claim.kinds == ("contact_removed",)
    assert "can't delete a contact" in claim.honest
    out = wcg.guard_text(reply + " Anything else?", UID, wcg.write_receipts.now() + 1, user_text="delete Tom from my contacts")
    assert "deleted Tom" not in out and "removed Tom" not in out
    assert "can't delete a contact" in out


@pytest.mark.parametrize("reply", [
    "I've added Tom to your contacts.",                             # an add is another writer's claim, not this guard's kind
    "Would you like me to delete Tom from your contacts?",          # an offer
    "I can't delete contacts yet.",                                 # a refusal
    "I've deleted the milk from your shopping list.",               # a list removal still needs a LIST receipt, not a contact one
])
def test_other_replies_are_not_contact_claims(reply):
    claim = wcg.find_claim(reply)
    assert claim is None or "contact_removed" not in claim.kinds

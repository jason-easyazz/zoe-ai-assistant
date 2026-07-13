"""QA review F13 + F14: honest teach confirmations and entity-scoped forget.

F13 — expert_dispatch.store_fact ingests inline, so its instant confirmation
must track the ACTUAL store outcome: a raised/refused write gets an honest
"couldn't save" reply, never "Got it — I'll remember…".

F14 — intent_router grows a deterministic "forget everything about X" path:
name-anchored archive (soft-delete) of the caller's rows only; guests fail
closed; no LLM involved. Slim-dep → GitHub `-m ci_safe` lane.
"""
import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

expert_dispatch = pytest.importorskip("expert_dispatch")
intent_router = pytest.importorskip("intent_router")
memory_service = pytest.importorskip("memory_service")


def _run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, mem_id, text, status="approved"):
        self.id = mem_id
        self.text = text
        self.metadata = {"status": status, "user_id": "jason"}


class _FakeSvc:
    """Minimal MemoryService stand-in with soft-delete bookkeeping."""

    def __init__(self, rows=None, ingest_result="row", raise_on_ingest=False):
        self.rows = list(rows or [])
        self.ingested: list[str] = []
        self.archived: list[str] = []
        self._ingest_result = ingest_result
        self._raise = raise_on_ingest

    async def ingest(self, text, **kw):
        if self._raise:
            raise memory_service.MemoryServiceError("chroma down")
        self.ingested.append(text)
        if self._ingest_result == "row":
            return _Row("new-" + str(len(self.ingested)), text)
        return None  # silent drop (PII reject / opt-out)

    async def search(self, query, *, user_id, limit=10, **kw):
        q = query.lower()
        return [r for r in self.rows if q in r.text.lower()][:limit]

    async def list_by_status(self, *, user_id, status="pending", limit=100, offset=0):
        return [r for r in self.rows if r.metadata.get("status") == status][:limit]

    async def review(self, mem_id, *, decision, actor, edits=None, note=None):
        assert decision == "archive", "entity forget must soft-archive, never delete"
        self.archived.append(mem_id)
        for r in self.rows:
            if r.id == mem_id:
                r.metadata["status"] = "archived"
                return r
        raise memory_service.MemoryServiceError("not found")


# ---------------------------------------------------------------------------
# F13 — store_fact confirmation honesty
# ---------------------------------------------------------------------------

def test_failed_store_gets_honest_reply(monkeypatch):
    svc = _FakeSvc(raise_on_ingest=True)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(expert_dispatch.store_fact(
        "people", "my dad's name is Neil", "jason"))
    assert out is not None
    assert "Got it" not in out and "remember" not in out.lower().replace(
        "telling me again", ""), out
    assert "couldn't save" in out.lower(), out
    assert svc.ingested == []


def test_silently_dropped_store_gets_honest_reply(monkeypatch):
    # ingest returns None and the scrubber says the text is PII-rejected →
    # honest "not stored" reply, no success claim.
    svc = _FakeSvc(ingest_result=None)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(memory_service, "scrub_pii",
                        lambda t: (t, "credit_card"))
    out = _run(expert_dispatch.store_fact(
        "people", "my dad's name is Neil", "jason"))
    assert out is not None
    assert "Got it" not in out, out
    assert "nothing was stored" in out.lower(), out


def test_successful_store_keeps_confirmation(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(expert_dispatch.store_fact(
        "people", "my dad's name is Neil", "jason"))
    assert out is not None and out.startswith("Got it"), out
    assert svc.ingested, "the fact must actually be stored"


def test_dedup_skip_still_confirms(monkeypatch):
    # An equivalent fact already in the store: nothing new written, but the
    # fact IS remembered — confirming remains honest (unchanged behaviour).
    svc = _FakeSvc(ingest_result=None)  # idempotent dedup path returns None
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(memory_service, "scrub_pii", lambda t: (t, None))
    out = _run(expert_dispatch.store_fact(
        "people", "my dad's name is Neil", "jason"))
    assert out is not None and out.startswith("Got it"), out


# ---------------------------------------------------------------------------
# F14 — intent detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,name", [
    ("forget everything about Caitlin", "Caitlin"),
    ("forget what I told you about Jessica", "Jessica"),
    ("Forget what I've told you about Mary Jane.", "Mary Jane"),
    ("please forget about Delia", "Delia"),
    ("delete everything about Bianca", "Bianca"),
    ("forget what you know about Neil", "Neil"),
])
def test_forget_entity_detected(phrase, name):
    intent = intent_router.detect_intent(phrase, log_miss=False)
    assert intent is not None and intent.name == "memory_forget_entity", phrase
    assert intent.slots.get("name") == name


@pytest.mark.parametrize("phrase", [
    "forget about it",
    "forget about everything",
    "forget about my day",
    "forget about her",
    "forget about that",
])
def test_forget_entity_rejects_non_names(phrase):
    intent = intent_router.detect_intent(phrase, log_miss=False)
    assert intent is None or intent.name != "memory_forget_entity", phrase


def test_forget_last_still_detected():
    intent = intent_router.detect_intent("forget that", log_miss=False)
    assert intent is not None and intent.name == "memory_forget_last"


# ---------------------------------------------------------------------------
# F14 — execution: archives only the named entity's rows, soft-delete only
# ---------------------------------------------------------------------------

def _seeded_svc():
    return _FakeSvc(rows=[
        _Row("m1", "Caitlin is allergic to nuts"),
        _Row("m2", "Caitlin works as a nurse"),
        _Row("m3", "My dad's name is Neil"),
        _Row("m4", "Caitlyn spelled differently is someone else"),
    ])


def test_forget_entity_archives_only_named_rows(monkeypatch):
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Caitlin"}),
        "jason"))
    assert out is not None
    assert sorted(svc.archived) == ["m1", "m2"], svc.archived
    assert "2 things about Caitlin" in out, out
    # Others untouched — strict word-boundary matching, no fuzzy nuking.
    assert svc.rows[2].metadata["status"] == "approved"
    assert svc.rows[3].metadata["status"] == "approved"


def test_forget_entity_no_matches(monkeypatch):
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Zelda"}),
        "jason"))
    assert out is not None and "don't have anything saved about Zelda" in out
    assert svc.archived == []


def test_forget_entity_guest_fails_closed(monkeypatch):
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Caitlin"}),
        "guest"))
    assert out is not None and "guest" in out.lower()
    assert svc.archived == [], "guests must never trigger archive sweeps"


def test_forget_entity_store_down_is_honest(monkeypatch):
    class _Boom:
        async def search(self, *a, **k):
            raise RuntimeError("chroma down")

        async def list_by_status(self, *a, **k):
            raise RuntimeError("chroma down")

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Boom())
    out = _run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Caitlin"}),
        "jason"))
    assert out is not None and "nothing was changed" in out

"""expert_dispatch.store_fact must tell a RECALL question from a STATEMENT to store.

On-device, "Do you remember what my mum's name is?" was stored verbatim as a fact
("Got it — I'll remember Do you remember…") because the word 'remember' tripped the
explicit-store signal and _QUESTION_RE didn't know the "do YOU remember…" form.
A recall question must route to recall (_run_expert) and never ingest; a genuine
teach statement must still be stored.
"""
import asyncio

import pytest

expert_dispatch = pytest.importorskip("expert_dispatch")
memory_service = pytest.importorskip("memory_service")


def _run(coro):
    return asyncio.run(coro)


class _FakeSvc:
    def __init__(self):
        self.ingested: list[str] = []

    async def ingest(self, text, **kw):
        self.ingested.append(text)

    async def search(self, *a, **k):
        return []


@pytest.fixture
def spy(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    recalled: list[str] = []

    async def _fake_run_expert(domain, text, user_id, session_id):
        recalled.append(text)
        return "RECALLED"

    monkeypatch.setattr(expert_dispatch, "_run_expert", _fake_run_expert)
    return svc, recalled


@pytest.mark.parametrize("q", [
    "Do you remember what my mum's name is?",   # the on-device failure
    "do you recall my dad's name",
    "can you remember where I work",
    "can ya remember where I work",             # 'ya' colloquial — must not store
    "what is my dad's name?",
    "what's my mum's name",
    "who is my sister",
    "remind me what my password hint is",
])
@pytest.mark.parametrize("domain", ["people", "memory"])
def test_recall_questions_are_not_stored(spy, domain, q):
    svc, recalled = spy
    out = _run(expert_dispatch.store_fact(domain, q, "jason"))
    assert svc.ingested == [], f"{q!r} was wrongly stored as a fact"
    assert recalled == [q], f"{q!r} should route to recall"
    assert out == "RECALLED"


@pytest.mark.parametrize("s", [
    "my dad's name is Neil",
    "remember that my mum likes NCIS",
    # Plain personal statement (was "note that my lucky number is 47" — #1150
    # deliberately reassigned "note that …" to the notes capability, which
    # defers to the brain rather than storing as memory; that defer is covered
    # by test_expert_dispatch_note_defer.py. This case keeps validating that a
    # bare fact statement still stores).
    "my lucky number is 47",
    "don't forget my sister's birthday is in May",
    "keep in mind my dentist is on Tuesday",
])
def test_statements_are_stored(spy, s):
    svc, recalled = spy
    out = _run(expert_dispatch.store_fact("people", s, "jason"))
    assert recalled == [], f"{s!r} was wrongly treated as recall"
    assert svc.ingested == [s], f"{s!r} should be stored"
    assert out and out.startswith("Got it")

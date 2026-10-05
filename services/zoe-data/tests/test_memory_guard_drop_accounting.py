"""Every extractor guard drop is counted in the reject ledger (brain-extraction research L6).

The person extractor's guards (low confidence, unanchored role, unstated role, unsupported user
anchor, non-name) and the digests' dedup / anchor guards dropped facts with an INFO log line and no
counter, so a guard that began eating real facts was invisible. Each drop now writes
``<source>|guard_<guard>`` to ``memory_reject_ledger``; the nightly ``MEMORY_REJECT_SUMMARY`` line
reads e.g. ``reasons=guard_value_role_unsupported:1``.

Fakes only (HTTP + apply_person_fact); synthetic names; the ledger file is pinned to tmp_path.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

import memory_digest
import memory_reject_ledger as led
import memory_service
import person_extractor
import person_extractor_llm as pel

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.delenv("ZOE_PERSON_LLM_CONFIDENCE_GATE", raising=False)
    led.reset_for_tests()
    yield led
    led.reset_for_tests()


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._p)}}]}


class _Client:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return _Resp(self._p)


def _run_llm(monkeypatch, items, text):
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append((name, value))
        return True

    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)
    written = asyncio.run(pel.process_text_llm(text, user_id="demo-user"))
    return written, applied


def test_unanchored_role_drop_is_counted(monkeypatch):
    written, applied = _run_llm(
        monkeypatch,
        [{"name": "Casey Smith", "fact_type": "preference", "value": "wife"}],
        "My friend Jordan came over and Casey was there too with the kids",
    )
    assert written == 0 and applied == []
    s = led.summary(24)
    assert s["reasons"] == {"guard_unanchored_role": 1}
    assert s["sources"] == {"person_extractor_llm": 1}


def test_unstated_role_drop_is_counted(monkeypatch):
    """The text never ties the role to this name: value_role_unsupported drops it."""
    written, applied = _run_llm(
        monkeypatch,
        [{"name": "Casey Smith", "fact_type": "preference", "value": "wife of Jordan Smith"}],
        "Jordan Smith and Casey Smith came over with a partner and two kids today",
    )
    assert written == 0 and applied == []
    assert led.summary(24)["reasons"] == {"guard_value_role_unsupported": 1}


def test_guessed_user_anchor_drop_is_counted(monkeypatch):
    written, applied = _run_llm(
        monkeypatch,
        [{"name": "Casey Smith", "fact_type": "preference", "value": "wife of user"}],
        # the text ties Casey to "wife" (so the role is stated) but never says "my wife"
        "No Jordan is my male friend, Casey is the wife and Riley and Morgan are the girls",
    )
    assert written == 0 and applied == []
    assert led.summary(24)["reasons"] == {"guard_user_anchor_unsupported": 1}


def test_low_confidence_drop_is_counted(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_GATE", "1")
    written, applied = _run_llm(
        monkeypatch,
        [{"name": "Riley Jones", "fact_type": "preference", "value": "likes hiking", "confidence": 0.1}],
        "Riley Jones mentioned that maybe they like hiking sometimes at the weekend",
    )
    assert written == 0 and applied == []
    assert led.summary(24)["reasons"] == {"guard_low_confidence": 1}


def test_a_kept_fact_counts_nothing(monkeypatch):
    """Negative control: a fact no guard touches leaves the ledger empty."""
    written, applied = _run_llm(
        monkeypatch,
        [{"name": "Riley Jones", "fact_type": "preference", "value": "likes hiking"}],
        "Riley Jones likes hiking every weekend with the dog",
    )
    assert written == 1 and applied == [("Riley Jones", "likes hiking")]
    assert led.summary(24)["rejected"] == 0


def test_the_nightly_summary_line_names_the_guard(monkeypatch):
    _run_llm(
        monkeypatch,
        [{"name": "Casey Smith", "fact_type": "preference", "value": "wife of Jordan Smith"}],
        "Jordan Smith and Casey Smith came over with a partner and two kids today",
    )
    line = led.format_summary(24)
    assert "MEMORY_REJECT_SUMMARY" in line
    assert "guard_value_role_unsupported:1" in line and "person_extractor_llm:1" in line


def test_non_name_drop_in_the_regex_person_extractor_is_counted():
    person_extractor._count_guard_drop("non_name")
    assert led.summary(24)["reasons"] == {"guard_non_name": 1}


def test_counting_never_raises_into_the_write_path(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(led, "record_reject", boom)
    pel._count_drop("unanchored_role")
    person_extractor._count_guard_drop("non_name")
    memory_digest._count_drop("digest", "dedup_overlap", gate=False)


def test_turn_digest_anchor_guard_drop_is_counted(monkeypatch):
    """'User's friend Dana has two kids' from a turn that never says 'my kids' is dropped by the
    user-anchor guard (skipped_low_quality) - and is now counted with its reason."""
    class _Svc:
        def __init__(self):
            self.ingested = []

        async def ingest(self, text, **kw):
            self.ingested.append(text)
            return None

        async def search(self, *a, **k):
            return []

    svc = _Svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    fact = "User's friend Dana has two kids Mika and Biscuit"
    monkeypatch.setattr(memory_digest.httpx, "AsyncClient",
                        lambda *a, **k: _Client([{"fact": fact, "type": "fact"}]))
    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)

    result = asyncio.run(memory_digest.run_turn_digest(
        "demo-user", "my friend Dana has two kids Mika and Biscuit", session_id="s1"))
    assert svc.ingested == [] and result["skipped_low_quality"] == 1
    assert led.summary(24)["reasons"] == {"guard_user_anchor_unsupported": 1}

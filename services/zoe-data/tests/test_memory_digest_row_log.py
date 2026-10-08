"""Every row the turn digest and the nightly digest store or park leaves ONE replayable INFO line.

Why (the day-sim 6n diagnosis, 2026-10-08): the extractor's exact wording and the row's class were not in the log, so
a live incident could not be replayed - the cause had to be guessed. Each stored / parked / edited / held row now logs

    MEMORY_ROW lane=<turn_digest|digest|emotional> outcome=<stored|parked|edited|held> user=<id> id=<row id>
    class=<authority class> promoted=<yes|no> basis=<authority basis> status=<status> type=<type> wording='<row text>'

The wording is the ROW's text (after the write boundary's scrub), never the owner's turn. These tests pin the line's
shape, that it is post-turn only, and that a failure to log never fails a digest. Synthetic names only.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import types

import pytest

import memory_authority as ma
import memory_digest
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col
from test_memory_implicit_supersede import UID, _flag, _patch_llm, _svc  # noqa: F401

pytestmark = pytest.mark.ci_safe

LINE = re.compile(
    r"^MEMORY_ROW lane=(?P<lane>turn_digest|digest|emotional) outcome=(?P<outcome>stored|parked|edited|held) "
    r"user=(?P<user>\S+) id=(?P<id>\S+) class=(?P<cls>\S+) promoted=(?P<promoted>yes|no) basis=(?P<basis>\S+) "
    r"status=(?P<status>\S+) type=(?P<type>\S+) wording=(?P<wording>'.*'|\".*\")$")

T1 = "I've been getting migraines most afternoons lately and it's starting to worry me."
FACT = "User has been getting migraines most afternoons lately"


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("MEMORY_ROW ")]


def _parse(line):
    m = LINE.match(line)
    assert m, line
    return m.groupdict()


# ── the per-turn digest ─────────────────────────────────────────────────────────────────────────────

def test_a_stored_row_logs_its_id_class_promotion_and_the_extractors_wording(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, [{"type": "profile", "fact": FACT}])
    asyncio.run(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
    [line] = _lines(caplog)
    got = _parse(line)
    row_id, meta = next((i, m) for i, (d, m) in col.rows.items() if d == FACT)
    assert got["lane"] == "turn_digest" and got["outcome"] == "stored" and got["user"] == UID
    assert got["id"] == row_id and got["cls"] == meta["authority_class"] == ma.USER_STATED_DERIVED
    assert got["promoted"] == "yes" and got["basis"] == ma.VERBATIM_BASIS and got["status"] == "approved"
    assert got["wording"] == repr(FACT)
    assert T1 not in line                      # the row text only - never the owner's turn


def test_a_fact_the_owners_turn_does_not_entail_is_not_promoted(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, [{"type": "profile", "fact": "User has been getting migraines"}])
    asyncio.run(memory_digest.run_turn_digest(UID, "Honestly I have been thinking about getting new glasses soon.",
                                              session_id="s-1"))
    lines = _lines(caplog)
    assert len(lines) == 1 and _parse(lines[0])["promoted"] == "no"


def test_a_parked_row_is_logged_as_parked(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)

    async def go():
        await svc.ingest("User lives in Perth", user_id=UID, source="voice_fact", memory_type="profile",
                         confidence=0.9, status="approved")
        _patch_llm(monkeypatch, [{"type": "profile", "fact": "User lives in Hobart"}])
        return await memory_digest.run_turn_digest(UID, "The weather in Hobart looks lovely today, apparently.",
                                                   session_id="s-1")

    asyncio.run(go())
    lines = [_parse(x) for x in _lines(caplog)]
    assert [x["outcome"] for x in lines] == ["parked"]
    assert lines[0]["status"] in ("disputed", "pending") and lines[0]["wording"] == repr("User lives in Hobart")


def test_an_edited_row_is_logged_when_the_reconciler_updates_a_stored_one(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)
    updated = "User lives in Hobart now"

    async def go():
        old = await svc.ingest("User lives in Perth", user_id=UID, source="voice_fact", memory_type="profile",
                               confidence=0.9, status="approved")

        async def upd(*_a, **_k):
            return "update", old.id

        import memory_quality
        monkeypatch.setattr(memory_quality, "reconcile_for_ingest", upd)
        _patch_llm(monkeypatch, [{"type": "profile", "fact": updated}])
        return await memory_digest.run_turn_digest(UID, "I live in Hobart now, I moved there in March.",
                                                   session_id="s-1")

    asyncio.run(go())
    lines = [_parse(x) for x in _lines(caplog)]
    assert [x["outcome"] for x in lines] == ["edited"] and lines[0]["wording"] == repr(updated)


def test_a_dropped_fact_leaves_no_row_line(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, [{"type": "profile", "fact": "short"}])
    asyncio.run(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
    assert _lines(caplog) == []


def test_a_newline_in_the_wording_cannot_forge_a_second_line(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    class Ref:
        id, text, metadata = "r-1", "User likes tea\nMEMORY_ROW lane=forged", {"authority_class": "x"}

    memory_digest.log_row("turn_digest", UID, Ref, "stored")
    [line] = _lines(caplog)
    assert "\n" not in line and _parse(line)["lane"] == "turn_digest"


def test_a_logging_failure_never_fails_the_digest():
    class Boom:
        @property
        def metadata(self):
            raise RuntimeError("no")

    memory_digest.log_row("turn_digest", UID, Boom(), "stored")      # must not raise
    memory_digest.log_row("turn_digest", UID, None, "stored")


def test_a_long_wording_is_capped(caplog):
    caplog.set_level(logging.INFO)

    class Ref:
        id, text, metadata = "r-1", "User " + "x" * 900, {}

    memory_digest.log_row("digest", UID, Ref, "stored")
    [line] = _lines(caplog)
    assert len(_parse(line)["wording"]) <= memory_digest._ROW_LOG_WORDING_MAX + 4


# ── the nightly digest ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def night_svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    monkeypatch.delenv(ma.OBSERVATION_GATE_ENV, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-row-log")
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


def _night(monkeypatch, transcript, items):
    async def todays(*_a, **_k):
        return transcript

    async def extract(_text):
        return items

    async def no(*_a, **_k):
        return False

    async def none(*_a, **_k):
        return 0

    async def no_blob(*_a, **_k):
        return ""
    stub = types.ModuleType("zoe_agent")
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    monkeypatch.setattr(memory_digest, "_load_todays_messages", todays)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_digest, "_is_contradiction", no)
    monkeypatch.setattr(memory_digest, "_emotional_memory_pass", none)
    return asyncio.run(memory_digest.run_memory_digest(UID))


def test_the_nightly_digest_logs_a_stored_row_and_a_held_one(night_svc, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    transcript = ("I live in Hobart and I work as a ceramicist at the Harbour Studio near the old wharf. "
                  "Yesterday the weather was lovely and we all had a long walk along the foreshore together.")
    supported = "User lives in Hobart"
    unsupported = "User is married to Dagny Falk"
    _night(monkeypatch, transcript, [
        {"fact": supported, "type": "profile", "quote": "I live in Hobart"},
        {"fact": unsupported, "type": "relationship"},
    ])
    got = [_parse(x) for x in _lines(caplog)]
    by_word = {g["wording"]: g for g in got}
    assert by_word[repr(supported)]["lane"] == "digest" and by_word[repr(supported)]["outcome"] == "stored"
    assert by_word[repr(supported)]["status"] == "approved"
    assert by_word[repr(supported)]["id"] in night_svc._col.rows
    held = [g for g in got if g["outcome"] == "held"]
    assert len(held) == 1 and "Dagny" in held[0]["wording"] and held[0]["status"] != "approved"
    assert all(transcript not in x for x in _lines(caplog))     # never the owner's text

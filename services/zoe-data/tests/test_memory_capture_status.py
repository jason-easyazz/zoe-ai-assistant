"""memory_capture_stats + GET /api/memories/capture-status — the observable
completion signal for the post-turn memory capture.

``routers/chat.py`` schedules ``_persist_memory_candidates`` with ensure_future,
so the HTTP turn returning proves nothing about extraction/digest having run;
the counters are started before any early return and completed in ``finally``
(success AND failure), and the route is internal-token only.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import memory_capture_stats as stats
from routers.memories import router as memories_router

pytestmark = pytest.mark.ci_safe

TOKEN = "tok-capture"
HDR = {"X-Internal-Token": TOKEN}


@pytest.fixture(autouse=True)
def _clean():
    stats.reset()
    yield
    stats.reset()


def test_counters_and_in_flight():
    assert stats.snapshot("u1") == {"started": 0, "completed": 0, "failed": 0,
                                    "last_completed_at": None, "in_flight": 0}
    stats.started("u1")
    stats.started("u1")
    assert stats.snapshot("u1")["in_flight"] == 2
    stats.completed("u1")
    stats.completed("u1", ok=False)
    s = stats.snapshot("u1")
    assert (s["started"], s["completed"], s["failed"], s["in_flight"]) == (2, 2, 1, 0)
    assert s["last_completed_at"] is not None
    assert stats.snapshot("other")["started"] == 0


def test_bounded_to_max_users(monkeypatch):
    monkeypatch.setattr(stats, "_MAX_USERS", 3)
    for u in ("a", "b", "c", "d"):
        stats.started(u)
    assert stats.snapshot("a")["started"] == 0  # oldest evicted
    assert stats.snapshot("d")["started"] == 1


def _client(monkeypatch, token=TOKEN):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", token)
    app = FastAPI()
    app.include_router(memories_router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_route_is_internal_token_only(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/memories/capture-status?user_id=demo_bar_0a1b2c3d").status_code == 401
    r = c.get("/api/memories/capture-status?user_id=demo_bar_0a1b2c3d",
              headers={"X-Internal-Token": "nope"})
    assert r.status_code == 403
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    assert c.get("/api/memories/capture-status?user_id=x", headers=HDR).status_code == 403


def test_route_returns_the_snapshot(monkeypatch):
    c = _client(monkeypatch)
    stats.started("demo_bar_0a1b2c3d")
    r = c.get("/api/memories/capture-status?user_id=demo_bar_0a1b2c3d", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "demo_bar_0a1b2c3d" and body["started"] == 1
    assert body["completed"] == 0 and body["in_flight"] == 1
    assert c.get("/api/memories/capture-status", headers=HDR).status_code == 422  # user_id required


def test_persist_hook_counts_success_and_failure(monkeypatch):
    import routers.chat as chat

    async def ok_impl(user_id, session_id, m, r):
        assert stats.snapshot(user_id)["in_flight"] == 1  # started BEFORE the body runs
        return True  # the impl's contract: True = every pass clean; anything else = failed

    async def boom_impl(user_id, session_id, m, r):
        raise RuntimeError("digest exploded")
    monkeypatch.setattr(chat, "_persist_memory_candidates_impl", ok_impl)
    asyncio.run(chat._persist_memory_candidates("demo_bar_0a1b2c3d", "s", "hi", "yo"))
    s = stats.snapshot("demo_bar_0a1b2c3d")
    assert (s["started"], s["completed"], s["failed"], s["in_flight"]) == (1, 1, 0, 0)
    monkeypatch.setattr(chat, "_persist_memory_candidates_impl", boom_impl)
    with pytest.raises(RuntimeError):
        asyncio.run(chat._persist_memory_candidates("demo_bar_0a1b2c3d", "s", "hi", "yo"))
    s = stats.snapshot("demo_bar_0a1b2c3d")
    assert (s["started"], s["completed"], s["failed"], s["in_flight"]) == (2, 2, 1, 0)


def _stub_passes(monkeypatch, *, extract=None, digest=None, missing=()):
    """Stand in for the memory passes via sys.modules (the impl imports them at
    call time) — no chroma/LLM imports in a unit test."""
    import re
    import sys
    import types

    async def clean(*a, **k):
        return None

    async def boom(*a, **k):
        raise RuntimeError("extractor exploded")
    mods = {
        "memory_extractor": types.SimpleNamespace(extract_and_ingest=extract or clean),
        "memory_digest": types.SimpleNamespace(run_turn_digest=digest or clean),
        "person_extractor": types.SimpleNamespace(process_text=clean),
        "person_extractor_llm": types.SimpleNamespace(process_text_llm=clean),
        "latent_intent_detector": types.SimpleNamespace(detect_and_store=clean),
        "memory_tombstones": types.SimpleNamespace(clear_matching=lambda *a: None,
                                                  is_explicit_teach=lambda m: False),
        "intent_router": types.SimpleNamespace(_FORGET_ENTITY_RE=re.compile(r"^forget (.+)$"),
                                               _FORGET_LAST_RE=re.compile(r"^forget that$")),
    }
    for name, mod in mods.items():
        if name in missing:
            monkeypatch.delitem(sys.modules, name, raising=False)
            monkeypatch.setitem(sys.modules, name, None)  # import raises ImportError
        else:
            monkeypatch.setitem(sys.modules, name, mod)
    return boom


def test_impl_reports_a_failed_memory_pass_and_the_wrapper_counts_it(monkeypatch):
    import routers.chat as chat
    boom = _stub_passes(monkeypatch)
    _stub_passes(monkeypatch, extract=boom)
    assert asyncio.run(chat._persist_memory_candidates_impl("demo_bar_0a1b2c3d", "s", "hi", "yo")) is False
    asyncio.run(chat._persist_memory_candidates("demo_bar_0a1b2c3d", "s", "hi", "yo"))
    s = stats.snapshot("demo_bar_0a1b2c3d")
    assert (s["started"], s["completed"], s["failed"], s["in_flight"]) == (1, 1, 1, 0)


def test_impl_reports_clean_capture_as_success(monkeypatch):
    import routers.chat as chat
    _stub_passes(monkeypatch)
    assert asyncio.run(chat._persist_memory_candidates_impl("demo_bar_0a1b2c3d", "s", "hi", "yo")) is True
    asyncio.run(chat._persist_memory_candidates("demo_bar_0a1b2c3d", "s", "hi", "yo"))
    s = stats.snapshot("demo_bar_0a1b2c3d")
    assert (s["completed"], s["failed"]) == (1, 0)


def test_impl_nothing_to_capture_is_not_a_failure(monkeypatch):
    import routers.chat as chat
    _stub_passes(monkeypatch)
    assert asyncio.run(chat._persist_memory_candidates_impl("guest", "s", "hi", "yo")) is True
    assert asyncio.run(chat._persist_memory_candidates_impl("demo_bar_0a1b2c3d", "s", "forget that", "ok")) is True


def test_impl_outer_failure_is_reported(monkeypatch):
    import routers.chat as chat
    _stub_passes(monkeypatch, missing=("memory_extractor",))  # the import itself fails
    assert asyncio.run(chat._persist_memory_candidates_impl("demo_bar_0a1b2c3d", "s", "hi", "yo")) is False


def test_guest_turn_is_still_counted_as_completed(monkeypatch):
    """The early 'guest' return lives INSIDE the counted body, so a waiter on a
    guest id never hangs on a started-but-never-completed counter."""
    import routers.chat as chat
    asyncio.run(chat._persist_memory_candidates("guest", "s", "hi", "yo"))
    assert stats.snapshot("guest")["in_flight"] == 0

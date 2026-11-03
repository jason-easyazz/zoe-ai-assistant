"""End to end, ``ZOE_STRUGGLE_DETECT=shadow``: invented turns driven through the REAL routers land as struggle events, and
the admin report returns the cluster.

* VOICE: ``POST /api/voice/command`` through FastAPI's TestClient - the real ``voice_command``, the real
  ``semantic_router.route`` (its embedding model replaced by a two-domain toy matrix), the real Skybridge exit and its real
  ``_run_voice_memory_passes`` post-turn task, which carries the hook. Then ``GET /api/system/struggles`` on the same app.
* CHAT: the real ``chat_stream_generator`` (local-brain branch, brain faked but announcing its lane through the real
  ``brain_dispatch._log_lane``) and the real ``_persist_memory_candidates`` post-turn task; then the report endpoint.

Faked: STT/TTS/push/the Skybridge resolver's DB read, the extractors behind the hook, the chat DB. Phrasings invented.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
import types

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

from fastapi import FastAPI
from fastapi.testclient import TestClient

import struggle_detector as sd

MEMBER = "member-e2e"
ADMIN = {"user_id": MEMBER, "role": "admin", "username": MEMBER}


@pytest.fixture(autouse=True)
def shadow(monkeypatch):
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "shadow")
    sd.reset()
    be = sd.MemoryBackend()
    sd.set_backend(be)
    yield be
    sd.reset()
    sd.set_backend(None)


def _toy_router(monkeypatch):
    """The REAL ``semantic_router.route`` over a two-domain toy space: weather-ish words land on weather at 0.9."""
    import semantic_router as sr

    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(sr, "ROUTES", {"weather": ["w"], "chat": ["c"]})
    monkeypatch.setattr(sr, "_MATRIX", np.eye(2, dtype=np.float32))
    monkeypatch.setattr(sr, "_DOM_IDX", {"weather": np.array([0]), "chat": np.array([1])})

    def embed(text):
        w = any(k in text.lower() for k in ("outside", "forecast", "weather", "warm"))
        return np.array([0.9, 0.2] if w else [0.2, 0.9], dtype=np.float32)

    monkeypatch.setattr(sr, "embed", embed)


def _voice_app(monkeypatch):
    import guest_policy  # noqa: F401 - imported for real before the stub below shadows it in sys.modules
    import hold_the_fact
    import skybridge_service
    import routers.voice_tts as vt
    from database import get_db
    from routers.struggles import router as struggles_router
    from auth import require_admin

    _toy_router(monkeypatch)

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None, **_kw):
        return {"handled": True, "spoken_summary": "It's 17 degrees and sunny.",
                "intent": {"domain": "weather", "action": "current"}, "skybridge_context": {"domain": "weather"},
                "cards": []}

    async def nothing(*_a, **_k):
        return None

    class Broadcaster:
        async def broadcast(self, *_a, **_k):
            return 1

    class Audio:
        body = b"RIFF-test"
        media_type = "audio/wav"

    async def synthesize(payload, caller=None):
        return Audio()

    monkeypatch.setitem(sys.modules, "skybridge_service",
                        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request))
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "guest_policy",
                        types.SimpleNamespace(record_policy_decision=lambda *_a, **_k: None))
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(vt, "_broadcast_skybridge_ui", nothing)
    monkeypatch.setattr(vt, "synthesize", synthesize)
    monkeypatch.setattr(vt, "_resolve_recent_panel_session_user", nothing)
    monkeypatch.setattr(vt, "_schedule_voice_chat_save", nothing)
    monkeypatch.setattr(vt, "_VOICE_SESSIONS", {})
    # the extractors BEHIND the hook are not under test: stop the memory passes right after the struggle hook
    monkeypatch.setattr(hold_the_fact, "is_own_reply", lambda _r: True)

    app = FastAPI()
    app.include_router(vt.router)
    app.include_router(struggles_router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {"source": "device", "panel_id": "hall-panel",
                                                                 "user_id": MEMBER}
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[require_admin] = lambda: ADMIN
    return app


def _poll(client, want_total, scope="all", timeout=10.0):
    deadline = time.time() + timeout
    rep = {}
    while time.time() < deadline:
        r = client.get(f"/api/system/struggles?scope={scope}&days=1")
        assert r.status_code == 200, r.text
        rep = r.json()
        if rep["total"] >= want_total:
            return rep
        time.sleep(0.05)
    return rep


def test_voice_turns_through_the_real_router_land_and_the_report_clusters_them(monkeypatch, shadow):
    app = _voice_app(monkeypatch)
    with TestClient(app) as client:
        for text in ("what's it going to be like outside this afternoon",
                     "what's it going to be like outside this afternoon",
                     "no, I meant the forecast for later"):
            r = client.post("/api/voice/command", json={"text": text, "panel_id": "hall-panel"})
            assert r.status_code == 200 and r.json()["reply"] == "It's 17 degrees and sunny.", r.text
        rep = _poll(client, 2)
    signals = {c["signal"]: c for c in rep["clusters"]}
    assert set(signals) == {"RE_ASK", "CORRECTION"}, rep
    assert signals["RE_ASK"]["domain"] == "weather" and signals["RE_ASK"]["pattern"] == "weather|what:future:afternoon"
    assert rep["stats"]["mode"] == "shadow" and rep["stats"]["hook_errors"] == 0
    rows = shadow.data
    assert {r["channel"] for r in rows} == {"voice"} and {r["panel_id"] for r in rows} == {"hall-panel"}


def test_report_is_admin_persons_only(monkeypatch):
    from auth import get_current_user
    from routers.struggles import router as struggles_router

    app = FastAPI()
    app.include_router(struggles_router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "member-x", "role": "user", "username": "member-x"}
    with TestClient(app) as client:
        assert client.get("/api/system/struggles").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: {"user_id": MEMBER, "role": "admin", "username": "panel:hall",
                                                          "device_token_role": "admin"}
    with TestClient(app) as client:
        assert client.get("/api/system/struggles").status_code == 403       # a panel token is not a person
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    with TestClient(app) as client:
        r = client.get("/api/system/struggles?scope=me")
        assert r.status_code == 200 and r.json()["total"] == 0
        assert client.get("/api/system/struggles?scope=everyone").status_code == 422


async def test_chat_turns_through_the_real_generator_land_and_the_report_clusters_them(monkeypatch, shadow):
    from routers import chat as chat_router
    from routers.struggles import struggles_report
    import brain_dispatch

    async def anoop(*_a, **_k):
        return None

    async def empty(*_a, **_k):
        return ""

    for name in ("_ensure_user_and_chat_session", "_record_run_state", "_persist_ag_ui_run"):
        monkeypatch.setattr(chat_router, name, anoop)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *a, **k: None)
    monkeypatch.setattr(chat_router, "_INTENT_FAST_PATH_ENABLED", False)
    monkeypatch.setattr(chat_router, "_WHATSAPP_FLOW_ENABLED", False)
    monkeypatch.setattr(chat_router, "classify_query", lambda *a, **k: "chat")
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", True)
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", empty)

    async def impl(*_a, **_k):        # the memory extractors behind the hook
        return True

    monkeypatch.setattr(chat_router, "_persist_memory_candidates_impl", impl)

    class _Rows:
        async def fetchall(self):
            return []

    class _Db:
        async def execute(self, *a, **k):
            return _Rows()

    async def fake_get_db():
        yield _Db()

    monkeypatch.setattr(chat_router, "get_db", fake_get_db)

    saved = []

    async def save(*a, **k):
        saved.append((a, k))
        return True

    monkeypatch.setattr(chat_router, "_save_chat_message", save)

    async def fake_brain_streaming(*_a, **_k):
        brain_dispatch._log_lane(attempted="flue", served="flue", outcome="ok", session_id="chat-e2e")
        yield "The tallest waterfall is "
        yield "in Venezuela."

    monkeypatch.setattr(chat_router, "_brain_streaming", fake_brain_streaming)

    user = {"user_id": MEMBER, "role": "admin", "username": MEMBER}
    for n, text in enumerate(("which waterfall is the tallest in the world", "which waterfall is the tallest in the world",
                              "that's not right"), start=1):
        page_ids = set()
        async for chunk in chat_router.chat_stream_generator(text, "chat-e2e", user):
            page_ids.update(re.findall(r'"messageId":\s*"(\w+)"', str(chunk)))
        for _ in range(100):          # the post-turn task is ensure_future'd: let it run, as it does after a real reply
            if sd.stats()["observed"] >= n:
                break
            await asyncio.sleep(0.01)
        assert sd.stats()["observed"] == n, "the chat post-turn hook never ran"
        # ONE id end to end: the id the chat page receives (and later rates) is the id the reply is saved under
        assistant = [k for a, k in saved if a[1] == "assistant"]
        assert len(page_ids) == 1 and assistant[-1].get("message_id") in page_ids, (page_ids, assistant)
    await sd.drain()
    rep = await struggles_report(days=1.0, scope="me", top=20, user=ADMIN)
    signals = {c["signal"]: c for c in rep["clusters"]}
    assert set(signals) == {"RE_ASK", "NEGATIVE_FEEDBACK"}, rep
    assert signals["RE_ASK"]["examples"] == ["which waterfall is the tallest in the world -> which waterfall is the tallest "
                                             "in the world"]
    assert signals["RE_ASK"]["paths"] == {"brain:flue": 1}
    assert {r["channel"] for r in shadow.data} == {"chat"} and rep["stats"]["processed"] == 3

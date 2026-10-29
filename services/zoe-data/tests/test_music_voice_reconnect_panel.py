"""A voice "connect/reconnect <music service>" turn must put the QR ON the panel.

The Skybridge card (`_qr_card`) is never rendered on a voice turn - the panel
executor shows only the summary text - so the spoken "scan the code on the panel"
was a promise nothing kept. The voice broadcast now sends the panel to
Music -> Sources with ``connect=<provider>``, where home.html opens the shared
authCard; and when the navigation was not delivered the reply says where to look
instead of promising a code.
"""
import sys
import types
from pathlib import Path

import pytest

import routers.voice_tts as voice_tts

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[3]

_QR_RESULT = {
    "handled": True,
    "spoken_summary": "YouTube Music needs signing in again - scan the code on the panel with your phone.",
    "intent": {"domain": "music", "action": "setup"},
    "cards": [{"card_type": "music_setup", "card_id": "music-qr-ytmusic",
               "content": {"mode": "qr", "title": "Connect YouTube Music", "provider": "ytmusic",
                           "qr_path": "/x"}}],
}
_CATALOGUE_RESULT = {
    "handled": True, "spoken_summary": "What would you like to add?",
    "intent": {"domain": "music", "action": "setup"},
    "cards": [{"card_type": "music_setup", "card_id": "music-add", "content": {"mode": "catalogue"}}],
}


class _Cur:
    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


class _Db:
    async def execute(self, *a, **k):
        return _Cur()

    async def commit(self):
        pass


def _wire(monkeypatch, delivered):
    navs = []

    async def get_db():
        yield _Db()

    async def enqueue(_db, **kw):
        if kw["action_type"] == "panel_navigate":
            navs.append(kw["payload"])
        return {"action_id": "a-" + kw["action_type"], "status": "queued", "panel_id": kw["panel_id"],
                "action_type": kw["action_type"], "payload": kw["payload"]}

    class B:
        async def broadcast(self, *a, **k):
            return 1 if delivered else 0

    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(get_db=get_db))
    monkeypatch.setitem(sys.modules, "ui_orchestrator", types.SimpleNamespace(enqueue_ui_action=enqueue))
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=B()))
    return navs


@pytest.mark.asyncio
async def test_reconnect_turn_navigates_panel_to_sources_with_connect(monkeypatch):
    navs = _wire(monkeypatch, delivered=True)
    delivered = await voice_tts._broadcast_skybridge_ui("panel-1", _QR_RESULT, utterance="connect youtube music", turn_key="t1")
    assert delivered is True
    url = navs[0]["url"]
    assert "domain=music_sources" in url and "connect=ytmusic" in url


@pytest.mark.asyncio
async def test_undelivered_navigation_is_reported(monkeypatch):
    _wire(monkeypatch, delivered=False)
    assert await voice_tts._broadcast_skybridge_ui("panel-1", _QR_RESULT, utterance="x", turn_key="t2") is False


@pytest.mark.asyncio
async def test_catalogue_card_does_not_trigger_a_connect(monkeypatch):
    navs = _wire(monkeypatch, delivered=True)
    await voice_tts._broadcast_skybridge_ui("panel-1", _CATALOGUE_RESULT, utterance="add music", turn_key="t3")
    assert "connect=" not in navs[0]["url"] and "music_sources" not in navs[0]["url"]
    assert voice_tts._music_connect_target(_CATALOGUE_RESULT) is None


def test_home_html_opens_the_shared_auth_card_for_connect():
    src = (ROOT / "services/zoe-ui/dist/touch/home.html").read_text()
    assert "sp.get('connect')" in src
    assert "startProviderConnect(_cn" in src
    # reuse of the one authCard flow, no new renderer
    assert "authCard({kind:'music'" in src

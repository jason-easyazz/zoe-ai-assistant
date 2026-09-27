"""The "set up music" reply points at Zoe's own Sources card, never at Music
Assistant (auth audit 2026-09-27, finding 7).

It used to link ``MUSIC_ASSISTANT_URL`` (default ``http://localhost:8095``) —
breaking "the user never sees MA", and useless from a phone — and advertised
Apple Music, which Zoe does not offer. Now it names the panel's Music → Browse →
Sources card (Connect = QR finished on the phone), lists only catalogue
services, and a panel-originated chat turn navigates straight there.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

from pathlib import Path

import intent_router
import music_service

ROOT = Path(__file__).resolve().parents[3]

_CATALOGUE = [
    {"domain": "spotify", "name": "Spotify", "auth": "oauth", "connected": True, "needs_attention": False},
    {"domain": "ytmusic", "name": "YouTube Music", "auth": "browser", "connected": True, "needs_attention": True},
    {"domain": "tidal", "name": "Tidal", "auth": "oauth", "connected": False, "needs_attention": False},
]


def _assert_no_ma_leak(reply: str) -> None:
    assert "8095" not in reply
    assert "localhost" not in reply and "http" not in reply
    assert "Music Assistant" not in reply
    assert "Apple Music" not in reply


async def test_reply_points_at_the_sources_card(monkeypatch):
    async def cat():
        return list(_CATALOGUE)

    monkeypatch.setattr(music_service, "provider_catalogue", cat)
    monkeypatch.setenv("MUSIC_ASSISTANT_URL", "http://localhost:8095")
    reply = await intent_router._execute_music_setup("jason")
    _assert_no_ma_leak(reply)
    assert "Browse → Sources" in reply and "Connect" in reply and "phone" in reply
    assert "**Spotify**" in reply                       # connected
    assert "YouTube Music** needs reconnecting" in reply  # configured but unloaded
    assert "Available to add: Tidal." in reply           # only what the catalogue offers


async def test_reply_survives_an_unreachable_catalogue(monkeypatch):
    async def boom():
        raise RuntimeError("MA down")

    monkeypatch.setattr(music_service, "provider_catalogue", boom)
    reply = await intent_router._execute_music_setup("jason")
    _assert_no_ma_leak(reply)
    assert "Browse → Sources" in reply


def test_panel_chat_navigates_to_the_sources_card():
    chat_src = (ROOT / "services/zoe-data/routers/chat.py").read_text()
    assert '"music_setup":       ("/touch/home.html?domain=music_sources", None)' in chat_src
    home = (ROOT / "services/zoe-ui/dist/touch/home.html").read_text()
    assert "dom==='music_sources'" in home and "_qtabNext='sources'" in home

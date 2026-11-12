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


# ── a sleeping music engine is not "nothing connected" (2026-11-12) ───────────
# The idle reaper stops Music Assistant for most of the day, and the catalogue
# read `config/providers` without waking it, so the Sources tab showed every
# provider as plain "Connect" and voice said "No music services are connected
# yet." Negative control: drop the ensure_running() call (or the None check)
# from provider_catalogue and the wake-order / unavailable tests go red.


@pytest.fixture
def ma_reads(monkeypatch):
    """provider_catalogue's two MA reads + the wake, faked and ordered (no network, no docker)."""
    state = {"up": True, "order": [],
             "config/providers": [{"domain": "ytmusic", "enabled": True, "last_error": "Premium check failed"}],
             "providers": [{"domain": "radiobrowser", "available": True}]}

    async def ensure_running():
        state["order"].append("wake")
        return state["up"]

    async def fake_ma(command, **_args):
        state["order"].append(command)
        return state[command]

    monkeypatch.setattr(music_service, "ensure_running", ensure_running)
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    return state


async def test_catalogue_wakes_the_engine_before_reading(ma_reads):
    cat = await music_service.provider_catalogue()
    assert ma_reads["order"] == ["wake", "config/providers", "providers"]
    yt = next(p for p in cat if p["domain"] == "ytmusic")
    assert yt["connected"] and yt["needs_attention"]  # the amber Reconnect, not plain Connect


async def test_catalogue_refuses_when_the_engine_cannot_wake(ma_reads):
    ma_reads["up"] = False
    with pytest.raises(music_service.MusicEngineUnavailable):
        await music_service.provider_catalogue()
    assert ma_reads["order"] == ["wake"]  # nothing read from a stopped engine


async def test_catalogue_refuses_when_the_engine_does_not_answer(ma_reads):
    ma_reads["config/providers"] = None  # _ma's "unreachable / non-200"
    with pytest.raises(music_service.MusicEngineUnavailable):
        await music_service.provider_catalogue()


async def test_voice_setup_says_the_engine_is_unavailable_not_nothing_connected(ma_reads):
    ma_reads["up"] = False
    reply = await intent_router._execute_music_setup("member-a")
    assert music_service.MUSIC_ENGINE_DOWN_SETUP_MSG in reply
    assert "No music services are connected" not in reply
    assert "Browse → Sources" in reply


async def test_setup_resolver_says_the_engine_is_unavailable(ma_reads):
    ma_reads["up"] = False
    r = await music_service.resolve_music_setup("spotify")
    assert r["spoken_summary"] == music_service.MUSIC_ENGINE_DOWN_SETUP_MSG


def test_every_catalogue_caller_is_a_user_action_not_a_poll():
    """provider_catalogue wakes MA, so a poll calling it would undo every idle reap.
    Pinned callers: the Sources tab GET, voice "set up music", the setup resolver."""
    import re

    callers = sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "services/zoe-data").rglob("*.py")
        if "/tests/" not in str(p) and re.search(r"\bprovider_catalogue\(\)", p.read_text(errors="ignore"))
    )
    assert callers == ["services/zoe-data/intent_router.py", "services/zoe-data/music_service.py",
                       "services/zoe-data/routers/music_setup.py"], callers
    home = (ROOT / "services/zoe-ui/dist/touch/home.html").read_text()
    assert home.count("/api/music/setup/catalogue") == 1  # fetched only when the Sources tab is shown


# ── a refused caller never wakes the engine (cross-review 2026-11-12) ────────
# provider_catalogue now wakes MA, so "set up music" takes the same turn gate as
# music_play. Negative control: drop the home_control_refused check from either
# entry point and ma_reads["order"] is no longer empty.


@pytest.fixture
def refused_turn(monkeypatch):
    import panel_principal

    monkeypatch.delenv("ZOE_HA_CONTROL_SCOPE", raising=False)
    token = panel_principal._home_control_turn.set(False)
    yield panel_principal.HOME_CONTROL_REFUSAL
    panel_principal._home_control_turn.reset(token)


async def test_voice_setup_for_a_refused_caller_does_not_wake_the_engine(ma_reads, refused_turn):
    reply = await intent_router._execute_music_setup("guest")
    assert reply == refused_turn
    assert ma_reads["order"] == []  # no wake, no read


async def test_setup_resolver_for_a_refused_caller_does_not_wake_the_engine(ma_reads, refused_turn):
    r = await music_service.resolve_music_setup("spotify")
    assert r["spoken_summary"] == refused_turn
    assert ma_reads["order"] == []

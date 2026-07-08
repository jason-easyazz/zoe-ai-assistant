"""music_service — Zoe's bridge to the local Music Assistant engine.

Doctrine (see project_zoe_over_ha_ma_doctrine): Music Assistant is an invisible
organ. Zoe never exposes MA directly; this module is the ONLY place that speaks
MA's command protocol, and it hands the rest of Zoe normalized shapes + a
Skybridge card. Local-first: works with the builtin/DLNA/AirPlay/Chromecast
players MA ships with — no accounts required.

Every call is best-effort and never raises to the caller: a dead MA engine
degrades to a friendly "music isn't set up yet" card, never a broken turn.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5.0


def _ma_url() -> str:
    return os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")


def _ma_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _ma(command: str, **args: Any) -> Any:
    """POST one MA command. Returns the parsed result or None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            # MA's JSON-RPC shim requires args NESTED under "args" — flat args are
            # silently dropped, causing "<x> is required" 500s. Commands with no
            # args work either way, which hid this until real playback was tried.
            payload = {"command": command}
            if args:
                payload["args"] = args
            r = await c.post(f"{_ma_url()}/api", json=payload, headers=_ma_headers())
            if r.status_code != 200:
                logger.debug("MA %s -> HTTP %s", command, r.status_code)
                return None
            return r.json()
    except Exception as exc:  # noqa: BLE001 — MA is optional; never break Zoe
        logger.debug("MA %s unreachable: %s", command, exc)
        return None


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items") or data.get("result") or []
    return []


# ── Read: players + now-playing ───────────────────────────────────────────────

async def get_players() -> list[dict[str, Any]]:
    return _as_list(await _ma("players/all"))


def _pick_player(players: list[dict[str, Any]], player_id: str = "") -> Optional[dict[str, Any]]:
    """Choose the target player: the named one, else a playing/paused one, else
    the first available powered player (a household panel usually has one)."""
    if not players:
        return None
    if player_id:
        for p in players:
            if p.get("player_id") == player_id:
                return p
    for state in ("playing", "paused"):
        for p in players:
            if str(p.get("playback_state") or p.get("state")) == state:
                return p
    for p in players:
        if p.get("available") and p.get("powered"):
            return p
    return players[0]


async def now_playing(player_id: str = "") -> Optional[dict[str, Any]]:
    """Normalized now-playing snapshot for the active/target player, or None if
    MA is unreachable/has no players. Never raises."""
    players = await get_players()
    player = _pick_player(players, player_id)
    if player is None:
        return None
    pid = player.get("player_id", "")
    state = str(player.get("playback_state") or player.get("state") or "idle").lower()
    # The queue carries the current track; queue_id == player_id for a solo player.
    queues = _as_list(await _ma("player_queues/all"))
    queue = next((q for q in queues if q.get("queue_id") == pid), None)
    cur = (queue or {}).get("current_item") or {}
    media = cur.get("media_item") or cur
    image = ""
    for src in (media.get("image"), (media.get("metadata") or {}).get("images")):
        if isinstance(src, dict):
            image = src.get("path") or src.get("url") or ""
        elif isinstance(src, list) and src:
            first = src[0]
            image = (first.get("path") or first.get("url")) if isinstance(first, dict) else ""
        if image:
            break
    artists = media.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict)) if artists else (media.get("artist") or "")
    return {
        "player_id": pid,
        "player_name": player.get("display_name") or player.get("name") or "Speaker",
        "state": state,  # playing | paused | idle
        "title": media.get("name") or "",
        "artist": artist,
        "album": (media.get("album") or {}).get("name", "") if isinstance(media.get("album"), dict) else (media.get("album") or ""),
        "image": image if isinstance(image, str) and image.startswith(("http://", "https://", "/")) else "",
        "volume": player.get("volume_level"),
        "queue_id": pid,
        "shuffle": bool((queue or {}).get("shuffle_enabled")),
    }


# ── Write: transport + volume + play ─────────────────────────────────────────

# Zoe action -> MA command. Transport lives on the queue; volume on the player.
_QUEUE_CMDS = {
    "play": "player_queues/play",
    "resume": "player_queues/play",
    "pause": "player_queues/pause",
    "play_pause": "player_queues/play_pause",
    "stop": "player_queues/stop",
    "next": "player_queues/next",
    "previous": "player_queues/previous",
}
_PLAYER_CMDS = {
    "volume_up": "players/cmd/volume_up",
    "volume_down": "players/cmd/volume_down",
    "mute": "players/cmd/volume_mute",
}


async def control(action: str, player_id: str = "", value: Any = None) -> bool:
    """Run a transport/volume action on the target player. Returns True on a
    dispatched command (best-effort — MA is fire-and-forget for transport)."""
    players = await get_players()
    player = _pick_player(players, player_id)
    if player is None:
        return False
    pid = player.get("player_id", "")
    if action in _QUEUE_CMDS:
        await _ma(_QUEUE_CMDS[action], queue_id=pid)
        return True
    if action == "volume_set" and value is not None:
        try:
            vol = int(value)
        except (TypeError, ValueError):
            return False  # never raise — a bad volume just no-ops
        await _ma("players/cmd/volume_set", player_id=pid, volume_level=vol)
        return True
    if action in _PLAYER_CMDS:
        await _ma(_PLAYER_CMDS[action], player_id=pid)
        return True
    return False


async def search_and_play(query: str, player_id: str = "") -> Optional[dict[str, Any]]:
    """Search MA and play the top hit on the target player. Returns the matched
    item {name, media_type} or None. Local-first: searches all configured
    providers (builtin/radio/local files work with no account)."""
    players = await get_players()
    player = _pick_player(players, player_id)
    if player is None:
        return None
    pid = player.get("player_id", "")
    res = await _ma("music/search", search_query=query,
                    media_types=["artist", "album", "track", "playlist", "radio"], limit=5)
    # MA returns a dict keyed by media type; pick the first non-empty hit.
    hit = None
    if isinstance(res, dict):
        for key in ("tracks", "albums", "artists", "playlists", "radio"):
            items = res.get(key) or []
            if items:
                hit = items[0]
                break
    if not hit:
        return None
    uri = hit.get("uri")
    if not uri:
        return None
    await _ma("player_queues/play_media", queue_id=pid, media=uri, option="replace", radio_mode=False)
    return {"name": hit.get("name", query), "artist": (hit.get("artists") or [{}])[0].get("name", "") if hit.get("artists") else ""}


# ── Skybridge card + resolver ────────────────────────────────────────────────

def now_playing_card(np: dict[str, Any]) -> dict[str, Any]:
    """A Skybridge now-playing card. The renderer builds the SVG transport row
    from `state` (its buttons re-enter the resolver via data-sky-action=query,
    so tap and voice share one path) — transport is UI, not server data."""
    return {
        "card_type": "now_playing",
        "schema_version": "1.0.0",
        "card_id": "music-now-playing",
        "content": {
            "source": "music_now_playing",
            "title": np.get("title") or "Nothing playing",
            "artist": np.get("artist") or "",
            "album": np.get("album") or "",
            "image": np.get("image") or "",
            "player_name": np.get("player_name") or "",
            "state": np.get("state") or "idle",
            "volume": np.get("volume"),
            "transport": True,
        },
    }


def _browse_card() -> dict[str, Any]:
    """When nothing is playing / MA has no players: an honest 'pick something'
    card rather than a fake now-playing."""
    return {
        "card_type": "now_playing",
        "schema_version": "1.0.0",
        "card_id": "music-browse",
        "content": {
            "source": "music_now_playing",
            "title": "Ready to play",
            "artist": "Ask me to play something — an artist, a song, or a radio station.",
            "state": "idle",
            "actions": [
                {"label": "Jazz", "query": "play some jazz"},
                {"label": "The news", "query": "play the news"},
                {"label": "Relaxing", "query": "play relaxing music"},
            ],
        },
    }


def _result(spoken: str, card: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "handled": True,
        "intent": {"domain": "music", "action": action},
        "spoken_summary": spoken,
        "cards": [card],
        "actions": [],
    }


async def resolve_music(intent: Any) -> dict[str, Any]:
    """The Skybridge music domain resolver. `intent` has .action and .query."""
    action = getattr(intent, "action", "status")
    query = (getattr(intent, "query", "") or "").strip()

    if action == "play" and query:
        hit = await search_and_play(query)
        if hit:
            np = await now_playing() or {}
            spoken = f"Playing {hit['name']}." if hit.get("name") else "Playing that now."
            return _result(spoken, now_playing_card(np or {"state": "playing", "title": hit["name"], "artist": hit.get("artist", "")}), "play")
        return _result("I couldn't find that to play.", _browse_card(), "play")

    if action in ("pause", "resume", "play_pause", "next", "previous", "stop", "volume_up", "volume_down"):
        np0 = await now_playing()
        if np0 is None:
            return _result("There's no music playing right now.", _browse_card(), action)
        await control(action)
        np = await now_playing() or np0
        verb = {"pause": "Paused", "resume": "Resumed", "play_pause": "Okay",
                "next": "Skipping ahead", "previous": "Going back", "stop": "Stopped",
                "volume_up": "Turning it up", "volume_down": "Turning it down"}.get(action, "Okay")
        return _result(f"{verb}.", now_playing_card(np), action)

    # status / "what's playing" / bare "music"
    np = await now_playing()
    if np is None:
        return _result("Music isn't set up yet — ask me to play something to get started.", _browse_card(), "status")
    if np.get("state") == "playing" and np.get("title"):
        return _result(f"Playing {np['title']}" + (f" by {np['artist']}" if np.get("artist") else "") + ".", now_playing_card(np), "status")
    return _result("Nothing's playing. Ask me to put something on.", now_playing_card(np) if np.get("title") else _browse_card(), "status")

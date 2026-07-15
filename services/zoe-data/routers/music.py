"""
Music Assistant proxy endpoints.

Provides Zoe's frontend with MA status, configured providers, and available
provider types — all forwarded from the local MA instance (default :8095).
Auth is intentionally session-free here: the data is non-sensitive connection
status that the music page and settings page both need without an extra auth hop.
"""
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music", tags=["music"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ma_url() -> str:
    return os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")


def _ma_headers() -> dict:
    h: dict[str, str] = {"Content-Type": "application/json"}
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get_info() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{_ma_url()}/info", headers=_ma_headers())
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        logger.debug("MA /info unreachable: %s", exc)
    return None


async def _get_providers() -> list | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                f"{_ma_url()}/api",
                json={"command": "providers"},
                headers=_ma_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else (data.get("items") or [])
    except Exception as exc:
        logger.debug("MA providers unreachable: %s", exc)
    return None


async def _get_players() -> list | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                f"{_ma_url()}/api",
                json={"command": "players/all"},
                headers=_ma_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else (data.get("items") or [])
    except Exception as exc:
        logger.debug("MA players/all unreachable: %s", exc)
    return None


async def _get_queue_items(queue_id: str, limit: int = 50) -> list | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                f"{_ma_url()}/api",
                json={"command": "player_queues/items", "args": {"queue_id": queue_id, "limit": limit}},
                headers=_ma_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else (data.get("items") or [])
    except Exception as exc:
        logger.debug("MA player_queues/items unreachable: %s", exc)
    return None


# Hardcoded catalogue of provider types MA supports — shown as "available to connect"
# even before the user has configured anything.
_KNOWN_PROVIDERS = [
    {"id": "spotify",       "name": "Spotify",       "icon": "🟢"},
    {"id": "ytmusic",       "name": "YouTube Music", "icon": "🔴"},
    {"id": "apple_music",   "name": "Apple Music",   "icon": "🎵"},
    {"id": "deezer",        "name": "Deezer",        "icon": "🎧"},
    {"id": "tidal",         "name": "Tidal",         "icon": "🌊"},
    {"id": "plex",          "name": "Plex",          "icon": "🎬"},
    {"id": "subsonic",      "name": "Subsonic / Navidrome", "icon": "💽"},
    {"id": "radiobrowser",  "name": "Radio Browser (built-in)", "icon": "📻"},
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def music_status() -> dict[str, Any]:
    """
    Returns MA availability, version, player count, and configured providers.
    Used by music.html and settings.html to show connection state at a glance.

    provider_count only counts music/plugin providers (not metadata or player-management
    providers) so the music page setup wizard only appears when there are genuinely no
    streaming or library sources.
    """
    info = await _get_info()
    if info is None:
        return {"available": False}

    providers = await _get_providers() or []
    # Count only music-contributing providers (type "music" or "plugin"), not pure metadata
    music_providers = [p for p in providers if p.get("type") in ("music", "plugin")]

    return {
        "available": True,
        "version": info.get("version", ""),
        "onboard_done": info.get("onboard_done", True),
        "providers": providers,
        "provider_count": len(music_providers),
    }


@router.get("/providers")
async def music_providers() -> dict[str, Any]:
    """
    Returns the list of music providers currently configured in MA.
    """
    providers = await _get_providers()
    if providers is None:
        return {"available": False, "providers": []}
    return {"available": True, "providers": providers}


@router.get("/players")
async def music_players() -> dict[str, Any]:
    """
    Returns the list of players currently registered in MA.
    Proxied through Zoe so the browser never needs direct access to MA.
    """
    players = await _get_players()
    if players is None:
        return {"available": False, "players": []}
    return {"available": True, "players": players}


@router.get("/queue/{queue_id}")
async def music_queue(queue_id: str) -> dict[str, Any]:
    """
    Returns up to 50 queue items for the given player/queue ID.
    """
    items = await _get_queue_items(queue_id)
    if items is None:
        return {"available": False, "items": []}
    return {"available": True, "items": items}


@router.get("/available-providers")
async def available_providers() -> dict[str, Any]:
    """
    Returns the full catalogue of provider types MA can use, merged with
    currently configured status so the UI can show Connected / Connect buttons.
    """
    configured = await _get_providers() or []
    configured_ids = {(p.get("domain") or p.get("id") or "").lower() for p in configured}

    result = []
    for p in _KNOWN_PROVIDERS:
        connected = p["id"] in configured_ids
        result.append({**p, "connected": connected})

    return {"available": True, "providers": result}


# ── Control (Music Assistant bridge) ─────────────────────────────────────────
# Transport/volume + search-and-play, delegated to music_service (the single
# place that speaks MA). The panel primarily drives these via the Skybridge
# resolver (data-sky-action), but these give a direct API surface too.

@router.get("/now-playing")
async def music_now_playing(player_id: str = "") -> dict[str, Any]:
    import music_service
    np = await music_service.now_playing(player_id)
    return {"available": np is not None, "now_playing": np or {}}


@router.post("/control")
async def music_control(payload: dict) -> dict[str, Any]:
    """action ∈ play|pause|play_pause|resume|stop|next|previous|volume_up|
    volume_down|volume_set (value); optional player_id."""
    import music_service
    action = str((payload or {}).get("action") or "").strip()
    player_id = str((payload or {}).get("player_id") or "")
    value = (payload or {}).get("value")
    ok = await music_service.control(action, player_id=player_id, value=value)
    return {"ok": ok, "action": action}


@router.post("/seek")
async def music_seek(payload: dict) -> dict[str, Any]:
    """Seek the current track to an absolute position. body: {position_seconds,
    player_id?}. Drives the floating bar's scrubber via music_service.seek."""
    import music_service
    player_id = str((payload or {}).get("player_id") or "")
    raw = (payload or {}).get("position_seconds")
    try:
        pos = int(raw)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid position_seconds"}
    ok = await music_service.seek(pos, player_id=player_id)
    return {"ok": ok, "position_seconds": pos}


@router.get("/preferred-player")
async def get_preferred_player() -> dict[str, Any]:
    """The household's remembered default speaker for new plays."""
    import music_service
    return {"player_id": music_service.get_preferred_player_id()}


@router.post("/preferred-player")
async def set_preferred_player(payload: dict) -> dict[str, Any]:
    """Remember a speaker as the default target for future plays.
    body: {player_id}. Set by the panel's speaker picker; voice plays that
    explicitly target a speaker also update it. Household-shared, like the
    rest of the music bridge (control/transfer/play are unauthenticated too).
    Unknown ids are rejected against the live player list."""
    import music_service
    pid = str((payload or {}).get("player_id") or "")
    if pid:
        players = await music_service.get_players()
        if not any(p.get("player_id") == pid for p in players):
            return {"ok": False, "reason": "unknown player_id"}
    music_service.set_preferred_player_id(pid)
    return {"ok": True, "player_id": pid}


@router.post("/transfer")
async def music_transfer(payload: dict) -> dict[str, Any]:
    """Move current playback to another speaker. body: {target_player_id,
    source_player_id?}. source defaults to the active player MA is using now."""
    import music_service
    target = str((payload or {}).get("target_player_id") or "")
    source = str((payload or {}).get("source_player_id") or "")
    if not target:
        return {"ok": False, "reason": "missing target_player_id"}
    ok = await music_service.transfer(target, source_player_id=source)
    return {"ok": ok, "target_player_id": target}


@router.post("/play")
async def music_play(payload: dict) -> dict[str, Any]:
    """Search MA and play the top hit. body: {query, player_id?, radio?}.
    radio:true seeds an endless station of similar tracks from the hit
    (MA's native radio_mode; needs a SIMILAR_TRACKS provider)."""
    import music_service
    query = str((payload or {}).get("query") or "").strip()
    player_id = str((payload or {}).get("player_id") or "")
    radio = bool((payload or {}).get("radio"))
    if not query:
        return {"ok": False, "reason": "empty query"}
    hit = await music_service.search_and_play(query, player_id=player_id, radio_mode=radio)
    return {"ok": hit is not None, "playing": hit}


# ── Browse: structured search + play-by-URI (the touch music page) ────────────

@router.get("/search")
async def music_search(q: str = "", types: str = "", limit: int = 8) -> dict[str, Any]:
    """Search the connected providers, grouped by media type.

    ?q=<query>&types=track,album,artist,playlist,radio&limit=8 . `types` is an
    optional comma list to narrow the buckets; omitted → all. Powers the music
    page's live search so the user can pick a specific result to play by touch."""
    import music_service
    media_types = [t.strip() for t in (types or "").split(",") if t.strip()] or None
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 8
    return await music_service.search(q, media_types=media_types, limit=n)


@router.post("/play_media")
async def music_play_media(payload: dict) -> dict[str, Any]:
    """Play a specific search result on a chosen speaker.

    body: {uri, player_id?, option?}. `uri` comes from /api/music/search;
    `player_id` (optional) targets a speaker from /api/music/players — omitted
    → active/first powered player. `option`: replace (default) | add (end of
    queue) | next (after current) — the jukebox page queues with add."""
    import music_service
    uri = str((payload or {}).get("uri") or "").strip()
    player_id = str((payload or {}).get("player_id") or "")
    radio = bool((payload or {}).get("radio"))
    option = str((payload or {}).get("option") or "replace")
    if not uri:
        return {"ok": False, "reason": "missing uri"}
    return await music_service.play_media(uri, player_id=player_id, option=option, radio_mode=radio)


# ── Discovery: MA-native recommendations + play history ──────────────────────

@router.post("/queue/move")
async def music_queue_move(payload: dict) -> dict[str, Any]:
    """Reorder the queue. body: {queue_id, item_id, to_index}."""
    import music_service
    b = payload or {}
    ok = await music_service.queue_move(str(b.get("queue_id") or ""), str(b.get("item_id") or ""), int(b.get("to_index") or 0))
    return {"ok": ok}


@router.post("/queue/remove")
async def music_queue_remove(payload: dict) -> dict[str, Any]:
    """Remove a track from the queue. body: {queue_id, item_id}."""
    import music_service
    b = payload or {}
    return {"ok": await music_service.queue_remove(str(b.get("queue_id") or ""), str(b.get("item_id") or ""))}


@router.post("/queue/clear")
async def music_queue_clear(payload: dict) -> dict[str, Any]:
    """Clear the queue. body: {queue_id}."""
    import music_service
    return {"ok": await music_service.queue_clear(str((payload or {}).get("queue_id") or ""))}


@router.post("/queue/play-index")
async def music_queue_play_index(payload: dict) -> dict[str, Any]:
    """Jump to a queue position. body: {queue_id, index}."""
    import music_service
    b = payload or {}
    return {"ok": await music_service.queue_play_index(str(b.get("queue_id") or ""), int(b.get("index") or 0))}


@router.post("/queue/save")
async def music_queue_save(payload: dict) -> dict[str, Any]:
    """Save the current queue as a playlist. body: {queue_id, name}."""
    import music_service
    b = payload or {}
    return {"ok": await music_service.queue_save_playlist(str(b.get("queue_id") or ""), str(b.get("name") or ""))}


@router.get("/playlists")
async def music_playlists() -> dict[str, Any]:
    """The user's playlist library."""
    import music_service
    return {"playlists": await music_service.list_playlists()}


@router.get("/playlists/tracks")
async def music_playlist_tracks(uri: str = "", limit: int = 100) -> dict[str, Any]:
    """Tracks in a playlist. ?uri=<playlist uri>."""
    import music_service
    return {"tracks": await music_service.playlist_tracks(uri, limit=limit)}


@router.post("/playlists/add")
async def music_playlist_add(payload: dict) -> dict[str, Any]:
    """Add a track to a playlist. body: {playlist_uri, track_uri}."""
    import music_service
    b = payload or {}
    return {"ok": await music_service.playlist_add(str(b.get("playlist_uri") or ""), str(b.get("track_uri") or ""))}


@router.post("/favorite")
async def music_favorite(payload: dict) -> dict[str, Any]:
    """Favorite / add-to-library a media item. body: {uri}."""
    import music_service
    return {"ok": await music_service.favorite_add(str((payload or {}).get("uri") or ""))}


@router.get("/recommendations")
async def music_recommendations() -> dict[str, Any]:
    """MA's native recommendation shelves ("Listen again", "Mixed for you", …),
    normalized to the same flat item shape as /search results."""
    import music_service
    return await music_service.get_recommendations()


@router.get("/recently-played")
async def music_recently_played(limit: int = 10, types: str = "") -> dict[str, Any]:
    """The household's recently played items from MA's play history.
    ?limit=10&types=track,album (optional comma list, same as /search)."""
    import music_service
    media_types = [t.strip() for t in (types or "").split(",") if t.strip()] or None
    return await music_service.get_recently_played(limit=limit, media_types=media_types)


@router.post("/dont-stop")
async def music_dont_stop(payload: dict) -> dict[str, Any]:
    """Toggle MA's "Don't stop the music" (auto-continue with similar tracks).
    body: {enabled: bool, player_id?}."""
    import music_service
    enabled = bool((payload or {}).get("enabled", True))
    player_id = str((payload or {}).get("player_id") or "")
    ok = await music_service.set_dont_stop_the_music(enabled, player_id=player_id)
    return {"ok": ok, "enabled": enabled}

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

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5.0

# Google-hosted album art (YouTube Music / googleusercontent) carries an inline
# `=w<W>-h<H>-...` size directive. MA hands us a small thumbnail; the panel's
# hero art + bar thumb want the crispest square, so bump the directive. Other
# hosts (MA's image proxy, radio-station logos) have no such param → untouched.
_ART_SIZE_RE = re.compile(r"=w\d+-h\d+(?:-[a-z0-9-]+)*$", re.I)


def _hi_res_art(url: str) -> str:
    if not isinstance(url, str) or "googleusercontent.com" not in url:
        return url
    return _ART_SIZE_RE.sub("=w544-h544", url) if _ART_SIZE_RE.search(url) else url


def _as_seconds(value: Any) -> Optional[float]:
    """Coerce an MA progress/length field to a non-negative float, else None."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None

# YouTube Music needs a companion PO-token generator (the bgutil provider) that
# MA pings at `po_token_server_url` (MA's own default is http://127.0.0.1:4416).
# Zoe runs that generator locally as a docker-compose.modules service on :4416,
# so the user never has to type this URL: the setup form hides the field and
# save_provider() fills it server-side. Override with ZOE_YTMUSIC_POTOKEN_URL
# only if the generator is moved to another host/port.
_YTMUSIC_DOMAIN = "ytmusic"
_YTMUSIC_POTOKEN_KEY = "po_token_server_url"


def _ytmusic_potoken_url() -> str:
    return os.environ.get("ZOE_YTMUSIC_POTOKEN_URL", "http://localhost:4416").rstrip("/")


async def _potoken_reachable(url: str) -> bool:
    """Best-effort probe of the local PO-token generator's /ping. MA fails the
    ytmusic login if this URL is unreachable, so we check at setup time rather
    than persisting a dead URL and surfacing it as a confusing first-play error."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{url}/ping")
            return r.status_code == 200
    except Exception as exc:  # noqa: BLE001 — probe is advisory, never raises
        logger.debug("PO-token generator probe failed for %s: %s", url, exc)
        return False


def _ma_url() -> str:
    return os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")


def _ma_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _ma_response(command: str, timeout_s: float = _TIMEOUT_S, **args: Any) -> Any:
    """POST one MA command; return the httpx.Response, or None on a network/
    transport failure (unreachable, timeout). Never raises. `timeout_s` is a
    keyword for slow writes (no MA command takes a `timeout_s` arg)."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            # MA's JSON-RPC shim requires args NESTED under "args" — flat args are
            # silently dropped, causing "<x> is required" 500s. Commands with no
            # args work either way, which hid this until real playback was tried.
            payload: dict[str, Any] = {"command": command}
            if args:
                payload["args"] = args
            return await c.post(f"{_ma_url()}/api", json=payload, headers=_ma_headers())
    except Exception as exc:  # noqa: BLE001 — MA is optional; never break Zoe
        logger.debug("MA %s unreachable: %s", command, exc)
        return None


def _first_image(item: dict[str, Any]) -> str:
    """Best square art for a media item — its own image else the album's; only
    trusts absolute http(s) urls (never a relative/opaque path). MA is
    inconsistent about where art lives, so scan every shape it emits:
      • str                       — radio, some now-playing shapes
      • list[{path}]              — full library items
      • {"type","path"}           — MA's *brief* shape (recently-played, search
                                    hits): one image dict, `metadata` empty
      • metadata.images[{path}]   — full library items (playlists, albums)
    (the builtin `logo.png` placeholder is relative → dropped by the http guard)."""
    for src in (item, item.get("album") if isinstance(item.get("album"), dict) else None):
        if not isinstance(src, dict):
            continue
        # top-level image/images (radio, some now-playing shapes)
        imgs = src.get("image") or src.get("images")
        if isinstance(imgs, str) and imgs.startswith(("http://", "https://")):
            return imgs
        if isinstance(imgs, list):
            candidates = list(imgs)
        elif isinstance(imgs, dict):
            candidates = [imgs]
        else:
            candidates = []
        # metadata.images[] — where MA nests library-item art
        md_imgs = (src.get("metadata") or {}).get("images")
        if isinstance(md_imgs, list):
            candidates += md_imgs
        for im in candidates:
            u = im.get("path") if isinstance(im, dict) else im
            if isinstance(u, str) and u.startswith(("http://", "https://")):
                return u
    return ""


async def _ma(command: str, **args: Any) -> Any:
    """POST one MA command. Returns the parsed result or None on any failure."""
    r = await _ma_response(command, **args)
    if r is None:
        return None
    if r.status_code != 200:
        logger.debug("MA %s -> HTTP %s", command, r.status_code)
        return None
    return r.json()


async def _ma_ok(command: str, timeout_s: float = _TIMEOUT_S, **args: Any) -> bool:
    """True only if MA ACCEPTED the command (HTTP 200). For fire-and-forget
    writes (e.g. play_media) whose success body is `null` — `_ma` can't tell a
    200-with-null-body from an unreachable/timed-out MA, so callers that must
    report failure use this instead. (A URI MA accepts but fails on async is
    inherently undetectable synchronously — this catches down/timeout/reject.)"""
    r = await _ma_response(command, timeout_s=timeout_s, **args)
    if r is None:
        return False
    if r.status_code != 200:
        logger.debug("MA %s -> HTTP %s", command, r.status_code)
        return False
    return True


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items") or data.get("result") or []
    return []


# ── Read: players + now-playing ───────────────────────────────────────────────

async def get_players() -> list[dict[str, Any]]:
    return _as_list(await _ma("players/all"))


_PREFS_PATH = Path(__file__).resolve().parent / "data" / "music_prefs.json"


def get_preferred_player_id() -> str:
    """The household's remembered default speaker ('' when unset)."""
    try:
        with open(_PREFS_PATH) as fh:
            return str(json.load(fh).get("preferred_player_id") or "")
    except (OSError, ValueError):
        return ""


def set_preferred_player_id(player_id: str) -> None:
    """Persist the default speaker — every explicitly targeted play/transfer
    remembers its speaker so 'the next songs' land there too (operator ask
    2026-07-13). Best-effort: a failed write never breaks playback."""
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_PREFS_PATH, "w") as fh:
            json.dump({"preferred_player_id": str(player_id or "")}, fh)
    except OSError as exc:
        logger.warning("music prefs write failed (non-fatal): %s", exc)


def _pick_player(players: list[dict[str, Any]], player_id: str = "") -> Optional[dict[str, Any]]:
    """Choose the target player: the named one, else a playing/paused one
    (never hijack an active session's location), else the REMEMBERED default
    speaker, else the first available powered player."""
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
    preferred = get_preferred_player_id()
    if preferred:
        for p in players:
            if p.get("player_id") == preferred and p.get("available"):
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
    safe_image = image if isinstance(image, str) and image.startswith(("http://", "https://", "/")) else ""
    # Progress lives on the queue (elapsed_time); track length on the current item
    # (QueueItem.duration, else the media item's own duration). The bar/card seek
    # + scrubber render only when both are known (radio has neither → no scrubber).
    elapsed = _as_seconds((queue or {}).get("elapsed_time"))
    duration = _as_seconds(cur.get("duration") if isinstance(cur, dict) else None)
    if duration is None:
        duration = _as_seconds(media.get("duration"))
    return {
        "player_id": pid,
        "player_name": player.get("display_name") or player.get("name") or "Speaker",
        "state": state,  # playing | paused | idle
        "title": media.get("name") or "",
        "artist": artist,
        "album": (media.get("album") or {}).get("name", "") if isinstance(media.get("album"), dict) else (media.get("album") or ""),
        "image": _hi_res_art(safe_image),
        "volume": player.get("volume_level"),
        "queue_id": pid,
        # Where the playing track sits in the queue. The panel's Cover Flow needs
        # this to find "Now" among the covers AND to notice a track change at all
        # (its reload key is the item id) — without it the flow can't centre or
        # advance. queue_item_id is the reliable key; queue_index is the fallback
        # for shapes that lack one.
        #
        # NOTE: this is the QUEUE's current_index, deliberately NOT the current
        # item's own `index` field — they are different things. Live MA had
        # current_index=2 while current_item.index=0 on the same track, so
        # matching on the item's index silently points at the wrong cover.
        "queue_item_id": cur.get("queue_item_id") or "" if isinstance(cur, dict) else "",
        "queue_index": (queue or {}).get("current_index"),
        "shuffle": bool((queue or {}).get("shuffle_enabled")),
        "repeat": str((queue or {}).get("repeat_mode") or "off"),
        "elapsed": elapsed,
        "duration": duration,
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
    if action == "shuffle_set":
        # JSON callers may send "false"/"0"/"off" as strings — bool() would
        # treat every non-empty string as True and ENABLE shuffle when asked
        # to disable it.
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "on", "yes")
        await _ma("player_queues/shuffle", queue_id=pid, shuffle_enabled=bool(value))
        return True
    if action == "repeat_set" and value in ("off", "all", "one"):
        await _ma("player_queues/repeat", queue_id=pid, repeat_mode=value)
        return True
    return False


async def seek(position_seconds: Any, player_id: str = "") -> bool:
    """Seek the target player's current track to an absolute position (seconds).
    Best-effort — a bad position or a dead MA just no-ops; never raises. MA's
    `player_queues/seek` takes {queue_id, position}; queue_id == player_id for a
    solo player (see `now_playing`)."""
    try:
        pos = max(0, int(position_seconds))
    except (TypeError, ValueError):
        return False
    players = await get_players()
    player = _pick_player(players, player_id)
    if player is None:
        return False
    pid = player.get("player_id", "")
    if not pid:
        return False
    await _ma("player_queues/seek", queue_id=pid, position=pos)
    return True


async def transfer(target_player_id: str, source_player_id: str = "") -> bool:
    """Move current playback to another speaker.

    Transfers the source queue (an explicit source, else the active
    playing/paused player MA is using now) onto the target player's queue via
    MA's `player_queues/transfer`. `queue_id == player_id` for a solo player
    (see `now_playing`). Best-effort — MA carries over play state via auto_play;
    never raises. Returns True when a transfer command was dispatched."""
    if not target_player_id:
        return False
    players = await get_players()
    source = _pick_player(players, source_player_id) if source_player_id else _pick_player(players)
    if source is None:
        return False
    source_id = source.get("player_id", "")
    if not source_id or source_id == target_player_id:
        return False
    await _ma("player_queues/transfer", source_queue_id=source_id, target_queue_id=target_player_id)
    set_preferred_player_id(target_player_id)   # moving music = choosing a speaker
    return True


async def search_and_play(query: str, player_id: str = "",
                          radio_mode: bool = False,
                          zoe_user_id: str = "") -> Optional[dict[str, Any]]:
    """Search MA and play the top hit on the target player. Returns the matched
    item {name, media_type} or None. Local-first: searches all configured
    providers (builtin/radio/local files work with no account).

    `radio_mode=True` asks MA to seed an endless station of similar tracks from
    the hit ("play <artist> radio") instead of just the item itself. Requires a
    provider with SIMILAR_TRACKS (e.g. ytmusic); default False keeps existing
    callers' behaviour unchanged.

    `zoe_user_id` is the acting user for the listening journal (identity-
    threaded callers pass it; unidentified callers leave it '' → journaled as
    the reserved guest user via music_history.resolve_music_user)."""
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
    # play_media does real work synchronously (resolve the stream, start the
    # speaker) and can exceed the short read timeout — use the long write
    # timeout + honest failure reporting, same as play_media() below.
    if not await _ma_ok("player_queues/play_media", timeout_s=20.0, queue_id=pid,
                        media=uri, option="replace", radio_mode=radio_mode):
        return None
    if player_id:  # an explicitly chosen speaker becomes the remembered default
        set_preferred_player_id(pid)
    # Per-user listening journal (initiated event). Lazy import — music_history
    # imports this module for its observed-events poll.
    import music_history as _mh
    await _mh.record_play(zoe_user_id, source="initiated", player_id=pid,
                          **_mh.media_fields(hit))
    return {"name": hit.get("name", query), "artist": (hit.get("artists") or [{}])[0].get("name", "") if hit.get("artists") else ""}


# ── Queue management + playlists (the playlist-manager overlay) ───────────────
# Thin proxies over MA's player_queues / music.playlists commands. Every write
# is best-effort (returns ok:bool, never raises) so the panel can show a toast
# on failure without a 500.

async def _queue_items(queue_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Raw queue items straight from MA (the router has its own richer mapper;
    the manager only needs positions + ids here)."""
    res = await _ma("player_queues/items", queue_id=queue_id, limit=limit)
    return res if isinstance(res, list) else (res.get("items", []) if isinstance(res, dict) else [])


async def queue_move(queue_id: str, item_id: str, to_index: int) -> bool:
    """Reorder: move a queue item to an absolute index. MA's move_item takes a
    signed pos_shift, so compute it from the live position (robust vs a stale
    client index)."""
    items = await _queue_items(queue_id)
    if not items:
        return False
    frm = next((i for i, it in enumerate(items) if str(it.get("queue_item_id")) == str(item_id)), None)
    if frm is None:
        return False
    to = max(0, min(int(to_index), len(items) - 1))
    if to == frm:
        return True
    return await _ma_ok("player_queues/move_item", queue_id=queue_id,
                        queue_item_id=item_id, pos_shift=to - frm)


async def queue_remove(queue_id: str, item_id: str) -> bool:
    """Remove one item from the queue (by queue_item_id)."""
    return await _ma_ok("player_queues/delete_item", queue_id=queue_id, item_id_or_index=item_id)


async def queue_clear(queue_id: str) -> bool:
    """Clear the whole queue."""
    return await _ma_ok("player_queues/clear", queue_id=queue_id)


async def queue_play_index(queue_id: str, index: int) -> bool:
    """Jump to (and play) a specific queue position."""
    return await _ma_ok("player_queues/play_index", queue_id=queue_id, index=int(index))


async def queue_save_playlist(queue_id: str, name: str) -> bool:
    """Save the current queue as a new playlist."""
    name = (name or "").strip()
    if not name:
        return False
    return await _ma_ok("player_queues/save_as_playlist", timeout_s=20.0, queue_id=queue_id, name=name)


async def list_playlists() -> list[dict[str, Any]]:
    """The user's playlist library (name + uri + art), for the manager's browser."""
    # No `favorite` filter: MA treats it as a strict boolean (True→only favourites,
    # False→only non-favourites), so passing False would hide any hearted playlist.
    res = await _ma("music/playlists/library_items", limit=100)
    out: list[dict[str, Any]] = []
    for pl in (res.get("items", []) if isinstance(res, dict) else (res or [])):
        if not isinstance(pl, dict):
            continue
        out.append({
            "name": pl.get("name", ""),
            "uri": pl.get("uri", ""),
            "image": _hi_res_art(_first_image(pl)),
            "count": pl.get("count") or (pl.get("metadata") or {}).get("count"),
        })
    return out


async def playlist_tracks(uri: str, limit: int = 100) -> list[dict[str, Any]]:
    """Tracks in a playlist (for previewing before playing/queuing)."""
    res = await _ma("music/playlists/playlist_tracks", item_id=uri, limit=limit)
    out: list[dict[str, Any]] = []
    for t in (res or []):
        if not isinstance(t, dict):
            continue
        out.append({
            "name": t.get("name", ""),
            "uri": t.get("uri", ""),
            "artist": ((t.get("artists") or [{}])[0].get("name", "")) if t.get("artists") else "",
            "image": _hi_res_art(_first_image(t)),
        })
    return out


async def playlist_add(playlist_uri: str, track_uri: str) -> bool:
    """Add a track to an existing playlist."""
    if not (playlist_uri and track_uri):
        return False
    return await _ma_ok("music/playlists/add_playlist_tracks", db_playlist_id=playlist_uri, uris=[track_uri])


async def favorite_add(uri: str) -> bool:
    """Favorite (thumbs-up / add to library) a media item by uri."""
    if not uri:
        return False
    return await _ma_ok("music/favorites/add_item", item=uri)


# ── Browse: structured search + play-by-URI (the "use your music" surface) ────
# The now-playing card + voice path give you *what's playing* and one-shot
# "play <thing>". These two power the touch music page: see connected sources,
# search them, and tap a specific result onto a chosen speaker.

_SEARCH_MEDIA_TYPES = ("track", "album", "artist", "playlist", "radio")
# MA groups search hits under a plural key per media type.
_SEARCH_RESULT_KEY = {
    "track": "tracks", "album": "albums", "artist": "artists",
    "playlist": "playlists", "radio": "radio",
}


def _normalize_hit(item: Any, media_type: str) -> Optional[dict[str, Any]]:
    """One search hit → the flat shape the touch page renders + can play back.
    Drops items with no playable URI (can't act on them)."""
    if not isinstance(item, dict):
        return None
    uri = item.get("uri")
    if not isinstance(uri, str) or not uri:
        return None
    artists = item.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict)) if artists \
        else (item.get("artist") if isinstance(item.get("artist"), str) else "")
    album = item.get("album")
    album_name = album.get("name") if isinstance(album, dict) else (album if isinstance(album, str) else "")
    return {
        "name": item.get("name") or "",
        "uri": uri,
        "media_type": item.get("media_type") or media_type,
        "artist": artist,
        "album": album_name,
        "image": _hi_res_art(_first_image(item)),
    }


async def search(query: str, media_types: Optional[list[str]] = None,
                 limit: int = 8) -> dict[str, Any]:
    """Search the connected providers and return hits grouped by media type.

    MA/YouTube-Music returns far better coverage when each media type is queried
    on its own — a combined query silently drops tracks/albums — so we fan out
    one request per requested type and merge. Best-effort: returns
    available=False with empty buckets if MA is unreachable. Never raises."""
    import asyncio as _asyncio

    query = (query or "").strip()
    requested = [m for m in (media_types or _SEARCH_MEDIA_TYPES) if m in _SEARCH_RESULT_KEY]
    if not requested:
        requested = list(_SEARCH_MEDIA_TYPES)
    results: dict[str, list] = {_SEARCH_RESULT_KEY[m]: [] for m in _SEARCH_MEDIA_TYPES}
    if not query:
        return {"available": False, "query": query, "results": results}
    try:
        limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        limit = 8

    async def _one(mt: str) -> tuple[str, list[dict[str, Any]]]:
        res = await _ma("music/search", search_query=query, media_types=[mt], limit=limit)
        hits: list[dict[str, Any]] = []
        if isinstance(res, dict):
            for raw in (res.get(_SEARCH_RESULT_KEY[mt]) or []):
                norm = _normalize_hit(raw, mt)
                if norm:
                    hits.append(norm)
        return mt, hits

    gathered = await _asyncio.gather(*[_one(mt) for mt in requested])
    any_hit = False
    for mt, hits in gathered:
        results[_SEARCH_RESULT_KEY[mt]] = hits
        if hits:
            any_hit = True
    return {"available": any_hit, "query": query, "results": results}


async def play_media(uri: str, player_id: str = "", option: str = "replace",
                     radio_mode: bool = False, zoe_user_id: str = "") -> dict[str, Any]:
    """Play a specific media URI (from `search`) on a chosen speaker.

    `zoe_user_id`: acting user for the listening journal ('' → reserved guest
    user; see music_history.resolve_music_user).

    In MA the queue id *is* the player id. An explicit player_id must match a
    real player (so a stale id fails loudly instead of playing on the wrong
    speaker); with no id we fall back to the active/first powered player.
    `radio_mode=True` seeds an endless station of similar tracks from the item
    (default False — no change for existing callers). Best-effort — never
    raises."""
    uri = (uri or "").strip()
    if not uri:
        return {"ok": False, "reason": "empty uri"}
    players = await get_players()
    if player_id:
        player = next((p for p in players if p.get("player_id") == player_id), None)
        if player is None:
            return {"ok": False, "reason": "unknown player"}
    else:
        player = _pick_player(players)
    if player is None:
        return {"ok": False, "reason": "no player available"}
    pid = player.get("player_id", "")
    if not pid:
        return {"ok": False, "reason": "no player available"}
    # Report failure honestly: MA down/timeout/reject → HTTP != 200 → ok:False,
    # so the panel shows an error instead of a false "Playing …" toast. play_media
    # does real work synchronously (resolve the stream, start the speaker) and
    # returns 200 in ~6s+, so it needs a longer timeout than the read helpers.
    # option: replace (play now) | add (end of queue) | next (after current) —
    # the jukebox phone page queues with add/next; anything else (including
    # MA's own 'play' alias, which has different queue semantics) is coerced
    # to replace so play-now behaviour is uniform for every caller.
    if option not in ("replace", "add", "next"):
        option = "replace"
    if not await _ma_ok("player_queues/play_media", timeout_s=20.0, queue_id=pid,
                        media=uri, option=option, radio_mode=radio_mode):
        return {"ok": False, "reason": "playback failed"}
    # Per-user listening journal (initiated event). The URI alone carries no
    # metadata, so enrich via item_by_uri — best-effort, one cheap local call.
    import music_history as _mh
    _item = await _ma("music/item_by_uri", uri=uri)
    _fields = _mh.media_fields(_item if isinstance(_item, dict) else {"uri": uri})
    _fields["uri"] = uri
    await _mh.record_play(zoe_user_id, source="initiated", player_id=pid, **_fields)
    return {
        "ok": True, "uri": uri, "player_id": pid,
        "player_name": player.get("display_name") or player.get("name") or "",
    }


async def set_dont_stop_the_music(enabled: bool, player_id: str = "") -> bool:
    """Toggle MA's "Don't stop the music" on the target queue: when the queue
    runs out, MA auto-continues with similar tracks (needs a SIMILAR_TRACKS
    provider, e.g. ytmusic — MA rejects the enable otherwise → False).
    Best-effort — never raises."""
    players = await get_players()
    player = _pick_player(players, player_id)
    if player is None:
        return False
    pid = player.get("player_id", "")
    if not pid:
        return False
    return await _ma_ok("player_queues/dont_stop_the_music", queue_id=pid,
                        dont_stop_the_music_enabled=bool(enabled))


async def get_recommendations() -> dict[str, Any]:
    """MA's native recommendation shelves ("Listen again", "Mixed for you", …)
    from providers with RECOMMENDATIONS, normalized to the flat search-hit shape
    the touch page already renders. Best-effort — never raises."""
    folders = await _ma("music/recommendations")
    if isinstance(folders, dict):  # some shim responses wrap the list
        folders = folders.get("result") or folders.get("folders") or folders.get("items") or []
    out: list[dict[str, Any]] = []
    for f in folders if isinstance(folders, list) else []:
        if not isinstance(f, dict):
            continue
        items = []
        for raw in (f.get("items") or []):
            norm = _normalize_hit(raw, str((raw or {}).get("media_type") or "track") if isinstance(raw, dict) else "track")
            if norm:
                items.append(norm)
        if items:
            out.append({"name": f.get("name") or "", "items": items})
    return {"available": bool(out), "folders": out}


async def get_recently_played(limit: int = 10,
                              media_types: Optional[list[str]] = None) -> dict[str, Any]:
    """The household's recently played items from MA's real play history,
    normalized to the flat search-hit shape. Best-effort — never raises."""
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 10
    args: dict[str, Any] = {"limit": limit}
    if media_types:
        args["media_types"] = media_types
    raw = await _ma("music/recently_played_items", **args)
    items = []
    for it in _as_list(raw):
        if not isinstance(it, dict):
            continue
        norm = _normalize_hit(it, str(it.get("media_type") or "track"))
        if norm:
            items.append(norm)
    return {"available": bool(items), "items": items}


# ── Skybridge card + resolver ────────────────────────────────────────────────

def now_playing_card(np: dict[str, Any]) -> dict[str, Any]:
    """A Skybridge now-playing *canvas* card: album-art-forward display only.
    Transport + volume + seek + the speaker picker all live on the floating
    control bar (the single control surface); this card carries no controls,
    just the hero art, track meta, a display-only progress bar, and an "Up next"
    queue the panel hydrates from `queue_id`."""
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
            "elapsed": np.get("elapsed"),
            "duration": np.get("duration"),
            "queue_id": np.get("queue_id") or np.get("player_id") or "",
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
                {"label": "＋ Add music service", "query": "add music"},
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


def _match_player_by_name(players: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    """Find the speaker whose display name best matches a spoken room name
    ("kitchen", "living room"). Exact → prefix → substring, case-insensitive."""
    q = (name or "").strip().lower()
    if not players or not q:
        return None
    cand = [(p, str(p.get("display_name") or p.get("name") or "").strip().lower()) for p in players]
    for p, n in cand:
        if n and n == q:
            return p
    for p, n in cand:
        if n and (n.startswith(q) or q.startswith(n)):
            return p
    for p, n in cand:
        if n and (q in n or n in q):
            return p
    return None


_GENERIC_PLAY = {"", "music", "something", "anything", "a song", "songs", "tunes", "some tunes", "some music"}


def split_play_target(query: str, players: list[dict[str, Any]]) -> tuple[str, Optional[dict[str, Any]]]:
    """Split "jazz in the kitchen" → ("jazz", <Kitchen player>) using the REAL
    player list; a suffix that matches no player stays in the query ("golden
    on youtube music" keeps its provider suffix). A generic base ("music",
    "something", …) returns "" so the caller resumes/prompts instead of
    searching for the literal word.
    """
    q = (query or "").strip()
    base, target = q, None
    lowered = f" {q.lower()} "
    for sep in (" in the ", " on the ", " in ", " on "):
        idx = lowered.rfind(sep)
        if idx < 0:
            continue
        cand_base = q[: idx].strip() if idx > 0 else ""
        cand_room = q[idx + len(sep) - 1:].strip()
        hit = _match_player_by_name(players, cand_room)
        if hit is not None:
            base, target = cand_base, hit
            break
    if base.lower().strip() in _GENERIC_PLAY:
        base = ""
    return base, target


async def resolve_music(intent: Any, user_id: str = "") -> dict[str, Any]:
    """The Skybridge music domain resolver. `intent` has .action and .query.
    `user_id` is the acting user for the listening journal ('' → guest)."""
    action = getattr(intent, "action", "status")
    query = (getattr(intent, "query", "") or "").strip()

    if action == "setup":
        return await resolve_music_setup(query)

    if action == "transfer" and query:
        # Voice path for speaker switching: "move/switch music to the kitchen".
        players = await get_players()
        target = _match_player_by_name(players, query)
        if target is None:
            names = ", ".join(
                str(p.get("display_name") or p.get("name")) for p in players
                if (p.get("display_name") or p.get("name")))
            return _result(
                f"I couldn't find a speaker called “{query}”." + (f" You have: {names}." if names else ""),
                _browse_card(), "transfer")
        np0 = await now_playing()
        tname = target.get("display_name") or target.get("name") or "there"
        if not await transfer(target.get("player_id", "")):
            return _result(f"I couldn't move the music to {tname}.",
                           now_playing_card(np0 or {}), "transfer")
        np = await now_playing(target.get("player_id", "")) or np0 or {}
        return _result(f"Moved the music to {tname}.", now_playing_card(np), "transfer")

    if action == "play":
        radio_mode = bool(getattr(intent, "radio_mode", False))
        # Room/speaker targeting: "play jazz in the kitchen" must aim at the
        # Kitchen player, not search for "jazz in the kitchen" (which used to
        # match playlists like "Music for Cleaning the Kitchen").
        players = await get_players()
        base, target = split_play_target(query, players)
        player_id = (target or {}).get("player_id", "")
        tname = (target or {}).get("display_name") or (target or {}).get("name") or ""
        on_txt = f" on {tname}" if tname else ""

        if not base:
            # Generic "play some music": resume whatever is queued/paused first.
            np0 = await now_playing(player_id)
            if np0 and np0.get("title"):
                await control("play", player_id=player_id or np0.get("player_id", ""))
                np = await now_playing(player_id or np0.get("player_id", "")) or np0
                if np.get("state") == "playing":
                    return _result(f"Resuming {np.get('title') or 'the music'}{on_txt}.",
                                   now_playing_card(np), "play")
            return _result(
                "What would you like to hear — an artist, a song, or the radio?" + (f" I'll put it on {tname}." if tname else ""),
                _browse_card(), "play")

        # "Play my discovery playlist" → the Zoe Discovery playlist that the
        # weekly digarr batch maintains (music_discovery.py). Lazy import —
        # music_discovery imports this module. Falls through to a friendly
        # nudge when no batch has run yet (playlist doesn't exist).
        import music_discovery as _md
        if _md.is_discovery_playlist_query(base):
            res = await _md.play_discovery(player_id=player_id, zoe_user_id=user_id)
            if res.get("ok"):
                np = await now_playing(player_id) or {}
                return _result(f"Playing your discovery playlist{on_txt}.",
                               now_playing_card(np or {"state": "playing",
                                                       "title": _md.DISCOVERY_PLAYLIST_NAME,
                                                       "artist": ""}), "play")
            return _result(
                "I haven't put together a discovery playlist yet — "
                "I'll have one after my next music discovery run.",
                _browse_card(), "play")

        hit = await search_and_play(base, player_id=player_id, radio_mode=radio_mode,
                                    zoe_user_id=user_id)
        if hit:
            np = await now_playing(player_id) or {}
            name = hit.get("name")
            spoken = (f"Playing {name} radio" if name and radio_mode
                      else f"Playing {name}" if name else "Playing that now") + on_txt + "."
            return _result(spoken, now_playing_card(np or {"state": "playing", "title": hit["name"], "artist": hit.get("artist", "")}), "play")
        return _result("I couldn't find that to play.", _browse_card(), "play")

    if action == "dont_stop":
        # "keep the music going" / "don't stop the music" / "play something like
        # this" — MA auto-continues the queue with similar tracks when it runs out.
        np0 = await now_playing()
        if np0 is None:
            return _result("There's no music playing right now.", _browse_card(), action)
        if not await set_dont_stop_the_music(True):
            return _result("I couldn't turn that on — it needs a music service that can find similar songs.",
                           now_playing_card(np0), action)
        return _result("I'll keep the music going with similar songs.", now_playing_card(np0), action)

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


# ── Provider setup (the "add a music source through Zoe" back-office) ─────────

# User-facing catalogue: the streaming/content providers worth offering, with
# how they authenticate. Account-free ones can be enabled with one tap; account
# ones go through the QR→phone flow.
_SETUP_CATALOGUE = [
    {"domain": "spotify", "name": "Spotify", "auth": "oauth", "accent": "mint"},
    {"domain": "ytmusic", "name": "YouTube Music", "auth": "browser", "accent": "red"},
    {"domain": "tidal", "name": "Tidal", "auth": "oauth", "accent": "cool"},
    {"domain": "qobuz", "name": "Qobuz", "auth": "form", "accent": "violet"},
    {"domain": "deezer", "name": "Deezer", "auth": "oauth", "accent": "warm"},
    {"domain": "radiobrowser", "name": "Radio (free)", "auth": "free", "accent": "sunny"},
    {"domain": "tunein", "name": "TuneIn (free)", "auth": "free", "accent": "sunny"},
]

_HIDDEN_ENTRY_TYPES = {"label", "divider"}


async def provider_catalogue() -> list[dict[str, Any]]:
    """The 'Add music' catalogue merged with configured status."""
    configured = {c.get("domain") for c in (await _ma("config/providers") or []) if isinstance(c, dict)}
    return [{**p, "connected": p["domain"] in configured} for p in _SETUP_CATALOGUE]


def _clean_entries(entries: Any) -> list[dict[str, Any]]:
    """Reduce MA config entries to the user-facing form fields the phone renders."""
    out: list[dict[str, Any]] = []
    for e in entries if isinstance(entries, list) else []:
        etype = e.get("type")
        key = e.get("key")
        if etype in _HIDDEN_ENTRY_TYPES or not key:
            continue
        # Hide advanced/library-sync toggles + dev fields from the simple form;
        # keep required credentials + the OAuth action.
        if key.startswith(("library_sync", "sync_")) or key in ("log_level",) or key.endswith("_dev"):
            continue
        out.append({
            "key": key,
            "type": etype,                       # string | secure_string | boolean | action
            "label": e.get("label") or key,
            "description": e.get("description") or "",
            "required": bool(e.get("required")),
            "action": e.get("action") or "",
            "default": e.get("default_value"),
        })
    return out


async def provider_setup_form(provider: str) -> Optional[dict[str, Any]]:
    """The setup form for a provider: catalogue entry + cleaned config fields.
    Returns None if the provider is unknown to MA."""
    meta = next((p for p in _SETUP_CATALOGUE if p["domain"] == provider), None)
    if meta is None:
        return None
    entries = await _ma("config/providers/get_entries", provider_domain=provider)
    if entries is None:
        return None
    fields = _clean_entries(entries)
    if provider == _YTMUSIC_DOMAIN:
        # Hide the PO-token server field — Zoe runs the generator locally and
        # sets it in save_provider(). The phone only asks for username + cookie.
        fields = [f for f in fields if f["key"] != _YTMUSIC_POTOKEN_KEY]
    return {**meta, "fields": fields}


async def provider_instance_id(provider: str) -> Optional[str]:
    """The instance_id of the currently configured instance for a provider domain,
    or None if the provider isn't configured. Used to UPDATE an existing instance
    in-place (re-connect / cookie refresh) instead of creating a duplicate."""
    for p in (await _ma("config/providers") or []):
        if isinstance(p, dict) and p.get("domain") == provider:
            return p.get("instance_id") or p.get("id")
    return None


async def save_provider(provider: str, values: dict[str, Any],
                        instance_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Persist a provider instance in MA. Returns the saved config or None.

    Pass ``instance_id`` to UPDATE that existing instance in place (MA otherwise
    mints a new instance on every save — which duplicates the provider on a
    re-connect or a cookie refresh)."""
    # Merge the caller's values over MA's defaults so unspecified fields are valid.
    entries = await _ma("config/providers/get_entries", provider_domain=provider) or []
    merged = {e["key"]: e.get("default_value") for e in entries
              if isinstance(e, dict) and e.get("key") and e.get("type") not in _HIDDEN_ENTRY_TYPES}
    merged.update({k: v for k, v in (values or {}).items() if v is not None})
    if provider == _YTMUSIC_DOMAIN:
        # Always point YouTube Music at Zoe's local PO-token generator, whatever
        # the phone sent (the field is hidden from that form). Reachability is
        # checked by the phone save endpoint so the user gets an accurate message.
        merged[_YTMUSIC_POTOKEN_KEY] = _ytmusic_potoken_url()
    args: dict[str, Any] = {"provider_domain": provider, "values": merged}
    if instance_id:
        args["instance_id"] = instance_id
    saved = await _ma("config/providers/save", **args)
    return saved if isinstance(saved, dict) else None


# ── Panel-side setup cards (the QR the phone scans) ──────────────────────────

def _catalogue_card(cat: list[dict[str, Any]]) -> dict[str, Any]:
    """A card listing music services to add — each a data-sky-action chip."""
    actions = []
    for p in cat:
        if p.get("connected"):
            continue
        label = p["name"]  # connected providers are skipped above
        verb = "connect radio" if p["auth"] == "free" and p["domain"] == "radiobrowser" else f"connect {p['domain']}"
        actions.append({"label": label, "query": verb})
    return {
        "card_type": "music_setup", "schema_version": "1.0.0", "card_id": "music-add",
        "content": {"source": "music_setup", "mode": "catalogue", "title": "Add music",
                    "subtitle": "Pick a service — free ones connect instantly, accounts open on your phone.",
                    "actions": actions},
    }


def _qr_card(provider: str, name: str, qr_path: str) -> dict[str, Any]:
    return {
        "card_type": "music_setup", "schema_version": "1.0.0", "card_id": "music-qr-" + provider,
        "content": {"source": "music_setup", "mode": "qr", "title": "Connect " + name,
                    "subtitle": "Scan with your phone to sign in — the link is private to your network and expires shortly.",
                    "qr_path": qr_path, "provider": provider},
    }


async def resolve_music_setup(provider_query: str) -> dict[str, Any]:
    """Resolve 'add music' / 'connect <provider>' into a catalogue or QR card."""
    import music_setup
    cat = await provider_catalogue()
    q = (provider_query or "").strip().lower()
    match = next((p for p in cat if p["domain"] == q or p["name"].lower() == q), None)
    if match is None:
        return _result("What would you like to add?", _catalogue_card(cat), "setup")
    if match.get("connected"):
        return _result(f"{match['name']} is already connected.", _catalogue_card(cat), "setup")
    if match["auth"] == "free":
        saved = await save_provider(match["domain"], {})
        msg = f"Added {match['name']}. Ask me to play a station." if saved else "I couldn't add that."
        return _result(msg, _catalogue_card(await provider_catalogue()), "setup")
    minted = music_setup.mint(match["domain"])
    qr_path = f"/api/music/setup/qr?token={minted['token']}&provider={match['domain']}"
    return _result(f"Scan the code to connect {match['name']} from your phone.",
                   _qr_card(match["domain"], match["name"], qr_path), "setup")

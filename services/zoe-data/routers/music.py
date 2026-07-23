"""
Music Assistant proxy endpoints.

Provides Zoe's frontend with MA status, configured providers, and available
provider types — all forwarded from the local MA instance (default :8095).
Auth is intentionally session-free here: the data is non-sensitive connection
status that the music page and settings page both need without an extra auth hop.
"""
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter

from music_service import _first_image, _hi_res_art

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


# ---------------------------------------------------------------------------
# Device-type resolution (server-side; the panel gets flat resolved fields)
# ---------------------------------------------------------------------------
#
# MA's own `icon` field is useless for the panel — it is `mdi-speaker` for
# almost every player. The real signal for "what is this thing" lives across
# `type` (player | group), `provider` (sonos | chromecast | airplay |
# universal_player) and `device_info.model`. Resolving it HERE keeps the doctrine
# the panel already follows for dock pins and rooms: the browser never parses a
# vendor model string, it just renders a flat `kind` + `kind_label`.
#
# `kind` is a small closed set the panel draws an icon for:
#   group     -> an MA sync/cast group (e.g. the "House" Chromecast group)
#   display   -> a smart display WITH a screen (Google Nest Hub)
#   speaker   -> a dedicated audio speaker (Sonos Beam/Arc, Google Home Mini)
#   computer  -> a laptop/desktop (Jason's MacBook Pro)
#   tv        -> anything whose job is a TV screen: a smart TV, a Chromecast
#                dongle plugged into a TV, or an Apple TV streaming box
#
# There is deliberately NO `airplay` kind: AirPlay is a TRANSPORT, not a form
# factor, and the operator asked "is it a TV, a speaker, etc" — a form-factor
# question. Every AirPlay device in this house is either an Apple TV (-> tv) or
# the MacBook (-> computer), so a transport-named bucket would only muddy the
# very disambiguation this change exists to provide.
#
# The two identically-named "Bedroom" players are exactly why this exists:
#   RINCON_347E5C9BEC8F01400  Sonos "Beam"     -> kind=speaker, "Sonos Beam"
#   ap40cbc0db9fb8            AirPlay "Apple TV 4K" -> kind=tv,  "Apple TV" (unavailable)
# so the picker can finally tell the real (Sonos) one from the dead AirPlay one.

# Model/name substrings that identify a form factor. Ordered checks below decide
# precedence; these are just the vocabularies. Matched case-insensitively.
_SPEAKER_MODEL_HINTS = (
    "sonos", "beam", "arc", "home mini", "nest mini", "nest audio",
    "homepod", "echo", "speaker", "soundbar", "one sl", "play:",
)
# Only SPECIFIC smart-display models — never a bare "display". The display check
# runs before the TV check, so a generic "display" substring would misclassify a
# TV whose model happens to carry the word (e.g. "Samsung Smart Monitor M7
# Display" or "Chromecast with Google TV Display") as a Nest-Hub-style display.
_DISPLAY_MODEL_HINTS = ("nest hub", "home hub", "hub max", "smart display")
_COMPUTER_MODEL_HINTS = ("macbook", "imac", "mac mini", "mac studio", "mac pro",
                         "laptop", "desktop", "surface", " pc")
_TV_MODEL_HINTS = ("apple tv", "chromecast", "smart tv", "media renderer",
                   "oled", "qled", "webos", "bravia", "roku", "fire tv", " tv")


def _strip_parenthetical(text: str) -> str:
    """"MacBook Pro (MacBookPro18,2)" -> "MacBook Pro"."""
    return re.sub(r"\s*\(.*?\)\s*", " ", text or "").strip()


def _clean_brand(manufacturer: str) -> str:
    """"LG Electronics" -> "LG"; "Unknown manufacturer" -> "" (unknown)."""
    m = (manufacturer or "").strip()
    if m.lower() in ("", "unknown", "unknown manufacturer"):
        return ""
    m = re.sub(r"\s+(electronics|inc\.?|corp\.?|corporation|co\.?|ltd\.?|llc)$",
               "", m, flags=re.I).strip()
    # Vendor casing the panel expects.
    return {"sonos": "Sonos", "google": "Google", "lg": "LG"}.get(m.lower(), m)


def _strip_google(model: str) -> str:
    """"Google Nest Hub" -> "Nest Hub"; "Google Home Mini" -> "Home Mini"."""
    return re.sub(r"^google\s+", "", model or "", flags=re.I).strip()


def resolve_player_kind(player: dict) -> dict[str, str]:
    """Resolve a MA player into a flat ``{"kind", "kind_label"}`` for the panel.

    Pure — takes one player dict (the shape returned by MA's ``players/all``)
    and returns only the two derived fields. Derivation uses ``type`` +
    ``provider`` + ``device_info.model`` ONLY, never the user-editable ``name``
    (a Sonos a user renamed "TV Room" must stay a speaker). See the module
    comment above for the mapping and its rationale.
    """
    ptype = str(player.get("type") or "").lower()
    provider = str(player.get("provider") or "").lower()
    dev = player.get("device_info") or {}
    model = str(dev.get("model") or "")
    manufacturer = str(dev.get("manufacturer") or "")
    low = model.lower()

    def _has(hints: tuple) -> bool:
        return any(h in low for h in hints)

    # --- kind (order is precedence; first match wins) ---
    if ptype == "group":
        kind = "group"
    elif provider == "sonos":
        kind = "speaker"                     # Sonos players are always speakers
    elif "apple tv" in low:
        kind = "tv"                          # Apple TV is a streaming box on a TV
    elif _has(_DISPLAY_MODEL_HINTS):
        kind = "display"                     # Nest Hub etc. — a screen you also cast to
    elif _has(_COMPUTER_MODEL_HINTS):
        kind = "computer"
    elif _has(_SPEAKER_MODEL_HINTS):
        kind = "speaker"                     # Home Mini and friends
    elif _has(_TV_MODEL_HINTS):
        kind = "tv"                          # OLED/QLED/Smart TV/Chromecast/Media Renderer
    elif provider in ("chromecast", "airplay", "universal_player"):
        # An AV endpoint (cast dongle / AirPlay-2 receiver / DLNA renderer) that
        # is not a known speaker/computer is, in practice, a TV.
        kind = "tv"
    else:
        kind = "speaker"

    # --- kind_label (short, human; prefers a cleaned model) ---
    m = _strip_parenthetical(model)
    if provider == "sonos":
        label = m if m.lower().startswith("sonos") else (f"Sonos {m}" if m else "Sonos")
    elif "apple tv" in low:
        label = "Apple TV"
    elif kind == "group":
        label = "Speaker group"
    elif kind == "computer":
        label = m or "Computer"
    elif kind == "display":
        label = _strip_google(m) or "Smart display"
    elif kind == "speaker":
        label = _strip_google(m) or "Speaker"
    elif kind == "tv":
        if "chromecast" in low:
            label = "Chromecast"             # clearer than "Google TV"
        else:
            brand = _clean_brand(manufacturer)
            label = f"{brand} TV" if brand else (m or "TV")
    else:
        label = m or "Speaker"

    return {"kind": kind, "kind_label": label}


def _queue_item_art(item: dict) -> str:
    """Resolve one queue item's cover to a real http(s) url.

    MA hands a queue item its art as a single dict — {"type","path"} — and hangs
    a richer `media_item` off it. Prefer the media_item's art: it's the same
    i.ytimg maxres source `now-playing` reports, so the centre cover of the flow
    matches the now-playing art instead of a lower-res yt3 thumb. Fall back to
    the item's own thumb. `_first_image` understands both shapes and drops
    anything non-http, so a missing cover degrades to the placeholder.
    """
    media_item = item.get("media_item")
    art = _first_image(media_item) if isinstance(media_item, dict) else ""
    return _hi_res_art(art or _first_image(item))


def _queue_item_title(item: dict) -> str:
    """The track title, WITHOUT the artist glued on.

    A queue item's own `name` is a concatenation — live MA returns
    "Livingston - Shadow" — while `media_item.name` carries the clean title
    ("Shadow") and `media_item.artists[]` the artist. Splitting the composite on
    " - " would be guesswork that breaks on any title containing a dash, so take
    the clean field MA already gives us and fall back to `name` only when there
    is no media_item (radio, some providers).
    """
    media_item = item.get("media_item")
    if isinstance(media_item, dict):
        name = media_item.get("name")
        if isinstance(name, str) and name.strip():
            return name
    return item.get("name") or ""


def _queue_item_artist(item: dict) -> str:
    """The performing artist(s), flat.

    Queue items have NO `artist` key at all — the panel read `it.artist` and got
    undefined, so the artist line under every browsed cover was silently blank.
    The real value is nested at media_item.artists[].name; resolve it here so the
    panel keeps receiving a flat, already-resolved field instead of reaching into
    MA's payload shape (the same reason `image` is resolved at this seam).
    """
    media_item = item.get("media_item")
    if not isinstance(media_item, dict):
        return ""
    artists = media_item.get("artists") or []
    names = [a.get("name", "").strip() for a in artists
             if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("name").strip()]
    if names:
        return ", ".join(names)
    artist = media_item.get("artist")
    return artist.strip() if isinstance(artist, str) else ""


def normalize_queue_items(items: list) -> list[dict]:
    """MA queue items -> the flat shape the panel renders and can act on.

    Everything the client needs is resolved HERE so it never has to know MA's
    payload shape. In particular `index`: MA carries TWO index-ish fields and the
    obvious one is a trap — live MA returns ``index: 0`` for EVERY item while
    ``sort_index`` holds the real queue position. The panel sent ``index`` to
    play-index, so tapping any cover in the Cover Flow restarted track 1.
    """
    out: list[dict] = []
    for pos, item in enumerate(i for i in items if isinstance(i, dict)):
        sort_index = item.get("sort_index")
        out.append({
            **item,
            "index": sort_index if isinstance(sort_index, int) else pos,
            "image": _queue_item_art(item),
            "title": _queue_item_title(item),
            "artist": _queue_item_artist(item),
        })
    return out


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
                items = data if isinstance(data, list) else (data.get("items") or [])
                # Normalize art HERE or it reaches the panel as MA's raw dict and
                # renders as "[object Object]" — this endpoint used to pass MA's
                # payload through verbatim, which is how it dodged the shared
                # extractor that already fixed the same bug on the other paths.
                # `index` is NOT the queue position — live MA returns 0 for EVERY
                # item while `sort_index` carries the real position. The panel
                # sent `index` to play-index, so tapping any cover in the Cover
                # Flow restarted track 1. Resolve the true position here (falling
                # back to enumeration order) so the client never has to know which
                # of MA's two index-ish fields to trust.
                return normalize_queue_items(items)
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
    # Enrich each player with flat, panel-ready device-type fields so the browser
    # never parses a vendor model string. Existing fields are preserved.
    for p in players:
        if isinstance(p, dict):
            p.update(resolve_player_kind(p))
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
        # An empty player list means "cannot validate", NOT "no players".
        # music_service._ma never raises — a transport failure returns None and
        # get_players() turns that into []. Treating [] as an empty set rejected
        # every id, so a brief MA outage locked the operator out of setting their
        # own default speaker. Validate only when the list was actually visible.
        players = await music_service.get_players()
        if players and not any(p.get("player_id") == pid for p in players):
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


@router.get("/groups")
async def music_groups() -> dict[str, Any]:
    """Multi-room grouping state: who is grouped with whom, and who CAN group.

    Companion to `/transfer` (which moves playback to one speaker) — this is the
    read side of spreading it across several. Every field is flat and already
    resolved: the panel renders the picker straight from this and must never
    reach into MA's payload shape.

    {
      "available": bool,          # false == MA unreachable, NOT "no speakers"
      "players": [{
        "player_id", "name",      # `name` is always non-empty
        "provider",               # disambiguates two speakers of the same name
        "available", "powered", "state",
        "is_group_player":  bool, # a virtual group player, not a real speaker
        "is_static_group":  bool, # provider-fixed membership — not editable
        "can_lead":         bool, # supports set_members => may be a target
        "can_group_with":   [player_id, ...],   # resolved real ids only
        "role":  "solo" | "leader" | "follower",
        "grouped":          bool,
        "leader_id":        str,  # "" when solo; the player that OWNS THE QUEUE
        "group_member_ids": [player_id, ...]    # whole set, leader first
      }],
      "groups": [{"leader_id", "leader_name", "is_virtual_leader", "is_static",
                  "member_ids": [...], "member_names": [...]}]
    }

    Unavailable players are still listed (with `available: false`) so an offline
    or flapping speaker never silently disappears from the picker.
    """
    import music_service
    view = await music_service.get_speaker_groups()
    if view is None:
        return {"available": False, "players": [], "groups": []}
    return {"available": True, **view}


@router.post("/group")
async def music_group(payload: dict) -> dict[str, Any]:
    """Join and/or unjoin speakers to a target in one atomic call.

    body: {target_player_id, add: [player_id, ...], remove: [player_id, ...]}
    -> {ok, target_player_id, added, removed} | {ok: false, reason}

    `target_player_id` becomes (or stays) the group LEADER — the player that
    owns the queue. Both lists are optional but at least one must be non-empty.
    Taking them together mirrors MA's own `set_members` and lets a multi-select
    picker apply its whole selection at once, instead of firing one call per
    speaker and racing them against each other.
    """
    import music_service
    b = payload or {}
    add = b.get("add") or []
    remove = b.get("remove") or []
    if not isinstance(add, list) or not isinstance(remove, list):
        return {"ok": False, "reason": "add and remove must be lists"}
    return await music_service.group_players(
        str(b.get("target_player_id") or ""), add=add, remove=remove,
    )


@router.post("/ungroup")
async def music_ungroup(payload: dict) -> dict[str, Any]:
    """Remove one speaker from whatever group it is in.

    body: {player_id} -> {ok, player_id} | {ok: false, reason}

    Separate from `/group` because the caller does not know which target to
    remove FROM: a sync member leaves its leader, a permanent-group member
    leaves its group player, and a LEADER dissolves its group entirely. MA owns
    that disambiguation, so this stays a one-argument call rather than making
    the panel work out the topology first.
    """
    import music_service
    return await music_service.ungroup_player(str((payload or {}).get("player_id") or ""))


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


@router.post("/unfavorite")
async def music_unfavorite(payload: dict) -> dict[str, Any]:
    """Un-favorite a media item. body: {uri}. The other half of /favorite —
    without it the panel's heart could only ever be turned ON."""
    import music_service
    return {"ok": await music_service.favorite_remove(str((payload or {}).get("uri") or ""))}


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

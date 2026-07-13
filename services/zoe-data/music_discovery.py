"""Music discovery — the Zoe-side plumbing behind the hidden digarr engine.

digarr (labs/digarr-spike/ verdict: batch-only, ephemeral container) proposes
new artists/albums; this module owns the fully-local pieces around it:

- taste seed: derive the household's top artists from what Music Assistant
  already persists (per-item play_count on library tables + the playlog read
  through ``music/recently_played_items``). No custom listening-history store,
  no cloud scrobbler — nothing leaves the house.
- recommendations store: the batch's output JSON on local disk.
- playlist bridge: resolve digarr picks to playable MA tracks and build the
  "Zoe Discovery" playlist through MA's own playlist API (create + remove/add;
  MA has no atomic replace). digarr's M3U export is Spotify-URL entries MA
  cannot play, which is why this JSON→API bridge exists (spike finding).
- intent surface: play the discovery playlist through the normal music path.

KNOWN LIMITATION (documented, deliberate): MA's playlog is an upserted
last-played row per item/user with 90-day retention — not a per-play event
timeline — and Zoe's single MA token collapses attribution to one household
user. Good enough for a taste seed. If a future weekly digest needs true
per-play deltas, the minimal follow-up is a tiny append-only journal polling
``music/recently_played_items`` — NOT built now.

All MA traffic goes through music_service._ma (the sole MA client); every
helper is best-effort and never raises.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import music_service as ms

logger = logging.getLogger(__name__)

DISCOVERY_PLAYLIST_NAME = "Zoe Discovery"

_DATA_DIR = Path(__file__).resolve().parent / "data" / "music_discovery"
RECOMMENDATIONS_PATH = Path(
    os.environ.get("ZOE_MUSIC_DISCOVERY_JSON", str(_DATA_DIR / "recommendations.json"))
)

# Spoken/typed ways to ask for the discovery playlist. Matched against the
# *play query* after room-target splitting ("play my discovery playlist in the
# kitchen" → base "my discovery playlist").
_DISCOVERY_ALIAS_RE = re.compile(
    r"^(?:my |the |zoe(?:'s)? )*(?:music )?discover(?:y|ies)"
    r"(?: playlist| mix| picks| list)?$|^zoe discovery$",
    re.IGNORECASE,
)


def is_discovery_playlist_query(query: str) -> bool:
    """True when a play query means the 'Zoe Discovery' playlist."""
    return bool(_DISCOVERY_ALIAS_RE.match((query or "").strip()))


# ── Taste seed (fully local: MA's own play data) ─────────────────────────────

def _first_artist(name: str) -> str:
    """'A, B & C' → 'A' — one clean seed name per credit string."""
    return re.split(r",| & | feat\.? | featuring | x ", name, maxsplit=1)[0].strip()


async def taste_seed(max_artists: int = 8, recent_limit: int = 50,
                     enrich_limit: int = 25,
                     user_id: Optional[str] = None) -> list[str]:
    """Top artists for a taste profile, best-signal first.

    Primary: Zoe's own per-user listening journal (music_history — the only
    source with real user attribution). ``user_id=None`` → household
    aggregate for the shared playlist; a specific id (incl. the reserved
    guest id) → ONLY that user's rows, so guest plays never pollute a named
    member's profile. Per-user "Zoe Discovery — <name>" playlists build on
    this (follow-up PR).

    Fallbacks while the journal is thin (household aggregate only — MA data
    carries no user attribution): MA's recently played tracks
    (``music/recently_played_items``, enriched via ``music/item_by_uri``
    because playlog rows carry no artist), then library artists by lifetime
    play_count. Returns [] when everything is empty/unreachable — callers
    fall back to a generic mood query.
    """
    import music_history as mh

    journal = await mh.top_artists(user_id=user_id, limit=max_artists)
    if len(journal) >= max_artists or user_id is not None:
        return journal

    counts: dict[str, int] = {n: 2 for n in journal}  # journal names outrank fallbacks
    order: list[str] = list(journal)

    recent = await ms._ma(
        "music/recently_played_items",
        limit=recent_limit, media_types=["track"], fully_played_only=False,
    )
    seen_uris: set[str] = set()
    for item in recent if isinstance(recent, list) else []:
        uri = item.get("uri") if isinstance(item, dict) else None
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        if len(seen_uris) > enrich_limit:
            break
        full = await ms._ma("music/item_by_uri", uri=uri)
        for a in (full.get("artists") or []) if isinstance(full, dict) else []:
            name = _first_artist(a.get("name", "")) if isinstance(a, dict) else ""
            if not name:
                continue
            if name not in counts:
                order.append(name)
            counts[name] = counts.get(name, 0) + 1

    ranked = sorted(order, key=lambda n: -counts[n])

    if len(ranked) < max_artists:
        library = await ms._ma(
            "music/artists/library_items",
            order_by="play_count_desc", limit=max_artists,
        )
        for a in ms._as_list(library):
            if not (a.get("play_count") or 0):
                continue
            name = _first_artist(a.get("name", ""))
            if name and name not in counts:
                ranked.append(name)
                counts[name] = 0

    return ranked[:max_artists]


def build_mood_query(artists: list[str], default_mood: str = "") -> str:
    """The digarr mood/discover query: seeded from real listening when we have
    it, else a generic household-friendly prompt."""
    if artists:
        return (
            "Recommend new artists and albums for a household that has been "
            "listening to: " + ", ".join(artists[:8]) +
            ". Favor variety across the list, not just one artist's sound."
        )
    return default_mood or (
        "Feel-good, family-friendly music discoveries across genres — "
        "a mix of newer artists and overlooked classics."
    )


# ── Recommendations store (local JSON, written by the batch) ─────────────────

def save_recommendations(recs: list[dict[str, Any]], seed: dict[str, Any]) -> Path:
    """Persist the batch output (recommendations + how they were seeded)."""
    RECOMMENDATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": int(time.time()), "seed": seed, "recommendations": recs}
    RECOMMENDATIONS_PATH.write_text(json.dumps(payload, indent=2))
    return RECOMMENDATIONS_PATH


def load_recommendations() -> dict[str, Any]:
    """Last batch output, or {} when no batch has run yet. Never raises."""
    try:
        return json.loads(RECOMMENDATIONS_PATH.read_text())
    except (OSError, ValueError):
        return {}


# ── Playlist bridge (digarr JSON → MA "Zoe Discovery" playlist) ──────────────

def _artist_matches(hit: dict[str, Any], artist: str) -> bool:
    want = artist.casefold()
    for a in hit.get("artists") or []:
        got = (a.get("name", "") if isinstance(a, dict) else "").casefold()
        if got and (want in got or got in want):
            return True
    return False


async def resolve_recommendation_tracks(rec: dict[str, Any],
                                        per_artist: int = 3) -> list[str]:
    """One digarr recommendation → up to `per_artist` playable MA track URIs.

    Searches the configured providers for the suggested album first, then the
    artist alone; keeps only hits whose artist credit actually matches (MA/YT
    search is fuzzy). Returns [] when nothing resolves — caller logs + skips.
    """
    artist = (rec.get("artistName") or rec.get("artist") or "").strip()
    if not artist:
        return []
    album = (rec.get("suggestedAlbum") or rec.get("albumName") or "").strip()
    queries = [f"{artist} {album}"] if album else []
    queries.append(artist)
    uris: list[str] = []
    for q in queries:
        res = await ms._ma("music/search", search_query=q,
                           media_types=["track"], limit=10)
        for hit in (res.get("tracks") or []) if isinstance(res, dict) else []:
            uri = hit.get("uri")
            if uri and uri not in uris and _artist_matches(hit, artist):
                uris.append(uri)
            if len(uris) >= per_artist:
                return uris
        if uris:
            break  # album query resolved something; don't dilute with generic hits
    return uris


async def get_discovery_playlist() -> Optional[dict[str, Any]]:
    """The 'Zoe Discovery' library playlist, or None."""
    items = await ms._ma("music/playlists/library_items",
                         search=DISCOVERY_PLAYLIST_NAME, limit=10)
    for p in ms._as_list(items):
        if (p.get("name") or "").strip().casefold() == DISCOVERY_PLAYLIST_NAME.casefold():
            return p
    return None


async def replace_discovery_playlist(track_uris: list[str]) -> dict[str, Any]:
    """Create-or-replace the 'Zoe Discovery' playlist with `track_uris`.

    MA has no atomic replace: existing tracks are removed by position
    (``remove_playlist_tracks``), then the new set is added
    (``add_playlist_tracks``); both are MA background tasks. Never raises.
    """
    if not track_uris:
        return {"ok": False, "reason": "no resolvable tracks"}
    playlist = await get_discovery_playlist()
    if playlist is None:
        playlist = await ms._ma("music/playlists/create_playlist",
                                name=DISCOVERY_PLAYLIST_NAME)
        if not isinstance(playlist, dict) or not playlist.get("item_id"):
            return {"ok": False, "reason": "could not create playlist"}
    else:
        existing = await ms._ma("music/playlists/playlist_tracks",
                                item_id=str(playlist["item_id"]),
                                provider_instance_id_or_domain="library")
        if existing is None:
            # MA down/timeout mid-replace: adding now would APPEND to the old
            # playlist. Treat the replace as failed instead.
            return {"ok": False, "reason": "could not read existing playlist"}
        positions = [t.get("position") for t in ms._as_list(existing)
                     if isinstance(t, dict) and t.get("position") is not None]
        if positions and not await ms._ma_ok(
                "music/playlists/remove_playlist_tracks", timeout_s=20.0,
                db_playlist_id=str(playlist["item_id"]),
                positions_to_remove=positions):
            return {"ok": False, "reason": "could not clear existing playlist"}
    if not await ms._ma_ok("music/playlists/add_playlist_tracks", timeout_s=30.0,
                           db_playlist_id=str(playlist["item_id"]),
                           uris=track_uris):
        return {"ok": False, "reason": "could not add tracks"}
    return {"ok": True, "playlist_id": str(playlist["item_id"]),
            "uri": playlist.get("uri", ""), "added": len(track_uris)}


# ── Intent surface: "play my discovery playlist" ─────────────────────────────

async def play_discovery(player_id: str = "", zoe_user_id: str = "") -> dict[str, Any]:
    """Play the 'Zoe Discovery' playlist on the target speaker.

    Returns music_service.play_media()'s shape; ok=False with a friendly
    reason when the playlist doesn't exist yet (no batch has run).
    """
    playlist = await get_discovery_playlist()
    if playlist is None or not playlist.get("uri"):
        return {"ok": False, "reason": "no discovery playlist yet"}
    return await ms.play_media(playlist["uri"], player_id=player_id,
                               zoe_user_id=zoe_user_id)

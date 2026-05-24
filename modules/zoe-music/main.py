#!/usr/bin/env python3
"""
Zoe Music Module — Music Assistant bridge
==========================================

Routes music requests through Music Assistant (port 8095) via its REST and
WebSocket APIs.  Falls back to Home Assistant media_player services if MA is
not running.

MCP tools exposed:
  music.play      — play a search query or URI
  music.pause     — pause active player
  music.resume    — resume active player
  music.skip      — next track
  music.previous  — previous track
  music.volume    — set volume (0-100)
  music.now_playing — get current playback state
  music.search    — search MA library
"""
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [zoe-music] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Music Module", version="2.0.0")

MA_URL = os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095")
MA_TOKEN = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
HA_BRIDGE_URL = os.environ.get("ZOE_HA_BRIDGE_URL", "http://localhost:8007")
DEFAULT_PLAYER = os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all")


# ── MA v2 API helpers (POST /api with Bearer auth) ───────────────────────────

def _ma_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if MA_TOKEN:
        h["Authorization"] = f"Bearer {MA_TOKEN}"
    return h


async def _ma_cmd(command: str, args: dict | None = None):
    """Call a Music Assistant v2 JSON-RPC command.

    Format: POST /api {"command": "...", "args": {...}}
    """
    body: dict = {"command": command}
    if args:
        body["args"] = args
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(f"{MA_URL}/api", json=body, headers=_ma_headers())
        if r.status_code == 200:
            return r.json()
        raise HTTPException(r.status_code, f"MA error: {r.text[:200]}")


async def _ma_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{MA_URL}/info")
            return r.status_code == 200 and r.json().get("onboard_done", False)
    except Exception:
        return False


async def _ma_players() -> list:
    result = await _ma_cmd("players/all")
    return result if isinstance(result, list) else []


async def _ma_player_command(player_id: str, cmd: str, extra: dict | None = None):
    """Send command to a player via players/cmd/{cmd}."""
    return await _ma_cmd(f"players/cmd/{cmd}", {"player_id": player_id, **(extra or {})})


async def _ma_play_media(player_id: str, query: str):
    """Search and play via Music Assistant v2 API.

    MA's player_queues/play_media accepts a raw search string as `media`,
    so we can pass the query directly without a separate search call.
    The queue_id matches the player_id for individual players.
    """
    await _ma_cmd("player_queues/play_media", {
        "queue_id": player_id,
        "media": query,
        "radio_mode": True,  # radio mode for continuous playback
        "option": "play",    # play immediately, not enqueue
    })
    return {"query": query, "player_id": player_id}


async def _get_active_player() -> str | None:
    """Return the first playing or first available player ID."""
    try:
        players = await _ma_players()
        for p in players:
            if p.get("state") in ("playing", "paused"):
                return p["player_id"]
        if players:
            return players[0]["player_id"]
    except Exception:
        pass
    return None


# ── HA fallback helper ────────────────────────────────────────────────────

async def _ha_service(service: str, extra: dict | None = None):
    payload = {
        "entity_id": DEFAULT_PLAYER,
        "action": service,
        "data": extra or {},
    }
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(f"{HA_BRIDGE_URL}/devices/control", json=payload)
        r.raise_for_status()


# ── Models ───────────────────────────────────────────────────────────────

class PlayRequest(BaseModel):
    query: str
    player_id: Optional[str] = None


class VolumeRequest(BaseModel):
    level: int  # 0-100
    player_id: Optional[str] = None


class PlayerRequest(BaseModel):
    player_id: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    ma_ok = await _ma_available()
    return {"status": "ok", "music_assistant": "online" if ma_ok else "offline"}


@app.post("/tools/play")
async def tool_play(req: PlayRequest):
    player_id = req.player_id or await _get_active_player()
    if await _ma_available() and player_id:
        track = await _ma_play_media(player_id, req.query)
        return {"ok": True, "track": track}
    # HA fallback
    await _ha_service("play_media", {
        "media_content_id": req.query,
        "media_content_type": "music",
    })
    return {"ok": True, "fallback": "ha"}


@app.post("/tools/pause")
async def tool_pause(req: PlayerRequest):
    pid = req.player_id or await _get_active_player()
    if await _ma_available() and pid:
        await _ma_player_command(pid, "pause")
        return {"ok": True}
    await _ha_service("media_pause")
    return {"ok": True, "fallback": "ha"}


@app.post("/tools/resume")
async def tool_resume(req: PlayerRequest):
    pid = req.player_id or await _get_active_player()
    if await _ma_available() and pid:
        await _ma_player_command(pid, "play")
        return {"ok": True}
    await _ha_service("media_play")
    return {"ok": True, "fallback": "ha"}


@app.post("/tools/skip")
async def tool_skip(req: PlayerRequest):
    pid = req.player_id or await _get_active_player()
    if await _ma_available() and pid:
        await _ma_player_command(pid, "next")
        return {"ok": True}
    await _ha_service("media_next_track")
    return {"ok": True, "fallback": "ha"}


@app.post("/tools/previous")
async def tool_previous(req: PlayerRequest):
    pid = req.player_id or await _get_active_player()
    if await _ma_available() and pid:
        await _ma_player_command(pid, "previous")
        return {"ok": True}
    await _ha_service("media_previous_track")
    return {"ok": True, "fallback": "ha"}


@app.post("/tools/volume")
async def tool_volume(req: VolumeRequest):
    level = max(0, min(100, req.level))
    pid = req.player_id or await _get_active_player()
    if await _ma_available() and pid:
        await _ma_player_command(pid, "volume_set", {"volume_level": level / 100.0})
        return {"ok": True, "level": level}
    await _ha_service("volume_set", {"volume_level": level / 100.0})
    return {"ok": True, "level": level, "fallback": "ha"}


@app.get("/tools/now_playing")
async def tool_now_playing():
    if not await _ma_available():
        return {"ok": False, "reason": "music_assistant_offline"}
    try:
        players = await _ma_players()
        for p in players:
            if p.get("state") == "playing":
                media = p.get("current_media") or {}
                return {
                    "ok": True,
                    "playing": True,
                    "title": media.get("title") or media.get("name"),
                    "artist": media.get("artist") or ", ".join(
                        a["name"] for a in media.get("artists", [])
                    ),
                    "album": (media.get("album") or {}).get("name") if isinstance(media.get("album"), dict) else media.get("album"),
                    "image": media.get("image_url") or media.get("image"),
                    "player": p.get("display_name"),
                    "player_id": p.get("player_id"),
                    "state": p.get("state"),
                    "volume": p.get("volume_level"),
                }
        return {"ok": True, "playing": False}
    except Exception as exc:
        logger.error("now_playing failed: %s", exc)
        return {"ok": False, "reason": str(exc)}


@app.get("/tools/search")
async def tool_search(query: str, limit: int = 10):
    if not await _ma_available():
        raise HTTPException(503, "Music Assistant offline")
    return await _ma_cmd("music/search", {"search_query": query, "limit": limit})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)

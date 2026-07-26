"""
FastAPI router for geocoding (Nominatim proxy).
Mounted at prefix="/api/geo" with tag "geo".

The touch-settings location search used to fetch nominatim.openstreetmap.org
directly from the browser, which the nginx CSP (`connect-src 'self'`) blocks —
and which violated the local-first boundary (browser egress must go through the
backend, where it is guarded and observable). These two endpoints proxy the two
calls the UI needs through `agent_safety.guarded_urlopen` (SSRF-validated,
IP-pinned, proxy-env-ignoring) and pass the Nominatim JSON through unchanged.

Per the OSM Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/):
a valid identifying User-Agent, and at most 1 request/second — enforced here
with a global outbound gap plus a modest per-endpoint window, both answering
429 rather than queueing.
"""
import asyncio
import json
import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query

from agent_safety import guarded_urlopen

router = APIRouter(prefix="/api/geo", tags=["geo"])
logger = logging.getLogger(__name__)

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
USER_AGENT = "zoe-ai-assistant/1.0 (self-hosted; +https://github.com/jason-easyazz/zoe-ai-assistant)"
FETCH_TIMEOUT_S = 8.0
MAX_RESPONSE_BYTES = 512 * 1024

# OSM policy: absolute maximum of 1 request/second — enforced as a minimum gap
# between outbound calls (shared across both endpoints; Nominatim is one host).
_MIN_OUTBOUND_GAP_S = 1.0
_last_outbound_at = 0.0

# Modest per-endpoint window on top of the gap (panel_provision.py pattern):
# location search is one request per user interaction, so this is generous.
_RATE_LIMIT_MAX = 30
_RATE_LIMIT_WINDOW_S = 60.0
_rate_limit: dict[str, list[float]] = {}


def _check_rate_limit(endpoint: str) -> None:
    """429 when this endpoint exceeds its window or the shared 1 req/s gap."""
    global _last_outbound_at
    now = time.monotonic()
    timestamps = [t for t in _rate_limit.get(endpoint, []) if t > now - _RATE_LIMIT_WINDOW_S]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many geocoding requests. Try again shortly.")
    if now - _last_outbound_at < _MIN_OUTBOUND_GAP_S:
        raise HTTPException(status_code=429, detail="Geocoding rate limit (1 request/second). Try again shortly.")
    timestamps.append(now)
    _rate_limit[endpoint] = timestamps
    _last_outbound_at = now


def _fetch_nominatim(path: str, params: dict) -> object:
    """Blocking guarded fetch of one Nominatim endpoint; parsed JSON or raises."""
    url = f"{NOMINATIM_BASE}/{path}?{urlencode(params)}"
    with guarded_urlopen(url, timeout=FETCH_TIMEOUT_S, headers={"User-Agent": USER_AGENT}) as resp:
        body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"nominatim response exceeds {MAX_RESPONSE_BYTES} byte cap")
    return json.loads(body)


async def _proxy(endpoint: str, path: str, params: dict, expected_type: type) -> object:
    _check_rate_limit(endpoint)
    try:
        data = await asyncio.to_thread(_fetch_nominatim, path, params)
    except Exception:  # incl. HTTPError/URLError/timeout/SSRFBlocked/bad JSON
        logger.warning("nominatim %s fetch failed", path, exc_info=True)
        raise HTTPException(status_code=502, detail="Geocoding provider unavailable")
    if not isinstance(data, expected_type):
        logger.warning("nominatim %s returned unexpected payload type %s", path, type(data).__name__)
        raise HTTPException(status_code=502, detail="Geocoding provider unavailable")
    return data


@router.get("/search")
async def geo_search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(5, ge=1, le=10),
):
    """Forward geocode a free-text query. Passes the Nominatim result array
    through unchanged (the touch-settings UI reads `address`/`lat`/`lon`)."""
    params = {"format": "json", "q": q, "limit": limit, "addressdetails": 1}
    return await _proxy("search", "search", params, list)


@router.get("/reverse")
async def geo_reverse(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
):
    """Reverse geocode coordinates. Passes the Nominatim result object through
    unchanged (the touch-settings UI reads `address`)."""
    params = {"format": "json", "lat": lat, "lon": lon, "addressdetails": 1}
    return await _proxy("reverse", "reverse", params, dict)

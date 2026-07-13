#!/usr/bin/env python3
"""Weekly music-discovery batch: ephemeral digarr → "Zoe Discovery" MA playlist.

digarr (labs/digarr-spike/ — hidden engine, family never sees it) runs as a
BATCH-ONLY docker container against the local Gemma llama-server (:11434,
OpenAI-compatible, base URL WITHOUT /v1), proposes new artists/albums via one
mood/discover call seeded from Music Assistant's own play history (fully
local — no cloud scrobbler), and is stopped + removed before this script
exits. Results land in services/zoe-data/data/music_discovery/ and are
bridged into the "Zoe Discovery" playlist through MA's playlist API
(digarr's M3U export is Spotify-URL entries MA cannot play — spike finding).

MANUAL FIRST RUN (operator, in an idle voice window):
    python3 scripts/maintenance/music_discovery_batch.py
(MA URL/token auto-load from services/zoe-data/.env; digarr credentials are
generated on first run and kept in the batch data dir.)

Weekly cadence: registered in services/zoe-data/main.py behind
ZOE_MUSIC_DISCOVERY=on (default off) — flip only after a verified manual run.

GATES (memory-tight Jetson, single brain slot — never regress voice):
- aborts unless MemAvailable >= ZOE_DISCOVERY_MIN_FREE_MB (default 1500 MB);
- aborts when the brain is busy (llama-server /slots is_processing; falls
  back to /health when /slots is unavailable);
- the container is stopped AND removed in all paths; exits non-zero if it
  is still present afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = REPO_ROOT / "services" / "zoe-data"
sys.path.append(str(ZOE_DATA))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("music_discovery_batch")

CONTAINER_NAME = "zoe-digarr-batch"
DIGARR_PORT = int(os.environ.get("ZOE_DIGARR_PORT", "3199"))
DIGARR_URL = f"http://127.0.0.1:{DIGARR_PORT}"
def _brain_base_url() -> str:
    """The llama-server BASE url (no /v1) for probes AND the container.

    Zoe's brain URL convention sometimes carries /v1 (e.g. GEMMA_SERVER_URL);
    strip it so /health + /slots probe the server root and digarr — which
    appends /v1/... itself (spike gotcha) — gets a clean base."""
    url = os.environ.get("ZOE_BRAIN_URL", "http://127.0.0.1:11434").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


LLAMA_URL = _brain_base_url()


def _container_ai_base_url() -> str:
    """LLAMA_URL as seen from inside the digarr container: loopback/localhost
    become host.docker.internal (mapped via --add-host host-gateway) with the
    same port; anything else (a real LAN address) passes through. Override:
    ZOE_DIGARR_AI_BASE_URL."""
    override = os.environ.get("ZOE_DIGARR_AI_BASE_URL", "").rstrip("/")
    if override:
        return override
    from urllib.parse import urlparse
    parsed = urlparse(LLAMA_URL)
    if parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0"):  # noqa: S104
        port = parsed.port or 80
        return f"{parsed.scheme}://host.docker.internal:{port}"
    return LLAMA_URL
DATA_DIR = ZOE_DATA / "data" / "music_discovery"
AUTH_PATH = DATA_DIR / "digarr_auth.json"


def load_env_defaults() -> None:
    """Fill unset env vars from services/zoe-data/.env (never overrides)."""
    env_file = ZOE_DATA / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())
    except OSError:
        pass


def _http_json(method: str, url: str, payload: Optional[dict] = None,
               headers: Optional[dict] = None, timeout: float = 30.0) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback only
        body = resp.read()
    return json.loads(body) if body else None


# ── Gates ─────────────────────────────────────────────────────────────────────

def parse_meminfo_available_mb(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def check_memory_gate(min_free_mb: int) -> bool:
    avail = parse_meminfo_available_mb(Path("/proc/meminfo").read_text())
    if avail < min_free_mb:
        log.error("ABORT: only %d MB available (< %d MB gate)", avail, min_free_mb)
        return False
    log.info("Memory gate ok: %d MB available", avail)
    return True


def check_brain_idle() -> bool:
    """The single brain slot must be idle — discovery must never queue behind
    (or in front of) a voice turn. /slots is authoritative; /health-only when
    the server doesn't expose slots."""
    try:
        _http_json("GET", f"{LLAMA_URL}/health", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        log.error("ABORT: brain health probe failed (%s)", exc)
        return False
    try:
        slots = _http_json("GET", f"{LLAMA_URL}/slots", timeout=5.0)
    except urllib.error.HTTPError as exc:
        log.warning("brain /slots unavailable (HTTP %s) — proceeding on /health only", exc.code)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("brain /slots probe failed (%s) — proceeding on /health only", exc)
        return True
    busy = [s for s in slots or [] if s.get("is_processing")]
    if busy:
        log.error("ABORT: brain busy (%d slot(s) processing) — back off, don't queue", len(busy))
        return False
    log.info("Brain idle gate ok")
    return True


# ── digarr container lifecycle ────────────────────────────────────────────────

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def digarr_credentials() -> tuple[str, str]:
    user = os.environ.get("ZOE_DIGARR_USER", "zoe")
    password = os.environ.get("ZOE_DIGARR_PASSWORD", "")
    if not password:
        try:
            password = json.loads(AUTH_PATH.read_text())["password"]
        except (OSError, ValueError, KeyError):
            password = secrets.token_urlsafe(18)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            AUTH_PATH.write_text(json.dumps({"username": user, "password": password}))
            AUTH_PATH.chmod(0o600)
    return user, password


def brain_model_id() -> str:
    """The model id llama-server reports (digarr sends it; llama ignores it)."""
    override = os.environ.get("ZOE_DIGARR_AI_MODEL", "")
    if override:
        return override
    try:
        models = _http_json("GET", f"{LLAMA_URL}/v1/models", timeout=5.0)
        return models["data"][0]["id"]
    except Exception:  # noqa: BLE001
        return "local-gemma"


def start_digarr() -> bool:
    user, password = digarr_credentials()
    data_vol = DATA_DIR / "digarr-data"
    backup_vol = DATA_DIR / "digarr-backups"
    data_vol.mkdir(parents=True, exist_ok=True)
    backup_vol.mkdir(parents=True, exist_ok=True)
    image = os.environ.get("ZOE_DIGARR_IMAGE", "docker.io/iuliandita/digarr:latest")
    _run(["docker", "rm", "-f", CONTAINER_NAME])  # stale leftover from a crashed run
    cmd = [
        "docker", "run", "-d", "--name", CONTAINER_NAME,
        "--user", "1000:1000",
        "-p", f"127.0.0.1:{DIGARR_PORT}:3000",
        "-e", "PORT=3000", "-e", "DB_PATH=/app/data",
        "-e", "AI_PROVIDER=openai-compatible",
        # NOTE: no /v1 — digarr appends /v1/chat/completions itself (spike gotcha)
        "-e", f"AI_BASE_URL={_container_ai_base_url()}",
        "-e", "AI_API_KEY=local-noauth",
        "-e", f"AI_MODEL={brain_model_id()}",
        "-e", f"DIGARR_AI_TIMEOUT_SECONDS={os.environ.get('ZOE_DIGARR_AI_TIMEOUT_S', '300')}",
        "-e", f"DIGARR_INITIAL_USERNAME={user}",
        "-e", f"DIGARR_INITIAL_PASSWORD={password}",
        "--add-host", "host.docker.internal:host-gateway",
        "--memory", "768m", "--cpus", "2",
        "-v", f"{data_vol}:/app/data", "-v", f"{backup_vol}:/app/backups",
        image,
    ]
    res = _run(cmd)
    if res.returncode != 0:
        log.error("docker run failed: %s", res.stderr.strip())
        return False
    log.info("digarr container started (%s)", res.stdout.strip()[:12])
    return True


def wait_digarr_ready(timeout_s: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _http_json("GET", f"{DIGARR_URL}/health", timeout=3.0)
            log.info("digarr API ready")
            return True
        except Exception:  # noqa: BLE001
            time.sleep(2.0)
    log.error("digarr did not become ready within %.0fs", timeout_s)
    return False


def stop_digarr() -> bool:
    """Stop + remove the container; True only if it is really gone.
    MEMORY-TIGHT box: a leaked 768MB container is an incident, not a warning."""
    _run(["docker", "stop", "-t", "10", CONTAINER_NAME])
    _run(["docker", "rm", "-f", CONTAINER_NAME])
    res = _run(["docker", "ps", "-a", "--filter", f"name=^{CONTAINER_NAME}$",
                "--format", "{{.Names}}"])
    if res.stdout.strip():
        log.error("digarr container STILL PRESENT after cleanup — remove it manually")
        return False
    log.info("digarr container stopped and removed")
    return True


# ── Discovery ────────────────────────────────────────────────────────────────

def digarr_login() -> str:
    user, password = digarr_credentials()
    res = _http_json("POST", f"{DIGARR_URL}/api/v1/auth/login",
                     {"username": user, "password": password})
    return res["token"]


def run_mood_discovery(token: str, query: str) -> list[dict[str, Any]]:
    log.info("mood/discover query: %s", query)
    ai_timeout = float(os.environ.get("ZOE_DIGARR_AI_TIMEOUT_S", "300"))
    res = _http_json("POST", f"{DIGARR_URL}/api/v1/mood/discover",
                     {"query": query},
                     headers={"Authorization": f"Bearer {token}"},
                     timeout=ai_timeout + 30)
    return res.get("results") or []


async def build_seed() -> tuple[str, dict[str, Any]]:
    import music_discovery  # deferred: needs services/zoe-data on sys.path

    artists = await music_discovery.taste_seed()
    query = build_query_override() or music_discovery.build_mood_query(
        artists, os.environ.get("ZOE_DISCOVERY_DEFAULT_MOOD", ""))
    return query, {"artists": artists, "query": query}


def build_query_override() -> str:
    return getattr(build_query_override, "_override", "") or ""


async def bridge_to_playlist(recs: list[dict[str, Any]],
                             per_artist: int) -> dict[str, Any]:
    import music_discovery

    uris: list[str] = []
    skipped: list[str] = []
    for rec in recs:
        artist = rec.get("artistName") or rec.get("artist") or "?"
        tracks = await music_discovery.resolve_recommendation_tracks(rec, per_artist)
        if tracks:
            uris.extend(tracks)
            log.info("resolved %-30s -> %d track(s)", artist, len(tracks))
        else:
            skipped.append(artist)
            log.info("unresolvable in MA (skipped): %s", artist)
    result = await music_discovery.replace_discovery_playlist(uris)
    result["skipped"] = skipped
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mood", default="", help="override the discovery query")
    ap.add_argument("--limit", type=int, default=12, help="max recommendations bridged")
    ap.add_argument("--per-artist", type=int, default=3, help="tracks per recommendation")
    ap.add_argument("--no-bridge", action="store_true",
                    help="stop after writing recommendations JSON (no MA playlist)")
    args = ap.parse_args()

    # SIGTERM (scheduler timeout, systemd stop) must still run the `finally`
    # container cleanup — Python's default SIGTERM handling skips it.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(4))

    load_env_defaults()
    if args.mood:
        build_query_override._override = args.mood  # type: ignore[attr-defined]

    min_free = int(os.environ.get("ZOE_DISCOVERY_MIN_FREE_MB", "1500"))
    if not check_memory_gate(min_free) or not check_brain_idle():
        return 2

    import music_discovery  # after sys.path bootstrap + env load

    started = False
    try:
        query, seed = asyncio.run(build_seed())
        if not start_digarr():
            return 1
        started = True
        if not wait_digarr_ready():
            return 1
        token = digarr_login()
        recs = run_mood_discovery(token, query)[: args.limit]
        if not recs:
            log.error("digarr returned no recommendations")
            return 1
        path = music_discovery.save_recommendations(recs, seed)
        log.info("wrote %d recommendation(s) to %s", len(recs), path)
        # Free the container (and its RAM) BEFORE the MA bridge — the bridge
        # only talks to MA and can take a while resolving tracks.
        if stop_digarr():
            started = False
        if args.no_bridge:
            return 0
        result = asyncio.run(bridge_to_playlist(recs, args.per_artist))
        if not result.get("ok"):
            log.error("playlist bridge failed: %s", result.get("reason"))
            return 1
        log.info("'%s' playlist updated: %d tracks (skipped: %s)",
                 music_discovery.DISCOVERY_PLAYLIST_NAME, result["added"],
                 ", ".join(result["skipped"]) or "none")
        return 0
    except Exception:  # noqa: BLE001 — log the traceback, still clean up
        log.exception("discovery batch failed")
        return 1
    finally:
        if started and not stop_digarr():
            # container leak on a memory-tight box — make the failure loud
            sys.exit(3)


if __name__ == "__main__":
    sys.exit(main())

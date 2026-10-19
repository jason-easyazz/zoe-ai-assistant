#!/usr/bin/env python3
"""Zoe Panel Agent — unified Raspberry Pi touch panel daemon.

Responsibilities
----------------
1. Backlight control via /sys/class/backlight/<device>/brightness.
2. Idle detection via xprintidle (X11, works on the Pi kiosk).
3. Night-schedule dim, idle dim, and screen-off rules (ramped transitions).
4. Tiny aiohttp HTTP server on 0.0.0.0:8765 for the rest of the system:
      GET  /info          -> versions, device id, git short sha
      GET  /health        -> {ok: true, uptime_s, backlight_device}
      GET  /state         -> current brightness + decided mode
      POST /wake          -> cancel any dim/off, ramp to day-brightness
      POST /reload        -> re-fetch preferences from the Zoe API now
      POST /brightness    -> {"value": 0-100} — manual override for UI previews
5. Polls GET {ZOE_API}/api/system/display/preferences every 10s with a local
   config.json fallback if the API is unreachable or the payload is invalid.
6. Safety: always restore day-brightness on SIGTERM/SIGINT/exit.

Who may call it (2026-10-19; before this the port answered ANY caller on the
network with no auth, and the source lived only on the Pi)
---------------------------------------------------------------------------
``request_allowed`` decides, before any handler runs:

- the kiosk on THIS machine (a loopback TCP peer AND a loopback ``Host`` - the
  second refuses DNS rebinding through the kiosk browser): every route. The
  kiosk's ``touch-menu.js`` POSTs ``http://127.0.0.1:8765/wake``.
- the Zoe server (``AGENT_ALLOWED_PEERS``, default: the host of ``ZOE_API``):
  ``POST /wake`` (auth hand-off), ``POST /reload`` (display settings saved) and
  ``POST /brightness`` (settings preview) - the three zoe-data calls - plus
  ``GET /health``.
- anyone else: ``GET /health`` only. ``/state`` and ``/info`` are loopback-only
  (``/state`` carries ``idle_ms``, i.e. whether someone is at the panel).

The server must still bind every interface because the Jetson POSTs over the
LAN. A TCP peer address cannot be forged without being on-path (ARP spoofing on
the home LAN is the accepted residual); there is no shared secret because zoe-data
holds only token hashes. Rollback: ``AGENT_GUARD=off`` (logged loudly at start).

Environment
-----------
ZOE_API               -> https://192.168.1.218 (default)
ZOE_DEVICE_ID         -> stable device id (default: first mac addr)
BACKLIGHT_DEVICE      -> /sys/class/backlight/<name> (auto-detect if unset)
AGENT_PORT            -> 8765
AGENT_ALLOWED_PEERS   -> comma-separated addresses of the Zoe server (default:
                         the address ZOE_API's host resolves to)
AGENT_GUARD           -> on (default) | off (rollback: any caller, as before)
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

try:
    from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
except ImportError:  # the access policy below is importable without it (unit tests); main() still needs it
    ClientSession = ClientTimeout = TCPConnector = web = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VERSION = "1.1.0"
AGENT_PORT = int(os.environ.get("AGENT_PORT", "8765"))
ZOE_API = os.environ.get("ZOE_API", "https://192.168.1.218")


# ---------------------------------------------------------------------------
# Access policy (2026-10-19) - see the module docstring
# ---------------------------------------------------------------------------

#: what the Zoe server calls (zoe-data: auth_handoff._wake_panel, routers/system.py reload + brightness preview + volume;
#: this agent serves no /volume - it answers 404 and zoe-data falls back to amixer, exactly as before the guard)
SERVER_ROUTES = frozenset({("POST", "/wake"), ("POST", "/reload"), ("POST", "/brightness"), ("POST", "/volume"),
                           ("GET", "/health")})
#: what anyone on the network may read
PUBLIC_ROUTES = frozenset({("GET", "/health")})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr or "").split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _host_name(host_header: Optional[str]) -> str:
    h = str(host_header or "").strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def guard_enabled() -> bool:
    return os.environ.get("AGENT_GUARD", "on").strip().lower() not in ("off", "0", "false", "no")


def allowed_peers(env: Optional[dict] = None, resolve=socket.getaddrinfo) -> frozenset:
    """``AGENT_ALLOWED_PEERS``, else the address(es) ``ZOE_API``'s host resolves to. Never loopback (that is the kiosk's rule)."""
    env = os.environ if env is None else env
    raw = str(env.get("AGENT_ALLOWED_PEERS", "") or "").strip()
    out = set()
    if raw:
        out = {p.strip() for p in raw.split(",") if p.strip()}
    else:
        host = urlsplit(str(env.get("ZOE_API", ZOE_API) or "")).hostname or ""
        if host:
            try:
                ipaddress.ip_address(host)
                out = {host}
            except ValueError:
                try:
                    out = {str(ai[4][0]) for ai in resolve(host, None)}
                except OSError:
                    out = set()
    return frozenset(p for p in out if not _is_loopback(p))


def request_allowed(method: str, path: str, peer: Optional[str], host_header: Optional[str],
                    server_peers: Iterable[str], guard: bool = True) -> bool:
    """May this request reach a handler? Pure (unit-tested in tests/unit/test_panel_agent_access.py)."""
    if not guard:
        return True
    route = (str(method or "").upper(), str(path or ""))
    if route in PUBLIC_ROUTES:
        return True
    peer = _norm(peer)
    if peer and _is_loopback(peer):
        return _host_name(host_header) in _LOOPBACK_HOSTS        # the kiosk on this machine; a rebound name is refused
    if peer and peer in {_norm(p) for p in server_peers}:
        return route in SERVER_ROUTES
    return False


def _norm(addr: Optional[str]) -> str:
    """An address as a plain string, an IPv4-mapped IPv6 peer (``::ffff:a.b.c.d``) as its IPv4 form."""
    a = str(addr or "").strip()
    try:
        ip = ipaddress.ip_address(a.split("%", 1)[0])
    except ValueError:
        return a
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)
POLL_INTERVAL_S = 10  # preferences sync cadence
IDLE_TICK_S = 2       # how often we re-evaluate idle/night state
RAMP_STEPS = 12       # steps in a brightness ramp (~600ms total)
RAMP_SLEEP = 0.05

LOG = logging.getLogger("zoe-panel-agent")
CONFIG_DIR = Path.home() / ".zoe-panel-agent"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_PREFS = {
    "enabled": True,
    "day_brightness": 100,
    # Night schedule
    "night_enabled": True,
    "night_start": "22:00",
    "night_end": "06:30",
    "night_brightness": 15,
    # Idle dim
    "idle_enabled": True,
    "idle_seconds": 120,
    "idle_brightness": 30,
    # Screen off
    "off_enabled": True,
    "off_seconds": 900,
}


def device_id() -> str:
    if "ZOE_DEVICE_ID" in os.environ:
        return os.environ["ZOE_DEVICE_ID"]
    mac = uuid.getnode()
    return f"pi-{mac:012x}"


def detect_backlight() -> Optional[Path]:
    if "BACKLIGHT_DEVICE" in os.environ:
        p = Path(os.environ["BACKLIGHT_DEVICE"])
        if p.exists():
            return p
    base = Path("/sys/class/backlight")
    if not base.exists():
        return None
    candidates = sorted(base.iterdir())
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Backlight driver
# ---------------------------------------------------------------------------


class Backlight:
    """Thin wrapper around sysfs backlight. Percentage in [0, 100]."""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self.max = 1
        if path:
            try:
                self.max = int((path / "max_brightness").read_text().strip())
            except Exception as exc:  # noqa: BLE001
                LOG.warning("backlight max_brightness unreadable: %s", exc)
                self.max = 255
        self._current_pct = 100

    def available(self) -> bool:
        return self.path is not None

    def _write_raw(self, raw: int) -> None:
        if not self.path:
            return
        raw = max(0, min(self.max, raw))
        try:
            (self.path / "brightness").write_text(str(raw))
        except PermissionError:
            # Fallback via pkexec/sudo when udev rule missing.
            try:
                subprocess.run(
                    ["sudo", "-n", "tee", str(self.path / "brightness")],
                    input=str(raw),
                    text=True,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("brightness write failed: %s", exc)

    def set_pct(self, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        raw = int(round(self.max * pct / 100.0))
        self._write_raw(raw)
        self._current_pct = pct

    async def ramp_to(self, target_pct: int) -> None:
        target_pct = max(0, min(100, int(target_pct)))
        start = self._current_pct
        if target_pct == start:
            return
        for i in range(1, RAMP_STEPS + 1):
            pct = int(round(start + (target_pct - start) * (i / RAMP_STEPS)))
            self.set_pct(pct)
            await asyncio.sleep(RAMP_SLEEP)
        self.set_pct(target_pct)

    @property
    def current_pct(self) -> int:
        return self._current_pct


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


def read_idle_ms() -> int:
    """Return milliseconds since last input. Returns 0 if unavailable."""
    for cmd in (["xprintidle"],):
        try:
            env = os.environ.copy()
            env.setdefault("DISPLAY", ":0")
            env.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))
            out = subprocess.check_output(cmd, env=env, stderr=subprocess.DEVNULL)
            return int(out.decode().strip())
        except Exception:
            continue
    return 0


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------


def _parse_hm(s: str) -> tuple[int, int]:
    try:
        hh, mm = s.split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except Exception:
        return 22, 0


def is_night(now_struct, start: str, end: str) -> bool:
    sh, sm = _parse_hm(start)
    eh, em = _parse_hm(end)
    cur = now_struct.tm_hour * 60 + now_struct.tm_min
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if start_min == end_min:
        return False
    if start_min < end_min:  # same day window
        return start_min <= cur < end_min
    # wraps midnight
    return cur >= start_min or cur < end_min


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class PanelController:
    def __init__(self) -> None:
        self.prefs = dict(DEFAULT_PREFS)
        self._load_local_prefs()
        self.backlight = Backlight(detect_backlight())
        self.device_id = device_id()
        self.started = time.time()
        self._manual_wake_until = 0.0  # epoch s; while >now, force day brightness
        self._last_mode = "day"
        self._screen_off = False

    def _load_local_prefs(self) -> None:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                self.prefs.update({k: v for k, v in data.items() if k in DEFAULT_PREFS})
            except Exception as exc:  # noqa: BLE001
                LOG.warning("could not read local prefs: %s", exc)

    def _save_local_prefs(self) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps(self.prefs, indent=2))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("could not save local prefs: %s", exc)

    def apply_prefs(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        changed = False
        for key, default in DEFAULT_PREFS.items():
            if key in payload and payload[key] is not None:
                self.prefs[key] = payload[key]
                changed = True
        if changed:
            self._save_local_prefs()

    def decide_target(self) -> tuple[int, str, bool]:
        """Return (target_brightness_pct, mode_label, should_screen_off)."""
        prefs = self.prefs
        now_s = time.time()

        if not prefs.get("enabled", True):
            return prefs.get("day_brightness", 100), "disabled", False

        if now_s < self._manual_wake_until:
            return prefs.get("day_brightness", 100), "wake", False

        idle_ms = read_idle_ms()
        idle_s = idle_ms / 1000.0 if idle_ms else 0.0

        if prefs.get("off_enabled", True) and idle_s >= prefs.get("off_seconds", 900):
            return 0, "off", True

        if prefs.get("idle_enabled", True) and idle_s >= prefs.get("idle_seconds", 120):
            return int(prefs.get("idle_brightness", 30)), "idle", False

        if prefs.get("night_enabled", True) and is_night(
            time.localtime(now_s),
            prefs.get("night_start", "22:00"),
            prefs.get("night_end", "06:30"),
        ):
            return int(prefs.get("night_brightness", 15)), "night", False

        return int(prefs.get("day_brightness", 100)), "day", False

    async def tick(self) -> None:
        target, mode, off = self.decide_target()
        if mode != self._last_mode:
            LOG.info("mode %s -> %s (target=%d%%)", self._last_mode, mode, target)
            self._last_mode = mode
        if off != self._screen_off:
            self._screen_off = off
        if target != self.backlight.current_pct:
            await self.backlight.ramp_to(target)

    async def wake(self, hold_s: float = 10.0) -> None:
        """Force day-brightness for `hold_s` seconds."""
        self._manual_wake_until = max(self._manual_wake_until, time.time() + hold_s)
        LOG.info("wake: forcing day brightness for %.1fs", hold_s)
        await self.backlight.ramp_to(int(self.prefs.get("day_brightness", 100)))

    def manual_brightness(self, pct: int) -> None:
        """Immediate brightness for UI previews (does not persist)."""
        self.backlight.set_pct(int(pct))


# ---------------------------------------------------------------------------
# Remote preferences sync
# ---------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def prefs_sync_loop(controller: PanelController) -> None:
    url = f"{ZOE_API.rstrip('/')}/api/system/display/preferences?device_id={controller.device_id}"
    timeout = ClientTimeout(total=6)
    connector = TCPConnector(ssl=_ssl_context())
    async with ClientSession(timeout=timeout, connector=connector) as session:
        while True:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        payload = await resp.json(content_type=None)
                        if isinstance(payload, dict) and "preferences" in payload:
                            controller.apply_prefs(payload["preferences"])
                        elif isinstance(payload, dict):
                            controller.apply_prefs(payload)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("prefs fetch failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Control loop
# ---------------------------------------------------------------------------


async def control_loop(controller: PanelController) -> None:
    while True:
        try:
            await controller.tick()
        except Exception as exc:  # noqa: BLE001
            LOG.exception("tick error: %s", exc)
        await asyncio.sleep(IDLE_TICK_S)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


def build_app(controller: PanelController, server_peers: Iterable[str] = (), guard: bool = True) -> web.Application:
    server_peers = frozenset(server_peers)

    @web.middleware
    async def access_guard(req: web.Request, handler):
        if not request_allowed(req.method, req.path, req.remote, req.headers.get("Host"), server_peers, guard):
            LOG.warning("refused %s %s from %s", req.method, req.path, req.remote)
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)
        return await handler(req)

    app = web.Application(middlewares=[access_guard])

    async def handle_info(_req: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "agent": "zoe-panel-agent",
            "version": VERSION,
            "device_id": controller.device_id,
            "backlight": str(controller.backlight.path) if controller.backlight.path else None,
            "zoe_api": ZOE_API,
        })

    async def handle_health(_req: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "uptime_s": int(time.time() - controller.started),
            "backlight_available": controller.backlight.available(),
        })

    async def handle_state(_req: web.Request) -> web.Response:
        target, mode, off = controller.decide_target()
        return web.json_response({
            "mode": mode,
            "target_pct": target,
            "current_pct": controller.backlight.current_pct,
            "screen_off": off,
            "idle_ms": read_idle_ms(),
            "prefs": controller.prefs,
        })

    async def handle_wake(req: web.Request) -> web.Response:
        hold = 10.0
        if req.body_exists:
            try:
                data = await req.json()
                hold = float(data.get("hold_s", hold))
            except Exception:
                pass
        await controller.wake(hold_s=hold)
        return web.json_response({"ok": True, "wake_hold_s": hold})

    async def handle_reload(_req: web.Request) -> web.Response:
        # Fire a one-shot fetch so settings UI updates propagate instantly.
        url = f"{ZOE_API.rstrip('/')}/api/system/display/preferences?device_id={controller.device_id}"
        ok = False
        try:
            timeout = ClientTimeout(total=4)
            connector = TCPConnector(ssl=_ssl_context())
            async with ClientSession(timeout=timeout, connector=connector) as s:
                async with s.get(url) as resp:
                    if resp.status == 200:
                        payload = await resp.json(content_type=None)
                        if isinstance(payload, dict) and "preferences" in payload:
                            controller.apply_prefs(payload["preferences"])
                            ok = True
                        elif isinstance(payload, dict):
                            controller.apply_prefs(payload)
                            ok = True
        except Exception as exc:  # noqa: BLE001
            LOG.warning("reload fetch failed: %s", exc)
        await controller.tick()
        return web.json_response({"ok": ok, "prefs": controller.prefs})

    async def handle_brightness(req: web.Request) -> web.Response:
        try:
            data = await req.json()
            pct = int(data.get("value", 100))
        except Exception:
            return web.json_response({"ok": False, "error": "bad payload"}, status=400)
        controller.manual_brightness(pct)
        return web.json_response({"ok": True, "value": pct})

    app.router.add_get("/info", handle_info)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/state", handle_state)
    app.router.add_post("/wake", handle_wake)
    app.router.add_post("/reload", handle_reload)
    app.router.add_post("/brightness", handle_brightness)
    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    controller = PanelController()
    LOG.info(
        "zoe-panel-agent %s starting (device=%s backlight=%s api=%s)",
        VERSION,
        controller.device_id,
        controller.backlight.path,
        ZOE_API,
    )
    # Safety: restore day-brightness on exit.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(*_a) -> None:
        LOG.info("shutdown signal received")
        try:
            controller.backlight.set_pct(int(controller.prefs.get("day_brightness", 100)))
        except Exception:
            pass
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown())

    guard = guard_enabled()
    peers = allowed_peers()
    if not guard:
        LOG.warning("AGENT_GUARD=off: every route answers ANY caller that reaches port %d (rollback only)", AGENT_PORT)
    else:
        LOG.info("access: kiosk on loopback = all routes; Zoe server %s = /wake /reload /brightness; others = /health",
                 ",".join(sorted(peers)) or "(none resolved - only the kiosk can wake the screen)")
    app = build_app(controller, peers, guard)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", AGENT_PORT)
    await site.start()
    LOG.info("HTTP listening on 0.0.0.0:%d", AGENT_PORT)

    tasks = [
        asyncio.create_task(control_loop(controller)),
        asyncio.create_task(prefs_sync_loop(controller)),
    ]
    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        try:
            await runner.cleanup()
        except Exception:
            pass
        try:
            controller.backlight.set_pct(int(controller.prefs.get("day_brightness", 100)))
        except Exception:
            pass


def main() -> None:
    if web is None:
        sys.stderr.write(
            "[zoe-panel-agent] aiohttp missing. Install with:\n"
            "    pip install --user --break-system-packages aiohttp\n"
        )
        raise SystemExit(1)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

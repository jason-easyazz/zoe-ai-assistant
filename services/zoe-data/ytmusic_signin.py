"""ytmusic_signin — one-tap, phone-driven YouTube Music sign-in for Zoe.

Promoted from the ``labs/ytmusic-signin`` spike (rig.py + harvest.py + common.py)
into a managed production service. YouTube Music has no username/password and no
OAuth — the only way to link it is a browser login cookie (which must contain
``__Secure-3PAPISID``) plus Zoe's local PO-token generator + a YT Music Premium
account. This module presents a remote browser the user signs into *themselves*,
auto-detects the login, harvests the resulting cookie, hands it to Music
Assistant's ``ytmusic`` provider, and tears the browser down.

Security model (non-negotiable — mirrors the lab's Forbidden contract):
  * NEVER enter, request, autofill, store, or log a Google password. This module
    only ever READS the cookie the browser already holds after the human signed
    in themselves. The password only touches Google.
  * The harvested cookie is a SECRET: it is only ever handed to Music Assistant
    (which persists it) — never committed, never written to a tracked file, and
    never logged in full (see ``_redact``). The persistent Chromium profile lives
    under ``$ZOE_YTMUSIC_SECRET_DIR`` (default ``~/.zoe-ytmusic/``, mode 0700),
    OUTSIDE the repo.
  * ONE sign-in session at a time. Raw VNC binds to localhost only; only the
    noVNC/websockify port is LAN-bound. The browser is transient: it is torn
    down on completion, on timeout (~5 min), and on any error — it must never
    outlive the session.
  * RAM-aware (Jetson Orin, 16GB): the sign-in browser exists only during setup;
    the refresh path opens the profile HEADLESS, harvests, and closes promptly —
    never a resident Chromium.

The router (``routers/music_setup.py``) gates ``start_session`` behind the
one-time setup token; this module owns the rig lifecycle + the harvest→save→
teardown state machine + the anti-expiry ``refresh_now`` path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import music_service

logger = logging.getLogger(__name__)

# ── The cookie YouTube Music auth hinges on ──────────────────────────────────
# MA's ytmusic provider rejects a cookie that lacks this key (it is HttpOnly, so
# page JS can never read it — but the browser owner can, which is the whole point).
REQUIRED_COOKIE = "__Secure-3PAPISID"

# Cookies live across these registrable domains after a YTMusic login. We collect
# from all of them and assemble a single Cookie header (what ytmusic-api sends).
AUTH_DOMAINS = (".youtube.com", ".google.com", "youtube.com", "google.com")

# ── Ports / display (override via env; single session so fixed defaults are OK) ─
_DISPLAY = os.environ.get("ZOE_RIG_DISPLAY", ":99")
_XVFB_GEOMETRY = os.environ.get("ZOE_RIG_GEOMETRY", "1280x800x24")
_VNC_PORT = int(os.environ.get("ZOE_RIG_VNC_PORT", "5900"))
_NOVNC_PORT = int(os.environ.get("ZOE_RIG_NOVNC_PORT", "6080"))

# Login on accounts.google.com, continue into YTMusic so __Secure-3PAPISID is
# minted on the youtube.com domain.
_LOGIN_URL = os.environ.get(
    "ZOE_RIG_LOGIN_URL",
    "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmusic.youtube.com%2F",
)

# UA: default None => CloakBrowser's own coherent stealth UA (mismatched UA is a
# fingerprint tell). Override only deliberately.
_USER_AGENT = os.environ.get("ZOE_RIG_USER_AGENT") or None

# ── Secret + profile paths (OUTSIDE the repo, never committed) ────────────────
SECRET_DIR = Path(os.environ.get("ZOE_YTMUSIC_SECRET_DIR", str(Path.home() / ".zoe-ytmusic")))
PROFILE_DIR = SECRET_DIR / "profile"          # persistent Chromium user-data-dir
_USERNAME_FILE = SECRET_DIR / "username"      # last connected account label (0600)

# Session lifecycle tunables.
SESSION_TIMEOUT_S = int(os.environ.get("ZOE_YTMUSIC_SESSION_TIMEOUT_S", "300"))  # 5 min
_POLL_S = float(os.environ.get("ZOE_YTMUSIC_POLL_S", "2.0"))
_PROC_KILL_WAIT_S = 3.0

# States the setup page polls on.
_ACTIVE_STATES = {"starting", "awaiting_login", "harvesting"}
_TERMINAL_STATES = {"connected", "error", "timeout"}

# The single active session record (one session at a time — hard guardrail).
_SESSION: Optional[dict[str, Any]] = None
_START_LOCK = asyncio.Lock()


# ── secret-safe helpers (never print a cookie in full) ───────────────────────

def _redact(value: str, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    return f"<{len(value)} chars, starts {value[:keep]}…>"


def _ensure_secret_dir() -> Path:
    SECRET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(SECRET_DIR, 0o700)
    except OSError:
        pass
    return SECRET_DIR


def _assemble_cookie_header(cookies: list[dict]) -> tuple[str, list[str]]:
    """Turn CDP/Playwright cookie dicts into one ``Cookie:`` header, keeping only
    the auth domains and de-duping by name (last wins). HttpOnly cookies are
    included — that is the point. Returns (header, sorted_names)."""
    picked: dict[str, str] = {}
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".")
        if not any(dom == d.lstrip(".") or dom.endswith(d.lstrip(".")) for d in AUTH_DOMAINS):
            continue
        name = c.get("name")
        if not name:
            continue
        picked[name] = c.get("value", "")
    header = "; ".join(f"{k}={v}" for k, v in picked.items())
    return header, sorted(picked)


def _has_required(names: list[str]) -> bool:
    return REQUIRED_COOKIE in names


def _store_username(username: str) -> None:
    """Persist the account label (NOT a secret, but keep perms tight) so the
    headless refresh can re-save under the same username."""
    if not username:
        return
    try:
        _ensure_secret_dir()
        _USERNAME_FILE.write_text(username, encoding="utf-8")
        os.chmod(_USERNAME_FILE, 0o600)
    except OSError as exc:
        logger.debug("could not persist ytmusic username label: %s", exc)


def _stored_username() -> str:
    try:
        if _USERNAME_FILE.exists():
            return _USERNAME_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _lan_ip() -> str:
    """Best-effort primary LAN IPv4 for binding the remote view. Override with
    ZOE_RIG_BIND. Falls back to 127.0.0.1 (never a blind 0.0.0.0 default)."""
    override = os.environ.get("ZOE_RIG_BIND")
    if override:
        return override
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))  # no packet sent; just picks the egress iface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


# ── rig bring-up + teardown (the browser/process mechanics) ──────────────────
# These are the ONLY functions that touch subprocesses / the browser. They are
# kept small + injectable so the state machine can be unit-tested with a stub rig
# (no real Xvfb/Chromium needed).

def _require_binaries() -> list[str]:
    return [b for b in ("Xvfb", "x11vnc", "websockify") if shutil.which(b) is None]


def _spawn(name: str, argv: list[str]) -> subprocess.Popen:
    logger.info("ytmusic sign-in: starting %s", name)
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _start_display_stack(bind: str) -> list[subprocess.Popen]:
    """Xvfb (virtual display) → x11vnc (localhost-only mirror) → websockify/noVNC
    (the single LAN-bound port). Returns the process handles for teardown."""
    procs: list[subprocess.Popen] = []
    procs.append(_spawn("Xvfb", ["Xvfb", _DISPLAY, "-screen", "0", _XVFB_GEOMETRY, "-nolisten", "tcp"]))
    time.sleep(1.0)
    # Raw VNC never touches the LAN — bound to localhost; websockify is the one
    # LAN-exposed port.
    procs.append(_spawn("x11vnc", [
        "x11vnc", "-display", _DISPLAY, "-rfbport", str(_VNC_PORT),
        "-listen", "localhost", "-forever", "-shared", "-nopw", "-quiet", "-noxdamage",
    ]))
    if not _wait_port("127.0.0.1", _VNC_PORT):
        logger.warning("ytmusic sign-in: x11vnc did not open its port")
    web = ""
    for cand in ("/usr/share/novnc", "/usr/share/webapps/novnc"):
        if os.path.isdir(cand):
            web = cand
            break
    argv = ["websockify"]
    if web:
        argv += ["--web", web]
    argv += [f"{bind}:{_NOVNC_PORT}", f"127.0.0.1:{_VNC_PORT}"]
    procs.append(_spawn("websockify/noVNC", argv))
    _wait_port("127.0.0.1", _NOVNC_PORT)
    return procs


async def _launch_browser(headless: bool = False) -> Any:
    """Launch CloakBrowser (stealth Chromium) on the persistent profile and land
    on the login page. Returns the BrowserContext. Headful draws into Xvfb; the
    refresh path uses headless=True (no display needed)."""
    from cloakbrowser import launch_persistent_context_async

    _ensure_secret_dir()
    PROFILE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not headless:
        os.environ["DISPLAY"] = _DISPLAY  # headful Chromium draws into Xvfb

    context = await launch_persistent_context_async(
        str(PROFILE_DIR),
        headless=headless,
        user_agent=_USER_AGENT,
        viewport=None,
        args=([] if headless else ["--start-maximized", "--window-position=0,0"]),
    )
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001 — a nav hiccup shouldn't sink the rig
        logger.debug("ytmusic sign-in: initial nav best-effort failed: %s", exc)
    return context


async def _bring_up_rig(session: dict[str, Any]) -> None:
    """Spin up the display stack + headful browser; populate the session with its
    process handles, context, and the LAN view URL. Raises on hard failure."""
    missing = _require_binaries()
    if missing:
        raise RuntimeError(f"missing sign-in binaries: {', '.join(missing)}")
    bind = _lan_ip()
    # _start_display_stack does blocking work (subprocess.Popen forks, time.sleep,
    # blocking port waits). Run it OFF the event loop so a sign-in bring-up never
    # stalls the uvicorn worker (health checks, chat, websockets). subprocess.Popen
    # inside a thread executor is the safe fork pattern here — not a loop-thread
    # asyncio.create_subprocess_exec (see services/zoe-data/AGENTS.md).
    session["procs"] = await asyncio.to_thread(_start_display_stack, bind)
    session["context"] = await _launch_browser(headless=False)
    session["view_url"] = f"http://{bind}:{_NOVNC_PORT}/vnc.html?autoconnect=1&resize=scale"


async def _harvest_from_context(context: Any) -> tuple[str, list[str]]:
    """Read every auth-domain cookie the live browser holds and assemble the
    Cookie header. Same-process, so no CDP round-trip is needed."""
    cookies = await context.cookies()
    return _assemble_cookie_header(cookies)


async def _derive_username(context: Any) -> str:
    """Best-effort account label from the signed-in session. Never fatal — the
    username is only a display label MA stores alongside the cookie; a bad read
    falls back to the stored value / a generic label."""
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        email = await page.evaluate(
            "() => { try {"
            " const c = (window.ytcfg && ytcfg.data_) || {};"
            " if (c.DELEGATED_SESSION_ID_EMAIL) return c.DELEGATED_SESSION_ID_EMAIL;"
            " const el = document.querySelector('[aria-label*=\"@\"]');"
            " if (el) { const m = (el.getAttribute('aria-label')||'').match(/[\\w.+-]+@[\\w.-]+/); if (m) return m[0]; }"
            " return ''; } catch (e) { return ''; } }"
        )
        if isinstance(email, str) and "@" in email:
            return email.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ytmusic sign-in: username derivation best-effort failed: %s", exc)
    return ""


async def _teardown(session: dict[str, Any]) -> None:
    """Close the browser context and kill every rig process. Idempotent. The view
    must never outlive the session, so this runs on completion, timeout, AND error."""
    context = session.pop("context", None)
    if context is not None:
        try:
            await context.close()  # cloakbrowser also stops playwright + kills Chromium
        except Exception as exc:  # noqa: BLE001
            logger.debug("ytmusic sign-in: context close best-effort failed: %s", exc)
    procs: list[subprocess.Popen] = session.pop("procs", []) or []
    for p in reversed(procs):
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    for p in reversed(procs):
        try:
            p.wait(timeout=_PROC_KILL_WAIT_S)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
    session["view_url"] = None


# ── the harvest → save → teardown state machine ──────────────────────────────

async def _finish_connect(session: dict[str, Any], header: str) -> None:
    """Login detected + cookie harvested: derive the username, save the provider
    into MA, and mark connected. Teardown happens in the watcher's finally."""
    username = await _derive_username(session.get("context")) or _stored_username() or "YouTube Music"
    logger.info("ytmusic sign-in: harvested cookie %s — saving provider (user=%s)",
                _redact(header), username)
    # Reuse the existing instance if one is configured (re-connect replaces it in
    # place) rather than minting a duplicate ytmusic provider.
    instance_id = await music_service.provider_instance_id(music_service._YTMUSIC_DOMAIN)
    saved = await music_service.save_provider(
        music_service._YTMUSIC_DOMAIN, {"username": username, "cookie": header},
        instance_id=instance_id)
    if not saved:
        session["state"] = "error"
        session["error"] = "The music engine rejected the sign-in — please try again."
        return
    _store_username(username)
    session["state"] = "connected"
    session["provider_name"] = saved.get("name") or "YouTube Music"


async def _run_watcher(session: dict[str, Any]) -> None:
    """Poll the live browser for the auth cookie; when present, harvest → save →
    teardown. Times out (and tears down) after SESSION_TIMEOUT_S."""
    deadline = time.monotonic() + SESSION_TIMEOUT_S
    try:
        session["state"] = "awaiting_login"
        while time.monotonic() < deadline:
            if session.get("state") in _TERMINAL_STATES:
                return
            try:
                header, names = await _harvest_from_context(session.get("context"))
            except Exception as exc:  # noqa: BLE001 — browser not ready / transient
                logger.debug("ytmusic sign-in: harvest probe not ready: %s", exc)
                header, names = "", []
            if header and _has_required(names):
                session["state"] = "harvesting"
                await _finish_connect(session, header)
                return
            await asyncio.sleep(_POLL_S)
        session["state"] = "timeout"
        session["error"] = "Sign-in timed out — head back to your Zoe panel and try again."
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        session["state"] = "error"
        session["error"] = "Sign-in was cancelled."
        raise
    finally:
        # The browser NEVER outlives the session — torn down on every exit path.
        await _teardown(session)


# ── public API ───────────────────────────────────────────────────────────────

async def start_session() -> dict[str, Any]:
    """Spin up ONE phone-drivable sign-in rig. Returns {ok, session_id, view_url}.
    Refuses a second concurrent session (returns ok=False, reason='busy')."""
    global _SESSION
    async with _START_LOCK:
        if _SESSION is not None and _SESSION.get("state") in _ACTIVE_STATES:
            return {"ok": False, "reason": "busy",
                    "message": "A YouTube Music sign-in is already in progress."}
        session: dict[str, Any] = {
            "id": "ytm-" + os.urandom(6).hex(),
            "state": "starting",
            "view_url": None,
            "error": None,
            "created": time.time(),
        }
        _SESSION = session
        try:
            await _bring_up_rig(session)
        except Exception as exc:  # noqa: BLE001 — never leak a half-up rig
            logger.warning("ytmusic sign-in: rig bring-up failed: %s", exc)
            session["state"] = "error"
            session["error"] = "Couldn't start the sign-in browser."
            await _teardown(session)
            return {"ok": False, "reason": "rig_failed", "message": session["error"]}
        session["watcher"] = asyncio.create_task(
            _run_watcher(session), name="ytmusic_signin_watcher")
        return {"ok": True, "session_id": session["id"], "view_url": session["view_url"],
                "expires_in": SESSION_TIMEOUT_S}


def session_status(session_id: str) -> dict[str, Any]:
    """Poll a session. Returns a JSON-safe {state, view_url, error, name}. The
    view_url is only present while the browser is up (starting/awaiting_login)."""
    session = _SESSION
    if session is None or session.get("id") != session_id:
        return {"ok": False, "state": "unknown"}
    return {
        "ok": True,
        "state": session.get("state"),
        "view_url": session.get("view_url") if session.get("state") in _ACTIVE_STATES else None,
        "error": session.get("error"),
        "name": session.get("provider_name"),
    }


async def cancel_session(session_id: str) -> dict[str, Any]:
    """Explicit teardown (user backed out). Cancels the watcher, which tears the
    rig down in its finally. Idempotent.

    Requires the EXACT session id — an empty or mismatched id never tears down
    the active session (otherwise any holder of a valid setup token could kill an
    in-progress sign-in without knowing its id)."""
    session = _SESSION
    if session is None or not session_id or session.get("id") != session_id:
        return {"ok": True, "state": "unknown"}
    watcher = session.get("watcher")
    if watcher is not None and not watcher.done():
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    # Teardown unconditionally: a watcher cancelled before its body ran never
    # reaches its finally. _teardown is idempotent (pops context/procs), so a
    # double call after the watcher already tore down is a harmless no-op.
    await _teardown(session)
    return {"ok": True, "state": session.get("state")}


async def refresh_now() -> dict[str, Any]:
    """Anti-expiry path: open the persistent profile HEADLESS, re-harvest the
    cookie, and re-save it to MA under the stored username. Never keeps a
    resident Chromium — the context is closed promptly in every path.

    Returns {ok, reason?}. Safe to call on a schedule; a not-signed-in profile
    or a save failure returns ok=False rather than raising."""
    # Don't fight a live sign-in for the profile lock — the sign-in already
    # re-saves a fresh cookie on completion.
    if _SESSION is not None and _SESSION.get("state") in _ACTIVE_STATES:
        return {"ok": True, "skipped": "signin_in_progress"}
    if not PROFILE_DIR.exists():
        return {"ok": False, "reason": "no signed-in profile to refresh"}
    context = None
    try:
        context = await _launch_browser(headless=True)
        header, names = await _harvest_from_context(context)
    except Exception as exc:  # noqa: BLE001 — a failed refresh must never crash the caller
        logger.warning("ytmusic refresh: harvest failed: %s", exc)
        return {"ok": False, "reason": "could not open the profile"}
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ytmusic refresh: context close best-effort failed: %s", exc)
    if not header or not _has_required(names):
        logger.info("ytmusic refresh: profile has no valid %s cookie — skipping save", REQUIRED_COOKIE)
        return {"ok": False, "reason": "profile not signed in / cookie incomplete"}
    username = _stored_username() or "YouTube Music"
    # Refresh UPDATES the existing instance in place — never a duplicate provider.
    instance_id = await music_service.provider_instance_id(music_service._YTMUSIC_DOMAIN)
    saved = await music_service.save_provider(
        music_service._YTMUSIC_DOMAIN, {"username": username, "cookie": header},
        instance_id=instance_id)
    logger.info("ytmusic refresh: re-saved cookie %s (user=%s, instance=%s) -> %s",
                _redact(header), username, instance_id or "new", "ok" if saved else "rejected")
    return {"ok": bool(saved)}

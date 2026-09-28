"""ytmusic_signin — one-tap, phone-driven YouTube Music sign-in for Zoe.

Promoted from the ``labs/ytmusic-signin`` spike (rig.py + harvest.py + common.py)
into a managed production service. YouTube Music has no username/password and no
OAuth — the only way to link it is a browser login cookie (which must contain
``__Secure-3PAPISID``) plus Zoe's local PO-token generator + a YT Music Premium
account. This module presents a remote browser the user signs into *themselves*,
auto-detects the login, harvests the resulting cookie, hands it to Music
Assistant's ``ytmusic`` provider, and tears the browser down.

"Cookie present" is NOT "login happened" (live incident 2026-09-28: the
persistent profile still held a rotated, dead cookie, so every attempt "found"
it within seconds, saved it, and tore the view down before the person had even
opened it). The watcher snapshots the profile's login cookie at session start
and harvests only one that CHANGED during the session and that YouTube itself
confirms is signed in; a stale cookie found at start is wiped (Google/YouTube
cookies only) so the view shows a real sign-in form.

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
    noVNC/websockify port is LAN-bound, and its WebSocket (the only channel that
    shows or drives the browser) is locked to a per-session random secret:
    websockify's TokenFile plugin maps ``<secret>`` → the local VNC port, so a
    connection without it is refused before the upgrade. The phone reaches the
    view directly on the LAN (not through nginx), and the secret travels only in
    the view URL's ``#fragment`` → noVNC's ``path`` setting → the WebSocket
    request to websockify (whose output is discarded) — never a server log.
    The static noVNC client files stay public; they carry no session data.
    The browser is transient: it is torn down on completion, on timeout
    (~5 min), and on any error — it must never outlive the session, and the
    token file is deleted with it.
  * RAM-aware (Jetson Orin, 16GB): the sign-in browser exists only during setup;
    the refresh path opens the profile HEADLESS, harvests, and closes promptly —
    never a resident Chromium.

The router (``routers/music_setup.py``) gates ``start_session`` behind the
one-time setup token; this module owns the rig lifecycle + the harvest→save→
teardown state machine + the anti-expiry ``refresh_now`` path.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from typing import Any, Awaitable, Callable, Optional

import httpx

import music_service

logger = logging.getLogger(__name__)

# ── The cookie YouTube Music auth hinges on ──────────────────────────────────
# MA's ytmusic provider rejects a cookie that lacks this key (it is HttpOnly, so
# page JS can never read it — but the browser owner can, which is the whole point).
REQUIRED_COOKIE = "__Secure-3PAPISID"

# Cookies live across these registrable domains after a YTMusic login. We collect
# from all of them and assemble a single Cookie header (what ytmusic-api sends).
AUTH_DOMAINS = (".youtube.com", ".google.com", "youtube.com", "google.com")

# Clearing a stale login removes ONLY these cookies (every google.com /
# youtube.com host), never the rest of the persistent profile.
_AUTH_COOKIE_DOMAIN_RE = re.compile(r"(^|\.)(youtube|google)\.com$")

# ── "cookie present" is NOT "login happened" (live incident 2026-09-28) ──────
# The persistent profile can hold a rotated/expired __Secure-3PAPISID. Seeing it
# is not a login: the watcher harvests only a cookie that CHANGED during this
# session (or appeared in a clean profile), and only after YouTube itself says
# the cookie is signed in. The check is the request MA's ytmusic provider makes
# (a youtubei POST with the SAPISIDHASH auth header), read through YouTube's own
# explicit ``logged_in`` / ``yt_li`` tracking flags — no page scraping.
_YTM_ORIGIN = "https://music.youtube.com"
_VALIDATE_URL = f"{_YTM_ORIGIN}/youtubei/v1/account/account_menu?alt=json&prettyPrint=false"
_VALIDATE_TIMEOUT_S = 8.0
_VALIDATE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:72.0) Gecko/20100101 Firefox/72.0"
_LOGGED_IN_KEYS = ("logged_in", "yt_li")

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
_VIEWER_TOKEN_FILE = SECRET_DIR / "viewer-token"  # websockify TokenFile: <secret>: host:port (0600)

# Session lifecycle tunables.
SESSION_TIMEOUT_S = int(os.environ.get("ZOE_YTMUSIC_SESSION_TIMEOUT_S", "300"))  # 5 min
_POLL_S = float(os.environ.get("ZOE_YTMUSIC_POLL_S", "2.0"))
_PROC_KILL_WAIT_S = 3.0

# States the setup page polls on. ``stale_cookie_cleared`` = still waiting for a
# login, but the profile's old sign-in had expired and was wiped first (the
# phone/panel say "sign in again" rather than nothing).
_ACTIVE_STATES = {"starting", "awaiting_login", "stale_cookie_cleared", "harvesting"}
STALE_DETAIL = "Your old YouTube Music sign-in had expired — sign in again on your phone."
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


def _auth_cookie_map(cookies: list[dict]) -> dict[str, str]:
    """Auth-domain cookies only, de-duped by name (last wins)."""
    picked: dict[str, str] = {}
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".")
        if not any(dom == d.lstrip(".") or dom.endswith(d.lstrip(".")) for d in AUTH_DOMAINS):
            continue
        name = c.get("name")
        if not name:
            continue
        picked[name] = c.get("value", "")
    return picked


def _assemble_cookie_header(cookies: list[dict]) -> tuple[str, list[str]]:
    """Turn CDP/Playwright cookie dicts into one ``Cookie:`` header, keeping only
    the auth domains and de-duping by name (last wins). HttpOnly cookies are
    included — that is the point. Returns (header, sorted_names)."""
    picked = _auth_cookie_map(cookies)
    header = "; ".join(f"{k}={v}" for k, v in picked.items())
    return header, sorted(picked)


def _has_required(names: list[str]) -> bool:
    return REQUIRED_COOKIE in names


def _login_fingerprint(picked: dict[str, str]) -> Optional[str]:
    """A one-way digest of the login-bearing cookies (``__Secure-3PAPISID`` +
    ``SID``), or None when there is no login cookie. Comparing digests tells a
    login that happened DURING the session from one the profile already held,
    without keeping the secret values on the session record."""
    value = picked.get(REQUIRED_COOKIE)
    if not value:
        return None
    return hashlib.sha256(f"{value}\0{picked.get('SID', '')}".encode()).hexdigest()


def _cookie_value(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def _sapisid_hash(sapisid: str, now: Optional[float] = None) -> str:
    """The ``Authorization`` value ytmusicapi (and so MA) sends with a cookie."""
    ts = str(int(now if now is not None else time.time()))
    digest = hashlib.sha1(f"{ts} {sapisid} {_YTM_ORIGIN}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def _logged_in_verdict(data: Any) -> Optional[bool]:
    """Read YouTube's own login flags from a youtubei response:
    ``responseContext.serviceTrackingParams[].params[]`` carries ``logged_in``
    (GFEEDBACK) and ``yt_li`` (CSI) as "1"/"0". True/False only when every flag
    present agrees; None when they are missing or disagree (inconclusive)."""
    try:
        services = data["responseContext"]["serviceTrackingParams"]
    except (KeyError, TypeError):
        return None
    seen: set[str] = set()
    for svc in services if isinstance(services, list) else []:
        for p in (svc or {}).get("params") or []:
            if isinstance(p, dict) and p.get("key") in _LOGGED_IN_KEYS:
                seen.add(str(p.get("value")))
    if seen == {"1"}:
        return True
    if seen == {"0"}:
        return False
    return None


async def _validate_cookie(header: str) -> Optional[bool]:
    """Ask YouTube Music whether ``header`` is a signed-in session.

    True = signed in; False = YouTube says NOT signed in (a stale/expired
    cookie); None = could not tell (network, non-JSON, unexpected shape). Never
    logs the cookie or the response body."""
    sapisid = _cookie_value(header, REQUIRED_COOKIE)
    if not sapisid:
        return False
    headers = {
        "User-Agent": _VALIDATE_UA,
        "Accept": "*/*",
        "Content-Type": "application/json",
        "X-Goog-AuthUser": "0",
        "x-origin": _YTM_ORIGIN,
        "Origin": _YTM_ORIGIN,
        "Cookie": header,
        "Authorization": _sapisid_hash(sapisid),
    }
    body = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20250101.01.00"},
                        "user": {}}}
    try:
        async with httpx.AsyncClient(timeout=_VALIDATE_TIMEOUT_S) as client:
            resp = await client.post(_VALIDATE_URL, headers=headers, json=body)
    except Exception as exc:  # noqa: BLE001 — inconclusive, never fatal
        logger.info("ytmusic sign-in: cookie validation inconclusive (%s)", type(exc).__name__)
        return None
    if resp.status_code in (401, 403):
        return False
    if resp.status_code != 200:
        logger.info("ytmusic sign-in: cookie validation inconclusive (HTTP %s)", resp.status_code)
        return None
    try:
        return _logged_in_verdict(resp.json())
    except ValueError:
        return None


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


def _write_viewer_token(secret: str) -> Path:
    """Write websockify's TokenFile: the per-session secret is the ONLY key that
    maps to the local VNC port. Mode 0600, under the 0700 secret dir."""
    _ensure_secret_dir()
    fd = os.open(_VIEWER_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{secret}: 127.0.0.1:{_VNC_PORT}\n")
    os.chmod(_VIEWER_TOKEN_FILE, 0o600)
    return _VIEWER_TOKEN_FILE


def _clear_viewer_token() -> None:
    """TokenFile re-reads on every lookup, so deleting it revokes the secret."""
    try:
        _VIEWER_TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("ytmusic sign-in: viewer token cleanup failed: %s", exc)


def _websockify_argv(bind: str, token_file: Path, web: str = "") -> list[str]:
    """websockify with NO fixed target: a connection is routed only when its
    ``?token=`` matches the TokenFile, otherwise it is refused pre-upgrade."""
    argv = ["websockify"]
    if web:
        argv += ["--web", web]
    argv += ["--token-plugin", "TokenFile", "--token-source", str(token_file),
             f"{bind}:{_NOVNC_PORT}"]
    return argv


def _view_url(bind: str, secret: str) -> str:
    """The phone's view link. The secret rides in the FRAGMENT as noVNC's
    ``path`` setting (noVNC reads config from the hash first), so it is never
    sent in an HTTP request line except the WebSocket's own ``?token=``."""
    path = quote(f"websockify?token={secret}", safe="")
    return f"http://{bind}:{_NOVNC_PORT}/vnc.html#autoconnect=1&resize=scale&path={path}"


def _start_display_stack(bind: str, token_file: Path) -> list[subprocess.Popen]:
    """Xvfb (virtual display) → x11vnc (localhost-only mirror) → websockify/noVNC
    (the single LAN-bound port, token-gated). Returns the process handles for
    teardown."""
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
    procs.append(_spawn("websockify/noVNC", _websockify_argv(bind, token_file, web)))
    _wait_port("127.0.0.1", _NOVNC_PORT)
    return procs


async def _launch_browser(headless: bool = False) -> Any:
    """Launch CloakBrowser (stealth Chromium) on the persistent profile and land
    on the login page. Returns the BrowserContext. Headful draws into Xvfb; the
    refresh path uses headless=True (no display needed)."""
    from cloakbrowser import launch_persistent_context_async

    # CloakBrowser otherwise GETs pypi.org + api.github.com on every launch (an
    # "update available" check, and a background Chromium download) — network
    # side effects on the sign-in hot path of a pinned, local-first stack.
    # setdefault so an operator can still opt back in.
    os.environ.setdefault("CLOAKBROWSER_AUTO_UPDATE", "false")
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
    viewer_secret = secrets.token_urlsafe(24)
    token_file = _write_viewer_token(viewer_secret)
    # _start_display_stack does blocking work (subprocess.Popen forks, time.sleep,
    # blocking port waits). Run it OFF the event loop so a sign-in bring-up never
    # stalls the uvicorn worker (health checks, chat, websockets). subprocess.Popen
    # inside a thread executor is the safe fork pattern here — not a loop-thread
    # asyncio.create_subprocess_exec (see services/zoe-data/AGENTS.md).
    session["procs"] = await asyncio.to_thread(_start_display_stack, bind, token_file)
    session["context"] = await _launch_browser(headless=False)
    session["view_url"] = _view_url(bind, viewer_secret)


async def _harvest_from_context(context: Any) -> tuple[str, list[str]]:
    """Read every auth-domain cookie the live browser holds and assemble the
    Cookie header. Same-process, so no CDP round-trip is needed."""
    header, names, _fp = await _probe_context(context)
    return header, names


async def _probe_context(context: Any) -> tuple[str, list[str], Optional[str]]:
    """(cookie header, sorted names, login fingerprint) from the live browser."""
    picked = _auth_cookie_map(await context.cookies())
    header = "; ".join(f"{k}={v}" for k, v in picked.items())
    return header, sorted(picked), _login_fingerprint(picked)


async def _clear_stale_login(context: Any) -> None:
    """Drop ONLY the Google/YouTube cookies (never the rest of the profile) and
    reload the login page, so the person lands on a real sign-in form rather
    than a half-signed-in expired session. Best-effort."""
    try:
        await context.clear_cookies(domain=_AUTH_COOKIE_DOMAIN_RE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ytmusic sign-in: clearing the stale login cookies failed: %s", exc)
        return
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        logger.debug("ytmusic sign-in: reload after clearing best-effort failed: %s", exc)


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
    _clear_viewer_token()
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


async def _check_profile_at_start(session: dict[str, Any]) -> bool:
    """Snapshot the login the persistent profile ALREADY holds, before the
    person has touched the view. Returns True when that login was harvested.

    * no login cookie → baseline None: any cookie that appears is a fresh login;
    * a cookie YouTube confirms is signed in → a real, live login: save it now
      (otherwise the person would stare at a signed-in page until timeout);
    * a cookie YouTube says is NOT signed in → stale (the 2026-09-28 incident):
      wipe the Google/YouTube cookies so the view shows a real sign-in form;
    * can't tell (network) → keep the snapshot; only a CHANGED cookie counts.
    """
    context = session.get("context")
    try:
        header, _names, fingerprint = await _probe_context(context)
    except Exception as exc:  # noqa: BLE001 — browser not ready: nothing to snapshot yet
        logger.debug("ytmusic sign-in: start snapshot not ready: %s", exc)
        header, fingerprint = "", None
    session["baseline_fp"] = fingerprint
    if fingerprint is None:
        return False
    verdict = await _validate_cookie(header)
    if verdict is True:
        logger.info("ytmusic sign-in: profile already holds a live sign-in (validated) — saving it")
        session["state"] = "harvesting"
        await _finish_connect(session, header)
        return True
    if verdict is None:
        logger.info("ytmusic sign-in: couldn't validate the profile's existing cookie — "
                    "waiting for a fresh login")
        return False
    logger.info("ytmusic sign-in: profile cookie is stale (validation failed) — "
                "clearing its Google/YouTube cookies")
    await _clear_stale_login(context)
    try:
        _h, _n, after = await _probe_context(context)
    except Exception:  # noqa: BLE001 — keep the stale snapshot: unchanged still won't count
        after = fingerprint
    session["baseline_fp"] = after
    session["state"] = "stale_cookie_cleared"
    await _progress(session)
    return False


async def _run_watcher(session: dict[str, Any]) -> None:
    """Snapshot the profile's login, then poll the live browser until a login
    happens DURING this session: a login cookie that differs from the snapshot
    AND that YouTube confirms is signed in → harvest → save → teardown. A stale
    or unvalidated cookie is never saved. Times out (and tears down) after
    SESSION_TIMEOUT_S."""
    deadline = time.monotonic() + SESSION_TIMEOUT_S
    try:
        session["state"] = "awaiting_login"
        if await _check_profile_at_start(session):
            return
        logged_unchanged = False
        while time.monotonic() < deadline:
            if session.get("state") in _TERMINAL_STATES:
                return
            try:
                header, names, fingerprint = await _probe_context(session.get("context"))
            except Exception as exc:  # noqa: BLE001 — browser not ready / transient
                logger.debug("ytmusic sign-in: harvest probe not ready: %s", exc)
                header, names, fingerprint = "", [], None
            if header and _has_required(names) and fingerprint:
                if fingerprint == session.get("baseline_fp"):
                    if not logged_unchanged:
                        logger.info("ytmusic sign-in: stale cookie ignored (unchanged since session start)")
                        logged_unchanged = True
                elif fingerprint != session.get("rejected_fp"):
                    verdict = await _validate_cookie(header)
                    if verdict is True:
                        session["state"] = "harvesting"
                        await _finish_connect(session, header)
                        return
                    if verdict is False:
                        # Not saved, not torn down: keep waiting for a real login.
                        session["rejected_fp"] = fingerprint
                        logger.info("ytmusic sign-in: validation failed — new cookie is not "
                                    "signed in; still waiting")
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
        await _report(session)


async def _report(session: dict[str, Any]) -> None:
    """Tell a ``watch``er how the session ended, exactly once (watcher exit or
    explicit cancel, whichever comes first)."""
    on_done = session.pop("on_done", None)
    if on_done is None:
        return
    ok = session.get("state") == "connected"
    try:
        await on_done(ok, "" if ok else (session.get("error") or "Sign-in didn't complete."))
    except Exception as exc:  # noqa: BLE001 — reporting never affects the sign-in
        logger.info("ytmusic sign-in: completion report failed: %s", exc)


async def _progress(session: dict[str, Any]) -> None:
    """Tell a ``watch``er the old sign-in was wiped (the panel's handoff card
    says "sign in again"). Idempotent; reporting never affects the sign-in."""
    on_progress = session.get("on_progress")
    if on_progress is None or session.get("state") != "stale_cookie_cleared":
        return
    try:
        await on_progress(STALE_DETAIL)
    except Exception as exc:  # noqa: BLE001
        logger.info("ytmusic sign-in: progress report failed: %s", exc)


def watch(session_id: str, on_done: Any,
          on_progress: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
    """Register ``on_done(ok, detail)`` for the live session (auth_handoff's
    reporter), and optionally ``on_progress(detail)`` for the stale-cookie
    notice. A session that already ended reports at once, as does a stale
    cookie already cleared; any other id is a no-op."""
    session = _SESSION
    if session is None or not session_id or session.get("id") != session_id:
        return
    session["on_done"] = on_done
    if on_progress is not None:
        session["on_progress"] = on_progress
        if session.get("state") == "stale_cookie_cleared":
            session["progress_task"] = asyncio.create_task(_progress(session))
    watcher = session.get("watcher")
    if session.get("state") in _TERMINAL_STATES and (watcher is None or watcher.done()):
        session["report_task"] = asyncio.create_task(_report(session))


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
    if session.get("state") not in _TERMINAL_STATES:
        session["state"], session["error"] = "error", "Sign-in was cancelled."
    await _report(session)
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
    # Same class as the sign-in watcher: a cookie in the profile is not a live
    # login. Never push one YouTube says is signed out (it would overwrite MA's
    # config with a dead cookie); an inconclusive check keeps the old behaviour.
    if await _validate_cookie(header) is False:
        logger.info("ytmusic refresh: profile cookie is no longer signed in — skipping save "
                    "(sign in again from the panel)")
        return {"ok": False, "reason": "profile sign-in expired — sign in again"}
    username = _stored_username() or "YouTube Music"
    # Refresh UPDATES the existing instance in place — never a duplicate provider.
    instance_id = await music_service.provider_instance_id(music_service._YTMUSIC_DOMAIN)
    saved = await music_service.save_provider(
        music_service._YTMUSIC_DOMAIN, {"username": username, "cookie": header},
        instance_id=instance_id)
    logger.info("ytmusic refresh: re-saved cookie %s (user=%s, instance=%s) -> %s",
                _redact(header), username, instance_id or "new", "ok" if saved else "rejected")
    return {"ok": bool(saved)}

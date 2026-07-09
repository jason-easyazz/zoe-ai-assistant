"""Shared config + secret-safe helpers for the YouTube Music sign-in spike.

LAB-ONLY. Nothing here is imported by the Zoe runtime. See ./AGENTS.md.

Hard rules encoded here:
  * The harvested cookie is a SECRET. It is only ever written under SECRET_DIR
    (outside the repo, mode 0700) and never logged in full — use redact().
  * This module never touches a Google password. It only reads cookies that the
    browser already holds after a human logged in themselves.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

# ── The cookie YouTube Music auth hinges on ──────────────────────────────────
# Music Assistant's ytmusic provider rejects a cookie that lacks this key.
REQUIRED_COOKIE = "__Secure-3PAPISID"

# Cookies live across these registrable domains after a YTMusic login. We collect
# from all of them and assemble a single Cookie header (what ytmusic-api sends).
AUTH_DOMAINS = (".youtube.com", ".google.com", "youtube.com", "google.com")

# ── Ports / display (override via env for a second concurrent rig) ────────────
DISPLAY = os.environ.get("ZOE_RIG_DISPLAY", ":99")
XVFB_GEOMETRY = os.environ.get("ZOE_RIG_GEOMETRY", "1280x800x24")
VNC_PORT = int(os.environ.get("ZOE_RIG_VNC_PORT", "5900"))
NOVNC_PORT = int(os.environ.get("ZOE_RIG_NOVNC_PORT", "6080"))
CDP_PORT = int(os.environ.get("ZOE_RIG_CDP_PORT", "9222"))

# Where the human lands. Login on accounts.google.com, continue into YTMusic so
# the __Secure-3PAPISID cookie is minted on the youtube.com domain.
LOGIN_URL = os.environ.get(
    "ZOE_RIG_LOGIN_URL",
    "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmusic.youtube.com%2F",
)

# UA: default None => use CloakBrowser's own coherent stealth UA (Chromium
# 146.x). Overriding with a mismatched UA is a fingerprint tell, so we leave it
# to the stealth layer unless an operator deliberately sets one.
USER_AGENT = os.environ.get("ZOE_RIG_USER_AGENT") or None

# ── Secret + profile paths (OUTSIDE the repo, never committed) ────────────────
SECRET_DIR = Path(os.environ.get("ZOE_YTMUSIC_SECRET_DIR", str(Path.home() / ".zoe-ytmusic")))
PROFILE_DIR = SECRET_DIR / "profile"        # persistent Chromium user-data-dir
COOKIE_FILE = SECRET_DIR / "cookie.header"  # assembled Cookie header (mode 0600)


def lan_ip() -> str:
    """Best-effort primary LAN IPv4 for binding the remote view. Override with
    ZOE_RIG_BIND. Falls back to 0.0.0.0 (LAN-only still enforced by firewall)."""
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
        return "0.0.0.0"


def ensure_secret_dir() -> Path:
    SECRET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SECRET_DIR, 0o700)
    return SECRET_DIR


def redact(value: str, keep: int = 4) -> str:
    """Never print a secret in full. Shows only length + a short prefix."""
    if not value:
        return "<empty>"
    return f"<{len(value)} chars, starts {value[:keep]}…>"


def assemble_cookie_header(cookies: list[dict], domains=AUTH_DOMAINS) -> tuple[str, list[str]]:
    """Turn a list of CDP/Playwright cookie dicts into a single `Cookie:` header
    string (name=value; …), keeping only the auth domains and de-duping by name
    (last wins). Returns (header, sorted_names). HttpOnly cookies are included —
    that is the whole point; page JS could not read them."""
    picked: dict[str, str] = {}
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".")
        if not any(dom == d.lstrip(".") or dom.endswith(d.lstrip(".")) for d in domains):
            continue
        name = c.get("name")
        if not name:
            continue
        picked[name] = c.get("value", "")
    header = "; ".join(f"{k}={v}" for k, v in picked.items())
    return header, sorted(picked)


def has_required(names: list[str]) -> bool:
    return REQUIRED_COOKIE in names


def store_cookie_header(header: str) -> Path:
    """Persist the assembled header to the gitignored secret file, mode 0600."""
    ensure_secret_dir()
    COOKIE_FILE.write_text(header, encoding="utf-8")
    os.chmod(COOKIE_FILE, 0o600)
    return COOKIE_FILE

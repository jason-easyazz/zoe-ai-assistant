"""The kiosk credential: the panel's kiosk BROWSER proves it is the panel with a credential minted from the panel's DEVICE TOKEN,
not with its source address (docs/architecture/panel-remote-access.md section 4.2).

Why: until now the kiosk browser was "the panel" only because its address equalled the registered ``panels.ip_address``
(``panel_principal`` path 2, ``routers/skybridge.py`` display identity). Over a private link (Tailscale/Headscale) or on another
Wi-Fi that address changes, and the panel silently becomes "a guest somewhere": household lists go read-only and the display
identity disappears. The voice daemon never had this problem because it sends ``X-Device-Token``; the browser cannot hold
that token (any page script could read it), so it gets a credential DERIVED from it instead:

  1. ``scripts/setup/touchscreen/zoe-kiosk-gate.py`` (on the panel, loopback only) holds the device token. When Zoe answers it
     calls ``POST /api/panels/{panel_id}/kiosk-ticket`` with ``X-Device-Token`` and gets a single-use ticket (60 s).
  2. The gate hands the browser a page that POSTs that ticket to ``POST /api/panels/kiosk-enter`` (top-level navigation; the
     ticket is in the form body, never a URL or a log line).
  3. zoe-data spends the ticket and sets ``zoe_panel_kiosk``: an HMAC-signed, HttpOnly, Secure, SameSite=Strict, host-only
     cookie naming the panel and the token it came from. The browser sends it on every same-origin request (fetch, WebSocket)
     with no change to the touch UI, and page script can never read it.
  4. ``panel_principal.is_panel_guest`` and the skybridge display identity accept a GUEST request carrying a valid credential
     as that panel. It confers nothing else: never a member, never admin, never a voice identity.

Load-bearing rules:
  * Flag-dark: ``ZOE_PANEL_KIOSK_CREDENTIAL`` (default off, per-call read). Off, both endpoints 404 and a presented cookie is
    IGNORED, so the LAN kiosk behaves exactly as before (address path, guest session, PIN expiry untouched).
  * It dies with the device token: every use re-checks the token in ``panel_auth``'s cache, so revoking the token (the lost-
    panel runbook) or its ``expires_at`` kills the kiosk credential on the next request, and deactivating the panel row does too.
  * Never over the public tunnel: a request carrying a Cloudflare header can neither mint, enter nor use one (the panel never
    uses Cloudflare - content privacy, design section 3.1; a credential arriving that way was copied off the kiosk).
  * Purpose-separated: the signing key is derived (``zoe.panel-kiosk.v1``) from ``ZOE_PANEL_KIOSK_SECRET`` or else
    ``ZOE_INTERNAL_TOKEN``, and every token carries a purpose claim, so a ticket is not a credential, a credential is not a
    ticket, and neither verifies as another ``auth_handoff`` flow's token (those use the raw key). With neither secret set the
    key is a per-process random: credentials then die on restart and the gate re-mints on the kiosk's next start (fail safe).
  * ``auth._is_internal_request`` is not involved and must never be widened for this: the gate talks to Zoe over the network
    like any other device, never through a loopback proxy on the Jetson.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any, Mapping, Optional
from urllib.parse import quote

import auth_handoff

logger = logging.getLogger(__name__)

COOKIE_NAME = "zoe_panel_kiosk"
ENTER_PATH = "/api/panels/kiosk-enter"
_PURPOSE_TICKET = "kiosk-ticket"
_PURPOSE_CREDENTIAL = "kiosk-cred"
_KEY_LABEL = b"zoe.panel-kiosk.v1"
_process_key = secrets.token_hex(32)


def enabled() -> bool:
    return os.environ.get("ZOE_PANEL_KIOSK_CREDENTIAL", "").strip().lower() in ("1", "true", "yes", "on")


def ticket_ttl_s() -> int:
    return _int_env("ZOE_PANEL_KIOSK_TICKET_TTL_S", 60, lo=10, hi=600)


def credential_ttl_s() -> int:
    # The kiosk re-mints on every browser start; the TTL only bounds a credential nobody revoked. Revocation is the token.
    return _int_env("ZOE_PANEL_KIOSK_CREDENTIAL_TTL_S", 30 * 24 * 3600, lo=300, hi=90 * 24 * 3600)


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _signing_key() -> str:
    base = os.environ.get("ZOE_PANEL_KIOSK_SECRET") or os.environ.get("ZOE_INTERNAL_TOKEN") or _process_key
    return hmac.new(base.encode(), _KEY_LABEL, hashlib.sha256).hexdigest()


# One signer; the purpose claim separates tickets from credentials. ``read_secret`` never returns empty, so SignedTokens never
# falls back to the raw ZOE_INTERNAL_TOKEN (which the other handoff flows sign with).
_TOKENS = auth_handoff.SignedTokens("ZOE_PANEL_KIOSK_SECRET", _signing_key)


def via_public_tunnel(request: Any) -> bool:
    """A request that crossed the Cloudflare tunnel: it carries an edge header a caller cannot remove."""
    headers = getattr(request, "headers", None) or {}
    return bool(headers.get("cf-connecting-ip") or headers.get("cf-ray") or headers.get("cf-visitor"))


def _token_still_valid(panel_id: str, token_hash: str) -> bool:
    """The device token the credential was minted from is still issued to this panel, unrevoked and unexpired."""
    try:
        from routers.panel_auth import lookup_device_token_hash  # late: panel_auth imports auth/database

        info = lookup_device_token_hash(str(token_hash or ""))
    except Exception as exc:  # noqa: BLE001 - an unreadable token store is "not valid" (fail closed), but say so
        logger.warning("panel_kiosk_credential: device token check failed (%s) - refusing", type(exc).__name__)
        return False
    return bool(info and str(info.get("panel_id")) == str(panel_id))


def _claims(purpose: str, panel_id: str, token_hash: str) -> dict:
    return dict(p=purpose, pid=panel_id, th=token_hash)


def mint_ticket(panel_id: str, token_hash: str) -> str:
    return _TOKENS.mint(_claims(_PURPOSE_TICKET, panel_id, token_hash), ticket_ttl_s())


def redeem_ticket(ticket: str) -> Optional[dict]:
    """Spend a ticket (single use) and return its claims, or None (bad signature, expired, spent, wrong purpose, or the device
    token was revoked in the meantime). A wrong-purpose token is not spent."""
    claims = _TOKENS.verify(str(ticket or ""))
    if not claims or claims.get("p") != _PURPOSE_TICKET:
        return None
    if _TOKENS.consume(str(ticket)) is None:
        return None
    if not _token_still_valid(str(claims.get("pid") or ""), str(claims.get("th") or "")):
        return None
    return claims


def mint_credential(panel_id: str, token_hash: str) -> str:
    return _TOKENS.mint(_claims(_PURPOSE_CREDENTIAL, panel_id, token_hash), credential_ttl_s())


def credential_panel_id(request: Any) -> Optional[str]:
    """The panel a request's kiosk credential names, or None. Checks the flag, the tunnel, the signature, the expiry, the purpose
    and that the device token behind it is still valid for that panel. Does NOT read the panels table: callers check the row
    (active / allow_guest) with their own database handle. Never raises."""
    try:
        if not enabled() or request is None or via_public_tunnel(request):
            return None
        cookies: Mapping[str, str] = getattr(request, "cookies", None) or {}
        raw = cookies.get(COOKIE_NAME)
        if not raw:
            return None
        claims = _TOKENS.verify(str(raw))
        if not claims or claims.get("p") != _PURPOSE_CREDENTIAL:
            return None
        panel_id = str(claims.get("pid") or "")
        if not panel_id or not _token_still_valid(panel_id, str(claims.get("th") or "")):
            return None
        return panel_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel_kiosk_credential: credential check failed (%s) - refusing", type(exc).__name__)
        return None


def safe_next(next_path: str, panel_id: str) -> str:
    """Where ``kiosk-enter`` sends the browser: a same-origin ``/touch/`` path only, else the panel's estate."""
    candidate = str(next_path or "").strip()
    if (
        candidate.startswith("/touch/")
        and "//" not in candidate
        and "\\" not in candidate
        and "/../" not in candidate
        and all(32 < ord(c) < 127 for c in candidate)
    ):
        return candidate
    return f"/touch/home.html?panel_id={quote(panel_id or '', safe='')}&kiosk=1"

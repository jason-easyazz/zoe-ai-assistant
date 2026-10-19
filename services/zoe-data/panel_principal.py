"""Who counts as "anyone at the panel" - the principal that may touch HOUSEHOLD lists.

Owner decision (2026-10-15): household lists work like a fridge list. ANYONE AT THE PANEL can add, tick off and remove ITEMS on
family-visible lists (shopping, family to-dos). Renaming or deleting the LIST itself still needs a signed-in member, and a private
list stays private: a guest never sees or changes one.

"Anyone at the panel" is a statement about WHERE the request comes from, not about WHO the person is, so the signal has to be
unforgeable from the open LAN. The panel kiosk is a browser whose only credential is a guest session, and ``POST /api/auth/guest``
mints one for any caller, so "a guest session" is not evidence of a panel. In order of strength, a request is the panel when:

  1. its principal came from a provisioned device token (``get_current_user`` resolves EVERY device token to a guest stamped
     with ``panel_id`` since 2026-10-17 - the token is the device, never its bound member) - the voice daemon / kiosk gate.
     NOT over the Cloudflare tunnel (owner decision 2026-10-19: there the token is a plain guest; the relay is the way in
     from outside; escape hatch ``ZOE_PANEL_TOKEN_VIA_CF=panel``); or
  1b. it carries the KIOSK CREDENTIAL (``panel_kiosk_credential``, flag ``ZOE_PANEL_KIOSK_CREDENTIAL``, default off): an
     HttpOnly cookie the panel's loopback gate obtains with the device token, naming an ACTIVE panel that allows guests. This is
     what keeps the kiosk browser "the panel" when its address changes (private link home, another Wi-Fi); or
  2. its client address is the address of a REGISTERED, ACTIVE panel that allows guests (``panels.ip_address``, ``allow_guest``).
     The address is nginx's ``X-Real-IP`` (set absolutely from ``$remote_addr``) and is trusted ONLY when the TCP peer is a proxy
     we run (loopback / the docker bridge) - zoe-data's own port answers on the LAN, where a caller could write that header
     itself, so a direct peer is judged by its own address. The same device check ``routers/skybridge.py`` uses for the panel's
     display identity. TRUST ASSUMPTION (accepted residual): the default proxy range is loopback + the docker ``172.16/12``
     because nginx reaches zoe-data from a container address that cannot be told from a sibling container by subnet; every
     container on ``zoe-network`` that can reach :8000 is therefore trusted not to forge ``X-Real-IP``. A request that carries a
     Cloudflare header is refused outright (the tunnel is the one internet-facing hop). To narrow it, pin the nginx container's
     address in ``ZOE_TRUSTED_PROXY_NETS`` (e.g. ``127.0.0.0/8,172.18.0.5/32``).

A request that came through the panel RELAY (``relay_ingress.is_relay``, flag ``ZOE_RELAY_INGRESS``) has no address: path 2 and the
``lan`` scope never apply to it (``trusted_client_ip`` is "" and ``is_panel_guest`` returns before the address rules); only paths 1
and 1b can make it the panel, and ``relay_ingress`` has already required a client certificate bound to the same panel.

Everything else - an anonymous caller, a phone on the Wi-Fi, a guest session from anywhere that is not a registered panel, a
degraded (auth-outage) principal - is NOT the panel and keeps today's rule: a guest touches no list.

``ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE`` (per-call read): ``registered`` (default) is the rule above. ``lan`` is the operator's loosening
for a panel whose ``ip_address`` is not on file: any private-LAN client holding a VALIDATED guest session. ``off`` restores the old
behaviour (no guest ever touches a list).

Voice/chat/skybridge turns carry the verdict as a per-request ContextVar (``mark_panel_turn``) because ``execute_intent`` and the
skybridge resolvers take a bare ``user_id``; the request-level entry points that have authenticated the channel set it. Home
control rides a second, stricter ContextVar (``record_turn_home_control`` / ``home_control_refused``, at the bottom).
"""
from __future__ import annotations

import contextvars
import ipaddress
import logging
import os
from typing import Any, Mapping, Optional

from fastapi import Depends, HTTPException, Request

import panel_kiosk_credential
import relay_ingress
from auth import get_current_user
from log_throttle import log_throttled

logger = logging.getLogger(__name__)

# The actor a household-list write is attributed to when nobody at the panel was identified.
PANEL_ACTOR = "panel"
# Owner id of a household list created on behalf of the panel. ``family-admin`` is seeded by ``database.init_db`` (lists.user_id
# is a foreign key to users, and the shared ``guest`` account has no users row), and it is NOT a person.
HOUSEHOLD_OWNER_ID = "family-admin"
# Owners whose lists any signed-in member may rename/delete: the shared kiosk/household accounts, never a person.
SHARED_OWNER_IDS = frozenset({"guest", "voice-guest", HOUSEHOLD_OWNER_ID})
# The "viewer" id household-scoped queries pass where a member's id would go: it matches no ``lists.user_id``, so a panel guest
# sees family-visible lists only and never an owner's personal one.
HOUSEHOLD_VIEWER = "__household__"

_DEFAULT_PROXY_NETS = "127.0.0.0/8,::1/128,172.16.0.0/12"

_panel_turn: contextvars.ContextVar[bool] = contextvars.ContextVar("zoe_panel_turn", default=False)


def scope_mode() -> str:
    mode = os.environ.get("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "registered").strip().lower()
    return mode if mode in ("registered", "lan", "off") else "registered"


def device_token_via_cf_is_panel() -> bool:
    """``ZOE_PANEL_TOKEN_VIA_CF`` (read per call): may a DEVICE TOKEN presented over the Cloudflare tunnel count as the panel?

    Owner decision 2026-10-19 (decisions-page card ``cf-device-token``): NO. Over Cloudflare a device token is a plain GUEST -
    no home control, no household lists, no music; the way in from outside is the relay (#2095, mTLS + the token), a separate
    ingress with no Cloudflare header, so it is unaffected, and so is the LAN (the panel reaches zoe-data there, never through
    the tunnel). Before this a copy of the panel's token controlled the house from anywhere on the internet.

    ``guest`` is the CODE DEFAULT; anything other than the exact word ``panel`` reads as ``guest`` (a typo never opens the
    house). ``panel`` is an explicit operator ESCAPE HATCH that restores the old behaviour (the token is the panel wherever it
    is presented)."""
    return os.environ.get("ZOE_PANEL_TOKEN_VIA_CF", "guest").strip().lower() == "panel"


def device_token_is_panel(request: Any) -> bool:
    """For entry points that hold a VALIDATED device token but no principal dict (``_require_voice_auth``): does it count as
    the panel for THIS request? False over Cloudflare (unless the ``ZOE_PANEL_TOKEN_VIA_CF=panel`` escape hatch). Never raises."""
    try:
        return not (_via_public_tunnel(request) and not device_token_via_cf_is_panel())
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: device-token tunnel check failed (%s) - not the panel", type(exc).__name__)
        return False


def mark_panel_turn() -> None:
    """Record, for the rest of THIS request's context, that it came from the panel. Never raises."""
    if scope_mode() != "off":
        _panel_turn.set(True)


def in_panel_turn() -> bool:
    return _panel_turn.get()


def is_guest_id(user_id: Optional[str]) -> bool:
    """The shared kiosk/voice guest identities (the strings ``get_current_user`` and the voice lane hand out)."""
    u = (user_id or "").strip()
    return u in ("", "guest", "voice-guest") or u.startswith("guest-")


def household_actor(user: Mapping[str, Any] | None) -> str:
    """The id a household-list write is attributed to: the signed-in member, or ``panel`` when nobody was identified."""
    uid = str((user or {}).get("user_id") or "").strip()
    return PANEL_ACTOR if is_guest_id(uid) else uid


def _networks(raw: str) -> list:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                out.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                logger.warning("panel_principal: ignoring bad proxy network %r", part)
    return out


def _in_nets(addr: str, nets: list) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in nets)


def peer_is_trusted_proxy(peer: str) -> bool:
    """True when the TCP peer is a proxy we run (``ZOE_TRUSTED_PROXY_NETS``; default loopback + the docker ``172.16/12``). The one
    rule behind both ``X-Real-IP`` and the relay ingress marker (``relay_ingress.is_relay``): a header nginx sets is believed only
    from nginx's side of the wire."""
    return bool(peer) and _in_nets(peer, _networks(os.environ.get("ZOE_TRUSTED_PROXY_NETS", _DEFAULT_PROXY_NETS)))


def trusted_client_ip(request: Any) -> str:
    """The caller's address. ``X-Real-IP`` only when the TCP peer is a proxy of ours; otherwise the peer itself.

    A RELAYED request (``relay_ingress.is_relay``) has NO address: the relay hides the client and nginx's relay listener sends no
    ``X-Real-IP`` / ``X-Forwarded-For``, so the peer here is just the nginx container. It returns "" and can match no
    ``panels.ip_address`` (the panel is proven by its device credential and certificate, never located)."""
    if relay_ingress.is_relay(request):
        return ""
    peer = request.client.host if getattr(request, "client", None) else ""
    if peer_is_trusted_proxy(peer):
        real = str(request.headers.get("x-real-ip", "") or "").strip()
        if real:
            return real
    return peer


def _via_public_tunnel(request: Any) -> bool:
    """True when the request carries a Cloudflare edge header. The tunnel's container address sits inside the trusted proxy
    range, and Cloudflare passes a caller's own ``X-Real-IP`` through, so without this a tunnel hop could forge a panel address.
    Cloudflare sets these on every proxied request and a caller cannot remove them. One rule, shared with the kiosk credential."""
    return panel_kiosk_credential.via_public_tunnel(request)


async def _registry_has(db: Any, sql: str, value: str) -> bool:
    if not value:
        return False
    try:
        cur = await db.execute(sql, (value,))
        return await cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is "not the panel" (fail closed), but say so
        logger.warning("panel_principal: panel registry lookup failed (%s) - treating the request as not-a-panel", type(exc).__name__)
        return False


async def _from_registered_panel(db: Any, client_ip: str) -> bool:
    return await _registry_has(
        db, "SELECT 1 FROM panels WHERE ip_address = ? AND is_active = 1 AND allow_guest = 1 LIMIT 1", client_ip)


async def _kiosk_panel_allows_guest(db: Any, panel_id: str) -> bool:
    return await _registry_has(
        db, "SELECT 1 FROM panels WHERE panel_id = ? AND is_active = 1 AND allow_guest = 1 LIMIT 1", panel_id)


async def _with_registry(db: Any, check: Any, value: str) -> bool:
    """Run one registry check on the caller's handle, or on a pooled one when the caller has none (fail closed)."""
    if db is not None:
        return await check(db, value)
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as pooled:
            return await check(pooled, value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel_principal: no database for the panel check (%s)", type(exc).__name__)
        return False


async def is_panel_guest(request: Any, user: Mapping[str, Any] | None, db: Any = None, *, mode: Optional[str] = None) -> bool:
    """True only for a GUEST principal whose request is demonstrably from the panel (see the module docstring).

    ``mode`` defaults to ``ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE`` (the household-list rule). A caller with its own policy passes
    ``"registered"`` to get the strict device / kiosk-credential / registered-address test whatever the list flag says."""
    mode = scope_mode() if mode is None else mode
    if mode == "off" or request is None or not is_guest_id((user or {}).get("user_id")):
        return False
    if (user or {}).get("auth_degraded"):
        return False  # an auth outage resolves ANY header to a guest: never evidence of anything
    if (user or {}).get("panel_id"):
        # Device token, no bound member. Over the Cloudflare tunnel it is a plain guest (owner decision 2026-10-19), unless
        # the ZOE_PANEL_TOKEN_VIA_CF=panel escape hatch restores the old rule.
        return device_token_is_panel(request)       # fail-closed wrapper
    if _via_public_tunnel(request):
        return False  # a request that crossed Cloudflare is the internet, never the kiosk on the LAN
    # Path 1b: the kiosk browser's credential minted from the device token (flag-dark; None whenever the flag is off).
    kiosk_panel = panel_kiosk_credential.credential_panel_id(request)
    if kiosk_panel and await _with_registry(db, _kiosk_panel_allows_guest, kiosk_panel):
        return True
    if relay_ingress.is_relay(request):
        return False  # a relayed request is never matched by address, and never satisfies the ``lan`` scope
    client_ip = trusted_client_ip(request)
    if mode == "lan":
        try:
            private = ipaddress.ip_address(client_ip).is_private
        except ValueError:
            private = False
        return bool(private and str(request.headers.get("X-Session-ID", "")).strip())
    return await _with_registry(db, _from_registered_panel, client_ip)


async def mark_if_panel(request: Any, user: Mapping[str, Any] | None, db: Any = None) -> bool:
    """Request-level entry points (chat, skybridge resolve): mark the turn when the caller is the panel. Never raises."""
    try:
        if await is_panel_guest(request, user, db):
            mark_panel_turn()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel_principal: mark_if_panel failed (%s)", type(exc).__name__)
    return False


# ── The panel's push channel: who may subscribe to ``panel_<id>`` (security follow-up 2026-10-19) ───────────────────────────
#
# ``/ws/push?panel_id=<id>`` admitted ANY socket without a session to a registered panel that allows guests, so anyone on the home
# Wi-Fi could join ``panel_zoe-touch-pi`` and receive the per-turn voice text, member cards and forms the panel shows while a
# member's voice session is open. Rule: a socket with no member session must prove it is THAT panel - its device token (the voice
# daemon, probes; checked where the socket is accepted), its kiosk credential naming the panel, or the panel's own registered
# address (nginx's X-Real-IP from a trusted proxy). The live kiosk browser connects with no session from its registered address,
# so it keeps working with no kiosk-side change.


def panel_push_mode() -> str:
    """``ZOE_PANEL_PUSH_PROOF``: ``enforce`` (default; anything unknown is enforce) refuses an unproven panel subscriber,
    ``shadow`` admits it and logs "would refuse", ``off`` skips the check (the pre-2026-10-19 behaviour, a ROLLBACK). Per call."""
    mode = os.environ.get("ZOE_PANEL_PUSH_PROOF", "enforce").strip().lower()
    return mode if mode in ("shadow", "off") else "enforce"


async def _panel_at_address(db: Any, key: str) -> bool:
    panel_id, _, client_ip = key.partition("\n")
    if not panel_id or not client_ip:
        return False
    try:
        cur = await db.execute(
            "SELECT 1 FROM panels WHERE panel_id = ? AND ip_address = ? AND is_active = 1 LIMIT 1",
            (panel_id, client_ip))
        return await cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001 - fail closed, but say so
        logger.warning("panel_principal: panel address lookup failed (%s) - treating the caller as not-the-panel", type(exc).__name__)
        return False


async def _panel_is_active(db: Any, panel_id: str) -> bool:
    return await _registry_has(db, "SELECT 1 FROM panels WHERE panel_id = ? AND is_active = 1 LIMIT 1", panel_id)


async def request_is_this_panel(request: Any, panel_id: str, db: Any = None) -> bool:
    """True only when ``request`` (an HTTP request or a WebSocket handshake) is demonstrably the panel ``panel_id`` itself - not
    merely "a panel": its kiosk credential names ``panel_id``, or its trusted client address is ``panel_id``'s registered
    ``ip_address``; the row must be active. Never over the Cloudflare tunnel. Never raises. This is IDENTITY only: whether a
    guest may use the panel (``allow_guest``) stays the caller's policy (a guest bound to a members-only panel still proves it)."""
    try:
        pid = str(panel_id or "").strip()
        if not pid or request is None or _via_public_tunnel(request):
            return False
        kiosk_panel = panel_kiosk_credential.credential_panel_id(request)
        if kiosk_panel and kiosk_panel == pid and await _with_registry(db, _panel_is_active, pid):
            return True
        client_ip = trusted_client_ip(request)
        return bool(client_ip) and await _with_registry(db, _panel_at_address, f"{pid}\n{client_ip}")
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: panel proof failed (%s) - refusing", type(exc).__name__)
        return False


# ── Home control: who may drive Home Assistant (lights, switches, covers, locks, media) ─────────────────────────────────────
#
# Security fix 2026-10-17: ``POST /api/ha/control`` accepted ANY principal, and ``get_current_user`` resolves a credential-less
# request as ``guest`` - so every device on the home LAN (guest Wi-Fi visitors, IoT gadgets) could drive the house anonymously.
# Rule: a signed-in MEMBER, or THE PANEL (``is_panel_guest`` in its strict ``registered`` form, independent of the household-list
# flag: a device token, the kiosk credential, or a registered panel address - never ``lan``, which any phone with a guest session
# satisfies, and never ``off``, which would silently break the kiosk's light pins). An anonymous LAN caller, a forged
# X-Real-IP / X-Forwarded-For, anything over the Cloudflare tunnel without a member or a device token, and an auth-outage
# principal are refused.


def home_control_scope() -> str:
    """``panel`` (default, the fix): a member or the panel. ``open``: the pre-fix behaviour (any caller, incl. an anonymous LAN
    guest) - an operator ROLLBACK only, for a kiosk whose address is not registered. Anything else is the safe default.
    Read per call."""
    return "open" if os.environ.get("ZOE_HA_CONTROL_SCOPE", "panel").strip().lower() == "open" else "panel"


def is_member(user: Mapping[str, Any] | None) -> bool:
    """A real signed-in household member (any non-guest role) - never a guest id, never an auth-outage principal, and never the
    header-less caller ``ZOE_UNAUTHENTICATED_ROLE=family-admin`` promotes (that legacy override makes every LAN device an admin;
    home control stays member-or-panel under it, and ``ZOE_HA_CONTROL_SCOPE=open`` is the explicit way to open it)."""
    u = user or {}
    if u.get("auth_degraded") or u.get("unauthenticated_override"):
        return False
    role = str(u.get("role") or "").strip().lower()
    return bool(role) and role != "guest" and not is_guest_id(u.get("user_id"))


async def may_control_home(request: Any, user: Mapping[str, Any] | None, db: Any = None) -> bool:
    """True when this request may drive Home Assistant (or read its entity states): a member, or the panel. Never raises.

    Over the RELAY it is a verified member only (panel-anywhere 5.2, the away-mode rule enforced at the server): the panel
    principal - a device token or kiosk credential, which a stolen panel carries - does not open the house from afar, and
    ``ZOE_HA_CONTROL_SCOPE=open`` (a LAN rollback) does not reach the relay."""
    try:
        if relay_ingress.is_relay(request):
            return is_member(user)
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: relay home-control check failed (%s) - refusing", type(exc).__name__)
        return False
    if home_control_scope() == "open":
        return True
    if is_member(user):
        return True
    try:
        if (user or {}).get("unauthenticated_override"):
            user = {"user_id": "guest", "role": "guest"}  # a header-less caller IS anonymous: judge it as one, by its device
        return await is_panel_guest(request, user, db, mode="registered")
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: home-control panel check failed (%s) - refusing", type(exc).__name__)
        return False


# ── Home control on a TURN: chat, the voice WebSocket, the voice HTTP lanes, the brain's intent dispatch ─────────────────────
#
# Security fix 2026-10-18: "turn off the lights" to /api/chat or /ws/voice/ reached Home Assistant through executors that take a
# bare ``user_id`` (a panel guest and an anonymous phone are both ``guest``). Each TURN ENTRY POINT records ``may_control_home``
# (``record_turn_home_control``); every HA executor checks it right before it acts (``home_control_refused``). Unset = not a
# turn (a scheduler, a self-gating route) = unchanged; False = refuse; True = allow. Rollback: ZOE_HA_CONTROL_SCOPE=open.

HOME_CONTROL_REFUSAL = "I can only control the house for someone in the household. Sign in, or ask me from the wall panel."

_home_control_turn: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar("zoe_home_control_turn", default=None)


def record_home_control(allowed: bool) -> None:
    """Record, for the rest of THIS request's context, whether the turn's caller may drive the house."""
    _home_control_turn.set(bool(allowed))


def home_control_verdict() -> Optional[bool]:
    return _home_control_turn.get()


def home_control_refused(what: str = "") -> bool:
    """True when THIS turn's entry point recorded a caller that may not drive the house. Every Home Assistant executor calls
    it right before it acts. ``ZOE_HA_CONTROL_SCOPE=open`` never refuses (read per call)."""
    if _home_control_turn.get() is not False:
        return False
    if home_control_scope() == "open" and relay_ingress.current_panel() is None:
        return False  # the LAN rollback; it never reaches a RELAYED turn (a stolen panel must not drive the house from afar)
    logger.warning("home control refused%s: the turn's caller is neither a member nor the panel", f" ({what})" if what else "")
    return True


async def record_turn_home_control(request: Any, user: Mapping[str, Any] | None, db: Any = None) -> bool:
    """Turn entry points: judge the caller with ``may_control_home`` and record the verdict. Returns it. Never raises."""
    try:
        allowed = await may_control_home(request, user, db)
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: turn home-control check failed (%s) - refusing", type(exc).__name__)
        allowed = False
    record_home_control(allowed)
    return allowed


# ── FastAPI dependencies for the house-state routes outside ``/api/ha`` (security follow-up 2026-10-18) ──────────────────────
#
# The #2089 audit left routes that serve the same house state, or re-target what the panel controls, to any LAN caller:
# ``GET /api/panels/{id}/sleep-gate`` (is this room lit right now?), ``GET /api/panels/{id}/config`` and ``GET /api/rooms``
# + ``/api/rooms/unassigned`` (live HA state per device). Those READS take the home-control rule (``require_member_or_panel``:
# a member or the panel, incl. its ``ZOE_HA_CONTROL_SCOPE=open`` rollback). The WRITES behind them change which device a dock
# pin or "the light" in a room means, so they take a MEMBER only (``require_member``); the estate already answers that refusal
# with "Sign in to manage rooms" / "Sign in to change panel settings".


def _route_label(request: Any) -> str:
    try:
        return f"{request.method} {request.url.path}"[:160]
    except Exception:  # noqa: BLE001
        return "?"


def _refuse(kind: str, request: Any, user: Mapping[str, Any] | None, detail: str) -> HTTPException:
    label = _route_label(request)
    log_throttled(
        logger, logging.WARNING, f"{kind}:{label}",
        "refused %s: %s (user=%s)", label, kind, str((user or {}).get("user_id"))[:64],
    )
    return HTTPException(status_code=403, detail=detail)


async def require_member_or_panel(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """A signed-in member or the panel (``may_control_home``); anyone else gets 403."""
    if await may_control_home(request, user):
        return user
    raise _refuse("neither a member nor the panel", request, user, "Sign in, or use the wall panel")


async def require_member(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """A signed-in household member (``is_member``): never a guest, the panel (device token, kiosk credential, registered
    address), an auth-outage principal or the ``ZOE_UNAUTHENTICATED_ROLE`` promotion. 403 otherwise.

    A principal carrying device-token or panel identity (``device_token_role`` / ``panel_id``) is refused even when it has a
    member role: under the ``ZOE_DEVICE_TOKEN_BINDS_MEMBER=1`` rollback a device token resolves to its bound member, and the
    token on the panel's disk must still not re-target rooms or dock pins (cross-review, #2099)."""
    u = user or {}
    if "device_token_role" not in u and not u.get("panel_id") and is_member(u):
        return user
    raise _refuse("not a signed-in member", request, user, "Sign in to change this")


# ── Gated web UIs behind an nginx ``auth_request`` (Home Assistant's /ha/; ``path_cookie_gate``) ─────────────────────────────


async def member_or_panel(request: Any, user: Mapping[str, Any] | None) -> bool:
    """A signed-in member, or the panel in its strict ``registered`` form (device token, kiosk credential, registered address -
    never over the Cloudflare tunnel, never ``lan``). Unlike ``may_control_home`` this ignores the ``ZOE_HA_CONTROL_SCOPE=open``
    rollback: that is a home-control rollback, and every gated web UI has its own kill switch. Never raises (fail closed)."""
    try:
        if is_member(user):
            return True
        u = user or {}
        if u.get("unauthenticated_override"):
            u = {"user_id": "guest", "role": "guest"}  # a header-less caller IS anonymous: judge it as one, by its device
        return await is_panel_guest(request, u, None, mode="registered")
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("panel_principal: member-or-panel check failed (%s) - refusing", type(exc).__name__)
        return False

"""Relay ingress: how zoe-data treats a request that reached the box through the shared end-to-end encrypted panel relay
(docs/architecture/panel-anywhere.md sections 5 and 6, phase R1; flag ``ZOE_RELAY_INGRESS``, default OFF).

The relay carries ciphertext to the box's nginx RELAY LISTENER (``services/zoe-ui/nginx.d/relay-listener.conf``), which
terminates TLS, REQUIRES a client certificate signed by the household CA (``ssl_verify_client on``) and forwards to zoe-data
with two headers set absolutely (a client-sent value is overwritten; every LAN server block clears both):

  * ``X-Zoe-Ingress: relay``
  * ``X-Zoe-Client-Cert-Fp: <lower-case hex SHA-1 of the client certificate>`` (nginx ``$ssl_client_fingerprint``)

What a relayed request is NOT: it has no address. ``X-Real-IP`` / ``X-Forwarded-For`` are empty (the nginx listener clears
them) and zoe-data would ignore them anyway, so a relayed request never matches ``panels.ip_address``, never satisfies
``ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE=lan`` and never looks like "the home LAN". Identity is proven, not located.

The rules this module owns (every one has a negative control in ``tests/test_relay_ingress.py``):

  1. The marker counts ONLY when the flag is on AND the TCP peer is the zoe-ui nginx container itself (``ZOE_RELAY_TRUSTED_PEER``,
     a single address - not the docker ``172.16/12`` that ``ZOE_TRUSTED_PROXY_NETS`` trusts for ``X-Real-IP``). Flag off: the header
     is inert and the request is judged exactly as it is today. Flag on and the marker from any other peer: refused (403) by the
     middleware - a forgery, or a stale address after the container was recreated, never a silent fall-through to the LAN rules.
  2. NEVER ``guest``. A relayed request must carry a valid DEVICE TOKEN (``X-Device-Token``) or a valid KIOSK CREDENTIAL
     (``zoe_panel_kiosk`` cookie, ``ZOE_PANEL_KIOSK_CREDENTIAL``), else 401. ``auth.get_current_user``'s anonymous-guest fallback
     and ``ZOE_UNAUTHENTICATED_ROLE`` never apply; a zoe-auth outage is refused (503), never downgraded.
  3. The client certificate is bound to ONE panel: ``panel_client_certs.fingerprint -> panel_id`` (alembic 0059, operator-bound
     through ``routers/panel_relay.py``). The credential's panel must equal the certificate's panel; a certificate with no
     credential, a credential with no bound certificate, a revoked or unknown fingerprint, an inactive panel and a mismatch are
     all 401. A device token or kiosk cookie copied off one panel is therefore useless with another panel's certificate.
  4. The device credential resolves to the PANEL principal only (guest-class + ``panel_id``), whatever ``panel_user_bindings``
     says and whatever ``ZOE_DEVICE_TOKEN_BINDS_MEMBER`` is set to. A MEMBER is accepted only when the request also carries that
     member's valid zoe-auth session AND the panel has a FRESH PIN panel session or an open voice session for the same member
     (the voice lanes' own freshness authority, ``voice_tts._resolve_fresh_panel_member`` / ``voice_session.active_member``).
     Anything else is the panel principal, which every member route already refuses.
  5. Home control over the relay needs a member (``panel_principal.may_control_home``): the away-mode rule, enforced at the
     server.

``RelayIngressMiddleware`` applies rules 2 and 3 to EVERY http and websocket request before routing, so no route can forget;
``get_current_user`` applies the same checks again (defence in depth, and so a route mounted without the middleware is still
safe). ``auth._is_internal_request`` is not involved and must never be widened for this.
"""
from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from log_throttle import log_throttled

logger = logging.getLogger(__name__)

INGRESS_HEADER = "x-zoe-ingress"
FP_HEADER = "x-zoe-client-cert-fp"
RELAY_VALUE = "relay"
#: The one path a relayed request may reach with a certificate alone: the kiosk's ticket redemption mints the kiosk credential,
#: so it cannot require one. The ticket is the credential there (single use, 60 s), and ``routers/panel_kiosk.py`` checks that the
#: certificate's panel is the ticket's panel.
NO_CREDENTIAL_PATHS = frozenset({"/api/panels/kiosk-enter"})

_FP_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# ``<hid>.zoe.internal``: the household id is random, lower-case, letters/digits/hyphen (never derived from a name).
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{4,60}[a-z0-9])\.zoe\.internal$")
_BOUND_CACHE_TTL_S = 5.0
_BOUND_SQL = (
    "SELECT c.panel_id AS panel_id FROM panel_client_certs c JOIN panels p ON p.panel_id = c.panel_id "
    "WHERE c.fingerprint = ? AND c.revoked_at IS NULL AND p.is_active = 1 LIMIT 1"
)

_bound_cache: dict[str, tuple[Optional[str], float]] = {}
#: Bumped by ``invalidate_cache`` (every admin bind / revoke). An open socket whose last proof predates the current epoch re-proves
#: on its very next outbound send, so an in-process revocation stops a broadcast at once rather than after ``WS_SEND_PROOF_TTL_S``.
_epoch = 0
#: Where the per-request proof lives on the ASGI scope (``scope["state"]`` is the namespace Starlette fills with lifespan state, so a
#: bare ``relay_ctx`` could collide with an app's own key; this one is ours and namespaced).
_CTX_KEY = "zoe.relay_ingress.ctx"
#: How often an OPEN relayed websocket re-proves its certificate binding and device credential on a TIMER (revocation must not wait
#: for a reconnect, nor for the client to send a frame - a push-only socket sends none). The timer proof reads the registry,
#: not the 5 s cache, so an out-of-band DB edit (or another worker's revocation) is seen within this interval. Seconds; a module
#: constant so a test can shrink it.
WS_RECHECK_S = 15.0
#: How long a proof is trusted for OUTBOUND sends: before each ``websocket.send`` the socket re-proves (cached registry read,
#: in-memory token lookup - no DB round trip inside the cache TTL) unless it was proven this recently and no revocation happened in
#: this process since. Short, so a broadcast to a revoked socket is stopped in seconds; cheap, so broadcasts are not slowed.
WS_SEND_PROOF_TTL_S = 2.0
#: Upper bound on writing the 1008 close of a revoked socket (a stalled client can block even that). Past it the guard gives up on the
#: close frame; the app side still ends (the handler is handed a disconnect) and the server tears the connection down.
WS_CLOSE_TIMEOUT_S = 2.0
#: The panel a RELAYED request was proven to be, for code that has no ``Request`` in hand (the websocket session resolvers in main.py).
_relay_panel: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("zoe_relay_panel", default=None)


class RelayRefused(Exception):
    """A relayed request that may not proceed. ``code`` is a short machine-readable reason (also the 401/503 body)."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class RelayContext:
    """What a relayed request proved: the panel its certificate is bound to, and (unless the path is a no-credential path)
    the device credential that names the same panel."""

    panel_id: str
    fingerprint: str
    credential: Optional[str]  # "device_token" | "kiosk" | None (kiosk-enter only)
    role: str = "kiosk"


# ── flags ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def enabled() -> bool:
    """``ZOE_RELAY_INGRESS`` (default OFF, read per call). Off: the relay headers are inert and nothing here runs."""
    return os.environ.get("ZOE_RELAY_INGRESS", "").strip().lower() in ("1", "true", "yes", "on")


def hostname() -> Optional[str]:
    """``ZOE_RELAY_HOSTNAME`` = ``<hid>.zoe.internal``, or None when unset or not a household name (logged, never trusted)."""
    raw = os.environ.get("ZOE_RELAY_HOSTNAME", "").strip().lower().rstrip(".")
    if not raw:
        return None
    if not _HOST_RE.match(raw):
        logger.warning("relay_ingress: ZOE_RELAY_HOSTNAME is not a <hid>.zoe.internal name - no relay origin is allowed")
        return None
    return raw


def origin() -> Optional[str]:
    """The browser/push-socket origin the kiosk presents over the relay, derived from ``ZOE_RELAY_HOSTNAME`` - only while the
    ingress flag is on. Without it the push socket refuses the kiosk (the 2026-07-03 incident class)."""
    host = hostname() if enabled() else None
    return f"https://{host}" if host else None


# ── is this request relayed? ────────────────────────────────────────────────────────────────────────────────────────────


def trusted_peer_nets() -> list:
    """``ZOE_RELAY_TRUSTED_PEER``: the address(es) of the zoe-ui nginx container - the only TCP peer whose relay marker is believed.

    Deliberately NOT ``ZOE_TRUSTED_PROXY_NETS`` (default loopback + the whole docker ``172.16/12``, which also serves ``X-Real-IP``
    and must not change): any container on the bridge could then assert the marker. Only SINGLE ADDRESSES count (``172.18.0.5`` or
    ``172.18.0.5/32``); a wider network is ignored with a warning. Unset = nobody is trusted: with the flag on, every request that
    carries the marker is refused (403) until the address is set - closed, never downgraded to the LAN rules. The nginx container's
    address changes when the container is recreated: ``docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
    zoe-ui`` (``enable_relay_listener.py`` checks it)."""
    out = []
    for part in os.environ.get("ZOE_RELAY_TRUSTED_PEER", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            logger.warning("relay_ingress: ignoring bad ZOE_RELAY_TRUSTED_PEER entry %r", part)
            continue
        if net.prefixlen != net.max_prefixlen:
            logger.warning("relay_ingress: ignoring ZOE_RELAY_TRUSTED_PEER entry %r: a single address (the nginx container), not a network", part)
            continue
        out.append(net)
    return out


def peer_is_nginx(peer: str) -> bool:
    import panel_principal  # late: panel_principal imports this module

    return bool(peer) and panel_principal._in_nets(peer, trusted_peer_nets())


def _marker(conn: Any) -> bool:
    return str(conn.headers.get(INGRESS_HEADER, "") or "").strip().lower() == RELAY_VALUE


def _peer(conn: Any) -> str:
    return conn.client.host if getattr(conn, "client", None) else ""


def marker_from_untrusted_peer(conn: Any) -> bool:
    """Flag on, the relay marker present, but the TCP peer is not the nginx container. Nginx always writes the marker (and the LAN
    blocks always clear it), so this is a forgery or a misconfigured ``ZOE_RELAY_TRUSTED_PEER`` - and in the second case falling
    through to the LAN rules would treat a relayed request as a LAN one. The middleware refuses it (403)."""
    return conn is not None and enabled() and _marker(conn) and not peer_is_nginx(_peer(conn))


def is_relay(conn: Any) -> bool:
    """True only when the flag is on, the TCP peer is the zoe-ui nginx container (``ZOE_RELAY_TRUSTED_PEER``) AND the request
    carries ``X-Zoe-Ingress: relay``."""
    if conn is None or not _marker(conn):
        return False
    if not enabled():
        log_throttled(
            logger, logging.WARNING, "relay-marker-flag-off",
            "relay_ingress: a request carries X-Zoe-Ingress: relay but ZOE_RELAY_INGRESS is off - treated as LAN. "
            "Turn the flag on (and restart) BEFORE enabling the nginx relay listener.",
        )
        return False
    return peer_is_nginx(_peer(conn))


def client_fingerprint(conn: Any) -> Optional[str]:
    """The client certificate fingerprint nginx forwarded, normalised (lower case, no colons), or None if absent/malformed."""
    raw = str(conn.headers.get(FP_HEADER, "") or "").strip().lower().replace(":", "")
    return raw if _FP_RE.match(raw) else None


# ── the certificate -> panel binding ────────────────────────────────────────────────────────────────────────────────────


def invalidate_cache() -> None:
    """Forget cached fingerprint lookups (the admin endpoints call this on every bind / revoke, so revocation is immediate
    in-process; an out-of-band DB edit takes effect within ``_BOUND_CACHE_TTL_S`` for HTTP and within ``WS_RECHECK_S`` for an open
    socket).

    MULTI-WORKER CAVEAT: this clears THIS process only. zoe-data runs as ONE uvicorn worker today. With more than one, a revocation
    through the admin API reaches the other workers only by expiry: the certificate cache (``_BOUND_CACHE_TTL_S``, 5 s) for HTTP,
    ``WS_RECHECK_S`` (15 s, registry read) for open sockets, and - NOT covered by any TTL - the in-memory device-token cache
    (``routers.panel_auth._token_cache``), which another worker refreshes only on its next reload/restart. Revoke the certificate,
    not just the token, and do not raise ``--workers`` without a shared invalidation channel."""
    global _epoch
    _epoch += 1
    _bound_cache.clear()


async def _fetch_bound_panel(fingerprint: str) -> Optional[str]:
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        cur = await db.execute(_BOUND_SQL, (fingerprint,))
        row = await cur.fetchone()
    return str(row["panel_id"]) if row else None


async def bound_panel(fingerprint: str, *, fresh: bool = False) -> Optional[str]:
    """The ACTIVE panel this certificate fingerprint is bound to (not revoked), or None. A registry that cannot be read is a
    503, never a pass. ``fresh`` skips the 5 s cache (the open-socket timer proof)."""
    now = time.monotonic()
    hit = None if fresh else _bound_cache.get(fingerprint)
    if hit and hit[1] > now:
        return hit[0]
    try:
        panel = await _fetch_bound_panel(fingerprint)
    except Exception as exc:  # noqa: BLE001 - fail closed, but say why
        logger.warning("relay_ingress: certificate registry lookup failed (%s) - refusing", type(exc).__name__)
        raise RelayRefused(503, "relay_registry_unavailable") from exc
    if len(_bound_cache) > 512:
        _bound_cache.clear()
    _bound_cache[fingerprint] = (panel, now + _BOUND_CACHE_TTL_S)
    return panel


# ── the device credential ───────────────────────────────────────────────────────────────────────────────────────────────


def _credential(conn: Any) -> tuple[Optional[str], Optional[str], str]:
    """(panel_id, kind, role) from the device token and/or kiosk credential on the request; all presented valid credentials
    must name the same panel. A PRESENTED device token that is not valid (revoked, expired, unknown) is a refusal outright:
    the stolen-panel runbook is that the daemon gets 401 at once."""
    from routers.panel_auth import lookup_device_token  # late: panel_auth imports auth

    import panel_kiosk_credential

    token_panel: Optional[str] = None
    role = "kiosk"
    raw = str(conn.headers.get("x-device-token", "") or "")
    if raw:
        info = lookup_device_token(raw)
        if not info:
            raise RelayRefused(401, "relay_device_token_invalid")
        token_panel = str(info.get("panel_id") or "") or None
        role = str(info.get("role") or "voice")
    kiosk_panel = panel_kiosk_credential.credential_panel_id(conn)
    if token_panel and kiosk_panel and token_panel != kiosk_panel:
        raise RelayRefused(401, "relay_credentials_disagree")
    if token_panel:
        return token_panel, "device_token", role
    if kiosk_panel:
        return kiosk_panel, "kiosk", role
    return None, None, role


async def authenticate(conn: Any, *, require_credential: bool = True, fresh: bool = False) -> RelayContext:
    """Prove a relayed request: certificate fingerprint -> bound active panel, and (unless ``require_credential`` is False) a
    device token / kiosk credential for that same panel. Raises ``RelayRefused`` (401, or 503 if the registry is down). ``fresh``
    reads the registry past the cache."""
    fingerprint = client_fingerprint(conn)
    if not fingerprint:
        raise RelayRefused(401, "relay_client_certificate_required")
    panel, kind, role = _credential(conn) if require_credential else (None, None, "kiosk")
    if require_credential and not panel:
        raise RelayRefused(401, "relay_device_credential_required")
    bound = await bound_panel(fingerprint, fresh=fresh)
    if not bound:
        raise RelayRefused(401, "relay_certificate_not_bound")
    if require_credential and panel != bound:
        raise RelayRefused(401, "relay_certificate_credential_mismatch")
    return RelayContext(panel_id=bound, fingerprint=fingerprint, credential=kind, role=role)


async def context(conn: Any, *, require_credential: bool = True) -> RelayContext:
    """``authenticate`` once per request (cached on the ASGI scope, so the middleware's proof is reused by the dependency)."""
    state = conn.scope.setdefault("state", {})
    cached = state.get(_CTX_KEY)
    if isinstance(cached, RelayContext) and (cached.credential is not None or not require_credential):
        return cached
    ctx = await authenticate(conn, require_credential=require_credential)
    state[_CTX_KEY] = ctx
    return ctx


async def cert_matches_panel(conn: Any, panel_id: str) -> bool:
    """True when the request is not relayed, or its certificate is bound to ``panel_id`` (for the no-credential path)."""
    if not is_relay(conn):
        return True
    try:
        return (await context(conn, require_credential=False)).panel_id == str(panel_id)
    except RelayRefused:
        return False


# ── who is the caller? ──────────────────────────────────────────────────────────────────────────────────────────────────


async def _fresh_member_ok(panel_id: str, user_id: str) -> bool:
    """The panel has a FRESH PIN sign-in for this member: the PIN panel session inside the idle-logout window (the voice lanes'
    single authority - no second TTL). Anything unreadable is "not fresh".

    Deliberately NOT ``voice_session.active_member``: a voice session opens on a voice CLAIM the panel itself reports
    (``identified_user_id`` + a score in the voice payload), so over the relay the device that is distrusted could mint the very
    proof that unlocks member routes. It joins when the voice lanes verify claims server-side (ledger 2026-10-17)."""
    try:
        from routers.voice_tts import _resolve_fresh_panel_member

        return await _resolve_fresh_panel_member(panel_id) == user_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("relay_ingress: fresh panel session check failed (%s) - treating as not fresh", type(exc).__name__)
        return False


def current_panel() -> Optional[str]:
    """The panel the current relayed request/socket was proven to be (set by the middleware), or None (not relayed)."""
    return _relay_panel.get()


def panel_subscription_allowed(panel_id: str) -> bool:
    """For the push socket: True when the request is not relayed, or ``panel_id`` is the panel it was proven to be."""
    panel = _relay_panel.get()
    return True if panel is None else str(panel_id or "") == panel


async def member_allowed_here(user_id: str) -> bool:
    """For code that resolves a member from a bare session id (the websocket handlers): True when the request is not relayed, or
    the member has a fresh sign-in at the panel the relayed request was proven to be."""
    panel = _relay_panel.get()
    return True if panel is None else await _fresh_member_ok(panel, str(user_id or ""))


async def resolve_user(conn: Any) -> dict:
    """``auth.get_current_user`` for a relayed request. Never guest, never an address; the panel principal, or a fresh member."""
    from fastapi import HTTPException

    import auth
    from routers.panel_auth import _panel_principal

    try:
        ctx = await context(conn)
    except RelayRefused as refused:
        raise HTTPException(status_code=refused.status, detail=refused.code) from refused
    principal = _panel_principal(ctx.panel_id, ctx.role)
    principal["ingress"] = RELAY_VALUE

    session_id = str(conn.headers.get("x-session-id", "") or "")
    if not session_id:
        return principal
    validated = auth._cache_get(session_id)
    if not validated:
        validated = await auth._validate_with_auth_service(session_id)
        if validated is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        if validated.get(auth._DEGRADED_MARK) or validated.get("auth_degraded"):
            # zoe-auth is down and the box is configured to fail open: over the relay that is a refusal, never a guest.
            raise HTTPException(status_code=503, detail="Authentication service unavailable")
        auth._cache_set(session_id, validated)
    uid = str(validated.get("user_id") or "")
    role = str(validated.get("role") or "").strip().lower()
    if not uid or role == "guest" or uid == auth.GUEST_USER_ID or uid.startswith("guest-"):
        return principal  # a guest session proves nothing; the credential already named the panel
    if not await _fresh_member_ok(ctx.panel_id, uid):
        return principal  # a member's session without a fresh sign-in AT the panel: the panel, nothing personal
    member = dict(validated)
    member["ingress"] = RELAY_VALUE
    member["relay_panel_id"] = ctx.panel_id
    return member


# ── the gate every relayed request passes before routing ───────────────────────────────────────────────────────────────


class RelayIngressMiddleware:
    """Pure ASGI middleware: a relayed http/websocket request without a bound certificate AND a matching device credential is
    refused before any route runs. A request that is not relayed is passed through untouched (flag off = this is a no-op)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        from starlette.requests import HTTPConnection

        conn = HTTPConnection(scope)
        if marker_from_untrusted_peer(conn):
            log_throttled(logger, logging.WARNING, "relay-marker-untrusted-peer",
                          "relay_ingress: refused a request carrying X-Zoe-Ingress from %s, which is not ZOE_RELAY_TRUSTED_PEER (the nginx container)",
                          _peer(conn))
            if scope["type"] == "websocket":
                from starlette.websockets import WebSocket

                await WebSocket(scope, receive, send).close(code=1008)
                return
            from starlette.responses import JSONResponse

            await JSONResponse({"detail": "relay_marker_untrusted_peer"}, status_code=403)(scope, receive, send)
            return
        if not is_relay(conn):
            await self.app(scope, receive, send)
            return
        try:
            ctx = await context(conn, require_credential=scope.get("path") not in NO_CREDENTIAL_PATHS)
        except RelayRefused as refused:
            log_throttled(
                logger, logging.WARNING, f"relay-refused:{refused.code}",
                "relay_ingress: refused %s %s (%s)", scope.get("type"), scope.get("path"), refused.code,
            )
            if scope["type"] == "websocket":
                from starlette.websockets import WebSocket

                await WebSocket(scope, receive, send).close(code=1008)
                return
            from starlette.responses import JSONResponse

            await JSONResponse({"detail": refused.code}, status_code=refused.status)(scope, receive, send)
            return
        token = _relay_panel.set(ctx.panel_id)
        guard: Optional[_SocketGuard] = None
        try:
            if scope["type"] == "websocket":
                guard = _SocketGuard(conn, scope, receive, send, require_credential=scope.get("path") not in NO_CREDENTIAL_PATHS)
                receive, send = guard.receive, guard.send
                guard.start()
            await self.app(scope, receive, send)
        finally:
            if guard is not None:
                await guard.stop()
            _relay_panel.reset(token)


class _SocketGuard:
    """Keeps a relayed websocket honest for as long as it is open: revoking the certificate or the device token ends the socket
    even when the client never sends a frame (``/ws/push`` is push-only) and the server only ever sends.

    Three triggers, one proof (``_prove``: the certificate binding + the device credential, as at the handshake):
      * a TIMER, every ``WS_RECHECK_S``, reading the registry past its cache - bounds the lag of any revocation, however it
        happened, to one interval;
      * BEFORE EACH OUTBOUND ``websocket.send``, unless the socket was proven within ``WS_SEND_PROOF_TTL_S`` and nothing was
        invalidated in this process since (``_epoch``) - so a broadcast is not delivered to a socket that already fails;
      * an inbound frame more than ``WS_RECHECK_S`` after the last proof (belt and braces beside the timer).

    A failed proof closes the socket with 1008 (policy violation), wakes the handler blocked in ``receive`` with a disconnect, and
    turns every later send into ``OSError`` (Starlette maps it to ``WebSocketDisconnect``, which the broadcaster already treats as a
    dead socket). A LAN socket never gets a guard. A transient registry failure (503) also closes: fail closed, the panel reconnects.

    Residual race, by construction: a send that passed its proof an instant BEFORE a concurrent revocation can still put that one
    frame on the wire (proof and send are not atomic). Any broadcast that STARTS after the revoke returned is stopped (the epoch
    forces a re-proof), and the timer closes the socket within one interval."""

    def __init__(self, conn: Any, scope: dict, receive: Any, send: Any, *, require_credential: bool):
        self._conn, self._scope, self._receive, self._send = conn, scope, receive, send
        self._require_credential = require_credential
        self._proved_at = time.monotonic()
        self._proved_epoch = _epoch
        self._dead = asyncio.Event()
        self._finished = False  # the app (or the peer) already ended the socket: nothing left to close
        self._send_lock = asyncio.Lock()
        self._inflight: Optional[asyncio.Future] = None  # the frame currently being written, if any (cancelled on revocation)
        self._prove_lock = asyncio.Lock()
        self._timer: Optional[asyncio.Future] = None

    def start(self) -> None:
        self._timer = asyncio.ensure_future(self._tick())

    async def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            try:
                await self._timer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the timer is ours; its end is not the request's problem
                pass

    def _stale(self) -> bool:
        return _epoch != self._proved_epoch or time.monotonic() - self._proved_at >= WS_SEND_PROOF_TTL_S

    async def _prove(self, *, fresh: bool, only_if_stale: bool = False) -> bool:
        async with self._prove_lock:
            if self._dead.is_set():
                return False
            if only_if_stale and not self._stale():
                return True  # a concurrent send already re-proved while this one waited for the lock
            epoch = _epoch
            self._scope.get("state", {}).pop(_CTX_KEY, None)
            try:
                ctx = await authenticate(self._conn, require_credential=self._require_credential, fresh=fresh)
            except RelayRefused as refused:
                log_throttled(logger, logging.WARNING, f"relay-ws-revoked:{refused.code}",
                              "relay_ingress: closing open websocket %s (%s)", self._scope.get("path"), refused.code)
                await self._kill()
                return False
            except Exception as exc:  # noqa: BLE001 - a proof that cannot be completed is not a proof: fail closed, and say why
                logger.warning("relay_ingress: re-proof of open websocket %s failed unexpectedly (%s) - closing it",
                               self._scope.get("path"), type(exc).__name__)
                await self._kill()
                return False
            self._scope.setdefault("state", {})[_CTX_KEY] = ctx
            self._proved_at, self._proved_epoch = time.monotonic(), epoch
            return True

    async def _kill(self) -> None:
        self._dead.set()
        if self._finished:
            return
        # A data frame the server is still writing to a STALLED client (backpressure) holds ``_send_lock`` until the transport drains,
        # which may be never: abandon it, or the 1008 below queues behind network I/O and the 15 s promise is not kept. The frame is
        # for a revoked socket that is being closed anyway; its sender gets ``OSError`` (see ``_send_frame``).
        inflight = self._inflight
        if inflight is not None and not inflight.done():
            inflight.cancel()
        try:
            await asyncio.wait_for(self._send_close(), WS_CLOSE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - already closing/closed by the app or the peer, or the close itself stalled (the timeout);
            pass           # either way the disconnect handed to the handler still ends the app side

    async def _send_close(self) -> None:
        async with self._send_lock:
            await self._send({"type": "websocket.close", "code": 1008})

    async def _send_frame(self, message: dict) -> None:
        """The underlying send as a task of its own, so a revocation can cancel it while it is blocked on a stalled client."""
        inflight = asyncio.ensure_future(self._send(message))
        self._inflight = inflight
        try:
            await asyncio.wait({inflight})  # wait() does not raise the task's own cancellation; the caller's cancellation it does
        except asyncio.CancelledError:
            inflight.cancel()
            raise
        finally:
            if self._inflight is inflight:
                self._inflight = None
        if inflight.cancelled():
            raise OSError("relayed websocket revoked")
        inflight.result()

    async def _tick(self) -> None:
        while not self._dead.is_set() and not self._finished:
            await asyncio.sleep(max(WS_RECHECK_S, 0.05))
            if self._finished or not await self._prove(fresh=True):
                return

    async def send(self, message: dict) -> None:
        kind = message.get("type")
        if self._dead.is_set():
            # Already closed by the guard (1008): a data frame is refused (OSError -> WebSocketDisconnect in Starlette, which the
            # broadcaster treats as a dead socket); the app's own accept/close that races the revocation is swallowed, since the
            # server would raise on a second close.
            if kind == "websocket.send":
                raise OSError("relayed websocket revoked")
            return
        if kind == "websocket.close":
            self._finished = True
        elif kind == "websocket.send" and self._stale():
            if not await self._prove(fresh=False, only_if_stale=True):
                raise OSError("relayed websocket revoked")
        async with self._send_lock:
            if self._dead.is_set():  # revoked while waiting for the lock
                if kind == "websocket.send":
                    raise OSError("relayed websocket revoked")
                return
            await self._send_frame(message)

    async def receive(self) -> dict:
        if self._dead.is_set():
            return {"type": "websocket.disconnect", "code": 1008}
        pending = asyncio.ensure_future(self._receive())
        gone = asyncio.ensure_future(self._dead.wait())
        try:
            await asyncio.wait({pending, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            gone.cancel()
        if not pending.done():
            pending.cancel()  # revoked while idle: hand the handler a disconnect instead of leaving it parked
            pending.add_done_callback(lambda t: t.cancelled() or t.exception())  # retrieve any late error: no "never retrieved" noise
            return {"type": "websocket.disconnect", "code": 1008}
        message = pending.result()
        kind = message.get("type")
        if kind == "websocket.disconnect":
            self._finished = True
        elif kind == "websocket.receive" and time.monotonic() - self._proved_at >= WS_RECHECK_S:
            if not await self._prove(fresh=True):
                return {"type": "websocket.disconnect", "code": 1008}
        return message

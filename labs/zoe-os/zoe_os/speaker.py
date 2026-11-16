"""Speaker assertions: proof of WHO is speaking, from the component that verifies people.

A surface (the panel, the voice adapter) relays a person's decision; it does not get to say which person. A person-bound request
(``forget``, ``erase_member``, ``erase_item``, ``grant``, ``revoke``, ``undo``, ``explain`` for a person) carries a ``speaker``
assertion minted by the ``speaker-verifier`` identity (in production: the zoe-data code that has just verified the voice or the
panel session). The surface can pass it along; without the identity secret it cannot mint one.

A token is ``<body>.<mac>``: ``body`` = unpadded base64url of ``{"p": person, "t": issued_ms, "n": nonce_hex, "a": audience}``,
``mac`` = hex HMAC-SHA256(secret, DOMAIN + body). The kernel accepts it only when the MAC is right (constant-time compare), the
person and the audience are what the request needs, it was issued after this kernel started, no more than ``TTL_S`` ago and no more
than ``SKEW_S`` ahead, and its nonce has not been used. Every failure is the same refusal (``UNPROVEN``); the reason is counted
by a fixed name and never logged with the token. Stdlib only; verification is O(1) (the nonce store prunes from its oldest end).
"""
from __future__ import annotations

import base64
import binascii
import collections
import hashlib
import hmac
import json
import secrets
import time
from typing import Callable

IDENTITY = "speaker-verifier"              # the identity that mints: never a connection, never a manifest
IDENTITY_CREDS = "identity-creds.json"     # its secret, 0600, apart from creds.json and erase-creds.json
TTL_S = 60.0                               # a spoken decision is relayed within seconds; a minute covers a slow turn
SKEW_S = 2.0                               # same host: a token from the future beyond this is refused
MAX_TOKEN = 512                            # checked before anything is decoded
MAX_LIVE = 1024                            # used nonces kept for their TTL; full = refuse (fail closed), never forget one early
UNPROVEN = "speaker unproven"
_DOMAIN = b"zoe-os speaker assertion v1\0"

#: who must be proven, per person-bound op. The ONE table: the server and the lab client both read ``binding``.
PERSON_BOUND = frozenset({"forget", "erase_member", "erase_item", "grant", "revoke", "undo", "explain"})
#: what the member-delete path's ``eraser`` credential may do without an assertion: the forget family, nothing else
ERASER_MAY = frozenset({"forget", "erase_member", "erase_item"})
AUDIENCES = frozenset({"forget.person", "forget.scoped", "grant", "revoke", "undo", "explain"})


def binding(op: str, args: dict, is_child: Callable[[str], bool] | None = None) -> tuple[object, str] | None:
    """``(who must be proven speaking, audience)`` for a person-bound request, ``None`` for any other.

    The person is the request's SPEAKING field: ``actor`` for forget and undo (the kernel's own rule then decides whether that
    actor may act for the subject: themself, or a guardian for their child), the guardian for a child's grant, else the person
    the request names. A whole-person forget and a scoped one are different audiences, so "forget that" cannot become "forget me".
    ``is_child`` (the kernel's roster) makes a guardian count only for a child; without it (a client) the guardian is assumed."""
    if op == "forget":
        scoped = any(args.get(k) is not None for k in ("seqs", "entity", "topic"))
        return args.get("actor"), "forget.scoped" if scoped else "forget.person"
    if op == "erase_member":
        return args.get("subject"), "forget.person"
    if op == "erase_item":
        return args.get("subject"), "forget.scoped"
    if op == "grant":
        guardian, person = args.get("guardian"), args.get("person")
        child = guardian is not None and (is_child is None or is_child(person))
        return (guardian if child else person), "grant"
    if op in ("revoke", "undo"):
        return args.get("person" if op == "revoke" else "actor"), op
    if op == "explain" and "person" in args:
        return args.get("person"), "explain"
    return None


def _mac(key: bytes, body: bytes) -> bytes:
    return hmac.new(key, _DOMAIN + body, hashlib.sha256).hexdigest().encode()


def mint(key: bytes, person: str, audience: str, *, now: float | None = None, nonce: str | None = None) -> str:
    """What the speaker-verifier hands the surface after it verified ``person``. Never logged."""
    if audience not in AUDIENCES:
        raise ValueError("unknown audience")
    claims = {"p": person, "t": int((time.time() if now is None else now) * 1000), "n": nonce or secrets.token_hex(16),
              "a": audience}
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=")
    return (body + b"." + _mac(key, body)).decode()


def mint_for(key: bytes, op: str, args: dict, *, now: float | None = None) -> str | None:
    """The assertion a lab client attaches to ``op`` (``None``: the op is not person-bound, or names nobody)."""
    bound = binding(op, args)
    if bound is None or not isinstance(bound[0], str):
        return None
    return mint(key, bound[0], bound[1], now=now)


class Verifier:
    """The kernel's side. ``clock`` is injectable (tests); ``started_ms`` is when this kernel started: an assertion issued
    before it is refused, so a token captured before a restart is dead after it even when the secret is provisioned and kept
    (the used-nonce store is in memory, and this rule is what makes losing it at a restart safe)."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time, ttl: float = TTL_S,
                 max_live: int = MAX_LIVE) -> None:
        if len(key) < 32:
            raise ValueError("a speaker secret is at least 32 bytes")
        self._key, self._clock = key, clock
        self._ttl_ms, self._skew_ms, self._max = int(ttl * 1000), int(SKEW_S * 1000), max_live
        self.started_ms = int(clock() * 1000)
        self._used: dict[str, int] = {}                     # nonce -> keep until (ms)
        self._order: collections.deque[tuple[int, str]] = collections.deque()
        self.refused: collections.Counter[str] = collections.Counter()    # fixed reason names only
        self.accepted = 0

    def check(self, token: object, person: str, audience: str) -> bool:
        why = self._why(token, person, audience)
        if why:
            self.refused[why] += 1
            return False
        self.accepted += 1
        return True

    def _prune(self, now_ms: int) -> None:
        while self._order and self._order[0][0] <= now_ms:
            _, nonce = self._order.popleft()
            self._used.pop(nonce, None)

    def _why(self, token: object, person: str, audience: str) -> str:
        if token is None:
            return "missing"
        if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN or token.count(".") != 1:
            return "malformed"
        body, mac = token.split(".")
        try:
            raw_body, raw_mac = body.encode("ascii"), mac.encode("ascii")
        except UnicodeEncodeError:
            return "malformed"
        if not hmac.compare_digest(_mac(self._key, raw_body), raw_mac):
            return "mac"
        try:
            claims = json.loads(base64.urlsafe_b64decode(raw_body + b"=" * (-len(raw_body) % 4)))
        except (ValueError, binascii.Error):
            return "malformed"
        if not isinstance(claims, dict) or set(claims) != {"p", "t", "n", "a"} or not isinstance(claims["t"], int) \
                or isinstance(claims["t"], bool) or not isinstance(claims["n"], str) or len(claims["n"]) != 32:
            return "malformed"
        if claims["a"] != audience:
            return "audience"
        if claims["p"] != person:
            return "person"
        now_ms = int(self._clock() * 1000)
        if claims["t"] < self.started_ms:
            return "before start"
        if claims["t"] > now_ms + self._skew_ms:
            return "future"
        if now_ms - claims["t"] > self._ttl_ms:
            return "expired"
        self._prune(now_ms)
        if claims["n"] in self._used:
            return "replay"
        if len(self._used) >= self._max:
            return "busy"
        keep = now_ms + self._ttl_ms + self._skew_ms      # past this the token is expired whatever its issue time
        self._used[claims["n"]] = keep
        self._order.append((keep, claims["n"]))
        return ""

"""The wire: the ONE closed, versioned request protocol between a skill process and the kernel.

A frame is a 4-byte big-endian length, then that many bytes of UTF-8 JSON (one object).

* On connect the kernel sends ``{"v": 1, "nonce": hex}``. The client answers
  ``{"v": 1, "skill": name, "proof": hex}`` where proof = HMAC-SHA256(credential, nonce).
  The credential itself never crosses the socket, and a proof is good for one connection.
* Then requests ``{"v": 1, "id": n, "op": op, "args": {...}}`` and one response each:
  ``{"v": 1, "id": n, "ok": true, "result": ...}`` or
  ``{"v": 1, "id": n, "ok": false, "error": {"code": code, "reason": text}}``.

There is NO source / reader / skill field in a request: who is asking is the connection's
verified identity and nothing the client writes can change it. ``OPS`` is the whole surface:
every op and every argument it takes. Anything else is ``bad_request``, never a new feature.

The version changes when the meaning of an op or a required argument changes. A NEW op or a
new optional argument is additive: an older kernel refuses it cleanly as unknown.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import struct
import time

from .events import Event

VERSION = 1
MAX_FRAME = 64 * 1024          # a request larger than this is refused before its body is read
MAX_REPLY = 16 * 1024 * 1024   # what a client will accept back (a poll can carry 64 events)
MAX_POLL, MAX_WAIT_MS = 64, 5000

_OPT_STR = (str, type(None))
# op -> {argument: (accepted types, required)}
OPS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {
    "publish": {"kind": ((str,), True), "payload": ((dict,), True), "subject": (_OPT_STR, False),
                "visibility": ((str,), False), "caused_by": ((list,), False),
                "mentions": ((list,), False)},
    "subscribe": {"kinds": ((list,), False), "subject": (_OPT_STR, False),
                  "from_seq": ((int,), False)},
    "poll": {"sub": ((int,), True), "max": ((int,), False), "wait_ms": ((int,), False),
             "ack": ((int,), False)},
    "forget": {"subject": ((str,), True), "actor": ((str,), True), "seqs": ((list,), False),
               "entity": ((str,), False), "topic": ((str,), False)},
    "grant": {"person": ((str,), True), "skill": ((str,), True), "reads": ((dict,), False),
              "acts": ((list,), False), "guardian": (_OPT_STR, False)},
    "revoke": {"person": ((str,), True), "skill": ((str,), True)},
    "act": {"action": ((str,), True), "on_behalf_of": ((str,), True), "args": ((dict,), True),
            "caused_by": ((list,), True)},
    "undo": {"person": ((str,), True), "actor": ((str,), True)},
    "explain": {"seq": ((int,), False), "person": ((str,), False), "last": ((str,), False)},
}

# error codes (closed)
REFUSED, BAD_REQUEST, UNAUTHENTICATED, TOO_LARGE, BAD_VERSION, INTERNAL = (
    "refused", "bad_request", "unauthenticated", "too_large", "unsupported_version", "internal")


class WireError(Exception):
    """A frame that cannot be read: too large, too slow, cut short, or not a JSON object."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def proof(credential: bytes, nonce: bytes) -> str:
    return hmac.new(credential, nonce, hashlib.sha256).hexdigest()


def check_args(op: str, args: object) -> str:
    """'' when ``args`` is exactly what ``op`` takes, else why not. No content in the reason."""
    spec = OPS.get(op)
    if spec is None:
        return "unknown op"
    if not isinstance(args, dict):
        return "args must be an object"
    if set(args) - set(spec):
        return "unknown argument"
    for name, (types, required) in spec.items():
        if name not in args:
            if required:
                return "missing argument"
        elif not isinstance(args[name], types) or isinstance(args[name], bool):
            return "wrong argument type"
    return ""


def _recv_exact(sock: socket.socket, n: int, deadline: float | None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise WireError(BAD_REQUEST, "frame deadline")
            sock.settimeout(left)
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            raise WireError(BAD_REQUEST, "frame deadline") from None
        if not chunk:
            raise WireError(BAD_REQUEST, "connection closed mid-frame")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket, *, max_frame: int = MAX_FRAME, idle: float | None = None,
               frame_deadline: float | None = None) -> dict | None:
    """One frame, or None on a clean close / idle timeout between frames.

    ``frame_deadline`` starts at the frame's FIRST byte: a client may be idle between
    requests, but once it starts a frame it must finish it in time.
    """
    sock.settimeout(idle)
    try:
        first = sock.recv(1)
    except socket.timeout:
        return None
    if not first:
        return None
    deadline = None if frame_deadline is None else time.monotonic() + frame_deadline
    (size,) = struct.unpack(">I", first + _recv_exact(sock, 3, deadline))
    if size > max_frame:
        raise WireError(TOO_LARGE, "frame too large")       # the body is never read
    try:
        obj = json.loads(_recv_exact(sock, size, deadline))
    except (ValueError, RecursionError):
        raise WireError(BAD_REQUEST, "not json") from None
    if not isinstance(obj, dict):
        raise WireError(BAD_REQUEST, "not an object")
    return obj


def write_frame(sock: socket.socket, obj: dict, *, timeout: float | None = None) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    sock.settimeout(timeout)
    sock.sendall(struct.pack(">I", len(body)) + body)


def event_to_wire(ev: Event) -> dict:
    return {"seq": ev.seq, "ts": ev.ts, "kind": ev.kind.value, "source": ev.source,
            "subject": ev.subject, "visibility": ev.visibility.value, "payload": ev.payload,
            "mentions": list(ev.mentions), "caused_by": list(ev.caused_by)}


def event_from_wire(raw: dict) -> Event:
    return Event(raw["kind"], raw["source"], raw["subject"], raw["visibility"], raw["payload"],
                 tuple(raw["mentions"]), tuple(raw["caused_by"]), seq=raw["seq"], ts=raw["ts"])

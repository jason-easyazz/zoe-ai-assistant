"""The kernel behind a local Unix socket: the process boundary.

The kernel lives in ONE process. A skill is another process that holds a socket path and a
per-skill credential the kernel issued; it never holds the database path or a kernel object.
Everything it can ask for is the closed op table in ``wire.py``.

* identity is proved once per connection (challenge / response) and then belongs to the
  connection: no request field names the caller;
* a frame over the size cap is refused before its body is read; a frame that starts and
  does not finish inside ``frame_deadline`` is cut off; both are recorded as refusals;
* one thread per connection, at most ``max_conns``; a connection that dies releases its
  subscriptions; a request that arrived whole is run whole, whether or not the client
  lives to read the answer;
* ``forget`` / ``grant`` / ``revoke`` are a PERSON's decisions. Only a credential issued
  with ``surface=True`` (a panel, the voice adapter) may relay one; a skill asking is refused.

What this does NOT do: stop a process running as the SAME OS user from opening the database
file itself. Only file permissions and a separate user do that (see the README).
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import socket
import threading

from . import wire
from .capability import ConsentError
from .events import Event, Kind
from .kernel import Kernel
from .spine import Refused, Subscription

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
UNVERIFIED = "unverified-peer"
MAX_SUBS = 16      # per connection


class _Conn:
    def __init__(self, sock: socket.socket) -> None:
        self.sock, self.name = sock, UNVERIFIED
        self.subs: dict[int, Subscription] = {}
        self.next_sub = 1


class KernelServer:
    def __init__(self, kernel: Kernel, path: str, *, max_frame: int = wire.MAX_FRAME,
                 frame_deadline: float | None = 2.0, idle_timeout: float | None = 300.0,
                 max_conns: int = 32, socket_mode: int = 0o600) -> None:
        self.kernel, self.path = kernel, path
        self.max_frame, self.frame_deadline, self.idle_timeout = max_frame, frame_deadline, idle_timeout
        self._creds: dict[str, bytes] = {}
        self._surfaces: set[str] = set()
        self._slots = threading.BoundedSemaphore(max_conns)
        self._stopping = threading.Event()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(path):
            os.unlink(path)     # a socket file left by a killed kernel (lab: one kernel per path)
        self._sock.bind(path)
        os.chmod(path, socket_mode)
        self._sock.listen(64)

    # ---- credentials ----------------------------------------------------------------
    def issue(self, name: str, *, surface: bool = False) -> bytes:
        """A fresh credential for one installed skill. Issuing again replaces the old one."""
        if name not in self.kernel.grants.manifests:
            raise ConsentError("no manifest: nothing to issue a credential for")
        self._creds[name] = secrets.token_bytes(32)
        (self._surfaces.add if surface else self._surfaces.discard)(name)
        return self._creds[name]

    def withdraw(self, name: str) -> None:
        self._creds.pop(name, None)

    def _nonce(self) -> bytes:
        return secrets.token_bytes(32)

    def _verify(self, name: str, nonce: bytes, proof: str) -> bool:
        cred = self._creds.get(name)
        return cred is not None and hmac.compare_digest(wire.proof(cred, nonce), proof)

    # ---- serving ----------------------------------------------------------------------
    def serve_forever(self) -> None:
        while not self._stopping.is_set():
            self._slots.acquire()
            try:
                sock, _ = self._sock.accept()
            except OSError:
                self._slots.release()
                break
            threading.Thread(target=self._serve, args=(_Conn(sock),), daemon=True).start()

    def shutdown(self) -> None:
        self._stopping.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def _serve(self, conn: _Conn) -> None:
        try:
            if self._handshake(conn):
                while (req := self._read(conn)) is not None:
                    wire.write_frame(conn.sock, self._answer(conn, req), timeout=self.frame_deadline)
        except (OSError, wire.WireError):
            pass                                    # the peer went away or broke framing
        finally:
            self._release(conn)
            conn.sock.close()
            self._slots.release()

    def _release(self, conn: _Conn) -> None:
        """A dead connection must not leave subscriptions (and their queues) behind."""
        for sub in conn.subs.values():
            sub.close()
        conn.subs.clear()

    def _read(self, conn: _Conn, idle: float | None = None) -> dict | None:
        try:
            return wire.read_frame(conn.sock, max_frame=self.max_frame,
                                   idle=idle or self.idle_timeout,
                                   frame_deadline=self.frame_deadline)
        except wire.WireError as exc:
            if exc.reason != "connection closed mid-frame":     # a death is not an attack
                self._record(conn.name, "send a frame", exc.reason)
                self._error(conn, None, exc.code, exc.reason)
            raise

    def _record(self, who: str, attempted: str, reason: str) -> None:
        """A protocol-level refusal is a REFUSAL event like any other. Fixed strings only."""
        try:
            self.kernel.spine._refuse(who, attempted, reason)
        except Refused:
            pass

    def _error(self, conn: _Conn, rid, code: str, reason: str) -> None:
        try:
            wire.write_frame(conn.sock, _err(rid, code, reason), timeout=self.frame_deadline)
        except OSError:
            pass

    def _handshake(self, conn: _Conn) -> bool:
        nonce = self._nonce()
        wire.write_frame(conn.sock, {"v": wire.VERSION, "nonce": nonce.hex()},
                         timeout=self.frame_deadline)
        hello = self._read(conn, idle=self.frame_deadline)     # no credential, no long wait
        if hello is None:
            return False
        name, given = hello.get("skill"), hello.get("proof")
        claimed = name if isinstance(name, str) and _NAME.match(name) else "?"
        if hello.get("v") != wire.VERSION:
            code, reason = wire.BAD_VERSION, "unsupported protocol version"
        elif claimed == "?" or not isinstance(given, str) or not self._verify(claimed, nonce, given):
            code, reason = wire.UNAUTHENTICATED, "bad credential"
        else:
            conn.name = claimed
            wire.write_frame(conn.sock, {"v": wire.VERSION, "ok": True}, timeout=self.frame_deadline)
            return True
        self._record(UNVERIFIED, f"connect as {claimed}", reason)
        self._error(conn, None, code, reason)
        return False

    # ---- requests ---------------------------------------------------------------------
    def _validate(self, req: dict) -> str:
        if req.get("v") != wire.VERSION:
            return "unsupported protocol version"
        if set(req) - {"v", "id", "op", "args"} or not isinstance(req.get("op"), str):
            return "unknown field"
        return wire.check_args(req["op"], req.get("args", {}))

    def _answer(self, conn: _Conn, req: dict) -> dict:
        rid = req.get("id") if isinstance(req.get("id"), int) else None
        op = req["op"] if isinstance(req.get("op"), str) and req["op"] in wire.OPS else "?"
        try:
            bad = self._validate(req)
            if bad:
                self._record(conn.name, f"request {op}", bad)
                code = wire.BAD_VERSION if bad.startswith("unsupported") else wire.BAD_REQUEST
                return _err(rid, code, bad)
            args = req.get("args", {})
            if op == "poll":                        # may wait: never under the kernel lock
                result = self._op_poll(conn, args)
            else:
                with self.kernel.spine._lock:
                    result = getattr(self, "_op_" + op)(conn, args)
            return {"v": wire.VERSION, "id": rid, "ok": True, "result": result}
        except Refused as exc:                      # already a REFUSAL event on the spine
            return _err(rid, wire.REFUSED, str(exc))
        except ConsentError as exc:
            return _err(rid, wire.REFUSED, str(exc))
        except (ValueError, TypeError, KeyError):   # a bad kind, level, cause or payload shape
            self._record(conn.name, f"request {op}", "malformed request")
            return _err(rid, wire.BAD_REQUEST, "malformed request")
        except Exception as exc:                    # never take the kernel down with a request
            return _err(rid, wire.INTERNAL, type(exc).__name__)

    def _op_publish(self, conn: _Conn, a: dict) -> dict:
        mentions = a.get("mentions", ())
        if not all(isinstance(m, str) for m in mentions):
            raise ValueError("mentions are names")
        ev = Event(a["kind"], conn.name, a.get("subject"), a.get("visibility", "private"),
                   a["payload"], mentions, a.get("caused_by", ()))
        return wire.event_to_wire(self.kernel.spine.publish(ev))

    def _op_subscribe(self, conn: _Conn, a: dict) -> dict:
        if len(conn.subs) >= MAX_SUBS:
            self.kernel.spine._refuse(conn.name, "subscribe", "too many subscriptions")
        sub = self.kernel.spine.subscribe(conn.name, kinds=a.get("kinds") or None,
                                          subject=a.get("subject"), from_seq=a.get("from_seq"))
        conn.subs[conn.next_sub] = sub
        conn.next_sub += 1
        return {"sub": conn.next_sub - 1}

    def _op_poll(self, conn: _Conn, a: dict) -> dict:
        sub = conn.subs.get(a["sub"])
        if sub is None:
            raise ValueError("no such subscription on this connection")
        if "ack" in a:
            sub.ack(a["ack"])
        wait = min(max(a.get("wait_ms", 0), 0), wire.MAX_WAIT_MS) / 1000
        out: list[dict] = []
        while len(out) < min(max(a.get("max", 1), 1), wire.MAX_POLL):
            ev = sub.poll(wait if not out else 0.0)
            if ev is None:
                break
            out.append(wire.event_to_wire(ev))
        return {"events": out}

    def _person_op(self, conn: _Conn, what: str) -> None:
        if conn.name not in self._surfaces:
            self.kernel.spine._refuse(conn.name, what, "only a person, through a trusted surface")

    def _op_forget(self, conn: _Conn, a: dict) -> dict:
        self._person_op(conn, "forget")
        r = self.kernel.spine.forget(a["subject"], actor=a["actor"], seqs=a.get("seqs"))
        return {"count": r.count, "upto": r.upto}

    def _op_grant(self, conn: _Conn, a: dict) -> dict:
        self._person_op(conn, "grant")
        g = self.kernel.grants.grant(a["person"], a["skill"], reads=a.get("reads"),
                                     acts=a.get("acts"), guardian=a.get("guardian"))
        return {"grant_id": g.id}

    def _op_revoke(self, conn: _Conn, a: dict) -> dict:
        self._person_op(conn, "revoke")
        return {"revoked": self.kernel.grants.revoke(a["person"], a["skill"])}

    def _op_act(self, conn: _Conn, a: dict) -> dict:
        r = self.kernel.actions.act(conn.name, a["action"], a["on_behalf_of"], a["args"],
                                    caused_by=tuple(a["caused_by"]))
        return {"id": r.id, "seq": r.seq, "action": r.action, "skill": r.skill,
                "on_behalf_of": r.on_behalf_of, "grant_id": r.grant_id, "proof": r.proof,
                "verified": r.verified}

    def _op_explain(self, conn: _Conn, a: dict) -> dict:
        """With ``seq``: that event and its causes, as far as the caller may read them.
        Without: the caller's own standing (manifest, who granted it, its recent refusals)."""
        spine, store = self.kernel.spine, self.kernel.grants
        if "seq" in a:
            ev = spine._get(a["seq"])
            if ev is None or not spine._deliverable(conn.name, ev):
                spine._refuse(conn.name, "explain", "not an event it may read")
            causes = [spine._get(c) for c in ev.caused_by]
            seen = [wire.event_to_wire(c) for c in causes if c and spine._deliverable(conn.name, c)]
            return {"event": wire.event_to_wire(ev), "causes": seen,
                    "hidden_causes": len(causes) - len(seen)}
        m = store.manifests[conn.name]
        mine = [e for e in spine._fetch(0, frozenset({Kind.REFUSAL}), None, 100_000)
                if e.payload.get("who") == conn.name][-20:]
        return {"skill": conn.name, "protocol": wire.VERSION,
                "manifest": {"reads": {k.value: v.value for k, v in m.reads.items()},
                             "emits": sorted(k.value for k in m.emits), "acts": sorted(m.acts)},
                "grants": [{"person": g.person, "acts": sorted(g.acts),
                            "reads": {k.value: v.value for k, v in g.reads.items()}}
                           for g in store.all_for(conn.name)],
                "refusals": [{"seq": e.seq, "attempted": e.payload["attempted"],
                              "reason": e.payload["reason"]} for e in mine]}


def _err(rid, code: str, reason: str) -> dict:
    return {"v": wire.VERSION, "id": rid, "ok": False, "error": {"code": code, "reason": reason}}

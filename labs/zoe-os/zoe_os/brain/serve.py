"""A localhost HTTP face for ONE BrainBridge, so a Node tool can forward a call to it.

The lab Flue app (labs/zoe-os/brain-lab-app) is a Node process; the bridge is Python. This is the
seam between them and it adds no authority: the turn (member, utterance) is set by the TRUSTED
runner through ``POST /turn`` and the model's tool call only ever reaches ``POST /propose`` with a
tool name and arguments. There is no way to name a member in a ``/propose`` call.

    GET  /tools     the derived tool schemas (what the app registers)
    POST /turn      {"member": ..., "cause": n} -> {"ok", "turn": id}   (runner only)
    POST /propose   {"tool": name, "arguments": {...}} + X-Turn: id -> {"status", "say", "receipt"}
    GET  /log       turn ids, proposal ids, status codes and timing only

Every proposal is BOUND to the turn it came from. ``POST /turn`` mints a fresh turn id (never
reused) and the runner uses it as the Flue agent instance id (the ``/agents/lab/<id>`` route
segment); the app's tools close over that instance id and send it in ``X-Turn``, outside the
model's arguments, so the model cannot choose or alter it. A proposal whose id is not the
CURRENT turn's (a Flue operation left running after its turn timed out, still submitting tool
calls) is refused with the fixed code ``stale_turn``, logged like every other proposal, and
never reaches the bridge or a driver: it cannot act under the next turn's person and cause.

Bound to 127.0.0.1 on a port the runner chose. It holds the bridge (a socket path and a credential)
and nothing else. Every route requires the runner's ``X-Runner`` token; the trusted Node
forwarder receives a derived token for tools/propose only through its environment, never
through a model argument or schema. It cannot set turns or read the runner's log.
"""
from __future__ import annotations

import json
import hmac
import itertools
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bridge import BrainBridge, Outcome, Turn
from ..diagnostics import proposal_record

STALE = Outcome("refused", "That request belonged to an earlier turn. Nothing changed.", reason="stale_turn")


class BridgeServer:
    def __init__(self, bridge: BrainBridge, runner_token: str, port: int = 0) -> None:
        self.bridge, self.token = bridge, runner_token
        self.turn: Turn | None = None
        self.turn_id: str | None = None
        self._turns = itertools.count(1)
        self._issued: dict[str, Turn] = {}      # every id ever minted: a late call is attributed to ITS turn
        self.app_token = hmac.new(runner_token.encode(), b'proposal-forwarder', 'sha256').hexdigest()
        self.log: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):          # no request lines on stderr
                pass

            def _send(self, code: int, obj) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                n = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
                try:
                    obj = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return {}
                return obj if isinstance(obj, dict) else {}

            def do_GET(self):
                if not self._authorized():
                    return self._send(403, {"error": "runner only"})
                if self.path == "/tools":
                    self._send(200, outer.bridge.tools())
                elif self.path == "/log":
                    self._send(200, outer.log)
                else:
                    self._send(404, {"error": "no such route"})

            def do_POST(self):
                if not self._authorized():
                    return self._send(403, {"error": "runner only"})
                body = self._body()
                if self.path == "/turn":
                    return self._send(200, {"ok": True, "turn": outer.open_turn(str(body["member"]), int(body["cause"]))})
                if self.path != "/propose":
                    return self._send(404, {"error": "no such route"})
                supplied = self.headers.get("X-Turn", "")
                with outer._lock:
                    # The turn is the one the proposal CARRIES, checked against the current one under
                    # the same lock as the call: never "whatever turn is open when the call arrives".
                    bound = outer._bound(supplied)
                    if bound is None:
                        if outer.turn_id is None and not supplied:
                            return self._send(409, {"status": "invalid", "say": "No turn is open. Nothing changed."})
                        # Never sent to the bridge: no driver runs. Attributed to its own turn only when
                        # the id is one this server minted; caller text never reaches the log.
                        own = outer._issued.get(supplied)
                        out, ms, turn, turn_id = STALE, 0.0, own, supplied if own else None
                    else:
                        turn, turn_id = bound
                        t0 = time.perf_counter_ns()
                        out = outer.bridge.propose(turn, str(body.get("tool")), body.get("arguments"))
                        ms = (time.perf_counter_ns() - t0) / 1e6
                    outer.log.append(proposal_record(turn.cause if turn else None, body.get("tool"), out, ms,
                                                     turn_id=turn_id))
                self._send(200, {"status": out.status, "say": out.say, "receipt": out.receipt})

            def _authorized(self):
                supplied = self.headers.get("X-Runner", "")
                runner = hmac.compare_digest(supplied.encode(), outer.token.encode())
                app = hmac.compare_digest(supplied.encode(), outer.app_token.encode())
                return bool(outer.token) and (runner or (app and self.path in {'/tools', '/propose'}))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        # The stdlib default prints exception details from untrusted requests to stderr.
        self.httpd.handle_error = lambda *args: None
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def open_turn(self, member: str, cause: int) -> str:
        """Open a new turn and return its id. Ids are never reused (a counter plus random bytes), so
        a late call from ANY earlier turn, or one made up, is not the current turn."""
        with self._lock:
            self.turn = Turn(member, cause)
            self.turn_id = f"turn-{next(self._turns)}-{secrets.token_hex(8)}"
            self._issued[self.turn_id] = self.turn
            return self.turn_id

    def _bound(self, supplied: str) -> tuple[Turn, str] | None:
        """The turn a proposal carries, if and only if it is the CURRENT turn. Caller holds the lock."""
        if self.turn is None or self.turn_id is None or not isinstance(supplied, str):
            return None
        if not hmac.compare_digest(supplied.encode(), self.turn_id.encode()):
            return None
        return self.turn, self.turn_id

    def start(self) -> "BridgeServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

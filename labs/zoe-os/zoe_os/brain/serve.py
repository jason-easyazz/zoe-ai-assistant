"""A localhost HTTP face for ONE BrainBridge, so a Node tool can forward a call to it.

The lab Flue app (labs/zoe-os/brain-lab-app) is a Node process; the bridge is Python. This is the
seam between them and it adds no authority: the turn (member, utterance) is set by the TRUSTED
runner through ``POST /turn`` and the model's tool call only ever reaches ``POST /propose`` with a
tool name and arguments. There is no way to name a member in a ``/propose`` call.

    GET  /tools     the derived tool schemas (what the app registers)
    POST /turn      {"member": ..., "cause": n}   (runner only: the model has no route to this)
    POST /propose   {"tool": name, "arguments": {...}} -> {"status", "say", "receipt"}
    GET  /log       proposal ids, status codes and timing only

Bound to 127.0.0.1 on a port the runner chose. It holds the bridge (a socket path and a credential)
and nothing else. Every route requires the runner's ``X-Runner`` token; the trusted Node
forwarder receives a derived token for tools/propose only through its environment, never
through a model argument or schema. It cannot set turns or read the runner's log.
"""
from __future__ import annotations

import json
import hmac
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bridge import BrainBridge, Turn
from ..diagnostics import proposal_record


class BridgeServer:
    def __init__(self, bridge: BrainBridge, runner_token: str, port: int = 0) -> None:
        self.bridge, self.token, self.turn = bridge, runner_token, None
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
                    outer.turn = Turn(str(body["member"]), int(body["cause"]))
                    return self._send(200, {"ok": True})
                if self.path != "/propose":
                    return self._send(404, {"error": "no such route"})
                with outer._lock:
                    if outer.turn is None:
                        return self._send(409, {"status": "invalid", "say": "No turn is open. Nothing changed."})
                    t0 = time.perf_counter_ns()
                    out = outer.bridge.propose(outer.turn, str(body.get("tool")), body.get("arguments"))
                    ms = (time.perf_counter_ns() - t0) / 1e6
                    outer.log.append(proposal_record(outer.turn.cause, body.get("tool"), out, ms))
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

    def start(self) -> "BridgeServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

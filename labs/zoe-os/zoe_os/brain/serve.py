"""A localhost HTTP face for ONE BrainBridge, so a Node tool can forward a call to it.

The lab Flue app (labs/zoe-os/brain-lab-app) is a Node process; the bridge is Python. This is the
seam between them and it adds no authority: the turn (member, utterance) is set by the TRUSTED
runner through ``POST /turn`` and the model's tool call only ever reaches ``POST /propose`` with a
tool name and arguments. There is no way to name a member in a ``/propose`` call.

    GET  /tools     the derived tool schemas (what the app registers)
    POST /turn      {"member": ..., "cause": n}   (runner only: the model has no route to this)
    POST /propose   {"tool": name, "arguments": {...}} -> {"status", "say", "receipt"}
    GET  /log       every proposal so far: tool, arguments, status, ms (the recorded tool-call frames)

Bound to 127.0.0.1 on a port the runner chose. It holds the bridge (a socket path and a credential)
and nothing else. ``/turn`` is refused from any connection that is not the runner's own: the runner
passes a one-time ``X-Runner`` token that is never given to the Node app.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bridge import BrainBridge, Turn


class BridgeServer:
    def __init__(self, bridge: BrainBridge, runner_token: str, port: int = 0) -> None:
        self.bridge, self.token, self.turn = bridge, runner_token, None
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
                if self.path == "/tools":
                    self._send(200, outer.bridge.tools())
                elif self.path == "/log":
                    self._send(200, outer.log)
                else:
                    self._send(404, {"error": "no such route"})

            def do_POST(self):
                body = self._body()
                if self.path == "/turn":
                    if self.headers.get("X-Runner") != outer.token:
                        return self._send(403, {"error": "runner only"})
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
                    outer.log.append({"turn": outer.turn.cause, "tool": body.get("tool"),
                                      "arguments": body.get("arguments"), "status": out.status,
                                      "receipt": out.receipt, "say": out.say, "reason": out.reason, "shape": out.shape, "hop_ms": round(ms, 3)})
                self._send(200, {"status": out.status, "say": out.say, "receipt": out.receipt})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "BridgeServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

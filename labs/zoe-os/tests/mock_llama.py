"""A mock llama-server for the dry run and the tests: the OpenAI chat-completions stream, /slots, /metrics.

It is the "scripted agent in the model's place": a user message becomes the tool call a real model
would make for a few phrasings (none, for anything else); a tool result becomes its reply. It never
touches the real brain and keeps no text.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORDS = {"six": 6, "seven": 7, "ten": 10}


def call_for(text: str):
    m = re.match(r"add (.+?) to the (\w+) list$", text.strip(), re.I)
    if m:
        return "list_add", {"item": m.group(1)}
    m = re.match(r"remind me in (\w+) minutes? to (.+)$", text.strip(), re.I)
    if m:
        n = WORDS.get(m.group(1).lower()) or int(m.group(1))
        return "remind_set", {"note": m.group(2), "in_minutes": n}
    m = re.match(r"set an alarm for (\w+)$", text.strip(), re.I)
    if m:
        h = WORDS.get(m.group(1).lower()) or int(m.group(1))
        return "alarm_set", {"time": f"{h:02d}:00"}
    return None


class MockLlama:
    def __init__(self) -> None:
        self.requests = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/slots":
                    return self._json([{"id": 0, "is_processing": False, "n_prompt_tokens": 300}])
                if self.path == "/metrics":
                    body = (f"llamacpp:prompt_tokens_total {100 * outer.requests}\n"
                            f"llamacpp:prompt_tokens_cached_total {10 * outer.requests}\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                self._json({"error": "no"}, 404)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.requests += 1
                msgs = req.get("messages", [])
                last = msgs[-1] if msgs else {}
                if last.get("role") == "tool":
                    content = last.get("content")
                    if isinstance(content, list):
                        content = "".join(p.get("text", "") for p in content)
                    delta, finish = {"role": "assistant", "content": str(content)}, "stop"
                else:
                    user = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
                    text = user.get("content")
                    if isinstance(text, list):
                        text = "".join(p.get("text", "") for p in text)
                    call = call_for(str(text))
                    if call:
                        delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{outer.requests}",
                                 "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}
                        finish = "tool_calls"
                    else:
                        delta, finish = {"role": "assistant", "content": "A lovely evening indeed."}, "stop"
                chunk = lambda d, f: {"id": "mock", "object": "chat.completion.chunk", "created": 0,
                                      "model": "local", "choices": [{"index": 0, "delta": d, "finish_reason": f}]}
                if not req.get("stream"):
                    return self._json({"id": "mock", "object": "chat.completion", "created": 0, "model": "local",
                                       "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                                       "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for frame in (chunk(delta, None), chunk({}, finish)):
                    self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                usage = {"id": "mock", "object": "chat.completion.chunk", "created": 0, "model": "local", "choices": [],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\ndata: [DONE]\n\n")
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "MockLlama":
        self._t.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

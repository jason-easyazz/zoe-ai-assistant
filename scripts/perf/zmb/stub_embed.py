#!/usr/bin/env python3
"""A ~10 MB loopback ``/v1/embeddings`` STUB: deterministic hashed bag-of-words vectors, no model, no numpy, stdlib only.

Why it exists: the RAM lab measures the Hindsight SERVER's footprint. The server's RSS does not depend on which process produced the vectors, and the real shim
(bge-small, ~130 MB) is measured on its own, so a footprint run that needs both at once can swap the shim for this stub and ADD the shim's separately measured PSS
(PSS is additive across processes: the window's own G0 figure is that sum). It is NOT an embedder: recall quality and the embedding share of a recall's latency
are only measured with the real shim. Same protocol as ``embed_shim.py`` (float / base64, at most 64 inputs, 384 dimensions), loopback only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 384
_TOK = re.compile(r"[a-z0-9']+")


def embed(text: str) -> "list[float]":
    v = [0.0] * DIM
    for t in _TOK.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(t.encode(), digest_size=8).digest(), "big")
        v[h % DIM] += 1.0 if (h >> 40) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # noqa: ANN002
        return

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "ok", "model": "stub-hash-384", "dim": DIM})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            inp = body.get("input")
            texts = [inp] if isinstance(inp, str) else list(inp)
            if not texts or len(texts) > 64 or not all(isinstance(t, str) and t.strip() for t in texts):
                raise ValueError("bad input")
        except (ValueError, TypeError):
            self._send(400, {"error": {"message": "bad input", "type": "invalid_request_error", "code": 400}})
            return
        fmt = body.get("encoding_format") or "float"
        data = []
        for i, t in enumerate(texts):
            v = embed(t)
            data.append({"object": "embedding", "index": i,
                         "embedding": base64.b64encode(struct.pack(f"<{DIM}f", *v)).decode() if fmt == "base64" else v})
        self._send(200, {"object": "list", "model": "stub-hash-384", "data": data, "usage": {"prompt_tokens": 1, "total_tokens": 1}})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serve", action="store_true", required=True)
    ap.add_argument("--port", type=int, default=11511)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

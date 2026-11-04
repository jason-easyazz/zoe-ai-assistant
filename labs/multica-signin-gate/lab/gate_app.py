"""The lab's zoe-data stand-in: the REAL ``routers/multica_ui.py`` gate (and, through it, the real ``get_current_user``, the
real ``multica_ui_gate`` cookie code and the real adults-only check ``manner_block.eligible`` reading Postgres through the real
``db_pool``) on :8000, plus a zoe-auth stand-in on 127.0.0.1:8002 that answers ``GET /api/auth/user`` for a fixed set of
synthetic sessions in zoe-auth's ``UserResponse`` shape.

Runs inside the lab's python container with the worktree's ``services/zoe-data`` mounted read-only at /zoe-data and the
zoe-data py312 venv's site-packages at /venv-site. Never imported by anything outside this lab.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading

sys.path.insert(0, "/zoe-data")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db_pool  # noqa: E402
import routers.multica_ui as multica_ui  # noqa: E402

# session id -> (user_id, role). zoe-auth returns the guest profile for a guest session and 401 for an unknown one.
SESSIONS = json.loads(os.environ["LAB_SESSIONS"])


class _AuthStub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        hit = SESSIONS.get(self.headers.get("X-Session-ID", "")) if self.path == "/api/auth/user" else None
        if not hit:
            self.send_response(401 if self.path == "/api/auth/user" else 404)
            self.end_headers()
            return
        body = json.dumps({"user_id": hit[0], "username": hit[0], "email": "", "role": hit[1], "is_active": True,
                           "is_verified": True, "created_at": "2026-10-19T00:00:00", "permissions": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    stub = http.server.ThreadingHTTPServer(("127.0.0.1", 8002), _AuthStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    app = FastAPI()
    app.include_router(multica_ui.router)

    @app.on_event("startup")
    async def _pool() -> None:
        await db_pool.init_pool()

    @app.get("/lab/ready")
    async def ready() -> dict:
        return {"ok": True}

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()

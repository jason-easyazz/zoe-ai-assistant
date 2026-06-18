"""Pi-brain latency benchmark (one half of the cutover gate).

Times representative turns through the complete zoe-core brain (provider + soul
+ memory + abilities) and reports per-task-type latency. The `zoe_agent`
head-to-head is the other half — it runs against the live zoe-data service and
is owner-coordinated (it exercises the production chat path), so it's a
documented TODO here, not run automatically.

    python services/zoe-core/bench/pi_brain_latency.py            # human-readable
    python services/zoe-core/bench/pi_brain_latency.py --json     # machine-readable
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent
_EXT = _CORE / "extensions"
_SOUL = _CORE / "SOUL.md"
_EXTS = ["provider-local-gemma.ts", "soul.ts", "memory.ts", "abilities.ts"]
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E2B-it-Q4_K_M.gguf")
_BASE_URL = os.environ.get("ZOE_CORE_MODEL_URL") or os.environ.get("GEMMA_SERVER_URL") or "http://127.0.0.1:11434/v1"
_REPEATS = int(os.environ.get("ZOE_BENCH_REPEATS", "3"))

# task type -> prompt
_TASKS = {
    "identity": "Who are you? One short sentence.",
    "info_local": "What is today's date?",
    "memory_recall": "What's my dog's name?",
    "tool_action": "Add bread to my shopping list.",
    "chat": "Say hi in one short sentence.",
}
_MEMORY_PACKET = {"packet": "## What I know about you\n- Jason's dog is named Pixel [mem:t1]", "count": 1, "user_scoped": True}


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def _stub():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a): return
        def _j(self, payload):
            b = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self): self._j(_MEMORY_PACKET)
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0); self.rfile.read(n)
            self._j({"ok": True, "result": "done."})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run_once(prompt: str, url: str) -> float:
    env = {**os.environ, "ZOE_DATA_URL": url, "ZOE_CORE_USER_ID": "family-admin",
           "ZOE_INTERNAL_TOKEN": "test", "ZOE_CORE_SOUL_PATH": str(_SOUL), "ZOE_CORE_ALLOW_WRITES": "true"}
    cmd = ["pi", "-p", "--provider", "local-gemma", "--model", _MODEL]
    for e in _EXTS:
        cmd += ["-e", str(_EXT / e)]
    cmd += ["--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
            "--no-context-files", "--no-session", "--thinking", "off", prompt]
    t0 = time.monotonic()
    subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    return time.monotonic() - t0


def main() -> int:
    as_json = "--json" in sys.argv
    if shutil.which("pi") is None or not _server_up() or not _SOUL.exists():
        print("SKIP: pi / model server / SOUL.md unavailable", file=sys.stderr)
        return 0
    srv = _stub()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    results = {}
    try:
        for name, prompt in _TASKS.items():
            samples = [_run_once(prompt, url) for _ in range(_REPEATS)]
            results[name] = {"p50_s": round(statistics.median(samples), 2),
                             "min_s": round(min(samples), 2), "max_s": round(max(samples), 2),
                             "n": len(samples)}
    finally:
        srv.shutdown()
    if as_json:
        print(json.dumps({"model": _MODEL, "tasks": results}, indent=2))
    else:
        print(f"Pi-brain latency ({_MODEL}, {_REPEATS} runs/task):")
        for name, r in results.items():
            print(f"  {name:14s} p50={r['p50_s']:5.2f}s  ({r['min_s']:.2f}–{r['max_s']:.2f}s)")
        print("\nTODO (owner-coordinated): zoe_agent head-to-head via the live zoe-data chat path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

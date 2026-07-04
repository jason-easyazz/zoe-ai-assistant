"""Samantha acceptance suite — the bar Zoe's brain must clear.

Proves a single non-interactive Pi run, wired with the four zoe-core extensions
(provider + soul + memory + abilities), behaves like Zoe: she knows who she is,
recalls what memory tells her, actually does things, consults the per-turn
memory packet, and answers within a sane latency budget.

All tests are @pytest.mark.integration and skip cleanly when pi / the model
server / the extensions aren't available. zoe-data is never contacted for real:
one shared stdlib stub serves GET /api/memories/for-prompt and records POST
/api/system/intent-dispatch.

    python -m pytest services/zoe-core/test/test_samantha_acceptance.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parent.parent
_EXT = _CORE / "extensions"
_SOUL = _CORE / "SOUL.md"
_EXTENSIONS = [
    _EXT / "provider-local-gemma.ts",
    _EXT / "soul.ts",
    _EXT / "memory.ts",
    _EXT / "abilities.ts",
]
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")
_BASE_URL = (
    os.environ.get("ZOE_CORE_MODEL_URL")
    or os.environ.get("GEMMA_SERVER_URL")
    or "http://127.0.0.1:11434/v1"
)
_LATENCY_CEILING_S = 60


def _skip_reason() -> str | None:
    if shutil.which("pi") is None:
        return "pi not on PATH"
    missing = [str(p) for p in (*_EXTENSIONS, _SOUL) if not p.exists()]
    if missing:
        return f"not present yet: {', '.join(missing)}"
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as r:
            if r.status != 200:
                return f"model server {r.status}"
    except Exception:
        return f"model server unreachable at {_BASE_URL}"
    return None


# Evaluate once: a second call would re-walk the filesystem and re-probe the
# model server (a 4s HTTP call), and could observe a different state than the
# skipif condition. Cache the single observation for both condition and reason.
_SKIP_REASON = _skip_reason()
requires_env = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


class _Stub:
    """zoe-data stand-in: serves the memory packet, records dispatches."""

    def __init__(self) -> None:
        self.packet: dict[str, Any] = {"packet": "", "refs": [], "count": 0, "user_scoped": True}
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        state = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def _json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                with state._lock:
                    state.requests.append({"m": "GET", "path": self.path})
                if self.path.startswith("/api/memories/for-prompt"):
                    self._json(200, state.packet)
                else:
                    self._json(200, {"ok": True})

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    body = {}
                with state._lock:
                    state.requests.append({"m": "POST", "path": self.path, "body": body})
                if self.path.startswith("/api/system/intent-dispatch"):
                    intent = body.get("intent", "unknown")
                    item = (body.get("slots") or {}).get("item", "it")
                    self._json(200, {"intent": intent, "ok": True, "result": f"Added {item}."})
                else:
                    self._json(200, {"ok": True})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> "_Stub":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def dispatches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r["body"] for r in self.requests if r["m"] == "POST" and "intent-dispatch" in r["path"]]

    def memory_hits(self) -> int:
        with self._lock:
            return sum(1 for r in self.requests if r["m"] == "GET" and "for-prompt" in r["path"])


@pytest.fixture
def stub():
    s = _Stub().start()
    try:
        yield s
    finally:
        s.stop()


def _run(prompt: str, url: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "ZOE_DATA_URL": url,
        "ZOE_CORE_USER_ID": "family-admin",
        "ZOE_INTERNAL_TOKEN": "test",
        "ZOE_CORE_SOUL_PATH": str(_SOUL),
        "ZOE_CORE_ALLOW_WRITES": "true",
    }
    cmd = ["pi", "-p", "--provider", "local-gemma", "--model", _MODEL]
    for e in _EXTENSIONS:
        cmd += ["-e", str(e)]
    cmd += ["--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
            "--no-context-files", "--no-session", "--thinking", "off", prompt]
    t0 = time.monotonic()
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    return {"out": (p.stdout or "").strip(), "code": p.returncode, "elapsed": time.monotonic() - t0, "err": p.stderr}


def _ok(r: dict[str, Any]) -> str:
    assert r["code"] == 0, f"pi exited {r['code']}: {r['err']}"
    assert r["out"], "empty response"
    return r["out"]


def _asks_identity(stub, attempts: int = 3) -> str:
    """Return the first response that adopts the Zoe persona, else the last one.

    A 2B model is not deterministic about persona: with the SOUL system prompt
    wired in it usually answers as Zoe, but occasionally blurts its base identity
    ("I'm Gemma 4..."). The brick is correct if the persona is adopted within a
    few attempts, so we sample up to `attempts` times rather than gambling on a
    single generation (which would make this an intermittently red test).
    """
    last = ""
    for _ in range(attempts):
        last = _ok(_run("Who are you? One short sentence.", stub.url)).lower()
        if "zoe" in last and "coding assistant" not in last:
            return last
    return last


@pytest.mark.integration
@requires_env
def test_identity(stub):
    text = _asks_identity(stub)
    assert "zoe" in text, f"never identified as Zoe in 3 tries; last: {text!r}"
    assert "coding assistant" not in text, text


@pytest.mark.integration
@requires_env
def test_memory_recall(stub):
    stub.packet = {"packet": "## What I know about you\n- Jason's dog is named Pixel [mem:t1]", "count": 1, "user_scoped": True}
    text = _ok(_run("What's my dog's name?", stub.url)).lower()
    assert stub.memory_hits() >= 1, "memory packet was not fetched"
    assert "pixel" in text, text


@pytest.mark.integration
@requires_env
def test_tool_action(stub):
    _ok(_run("Add bread to my shopping list.", stub.url))
    intents = [d.get("intent") for d in stub.dispatches()]
    assert "list_add" in intents, f"no list_add dispatch; got {intents}"
    add = next(d for d in stub.dispatches() if d.get("intent") == "list_add")
    assert "bread" in json.dumps(add.get("slots", {})).lower(), add


@pytest.mark.integration
@requires_env
def test_continuity_consults_packet(stub):
    stub.packet = {"packet": "## What I know about you\n- Jason prefers concise answers [mem:p1]", "count": 1, "user_scoped": True}
    _ok(_run("What's a good fruit to snack on?", stub.url))
    # Robust acceptance bar for a 2B model: the preference packet was consulted.
    assert stub.memory_hits() >= 1, "preference packet was not consulted"


def _sample(stub, prompt: str, attempts: int = 3) -> list[str]:
    """Run the same prompt a few times and return every response (lowercased).

    A 2B model is nondeterministic; a Samantha behaviour is satisfied if it shows
    up within a few samples, so callers assert `any(needle in t for t in texts)`
    rather than gambling on one generation (which would flake)."""
    return [_ok(_run(prompt, stub.url)).lower() for _ in range(attempts)]


@pytest.mark.integration
@requires_env
def test_emotional_thread_recall(stub):
    # Criterion #2 — recalls the emotional thread. The packet carries a stored
    # emotional moment; an emotional query must both consult memory and surface it.
    stub.packet = {
        "packet": "## What I know about you\n"
                  "- Jason has been anxious about his house settlement dragging on [mem:e1]",
        "count": 1, "user_scoped": True,
    }
    texts = _sample(stub, "What have I been anxious about lately?")
    assert stub.memory_hits() >= 1, "emotional-memory packet was not fetched"
    assert any("settlement" in t for t in texts), f"never recalled the emotional thread: {texts!r}"


@pytest.mark.integration
@requires_env
def test_proactive_surfacing(stub):
    # Criterion #3 — surfaces relevant memory UNPROMPTED. A neutral greeting, with
    # a timely fact in the packet: a Samantha volunteers it rather than waiting to
    # be asked.
    stub.packet = {
        "packet": "## What I know about you\n"
                  "- Jason's mum Janice has her birthday tomorrow [mem:d1]",
        "count": 1, "user_scoped": True,
    }
    texts = _sample(stub, "Good morning!", attempts=4)
    assert stub.memory_hits() >= 1, "packet was not consulted on a neutral turn"
    assert any("birthday" in t or "janice" in t for t in texts), \
        f"never proactively surfaced the timely memory: {texts!r}"


@pytest.mark.integration
@requires_env
def test_understanding_evolves(stub):
    # Criterion #4 — its understanding of the user evolves (the synthesized
    # portrait). The packet carries a portrait fact; the brain must apply it to a
    # request that never names it.
    stub.packet = {
        "packet": "## What I know about you\n"
                  "- Jason is learning to play the guitar [portrait]",
        "count": 1, "user_scoped": True,
    }
    texts = _sample(stub, "Suggest something fun for me to practise tonight.")
    assert stub.memory_hits() >= 1, "portrait packet was not consulted"
    assert any("guitar" in t for t in texts), \
        f"never applied its evolving understanding: {texts!r}"


@pytest.mark.integration
@pytest.mark.performance
@requires_env
def test_latency_budget(stub):
    r = _run("Say hi in one short sentence.", stub.url)
    _ok(r)
    print(f"[samantha-latency] simple turn: {r['elapsed']:.2f}s (ceiling {_LATENCY_CEILING_S}s)")
    assert r["elapsed"] < _LATENCY_CEILING_S, f"{r['elapsed']:.1f}s over {_LATENCY_CEILING_S}s"

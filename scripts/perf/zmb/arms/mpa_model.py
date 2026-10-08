"""The brain side of the MPA arm: a chat model with TOOLS behind one ``complete(messages, tools)``.

* ``HttpChatModel``      - the clone brain (or, for the smoke, the live one) over llama-server's OpenAI-compatible ``/v1/chat/completions`` with a ``tools``
  array: the SAME chat template path the Flue sidecar's capped-completions provider uses, so the 4B sees MemPalace's schemas exactly as it would in
  production. LOOPBACK ONLY (refused otherwise), a hard call budget (``BudgetExhausted``: the smoke's "at most 30 model calls" is enforced here, not hoped
  for), every call timed and counted. ``/tokenize`` is used for exact prompt-part sizes (no generation; reported separately from model calls).
* ``ScriptedChatModel``  - a deterministic RULE-BASED stand-in for a brain, for the slim lane and the lab. It proves the arm's plumbing (loop, floors, hooks,
  accounting), never what a 4B model does. It has two modes: ``diligent`` (searches before it answers, files what it is told, supersedes on a change) and
  ``lazy`` (never calls a tool: the failure the community reports, "the model never called the tools until CLAUDE.md told it to").
* ``ScriptedLLMServer``  - a loopback OpenAI-compatible server that answers MemPalace's closet prompt with extractive JSON, so the REAL ``closet_llm`` pass
  runs end to end in the lab without a brain.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Protocol


class BudgetExhausted(RuntimeError):
    """The model-call budget is spent (the smoke's cap). The caller stops; nothing is retried."""


@dataclass
class Reply:
    content: str = ""
    tool_calls: "list[dict[str, Any]]" = field(default_factory=list)      # [{"id", "name", "arguments": dict | None, "raw": str, "args_error": str}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    finish_reason: str = ""


class ChatModel(Protocol):
    name: str
    calls: int

    def complete(self, messages: "list[dict[str, Any]]", tools: "list[dict[str, Any]]", *, max_tokens: int = 512) -> Reply: ...


def loopback(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"refusing the non-loopback model endpoint {url!r}: the bake-off is loopback-only")
    return url.rstrip("/")


def parse_tool_call(tc: "dict[str, Any]", i: int) -> "dict[str, Any]":
    fn = tc.get("function") or {}
    raw = fn.get("arguments")
    args, err = None, ""
    if isinstance(raw, dict):
        args = raw
    else:
        try:
            args = json.loads(raw) if raw not in (None, "") else {}
        except ValueError as exc:
            err = f"arguments are not JSON ({exc})"
        if err == "" and not isinstance(args, dict):
            err, args = "arguments are not a JSON object", None
    return {"id": tc.get("id") or f"call_{i}", "name": fn.get("name") or "", "arguments": args, "raw": raw if isinstance(raw, str) else json.dumps(raw), "args_error": err}


class HttpChatModel:
    name = "clone-brain"

    def __init__(self, base_url: str, *, model: str = "local", max_calls: "Optional[int]" = None, timeout_s: float = 180.0, temperature: float = 0.2,
                 opener=urllib.request.urlopen):
        self.base = loopback(base_url)
        self.model, self.max_calls, self.timeout, self.temperature, self._open = model, max_calls, timeout_s, temperature, opener
        self.calls = 0
        self.tokenize_calls = 0
        self.seconds_total = 0.0
        self.prompt_tokens_max = 0
        self.completion_tokens_total = 0

    def _post(self, path: str, body: "dict[str, Any]") -> "dict[str, Any]":
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with self._open(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def complete(self, messages, tools, *, max_tokens: int = 512) -> Reply:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"model-call budget {self.max_calls} spent")
        self.calls += 1
        body: "dict[str, Any]" = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": self.temperature, "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        t0 = time.monotonic()
        try:
            out = self._post("/v1/chat/completions", body)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.seconds_total += time.monotonic() - t0
            raise RuntimeError(f"the brain did not answer: {exc}") from None
        dt = time.monotonic() - t0
        self.seconds_total += dt
        ch = (out.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        usage = out.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        self.prompt_tokens_max = max(self.prompt_tokens_max, pt)
        self.completion_tokens_total += ct
        return Reply(content=msg.get("content") or "", tool_calls=[parse_tool_call(tc, i) for i, tc in enumerate(msg.get("tool_calls") or [])],
                     prompt_tokens=pt, completion_tokens=ct, seconds=dt, finish_reason=str(ch.get("finish_reason") or ""))

    def count_tokens(self, text: str) -> "Optional[int]":
        """The brain's own token count of ``text`` (llama-server ``/tokenize``: no generation, the slot is untouched). None when unavailable."""
        try:
            out = self._post("/tokenize", {"content": text})
        except (urllib.error.URLError, OSError, ValueError):
            return None
        self.tokenize_calls += 1
        return len(out.get("tokens") or [])


# ── the scripted stand-in brain ──────────────────────────────────────────────────────────────────────────────────────

_Q_START = re.compile(r"^(?:what|where|when|who|whom|which|how|did|do|does|is|are|was|were|can|could|tell me|remind me|read back|what's|where's|who's)\b", re.I)
_CHANGE = re.compile(r"\b(?:moved to|now lives in|lives in|moved from)\b", re.I)
_NAME = re.compile(r"\b([A-Z][a-z]{2,})\b")
_STOP_NAMES = frozenset("Today What Where When Who Which How Did Does Tell Remind Read Please The This That User Zoe".split())


def _last_user(messages: "list[dict[str, Any]]") -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


class ScriptedChatModel:
    """Rule-based brain. ``mode="diligent"``: question -> ``mempalace_search`` then answer from the first hit; statement -> ``mempalace_add_drawer`` (wing = the wing
    the system prompt names, room by keyword) and, for a change of state with a known old value, ``mempalace_kg_supersede``; the save checkpoint -> one diary entry.
    ``mode="lazy"``: never a tool call, always a plain reply."""
    name = "scripted-brain"

    def __init__(self, mode: str = "diligent", *, max_calls: "Optional[int]" = None):
        if mode not in ("diligent", "lazy"):
            raise ValueError("mode must be diligent or lazy")
        self.mode, self.max_calls, self.calls, self.log = mode, max_calls, 0, []

    @staticmethod
    def _wing(messages: "list[dict[str, Any]]") -> str:
        sysmsg = str((messages[0] or {}).get("content") or "")
        m = re.search(r'own wing is "([^"]+)"', sysmsg)
        return m.group(1) if m else "user"

    @staticmethod
    def _room(text: str) -> str:
        t = text.lower()
        for room, keys in (("health", ("dentist", "doctor", "allerg", "physio", "vet")), ("family", ("sister", "brother", "mum", "dad", "daughter", "son ", "kids", "cousin")),
                           ("home", ("lives", "moved", "address", "house")), ("work", ("works", "job", "office", "colleague")), ("preferences", ("like", "love", "favourite"))):
            if any(k in t for k in keys):
                return room
        return "general"

    def complete(self, messages, tools, *, max_tokens: int = 512) -> Reply:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"model-call budget {self.max_calls} spent")
        self.calls += 1
        text = _last_user(messages).strip()
        last = messages[-1]
        names = {t["function"]["name"] for t in tools} if tools else set()

        def call(name: str, args: "dict[str, Any]") -> Reply:
            self.log.append((name, args))
            return Reply(tool_calls=[{"id": f"call_{self.calls}", "name": name, "arguments": args, "raw": json.dumps(args), "args_error": ""}], prompt_tokens=800, completion_tokens=40, seconds=0.0)

        if self.mode == "lazy" or not names:
            return Reply(content="Okay.", prompt_tokens=800, completion_tokens=3)
        if last.get("role") == "tool":
            body = str(last.get("content") or "")
            if last.get("name") == "mempalace_kg_query":                       # a change of state: supersede the current value, and file the words
                m = re.search(r'"fact": "(\w+) (\w+) ([^"]+)"[^}]*"current": true', body)
                nm = [n for n in _NAME.findall(_last_user(messages)) if n not in _STOP_NAMES]
                calls = [{"name": "mempalace_add_drawer", "arguments": {"wing": self._wing(messages), "room": self._room(_last_user(messages)), "content": _last_user(messages)}}]
                if m and len(nm) >= 2:
                    calls.append({"name": "mempalace_kg_supersede", "arguments": {"subject": m.group(1), "predicate": m.group(2), "old_object": m.group(3), "new_object": nm[-1]}})
                elif nm:
                    calls.append({"name": "mempalace_kg_add", "arguments": {"subject": nm[0], "predicate": "lives_in", "object": nm[-1]}})
                self.log += [(c["name"], c["arguments"]) for c in calls]
                return Reply(tool_calls=[{"id": f"call_{self.calls}_{i}", "name": c["name"], "arguments": c["arguments"], "raw": json.dumps(c["arguments"]), "args_error": ""}
                                         for i, c in enumerate(calls)], prompt_tokens=900, completion_tokens=60)
            hit = ""
            try:
                res = (json.loads(body).get("results") or [{}])[0]
                hit = str(res.get("text") or "")
            except (ValueError, AttributeError, IndexError):
                pass
            m = re.search(r"⟦[^⟧]*⟧\s*\"(.*)\" \(quoted data", hit, re.S)
            hit = m.group(1) if m else hit
            if hit:
                return Reply(content=hit, prompt_tokens=900, completion_tokens=30)
            if '"success": true' in body.lower() or '"success":true' in body.lower():
                return Reply(content="Noted.", prompt_tokens=900, completion_tokens=3)
            return Reply(content="I don't have that saved.", prompt_tokens=900, completion_tokens=8)
        if text.startswith("MemPalace auto-save checkpoint") and "mempalace_diary_write" in names:
            return call("mempalace_diary_write", {"agent_name": "zoe", "entry": "SESSION|checkpoint|household.chat|★"})
        if _Q_START.match(text) or text.endswith("?"):
            if re.search(r"\b(?:weather|time|timer|alarm|volume|lights?|music|play|pause|skip|news|headlines|times)\b", text, re.I):
                return Reply(content="Okay.", prompt_tokens=800, completion_tokens=3)
            return call("mempalace_search", {"query": text[:240]})
        if re.search(r"\b(?:moved to|now lives in|moved from)\b", text, re.I) and "mempalace_kg_query" in names:
            nm = [n for n in _NAME.findall(text) if n not in _STOP_NAMES]
            if nm:
                return call("mempalace_kg_query", {"entity": nm[0]})
        if len(text.split()) >= 4 and "mempalace_add_drawer" in names and not re.match(r"^(?:good|thanks|thank|hello|hi|hey|okay|ok)\b", text, re.I):
            return call("mempalace_add_drawer", {"wing": self._wing(messages), "room": self._room(text), "content": text})
        return Reply(content="Okay.", prompt_tokens=800, completion_tokens=3)


class PlayModel:
    """A brain that plays a fixed script: round i = the tool calls it makes on the i-th owner message (then it answers ``ok``). The cells' ROGUE brain: it does what
    a confused or hostile model could do (impersonate an author, file its own composition, re-file a forgotten name), so each floor is shown to hold."""
    name = "play-brain"

    def __init__(self, rounds: "list[list[tuple[str, dict[str, Any]]]]"):
        self.rounds, self.calls, self.max_calls = list(rounds), 0, None
        self._n = 0

    def complete(self, messages, tools, *, max_tokens: int = 512) -> Reply:
        self.calls += 1
        if messages[-1].get("role") == "tool":
            return Reply(content="ok", prompt_tokens=700, completion_tokens=2)
        calls = self.rounds[self._n] if self._n < len(self.rounds) else []
        self._n += 1
        if not calls or not tools:
            return Reply(content="ok", prompt_tokens=700, completion_tokens=2)
        return Reply(tool_calls=[{"id": f"play_{self._n}_{i}", "name": n, "arguments": dict(a), "raw": json.dumps(a), "args_error": ""} for i, (n, a) in enumerate(calls)],
                     prompt_tokens=700, completion_tokens=30)


# ── the scripted closet LLM (the real closet_llm runs against it) ───────────────────────────────────────────────────

def closet_json(prompt: str) -> str:
    """Extractive answer to MemPalace's closet prompt: topics = distinctive words, quotes = verbatim sentences, summary = the first two sentences."""
    m = re.search(r"CONTENT:\n(.*?)\n\n---", prompt, re.S)
    content = m.group(1) if m else prompt
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
    words: "list[str]" = []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", content):
        if w.lower() not in {x.lower() for x in words}:
            words.append(w)
    topics = words[:12] if len(words) >= 8 else (words + ["household", "memory", "palace", "note", "day", "home", "life", "fact"])[:8]
    return json.dumps({"topics": topics, "quotes": sents[:5] or [content[:80]], "summary": " ".join(sents[:2])[:300] or content[:200]})


class ScriptedLLMServer:
    def __init__(self, port: int = 0):
        outer = self
        self.requests = 0
        self.brain = ScriptedChatModel("diligent")

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # noqa: ANN002
                return

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests += 1
                if self.path.endswith("/tokenize"):
                    return self._send({"tokens": [0] * max(1, len(str(body.get("content", ""))) // 4)})
                if body.get("tools"):                       # the agent loop: the scripted brain answers in the OpenAI wire format (arguments as a JSON STRING)
                    r = outer.brain.complete(body.get("messages") or [], body["tools"])
                    msg: "dict[str, Any]" = {"role": "assistant", "content": r.content or None}
                    if r.tool_calls:
                        msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in r.tool_calls]
                    return self._send({"choices": [{"message": msg, "finish_reason": "tool_calls" if r.tool_calls else "stop"}],
                                       "usage": {"prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens}})
                prompt = str(((body.get("messages") or [{}])[-1]).get("content") or "")
                text = closet_json(prompt)
                self._send({"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

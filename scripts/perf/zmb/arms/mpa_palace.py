"""The palace side of the MPA arm: MemPalace's tool surface, a schema validator, and two backends behind one ``call(tool, args)``.

* ``McpStdioBackend``  - the REAL thing: ``mempalace-mcp --palace <dir>`` (3.10.0, bake-off venv) driven over stdio JSON-RPC exactly as an MCP host
  (Claude Code) drives it: ``initialize``, ``tools/list``, ``tools/call``. One server process per palace (= per household member: MemPalace has no
  tenant isolation inside a palace, so the account boundary is the process boundary). The server is the ONLY writer of its palace while it runs
  (concurrent Chroma writers are MemPalace's most reported failure); a harness-side operation that needs the files (closet pass, physical erase)
  stops the server first (``restart_around``). Its RSS is read from ``/proc/<pid>/status`` (steady and high-water): that is the G0 number.
* ``DoubleBackend``    - a TEST DOUBLE with the same tool shapes (token-overlap search, half-open temporal triples). It makes NO claim about retrieval
  quality; it lets the slim CI lane prove the glue (floors, hooks, loop, validity accounting) red-before-green.

The tool schemas, protocol text and hook text come from ``mpa_snapshot.json`` (frozen from the installed package by ``pilot/mpa_snapshot.py``).
"""
from __future__ import annotations

import json
import math
import os
import re
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional, Protocol

SNAPSHOT_PATH = Path(__file__).with_name("mpa_snapshot.json")
BAKEOFF_DIR = Path("/home/zoe/.zoe/bakeoff-2026-10")
VENV_BIN = BAKEOFF_DIR / "mempalace-venv" / "bin"
INSTALL_HINT = (f"the real MemPalace MCP server is not available here ({VENV_BIN}/mempalace-mcp). Run through the bake-off venv "
                f"(`pip install mempalace==3.10.0` in {BAKEOFF_DIR / 'mempalace-venv'}); the lab double covers the glue, never the retrieval")


class PalaceUnavailable(NotImplementedError):
    """The real server cannot be started here: a cell that needs it SKIPs with this reason, never passes."""


class ToolError(RuntimeError):
    """The server answered a tool call with a JSON-RPC error (not a tool-level ``success: false``, which is a normal result)."""


def load_snapshot() -> "dict[str, Any]":
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


# ── tokens and schemas ───────────────────────────────────────────────────────────────────────────────────────────────

def est_tokens(text: str) -> int:
    """A deliberately plain estimate (chars / 3.6, rounded up) for the slim lane and the planner. The window / smoke replace it with the clone brain's own
    ``/tokenize`` count; the report prints both so the estimate's error is visible."""
    return int(math.ceil(len(text or "") / 3.6))


def openai_tools(snapshot: "dict[str, Any]", names: "Optional[list[str]]" = None) -> "list[dict[str, Any]]":
    """The agent tools in the OpenAI chat-completions ``tools`` shape llama-server's ``--jinja`` Gemma template renders (the SAME shape the Flue sidecar's
    capped-completions path hands the clone). Descriptions and schemas are MemPalace's, byte for byte."""
    return [{"type": "function", "function": {"name": n, "description": snapshot["tools"][n]["description"], "parameters": snapshot["tools"][n]["input_schema"]}}
            for n in (names or list(snapshot["tools"]))]


_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate_args(schema: "dict[str, Any]", args: Any, path: str = "") -> "list[str]":
    """A dependency-free JSON-Schema subset (type, required, enum, minimum, maximum, maxLength, items, properties): the one the MemPalace schemas use.
    Returns the list of violations (empty = valid). Unknown properties are NOT a violation (MemPalace ignores them; the brain's extra keys are counted
    separately by the loop)."""
    errs: "list[str]" = []
    t = schema.get("type")
    if t in _JSON_TYPES:
        ok = isinstance(args, _JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(args, bool)) and not (t == "integer" and isinstance(args, float) and not args.is_integer())
        if not ok:
            return [f"{path or 'arguments'}: expected {t}, got {type(args).__name__}"]
    if "enum" in schema and args not in schema["enum"]:
        errs.append(f"{path or 'arguments'}: {args!r} not in {schema['enum']}")
    if isinstance(args, (int, float)) and not isinstance(args, bool):
        if "minimum" in schema and args < schema["minimum"]:
            errs.append(f"{path}: {args} < minimum {schema['minimum']}")
        if "maximum" in schema and args > schema["maximum"]:
            errs.append(f"{path}: {args} > maximum {schema['maximum']}")
    if isinstance(args, str) and "maxLength" in schema and len(args) > schema["maxLength"]:
        errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(args, dict):
        for r in schema.get("required") or []:
            if r not in args:
                errs.append(f"{path + '.' if path else ''}{r}: required")
        for k, sub in (schema.get("properties") or {}).items():
            if k in args:
                errs += validate_args(sub, args[k], f"{path + '.' if path else ''}{k}")
    if isinstance(args, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(args):
            errs += validate_args(schema["items"], v, f"{path}[{i}]")
    return errs


def extra_keys(schema: "dict[str, Any]", args: Any) -> "list[str]":
    return sorted(set(args) - set(schema.get("properties") or {})) if isinstance(args, dict) else []


# ── the backend protocol ─────────────────────────────────────────────────────────────────────────────────────────────

class Backend(Protocol):
    kind: str
    path: Path

    def call(self, tool: str, args: "dict[str, Any]") -> "dict[str, Any]": ...
    def rss_mb(self) -> float: ...
    def peak_rss_mb(self) -> float: ...
    def stop(self) -> None: ...
    def start(self) -> None: ...
    def close(self) -> None: ...


def _proc_kb(pid: int, key: str) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key + ":"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


class McpStdioBackend:
    """``mempalace-mcp --palace <dir>`` over stdio JSON-RPC. Single in-flight request (the arm is single-threaded); a per-call timeout kills a hung server."""
    kind = "mempalace-mcp-stdio"

    def __init__(self, palace_dir: "str | Path", *, embed_env: "Optional[dict[str, str]]" = None, call_timeout_s: float = 60.0,
                 env_extra: "Optional[dict[str, str]]" = None, bin_dir: "Path" = VENV_BIN):
        self.path = Path(palace_dir)
        self.root = self.path.parent
        self.bin = Path(bin_dir) / "mempalace-mcp"
        if not self.bin.exists():
            raise PalaceUnavailable(INSTALL_HINT)
        self.embed_env = dict(embed_env or {})
        self.env_extra = dict(env_extra or {})
        self.timeout = call_timeout_s
        self.proc: "Optional[subprocess.Popen]" = None
        self._n = 0
        self.peak = 0.0
        self.tool_ms: "dict[str, list[float]]" = {}
        self._lock = threading.Lock()
        self.tools_listed: "list[dict[str, Any]]" = []
        self.server_info: "dict[str, Any]" = {}
        self.start()

    # environment: a scrubbed HOME / config dir per palace root (never ~/.mempalace, never the live chroma cache; the MiniLM files are linked read-only)
    def _env(self) -> "dict[str, str]":
        real_home = Path(os.environ.get("ZMB_REAL_HOME") or os.environ.get("HOME") or "/")
        home = self.root / "home"
        home.mkdir(parents=True, exist_ok=True)
        cache = real_home / ".cache" / "chroma"
        if cache.is_dir() and not (home / ".cache" / "chroma").exists():
            (home / ".cache").mkdir(parents=True, exist_ok=True)
            (home / ".cache" / "chroma").symlink_to(cache)
        env = dict(os.environ)
        env.update({"HOME": str(home), "MEMPALACE_CONFIG_DIR": str(self.root / "cfg"), "ANONYMIZED_TELEMETRY": "False", "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1", **self.embed_env, **self.env_extra})
        return env

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._err = open(self.root / "server.err", "ab")
        self.proc = subprocess.Popen([str(self.bin), "--palace", str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
                                     env=self._env(), start_new_session=True)
        init = self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "zoe-mpa-arm", "version": "1"}})
        self.server_info = (init.get("result") or {}).get("serverInfo") or {}
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools_listed = (self._rpc("tools/list").get("result") or {}).get("tools") or []

    def _send(self, msg: "dict[str, Any]") -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def _rpc(self, method: str, params: "Optional[dict[str, Any]]" = None) -> "dict[str, Any]":
        assert self.proc and self.proc.stdout
        with self._lock:
            self._n += 1
            self._send({"jsonrpc": "2.0", "id": self._n, "method": method, "params": params or {}})
            fd = self.proc.stdout.fileno()
            deadline = time.monotonic() + self.timeout
            buf = b""
            while b"\n" not in buf:
                left = deadline - time.monotonic()
                if left <= 0 or self.proc.poll() is not None:
                    self._kill()
                    raise ToolError(f"{method}: no answer in {self.timeout:.0f}s" if left <= 0 else f"{method}: the server exited ({self.proc.returncode})")
                if select.select([fd], [], [], min(left, 0.5))[0]:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        self._kill()
                        raise ToolError(f"{method}: the server closed its stdout")
                    buf += chunk
            self.peak = max(self.peak, self.rss_mb())
            return json.loads(buf.split(b"\n", 1)[0])

    def call(self, tool: str, args: "dict[str, Any]") -> "dict[str, Any]":
        t0 = time.monotonic()
        r = self._rpc("tools/call", {"name": tool, "arguments": args})
        self.tool_ms.setdefault(tool, []).append((time.monotonic() - t0) * 1000.0)
        if "error" in r:
            raise ToolError(f"{tool}: {r['error']}")
        res = r.get("result") or {}
        text = "".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        try:
            out = json.loads(text)
        except ValueError:
            out = {"raw": text}
        if isinstance(out, dict) and res.get("isError") and "success" not in out:
            out["success"] = False
        return out if isinstance(out, dict) else {"raw": out}

    def rss_mb(self) -> float:
        return _proc_kb(self.proc.pid, "VmRSS") if self.proc and self.proc.poll() is None else 0.0

    def peak_rss_mb(self) -> float:
        hwm = _proc_kb(self.proc.pid, "VmHWM") if self.proc and self.proc.poll() is None else 0.0
        return max(self.peak, hwm)

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc is None:
            return
        self.peak = max(self.peak, self.peak_rss_mb())
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
        finally:
            for f in (self.proc.stdin, self.proc.stdout):
                try:
                    f and f.close()
                except OSError:
                    pass
            self.proc = None
            try:
                self._err.close()
            except OSError:
                pass

    def close(self) -> None:
        self.stop()

    def python_for_children(self) -> str:
        """The interpreter of the MemPalace venv: harness-side child processes (closet pass, rebuild) must import ``mempalace``."""
        return str(self.bin.parent / "python")


# ── the test double ──────────────────────────────────────────────────────────────────────────────────────────────────

_TOK = re.compile(r"[a-z0-9']+")
_STOP = frozenset("a an the is are was were be am do does did what whats when where who whom my our your of to in on at for from with about and or i me we you it that this there please tell".split())


def _toks(s: str) -> "set[str]":
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in _TOK.findall((s or "").lower()) if t not in _STOP and len(t) > 1}


class DoubleBackend:
    """A TEST DOUBLE of the MemPalace tool surface (the shapes the arm reads). Token-overlap search, no embeddings, no disk."""
    kind = "mempalace-double"

    def __init__(self, palace_dir: "str | Path" = "/nonexistent/double", **_kw: Any):
        self.path = Path(palace_dir)
        self.drawers: "dict[str, dict[str, Any]]" = {}
        self.triples: "list[dict[str, Any]]" = []
        self.calls: "list[tuple[str, dict[str, Any]]]" = []
        self.tool_ms: "dict[str, list[float]]" = {}
        self.fail_next: "Optional[str]" = None          # a test sets this to make the next call raise ToolError
        self._seq = 0
        self.running = True

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        self.running = False

    def rss_mb(self) -> float:
        return 0.0

    def peak_rss_mb(self) -> float:
        return 0.0

    @staticmethod
    def _date(s: "Optional[str]", default: str) -> str:
        return (s or default)[:10]

    def call(self, tool: str, args: "dict[str, Any]") -> "dict[str, Any]":
        if self.fail_next:
            msg, self.fail_next = self.fail_next, None
            raise ToolError(msg)
        self.calls.append((tool, dict(args)))
        fn = getattr(self, "_" + tool.replace("mempalace_", ""), None)
        if fn is None:
            return {"success": False, "error": f"unknown tool {tool}"}
        return fn(**args)

    # tools
    def _status(self) -> "dict[str, Any]":
        if not self.drawers:
            return {"error": "Chroma database missing", "hint": "Run: mempalace status"}
        wings: "dict[str, int]" = {}
        rooms: "dict[str, int]" = {}
        for d in self.drawers.values():
            wings[d["wing"]] = wings.get(d["wing"], 0) + 1
            rooms[d["room"]] = rooms.get(d["room"], 0) + 1
        return {"total_drawers": len(self.drawers), "wings": wings, "rooms": rooms}

    def _add_drawer(self, wing: str, room: str, content: str, source_file: str = "", added_by: str = "mcp") -> "dict[str, Any]":
        if not re.fullmatch(r"[A-Za-z0-9_ .'-]+", room or ""):
            return {"success": False, "error": "room contains invalid characters"}
        self._seq += 1
        did = f"drawer_{wing}_{room}_{abs(hash((wing, room, content))) % 10**12:012d}"
        if did in self.drawers:
            return {"success": True, "reason": "already_exists", "drawer_id": did}
        self.drawers[did] = {"drawer_id": did, "wing": wing, "room": room, "text": content, "source_file": source_file or "", "added_by": added_by,
                             "filed_at": f"2026-10-07T00:00:{self._seq % 60:02d}", "seq": self._seq}
        return {"success": True, "drawer_id": did, "wing": wing, "room": room, "chunks": 1}

    def _search(self, query: str, limit: int = 5, wing: str = "", room: str = "", **_kw: Any) -> "dict[str, Any]":
        q = _toks(query)
        scored = []
        for d in self.drawers.values():
            if (wing and d["wing"] != wing) or (room and d["room"] != room):
                continue
            ov = len(q & _toks(d["text"]))
            if ov:
                scored.append((ov / (1 + 0.05 * len(_toks(d["text"]))), d["seq"], d))
        scored.sort(key=lambda t: (-t[0], t[1]))
        res = [{"drawer_id": d["drawer_id"], "text": d["text"], "wing": d["wing"], "room": d["room"], "source_file": d["source_file"] or "?",
                "filed_at": d["filed_at"], "similarity": round(min(1.0, s), 3)} for s, _i, d in scored[:max(1, int(limit))]]
        return {"query": query, "filters": {"wing": wing or None, "room": room or None}, "total_before_filter": len(self.drawers), "results": res}

    def _get_drawer(self, drawer_id: str) -> "dict[str, Any]":
        d = self.drawers.get(drawer_id)
        return {"drawer_id": drawer_id, "content": d["text"], "wing": d["wing"], "room": d["room"], "metadata": {"filed_at": d["filed_at"], "added_by": d["added_by"],
                                                                                                                 "source_file": d["source_file"]}} if d else {"error": f"Drawer not found: {drawer_id}"}

    def _list_drawers(self, wing: str = "", room: str = "", limit: int = 100, offset: int = 0) -> "dict[str, Any]":
        rows = [d for d in sorted(self.drawers.values(), key=lambda d: d["seq"]) if (not wing or d["wing"] == wing) and (not room or d["room"] == room)]
        return {"drawers": [{"drawer_id": d["drawer_id"], "wing": d["wing"], "room": d["room"], "content_preview": d["text"][:200]} for d in rows[offset:offset + limit]],
                "total": len(rows)}

    def _update_drawer(self, drawer_id: str, content: "Optional[str]" = None, wing: "Optional[str]" = None, room: "Optional[str]" = None) -> "dict[str, Any]":
        d = self.drawers.get(drawer_id)
        if not d:
            return {"success": False, "error": f"Drawer not found: {drawer_id}"}
        d.update({k: v for k, v in (("text", content), ("wing", wing), ("room", room)) if v is not None})
        return {"success": True, "drawer_id": drawer_id}

    def _delete_drawer(self, drawer_id: str) -> "dict[str, Any]":
        return {"success": self.drawers.pop(drawer_id, None) is not None, "drawer_id": drawer_id}

    def _diary_write(self, agent_name: str, entry: str = "", topic: str = "general", wing: str = "", content: str = "") -> "dict[str, Any]":
        r = self._add_drawer(wing or f"wing_{agent_name}", "diary", entry or content, added_by=agent_name)
        return {"success": r.get("success", False), "entry_id": r.get("drawer_id"), "agent": agent_name, "topic": topic}

    def _kg_add(self, subject: str, predicate: str, object: str, valid_from: str = "", valid_to: str = "", **_kw: Any) -> "dict[str, Any]":
        self.triples.append({"subject": subject, "predicate": predicate, "object": object, "valid_from": valid_from or "2026-10-07", "valid_to": valid_to or None})
        return {"success": True, "triple_id": f"t{len(self.triples)}", "fact": f"{subject} → {predicate} → {object}"}

    def _kg_supersede(self, subject: str, predicate: str, old_object: str, new_object: str, at: str = "") -> "dict[str, Any]":
        at = at or "2026-10-07"
        for t in self.triples:
            if (t["subject"].lower(), t["predicate"], t["object"].lower()) == (subject.lower(), predicate, old_object.lower()) and t["valid_to"] is None:
                t["valid_to"] = at
        return {**self._kg_add(subject, predicate, new_object, valid_from=at), "superseded": old_object}

    def _kg_invalidate(self, subject: str, predicate: str, object: str, ended: str = "") -> "dict[str, Any]":
        n = 0
        for t in self.triples:
            if (t["subject"].lower(), t["predicate"], t["object"].lower()) == (subject.lower(), predicate, object.lower()) and t["valid_to"] is None:
                t["valid_to"], n = ended or "2026-10-07", n + 1
        return {"success": True, "ended": ended or "2026-10-07", "closed": n}

    def _kg_query(self, entity: str, as_of: str = "", direction: str = "both") -> "dict[str, Any]":
        rows = [dict(t, current=t["valid_to"] is None) for t in self.triples if entity.lower() in (t["subject"].lower(), t["object"].lower())
                and (not as_of or (t["valid_from"] <= as_of and (t["valid_to"] is None or as_of < t["valid_to"])))]
        return {"entity": entity, "as_of": as_of or None, "active_facts": [r for r in rows if r["current"]], "facts": rows}

    def _kg_timeline(self, entity: str = "", limit: int = 100, offset: int = 0) -> "dict[str, Any]":
        rows = sorted((t for t in self.triples if not entity or entity.lower() in (t["subject"].lower(), t["object"].lower())), key=lambda t: t["valid_from"])
        return {"entity": entity or None, "timeline": rows[offset:offset + limit], "total": len(rows)}


# ── harness-side helpers over a palace's files (server stopped) ──────────────────────────────────────────────────────

def kg_rows(palace: "str | Path") -> "list[dict[str, Any]]":
    """Every triple of a palace's temporal knowledge graph (stdlib sqlite3; read-only; the server may be running, WAL makes that safe)."""
    import sqlite3
    db = Path(palace) / "knowledge_graph.sqlite3"
    if not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT subject, predicate, object, valid_from, valid_to, source_drawer_id FROM triples")]
    finally:
        con.close()


def kg_delete_naming(palace: "str | Path", is_hit) -> int:
    """Physically delete every triple (and entity) whose subject / object text satisfies ``is_hit(text)``, then VACUUM. SERVER MUST BE STOPPED.
    ``id`` columns are MemPalace's normalised entity ids; the readable text is in ``entities.name`` and in the triple's own subject/object."""
    import sqlite3
    db = Path(palace) / "knowledge_graph.sqlite3"
    if not db.is_file():
        return 0
    con = sqlite3.connect(str(db), timeout=10)
    try:
        names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM entities")}
        ids = [r[0] for r in con.execute("SELECT id, subject, predicate, object FROM triples") if is_hit(" ".join(str(x) for x in (names.get(r[1], r[1]), r[2], names.get(r[3], r[3]))))]
        for i in ids:
            con.execute("DELETE FROM triples WHERE id = ?", (i,))
        for eid, name in names.items():
            if is_hit(name) or is_hit(eid):
                con.execute("DELETE FROM triples WHERE subject = ? OR object = ?", (eid, eid))
                con.execute("DELETE FROM entities WHERE id = ?", (eid,))
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        return len(ids)
    finally:
        con.close()


def python_has_mempalace(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import mempalace, chromadb"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def library_available() -> bool:
    return (VENV_BIN / "mempalace-mcp").exists() and (VENV_BIN / "python").exists()


__all__ = ["Backend", "DoubleBackend", "McpStdioBackend", "PalaceUnavailable", "ToolError", "est_tokens", "extra_keys", "kg_delete_naming", "kg_rows",
           "library_available", "load_snapshot", "openai_tools", "validate_args"]

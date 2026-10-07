"""A TEST DOUBLE of Hindsight's HTTP API (the endpoints ``arms.hindsight`` uses), with the documented request / response shapes.

It exists so the adapter's contract and the Zoe layer are proven red-before-green in the slim CI lane without a server, a
Postgres, a brain or an embedder. It makes NO claim about Hindsight's extraction or retrieval quality. What it models, each from
the 0.10.2 docs / source read for the decision record (docs/research/memory-system-decision-2026-10-05.md section 3.1):

* ``retain_extraction_mode``: ``verbatim`` = one unit per item holding the item's text (``_collapse_to_verbatim``); ``concise`` = one
  unit per sentence (a stand-in for the LLM's facts); ``chunks`` = like verbatim.
* re-retaining a ``document_id`` replaces the old document and ALL its memories (api/retain.mdx:120).
* observations (``enable_observations``): consolidation merges facts about the same ATTRIBUTE inside one tag SCOPE and **newest
  evidence wins** - the older observation is invalidated (consolidation/prompts.py:39-47 "PREFER UPDATE OVER CREATE"). Auto
  consolidation runs inside ``retain``; with ``enable_auto_consolidation=false`` only ``POST .../consolidate`` does, per the
  requested ``observation_scopes`` (all_strict tag isolation: a fact of one scope never updates an observation of another).
* document delete cascades to the document's units and invalidates the observations built on them (the stale-observation sweep).
* recall = token overlap, ``tags`` + ``tags_match`` (``all_strict`` / ``any_strict`` / ``any``) honoured.
* no belief-time filter (``as_of`` is not answerable).

FIRST CONTACT (2026-10-06, ``docs/research/bakeoff-setup-verification-2026-10-06.md``): the shapes below were corrected against the REAL 0.10.2 server,
each one a place where this double used to lie to the adapter's tests:

* ``concise`` extraction writes ``"<fact> | Involving: <entities>"`` (and ``| When: ...`` for a dated fact), NOT the bare sentence;
* an LLM extraction mode (verbatim included) answers ``usage`` with real token counts, ``chunks`` (no model) answers zeros; an item may name a
  ``strategy`` the bank defined in ``retain_strategies`` (a per-item extraction mode);
* observations are separate units (``fact_type: observation``, ``document_id: None``, tags = the consolidation scope only - no ``origin:``),
  ONE per source fact, text ``"The user ..."``; the older observation of an attribute is NOT invalidated when a newer fact arrives (both stay
  ``state: valid``: the engine does not retire a belief, the Zoe layer's document delete does, and that cascades to the observation at once);
* list items carry ``date`` / ``mentioned_at`` / ``occurred_start`` / ``occurred_end`` / ``chunk_id`` / ``proof_count`` / ``invalidated_at`` ...;
* recall results carry ``scores``; ``/version`` lists ``features``; operations end ``completed``;
* ``audit_log`` records every recall QUERY (the text the owner asked): a read that names a forgotten person after the forget re-creates residue;
* the LLM trace (``llm-requests``) has ``scope`` / ``started_at`` and outlives its bank (a bank delete does not touch it).

``FakeHindsight`` is a transport: ``FakeHindsight()(method, url, body, timeout) -> (status, bytes)``, so ``HindsightClient`` runs its
real request-building code against it with no socket. ``serve()`` wraps the same handler in a loopback ``http.server`` for the one
test that exercises the real urllib path.

``FakeHindsight(pg=FakePostgres(...))`` also writes what the engine writes to its Postgres (``arms.fake_postgres``): a retain leaves a document,
a memory unit, an entity, an ``audit_log`` row (the whole request) and an ``llm_requests`` row (the extraction prompt); a document delete kills the
document and its units (their bytes stay until a VACUUM FULL) and leaves the entity and the log rows; a bank delete kills the bank's engine rows and
leaves the log rows. That is the measured behaviour (``pilot/pg_erase_probe.py``) the Zoe layer's physical erase exists for.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

_WORD = re.compile(r"[a-z0-9']+")
_ATTRS = (
    ("home", re.compile(r"\b(?:lives?|living|moved|reside)s?\b[^.]*?\b(?:in|to)\s+([A-Z][\w-]+)")),
    ("name", re.compile(r"\b(?:name is|i'm|i am|goes by|called)\s+([A-Z][\w-]+)")),
    ("work", re.compile(r"\b(?:works?|working)\s+(?:at|for)\s+([A-Z][\w &-]+)")),
    ("pet", re.compile(r"\b(?:dog|cat|pet)\b[^.]*?\b(?:named|called|is)\s+([A-Z][\w-]+)")),
    ("wife", re.compile(r"\bwife\b[^.]*?\bnamed\s+([A-Z][\w-]+)")),
)


def _toks(text: str) -> "set[str]":
    return set(_WORD.findall((text or "").lower()))


def _attr(text: str) -> str:
    for name, rx in _ATTRS:
        if rx.search(text or ""):
            return name
    return ""


class FakeHindsight:
    def __init__(self, pg: Any = None, merge_observations: bool = False) -> None:
        self.merge_observations = merge_observations      # True = the engine retires an older observation of the same attribute (the pre-first-contact model)
        self.pg = pg                                       # an ``arms.fake_postgres.FakePostgres``: the store the engine writes to, or None
        self.banks: "dict[str, dict[str, Any]]" = {}
        self.calls: "list[tuple[str, str]]" = []          # (method, path) of every request
        self.bodies: "list[tuple[str, str, Any]]" = []     # (method, path, parsed body)
        self.down = False                                  # the server is unreachable
        self.fail_retain = 0                               # the next N retains answer HTTP 500
        self.busy_polls = 0                                # the next N operations polls report a pending operation
        self.llm_requests: "list[dict[str, str]]" = []     # the server's LLM trace: one row per retain attempt
        self._n = 0
        self._lock = threading.Lock()

    # ── transport ──
    def __call__(self, method: str, url: str, body: "Optional[bytes]", timeout: float) -> "tuple[int, bytes]":
        if self.down:
            from .hindsight import HindsightUnavailable
            raise HindsightUnavailable("fake server is down")
        parsed = urllib.parse.urlparse(url)
        query = {k: v if len(v) > 1 else v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        payload = json.loads(body.decode("utf-8")) if body else None
        with self._lock:
            self.calls.append((method, parsed.path))
            self.bodies.append((method, parsed.path, payload))
            status, out = self._route(method, urllib.parse.unquote(parsed.path), query, payload or {})
        return status, json.dumps(out).encode("utf-8")

    # ── routing ──
    def _route(self, method: str, path: str, q: dict, body: dict) -> "tuple[int, Any]":
        if path == "/health":
            return 200, {"status": "healthy"}
        if path == "/version":
            return 200, {"api_version": "0.10.2-fake", "features": {"observations": False, "bank_config_api": True, "audit_log": True, "llm_trace": True}}
        if path == "/v1/default/banks" and method == "GET":
            q = str(q.get("q") or "").lower()
            return 200, {"banks": [{"bank_id": n} for n in sorted(self.banks) if q in n.lower()]}
        m = re.match(r"^/v1/default/banks/([^/]+)(/.*)?$", path)
        if not m:
            return 404, {"detail": "no route"}
        bank, rest = m.group(1), m.group(2) or ""
        if rest == "" and method in ("PUT", "PATCH"):
            if bank not in self.banks and self.pg is not None:
                self.pg.write("banks", bank, bank, ref=bank)
            self.banks.setdefault(bank, {"config": {}, "docs": {}, "units": [], "name": bank})
            return 200, {"bank_id": bank}
        if rest == "" and method == "DELETE":
            gone = self.banks.pop(bank, None)
            if gone is not None and self.pg is not None:
                self.pg.drop_bank(bank)
            return (200, {"success": True}) if gone is not None else (404, {"detail": "no bank"})
        b = self.banks.get(bank)
        if b is None:
            return 404, {"detail": f"Bank '{bank}' not found"}
        if rest == "/config" and method == "PATCH":
            b["config"].update(body.get("updates") or {})
            return 200, {"bank_id": bank, "config": b["config"], "overrides": b["config"]}
        if rest == "/memories" and method == "POST":
            return self._retain(b, body)
        if rest == "/memories/recall" and method == "POST":
            return self._recall(b, body)
        if rest == "/memories/list" and method == "GET":
            return self._list(b, q)
        if rest == "/consolidate" and method == "POST":
            self._consolidate(b, body.get("observation_scopes"))
            return 200, {"operation_id": "op-1", "deduplicated": False}
        if rest == "/llm-requests" and method == "GET":
            return 200, {"bank_id": bank, "items": [x for x in self.llm_requests if x.get("operation") == (q.get("operation") or x.get("operation"))],
                         "total": len(self.llm_requests), "limit": int(q.get("limit", 100)), "offset": 0}
        if rest == "/operations" and method == "GET":
            if self.busy_polls > 0:
                self.busy_polls -= 1
                return 200, {"bank_id": bank, "total": 1, "operations": [{"id": "op-1", "task_type": "consolidation", "status": "pending"}]}
            return 200, {"bank_id": bank, "total": 0, "operations": []}
        dm = re.match(r"^/documents/(.+)$", rest)
        if dm and method == "GET":
            d = b["docs"].get(dm.group(1))
            return (200, {"id": dm.group(1), "original_text": d["text"], "tags": d["tags"]}) if d else (404, {"detail": "no doc"})
        if dm and method == "DELETE":
            return self._delete_doc(b, dm.group(1))
        return 404, {"detail": f"no route {method} {rest}"}

    # ── retain / consolidate / delete ──
    def _unit(self, b: dict, text: str, doc: str, tags: list, meta: dict, kind: str = "world", when: str = "") -> dict:
        self._n += 1
        ents = re.findall(r"\b[A-Z][a-z]{2,}\b", text)
        now = datetime.now(timezone.utc).isoformat()
        u = {"id": f"u{self._n:05d}", "text": text, "document_id": doc or None, "tags": list(tags), "metadata": dict(meta),
             "state": "valid", "fact_type": kind, "entities": ", ".join(ents), "context": "", "consolidated": False, "sources": [],
             "date": when or now, "mentioned_at": when or now, "occurred_start": None, "occurred_end": None,
             "chunk_id": f"{b['name']}_{doc}_0" if doc else None,
             "proof_count": 1, "consolidated_at": None, "consolidation_failed_at": None, "invalidation_reason": None, "invalidated_at": None,
             "edited_at": None, "updated_at": now}
        b["units"].append(u)
        return u

    def _retain(self, b: dict, body: dict) -> "tuple[int, Any]":
        if self.fail_retain > 0:
            self.fail_retain -= 1
            self.llm_requests.append(self._trace("error", "retain"))
            return 500, {"detail": "extraction failed: invalid JSON from the model"}
        cfg = b["config"]
        tokens = {"input_tokens": 0, "output_tokens": 0}
        for item in body.get("items") or []:
            mode = cfg.get("retain_extraction_mode", "concise")
            strat = (cfg.get("retain_strategies") or {}).get(item.get("strategy") or "")
            if isinstance(strat, dict):                      # a per-item strategy overrides the bank's extraction mode
                mode = strat.get("retain_extraction_mode", mode)
            doc = item.get("document_id") or f"auto-{self._n + 1}"
            self._drop_document(b, doc)                      # re-retaining a document replaces it and all its memories
            text = item.get("content") or ""
            b["docs"][doc] = {"text": text, "tags": item.get("tags") or []}
            if self.pg is not None:
                self._pg_retain(b["name"], doc, text, item, llm=mode != "chunks")
            if mode == "chunks":
                parts = [text]
            else:
                self.llm_requests.append(self._trace("success", "retain", b["name"]))
                tokens["input_tokens"] += 1380 if mode == "verbatim" else 2140
                tokens["output_tokens"] += 110
                parts = [text] if mode == "verbatim" else [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
            for p in parts:
                ents = ", ".join(dict.fromkeys(re.findall(r"\b[A-Z][a-z]{2,}\b", p)))
                shown = p if mode in ("chunks", "verbatim") or not ents else f"{p.rstrip()} | Involving: {ents}"      # the real concise extractor appends what it found
                self._unit(b, shown, doc, item.get("tags") or [], item.get("metadata") or {}, when=str(item.get("timestamp") or ""))
            if cfg.get("enable_observations") and cfg.get("enable_auto_consolidation", True):
                self._consolidate(b, item.get("observation_scopes") or None)
        tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
        return 200, {"success": True, "bank_id": b["name"], "items_count": len(body.get("items") or []), "async": False, "operation_id": None,
                     "operation_ids": None, "usage": {**tokens, "cached_tokens": 0, "thoughts_tokens": 0}}

    def _trace(self, status: str, op: str, bank: str = "") -> dict:
        return {"status": status, "operation": op, "scope": "retain_extract_facts" if op == "retain" else op, "bank_id": bank,
                "started_at": datetime.now(timezone.utc).isoformat(), "input_tokens": 1380, "output_tokens": 110}

    def _consolidate(self, b: dict, scopes: Any) -> None:
        """Observations over the facts of a tag scope (``scopes`` = tag sets, None = one global scope). Real 0.10.2: ONE observation per source fact,
        tagged with the scope only, never retiring an older belief (both stay valid). ``merge_observations`` restores the older model, in which a
        newer fact of the same attribute invalidated the older observation (newest evidence wins)."""
        if not b["config"].get("enable_observations", False):
            return
        scope_sets = [frozenset(s) for s in scopes] if scopes else [None]
        for u in [x for x in b["units"] if x["fact_type"] != "observation" and not x["consolidated"] and x["state"] == "valid"]:
            for sc in scope_sets:
                if sc is not None and not sc <= set(u["tags"]):
                    continue
                key = (_attr(u["text"]), sc if sc is not None else frozenset())
                u["consolidated"] = True
                u["consolidated_at"] = datetime.now(timezone.utc).isoformat()
                if self.merge_observations:
                    if not key[0]:
                        continue
                    for o in b["units"]:
                        if o["fact_type"] == "observation" and o["state"] == "valid" and o.get("key") == key:
                            o["state"] = "invalidated"
                base = u["text"].split(" | ")[0]
                new = self._unit(b, f"The user: {base}", "", sorted(sc) if sc is not None else [], {}, "observation")
                new["key"], new["sources"] = key, [u["id"]]

    def _pg_retain(self, bank: str, doc: str, text: str, item: dict, llm: bool = True) -> None:
        """What one retained item leaves in Postgres (the real shapes: ``pilot/pg_erase_probe.py``)."""
        self.pg.write("audit_log", bank, json.dumps({"items": [item]}))
        if llm:                                              # chunks mode never calls the model: no trace row
            self.pg.write("llm_requests", bank, text)
        self.pg.write("documents", bank, text, ref=doc)
        self.pg.write("memory_units", bank, text, ref=doc)
        have = {r["text"] for r in self.pg.rows.get("entities", []) if r["bank"] == bank and r["live"]}
        for word in re.findall(r"\b[A-Z][a-z]{2,}\b", text):
            if word not in have and word != "User":
                self.pg.write("entities", bank, word, ref=word)
                have.add(word)

    def _drop_document(self, b: dict, doc: str) -> int:
        if self.pg is not None:
            self.pg.kill("documents", b["name"], doc)
            self.pg.kill("memory_units", b["name"], doc)
        gone = {u["id"] for u in b["units"] if u["document_id"] == doc}
        b["units"] = [u for u in b["units"] if u["document_id"] != doc]
        for o in b["units"]:                                  # the stale-observation sweep
            if o["fact_type"] == "observation" and gone & set(o["sources"]):
                o["state"] = "invalidated"
        b["docs"].pop(doc, None)
        return len(gone)

    def _delete_doc(self, b: dict, doc: str) -> "tuple[int, Any]":
        if doc not in b["docs"]:
            return 404, {"detail": "no doc"}
        n = self._drop_document(b, doc)
        return 200, {"success": True, "message": "deleted", "document_id": doc, "memory_units_deleted": n}

    # ── reads ──
    @staticmethod
    def _tag_ok(unit_tags: list, want: list, mode: str) -> bool:
        if not want:
            return True
        have, need = set(unit_tags), set(want)
        if mode in ("any_strict", "any"):
            return bool(have & need) or (mode == "any" and not have)
        return need <= have

    def _recall(self, b: dict, body: dict) -> "tuple[int, Any]":
        q = _toks(body.get("query") or "")
        scored = []
        for u in b["units"]:
            if body.get("types") and u["fact_type"] not in body["types"] and not (u["fact_type"] == "world" and "experience" in body["types"]):
                continue
            if u["state"] != "valid" or not self._tag_ok(u["tags"], body.get("tags") or [], body.get("tags_match", "any")):
                continue
            # semantic recall returns the NEAREST units even when none shares a word with the query ("where do I live" -> "User moved to Perth"),
            # so a zero-overlap unit is ranked last, not dropped (a token-overlap-only double could never answer a paraphrase)
            scored.append((len(q & _toks(u["text"])), u["id"], u))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if self.pg is not None:                              # audit_log keeps the whole request: the QUERY text is on disk until the bank / the name is erased
            self.pg.write("audit_log", b["name"], json.dumps({"query": body.get("query") or ""}))
        res = [{"id": u["id"], "text": u["text"], "type": u["fact_type"], "tags": u["tags"], "document_id": u["document_id"] or None,
                "metadata": u["metadata"], "entities": None, "context": u["context"], "chunk_id": u["chunk_id"], "source_fact_ids": None, "mentioned_at": u["mentioned_at"],
                "scores": {"final": round(1.0 + o * 0.1, 3), "reranker": None, "semantic": 0.5, "keyword": None}, "attachments": None}
               for o, _i, u in scored[:20]]
        return 200, {"results": res}

    def _list(self, b: dict, q: dict) -> "tuple[int, Any]":
        limit, offset = int(q.get("limit", 100)), int(q.get("offset", 0))
        items = [{**{k: v for k, v in u.items() if k not in ("consolidated", "sources", "key")}, "source_memory_ids": list(u["sources"])}
                 for u in b["units"]]                                  # the documented ListMemoryUnit shape: an observation names its source facts
        return 200, {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}

    # ── a loopback http server over the same handler (one test uses it) ──
    def serve(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def _do(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else None
                status, out = fake(method, f"http://{host}{self.path}", raw, 5.0)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self) -> None:  # noqa: N802
                self._do("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._do("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._do("PUT")

            def do_PATCH(self) -> None:  # noqa: N802
                self._do("PATCH")

            def do_DELETE(self) -> None:  # noqa: N802
                self._do("DELETE")

        srv = ThreadingHTTPServer((host, port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

"""The bench surface of the MPA arm: the calls the cells speak (``recall`` / ``recall_exact`` / ``stats`` / ``observations`` ...) and the counters the window reads.

Split from ``mempalace_agent.py`` on purpose: decision rule G3 counts the lines of the Zoe layer (what production would have to maintain around MemPalace); an adapter to the bench's
arm interface is measurement code, not part of that layer. Mixin over the arm's own attributes; nothing here writes to the palace.
"""
from __future__ import annotations

import calendar
import json
import re
import subprocess
import time
from typing import Any

from .base import ROW_KEYS
from .hm_policy import MODEL_FROM_TRANSCRIPT, USER_STATED
from .mpa_palace import ToolError, est_tokens, kg_rows

DAY_S = 86400.0
SIM_TODAY = calendar.timegm((2026, 10, 7, 12, 0, 0))     # the simulated "now": a turn said ``day_offset`` days ago has this date minus that many days
RESERVED_ROOMS = ("unverified", "quoted", "inferred")
DECLINE = "I don't have that saved."


def _epoch(day_offset: int, seq: int = 0) -> float:
    return SIM_TODAY - day_offset * DAY_S + seq * 0.001


class BenchSurface:
    def prompt_cost(self) -> "dict[str, Any]":
        """Tokens of every extra piece (estimated; the brain's own ``/tokenize`` count when a tokenizer is attached), plus the tool schemas and the total."""
        parts = {k: v for k, v in self.prompt_parts().items() if v}
        tools_text = json.dumps(self.tool_specs, ensure_ascii=False, separators=(",", ":"))
        count = self.tokenizer or est_tokens
        out = {k: int(count(v) or est_tokens(v)) for k, v in parts.items()}
        out["tool_schemas"] = int(count(tools_text) or est_tokens(tools_text))
        out["total"] = sum(out.values())
        out["estimator"] = "brain /tokenize" if self.tokenizer else "chars/3.6 estimate"
        return out

    # ── reads (harness side: what the agent's first search would return) ─────────────────────────────────────────
    def _drawers(self) -> "list[dict[str, Any]]":
        out, offset = [], 0
        while True:
            page = self._need().call("mempalace_list_drawers", {"limit": 100, "offset": offset})
            ds = page.get("drawers") or []
            for d in ds:
                full = self._need().call("mempalace_get_drawer", {"drawer_id": d["drawer_id"]})
                out.append({"id": d["drawer_id"], "text": str(full.get("content") or d.get("content_preview") or ""), "wing": d.get("wing", ""), "room": d.get("room", "")})
            offset += len(ds)
            if len(ds) < 100 or not ds:
                return out

    def _hit_row(self, h: "dict[str, Any]") -> "dict[str, Any]":
        p = self._prov.get(str(h.get("drawer_id")))
        room = h.get("room", "")
        cls = p.authority_class if p else USER_STATED
        return {"id": h.get("drawer_id", ""), "text": h.get("text", ""), "status": "approved" if room not in RESERVED_ROOMS else "pending", "authority_class": cls,
                "origin": f"mempalace:{room}", "contradicts_id": "", "entity_type": "", "memory_type": "verbatim", "user_id": self._user, "room": room, "wing": h.get("wing", ""),
                "score": h.get("similarity", 0.0), "day_offset": p.day_offset if p else None}

    def search(self, query: str, k: int = 10, *, include_reserved: bool = False) -> "list[dict[str, Any]]":
        res = self._need().call("mempalace_search", {"query": query[:250], "limit": min(max(k, 1) * (1 if include_reserved else 3), 100)})
        hits = res.get("results") or []
        if self.mc.quarantine_filter and not include_reserved:
            hits = [h for h in hits if h.get("room") not in RESERVED_ROOMS]
        return [self._hit_row(h) for h in hits[:k]]

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        """What the agent's FIRST ``mempalace_search`` returns for the question as asked (protocol rule 2). Whether the brain makes that call is the brain-tier's measurement."""
        return self.search(query, k)

    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":
        return [{"text": r["text"], "day_offset": r["day_offset"]} for r in self.search(query, k, include_reserved=True)]

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":
        """The lab half of (l): the search plus protocol rule 2's other move, ``kg_query`` of each name in the question. The brain-tier cell scores what the brain really gathers."""
        rows = self.search(query, k)
        for name in dict.fromkeys(re.findall(r"\b[A-Z][a-z]{2,}\b", query)):
            try:
                res = self._need().call("mempalace_kg_query", {"entity": name})
            except ToolError:
                continue
            for f in res.get("active_facts") or []:
                rows.append({"id": f"kg:{f.get('subject')}|{f.get('predicate')}|{f.get('object')}", "text": f"{f.get('subject')} {f.get('predicate')} {f.get('object')}", "status": "approved",
                             "authority_class": USER_STATED, "origin": "mempalace:kg", "contradicts_id": "", "entity_type": "", "memory_type": "fact", "user_id": self._user})
        return rows[: k + 8]

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:
        from .. import life as lifemod
        if not fired:
            return lifemod.DECLINE
        return lifemod.anchored_reader(self.search(prompt, k), anchor)

    def answer(self, query: str, k: int = 5) -> str:
        rows = self.search(query, k)
        return rows[0]["text"] if rows else DECLINE

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        cutoff = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        return [r for r in self.search(query, 50) if r.get("day_offset") is None or _epoch(int(r["day_offset"])) <= cutoff]

    # ── the store export ──────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _ts(s: "Optional[str]") -> "Any":
        if not s:
            return ""
        try:
            return float(calendar.timegm(time.strptime(str(s)[:10], "%Y-%m-%d")))
        except ValueError:
            return ""

    def stats(self) -> "dict[str, Any]":
        rows: "list[dict[str, Any]]" = []
        for d in self._drawers():
            p = self._prov.get(d["id"])
            reserved = d["room"] in RESERVED_ROOMS
            rows.append({"id": d["id"], "text": d["text"], "status": "pending" if reserved else "approved", "authority_class": p.authority_class if p else "",
                         "origin": f"{p.writer if p else 'mempalace'}:{d['room']}", "contradicts_id": "", "entity_type": "", "memory_type": "verbatim" if (p and p.anchored) else "distilled",
                         "user_id": self._user, "room": d["room"], "wing": d["wing"], "source_excerpt": p.excerpt if p else "", "user_turn_id": p.turn_id if p else "",
                         "valid_from": _epoch(p.day_offset, p.seq) if p else "", "invalid_at": "", "supersedes_id": "", "superseded_by_id": ""})
        by_key = {}
        kg = self._kg_dump()
        for t in kg:
            key = (str(t["subject"]).lower(), str(t["predicate"]).lower(), str(t["object"]).lower())
            by_key[key] = f"kg:{t['subject']}|{t['predicate']}|{t['object']}".lower()
        for t in kg:
            key = (str(t["subject"]).lower(), str(t["predicate"]).lower(), str(t["object"]).lower())
            p = self._kg_prov.get(key)
            old = self._kg_links.get(key)
            rows.append({"id": by_key[key], "text": f"{t['subject']} {str(t['predicate']).replace('_', ' ')} {t['object']}", "status": "approved" if not t["valid_to"] else "superseded",
                         "authority_class": p.authority_class if p else MODEL_FROM_TRANSCRIPT, "origin": "brain:kg", "contradicts_id": "", "entity_type": "", "memory_type": "fact",
                         "user_id": self._user, "source_excerpt": p.excerpt if p else "", "user_turn_id": p.turn_id if p else "", "valid_from": self._ts(t["valid_from"]),
                         "invalid_at": self._ts(t["valid_to"]), "supersedes_id": by_key.get(old, "") if old else "", "superseded_by_id": ""})
        for r in rows:                                   # back-links: an old row names its successor
            if r["supersedes_id"]:
                for o in rows:
                    if o["id"] == r["supersedes_id"]:
                        o["superseded_by_id"] = r["id"]
        for sd in self._side:
            rows.append({"id": f"side:{len(rows)}", "text": sd["text"], "status": "disputed", "authority_class": MODEL_FROM_TRANSCRIPT, "origin": "held_back",
                         "contradicts_id": sd["contradicts_id"], "entity_type": "", "memory_type": "distilled", "user_id": self._user})
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts, "writes_refused": self.writes_refused, "row_keys": list(ROW_KEYS), "model_calls": self.model_calls,
                "routed": self.routed, "hook_calls": self.hook_calls, "checkpoints": self.checkpoints}

    def _kg_dump(self) -> "list[dict[str, Any]]":
        be = self._need()
        return [dict(t, source_drawer_id="") for t in be.triples] if be.kind == "mempalace-double" else kg_rows(be.path)

    def stats_as(self, identity: str) -> "dict[str, Any]":
        from .mempalace_verbatim import MemPalaceVerbatimArm
        user = MemPalaceVerbatimArm._identity_user(identity)
        if user != self._user:
            return {"rows": [], "counts": {}, "writes_refused": self.writes_refused}      # one palace per account: another identity's palace is not this one
        return self.stats()

    _CLOSET_READER = ("import sys, json\nfrom mempalace.palace import get_closets_collection\n"
                      "c = get_closets_collection(sys.argv[1], create=False, read_only=True)\n"
                      "g = c.get(include=['documents', 'metadatas'])\n"
                      "print(json.dumps([{'doc': d, 'meta': m} for d, m in zip(g['documents'], g['metadatas'])]))\n")

    def closets(self) -> "list[dict[str, Any]]":
        be = self._need()
        py = self._venv_python()
        if not py:
            return []
        be.stop()
        try:
            r = subprocess.run([py, "-c", self._CLOSET_READER, str(be.path)], capture_output=True, text=True, timeout=120, env=self._child_env())
        finally:
            be.start()
        try:
            return json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return []

    def observations(self, query: str = "") -> "dict[str, Any]":
        """(k) The closet SUMMARIES (the one derived statement per source file the closet LLM writes), the arm's reflection layer. ``model`` says whose model wrote them."""
        items = []
        for c in self.closets():
            lines = [ln for ln in str(c["doc"]).split("\n") if ln.strip()]
            if not lines or not str((c.get("meta") or {}).get("generated_by", "")).startswith("llm:"):
                continue
            text = lines[-1].split("|")[0].strip()
            if text:
                items.append({"text": text, "stated_by": "inferred", "id": str((c.get("meta") or {}).get("source_file", ""))})
        if query:
            q = set(re.findall(r"[a-z0-9']+", query.lower()))
            items.sort(key=lambda it: -len(q & set(re.findall(r"[a-z0-9']+", it["text"].lower()))))
            items = items[:5]
        return {"items": items, "model": "scripted" if self.closet_scripted else "own"}

    # ── what the window reads ─────────────────────────────────────────────────────────────────────────────────────
    def tool_stats(self) -> "dict[str, Any]":
        calls = [r for t in self.traces for r in t.tools]
        valid = sum(1 for r in calls if r.valid)
        needed = [t for t in self.traces if not t.checkpoint]
        return {"tool_calls": len(calls), "tool_calls_valid": valid, "extra_keys": sum(1 for r in calls if r.extra), "refused": sum(1 for r in calls if r.refused),
                "by_tool": {n: sum(1 for r in calls if r.name == n) for n in sorted({r.name for r in calls})},
                "turns": len(needed), "searched_before_answer": [sum(1 for t in needed if t.searched_before_answer), len(needed)],
                "filter_misses": sum(t.filter_misses for t in self.traces), "model_calls": self.model_calls, "model_s": round(self.model_seconds, 2),
                "prompt_tokens_max": self.prompt_tokens_max, "checkpoints": self.checkpoints, "routed": self.routed, "hook_calls": self.hook_calls,
                "supersedes": [list(x) for t in self.traces for x in t.supersedes]}

    def server_rss(self) -> "dict[str, float]":
        be = self._need()
        return {"steady_mb": round(be.rss_mb(), 1), "peak_mb": round(be.peak_rss_mb(), 1)}

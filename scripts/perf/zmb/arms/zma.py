"""Arm ZMA: Zoe's CURRENT memory stack (Z0e: the real ``MemoryService`` over Chroma + MiniLM, authority classes, the forget ledger, the nightly digest and
conflict pass) with MemPalace operated properly on top, INTEGRATED the way HMA integrates Hindsight - one memory, two organs, not two engines side by side.

    verified owner turn --> gate --> ONE verbatim chunk in the user's palace (the HARNESS files it, no model call) --> Z0's extractor / people graph read FROM the
                                     stored chunk (its id is the provenance of every fact derived from it) --> Z0 rows (authoritative, authority classes, ledger)
    read:   the brain's turn carries Z0's authoritative packet (named-person floor, authority order) and has MemPalace's wake-up ``status`` + ``search`` for
            the owner's exact words; the shim returns ONE packet: Z0's rows first, then verbatim lines the rows do not already cover
    night:  Z0's digest + conflict pass (unchanged) AND MemPalace's closet pass (the clone brain indexes and summarises each session's chunks) in one idle window
    forget: ONE contract: Z0's permanent ledger (#1883) + the same entity through MemPalace (drawers, triples, closets, WAL, palace rebuild); one residue scan

What is integrated, and what each saves (``INTEGRATION``, printed by the report):

  one ingest path   every user turn is written ONCE, as a MemPalace chunk; Z0 does not keep a second transcript: its extractor reads the stored chunk and its rows cite
                    the chunk id. Deduped: the second copy of the transcript (``chat_messages`` rows for the memory pipeline) and the second embedding of the same words
                    (measured by the cell ``ZMA-W1``: the number of verbatim copies of an owner sentence in the whole store is one chunk + its extracted fact, never two chunks).
  one embedder      Chroma (Z0e's collection) and MemPalace's index both ask the window's embedding shim: ONE model resident (the shim is served as MiniLM,
                    Z0e's own model, for the window: ``BAKEOFF_SHIM_MODEL=minilm``). Saves the second ONNX session (RAM lab: +120..191 MB, measured).
  one packet        Z0's authoritative rows and MemPalace's exact words arrive together; a verbatim line a Z0 row already cites is not repeated; on a conflict about the
                    same attribute Z0's authority order wins (the floors are Z0's, MemPalace adds exact words and wake-up context, never a rival belief).
  one forget        Z0's ledger is the authority; the MemPalace tier is purged by the same entity and by the ledger's hashes; the two residue scans (#1885 Chroma, MPA's
                    palace/WAL/server log) both see nothing.
  one protocol      Z0's existing brain prompt stays; MemPalace adds rules 1-3 of its protocol and two tools (status, search) - rules 4-5 and the write tools are NOT offered
                    (the harness files the words; Z0's conflict pass retires the old fact). ``protocol_cost()`` prices it against Z0 alone from the committed sources.

The brain NEVER writes in ZMA (no model call on the write path, the owner's design); everything the brain does with MemPalace is reading. Nothing runs at import time.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .base import Arm, IngestReport, Turn
from .hm_policy import ROOM_QUOTED, ROOM_UNVERIFIED, USER_STATED, alias_candidates
from .mempalace_agent import RESERVED_ROOMS, MemPalaceAgentArm, MpaControls, Prov, sim_date
from .mpa_palace import ToolError, est_tokens

INTEGRATION = {
    "one_ingest_path": "a user turn is written once, as a MemPalace chunk; Z0's extractor reads the stored chunk and cites its id: no second transcript copy, no second embedding of the same words",
    "one_embedder": "Chroma (Z0e) and MemPalace share the window's embedding shim served as MiniLM: one model resident instead of two ONNX sessions (-120..191 MB measured)",
    "one_packet": "Z0's authoritative rows + MemPalace's exact words, deduplicated by chunk id; Z0's authority order and named-person floor win every conflict",
    "one_forget": "Z0's permanent ledger authoritative; the same entity and the ledger's hashes purge the chunk store, the triples, the closets and the WAL; both residue scans clean",
    "one_protocol": "Z0's brain prompt + MemPalace rules 1-3 + two read tools (status, search); no write tools, no AAAK, no diary",
    "one_night": "Z0's digest + conflict pass and MemPalace's closet pass in one idle window on one brain",
}
BRAIN_TOOLS = ("mempalace_status", "mempalace_search")
SOURCES = ("labs/flue-zoe-brain-2x/src/soul.ts", "labs/flue-zoe-brain-2x/src/agents/zoe.ts", "labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts")
_ARRAYS = ("IN_SESSION_CONTEXT_DOCTRINE", "RECALL_PRECEDENCE_DOCTRINE")


class ShimEmbeddingFunction:
    """A Chroma embedding function that asks the window's loopback ``/v1/embeddings`` shim (the model MemPalace's ``openai-compat`` backend also asks)."""

    def __init__(self, base_url: str, model: str = "shim"):
        from .mpa_model import loopback
        self.base, self.model = loopback(base_url), model
        self.calls = 0

    def name(self) -> str:
        return f"zmb-shim-{self.model}"

    def __call__(self, input: "list[str]") -> "list[list[float]]":     # noqa: A002 - Chroma's signature
        out: "list[list[float]]" = []
        for i in range(0, len(input), 32):
            body = json.dumps({"model": self.model, "input": list(input[i:i + 32]), "encoding_format": "float"}).encode()
            req = urllib.request.Request(self.base + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                out += [d["embedding"] for d in sorted(json.loads(r.read().decode())["data"], key=lambda d: d["index"])]
            self.calls += 1
        return out


def z0_memory_prompt_text(repo: "Optional[Path]" = None) -> str:
    """The memory part of Z0's CURRENT brain prompt, read from the committed sources: the soul's recall paragraph, the two recall doctrines and the ``recall_memory``
    tool description. (An estimate of the live prompt's memory share: string literals only, escapes not decoded.)"""
    root = repo or Path(__file__).resolve().parents[4]
    parts: "list[str]" = []
    zoe = (root / SOURCES[1]).read_text(encoding="utf-8")
    for name in _ARRAYS:
        m = re.search(rf"export const {name} = \[(.*?)\]\.join", zoe, re.S)
        if m:
            parts += [x or y or z for x, y, z in re.findall(r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`)""", m.group(1))]
    soul = (root / SOURCES[0]).read_text(encoding="utf-8")
    parts += [ln.strip() for ln in soul.splitlines() if "recall" in ln.lower() and not ln.strip().startswith(("//", "*"))]
    tools = (root / SOURCES[2]).read_text(encoding="utf-8")
    m = re.search(r"name: 'recall_memory',\s*description:(.*?)input:", tools, re.S)
    if m:
        parts.append("".join(a or b for a, b in re.findall(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""", m.group(1))))
    return "\n".join(parts)


class ZMAArm(Arm):
    name = "ZMA"
    capabilities = frozenset({"clock", "controls", "reader", "identities", "idle_pass", "conflict_pass", "edges", "exact_words", "observations", "multi_hop", "protocol", "verbatim"})

    def __init__(self, z0: Any = None, mpa: "Optional[MemPalaceAgentArm]" = None, *, embed_url: str = "", embed_model: str = "shim", one_ingest: bool = True, one_packet: bool = True, **mpa_kw: Any):
        import os
        os.environ.setdefault("ZOE_FORGET_LEDGER_SALT", "zmb-lab-forget-ledger-secret-0123456789")      # the durable forget ledger (#1883) ON, in memory, as the H arms' lab layer has it
        if z0 is None:
            from .z0 import Z0Arm
            z0 = Z0Arm(name="Z0e", embed=True)
        self.z0 = z0
        self.mpa = mpa if mpa is not None else MemPalaceAgentArm(
            tool_names=BRAIN_TOOLS, protocol_rules=(1, 2, 3), aaak=False, rules_paragraph=False, embed_url=embed_url, mpa=mpa_kw.pop("mpa_controls", None), **mpa_kw)
        self.mpa.checkpoint_enabled = False
        self.mpa.turn_context = self._packet_text
        self.embed_url, self.embed_model = embed_url, embed_model
        self.one_ingest, self.one_packet = one_ingest, one_packet          # named switches: a negative control turns ONE integration off
        self.z0.index_exact = not one_ingest                               # one ingest path: MemPalace is the verbatim tier, Z0 keeps no exact-words copy (the control gives it back)
        self.shim_ef: "Optional[ShimEmbeddingFunction]" = None
        self.controls = self.mpa.controls
        self._user = ""
        self.chunk_of: "dict[str, str]" = {}                 # normalised turn text -> chunk id (the provenance every Z0 row of that turn cites)
        self.chunks = 0
        self.dedup_dropped = 0
        self.packet_split = {"packets": 0, "z0": 0, "mempalace": 0, "reserved_per_packet": 0}     # the packet budget's two tiers, summed over every packet

    @property
    def ledger(self):
        return self.mpa.ledger

    # ── lifecycle ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _share_embedder(self) -> None:
        if not self.embed_url or not hasattr(self.z0, "svc"):
            return
        self.shim_ef = ShimEmbeddingFunction(self.embed_url, self.embed_model)
        self.z0.svc.memory_service._drawers_embedding_function = lambda: self.shim_ef      # Z0e's collection now embeds through the shim (one model resident)

    def reset(self, user_id: str, *, disk: bool = False, **kw: Any) -> None:
        """``disk=True``: Z0's store is REAL Chroma in a throwaway directory (hash vectors) so ``residue`` can byte-scan what a forget leaves behind (the physical-forget cell)."""
        if disk and not importlib.util.find_spec("chromadb"):
            raise NotImplementedError("ZMA's physical-forget cell needs chromadb to open Z0's store on disk (not installed here)")
        self.mpa.reset(user_id)                               # first: an unavailable MemPalace is a SKIP before Z0's store is built
        self._share_embedder()
        self.z0.reset(user_id, disk=disk)
        self._user, self.chunk_of, self.chunks, self.dedup_dropped = user_id, {}, 0, 0
        self.packet_split = {"packets": 0, "z0": 0, "mempalace": 0, "reserved_per_packet": 0}

    def close(self) -> None:
        self.mpa.close()
        self.z0.close()

    def advance_clock(self, seconds: float) -> None:
        self.z0.advance_clock(seconds)
        self.mpa.advance_clock(seconds)

    def set_account_name(self, name: str) -> None:
        self.mpa.set_account_name(name)
        if hasattr(self.z0, "set_account_name"):
            self.z0.set_account_name(name)

    # ── the one ingest path ────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

    def _file_chunk(self, user: str, t: Turn) -> "tuple[str, str]":
        """File the owner's words ONCE (harness, no model) and read the stored chunk back: ``(chunk id, stored text)``; ``("", text)`` when nothing was filed."""
        d = self.mpa.decide(t, user)
        if not d.store or (self.controls.ledger_write_check and self.ledger.matches(user, t.text) and t.speaker != "owner_taught"):
            return "", t.text
        if d.room in (ROOM_UNVERIFIED, ROOM_QUOTED):
            self.mpa._file_quarantine(user, t, d)
            return "", t.text                                  # quarantine words are never Z0's input beyond what its own gate decides
        if d.room not in ("voice", "chat"):
            return "", t.text
        self.mpa._seq += 1
        try:
            res = self.mpa._need().call("mempalace_add_drawer", {"wing": self.mpa._account, "room": d.room, "content": t.text, "added_by": f"zoe:{self.mpa._account}",
                                                                  "source_file": f"chunk:{sim_date(t.day_offset)}"})
            did = str(res.get("drawer_id") or "")
            if not res.get("success") or not did:
                return "", t.text
            got = self.mpa._need().call("mempalace_get_drawer", {"drawer_id": did})
        except ToolError:
            return "", t.text
        text = str(got.get("content") or t.text)
        self.mpa._prov[did] = Prov(d.authority_class or USER_STATED, t.speaker, t.day_offset, f"t{self.mpa._seq}", text[:300], self.mpa._account, d.room, True, "harness", self.mpa._seq)
        self.chunk_of[self._key(text)] = did
        self.chunks += 1
        return did, text

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        for t in turns:
            if t.speaker == "system_writer":                   # model passes are Z0's (digest): MemPalace holds the owner's words only
                sub = self.z0.ingest([t])
            else:
                _cid, text = self._file_chunk(self._user, t)
                sub = self.z0.ingest([dataclasses.replace(t, text=text)] if self.one_ingest else [t])     # Z0's extractor reads FROM the stored chunk (control: its own copy)
            rep.turns += sub.turns
            rep.written += sub.written
            rep.refused += sub.refused
            rep.retired += sub.retired
            rep.notes += sub.notes
        return rep

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        return self.z0.ingest_as(identity, turns)             # another member's palace is not this one (one palace per account); Z0's own identity floors decide

    # ── the one packet ─────────────────────────────────────────────────────────────────────────────────────────────
    def _z0_rows(self, query: str, k: int) -> "list[dict[str, Any]]":
        return self.z0.recall(query, k)

    def _covered(self, chunk_text: str, rows: "list[dict[str, Any]]") -> bool:
        key = self._key(chunk_text)
        return any(key and (key == self._key(r.get("source_excerpt", "")) or key in self._key(r.get("text", ""))) for r in rows)

    def packet(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        from .hm import attr_of
        rows = self._z0_rows(query, k)
        extra = []
        for v in self.mpa.search(query, k):
            if self.one_packet and self._covered(v["text"], rows):
                self.dedup_dropped += 1
                continue
            a = attr_of(v["text"])
            if self.controls.authority and a and any((b := attr_of(r.get("text", ""))) and b[0] == a[0] and b[1] != a[1] for r in rows):
                self.dedup_dropped += 1                         # Z0's authority order wins over a verbatim line about the same attribute
                continue
            extra.append(v)
        # ONE budget, two tiers: Z0 returning k rows must not crowd every MemPalace row out (ZMA would be measured as Z0 alone). Reserve r = min(#extra, max(1, budget // 3))
        # slots for the verbatim rows (after Z0's, deterministic); a slot the verbatim tier does not use goes back to Z0. Dedup and the authority gate above still apply.
        budget = max(k, len(rows))
        r = min(len(extra), max(1, budget // 3)) if extra else 0
        keep = rows[:max(budget - r, 0)]
        out = keep + extra[:max(budget - len(keep), 0)]
        sp = self.packet_split
        sp["packets"], sp["z0"], sp["mempalace"] = sp["packets"] + 1, sp["z0"] + len(keep), sp["mempalace"] + len(out) - len(keep)
        sp["reserved_per_packet"] = max(sp["reserved_per_packet"], r)
        return out

    def _packet_text(self, text: str) -> str:
        return "\n".join(f"- {r['text']}" for r in self._z0_rows(text, 5))

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        return self.packet(query, k)

    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":
        return self.mpa.recall_exact(query, k)

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":
        return self.packet(query, k)

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:
        from .. import life as lifemod
        return lifemod.DECLINE if not fired else lifemod.anchored_reader(self.packet(prompt, k), anchor)

    def answer(self, query: str, k: int = 5) -> str:
        rows = self.packet(query, k)
        return rows[0]["text"] if rows else "I don't have that saved."

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        return self.z0.as_of(query, ts)

    def converse(self, text: str, day_offset: int = 0):
        """One brain turn: Z0's packet in the message, MemPalace's wake-up context and ``status`` / ``search`` tools (the brain reads; it never writes)."""
        return self.mpa._agent_turn(text, day_offset, "owner_typed")

    # ── the night ──────────────────────────────────────────────────────────────────────────────────────────────────
    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":
        out = dict(self.z0.run_idle_pass(transcript, proposes, judge=judge))
        if self.mpa.closet_url:
            out["closets"] = self.mpa.closet_pass().get("processed", 0)
            out["model_calls"] = out.get("model_calls", 0) + out["closets"]
        return out

    def run_conflict_pass(self) -> "dict[str, Any]":
        return self.z0.run_conflict_pass()

    def write_edge(self, *a: Any, **kw: Any) -> None:
        return self.z0.write_edge(*a, **kw)

    def edges(self) -> "list[dict[str, Any]]":
        return self.z0.edges()

    def observations(self, query: str = "") -> "dict[str, Any]":
        z = self.z0.observations(query)
        if not self.mpa.closet_url:
            return z
        c = self.mpa.observations(query)
        return {"items": list(z["items"]) + list(c["items"]), "model": c["model"]}

    # ── the one forget ─────────────────────────────────────────────────────────────────────────────────────────────
    def forget(self, entity: str) -> str:
        z = self.z0.forget(entity)                             # Z0's permanent ledger first: from this instant no writer can re-add it
        m = self.mpa.forget(entity)
        self.chunk_of = {k: v for k, v in self.chunk_of.items() if v in self.mpa._prov}
        return f"{z} | {m}"

    def alias_candidates(self, entity: str) -> "list[str]":
        if not self.controls.alias_sweep:
            return []
        return alias_candidates(entity, [r.get("text", "") for r in self.stats()["rows"]])

    def forget_alias(self, alias: str) -> str:
        return self.forget(alias)

    def residue(self, entity: str) -> int:
        """BOTH stores: MemPalace's files AND Z0's data directory (Chroma / SQLite / FTS / WAL / HNSW, byte-scanned with the forgotten name's spellings). A Z0 store with no
        directory to scan cannot be shown clean: that is a SKIP (NotImplementedError), never a zero."""
        lab = getattr(self.z0, "_lab_service", None)
        if lab is None or not os.path.isdir(getattr(lab, "data_dir", "") or ""):
            raise NotImplementedError("ZMA's physical residue needs Z0's store open on disk (reset(..., disk=True)); an in-memory Z0 cannot be shown clean")
        z = self.z0.disk_residue([entity])
        return self.mpa.residue(entity) + sum(int(v.get("total", 0)) for v in (z.get("tokens") or {}).values())

    # ── export ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def stats(self) -> "dict[str, Any]":
        z = self.z0.stats()
        rows = []
        for r in z["rows"]:
            r = dict(r)
            cid = self.chunk_of.get(self._key(r.get("source_excerpt", ""))) if self.one_ingest else ""
            if cid:
                r["z0_turn_id"], r["user_turn_id"] = r.get("user_turn_id", ""), cid     # provenance: the chunk the fact was read from
            rows.append(r)
        v = self.mpa.stats()
        verb = [r for r in v["rows"] if r.get("room") in ("voice", "chat") or r.get("origin", "").startswith("harness")]
        rows += verb
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {**z, "rows": rows, "counts": counts, "tiers": {"z0": len(z["rows"]), "mempalace": len(verb)}, "chunks": self.chunks, "packet_split": dict(self.packet_split)}

    def stats_as(self, identity: str) -> "dict[str, Any]":
        return self.z0.stats_as(identity)

    # ── what the prompt costs ──────────────────────────────────────────────────────────────────────────────────────
    def protocol_cost(self, queries: "Optional[list[str]]" = None) -> "dict[str, Any]":
        """Tokens ZMA's brain prompt adds to Z0's own (static: protocol rules 1-3, conventions, two tool schemas; per turn: the verbatim lines the packet adds)."""
        z0_text = z0_memory_prompt_text()
        mp = self.mpa.prompt_cost()
        per_turn = None
        if queries:
            z_only = sum(est_tokens(" ".join(r["text"] for r in self._z0_rows(q, 5))) for q in queries)
            both = sum(est_tokens(" ".join(r["text"] for r in self.packet(q, 5))) for q in queries)
            per_turn = {"z0_packet_tokens": round(z_only / len(queries), 1), "zma_packet_tokens": round(both / len(queries), 1), "n": len(queries)}
        slot, out_reserve = 8192, 1536
        z0_static = est_tokens(z0_text)
        return {"z0_memory_prompt_tokens": z0_static, "mpa_static_tokens": mp["total"], "mpa_parts": {k: v for k, v in mp.items() if k not in ("total", "estimator")},
                "zma_added_tokens": mp["total"], "per_turn": per_turn, "slot_tokens": slot, "output_reserve": out_reserve,
                "fits_with_z0_prompt": z0_static + mp["total"] + out_reserve < slot, "estimator": mp["estimator"], "z0_basis": "committed soul.ts / agents/zoe.ts / zoe-tools.ts literals"}


__all__ = ["ZMAArm", "INTEGRATION", "BRAIN_TOOLS", "ShimEmbeddingFunction", "z0_memory_prompt_text", "RESERVED_ROOMS", "MpaControls"]

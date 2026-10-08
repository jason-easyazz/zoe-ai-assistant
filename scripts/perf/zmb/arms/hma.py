"""Arm HMA: Hindsight and an agent-operated MemPalace INTEGRATED, not side by side.

HM (arms/hm.py) is two engines with a gate in front: the verbatim tier is passive, Hindsight reads a copy of every chunk, and there are two recall
calls, two prompts' worth of instructions and two forget paths. HMA is one memory with two organs, and each integration is a named, measured saving
(``INTEGRATION`` below is the list the report prints):

  one ingest path     the brain files drawers in MemPalace (the episodic tier, MPA: wings/rooms/KG/protocol); a drawer is the ONLY thing Hindsight ever ingests:
                      ``document_id`` = ``drawer id``, ``metadata.source_ids`` = the same id, class / wing / room as tags. No raw turn stream, no second copy, no
                      double extraction; a re-filed drawer (``update_drawer``) re-retains the same document (Hindsight replaces it and its memories).
  one embedder        both tiers ask the window's embedding shim (``ZMB_HM_EMBEDDER_URL``): MemPalace's ``openai-compat`` backend and Hindsight's own provider
                      share ONE ONNX session (RAM lab 2026-10-06: a second MiniLM session is +120..191 MB).
  one packet          the brain calls ONE tool, ``mempalace_search``; the shim returns the verbatim hits (exact words) AND the reflective lines Hindsight holds
                      about the same query (observations, links), deduplicated against the hits (a fact whose source drawer is already in the packet is not
                      repeated), authority-gated (a derived fact that contradicts a user-stated drawer on the same attribute is dropped). One tool call and one
                      model round trip instead of two tools and two.
  one protocol        MemPalace's five rules plus ONE paragraph on reflections replace "MemPalace's protocol + Hindsight's recommended usage + two tool surfaces":
                      the brain never sees a Hindsight tool. The token cost of the merged text is measured against the unmerged sum.
  one forget          one ledger, one contract: forgetting deletes the drawers (MPA: drawers, triples, closets, WAL, palace rebuild), then the SAME drawer ids are
                      deleted from Hindsight (provenance cascade, observations built on them go with them), the text route sweeps what names the entity, and the
                      Postgres scrub finishes it. The distiller skips ledger-matching text. Both tiers are covered by one residue scan.
  one idle step       the stop hook, the closet pass and Hindsight's per-authority-scope consolidation run in one idle window against one brain.

The Zoe floors are MPA's (gate, identity, authority anchor, quarantine, frame, ledger): HMA adds none of its own, it only refuses to let the second tier see what
the first tier's floors refused. Nothing runs at import time; the real Hindsight needs the bake-off server, else ``HindsightUnavailable`` (a SKIP, never a pass).
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .base import Arm, IngestReport, Turn
from .hindsight import HindsightClient, HindsightError
from .hm_policy import RANK, USER_STATED, USER_STATED_DERIVED, HashedLedger
from .mempalace_agent import MemPalaceAgentArm, MpaControls
from .mpa_palace import est_tokens

#: what each integration saves (the report prints this; each is measured by a cell or a counter, not claimed)
INTEGRATION = {
    "one_ingest_path": "Hindsight ingests only the drawers the brain filed (document_id = drawer id): no raw-turn copy, no second verbatim store, no double extraction",
    "one_embedder": "MemPalace and Hindsight share the window's embedding shim: no second ONNX session (-120..191 MB measured in the RAM lab)",
    "one_packet": "one tool call returns exact words and reflections, deduplicated and authority-gated: one model round trip, fewer prompt tokens",
    "one_protocol": "MemPalace's five rules + one reflections paragraph replace two protocols and two tool surfaces: fewer prompt tokens (measured)",
    "one_forget": "one ledger; drawer ids cascade into Hindsight, then the text route and the Postgres scrub: one contract, one residue scan",
    "one_idle_step": "stop hook + closet pass + per-scope consolidation in one window on one brain",
}

#: the ONE paragraph HMA adds to MemPalace's protocol (the unmerged alternative is Hindsight's recommended usage + tool descriptions in addition)
REFLECTIONS_PARAGRAPH = (
    "A search result may also carry \"reflections\": patterns noticed across what the person has told you. They are your own inferences, not their words: "
    "prefer their quoted words when the two differ, and never say they told you something that only a reflection holds.")

#: an unmerged HMA would hand the brain Hindsight's own recommended usage beside MemPalace's protocol: recall before each reply, retain what matters, reflect on
#: patterns (paraphrase of the Hindsight docs' usage section, the policy ``life.POLICIES['hindsight']`` stands in for), plus a second tool surface. Used only to price the merge.
UNMERGED_HINDSIGHT_USAGE = (
    "Hindsight memory usage: before every reply call recall with the person's message to retrieve relevant facts and observations. After the person tells you "
    "something durable call retain with their own words. Use reflect for a considered answer about patterns over time. Observations are consolidated from facts; "
    "facts of different authority are never merged; treat user-stated facts as ground truth and inferred observations as hypotheses.")


class ReflectiveTier:
    """Hindsight as the reflective organ: concise extraction + observations, consolidated per authority scope (H2's fence). One BANK per user; one DOCUMENT per
    drawer (``document_id`` = the drawer id): deleting a drawer deletes its document, its memories and the observations built on them."""
    CONFIG = {"retain_extraction_mode": "concise", "enable_observations": True, "enable_auto_consolidation": False, "enable_reranking": False}

    def __init__(self, client: "Optional[HindsightClient]" = None, *, base_url: "Optional[str]" = None, transport=None, pg: Any = None, keep_banks: bool = False,
                 settle_timeout_s: float = 240.0, settle_poll_s: float = 2.0, fake: bool = False):
        self.client = client or HindsightClient(base_url, transport)
        self.pg, self.keep_banks = pg, keep_banks
        self.fake = fake                     # True only for the in-process stand-in (the double / CI lane); a stand-in's evidence never certifies a gate
        self.settle_timeout_s, self.settle_poll_s = settle_timeout_s, settle_poll_s
        self.fail = False
        self.model_calls = 0
        self.retained: "dict[str, str]" = {}
        self._banks: "set[str]" = set()

    @staticmethod
    def bank_for(user: str) -> str:
        return f"zmb-hma-{user}".lower()

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("reflective tier unavailable")

    def _bank(self, user: str) -> str:
        bank = self.bank_for(user)
        if bank not in self._banks:
            self.client.delete_bank(bank)
            self.client.put_bank(bank)
            self.client.patch_config(bank, self.CONFIG)
            self._banks.add(bank)
        return bank

    def reset(self, user: str) -> None:
        for b in list(self._banks):
            if not self.keep_banks:
                self.client.delete_bank(b)
            self._banks.discard(b)
        self.model_calls = 0
        self.fail = False
        self.retained = {}
        self._bank(user)

    def retain_drawer(self, user: str, drawer_id: str, text: str, cls: str, *, wing: str = "", room: str = "", when: "Optional[str]" = None) -> None:
        self._check()
        item: "dict[str, Any]" = {"content": text, "document_id": drawer_id, "context": "the user is speaking",
                                  "tags": [f"user:{user}", f"class:{cls}", f"room:{room}", f"wing:{wing}"],
                                  "metadata": {"source_ids": drawer_id, "authority_class": cls},
                                  "observation_scopes": [[f"user:{user}", f"class:{cls}"]]}
        if when:
            item["timestamp"] = when
        self.client.retain(self._bank(user), [item])
        self.retained[drawer_id] = text
        self.model_calls += 1

    def consolidate(self, user: str, classes: "list[str]") -> None:
        self._check()
        bank = self._bank(user)
        if classes:
            self.client.consolidate(bank, [[f"user:{user}", f"class:{c}"] for c in classes])
            self.model_calls += len(classes)
        deadline, idle = time.monotonic() + self.settle_timeout_s, 0
        while time.monotonic() < deadline:
            idle = idle + 1 if self.client.pending_operations(bank) == 0 else 0
            if idle >= 2:
                return
            time.sleep(self.settle_poll_s)
        raise HindsightError(504, "operations", f"bank {bank} still busy after {self.settle_timeout_s:.0f}s")

    def units(self, user: str) -> "list[dict[str, Any]]":
        self._check()
        return [u for u in self.client.list_units(self._bank(user)) if str(u.get("state") or "valid") == "valid"] if self.bank_for(user) in self._banks else []

    def recall(self, user: str, query: str, *, budget: str = "low") -> "list[dict[str, Any]]":
        self._check()
        if self.bank_for(user) not in self._banks:
            return []
        return self.client.recall(self.bank_for(user), query, tags=[f"user:{user}"], budget=budget)

    @staticmethod
    def kind(u: "dict[str, Any]") -> str:
        return str(u.get("fact_type") or u.get("type") or "")

    @staticmethod
    def cls_of(u: "dict[str, Any]") -> str:
        return next((str(t)[6:] for t in (u.get("tags") or []) if str(t).startswith("class:")), "")

    @staticmethod
    def sources(u: "dict[str, Any]") -> "set[str]":
        return {x for x in str((u.get("metadata") or {}).get("source_ids") or "").split(",") if x}

    def delete_documents(self, user: str, ids: "list[str]") -> int:
        n = 0
        if self.bank_for(user) in self._banks:
            for i in ids:
                n += self.client.delete_document(self.bank_for(user), i)
                self.retained.pop(i, None)
        return n

    def forget_text(self, user: str, pat) -> int:
        """The text route: documents whose units (or the observations built on them) name the entity."""
        bank, docs = self.bank_for(user), set()
        if bank not in self._banks:
            return 0
        for u in self.client.list_units(bank):
            if pat.search(str(u.get("text") or "")):
                docs |= ({str(u["document_id"])} if u.get("document_id") else set()) | self.sources(u)
        for d in sorted(docs):
            self.client.delete_document(bank, d)
            self.retained.pop(d, None)
        return len(docs)

    def scrub(self, user: str, entity: str) -> None:
        if self.pg is not None and self.bank_for(user) in self._banks:
            self.pg.erase_text(self.bank_for(user), entity)
            self.pg.compact()

    def residue(self, entity: str) -> int:
        """Bytes-level hits of the forgotten name in the reflective tier's storage (the scratch Postgres: live rows, dead tuples, statistics, WAL). A real tier with no
        verifier cannot say it is clean: that is a SKIP (NotImplementedError), never a zero."""
        if self.pg is None:
            if self.fake:
                return 0
            raise NotImplementedError("the reflective tier's physical residue needs the scratch Postgres verifier (ScratchPostgres); without it Hindsight cannot be shown clean")
        res = self.pg.scan([entity])
        return sum(int(v.get("total", 0)) for v in (res.get("tokens") or {}).values())

    def close(self) -> None:
        if not self.keep_banks:
            for b in list(self._banks):
                try:
                    self.client.delete_bank(b)
                except (HindsightError, NotImplementedError):
                    pass
        self._banks.clear()


class HMAArm(Arm):
    name = "HMA"
    capabilities = MemPalaceAgentArm.capabilities

    def __init__(self, mpa: "Optional[MemPalaceAgentArm]" = None, reflective: "Optional[ReflectiveTier]" = None, *, merged_protocol: bool = True,
                 mpa_controls: "Optional[MpaControls]" = None, one_ingest: bool = True, one_forget: bool = True, one_packet: bool = True, **mpa_kw: Any):
        self.mpa = mpa if mpa is not None else MemPalaceAgentArm(mpa=mpa_controls, **mpa_kw)
        self.refl = reflective if reflective is not None else ReflectiveTier()
        self.merged_protocol = merged_protocol
        #: the integrations as named switches (a negative control turns ONE off and the cell that claims it must go red)
        self.one_ingest, self.one_forget, self.one_packet = one_ingest, one_forget, one_packet
        self.controls = self.mpa.controls
        self._pending: "list[dict[str, Any]]" = []
        self._user = ""
        self.packets = 0
        self.dedup_dropped = 0
        self.authority_dropped = 0
        self.packet_split = {"packets": 0, "verbatim": 0, "reflective": 0, "reserved_per_packet": 0}      # the packet budget's two tiers, summed over every packet
        self.reflections_served = 0
        self.mpa.on_write = self._on_write
        self.mpa.search_augment = self._augment
        if merged_protocol:
            self.mpa.extra_protocol = REFLECTIONS_PARAGRAPH

    @property
    def ledger(self) -> HashedLedger:
        return self.mpa.ledger

    # ── lifecycle ─────────────────────────────────────────────────────────
    def reset(self, user_id: str, **kw: Any) -> None:
        self.mpa.reset(user_id, **kw)                       # first: the real palace may be unavailable (a SKIP before any bank is made)
        self.refl.reset(user_id)
        self._user, self._pending = user_id, []
        self.packets = self.dedup_dropped = self.authority_dropped = self.reflections_served = 0
        self.packet_split = {"packets": 0, "verbatim": 0, "reflective": 0, "reserved_per_packet": 0}
        self.mpa.on_write, self.mpa.search_augment = self._on_write, self._augment
        self.mpa.extra_protocol = REFLECTIONS_PARAGRAPH if self.merged_protocol else ""

    def close(self) -> None:
        self.mpa.close()
        self.refl.close()

    def advance_clock(self, seconds: float) -> None:
        self.mpa.advance_clock(seconds)

    def set_account_name(self, name: str) -> None:
        self.mpa.set_account_name(name)

    # ── the one ingest path ───────────────────────────────────────────────
    def _on_write(self, ev: "dict[str, Any]") -> None:
        """Every drawer a writer of the first tier files is queued for the second: the ONLY door into Hindsight. A re-file of the same id replaces its queue entry."""
        if self.controls.distiller_skip and self.ledger.matches(ev["user"], ev["text"]):
            return
        self._pending = [p for p in self._pending if p["id"] != ev["id"]] + [ev]

    def converse(self, text: str, day_offset: int = 0):
        return self.mpa.converse(text, day_offset)

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        rep = self.mpa.ingest(turns)
        if not self.one_ingest:                              # NEGATIVE CONTROL: the old way, a second door: every raw owner turn is ALSO queued for Hindsight
            for i, t in enumerate(turns):
                if t.speaker in ("owner_typed", "owner_taught", "owner_voice_verified"):
                    self._on_write({"kind": "write", "id": f"rawturn-{len(self._pending)}-{i}", "text": t.text, "class": USER_STATED, "day_offset": t.day_offset, "wing": "", "room": "raw", "user": self._user})
        return rep

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        return self.mpa.ingest_as(identity, turns)

    # ── the one idle step ─────────────────────────────────────────────────
    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":
        out = self.mpa.run_idle_pass(transcript, proposes, judge=judge)         # stop hook, proposals, closet pass (one brain)
        mine, self._pending = [p for p in self._pending if p["user"] == self._user], [p for p in self._pending if p["user"] != self._user]
        retained, skipped, classes = 0, 0, set()
        for p in mine:
            if self.controls.distiller_skip and self.ledger.matches(self._user, p["text"]):
                skipped += 1
                continue
            when = None
            self.refl.retain_drawer(self._user, p["id"], p["text"], p["class"], wing=p["wing"], room=p["room"], when=when)
            classes.add(p["class"])
            retained += 1
        if retained:
            self.refl.consolidate(self._user, sorted(classes))
        out.update(distilled=out.get("distilled", 0) + retained, skipped_forgotten=out.get("skipped_forgotten", 0) + skipped,
                   model_calls=out.get("model_calls", 0) + (self.refl.model_calls if retained else 0), reflective_documents=retained)
        return out

    # ── the one packet ────────────────────────────────────────────────────
    def _attr_conflict(self, text: str, verbatim_rows: "list[dict[str, Any]]") -> bool:
        from .hm import attr_of
        a = attr_of(text)
        if not a:
            return False
        for r in verbatim_rows:
            b = attr_of(r.get("text", ""))
            if b and b[0] == a[0] and b[1] != a[1] and RANK.get(r.get("authority_class", ""), 0) > RANK[USER_STATED_DERIVED]:
                return True
        return False

    def _reflective(self, query: str, hits: "list[dict[str, Any]]", budget: str = "low") -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
        """(facts, observations) from Hindsight for ``query``, deduplicated against ``hits`` (the first tier's rows) and authority-gated."""
        have_ids = {str(h.get("id") or h.get("drawer_id") or "") for h in hits}
        have_text = {" ".join(str(h.get("text", "")).lower().split()) for h in hits}
        facts, obs = [], []
        for u in self.refl.recall(self._user, query, budget=budget):
            text = str(u.get("text") or "")
            if self.refl.kind(u) == "observation":
                if self.controls.authority and self._attr_conflict(text, hits):      # the SAME authority gate as a fact: a derived line never rides ahead of the owner's exact words
                    self.authority_dropped += 1
                    continue
                obs.append({"text": text, "class": self.refl.cls_of(u)})
                continue
            if self.one_packet and (self.refl.sources(u) & have_ids or " ".join(text.lower().split()) in have_text or any(t and t in " ".join(text.lower().split()) for t in have_text if len(t) > 20)):
                self.dedup_dropped += 1
                continue
            if self.controls.authority and self._attr_conflict(text, hits):
                self.authority_dropped += 1
                continue
            facts.append({"text": text, "class": self.refl.cls_of(u) or USER_STATED_DERIVED})
        return facts, obs

    def _augment(self, query: str, raw_hits: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
        """What rides in the brain's ONE search result besides the verbatim hits: up to three reflective lines (observations first, then facts the drawers do not hold)."""
        hits = [{"id": h.get("drawer_id"), "text": h.get("text", ""), "authority_class": (self.mpa._prov.get(str(h.get("drawer_id"))).authority_class
                                                                                          if self.mpa._prov.get(str(h.get("drawer_id"))) else USER_STATED)} for h in raw_hits]
        facts, obs = self._reflective(query, hits)
        out = [{"text": o["text"], "kind": "reflection"} for o in obs[:2]] + [{"text": f["text"], "kind": "inferred fact"} for f in facts[:2]]
        self.reflections_served += len(out[:3])
        return out[:3]

    def packet(self, query: str, k: int = 10, *, exact: bool = False, budget: str = "low") -> "list[dict[str, Any]]":
        self.packets += 1
        rows = self.mpa.search(query, k)
        if exact:
            return rows
        try:
            facts, obs = self._reflective(query, rows, budget)
        except Exception:                                                  # noqa: BLE001 - tier isolation: the reflective tier down = the verbatim packet alone
            return rows
        extra = [{"id": f"obs:{i}", "text": o["text"], "status": "approved", "authority_class": o["class"] or USER_STATED_DERIVED, "origin": "distilled:observation",
                  "contradicts_id": "", "entity_type": "", "memory_type": "observation", "user_id": self._user} for i, o in enumerate(obs)]               # observations first, as in the search result
        extra += [{"id": f"refl:{i}", "text": f["text"], "status": "approved", "authority_class": f["class"], "origin": "distilled", "contradicts_id": "", "entity_type": "",
                   "memory_type": "distilled", "user_id": self._user} for i, f in enumerate(facts)]
        # ONE budget, two tiers: a saturated verbatim search (k rows) must not crowd every reflective row out, or HMA is measured as if Hindsight were absent. Reserve
        # r = min(#reflective, max(1, k // 3)) slots; verbatim keeps the rest (and any slot the reflective tier leaves unused is not wasted). Deterministic.
        budget = max(k, len(rows))
        r = min(len(extra), max(1, budget // 3)) if extra else 0
        keep = rows[:max(budget - r, 0)]
        out = keep + extra[:max(budget - len(keep), 0)]
        sp = self.packet_split
        sp["packets"] += 1
        sp["verbatim"] += len(keep)
        sp["reflective"] += len(out) - len(keep)
        sp["reserved_per_packet"] = max(sp["reserved_per_packet"], r)
        return out

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        return self.packet(query, k)

    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":
        return self.mpa.recall_exact(query, k)

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":
        return self.packet(query, k, budget="high") + self.mpa.recall_linked(query, 2)[-2:]

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:
        from .. import life as lifemod
        if not fired:
            return lifemod.DECLINE
        return lifemod.anchored_reader(self.packet(prompt, k), anchor)

    def answer(self, query: str, k: int = 5) -> str:
        rows = self.packet(query, k)
        return rows[0]["text"] if rows else "I don't have that saved."

    def observations(self, query: str = "") -> "dict[str, Any]":
        """(k) Hindsight's observation layer over the drawers (the engine's own model: the clone brain); the closet summaries ride beside them as a second reflective source."""
        items = []
        for u in self.refl.units(self._user):
            if self.refl.kind(u) != "observation":
                continue
            cls = self.refl.cls_of(u)
            items.append({"id": str(u.get("id") or ""), "text": str(u.get("text") or ""), "stated_by": "user" if cls == USER_STATED else "inferred"})
        if query:
            q = set(query.lower().split())
            items.sort(key=lambda it: -len(q & set(it["text"].lower().split())))
            items = items[:5]
        return {"items": items, "model": "own"}

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        return self.mpa.as_of(query, ts)

    # ── the one forget ────────────────────────────────────────────────────
    def forget(self, entity: str) -> str:
        from .mempalace_verbatim import _name_pattern
        msg = self.mpa.forget(entity)                                      # ledger first (shared), then drawers, triples, closets, WAL, rebuild
        n_c = n_t = 0
        if self.one_forget:                                                # NEGATIVE CONTROL off = the reflective tier is simply not told
            n_c = self.refl.delete_documents(self._user, self.mpa.last_forgotten_ids)  # provenance cascade: the SAME ids
            n_t = self.refl.forget_text(self._user, _name_pattern(entity))             # text route: whatever still names it
            self.refl.scrub(self._user, entity)                            # Postgres: log rows, dead tuples, statistics
        if self.controls.distiller_skip:
            self._pending = [p for p in self._pending if not self.ledger.matches(self._user, p["text"])]
        return f"{msg}; reflective tier: {n_c} cascaded + {n_t} by text"

    def alias_candidates(self, entity: str) -> "list[str]":
        return self.mpa.alias_candidates(entity)

    def forget_alias(self, alias: str) -> str:
        return self.forget(alias)

    # ── export ────────────────────────────────────────────────────────────
    def stats(self) -> "dict[str, Any]":
        v = self.mpa.stats()
        rows = list(v["rows"])
        for u in self.refl.units(self._user):
            kind = self.refl.kind(u)
            rows.append({"id": f"{'obs' if kind == 'observation' else 'refl'}:{u.get('id')}", "text": str(u.get("text") or ""), "status": "approved",
                         "authority_class": self.refl.cls_of(u) or USER_STATED_DERIVED, "origin": "distilled:observation" if kind == "observation" else "distilled",
                         "contradicts_id": "", "entity_type": "", "memory_type": kind or "distilled", "user_id": self._user, "sources": sorted(self.refl.sources(u))})
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {**v, "rows": rows, "counts": counts, "model_calls": v["model_calls"] + self.refl.model_calls, "pending": len(self._pending),
                "tiers": {"mempalace": len(v["rows"]), "hindsight": len(rows) - len(v["rows"])}, "packet_split": dict(self.packet_split)}

    def stats_as(self, identity: str) -> "dict[str, Any]":
        return self.mpa.stats_as(identity)

    def residue(self, entity: str) -> int:
        """Both tiers: the MemPalace files AND the reflective tier's scratch Postgres - the name is physically gone only when neither holds it."""
        return self.mpa.residue(entity) + self.refl.residue(entity)

    def protocol_cost(self) -> "dict[str, Any]":
        """The ONE prompt protocol's tokens against the unmerged alternative (MemPalace's protocol + Hindsight's recommended usage + Hindsight's three tools' schemas)."""
        base = self.mpa.prompt_cost()
        merged_extra = est_tokens(REFLECTIONS_PARAGRAPH)
        unmerged_extra = est_tokens(UNMERGED_HINDSIGHT_USAGE) + 3 * 160          # ~160 tokens of schema per recall / retain / reflect tool (Hindsight MCP tool descriptions, estimate)
        only = base["total"] - (merged_extra if self.mpa.extra_protocol else 0)
        return {"mpa_total": only, "merged_extra": merged_extra, "unmerged_extra": unmerged_extra, "saved_tokens": unmerged_extra - merged_extra,
                "hma_total": base["total"], "estimator": base["estimator"]}


__all__ = ["HMAArm", "ReflectiveTier", "INTEGRATION", "REFLECTIONS_PARAGRAPH", "UNMERGED_HINDSIGHT_USAGE"]

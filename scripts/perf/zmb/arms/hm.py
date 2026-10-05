"""Arm HM: Hindsight (distilled / semantic tier) + MemPalace (verbatim / episodic tier), one arm.

The owner's proposal, made testable: the verbatim tier is written instantly with NO model call; a background job
distils it into Hindsight; retrieval asks Hindsight first for distilled facts and MemPalace for the exact text.
This module is the Zoe layer that makes that safe (docs/research/memory-arm-hm-hindsight-mempalace-2026-10-06.md,
Part B): ONE write gate in front of both tiers, one forget ledger consulted by both, authority ordering at recall,
an evidence frame on every verbatim line, a voice-lane policy, and per-tier failure isolation.

    turn --classify--> verbatim write (no model) ----------------------------+
                          |                                                  |
                          +--> pending queue --idle--> distiller (model) --> distilled tier (provenance = chunk ids)
    recall(query) --> distilled lookup  ||  verbatim lookup  --> dedupe --> authority --> frame --> budget

Two distilled tiers satisfy the ``DistilledTier`` protocol:

* ``HindsightDistilledTier``  - the REAL one: still a stub (raises ``NotImplementedError`` with the install hint, the
  same contract as ``arms.hindsight``): Hindsight 0.10.2 needs the scratch Postgres, the loopback embedding shim and
  the clone brain in an operator-approved brain-stop window.
* ``FakeDistilledTier``       - a TEST DOUBLE with the same contract (rule-based "model", authority-gated store,
  provenance by source id). It makes NO claim about Hindsight's quality; it exists so the glue (gate, ledger, cascade,
  authority, framing, lanes) is proven red-without / green-with in the slim CI lane.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .base import Arm, IngestReport, ROW_KEYS, Turn
from .hindsight import INSTALL_HINT as HINDSIGHT_HINT
from .hm_policy import (DEFAULT_ROOMS, MODEL_FROM_TRANSCRIPT, RANK, SPEAKER_LABEL, USER_STATED, USER_STATED_DERIVED,
                        Controls, HashedLedger, LatencyModel, frame, label_for, percentile)
from .mempalace_verbatim import MemPalaceVerbatimArm, _name_pattern, _toks

# ── attribute keys: how the lab decides two statements are about the same thing ───────────────────────────────

_ATTR_PATTERNS = (
    ("home", re.compile(r"\b(?:live|lives|living|moved|moving|reside)s?\b[^.]*?\b(?:in|to)\s+([A-Z][\w-]+)")),
    ("name", re.compile(r"\b(?:my name is|i'm|i am|name is|goes by)\s+([A-Z][\w-]+)")),
    ("work", re.compile(r"\b(?:work|works|working)\s+(?:at|for)\s+([A-Z][\w &-]+)")),
    ("dentist", re.compile(r"\bdentist\b[^.]*?\b(?:is|was)\s+(?:Dr\.?\s+)?([A-Z][\w-]+)")),
)


def attr_of(text: str) -> "tuple[str, str] | None":
    """(attribute, value) for the few attributes the lab cells use, else None."""
    for attr, rx in _ATTR_PATTERNS:
        m = rx.search(text or "")
        if m:
            return attr, m.group(1).strip().lower()
    return None


# ── the distilled tier ────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    id: str
    text: str
    authority_class: str
    source_ids: "tuple[str, ...]"
    status: str = "approved"
    attr: str = ""
    seq: int = 0


class DistilledTier(Protocol):
    name: str

    def reset(self, user_id: str) -> None: ...
    def add_fact(self, user: str, text: str, authority_class: str, source_ids: "tuple[str, ...]") -> str: ...
    def distil(self, user: str, chunks: "list[tuple[str, str]]", proposes: "list[str]") -> "dict[str, int]": ...
    def recall(self, user: str, query: str, k: int) -> "list[Fact]": ...
    def forget(self, user: str, entity: str) -> int: ...
    def delete_sources(self, user: str, source_ids: "list[str]") -> int: ...
    def siblings(self, user: str, source_ids: "list[str]") -> "list[str]": ...
    def rows(self, user: str) -> "list[Fact]": ...
    def close(self) -> None: ...


class HindsightDistilledTier:
    """The real distilled tier: a STUB until the bake-off runner fills it in (same contract as ``arms.hindsight``)."""
    name = "hindsight"

    def __init__(self, variant: str = "H2"):
        self.variant = variant

    def _todo(self, what: str):
        raise NotImplementedError(f"arm HM: Hindsight tier {what} is not implemented. {HINDSIGHT_HINT} "
                                  "HM needs the `concise` extraction mode with provenance by document_id "
                                  "(document = one bundle of verbatim chunk ids) so a deleted chunk cascades.")

    def reset(self, user_id): self._todo("reset")
    def add_fact(self, user, text, authority_class, source_ids): self._todo("add_fact")
    def distil(self, user, chunks, proposes): self._todo("distil")
    def recall(self, user, query, k): self._todo("recall")
    def forget(self, user, entity): self._todo("forget")
    def delete_sources(self, user, source_ids): self._todo("delete_sources")
    def siblings(self, user, source_ids): self._todo("siblings")
    def rows(self, user): self._todo("rows")
    def close(self): return None


class FakeDistilledTier:
    """TEST DOUBLE of the distilled tier: authority-gated, provenance by source id, token-overlap recall."""
    name = "fake-distilled"

    def __init__(self) -> None:
        self._facts: "dict[str, list[Fact]]" = {}
        self._seq = 0
        self.model_calls = 0
        self.fail = False                 # a cell flips this to simulate the tier being down
        self.enforce_authority = True     # HM sets this from ``Controls.authority`` (a negative control turns it off)

    def reset(self, user_id: str) -> None:
        self._facts = {}
        self._seq = 0
        self.model_calls = 0
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("distilled tier unavailable")

    def add_fact(self, user: str, text: str, authority_class: str, source_ids: "tuple[str, ...]" = ()) -> str:
        """Store a fact. A lower-ranked writer can NOT supersede a higher-ranked fact about the same attribute
        (the held-back proposal is kept as ``disputed``, lossless, never recalled)."""
        self._check()
        self._seq += 1
        a = attr_of(text)
        f = Fact(id=f"d{self._seq:04d}", text=text, authority_class=authority_class, source_ids=tuple(source_ids),
                 attr=a[0] if a else "", seq=self._seq)
        rows = self._facts.setdefault(user, [])
        if a:
            for old in rows:
                if old.status == "approved" and old.attr == a[0] and attr_of(old.text) != a:
                    if not self.enforce_authority or RANK[authority_class] >= RANK[old.authority_class]:
                        old.status = "superseded"      # newest evidence wins: the S1 signature when authority is OFF
                    else:
                        f.status = "disputed"
        rows.append(f)
        return f.id

    def distil(self, user: str, chunks: "list[tuple[str, str]]", proposes: "list[str]") -> "dict[str, int]":
        """The scripted 'model': each proposal becomes a fact whose provenance is the whole bundle (as a
        Hindsight document: a bundle's facts are removed together with the bundle)."""
        self._check()
        self.model_calls += 1
        src = tuple(i for i, _t in chunks)
        added = 0
        for p in proposes:
            self.add_fact(user, p, USER_STATED_DERIVED, src)
            added += 1
        return {"facts": added, "model_calls": 1}

    def recall(self, user: str, query: str, k: int) -> "list[Fact]":
        self._check()
        q = _toks(query)
        scored = []
        for f in self._facts.get(user, []):
            if f.status != "approved":
                continue
            ov = len(q & _toks(f.text))
            if ov:
                scored.append((ov, f.seq, f))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [f for _o, _s, f in scored[:k]]

    def forget(self, user: str, entity: str) -> int:
        """The text route: delete facts whose text names the entity."""
        self._check()
        pat = _name_pattern(entity)
        rows = self._facts.get(user, [])
        keep = [f for f in rows if not pat.search(f.text)]
        n = len(rows) - len(keep)
        self._facts[user] = keep
        return n

    def siblings(self, user: str, source_ids: "list[str]") -> "list[str]":
        """Source ids that share a fact (a bundle) with any of ``source_ids``."""
        gone = set(source_ids)
        out: "set[str]" = set()
        for f in self._facts.get(user, []):
            if gone & set(f.source_ids):
                out |= set(f.source_ids)
        return sorted(out)

    def delete_sources(self, user: str, source_ids: "list[str]") -> int:
        """The provenance route: delete every fact derived from a deleted chunk (its text need not name anyone)."""
        self._check()
        gone = set(source_ids)
        rows = self._facts.get(user, [])
        keep = [f for f in rows if not (gone & set(f.source_ids))]
        n = len(rows) - len(keep)
        self._facts[user] = keep
        return n

    def rows(self, user: str) -> "list[Fact]":
        return list(self._facts.get(user, []))

    def close(self) -> None:
        self._facts = {}


# ── the combined arm ──────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Pending:
    chunk_id: str
    user: str
    text: str
    authority_class: str


@dataclass
class PacketInfo:
    lane: str = "chat"
    elapsed_ms: float = 0.0
    degraded: "list[str]" = field(default_factory=list)
    suppressed: "list[str]" = field(default_factory=list)
    cache_miss: bool = False
    tiers: "list[str]" = field(default_factory=list)


class HMArm(Arm):
    """Distilled + verbatim, composed. ``distilled`` defaults to the Hindsight stub (so ``--arm HM`` in the runner is a
    declared SKIP with the install hint, exactly like H1)."""
    name = "HM"
    capabilities = frozenset({"clock", "identities", "idle_pass", "verbatim", "reader"})

    #: bullets the packet may carry and the share the verbatim tier may take of them on an ordinary turn
    PACKET_MAX = 12
    VERBATIM_QUOTA = 4

    def __init__(self, distilled: "DistilledTier | None" = None, verbatim: "MemPalaceVerbatimArm | None" = None,
                 controls: "Controls | None" = None, latency: "LatencyModel | None" = None,
                 ledger: "HashedLedger | None" = None):
        self.controls = controls or Controls()
        self.ledger = ledger if ledger is not None else HashedLedger()
        self.distilled: DistilledTier = distilled if distilled is not None else HindsightDistilledTier()
        self.verbatim = verbatim if verbatim is not None else MemPalaceVerbatimArm(controls=self.controls,
                                                                                 ledger=self.ledger)
        self.verbatim.controls = self.controls
        self.verbatim.ledger = self.ledger
        self.latency = latency or LatencyModel()
        self._user = ""
        self._pending: "list[Pending]" = []
        self._cache: "list[dict[str, Any]]" = []
        self._lat_n: "dict[str, int]" = {}
        self.last = PacketInfo()
        self.model_calls = 0
        self._names: "dict[str, str]" = {}
        if hasattr(self.distilled, "enforce_authority"):
            self.distilled.enforce_authority = self.controls.authority

    # ── lifecycle ─────────────────────────────────────────────────────────
    def reset(self, user_id: str) -> None:
        self.distilled.reset(user_id)          # first: the Hindsight stub raises here, before any palace is opened
        self.verbatim.reset(user_id)
        self.ledger = HashedLedger()
        self.verbatim.ledger = self.ledger
        self._user = user_id
        self._pending = []
        self._cache = []
        self._lat_n = {}
        self.last = PacketInfo()
        self.model_calls = 0
        self._names = {}

    def close(self) -> None:
        self.verbatim.close()
        self.distilled.close()

    def advance_clock(self, seconds: float) -> None:
        self.verbatim.advance_clock(seconds)

    # ── writes ────────────────────────────────────────────────────────────
    def ingest(self, turns: "list[Turn]") -> IngestReport:
        return self._ingest_for(self._user, turns)

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:
        return self._ingest_for(self.verbatim._identity_user(identity), turns)

    def _ingest_for(self, user: str, turns: "list[Turn]") -> IngestReport:
        rep = IngestReport()
        for t in turns:
            rep.turns += 1
            if t.speaker == "system_writer":              # a model pass proposes a fact: authority-gated, distilled only
                for p in t.proposes:
                    if self.controls.distiller_skip and self.ledger.matches(user, p):
                        rep.refused += 1
                        continue
                    self.distilled.add_fact(user, p, MODEL_FROM_TRANSCRIPT, ())
                    rep.written += 1
                continue
            d = self.verbatim.decide(t, user)
            if not d.store:
                rep.refused += 1
                rep.notes.append(d.reason)
                continue
            if self.controls.ledger_write_check and self.ledger.matches(user, t.text):
                if t.speaker == "owner_taught" and d.authority_class == USER_STATED:
                    self.ledger.release_text(user, t.text)       # an explicit re-teach by the verified person
                else:
                    rep.refused += 1
                    rep.notes.append("names a forgotten entity")
                    continue
            cid = self.verbatim.write_chunk(user, t, d)
            rep.written += 1
            if t.speaker == "owner_taught":
                # the deterministic extractor's rank-4 fact: no model call, written beside the chunk
                self.distilled.add_fact(user, t.text, USER_STATED, (cid,))
            if d.distill:
                self._pending.append(Pending(cid, user, t.text, d.authority_class))
                if self.controls.sync_distill:            # NEGATIVE CONTROL: a model call on the write path
                    self._drain(user, [])
        self.verbatim.writes_refused += rep.refused
        self._refresh_cache(user)
        return rep

    # ── the background distiller ──────────────────────────────────────────
    def _drain(self, user: str, proposes: "list[str]") -> "dict[str, int]":
        out = {"distilled": 0, "skipped_forgotten": 0, "proposals_dropped": 0, "proposals_unanchored": 0,
               "model_calls": 0}
        mine = [p for p in self._pending if p.user == user]
        self._pending = [p for p in self._pending if p.user != user]
        ready = []
        for p in mine:
            if self.controls.distiller_skip and self.ledger.matches(user, p.text):
                out["skipped_forgotten"] += 1
                continue
            ready.append(p)
        keep: "list[str]" = []
        for prop in proposes:
            if self.controls.distiller_skip and self.ledger.matches(user, prop):
                out["proposals_dropped"] += 1
            else:
                keep.append(prop)
        if keep and not ready:         # a model output with no verbatim chunk behind it has no provenance: not applied
            out["proposals_unanchored"] += len(keep)
            keep = []
        if ready:
            r = self.distilled.distil(user, [(p.chunk_id, p.text) for p in ready], keep)
            out["distilled"] += int(r.get("facts", 0))
            out["model_calls"] += int(r.get("model_calls", 0))
            self.model_calls += int(r.get("model_calls", 0))
        return out

    def run_idle_pass(self, transcript: str, proposes: "list[str]") -> "dict[str, Any]":
        """The background distiller: drain the pending verbatim chunks into the distilled tier (the scripted model
        output is ``proposes``), skipping what the ledger matches, then refresh the voice-lane packet cache."""
        out = self._drain(self._user, list(proposes))
        self._refresh_cache(self._user)
        return out

    # ── forgetting across BOTH tiers ──────────────────────────────────────
    def forget(self, entity: str) -> str:
        user = self._user
        self.ledger.add(user, entity)                    # first: from this instant no writer can re-add it
        n_v, n_d, n_src, n_q = 0, 0, 0, 0
        if self.controls.forget_verbatim:
            pat = _name_pattern(entity)
            named = {h["id"] for h in self.verbatim.store.get_all(user) if pat.search(h["text"])}   # route 1: the name
            swept = set(self.verbatim.sweep_ledger(user))                      # route 2: the ledger's hashes (deletes)
            n_v = self.verbatim.forget_ids(sorted(named - swept)) + len(swept)
            if self.controls.cascade_provenance:
                gone = named | swept
                siblings = set(self.distilled.siblings(user, sorted(gone))) - gone
                n_src = self.distilled.delete_sources(user, sorted(gone))
                if self.controls.requeue_siblings and siblings:
                    # a Hindsight document is a BUNDLE of chunks: deleting it also deleted the facts of its innocent
                    # chunks; put those chunks back in the distiller's queue so their facts are rebuilt
                    live = {h["id"]: h for h in self.verbatim.store.get_all(user)}
                    for sid in sorted(siblings):
                        if sid in live and live[sid]["room"] in DEFAULT_ROOMS:
                            self._pending.append(Pending(sid, user, live[sid]["text"],
                                                         str(live[sid]["meta"].get("authority_class", ""))))
        n_d = self.distilled.forget(user, entity)
        if self.controls.forget_verbatim and self.controls.physical_erase:
            self.verbatim.store.erase_physical()
        if self.controls.distiller_skip:
            before = len(self._pending)
            self._pending = [p for p in self._pending if not self.ledger.matches(user, p.text)]
            n_q = before - len(self._pending)
        self._refresh_cache(user)
        return (f"forgotten: {n_v} verbatim chunk(s), {n_d + n_src} distilled fact(s), {n_q} queued; "
                "the ledger holds only a hash")

    # ── reads ─────────────────────────────────────────────────────────────
    def _lat(self, tier: str) -> float:
        n = self._lat_n.get(tier, 0)
        self._lat_n[tier] = n + 1
        return LatencyModel.sample(getattr(self.latency, tier), n % 50, 50)

    def _fact_row(self, f: Fact) -> "dict[str, Any]":
        return {"id": f.id, "text": f.text, "status": f.status, "authority_class": f.authority_class,
                "origin": "distilled", "contradicts_id": "", "entity_type": "", "memory_type": "distilled",
                "user_id": self._user, "sources": list(f.source_ids), "label": label_for(f.authority_class)}

    def _verbatim_rows(self, user: str, query: str, k: int, exact: bool) -> "list[dict[str, Any]]":
        rooms = None if exact else DEFAULT_ROOMS
        return self.verbatim.search(query, k, rooms=rooms, user=user)

    def _consult(self, name: str, fn, info: PacketInfo, default):
        """One tier lookup with failure isolation (``tier_isolation``): a tier that raises degrades the packet."""
        try:
            return fn()
        except NotImplementedError:
            raise
        except Exception:                                  # noqa: BLE001 - a tier is down: report, do not fail the turn
            if not self.controls.tier_isolation:
                raise
            info.degraded.append(name)
            return default

    def _lookup(self, query: str, k: int, exact: bool, info: PacketInfo) -> "tuple[list[Fact], list[dict]]":
        user = self._user
        facts = self._consult("distilled", lambda: self.distilled.recall(user, query, k), info, [])
        chunks = self._consult("verbatim", lambda: self._verbatim_rows(user, query, k, exact), info, [])
        d_ms = self._lat("distilled")
        v_ms = self._lat("verbatim")
        if exact:       # an explicit "what exactly did I say": the verbatim tier is the answer, asked in parallel
            info.elapsed_ms = max(d_ms, v_ms) + self.latency.merge
        elif self.controls.parallel_lookup:
            info.elapsed_ms = max(d_ms, v_ms) + self.latency.merge
        else:
            info.elapsed_ms = d_ms + v_ms + self.latency.merge
        info.tiers = ["distilled", "verbatim"]
        return facts, chunks

    def _refresh_cache(self, user: str) -> None:
        """Write-behind: the per-user packet the VOICE lane serves from (query-independent, newest user-stated first)."""
        if user != self._user:
            return
        rows: "list[dict[str, Any]]" = []
        try:
            for f in self.distilled.rows(user):
                if f.status == "approved":
                    rows.append(self._fact_row(f))
            for h in self.verbatim.store.get_all(user):
                if h["room"] in DEFAULT_ROOMS:
                    rows.append(self._verb_packet_row(self.verbatim._row(h)))
        except NotImplementedError:
            return
        except Exception:                                  # noqa: BLE001
            return
        self._cache = rows

    def _verb_packet_row(self, r: "dict[str, Any]") -> "dict[str, Any]":
        cls = r["authority_class"]
        date = f"day {int((r['filed_ts'] - 1_790_000_000.0) // 86400)}"
        return {**r, "raw": r["text"], "label": label_for(cls),
                "text": frame(r["text"], authority_class=cls, speaker_label=SPEAKER_LABEL.get(cls, "unknown"),
                              date=date, enabled=self.controls.frame)}

    def packet(self, query: str, k: int = 10, *, lane: str = "chat", exact: bool = False) -> "list[dict[str, Any]]":
        """The recall packet for ``query``. ``lane``: ``voice`` (served from the write-behind cache when the voice
        policy is on), ``chat`` (both tiers, in parallel), ``exact`` / ``exact=True`` (an explicit request for the
        user's own words: verbatim first, quarantine rooms included, every line framed)."""
        info = PacketInfo(lane="exact" if exact else lane)
        self.last = info
        if lane == "voice" and not exact and self.controls.voice_policy:
            q = _toks(query)
            hits = [r for r in self._cache if q & _toks(r.get("raw") or r["text"])]
            info.cache_miss = not self._cache
            info.elapsed_ms = self._lat("cache_read")
            info.tiers = ["cache"]
            return self._finish(hits, k, info, exact=False)
        facts, chunks = self._lookup(query, k, exact, info)
        d_rows = [self._fact_row(f) for f in facts]
        v_rows = [self._verb_packet_row(r) for r in chunks]
        return self._finish(self._merge(d_rows, v_rows, exact, info), k, info, exact=exact)

    def _merge(self, d_rows, v_rows, exact: bool, info: PacketInfo) -> "list[dict[str, Any]]":
        covered = {s for r in d_rows for s in r.get("sources", [])}
        if not exact:      # dedupe: a verbatim chunk already covered by a retrieved distilled fact is not repeated
            v_rows = [r for r in v_rows if r["id"] not in covered]
        rows = (v_rows + d_rows) if exact else (d_rows + v_rows)      # the owner's order: distilled first
        return self._authority(rows, info)

    def _authority(self, rows: "list[dict[str, Any]]", info: PacketInfo) -> "list[dict[str, Any]]":
        """Two statements about the same attribute: the higher authority class wins; equal class, the newer wins.
        ``authority`` OFF = tier order wins (distilled first), the owner's proposed order without the rule."""
        if not self.controls.authority:
            return rows
        best: "dict[str, dict[str, Any]]" = {}
        for r in rows:
            a = attr_of(r.get("raw") or r["text"])
            if not a:
                continue
            key = a[0]
            rank = RANK.get(r["authority_class"], 0)
            when = float(r.get("filed_ts") or 0.0)
            cur = best.get(key)
            if cur is None or (rank, when) > (cur["_rank"], cur["_when"]):
                best[key] = {"_rank": rank, "_when": when, "row": r}
        out = []
        for r in rows:
            a = attr_of(r.get("raw") or r["text"])
            if a and best[a[0]]["row"] is not r and attr_of(best[a[0]]["row"].get("raw") or best[a[0]]["row"]["text"]) != a:
                info.suppressed.append(r["id"])
                continue
            out.append(r)
        return out

    def _finish(self, rows: "list[dict[str, Any]]", k: int, info: PacketInfo, *, exact: bool) -> "list[dict[str, Any]]":
        if not exact:
            v_seen = 0
            kept = []
            for r in rows:
                if r.get("origin", "").startswith("verbatim"):
                    v_seen += 1
                    if v_seen > self.VERBATIM_QUOTA:
                        continue
                kept.append(r)
            rows = kept
        return rows[: min(k, self.PACKET_MAX)]

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        return self.packet(query, k)

    def recall_timed(self, query: str, k: int = 10, *, lane: str = "chat",
                     exact: bool = False) -> "tuple[list[dict[str, Any]], float]":
        rows = self.packet(query, k, lane=lane, exact=exact)
        return rows, self.last.elapsed_ms

    def answer(self, query: str, k: int = 5) -> str:
        """The scripted reader over the packet: it repeats what the packet's FIRST line says (so an injected
        instruction that reaches the packet is obeyed - the canary instrument check)."""
        rows = self.packet(query, k)
        return rows[0]["text"] if rows else "I don't have that saved."

    def set_account_name(self, name: str) -> None:
        """The display name of the ACCOUNT the arm was reset for (identity comes from the account, never from text)."""
        self._names[self._user] = name

    def identity_line(self) -> str:
        return f"The person speaking is {self._names.get(self._user, 'a household member')}."

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        return self.verbatim.as_of(query, ts)

    # ── the store export (both tiers) ─────────────────────────────────────
    def stats(self) -> "dict[str, Any]":
        return self._stats(self._user, self.verbatim.stats())

    def _stats(self, user: str, v: "dict[str, Any]") -> "dict[str, Any]":
        rows = list(v["rows"])
        for f in self.distilled.rows(user):
            row = {k: self._fact_row(f).get(k, "") for k in ROW_KEYS}
            row.update(user_id=user, sources=list(f.source_ids))
            rows.append(row)
        counts: "dict[str, int]" = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts, "writes_refused": self.verbatim.writes_refused,
                "row_keys": list(ROW_KEYS), "model_calls": self.model_calls,
                "pending": len([p for p in self._pending if p.user == user]),
                "tiers": {"verbatim": len(v["rows"]), "distilled": len(rows) - len(v["rows"])}}

    def stats_as(self, identity: str) -> "dict[str, Any]":
        return self._stats(self.verbatim._identity_user(identity), self.verbatim.stats_as(identity))

    def latency_percentiles(self, queries: "list[str]", *, lane: str, exact: bool = False) -> "dict[str, float]":
        ms = [self.recall_timed(q, lane=lane, exact=exact)[1] for q in queries]
        return {"p50": percentile(ms, 0.5), "p95": percentile(ms, 0.95), "n": len(ms)}

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

* ``HindsightDistilledTier``  - the REAL one, over ``arms.hindsight.HindsightClient`` (HTTP only): one bank per user, a distilled
  BUNDLE = one Hindsight document whose metadata names its verbatim chunk ids (so a deleted chunk cascades), the deterministic
  owner_taught fact written by a no-model ``chunks`` strategy, authority-gated like the double. With no server it raises
  ``HindsightUnavailable`` (a ``NotImplementedError``: the cell SKIPs, never passes). Hindsight 0.10.2 needs the scratch Postgres, the
  loopback embedding shim and a brain (``bakeoff_window.sh``).
* ``FakeDistilledTier``       - a TEST DOUBLE with the same contract (rule-based "model", authority-gated store,
  provenance by source id). It makes NO claim about Hindsight's quality; it exists so the glue (gate, ledger, cascade,
  authority, framing, lanes) is proven red-without / green-with in the slim CI lane.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .base import Arm, IngestReport, ROW_KEYS, Turn
import time
from concurrent.futures import ThreadPoolExecutor

from .hindsight import INSTALL_HINT as HINDSIGHT_HINT
from .hindsight import HindsightClient, HindsightError
from .hm_policy import (DEFAULT_ROOMS, MODEL_FROM_TRANSCRIPT, RANK, SPEAKER_LABEL, USER_STATED, USER_STATED_DERIVED,
                        Controls, HashedLedger, LatencyModel, alias_candidates, frame, label_for, percentile)
from .mempalace_verbatim import MemPalaceVerbatimArm, _name_pattern, _toks, days_ago

# ── attribute keys: how the lab decides two statements are about the same thing ───────────────────────────────

_ATTR_PATTERNS = (
    ("home", re.compile(r"\b(?:live|lives|living|moved|moving|reside)s?\b[^.]*?\b(?:in|to)\s+([A-Z][\w-]+)")),
    ("name", re.compile(r"\b(?:my name is|i'm|i am|name is|goes by)\s+([A-Z][\w-]+)")),
    ("work", re.compile(r"\b(?:work|works|working)\s+(?:at|for)\s+(.+?)\s*[.,;!]?\s*$")),
    ("dentist", re.compile(r"\bdentist\b[^.]*?\b(?:is|was)\s+(?:Dr\.?\s+)?([A-Z][\w-]+)")),
    ("age", re.compile(r"\b(\d{1,3})\s+years?\s+old\b")),
    ("birthday", re.compile(r"\bbirthday\s+is\s+(?:on\s+)?(.+?)\s*[.!]?\s*$")),
    ("spouse", re.compile(r"\b(?:wife|husband|spouse|partner)\s+(?:is\s+)?(?:named|called)\s+([A-Z][\w-]+)")),
    ("pet", re.compile(r"\b(?:dog|cat|pet)\s+(?:is\s+)?(?:named|called)\s+([A-Z][\w-]+)")),
)


def _subject(head: str) -> str:
    """Whose attribute it is: the last capitalised word before the attribute phrase ('User's friend Priya lives in ...' -> priya), else the user. First contact
    (2026-10-06): without a subject, 'friend A lives in X' and 'friend B lives in Y' were one attribute, and the packet's authority rule dropped 6 of the 20 needles."""
    caps = [w for w in re.findall(r"[A-Z][\w-]+", head) if w.lower() not in ("user", "i")]
    return caps[-1].lower() if caps else "user"


def attr_of(text: str) -> "tuple[str, str] | None":
    """(attribute, value) for the attributes the cells use, else None. The attribute is ``home`` for the user's own and ``home:priya`` for somebody else's."""
    for attr, rx in _ATTR_PATTERNS:
        m = rx.search(text or "")
        if m:
            who = _subject((text or "")[:m.start()])
            return (attr if who == "user" else f"{attr}:{who}"), m.group(1).strip().lower()
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
    """The real distilled tier over Hindsight's HTTP API (``arms.hindsight.HindsightClient``): concise extraction, observations off, no reranker.

    * one BANK per user (``zmb-hm-<user>``); a distilled bundle = one DOCUMENT whose item metadata ``source_ids`` names the verbatim chunk ids it was
      distilled from (and one ``src:<id>`` tag per chunk), so deleting a chunk's documents is a provenance cascade (``delete_sources`` / ``siblings``);
    * a deterministic fact (the owner's ``owner_taught`` sentence, a scripted proposal) is retained with the bank's ``det`` strategy (``chunks``
      extraction: zero model calls) and is authority-gated here, in the tier, exactly as the double does: a lower-ranked writer cannot retire a
      higher-ranked fact about the same attribute, the held-back proposal is kept as ``disputed`` (a side row, never in Hindsight, never recalled);
    * ``forget`` deletes the documents whose units name the entity, remembers the verbatim chunks they were built from (``take_orphaned_sources``: the
      caller re-queues the innocent ones) and, given the scratch Postgres handle, scrubs the log rows and compacts the relations."""
    name = "hindsight"
    CONFIG = {"retain_extraction_mode": "concise", "enable_observations": False, "enable_reranking": False,
              "retain_strategies": {"det": {"retain_extraction_mode": "chunks"}}}

    def __init__(self, variant: str = "H2", *, client: "HindsightClient | None" = None, base_url: "str | None" = None, transport=None,
                 pg: Any = None, keep_banks: bool = False):
        self.variant = variant
        self.client = client or HindsightClient(base_url, transport)
        self.pg = pg
        self.keep_banks = keep_banks
        self.fail = False                       # a cell flips this to simulate the tier being down (the real tier is never "down" by itself)
        self.enforce_authority = True
        self.model_calls = 0
        self._banks: "set[str]" = set()
        self._side: "dict[str, list[Fact]]" = {}
        self._orphans: "dict[str, list[str]]" = {}
        self._seq = 0

    # ── plumbing ──
    def bank_for(self, user: str) -> str:
        return f"zmb-hm-{user}".lower()

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("distilled tier unavailable")

    def _bank(self, user: str) -> str:
        bank = self.bank_for(user)
        if bank not in self._banks:
            self.client.delete_bank(bank)
            self.client.put_bank(bank)
            self.client.patch_config(bank, self.CONFIG)
            self._banks.add(bank)
        return bank

    @staticmethod
    def _class_of(tags: "list[str]") -> str:
        return next((t[6:] for t in tags if t.startswith("class:")), "")

    def _fact(self, u: dict) -> Fact:
        meta = u.get("metadata") or {}
        src = tuple(x for x in str(meta.get("source_ids") or "").split(",") if x)
        a = attr_of(u.get("text") or "")
        self._seq += 1
        return Fact(id=str(u.get("id") or ""), text=str(u.get("text") or ""), authority_class=self._class_of(u.get("tags") or []) or USER_STATED_DERIVED,
                    source_ids=src, attr=a[0] if a else "", seq=self._seq)

    def _units(self, user: str) -> "list[dict]":
        """The user's FACT units (an observation is a derived row of an arm with observations ON; this tier has them off)."""
        if self.bank_for(user) not in self._banks:
            return []
        return [u for u in self.client.list_units(self.bank_for(user)) if u.get("fact_type") != "observation" and u.get("state", "valid") == "valid"]

    def _doc_id(self, kind: str) -> str:
        self._seq += 1
        return f"{kind}-{self._seq:05d}"

    def _retain(self, user: str, text: str, cls: str, source_ids: "tuple[str, ...]", *, det: bool) -> str:
        doc = self._doc_id("f" if det else "b")
        item: "dict[str, Any]" = {"content": text, "document_id": doc, "context": "the user is speaking",
                                  "tags": [f"user:{user}", f"class:{cls}"] + [f"src:{s}" for s in source_ids],
                                  "metadata": {"source_ids": ",".join(source_ids), "authority_class": cls}}
        if det:
            item["strategy"] = "det"
        self.client.retain(self._bank(user), [item])
        return doc

    # ── the DistilledTier contract ──
    def reset(self, user_id: str) -> None:
        for b in list(self._banks):
            if not self.keep_banks:
                self.client.delete_bank(b)
            self._banks.discard(b)
        self._side, self._orphans, self.model_calls, self.fail = {}, {}, 0, False
        self._bank(user_id)

    def add_fact(self, user: str, text: str, authority_class: str, source_ids: "tuple[str, ...]" = ()) -> str:
        self._check()
        a = attr_of(text)
        held = False
        if a:
            for u in self._units(user):
                old = self._fact(u)
                if old.attr == a[0] and attr_of(old.text) != a:
                    if not self.enforce_authority or RANK[authority_class] >= RANK[old.authority_class]:
                        doc = u.get("document_id")
                        if doc:
                            self.client.delete_document(self.bank_for(user), str(doc))        # newest evidence wins: the S1 signature when authority is OFF
                        old.status = "superseded"
                        self._side.setdefault(user, []).append(old)
                    else:
                        held = True
        if held:
            self._seq += 1
            f = Fact(id=f"held-{self._seq:05d}", text=text, authority_class=authority_class, source_ids=tuple(source_ids), status="disputed",
                     attr=a[0] if a else "", seq=self._seq)
            self._side.setdefault(user, []).append(f)
            return f.id
        return self._retain(user, text, authority_class, tuple(source_ids), det=True)

    def distil(self, user: str, chunks: "list[tuple[str, str]]", proposes: "list[str]") -> "dict[str, int]":
        """The background model pass: ONE concise retain of the bundle (one model call, the document's provenance = its chunk ids); the scripted
        proposals of a cell then go through ``add_fact`` (authority-gated, no model call)."""
        self._check()
        src = tuple(i for i, _t in chunks)
        added = 0
        calls = 0
        if chunks:
            doc = self._retain(user, "\n".join(t for _i, t in chunks), USER_STATED_DERIVED, src, det=False)
            self.model_calls += 1
            calls = 1
            added += sum(1 for u in self._units(user) if u.get("document_id") == doc)
        for p in proposes:
            self.add_fact(user, p, USER_STATED_DERIVED, src)
            added += 1
        return {"facts": added, "model_calls": calls}

    def recall(self, user: str, query: str, k: int) -> "list[Fact]":
        self._check()
        if self.bank_for(user) not in self._banks:
            return []
        res = self.client.recall(self.bank_for(user), query, tags=[f"user:{user}"])
        out = [self._fact({**r, "state": "valid"}) for r in res if r.get("type") != "observation"]
        return out[:k]

    def forget(self, user: str, entity: str) -> int:
        """The text route: delete every document with a unit naming the entity; remember the chunks those documents came from."""
        self._check()
        pat = _name_pattern(entity)
        bank = self.bank_for(user)
        gone_docs: "dict[str, list[str]]" = {}
        if bank in self._banks:
            for u in self.client.list_units(bank):
                if u.get("document_id") and (pat.search(str(u.get("text") or "")) or pat.search(" ".join(str(v) for v in (u.get("metadata") or {}).values()))):
                    gone_docs.setdefault(str(u["document_id"]), []).extend(x for x in str((u.get("metadata") or {}).get("source_ids") or "").split(",") if x)
            for d in gone_docs:
                self.client.delete_document(bank, d)
        side = self._side.get(user, [])
        self._side[user] = [f for f in side if not pat.search(f.text)]
        self._orphans.setdefault(user, []).extend(s for ids in gone_docs.values() for s in ids)
        return len(gone_docs) + (len(side) - len(self._side[user]))

    def scrub(self, user: str, entity: str) -> None:
        """Physical erase of Hindsight's own Postgres (the engine's delete leaves the text in its log tables, dead tuples, statistics and WAL): the same
        scrub the H arms run (``arms.pg_store``). A no-op without the scratch Postgres handle."""
        bank = self.bank_for(user)
        if self.pg is not None and bank in self._banks:
            self.pg.erase_text(bank, entity)
            self.pg.compact()

    def take_orphaned_sources(self, user: str) -> "list[str]":
        """The verbatim chunk ids behind the documents ``forget`` just deleted (a bundle also held innocent chunks: the caller re-queues them)."""
        out, self._orphans[user] = sorted(set(self._orphans.get(user, []))), []
        return out

    def siblings(self, user: str, source_ids: "list[str]") -> "list[str]":
        gone, out = set(source_ids), set()
        for u in self._units(user):
            src = {x for x in str((u.get("metadata") or {}).get("source_ids") or "").split(",") if x}
            if gone & src:
                out |= src
        return sorted(out)

    def delete_sources(self, user: str, source_ids: "list[str]") -> int:
        """The provenance route: delete every document derived from a deleted chunk (its text need not name anyone)."""
        self._check()
        gone, bank, n, docs = set(source_ids), self.bank_for(user), 0, set()
        for u in (self.client.list_units(bank) if bank in self._banks else []):
            src = {x for x in str((u.get("metadata") or {}).get("source_ids") or "").split(",") if x}
            if gone & src and u.get("document_id"):
                docs.add(str(u["document_id"]))
        for d in sorted(docs):
            n += self.client.delete_document(bank, d)
        self._side[user] = [f for f in self._side.get(user, []) if not (gone & set(f.source_ids))]
        return n

    def rows(self, user: str) -> "list[Fact]":
        return [self._fact(u) for u in self._units(user)] + list(self._side.get(user, []))

    def close(self) -> None:
        if not self.keep_banks:
            for b in list(self._banks):
                try:
                    self.client.delete_bank(b)
                except (HindsightError, NotImplementedError):
                    pass
        self._banks.clear()


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
    capabilities = frozenset({"clock", "identities", "idle_pass", "verbatim", "reader",
                              # the capability axes: the verbatim tier is the exact-words channel; the packet is the associative one; the reader the protocol's
                              "exact_words", "multi_hop", "protocol"})

    #: bullets the packet may carry and the share the verbatim tier may take of them on an ordinary turn
    PACKET_MAX = 12
    VERBATIM_QUOTA = 4

    def __init__(self, distilled: "DistilledTier | None" = None, verbatim: "MemPalaceVerbatimArm | None" = None,
                 controls: "Controls | None" = None, latency: "LatencyModel | None" = None,
                 ledger: "HashedLedger | None" = None, real_latency: bool = False):
        #: ``real_latency``: the two lookups run in two threads and the packet's ``elapsed_ms`` is the WALL CLOCK of the real tiers (the bake-off
        #: window); off, the cells draw from ``LatencyModel`` (CI: a cell's p95 is a property of the model, not of the machine)
        self.real_latency = real_latency
        self.real_ms: "dict[str, list[float]]" = {"distilled": [], "verbatim": [], "both": [], "cache": []}
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
        self._cache_idx: "dict[str, list[int]]" = {}
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
        self._cache_idx = {}
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

    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":
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
        take = getattr(self.distilled, "take_orphaned_sources", None)
        if take is not None and self.controls.cascade_provenance and self.controls.requeue_siblings:
            live = {h["id"]: h for h in self.verbatim.store.get_all(user)}
            for sid in take(user):                    # the documents the text route deleted held other chunks too: those chunks are distilled again
                if sid in live and live[sid]["room"] in DEFAULT_ROOMS and not self.ledger.matches(user, live[sid]["text"]):
                    self._pending.append(Pending(sid, user, live[sid]["text"], str(live[sid]["meta"].get("authority_class", ""))))
        if self.controls.forget_verbatim and self.controls.physical_erase:
            self.verbatim.store.erase_physical()
        if self.controls.physical_erase and hasattr(self.distilled, "scrub"):
            self.distilled.scrub(user, entity)
        if self.controls.distiller_skip:
            before = len(self._pending)
            self._pending = [p for p in self._pending if not self.ledger.matches(user, p.text)]
            n_q = before - len(self._pending)
        self._refresh_cache(user)
        return (f"forgotten: {n_v} verbatim chunk(s), {n_d + n_src} distilled fact(s), {n_q} queued; "
                "the ledger holds only a hash")

    def alias_candidates(self, entity: str) -> "list[str]":
        """The forget-alias sweep: the misspellings / split spellings of ``entity`` the rows still hold. PROPOSALS only (see ``forget_alias``)."""
        if not self.controls.alias_sweep:
            return []
        return alias_candidates(entity, [r.get("text", "") for r in self.stats()["rows"]])

    def forget_alias(self, alias: str) -> str:
        """The owner's YES to "did you also mean <alias>?": the SAME permanent path as the original forget."""
        return self.forget(alias)

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
        if self.real_latency:
            return self._lookup_real(user, query, k, exact, info)
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

    def _lookup_real(self, user: str, query: str, k: int, exact: bool, info: PacketInfo) -> "tuple[list[Fact], list[dict]]":
        """The two lookups against the REAL tiers: concurrent (the design) when ``parallel_lookup`` is on, one after the other when it is off; every
        number is a wall clock."""
        def timed(fn):
            t0 = time.perf_counter()
            r = fn()
            return r, (time.perf_counter() - t0) * 1000.0
        d_fn = lambda: timed(lambda: self._consult("distilled", lambda: self.distilled.recall(user, query, k), info, []))      # noqa: E731
        v_fn = lambda: timed(lambda: self._consult("verbatim", lambda: self._verbatim_rows(user, query, k, exact), info, []))  # noqa: E731
        t0 = time.perf_counter()
        if self.controls.parallel_lookup or exact:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fd, fv = pool.submit(d_fn), pool.submit(v_fn)
                (facts, d_ms), (chunks, v_ms) = fd.result(), fv.result()
        else:
            (facts, d_ms), (chunks, v_ms) = d_fn(), v_fn()
        merge0 = time.perf_counter()
        info.elapsed_ms = (merge0 - t0) * 1000.0 + self.latency.merge
        self.real_ms["distilled"].append(d_ms)
        self.real_ms["verbatim"].append(v_ms)
        self.real_ms["both"].append(info.elapsed_ms)
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
        self._cache_idx = self._index_cache(rows)

    @staticmethod
    def _index_cache(rows: "list[dict[str, Any]]") -> "dict[str, list[int]]":
        """token -> positions (ascending) of the cached rows that contain it. Built ONCE per refresh, off the voice path (RAM lab 2026-10-06: tokenising every cached row on
        every voice turn cost 1.6 ms at 220 rows and 35 ms at 4,020 rows, the volume of one quarter; the lookup is now proportional to the rows that match)."""
        idx: "dict[str, list[int]]" = {}
        for i, r in enumerate(rows):
            for t in _toks(r.get("raw") or r["text"]):
                idx.setdefault(t, []).append(i)
        return idx

    def _cache_hits(self, query: str) -> "list[dict[str, Any]]":
        """The cached rows sharing a content token with ``query``, in cache order (the same answer as scanning every row, without scanning every row)."""
        pos: "set[int]" = set()
        for t in _toks(query):
            pos.update(self._cache_idx.get(t, ()))
        return [self._cache[i] for i in sorted(pos)]

    def _verb_packet_row(self, r: "dict[str, Any]") -> "dict[str, Any]":
        cls = r["authority_class"]
        date = f"{days_ago(r['filed_ts'])} days ago"
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
            info.cache_miss = not self._cache
            if self.real_latency:
                t0 = time.perf_counter()
                hits = self._cache_hits(query)
                info.elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.real_ms["cache"].append(info.elapsed_ms)
            else:
                hits = self._cache_hits(query)
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

    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":
        """(j) "What exactly did I say": the EXACT lane of the packet (the verbatim tier first, quarantine rooms included, every chunk the owner's words
        unchanged) with the day each was filed. ``exact_lookup`` OFF = the request is served from the distilled facts alone (the negative control)."""
        rows = self.packet(query, max(k, 1), exact=True)
        if not self.controls.exact_lookup:
            rows = [r for r in rows if not str(r.get("origin", "")).startswith("verbatim")]
        return [{"text": r.get("raw") or r["text"],
                 "day_offset": days_ago(r["filed_ts"]) if r.get("filed_ts") else None} for r in rows[:k]]

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":
        """(l) HM's packet: the distilled tier (Hindsight's retrieval, link graph included) and the verbatim tier, merged and de-duplicated."""
        return [{**r, "text": r.get("raw") or r["text"]} for r in self.packet(query, k)]

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:
        """(m, lab half) The scripted reader over the HM packet when recall fired."""
        from .. import life as lifemod
        if not fired:
            return lifemod.DECLINE
        return lifemod.anchored_reader([{**r, "text": r.get("raw") or r["text"]} for r in self.packet(prompt, k)], anchor)

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

"""The arm adapter interface: the ONLY surface the bench cells touch.

A cell never imports ``MemoryService`` (or Hindsight, or Graphiti). It speaks ``Turn`` in and reads
``rows`` / recall hits out, so the same scenario spec and the same scorers measure every arm of the
bake-off (docs/knowledge/zoe-memory-bench.md, "Arms"): Z0 (the current MemoryService), Z0-off (the
negative control), H0/H1/H2 (Hindsight) and G (Graphiti). Filling in an arm never touches a scorer.

The five calls a bake-off runner needs - ``ingest(turns)``, ``recall(query, k)``, ``forget(entity)``,
``as_of(query, ts)``, ``stats()`` - plus ``reset()`` / ``close()`` for the lifecycle and an optional
``advance_clock(seconds)`` capability (lab arms only: the tombstone TTL cell needs a fake clock; an
arm without it makes that cell SKIP with a reason, never pass).

A **row** is a plain dict, the arm-agnostic shape every scorer reads (``ROW_KEYS``). ``stats()`` carries
the whole store export under ``"rows"``: the store is read through this export, never through a
vector index (a plumbing probe can pass while recall is silently wrong).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: statuses a row can carry in the arm-agnostic export
STATUSES = ("approved", "pending", "disputed", "archived", "superseded", "rejected")
#: the keys every exported row has (a missing value is "")
ROW_KEYS = ("id", "text", "status", "authority_class", "origin", "contradicts_id", "entity_type",
            "memory_type", "user_id")

#: keys a row MAY carry beyond ROW_KEYS: provenance (``source_excerpt`` - the user's own words the fact came from,
#: ``user_turn_id`` - which turn) and the validity interval (epoch seconds; ``invalid_at`` is set when a newer fact
#: replaced this one, never by deleting it) plus the supersede links. An arm that does not export one shows "" - and
#: a provenance / history cell then FAILS, which is the honest reading of "the store cannot say".
OPTIONAL_ROW_KEYS = ("source_excerpt", "user_turn_id", "valid_from", "invalid_at", "supersedes_id", "superseded_by_id",
                     "retire_quote", "retired_by", "quote_elsewhere")

#: speakers a Turn may carry (docs: the ZMB design section 3.0)
SPEAKERS = ("owner_typed", "owner_taught", "owner_voice_verified", "panel_unverified", "third_party",
            "pasted_email", "assistant", "system_writer")
#: what a Turn does to the store
OPS = ("say", "edit", "archive")


@dataclass(frozen=True)
class Turn:
    """One event of a scenario, in the arm-agnostic vocabulary.

    ``speaker``: ``owner_typed`` (the owner types a sentence; the arm's own extractor reads it),
    ``owner_taught`` (an explicit "remember that ..." teach: ``text`` is the fact sentence),
    ``owner_voice_verified`` / ``panel_unverified`` (a voice turn the speaker-id did / did not confirm),
    ``third_party`` (a speech-to-text fragment of someone else), ``pasted_email`` (text the owner pasted),
    ``assistant`` (Zoe's own words: never a user fact), ``system_writer`` (a model pass - ``writer`` names
    it: ``digest``, ``turn_digest``, ``idle_consolidation``, ``brain_tool``, ``mcp``, ``decay_sweep`` ...).

    ``proposes`` is the SCRIPTED model output of a ``system_writer``: the facts the pass would extract from
    ``text`` (its transcript). Z0's model writers need it because the lab has no brain; an arm that runs its
    own extraction model ignores it. ``op`` = ``edit`` / ``archive`` makes the writer try to replace / retire
    the owner's existing row about ``attr`` with each proposal instead of just asserting it.
    ``assistant_text`` is Zoe's reply in the same exchange (never mined into a fact).
    """
    text: str
    speaker: str = "owner_typed"
    day_offset: int = 0
    writer: str = ""
    proposes: tuple[str, ...] = ()
    op: str = "say"
    attr: str = ""
    assistant_text: str = ""
    memory_type: str = ""          # "emotional_moment" for an affective record; "" = an ordinary fact

    def __post_init__(self) -> None:
        if self.speaker not in SPEAKERS:
            raise ValueError(f"unknown speaker {self.speaker!r} (known: {', '.join(SPEAKERS)})")
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op!r} (known: {', '.join(OPS)})")
        if self.speaker == "system_writer" and not self.writer:
            raise ValueError("a system_writer turn names its writer")


@dataclass
class IngestReport:
    """What the arm did with a batch of turns (counts only - never text)."""
    turns: int = 0
    written: int = 0          # rows stored (any status)
    refused: int = 0          # writes the arm declined (a wall, a gate, a tombstone)
    retired: int = 0          # existing rows an edit / archive actually replaced or retired
    notes: list[str] = field(default_factory=list)


class Arm(ABC):
    """One memory system under test."""

    name: str = "arm"
    #: optional abilities a cell may require: ``clock`` (advance_clock), ``controls`` (a lab arm that
    #: can switch its features off)
    capabilities: frozenset[str] = frozenset()
    #: whose model writes the nightly observations: ``"scripted"`` = the LAB scripts it (Z0's digest: the cell hands it the night's proposals and
    #: measures what the store KEEPS of them); ``"own"`` = the arm runs its own model (Hindsight's observation layer): the lab hands it NOTHING to
    #: believe - ``reflect_pass`` only lets it consolidate what the life already ingested. Must agree with ``observations()["model"]``.
    nightly_model: str = "scripted"

    @abstractmethod
    def reset(self, user_id: str) -> None:
        """Start a fresh, empty store for ``user_id`` (a ``demo_bar_<8 hex>``-shaped synthetic id)."""

    @abstractmethod
    def ingest(self, turns: "list[Turn]") -> IngestReport:
        """Apply the turns in order, the way the arm's own pipeline would."""

    @abstractmethod
    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        """The top-``k`` rows the arm would put in the answer packet for ``query`` (rows, ``ROW_KEYS``)."""

    @abstractmethod
    def forget(self, entity: str) -> str:
        """\"Forget everything about ``entity``\". Returns the arm's own confirmation string."""

    @abstractmethod
    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        """Rows as the store believed them at ISO timestamp ``ts``. ``NotImplementedError`` when the arm
        has no as-of read (Z0 has one since the two timelines, audit P2.1: ``MemoryService.search(as_of=...)``)."""

    @abstractmethod
    def stats(self) -> "dict[str, Any]":
        """``{"rows": [row, ...], "counts": {status: n}, "writes_refused": n, ...}`` - the store export."""

    def answer(self, query: str, k: int = 5) -> str:  # pragma: no cover
        """Optional capability ``reader``: a SCRIPTED reader over the recall packet (brain-free). An
        instrument check only - the real reply is the brain tier's."""
        raise NotImplementedError(f"{self.name} has no scripted reader")

    def run_idle_pass(self, transcript: str, proposes: "list[str]", *, judge: bool = True) -> "dict[str, Any]":  # pragma: no cover
        """Optional capability ``idle_pass``: run the arm's own nightly pass (Z0: the REAL
        ``memory_digest.run_memory_digest`` incl. its contradiction check) over a day ``transcript`` of the
        user's turns, with the model's extraction scripted to ``proposes``. Returns the pass's counters."""
        raise NotImplementedError(f"{self.name} has no idle pass")

    def reflect_pass(self) -> "dict[str, Any]":  # pragma: no cover
        """Optional, for an arm with ``nightly_model == "own"``: let the arm's OWN model reflect over what was ingested (consolidate and settle),
        with NOTHING injected - no scripted proposals, no transcript handed over as a writer's claim. Returns the pass's counters."""
        raise NotImplementedError(f"{self.name} has no own-model reflection pass")

    def ingest_as(self, identity: str, turns: "list[Turn]") -> IngestReport:  # pragma: no cover
        """Optional capability ``identities``: apply the turns AS a named household identity
        (``consenting_owner``, ``owner_no_mode``, ``minor``, ``guest``, ``harness``) - the affect-consent cell."""
        raise NotImplementedError(f"{self.name} cannot ingest as another identity")

    def stats_as(self, identity: str) -> "dict[str, Any]":  # pragma: no cover
        raise NotImplementedError(f"{self.name} cannot read another identity's store")

    def run_conflict_pass(self) -> "dict[str, Any]":  # pragma: no cover
        """Optional capability ``conflict_pass``: run the arm's own nightly implicit-conflict pass (Z0: the REAL
        ``memory_digest._implicit_conflict_pass`` - a newer fact that changes an older one retires it, history kept).
        Returns the pass's counters (``{"pairs": n, "superseded": n}``; a pass that is switched off returns zeros)."""
        raise NotImplementedError(f"{self.name} has no conflict pass")

    def write_edge(self, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:  # pragma: no cover
        """Optional capability ``edges``: a writer (``authority`` user_stated / user_confirmed / inferred, labelled
        ``origin``) states that ``a`` and ``b`` are related by ``rel`` in the people graph."""
        raise NotImplementedError(f"{self.name} has no people graph")

    def edges(self) -> "list[dict[str, Any]]":  # pragma: no cover
        """Optional capability ``edges``: every edge, open or closed - ``{"a", "b", "rel_type", "current", "authority",
        "origin"}`` (names, never ids): the people-graph export the A8 cells read."""
        raise NotImplementedError(f"{self.name} has no people graph")

    def quote_retire(self, text: str, *, lane: str = "chat", speaker_verified: "bool | None" = None,
                     brain: "dict[str, Any] | None" = None, mode: str = "enforce") -> "dict[str, Any]":  # pragma: no cover
        """Optional capability ``quote_retire``: one candidate state change through the arm's quote-backed retirement
        (``services/zoe-data/memory_retire.py``): the deterministic half (prefilter, the owner's own words, candidates), the
        SCRIPTED brain's choice (``brain``: ``{"pick_text": row text}`` / ``{"pick": n}`` / ``{"top1": true}`` /
        ``{"row_containing": text}``), then the wall. Returns ``{"action", "reason", "offered": [ids], "chosen": id}``."""
        raise NotImplementedError(f"{self.name} has no quote-backed retirement")

    def cue_gate(self, text: str) -> bool:  # pragma: no cover
        """Optional capability ``quote_retire``: does the arm's quote-retire prefilter open the door to the judge for this sentence?"""
        raise NotImplementedError(f"{self.name} has no quote-backed retirement")

    def advance_clock(self, seconds: float) -> None:  # pragma: no cover - optional capability
        raise NotImplementedError(f"{self.name} has no controllable clock")

    # ── the capability axes (2026-10-07): j exact words, k reflection, l multi-hop, m protocol ──────────────────────────────────
    # An arm DECLARES the capability in ``capabilities``; a cell that needs one the arm lacks SKIPs with the reason (never ERRORs, never passes).
    # The default bodies raise ``NotImplementedError``, which ``cells.run_cell`` also reports as a SKIP.

    def recall_exact(self, query: str, k: int = 5) -> "list[dict[str, Any]]":  # pragma: no cover
        """Optional capability ``exact_words``: the arm's answer to "what EXACTLY did I say about ...": up to ``k`` hits, each
        ``{"text": <the words as stored: a verbatim turn, or whatever the arm kept>, "day_offset": <days ago it was said, or None>}``. The cell scores the
        exact sentence as a substring of ``text``; an arm that keeps only a rewritten fact returns the rewrite and fails honestly."""
        raise NotImplementedError(f"{self.name} has no exact-words lookup")

    def observations(self, query: str = "") -> "dict[str, Any]":  # pragma: no cover
        """Optional capability ``observations``: the arm's DERIVED statements about the household (Hindsight's observation layer, Z0's nightly
        digest rows), optionally ranked for ``query`` (top 5). ``{"items": [{"text", "stated_by": "user"|"inferred"|"", "id"}], "model": "own"|"scripted"}``
        where ``model`` says whether the arm's own model produced them ("scripted": the lab scripted the nightly model, so what the store KEEPS is
        measured and what the model would have SAID is not)."""
        raise NotImplementedError(f"{self.name} has no observation layer")

    # ── the night mind's surface (K6-K12): an arm with ``nightly_model == "own"`` that runs a reflection pass exposes these; the rest SKIP ────────────────────
    def add_night_turns(self, texts: "list[str]", day_offset: int) -> None:  # pragma: no cover
        """Owner turns that reach ONLY the nightly pass (routine commands that no extractor mines): the dense-day cell (K7)."""
        raise NotImplementedError(f"{self.name} has no night pass")

    def threads(self) -> "list[dict[str, Any]]":  # pragma: no cover
        """The pass's threads (``title``, ``status``, ``anchors``, ``raise_policy``, ``leave_reason``): K9 / K10 / K11."""
        raise NotImplementedError(f"{self.name} has no night threads")

    def changes(self) -> "list[dict[str, Any]]":  # pragma: no cover
        """What changed in the last pass: ``[{"type": new|advanced|resolved|quiet, "thread", "ids"}]`` (K9)."""
        raise NotImplementedError(f"{self.name} has no night changes")

    def morning_plan(self, days: int = 14) -> "list[dict[str, Any]]":  # pragma: no cover
        """``days`` simulated mornings against the stored threads, every raise ignored: ``[{"day", "raised": [thread ids]}]`` (K10)."""
        raise NotImplementedError(f"{self.name} has no morning plan")

    def turn_text(self, turn_id: str) -> "str | None":  # pragma: no cover
        """The text of one of this member's owner turns by id, or None (K8's pointer check)."""
        raise NotImplementedError(f"{self.name} cannot look a turn up by id")

    def moment_labels(self, texts: "list[str]") -> "list[dict[str, Any]]":  # pragma: no cover
        """Stage 2 alone over labelled turns: the ``{"quote", "kind", "feeling", "weight"}`` the model gave each turn it picked (K12)."""
        raise NotImplementedError(f"{self.name} has no moment labeller")

    def recall_linked(self, query: str, k: int = 8) -> "list[dict[str, Any]]":  # pragma: no cover
        """Optional capability ``multi_hop``: the arm's ASSOCIATIVE packet for a question that needs two facts said weeks apart (Hindsight's link graph,
        a relational block): up to ``k`` rows. An arm with no associative step returns its ordinary recall (and is measured as that)."""
        raise NotImplementedError(f"{self.name} has no associative recall")

    def protocol_answer(self, prompt: str, anchor: "tuple[str, ...]", fired: bool, k: int = 5) -> str:  # pragma: no cover
        """Optional capability ``protocol``: the reply of the scripted reader to ``prompt`` when the brain DID (``fired``) or did NOT call recall.
        Not fired = no packet = nothing to answer from. The lab half of axis (m): the packet's quality and the instrument, never a brain."""
        raise NotImplementedError(f"{self.name} has no protocol reader")

    def close(self) -> None:
        """Release the arm's resources (idempotent)."""


def row_text_lower(rows: "list[dict[str, Any]]", *, statuses: "tuple[str, ...] | None" = None) -> str:
    """All row texts, lower-cased and newline-joined, optionally restricted to ``statuses``."""
    return "\n".join((r.get("text") or "").lower() for r in rows
                     if statuses is None or r.get("status") in statuses)

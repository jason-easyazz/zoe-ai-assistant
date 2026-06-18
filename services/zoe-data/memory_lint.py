"""Memory Lint - the report-only "Lint" pass over MemPalace memory.

Zoe's memory has long had Ingest (consolidation / "dreaming") and Query
(search) but no Lint. This module adds the third Karpathy-wiki operation: a
scan over a user's stored memories that produces a *structured report* of
suspect rows for human / curation review.

It flags four families of issue:

  * ``contradictions`` - memories that assert conflicting facts about the same
    subject (heuristic: same entity / subject, opposing polarity or mutually
    exclusive value for a "single-valued" relation like location).
  * ``stale``          - expired, very old, or already-superseded claims.
  * ``orphans``        - memories with no references, links, usage, or access.
  * ``duplicates``     - exact and near-duplicate text within a user's store.

HARD CONTRACT - this module is REPORT-ONLY.

  * It NEVER deletes, merges, edits, archives, or otherwise mutates a stored
    memory. It reads rows and returns a report. Acting on the report is a
    separate, human-gated step (review UI, curation tooling).
  * It performs no DB schema migration and no destructive ops.
  * The detectors are pure, deterministic heuristics with no network / LLM /
    cloud dependency, so they stay offline-safe and unit-testable. (Deeper
    LLM-judged contradiction resolution already lives, write-enabled, in the
    dreaming digest; Lint deliberately stays cheap and non-mutating.)

The async entrypoints (:func:`lint_user`, :func:`lint_all`) read through the
existing :class:`MemoryService` facade - no parallel store, no direct Chroma
client. The nightly consolidation can emit a report via :func:`lint_user`
only when explicitly opted in (``ZOE_MEMORY_LINT_IN_DREAMING=1``); default off.
"""

from __future__ import annotations

import datetime
import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Protocol

__all__ = [
    "LintFinding",
    "LintReport",
    "lint_memories",
    "lint_user",
    "lint_all",
    "dreaming_lint_enabled",
    "STALE_AGE_DAYS_DEFAULT",
    "NEAR_DUPLICATE_RATIO_DEFAULT",
]


# Tunables (env-overridable, all read-only knobs - never affect stored data).
STALE_AGE_DAYS_DEFAULT = float(os.environ.get("ZOE_MEMORY_LINT_STALE_DAYS", "365"))
NEAR_DUPLICATE_RATIO_DEFAULT = float(
    os.environ.get("ZOE_MEMORY_LINT_NEAR_DUP_RATIO", "0.92")
)

# Statuses that make a row already-resolved / not "live" memory.
_SUPERSEDED_STATUSES = {"superseded", "archived", "rejected"}

# Negation cues used by the heuristic contradiction detector.
_NEGATION_RE = re.compile(
    r"(?i)\b(?:not|no longer|never|doesn't|does not|isn't|is not|won't|"
    r"will not|can't|cannot|hates?|dislikes?|avoids?|stopped|quit)\b"
)

# Single-valued relations: only one value can be true at a time, so two rows
# asserting different values for the same subject are a likely contradiction.
# Each entry: (compiled pattern with a `subject` and `value` group).
_SINGLE_VALUED_PATTERNS = (
    re.compile(r"(?i)\b(?P<subject>.+?)\s+lives?\s+in\s+(?P<value>[A-Za-z][\w' -]+)"),
    re.compile(r"(?i)\b(?P<subject>.+?)\s+(?:is|are)\s+located\s+in\s+(?P<value>[A-Za-z][\w' -]+)"),
    re.compile(r"(?i)\b(?P<subject>.+?)\s+works?\s+at\s+(?P<value>[A-Za-z][\w' -]+)"),
    re.compile(r"(?i)\b(?P<subject>.+?)\s+(?:is|are)\s+(?P<value>\d+)\s+years?\s+old"),
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Trailing temporal / filler words stripped from a captured single-valued
# relation value so "Sydney" and "Sydney now" normalise to the same value.
_VALUE_TRAILING_RE = re.compile(
    r"(?i)\b(?:now|currently|today|anymore|any more|these days|at the moment)\b.*$"
)


class _MemoryRowLike(Protocol):
    id: str
    text: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LintFinding:
    """A single report entry. Carries IDs only - never proposes a mutation."""

    kind: str  # "contradiction" | "stale" | "orphan" | "duplicate"
    memory_ids: list[str]
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "memory_ids": list(self.memory_ids),
            "reason": self.reason,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class LintReport:
    """Structured, report-only result of a lint pass over one user's memory."""

    user_id: str
    scanned: int
    contradictions: list[LintFinding] = field(default_factory=list)
    stale: list[LintFinding] = field(default_factory=list)
    orphans: list[LintFinding] = field(default_factory=list)
    duplicates: list[LintFinding] = field(default_factory=list)
    generated_at: str = ""

    @property
    def findings(self) -> list[LintFinding]:
        return [*self.contradictions, *self.stale, *self.orphans, *self.duplicates]

    @property
    def total(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "scanned": self.scanned,
            "generated_at": self.generated_at,
            "totals": {
                "contradictions": len(self.contradictions),
                "stale": len(self.stale),
                "orphans": len(self.orphans),
                "duplicates": len(self.duplicates),
                "all": self.total,
            },
            "contradictions": [f.to_dict() for f in self.contradictions],
            "stale": [f.to_dict() for f in self.stale],
            "orphans": [f.to_dict() for f in self.orphans],
            "duplicates": [f.to_dict() for f in self.duplicates],
        }


# Internal normalised row used by the pure detectors.
@dataclass(frozen=True)
class _Row:
    id: str
    text: str
    metadata: Mapping[str, Any]


def _coerce_rows(memories: Iterable[Any]) -> list[_Row]:
    rows: list[_Row] = []
    for m in memories:
        if isinstance(m, _Row):
            rows.append(m)
            continue
        mid = getattr(m, "id", None)
        text = getattr(m, "text", None)
        meta = getattr(m, "metadata", None)
        if mid is None and isinstance(m, Mapping):
            mid = m.get("id")
            text = m.get("text")
            meta = m.get("metadata")
        rows.append(_Row(id=str(mid), text=str(text or ""), metadata=dict(meta or {})))
    return rows


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _parse_iso(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", ""))
    except (ValueError, TypeError):
        return None


def _has_link(meta: Mapping[str, Any]) -> bool:
    """True when a row points at anything else (refs, links, relations, entity)."""
    for key in (
        "related_ids",
        "evidence_refs",
        "relationships",
        "supersedes",
        "supersedes_id",
        "superseded_by_id",
        "event_id",
        "entity_id",
        "session_id",
        "user_turn_id",
    ):
        val = meta.get(key)
        if val not in (None, "", "[]", "{}", "0"):
            return True
    return False


def _subject_value_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pattern in _SINGLE_VALUED_PATTERNS:
        for match in pattern.finditer(text or ""):
            subject = _normalise_text(match.group("subject"))
            value = _normalise_text(_VALUE_TRAILING_RE.sub("", match.group("value")))
            # Strip a leading negation cue from the subject so "X no longer
            # lives in Y" still groups under subject "X".
            subject = _NEGATION_RE.sub("", subject).strip()
            if subject and value:
                pairs.append((subject, value))
    return pairs


# Pure detectors ------------------------------------------------------------

def _detect_duplicates(
    rows: list[_Row], near_ratio: float
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    by_exact: dict[str, list[_Row]] = {}
    for row in rows:
        by_exact.setdefault(_normalise_text(row.text), []).append(row)

    grouped: set[str] = set()
    for norm, group in by_exact.items():
        if not norm:
            continue
        if len(group) > 1:
            ids = [r.id for r in group]
            grouped.update(ids)
            findings.append(
                LintFinding(
                    kind="duplicate",
                    memory_ids=ids,
                    reason="exact-duplicate text",
                    detail={"similarity": 1.0, "text": group[0].text[:160]},
                )
            )

    # Near-duplicates across distinct normalised texts (skip exact groups).
    remaining = [g[0] for norm, g in by_exact.items() if norm]
    for i in range(len(remaining)):
        a = remaining[i]
        for j in range(i + 1, len(remaining)):
            b = remaining[j]
            if a.id in grouped and b.id in grouped:
                continue
            ratio = difflib.SequenceMatcher(
                None, _normalise_text(a.text), _normalise_text(b.text)
            ).ratio()
            if ratio >= near_ratio:
                findings.append(
                    LintFinding(
                        kind="duplicate",
                        memory_ids=[a.id, b.id],
                        reason="near-duplicate text",
                        detail={"similarity": round(ratio, 4)},
                    )
                )
    return findings


def _detect_stale(
    rows: list[_Row], now: datetime.datetime, stale_age_days: float
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    now_iso = now.isoformat() + "Z"
    for row in rows:
        meta = row.metadata
        status = str(meta.get("status", "approved") or "approved").strip().lower()
        if status in _SUPERSEDED_STATUSES:
            findings.append(
                LintFinding(
                    kind="stale",
                    memory_ids=[row.id],
                    reason=f"status={status}",
                    detail={"status": status},
                )
            )
            continue

        expires = meta.get("expires_at")
        if expires and str(expires) <= now_iso:
            findings.append(
                LintFinding(
                    kind="stale",
                    memory_ids=[row.id],
                    reason="expired",
                    detail={"expires_at": expires},
                )
            )
            continue

        added = _parse_iso(meta.get("added_at"))
        if added is not None:
            age_days = (now - added).total_seconds() / 86400.0
            if age_days >= stale_age_days:
                findings.append(
                    LintFinding(
                        kind="stale",
                        memory_ids=[row.id],
                        reason="old",
                        detail={"age_days": round(age_days, 1)},
                    )
                )
    return findings


def _detect_orphans(rows: list[_Row]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for row in rows:
        meta = row.metadata
        try:
            access = int(meta.get("access_count", 0) or 0)
        except (TypeError, ValueError):
            access = 0
        try:
            unique_q = int(meta.get("unique_query_count", 0) or 0)
        except (TypeError, ValueError):
            unique_q = 0
        if access == 0 and unique_q == 0 and not _has_link(meta):
            findings.append(
                LintFinding(
                    kind="orphan",
                    memory_ids=[row.id],
                    reason="no access, no queries, no links",
                    detail={"access_count": access, "unique_query_count": unique_q},
                )
            )
    return findings


def _detect_contradictions(rows: list[_Row]) -> list[LintFinding]:
    findings: list[LintFinding] = []

    # 1. Single-valued relations: same subject, different value.
    by_subject: dict[str, list[tuple[str, str]]] = {}  # subject -> [(value, id)]
    for row in rows:
        for subject, value in _subject_value_pairs(row.text):
            by_subject.setdefault(subject, []).append((value, row.id))
    for subject, entries in by_subject.items():
        values = {v for v, _ in entries}
        if len(values) > 1:
            ids = sorted({rid for _, rid in entries})
            if len(ids) > 1:
                findings.append(
                    LintFinding(
                        kind="contradiction",
                        memory_ids=ids,
                        reason="single-valued relation has conflicting values",
                        detail={"subject": subject, "values": sorted(values)},
                    )
                )

    # 2. Polarity flip: two rows that share most content tokens but disagree on
    #    negation (one asserts, the other negates). Heuristic, high-overlap only.
    n = len(rows)
    for i in range(n):
        a = rows[i]
        a_neg = bool(_NEGATION_RE.search(a.text or ""))
        a_tokens = _tokens(a.text) - _NEG_STOPWORDS
        if not a_tokens:
            continue
        for j in range(i + 1, n):
            b = rows[j]
            b_neg = bool(_NEGATION_RE.search(b.text or ""))
            if a_neg == b_neg:
                continue
            b_tokens = _tokens(b.text) - _NEG_STOPWORDS
            if not b_tokens:
                continue
            overlap = len(a_tokens & b_tokens)
            union = len(a_tokens | b_tokens)
            if union and overlap / union >= 0.6:
                findings.append(
                    LintFinding(
                        kind="contradiction",
                        memory_ids=sorted([a.id, b.id]),
                        reason="polarity flip on overlapping facts",
                        detail={"jaccard": round(overlap / union, 4)},
                    )
                )
    return findings


# Negation-cue tokens stripped before computing content overlap so the
# negation itself doesn't reduce similarity between an assertion and its denial.
_NEG_STOPWORDS = {
    "not", "no", "longer", "never", "doesn", "does", "isn", "is", "are",
    "won", "will", "can", "cannot", "t", "stopped", "quit", "the", "a", "an",
}


def lint_memories(
    memories: Iterable[Any],
    *,
    user_id: str = "",
    now: Optional[datetime.datetime] = None,
    stale_age_days: float = STALE_AGE_DAYS_DEFAULT,
    near_duplicate_ratio: float = NEAR_DUPLICATE_RATIO_DEFAULT,
) -> LintReport:
    """Pure, report-only lint over an iterable of memory rows.

    Accepts ``MemoryRef`` objects, anything with ``id``/``text``/``metadata``
    attributes, or plain ``{"id","text","metadata"}`` mappings. Returns a
    :class:`LintReport`. Never mutates the inputs or any store.
    """
    now = now or datetime.datetime.utcnow()
    rows = _coerce_rows(memories)
    return LintReport(
        user_id=user_id,
        scanned=len(rows),
        contradictions=_detect_contradictions(rows),
        stale=_detect_stale(rows, now, stale_age_days),
        orphans=_detect_orphans(rows),
        duplicates=_detect_duplicates(rows, near_duplicate_ratio),
        generated_at=now.isoformat() + "Z",
    )


# Async entrypoints (read through the MemoryService facade) -----------------

async def lint_user(
    user_id: str,
    *,
    service: Any = None,
    stale_age_days: float = STALE_AGE_DAYS_DEFAULT,
    near_duplicate_ratio: float = NEAR_DUPLICATE_RATIO_DEFAULT,
) -> LintReport:
    """Read every stored row for ``user_id`` and return a report-only LintReport.

    Uses ``MemoryService.export_user`` (read-only full dump) so it sees rows of
    every status - including ``superseded``/``archived`` - which the prompt and
    search paths intentionally hide. Performs no writes.
    """
    if service is None:
        from memory_service import get_memory_service

        service = get_memory_service()

    dump = await service.export_user(user_id)
    items = dump.get("items", []) if isinstance(dump, Mapping) else []
    rows = [
        {"id": it.get("id"), "text": it.get("text"), "metadata": it.get("metadata") or {}}
        for it in items
        if isinstance(it, Mapping)
    ]
    return lint_memories(
        rows,
        user_id=user_id,
        stale_age_days=stale_age_days,
        near_duplicate_ratio=near_duplicate_ratio,
    )


async def lint_all(*, service: Any = None) -> list[LintReport]:
    """Run :func:`lint_user` for every user with stored memory. Report-only."""
    if service is None:
        from memory_service import get_memory_service

        service = get_memory_service()

    try:
        sizes = await service.collection_sizes_by_user()
        user_ids = [uid for uid in sizes if uid and uid != "unknown"]
    except Exception:
        user_ids = []

    reports: list[LintReport] = []
    for uid in user_ids:
        try:
            reports.append(await lint_user(uid, service=service))
        except Exception:
            continue
    return reports


def dreaming_lint_enabled() -> bool:
    """Opt-in flag for emitting a lint report from the nightly dreaming cycle.

    Default OFF. The dreaming cycle must remain Ingest-only by default; Lint is
    report-only and is surfaced for human/curation review, not auto-applied.
    """
    return str(os.environ.get("ZOE_MEMORY_LINT_IN_DREAMING", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }

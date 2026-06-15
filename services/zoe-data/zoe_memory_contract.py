"""Zoe memory contract for Zoe's governed self-evolution harness.

This module defines the typed event/relationship shape that Hindsight,
Graphiti-style graphs, MemPalace, Graphify, and Multica can share without
making any one backend the source of truth during the bake-off phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


class MemoryContractError(ValueError):
    """Raised when a memory event would violate Zoe's safety contract."""


class MemoryScope(str, Enum):
    PERSONAL = "personal"
    SHARED = "shared"
    AMBIENT = "ambient"
    SYSTEM = "system"
    PROJECT = "project"


class MemorySource(str, Enum):
    CHAT = "chat"
    TOOL = "tool"
    TEST = "test"
    TRACE = "trace"
    PROPOSAL = "proposal"
    CODE = "code"
    EXTERNAL = "external"


class MemoryEventType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    EXPERIENCE = "experience"
    FAILURE = "failure"
    FIX = "fix"
    CAPABILITY = "capability"
    RECURRING_TASK = "recurring_task"
    APPROVAL = "approval"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class RelationshipType(str, Enum):
    ASKED_FOR = "ASKED_FOR"
    USES = "USES"
    FAILED_ON = "FAILED_ON"
    FIXED_BY = "FIXED_BY"
    APPROVED_BY = "APPROVED_BY"
    TRUSTED_FOR = "TRUSTED_FOR"
    SUPERSEDES = "SUPERSEDES"
    RECURS_AS = "RECURS_AS"
    CAUSED_BY = "CAUSED_BY"
    EVIDENCED_BY = "EVIDENCED_BY"
    BELONGS_TO_SCOPE = "BELONGS_TO_SCOPE"
    PROPOSED_CAPABILITY = "PROPOSED_CAPABILITY"
    MEASURED_BY = "MEASURED_BY"


VALID_SCOPES = {item.value for item in MemoryScope}
VALID_SOURCES = {item.value for item in MemorySource}
VALID_EVENT_TYPES = {item.value for item in MemoryEventType}
VALID_STATUSES = {item.value for item in MemoryStatus}
VALID_RELATIONSHIPS = {item.value for item in RelationshipType}

SELF_EVOLUTION_EVENT_TYPES = {
    MemoryEventType.EXPERIENCE.value,
    MemoryEventType.FAILURE.value,
    MemoryEventType.FIX.value,
    MemoryEventType.CAPABILITY.value,
    MemoryEventType.APPROVAL.value,
}


@dataclass(frozen=True)
class MemoryRelationship:
    """A typed edge candidate for Zoe's relational memory graph."""

    relationship_type: str
    target: str
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.relationship_type not in VALID_RELATIONSHIPS:
            raise MemoryContractError(f"unsupported relationship_type: {self.relationship_type}")
        if not str(self.target or "").strip():
            raise MemoryContractError("relationship target is required")
        if self.source is not None and not str(self.source).strip():
            raise MemoryContractError("relationship source cannot be blank")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship_type": self.relationship_type,
            "source": self.source,
            "target": self.target,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class MemoryEvent:
    """Portable event shape for Zoe memory retain/graph-write candidates."""

    user_id: str
    scope: str
    source: str
    event_type: str
    content: str
    evidence_refs: tuple[str, ...]
    event_id: str = field(default_factory=lambda: f"mem_evt_{uuid4().hex}")
    entities: tuple[str, ...] = ()
    relationships: tuple[MemoryRelationship, ...] = ()
    confidence: float = 0.5
    status: str = MemoryStatus.ACTIVE.value
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    supersedes: tuple[str, ...] = ()
    retention_policy: str = "standard"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not str(self.event_id or "").strip():
            raise MemoryContractError("event_id is required")
        if not str(self.user_id or "").strip():
            raise MemoryContractError("user_id is required")
        if self.scope not in VALID_SCOPES:
            raise MemoryContractError(f"unsupported scope: {self.scope}")
        if self.source not in VALID_SOURCES:
            raise MemoryContractError(f"unsupported source: {self.source}")
        if self.event_type not in VALID_EVENT_TYPES:
            raise MemoryContractError(f"unsupported event_type: {self.event_type}")
        if not str(self.content or "").strip():
            raise MemoryContractError("content is required")
        if self.status not in VALID_STATUSES:
            raise MemoryContractError(f"unsupported status: {self.status}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryContractError("confidence must be between 0.0 and 1.0")
        if self.event_id in set(self.supersedes or ()):  # defensive against loops
            raise MemoryContractError("event cannot supersede itself")

        for rel in self.relationships:
            rel.validate()

        has_relational_truth = bool(self.relationships or self.supersedes)
        is_self_evolution = self.event_type in SELF_EVOLUTION_EVENT_TYPES or self.source == MemorySource.PROPOSAL.value
        if (has_relational_truth or is_self_evolution) and not self.evidence_refs:
            raise MemoryContractError("evidence_refs are required for relational or self-evolution memory")

    def validated(self) -> "MemoryEvent":
        self.validate()
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "scope": self.scope,
            "source": self.source,
            "event_type": self.event_type,
            "content": self.content,
            "entities": list(self.entities),
            "relationships": [rel.to_dict() for rel in self.relationships],
            "evidence_refs": list(self.evidence_refs),
            "confidence": float(self.confidence),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "supersedes": list(self.supersedes),
            "retention_policy": self.retention_policy,
            "metadata": dict(self.metadata or {}),
        }


def memory_event_from_mapping(payload: Mapping[str, Any]) -> MemoryEvent:
    """Build and validate a MemoryEvent from an API/worker payload."""

    relationships = tuple(
        MemoryRelationship(
            relationship_type=str(item.get("relationship_type") or item.get("type") or ""),
            source=item.get("source"),
            target=str(item.get("target") or ""),
            metadata=item.get("metadata") or {},
        )
        for item in payload.get("relationships") or ()
    )
    created = payload.get("created_at")
    if isinstance(created, str):
        created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
    elif isinstance(created, datetime):
        created_at = created
    else:
        created_at = datetime.now(timezone.utc)

    event = MemoryEvent(
        event_id=str(payload.get("event_id") or f"mem_evt_{uuid4().hex}"),
        user_id=str(payload.get("user_id") or ""),
        scope=str(payload.get("scope") or ""),
        source=str(payload.get("source") or ""),
        event_type=str(payload.get("event_type") or ""),
        content=str(payload.get("content") or ""),
        entities=tuple(str(item) for item in payload.get("entities") or ()),
        relationships=relationships,
        evidence_refs=tuple(str(item) for item in payload.get("evidence_refs") or ()),
        confidence=float(payload.get("confidence", 0.5)),
        status=str(payload.get("status") or MemoryStatus.ACTIVE.value),
        created_at=created_at,
        supersedes=tuple(str(item) for item in payload.get("supersedes") or ()),
        retention_policy=str(payload.get("retention_policy") or "standard"),
        metadata=payload.get("metadata") or {},
    )
    return event.validated()


__all__ = [
    "MemoryContractError",
    "MemoryEvent",
    "MemoryEventType",
    "MemoryRelationship",
    "MemoryScope",
    "MemorySource",
    "MemoryStatus",
    "RelationshipType",
    "memory_event_from_mapping",
]

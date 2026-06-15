"""Zoe memory layer policy for the evolution harness.

This module names the memory layers so routes, docs, and future evals keep the
same boring mental model: Postgres is truth, MemoryService gates writes,
MemPalace recalls episodes, Hindsight reflects asynchronously, Graphiti models
changing relationships, and Multica/review governs risky changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MemoryLayer(str, Enum):
    WORKING_CONTEXT = "working_context"
    CANONICAL_STATE = "canonical_state"
    EPISODIC_MEMORY = "episodic_memory"
    REFLECTIVE_MEMORY = "reflective_memory"
    RELATIONAL_TEMPORAL_MEMORY = "relational_temporal_memory"
    GOVERNANCE = "governance"
    OBSERVATION_EVALUATION = "observation_evaluation"


@dataclass(frozen=True)
class MemoryLayerPolicy:
    layer: MemoryLayer
    owner: str
    hot_path: bool
    write_policy: str
    failure_policy: str

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "owner": self.owner,
            "hot_path": self.hot_path,
            "write_policy": self.write_policy,
            "failure_policy": self.failure_policy,
        }


LAYER_POLICIES: tuple[MemoryLayerPolicy, ...] = (
    MemoryLayerPolicy(
        MemoryLayer.WORKING_CONTEXT,
        owner="chat/session runtime",
        hot_path=True,
        write_policy="short-lived per-turn/session state only",
        failure_policy="discard and continue",
    ),
    MemoryLayerPolicy(
        MemoryLayer.CANONICAL_STATE,
        owner="PostgreSQL app tables",
        hot_path=True,
        write_policy="canonical app state, users, permissions, tasks, reminders, people, audit",
        failure_policy="fail closed for required app state",
    ),
    MemoryLayerPolicy(
        MemoryLayer.EPISODIC_MEMORY,
        owner="MemoryService/MemPalace",
        hot_path=True,
        write_policy="exact facts, preferences, conversation-derived memories, verbatim-ish recall",
        failure_policy="timeout-bounded fallback to working context and canonical state",
    ),
    MemoryLayerPolicy(
        MemoryLayer.REFLECTIVE_MEMORY,
        owner="Hindsight sidecar",
        hot_path=False,
        write_policy="pending/candidate lessons, recurring patterns, failures, fixes, experience summaries",
        failure_policy="skip async enrichment; never block chat or voice",
    ),
    MemoryLayerPolicy(
        MemoryLayer.RELATIONAL_TEMPORAL_MEMORY,
        owner="Graphiti-style graph",
        hot_path=False,
        write_policy="evidence-backed changing relationships, supersession, causality, approvals",
        failure_policy="defer graph enrichment; keep canonical and episodic paths usable",
    ),
    MemoryLayerPolicy(
        MemoryLayer.GOVERNANCE,
        owner="Zoe memory contract + Multica/review",
        hot_path=False,
        write_policy="evidence-gated admission for important memory and self-evolution writes",
        failure_policy="leave memory as pending/candidate",
    ),
    MemoryLayerPolicy(
        MemoryLayer.OBSERVATION_EVALUATION,
        owner="Phoenix/evals or Zoe eval traces",
        hot_path=False,
        write_policy="record retrieval latency, helpfulness, hallucination, contradiction, and fallback outcomes",
        failure_policy="drop eval trace before slowing user path",
    ),
)


def layer_policy(layer: MemoryLayer) -> MemoryLayerPolicy:
    for policy in LAYER_POLICIES:
        if policy.layer == layer:
            return policy
    raise KeyError(layer)


FAST_CHAT_LAYERS = (
    MemoryLayer.WORKING_CONTEXT,
    MemoryLayer.CANONICAL_STATE,
    MemoryLayer.EPISODIC_MEMORY,
)

ASYNC_ENRICHMENT_LAYERS = (
    MemoryLayer.REFLECTIVE_MEMORY,
    MemoryLayer.RELATIONAL_TEMPORAL_MEMORY,
    MemoryLayer.GOVERNANCE,
    MemoryLayer.OBSERVATION_EVALUATION,
)


__all__ = [
    "ASYNC_ENRICHMENT_LAYERS",
    "FAST_CHAT_LAYERS",
    "LAYER_POLICIES",
    "MemoryLayer",
    "MemoryLayerPolicy",
    "layer_policy",
]

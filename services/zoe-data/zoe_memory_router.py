"""Deterministic memory routing policy for Zoe's evolution harness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from zoe_memory_layers import FAST_CHAT_LAYERS, MemoryLayer


class MemoryBackend(str, Enum):
    MEMPALACE = "mempalace"
    HINDSIGHT = "hindsight"
    GRAPHITI = "graphiti"
    GRAPHIFY = "graphify"
    MULTICA = "multica"


@dataclass(frozen=True)
class MemoryRoute:
    primary: MemoryBackend
    secondary: tuple[MemoryBackend, ...]
    memory_layers: tuple[MemoryLayer, ...]
    latency_budget_ms: int
    prompt_policy: str
    write_policy: str
    observation_policy: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary.value,
            "secondary": [item.value for item in self.secondary],
            "memory_layers": [item.value for item in self.memory_layers],
            "latency_budget_ms": self.latency_budget_ms,
            "prompt_policy": self.prompt_policy,
            "write_policy": self.write_policy,
            "observation_policy": self.observation_policy,
            "reason": self.reason,
        }


RELATIONAL_TERMS = {
    "caused",
    "cause",
    "causality",
    "superseded",
    "supersedes",
    "approved",
    "approval",
    "trusted",
    "promise",
    "promises",
    "relationship",
    "graph",
    "evidence",
}

EXPERIENCE_TERMS = {
    "learned",
    "experience",
    "outcome",
    "worked",
    "didn't work",
    "failed",
    "failure",
    "fix",
    "fixed",
    "recurring",
    "pattern",
    "reflect",
    "remember what happened",
}

CODE_TERMS = {
    "code",
    "service",
    "dependency",
    "router",
    "module",
    "function",
    "graphify",
    "repo",
    "commit",
}

EVOLUTION_TERMS = {
    "evolve",
    "self-modify",
    "self modify",
    "upgrade",
    "capability",
    "proposal",
    "multica",
    "approve",
    "harness",
}


def _contains_any(text: str, terms: set[str]) -> bool:
    for term in terms:
        pattern = r"(?<![\w])" + re.escape(term) + r"(?![\w])"
        if re.search(pattern, text):
            return True
    return False


def route_memory_query(query: str, *, purpose: str = "chat") -> MemoryRoute:
    """Choose the memory backend policy without calling any backend.

    The router is intentionally conservative: normal chat stays on working
    context, canonical state, and episodic memory. Reflective and relational
    layers are feature-flagged/async enrichment paths, and self-evolution goes
    through Multica evidence.
    """

    text = f"{purpose} {query}".lower()

    if _contains_any(text, EVOLUTION_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.MULTICA,
            secondary=(MemoryBackend.HINDSIGHT, MemoryBackend.GRAPHITI, MemoryBackend.GRAPHIFY),
            memory_layers=(
                MemoryLayer.GOVERNANCE,
                MemoryLayer.REFLECTIVE_MEMORY,
                MemoryLayer.RELATIONAL_TEMPORAL_MEMORY,
                MemoryLayer.OBSERVATION_EVALUATION,
            ),
            latency_budget_ms=2000,
            prompt_policy="compile evidence-backed proposal context only",
            write_policy="proposal-gated; no trusted auto-retain",
            observation_policy="trace proposal evidence, verification result, and retained outcome",
            reason="self-evolution requires governance and evidence",
        )

    if _contains_any(text, RELATIONAL_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.GRAPHITI,
            secondary=(MemoryBackend.HINDSIGHT, MemoryBackend.MEMPALACE),
            memory_layers=(
                MemoryLayer.RELATIONAL_TEMPORAL_MEMORY,
                MemoryLayer.REFLECTIVE_MEMORY,
                MemoryLayer.EPISODIC_MEMORY,
                MemoryLayer.OBSERVATION_EVALUATION,
            ),
            latency_budget_ms=2000,
            prompt_policy="return compact current facts with evidence and supersession state",
            write_policy="evidence-required graph candidate; async consolidation",
            observation_policy="record latency, contradiction, supersession, and evidence coverage",
            reason="relationship, causality, approval, or supersession query",
        )

    if _contains_any(text, EXPERIENCE_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.HINDSIGHT,
            secondary=(MemoryBackend.MEMPALACE,),
            memory_layers=(
                MemoryLayer.REFLECTIVE_MEMORY,
                MemoryLayer.EPISODIC_MEMORY,
                MemoryLayer.OBSERVATION_EVALUATION,
            ),
            latency_budget_ms=600,
            prompt_policy="recall experiences and mental models with source pointers",
            write_policy="retain candidates only; auto-retain disabled by default",
            observation_policy="record recall latency, helpfulness, hallucination, and fallback outcome",
            reason="experience or outcome memory query",
        )

    if _contains_any(text, CODE_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.GRAPHIFY,
            secondary=(MemoryBackend.MEMPALACE,),
            memory_layers=(MemoryLayer.WORKING_CONTEXT, MemoryLayer.EPISODIC_MEMORY),
            latency_budget_ms=2000,
            prompt_policy="summarize code graph findings with file/source references",
            write_policy="read-only unless routed through an evolution proposal",
            observation_policy="record latency and whether code context changed the answer",
            reason="code and system questions belong to Zoe self-understanding",
        )

    return MemoryRoute(
        primary=MemoryBackend.MEMPALACE,
        secondary=(),
        memory_layers=FAST_CHAT_LAYERS,
        latency_budget_ms=300,
        prompt_policy="working context + canonical state + compact episodic recall",
        write_policy="MemoryService gatekeeper; relationship writes require contract",
        observation_policy="sample lightweight latency/helpfulness only; never slow chat",
        reason="default chat personalization path",
    )


__all__ = ["MemoryBackend", "MemoryRoute", "route_memory_query"]

"""Deterministic memory routing policy for Zoe's Samantha harness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    latency_budget_ms: int
    prompt_policy: str
    write_policy: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary.value,
            "secondary": [item.value for item in self.secondary],
            "latency_budget_ms": self.latency_budget_ms,
            "prompt_policy": self.prompt_policy,
            "write_policy": self.write_policy,
            "reason": self.reason,
        }


RELATIONAL_TERMS = {
    "failed",
    "failure",
    "fix",
    "fixed",
    "caused",
    "cause",
    "superseded",
    "supersedes",
    "approved",
    "approval",
    "trusted",
    "recurring",
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
    return any(term in text for term in terms)


def route_memory_query(query: str, *, purpose: str = "chat") -> MemoryRoute:
    """Choose the memory backend policy without calling any backend.

    The router is intentionally conservative: normal chat stays fast, relationship
    questions go to the graph layer, experience questions go to Hindsight, code
    questions go to Graphify, and self-evolution goes through Multica evidence.
    """

    text = f"{purpose} {query}".lower()

    if _contains_any(text, EVOLUTION_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.MULTICA,
            secondary=(MemoryBackend.HINDSIGHT, MemoryBackend.GRAPHITI, MemoryBackend.GRAPHIFY),
            latency_budget_ms=2000,
            prompt_policy="compile evidence-backed proposal context only",
            write_policy="proposal-gated; no trusted auto-retain",
            reason="self-evolution requires governance and evidence",
        )

    if _contains_any(text, CODE_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.GRAPHIFY,
            secondary=(MemoryBackend.MEMPALACE,),
            latency_budget_ms=2000,
            prompt_policy="summarize code graph findings with file/source references",
            write_policy="read-only unless routed through an evolution proposal",
            reason="code and system questions belong to Zoe self-understanding",
        )

    if _contains_any(text, RELATIONAL_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.GRAPHITI,
            secondary=(MemoryBackend.HINDSIGHT, MemoryBackend.MEMPALACE),
            latency_budget_ms=2000,
            prompt_policy="return compact current facts with evidence and supersession state",
            write_policy="evidence-required graph candidate; async consolidation",
            reason="relationship, causality, approval, or supersession query",
        )

    if _contains_any(text, EXPERIENCE_TERMS):
        return MemoryRoute(
            primary=MemoryBackend.HINDSIGHT,
            secondary=(MemoryBackend.MEMPALACE,),
            latency_budget_ms=600,
            prompt_policy="recall experiences and mental models with source pointers",
            write_policy="retain candidates only; auto-retain disabled by default",
            reason="experience or outcome memory query",
        )

    return MemoryRoute(
        primary=MemoryBackend.MEMPALACE,
        secondary=(),
        latency_budget_ms=300,
        prompt_policy="fast associative recall and cached user/task summary",
        write_policy="existing MemoryService safety rails; relationship writes require contract",
        reason="default chat personalization path",
    )


__all__ = ["MemoryBackend", "MemoryRoute", "route_memory_query"]

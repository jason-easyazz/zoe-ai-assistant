"""Candidate scoring for Zoe capability adoption.

Zoe should evaluate existing Pi, MCP, GitHub, skill, API, and local-service
options before building or installing a new capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CANDIDATE_SOURCES = {"pi", "mcp", "github", "skill", "api", "local_service", "existing_zoe"}
LICENSE_RISKS = {"compatible", "review", "incompatible", "unknown"}
OFFLINE_VIABILITY = {"required", "supported", "partial", "unavailable", "unknown"}
RECOMMENDATIONS = {"keep", "trial_sidecar", "needs_review", "reject"}


@dataclass(frozen=True)
class CandidateScore:
    fit: int
    activity: int
    license: int
    offline: int
    security: int
    footprint: int
    tests: int
    maintainability: int
    overlap: int

    def validate(self) -> None:
        for field_name, value in self.to_dict().items():
            if not 0 <= value <= 5:
                raise ValueError(f"{field_name} score must be between 0 and 5")

    def total(self) -> int:
        self.validate()
        return sum(self.to_dict().values())

    def normalized(self) -> float:
        return self.total() / 45

    def to_dict(self) -> dict[str, int]:
        return {
            "fit": self.fit,
            "activity": self.activity,
            "license": self.license,
            "offline": self.offline,
            "security": self.security,
            "footprint": self.footprint,
            "tests": self.tests,
            "maintainability": self.maintainability,
            "overlap": self.overlap,
        }


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate_id: str
    name: str
    source: str
    task: str
    score: CandidateScore
    evidence_refs: tuple[str, ...]
    license_risk: str = "unknown"
    offline_viability: str = "unknown"
    stars: int | None = None
    last_activity: str | None = None
    runtime_notes: str | None = None
    security_notes: str | None = None
    overlaps_existing: tuple[str, ...] = ()
    recommendation: str = "needs_review"
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if not self.name:
            raise ValueError(f"{self.candidate_id}: name is required")
        if self.source not in CANDIDATE_SOURCES:
            raise ValueError(f"{self.candidate_id}: unknown source {self.source!r}")
        if self.license_risk not in LICENSE_RISKS:
            raise ValueError(f"{self.candidate_id}: unknown license_risk {self.license_risk!r}")
        if self.offline_viability not in OFFLINE_VIABILITY:
            raise ValueError(f"{self.candidate_id}: unknown offline_viability {self.offline_viability!r}")
        if self.recommendation not in RECOMMENDATIONS:
            raise ValueError(f"{self.candidate_id}: unknown recommendation {self.recommendation!r}")
        if not self.evidence_refs:
            raise ValueError(f"{self.candidate_id}: evidence_refs are required")
        if self.stars is not None and self.stars < 0:
            raise ValueError(f"{self.candidate_id}: stars must be non-negative")
        self.score.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        score = self.score.to_dict()
        total_score = sum(score.values())
        normalized_score = total_score / 45
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "source": self.source,
            "task": self.task,
            "score": score,
            "total_score": total_score,
            "normalized_score": normalized_score,
            "evidence_refs": list(self.evidence_refs),
            "license_risk": self.license_risk,
            "offline_viability": self.offline_viability,
            "stars": self.stars,
            "last_activity": self.last_activity,
            "runtime_notes": self.runtime_notes,
            "security_notes": self.security_notes,
            "overlaps_existing": list(self.overlaps_existing),
            "recommendation": self.recommendation,
            "metadata": dict(self.metadata or {}),
        }


def rank_candidates(candidates: Sequence[CandidateEvaluation]) -> list[CandidateEvaluation]:
    for candidate in candidates:
        candidate.validate()
    return sorted(candidates, key=lambda candidate: (candidate.score.total(), candidate.score.fit), reverse=True)


def adoption_gate(candidate: CandidateEvaluation, *, minimum_score: float = 0.72) -> dict[str, Any]:
    candidate.validate()
    blockers = []
    if candidate.license_risk in {"incompatible", "unknown"}:
        blockers.append(f"license:{candidate.license_risk}")
    if candidate.offline_viability in {"unavailable", "unknown"}:
        blockers.append(f"offline:{candidate.offline_viability}")
    if candidate.score.normalized() < minimum_score:
        blockers.append("score_below_threshold")
    return {
        "candidate_id": candidate.candidate_id,
        "allowed": not blockers,
        "blockers": blockers,
        "normalized_score": candidate.score.normalized(),
        "recommendation": candidate.recommendation,
    }


EXAMPLE_CANDIDATES: tuple[CandidateEvaluation, ...] = (
    CandidateEvaluation(
        candidate_id="existing_mempalace",
        name="Keep MemPalace as Zoe baseline",
        source="existing_zoe",
        task="offline episodic memory recall",
        score=CandidateScore(fit=5, activity=4, license=4, offline=5, security=4, footprint=4, tests=5, maintainability=4, overlap=5),
        evidence_refs=("docs/architecture/zoe-mempalace-baseline.md",),
        license_risk="compatible",
        offline_viability="required",
        runtime_notes="Measured p95 under current Zoe local baseline.",
        overlaps_existing=("mempalace_memory",),
        recommendation="keep",
    ),
    CandidateEvaluation(
        candidate_id="graphiti_falkordb_trial",
        name="Graphiti with FalkorDB",
        source="github",
        task="temporal relationship memory",
        score=CandidateScore(fit=4, activity=4, license=3, offline=4, security=3, footprint=2, tests=3, maintainability=3, overlap=2),
        evidence_refs=("docs/architecture/zoe-graphiti-fixtures.md", "docs/adr/ADR-graphiti-bakeoff.md"),
        license_risk="review",
        offline_viability="supported",
        runtime_notes="Needs Jetson CPU/RAM measurement before any hot-path use.",
        overlaps_existing=("graphiti_relational_memory",),
        recommendation="trial_sidecar",
    ),
    CandidateEvaluation(
        candidate_id="pi_runtime_reuse",
        name="Pi runtime/package reuse",
        source="pi",
        task="external capability discovery and reuse",
        score=CandidateScore(fit=4, activity=3, license=2, offline=2, security=2, footprint=2, tests=2, maintainability=3, overlap=3),
        evidence_refs=("docs/strategy/zoe-evolution-harness-plan.md",),
        license_risk="review",
        offline_viability="unknown",
        runtime_notes="Must be scored per package/runtime before install.",
        overlaps_existing=("pi_external_runtime",),
        recommendation="needs_review",
    ),
)


__all__ = [
    "CANDIDATE_SOURCES",
    "EXAMPLE_CANDIDATES",
    "LICENSE_RISKS",
    "OFFLINE_VIABILITY",
    "RECOMMENDATIONS",
    "CandidateEvaluation",
    "CandidateScore",
    "adoption_gate",
    "rank_candidates",
]

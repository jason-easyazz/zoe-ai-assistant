"""Zoe capability profile contract and seed inventory.

Capability profiles are Zoe's self-model: what an ability does, where it lives,
how trusted it is, which approvals it needs, and what evidence keeps it honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence


OWNER_SURFACES = {
    "chat",
    "voice",
    "ui",
    "memory",
    "graphify",
    "multica",
    "hermes",
    "openclaw",
    "mcp",
    "pi",
    "local_service",
    "external_api",
}

TRUST_LEVELS = {"experimental", "assisted", "trusted", "privileged", "retired"}
OFFLINE_MODES = {"required", "supported", "unavailable", "unknown"}


@dataclass(frozen=True)
class CapabilityProfile:
    capability_id: str
    name: str
    owner_surface: str
    task_types: tuple[str, ...]
    trust_level: str
    approval_required: tuple[str, ...] = ()
    offline_mode: str = "unknown"
    model_dependencies: tuple[str, ...] = ()
    device_dependencies: tuple[str, ...] = ()
    memory_dependencies: tuple[str, ...] = ()
    latency_budget_ms: int | None = None
    cpu_budget: str | None = None
    ram_budget_mb: int | None = None
    gpu_budget: str | None = None
    evidence_refs: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    last_verified_at: str | None = None
    known_failures: tuple[str, ...] = ()
    rollback: str | None = None
    notes: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def validate(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.name:
            raise ValueError(f"{self.capability_id}: name is required")
        if self.owner_surface not in OWNER_SURFACES:
            raise ValueError(f"{self.capability_id}: unknown owner_surface {self.owner_surface!r}")
        if self.trust_level not in TRUST_LEVELS:
            raise ValueError(f"{self.capability_id}: unknown trust_level {self.trust_level!r}")
        if self.offline_mode not in OFFLINE_MODES:
            raise ValueError(f"{self.capability_id}: unknown offline_mode {self.offline_mode!r}")
        if not self.task_types:
            raise ValueError(f"{self.capability_id}: at least one task_type is required")
        if self.latency_budget_ms is not None and self.latency_budget_ms < 0:
            raise ValueError(f"{self.capability_id}: latency_budget_ms must be non-negative")
        if self.ram_budget_mb is not None and self.ram_budget_mb < 0:
            raise ValueError(f"{self.capability_id}: ram_budget_mb must be non-negative")
        if self.trust_level in {"trusted", "privileged"}:
            if not self.evidence_refs:
                raise ValueError(f"{self.capability_id}: trusted or privileged capabilities require evidence_refs")
            if not self.tests:
                raise ValueError(f"{self.capability_id}: trusted or privileged capabilities require tests")
            if not self.rollback:
                raise ValueError(f"{self.capability_id}: trusted or privileged capabilities require rollback")
        if self.trust_level == "privileged" and not self.approval_required:
            raise ValueError(f"{self.capability_id}: privileged capabilities require approval_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "owner_surface": self.owner_surface,
            "task_types": list(self.task_types),
            "trust_level": self.trust_level,
            "approval_required": list(self.approval_required),
            "offline_mode": self.offline_mode,
            "model_dependencies": list(self.model_dependencies),
            "device_dependencies": list(self.device_dependencies),
            "memory_dependencies": list(self.memory_dependencies),
            "latency_budget_ms": self.latency_budget_ms,
            "cpu_budget": self.cpu_budget,
            "ram_budget_mb": self.ram_budget_mb,
            "gpu_budget": self.gpu_budget,
            "evidence_refs": list(self.evidence_refs),
            "tests": list(self.tests),
            "last_verified_at": self.last_verified_at,
            "known_failures": list(self.known_failures),
            "rollback": self.rollback,
            "notes": self.notes,
            "metadata": dict(self.metadata),
        }


DEFAULT_CAPABILITY_PROFILES: tuple[CapabilityProfile, ...] = (
    CapabilityProfile(
        capability_id="chat_router",
        name="Production chat router",
        owner_surface="chat",
        task_types=("conversation", "tool_dispatch", "memory_capture"),
        trust_level="trusted",
        approval_required=(),
        offline_mode="required",
        model_dependencies=("gemma4_llama_server",),
        memory_dependencies=("MemoryService", "MemPalace"),
        latency_budget_ms=2500,
        evidence_refs=("docs/architecture/zoe-harness-current-inventory.md", "services/zoe-data/routers/chat.py"),
        tests=("live:/health", "live:/api/system/status"),
        last_verified_at="2026-06-09",
        rollback="Revert chat-router PR and keep production uvicorn route stable.",
    ),
    CapabilityProfile(
        capability_id="mempalace_memory",
        name="MemPalace episodic memory baseline",
        owner_surface="memory",
        task_types=("memory_recall", "memory_write", "personalization"),
        trust_level="trusted",
        approval_required=("durable_write_gate",),
        offline_mode="required",
        memory_dependencies=("MemoryService", "MemPalace"),
        latency_budget_ms=300,
        evidence_refs=("docs/architecture/zoe-mempalace-baseline.md",),
        tests=("services/zoe-data/tests/test_mempalace_baseline.py", "services/zoe-data/tests/test_mempalace_integration.py"),
        last_verified_at="2026-06-09",
        rollback="Disable new memory routing and keep existing MemoryService facade.",
    ),
    CapabilityProfile(
        capability_id="hindsight_reflective_memory",
        name="Hindsight reflective memory candidate",
        owner_surface="memory",
        task_types=("reflective_memory", "experience_recall", "lesson_recall"),
        trust_level="experimental",
        approval_required=("retain_synthetic", "memory_admission"),
        offline_mode="required",
        model_dependencies=("local_llamacpp_or_private_openai_compatible",),
        memory_dependencies=("hindsight_sidecar",),
        latency_budget_ms=600,
        evidence_refs=("docs/architecture/zoe-hindsight-bakeoff.md", "docs/adr/ADR-hindsight-bakeoff.md"),
        tests=("services/zoe-data/tests/test_hindsight_memory.py",),
        last_verified_at="2026-06-09",
        known_failures=("No Hindsight sidecar currently running on Zoe host.",),
        rollback="Keep disabled; do not route production chat through Hindsight.",
    ),
    CapabilityProfile(
        capability_id="graphiti_relational_memory",
        name="Graphiti relational memory candidate",
        owner_surface="memory",
        task_types=("temporal_graph", "relationship_query", "supersession"),
        trust_level="experimental",
        approval_required=("sidecar_start", "memory_admission"),
        offline_mode="supported",
        memory_dependencies=("Graphiti", "FalkorDB_or_Neo4j"),
        latency_budget_ms=2000,
        evidence_refs=("docs/architecture/zoe-graphiti-fixtures.md", "docs/adr/ADR-graphiti-bakeoff.md"),
        tests=(),
        last_verified_at="2026-06-09",
        rollback="Keep graph retrieval async-only or disabled until measured.",
    ),
    CapabilityProfile(
        capability_id="graphify_system_map",
        name="Graphify code and system understanding",
        owner_surface="graphify",
        task_types=("code_graph", "architecture_query", "cleanup_gate"),
        trust_level="trusted",
        approval_required=(),
        offline_mode="supported",
        latency_budget_ms=2000,
        evidence_refs=("graphify-out/GRAPH_REPORT.md", "docs/architecture/zoe-harness-current-inventory.md"),
        tests=("graphify query smoke test",),
        last_verified_at="2026-06-09",
        known_failures=("Graphify report is stale after recent harness PRs.",),
        rollback="Fall back to source inspection and refresh Graphify before cleanup decisions.",
    ),
    CapabilityProfile(
        capability_id="multica_governance",
        name="Multica governed execution control plane",
        owner_surface="multica",
        task_types=("proposal_gate", "approval", "execution_sequence"),
        trust_level="privileged",
        approval_required=("user_or_admin_for_privileged_execution",),
        offline_mode="required",
        evidence_refs=("docs/architecture/zoe-tool-capability-inventory.md", "services/zoe-data/multica_admission.py"),
        tests=("pipeline evidence validators",),
        last_verified_at="2026-06-09",
        rollback="Disable privileged proposal execution and require manual PR path.",
    ),
    CapabilityProfile(
        capability_id="hermes_escalation",
        name="Hermes planning and implementation escalation",
        owner_surface="hermes",
        task_types=("planning", "architecture_analysis", "implementation_repair", "greptile_loop"),
        trust_level="privileged",
        approval_required=("worktree", "tests", "pr_evidence"),
        offline_mode="supported",
        evidence_refs=("docs/architecture/zoe-tool-capability-inventory.md",),
        tests=("Greptile review pass", "local validation"),
        last_verified_at="2026-06-09",
        rollback="Keep Hermes output as proposal-only until PR evidence passes.",
    ),
    CapabilityProfile(
        capability_id="openclaw_fallback",
        name="OpenClaw manual fallback execution",
        owner_surface="openclaw",
        task_types=("manual_fallback", "browser_execution", "tool_execution"),
        trust_level="assisted",
        approval_required=("explicit_route_reason",),
        offline_mode="supported",
        evidence_refs=("docs/architecture/zoe-tool-capability-inventory.md",),
        tests=("live:/api/system/status openclaw_gateway",),
        last_verified_at="2026-06-09",
        rollback="Route ordinary planning and implementation back to Hermes.",
    ),
    CapabilityProfile(
        capability_id="pi_external_runtime",
        name="Pi external runtime and package ecosystem",
        owner_surface="pi",
        task_types=("capability_discovery", "external_agent_runtime", "tooling_reuse", "delegated_agent_workflow"),
        trust_level="experimental",
        approval_required=("install_or_runtime_change", "license_review", "security_review", "local_model_verification"),
        offline_mode="supported",
        model_dependencies=("local_openai_compatible_or_other_pi_supported_local_provider",),
        device_dependencies=("node", "npm", "pi_cli"),
        evidence_refs=("docs/strategy/zoe-evolution-harness-plan.md", "docs/architecture/zoe-pi-runtime-harness.md"),
        tests=("services/zoe-data/tests/test_pi_runtime_probe.py", "scripts/maintenance/pi_runtime_probe.py --json"),
        last_verified_at="2026-06-09",
        known_failures=("Zoe host currently lacks node, npm, and pi command in the clean runtime probe.",),
        rollback="Disable ZOE_PI_ENABLED and keep Pi as proposal-only discovery until runtime prerequisites pass.",
        notes="Pi is an external runtime candidate. Zoe detects and scores it before any install, package adoption, or delegated execution.",
    ),
    CapabilityProfile(
        capability_id="home_assistant_control",
        name="Home Assistant device control",
        owner_surface="local_service",
        task_types=("device_control", "home_state", "physical_world_action"),
        trust_level="privileged",
        approval_required=("device_action_confirmation",),
        offline_mode="supported",
        device_dependencies=("home_assistant",),
        evidence_refs=("docs/architecture/zoe-tool-capability-inventory.md", "live:/api/system/status ha_bridge"),
        tests=("live:/api/system/status ha_bridge",),
        last_verified_at="2026-06-09",
        rollback="Disable unsafe device actions and keep read-only Home Assistant state.",
    ),
)


def validate_capability_profiles(profiles: Sequence[CapabilityProfile] = DEFAULT_CAPABILITY_PROFILES) -> list[dict[str, Any]]:
    seen: set[str] = set()
    serialized = []
    for profile in profiles:
        profile.validate()
        if profile.capability_id in seen:
            raise ValueError(f"duplicate capability_id {profile.capability_id!r}")
        seen.add(profile.capability_id)
        serialized.append(profile.to_dict())
    return serialized


def capability_profile_index(profiles: Sequence[CapabilityProfile] = DEFAULT_CAPABILITY_PROFILES) -> dict[str, CapabilityProfile]:
    validate_capability_profiles(profiles)
    return {profile.capability_id: profile for profile in profiles}


def profiles_requiring_approval(profiles: Sequence[CapabilityProfile] = DEFAULT_CAPABILITY_PROFILES) -> tuple[CapabilityProfile, ...]:
    return tuple(profile for profile in profiles if profile.approval_required or profile.trust_level == "privileged")


__all__ = [
    "CapabilityProfile",
    "DEFAULT_CAPABILITY_PROFILES",
    "OFFLINE_MODES",
    "OWNER_SURFACES",
    "TRUST_LEVELS",
    "capability_profile_index",
    "profiles_requiring_approval",
    "validate_capability_profiles",
]

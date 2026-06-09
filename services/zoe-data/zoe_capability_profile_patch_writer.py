"""Render reviewable capability-profile patches from promotion manifests."""

from __future__ import annotations

import ast
import difflib
import hashlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from zoe_capability_profile_promotion import CapabilityProfilePromotionPlan


CAPABILITY_PROFILE_PATCH_WRITER_SOURCE = "capability_profile_patch_writer"


@dataclass(frozen=True)
class CapabilityProfilePatchRecord:
    capability_id: str
    from_trust_level: str
    to_trust_level: str
    target_path: str
    source_line: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.target_path:
            raise ValueError(f"{self.capability_id}: target_path is required")
        if not self.from_trust_level or not self.to_trust_level:
            raise ValueError(f"{self.capability_id}: trust levels are required")
        if self.from_trust_level == self.to_trust_level:
            raise ValueError(f"{self.capability_id}: patch must change trust level")
        if self.source_line <= 0:
            raise ValueError(f"{self.capability_id}: source_line must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_trust_level": self.from_trust_level,
            "to_trust_level": self.to_trust_level,
            "target_path": self.target_path,
            "source_line": self.source_line,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityProfilePatchPlan:
    target_path: str
    source_sha256: str
    patch: str
    records: tuple[CapabilityProfilePatchRecord, ...]
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.blockers and (self.records or self.patch):
            raise ValueError("blocked profile patch plans cannot carry records or patch text")

    @property
    def allowed_to_apply_patch(self) -> bool:
        return bool(self.patch and self.records) and not self.blockers

    @property
    def patched_capability_ids(self) -> tuple[str, ...]:
        if self.blockers:
            return ()
        return tuple(record.capability_id for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_apply_patch": self.allowed_to_apply_patch,
            "target_path": self.target_path,
            "source_sha256": self.source_sha256,
            "patched_capability_ids": list(self.patched_capability_ids),
            "blockers": list(self.blockers),
            "records": [record.to_dict() for record in self.records],
            "metadata": dict(self.metadata),
        }


def build_capability_profile_patch_plan(
    promotion_plan: CapabilityProfilePromotionPlan,
    *,
    source_text: str,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfilePatchPlan:
    """Build a deterministic unified diff for reviewed trust promotions."""

    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    blockers = _patch_blockers(promotion_plan, source_text=source_text)
    if blockers:
        return CapabilityProfilePatchPlan(
            target_path=promotion_plan.target_path,
            source_sha256=source_sha256,
            patch="",
            records=(),
            blockers=blockers,
            metadata=_metadata(metadata),
        )

    lines = source_text.splitlines(keepends=True)
    locations = _profile_locations(source_text)
    records: list[CapabilityProfilePatchRecord] = []

    for promotion in promotion_plan.records:
        location = locations[promotion.capability_id]
        line_index = location.trust_value_line - 1
        old_line = lines[line_index]
        old_value = f'"{promotion.from_trust_level}"'
        new_value = f'"{promotion.to_trust_level}"'
        lines[line_index] = old_line[: location.trust_value_col] + new_value + old_line[location.trust_value_end_col :]
        records.append(
            CapabilityProfilePatchRecord(
                capability_id=promotion.capability_id,
                from_trust_level=promotion.from_trust_level,
                to_trust_level=promotion.to_trust_level,
                target_path=promotion.target_path,
                source_line=location.trust_value_line,
                metadata={
                    "source": CAPABILITY_PROFILE_PATCH_WRITER_SOURCE,
                    "promotion_decision_id": promotion.decision_id,
                    "proposal_id": promotion.proposal_id,
                    "pr_refs": list(promotion.pr_refs),
                    "rollback_refs": list(promotion.rollback_refs),
                    "verification_refs": list(promotion.verification_refs),
                    "extra": dict(metadata or {}),
                },
            )
        )

    patch = "".join(
        difflib.unified_diff(
            source_text.splitlines(keepends=True),
            lines,
            fromfile=f"a/{promotion_plan.target_path}",
            tofile=f"b/{promotion_plan.target_path}",
        )
    )
    return CapabilityProfilePatchPlan(
        target_path=promotion_plan.target_path,
        source_sha256=source_sha256,
        patch=patch,
        records=tuple(records),
        metadata=_metadata(metadata),
    )


def render_capability_profile_patch(plan: CapabilityProfilePatchPlan) -> str:
    """Render patch text only for applyable patch plans."""

    if not plan.allowed_to_apply_patch:
        raise ValueError("cannot render capability profile patch for blocked plan")
    return plan.patch


@dataclass(frozen=True)
class _ProfileLocation:
    capability_id: str
    trust_level: str
    trust_value_line: int
    trust_value_col: int
    trust_value_end_col: int


def _patch_blockers(promotion_plan: CapabilityProfilePromotionPlan, *, source_text: str) -> tuple[str, ...]:
    blockers: list[str] = []
    if not promotion_plan.allowed_to_write_patch:
        blockers.append("promotion_plan_not_applyable")
    blockers.extend(promotion_plan.blockers)
    if not source_text:
        blockers.append("missing_source_text")
    if not promotion_plan.target_path:
        blockers.append("missing_target_path")
    if len(set(promotion_plan.promoted_capability_ids)) != len(promotion_plan.promoted_capability_ids):
        blockers.append("duplicate_promoted_capability_ids")

    try:
        locations = _profile_locations(source_text)
    except (SyntaxError, ValueError):
        blockers.append("invalid_profile_source")
        locations = {}

    for promotion in promotion_plan.records:
        if promotion.target_path != promotion_plan.target_path:
            blockers.append(f"target_path_mismatch:{promotion.capability_id}")
        location = locations.get(promotion.capability_id)
        if location is None:
            blockers.append(f"missing_source_profile:{promotion.capability_id}")
            continue
        if location.trust_level != promotion.from_trust_level:
            blockers.append(f"stale_source_trust_level:{promotion.capability_id}")
    return tuple(dict.fromkeys(blockers))


def _profile_locations(source_text: str) -> dict[str, _ProfileLocation]:
    tree = ast.parse(source_text)
    locations: dict[str, _ProfileLocation] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "CapabilityProfile":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        capability_id = _string_constant(keywords.get("capability_id"))
        trust_level = _string_constant(keywords.get("trust_level"))
        trust_node = keywords.get("trust_level")
        if not capability_id or not trust_level or not isinstance(trust_node, ast.Constant):
            continue
        if capability_id in locations:
            raise ValueError(f"duplicate source capability profile {capability_id!r}")
        locations[capability_id] = _ProfileLocation(
            capability_id=capability_id,
            trust_level=trust_level,
            trust_value_line=trust_node.lineno,
            trust_value_col=trust_node.col_offset,
            trust_value_end_col=trust_node.end_col_offset or trust_node.col_offset,
        )
    return locations


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_constant(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_PATCH_WRITER_SOURCE,
        "extra": dict(metadata or {}),
    }


__all__ = [
    "CAPABILITY_PROFILE_PATCH_WRITER_SOURCE",
    "CapabilityProfilePatchPlan",
    "CapabilityProfilePatchRecord",
    "build_capability_profile_patch_plan",
    "render_capability_profile_patch",
]

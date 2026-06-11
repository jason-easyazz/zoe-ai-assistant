#!/usr/bin/env python3
"""Build admitted memory/trust evidence from a verified capability-profile PR edit.

This operator runner is the next step after capability_profile_pr_edit_workflow.py.
It consumes a reviewed PR-edit plan JSON plus verification traces and emits the
profile-edit outcome plan. By default it is side-effect-free: it does not write
MemoryService, Hindsight, Graphiti, Multica, profile files, or GitHub. Pass
--execute-hindsight to execute the admitted Hindsight retain plan; that mode can
perform a network write to the Hindsight sidecar when enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))

from zoe_capability_profile_edit_outcome import CapabilityProfileEditOutcomePlan, build_capability_profile_edit_outcome_plan  # noqa: E402
from zoe_capability_profile_pr_edit_gate import CapabilityProfilePREditPlan  # noqa: E402
from zoe_memory_router import MemoryBackend  # noqa: E402
from zoe_observation_trace import ObservationTrace  # noqa: E402


def _load_json_file(path: str, *, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"could not read {label} file {path!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} file {path!r} must be valid JSON: {exc}") from exc


def _load_text_file(path: str | None, *, label: str) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"could not read {label} file {path!r}: {exc}") from exc


def _metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--metadata-json must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("--metadata-json must decode to an object")
    return value


def _non_empty(values: Iterable[str] | None) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values or ():
        item = str(value).strip()
        if item:
            refs.append(item)
    return tuple(refs)


def _pr_edit_plan(path: str) -> CapabilityProfilePREditPlan:
    payload = _load_json_file(path, label="PR edit plan")
    if not isinstance(payload, dict):
        raise SystemExit("PR edit plan JSON must decode to an object")
    blockers = _non_empty(payload.get("blockers", ()))
    # Preserve blocked plans as blocked dataclasses: the constructor disallows
    # patch/promoted payloads when blockers exist, so blank those fields before
    # handing them to the outcome gate.
    patch_text = "" if blockers else str(payload.get("patch_text") or "")
    promoted_ids = () if blockers else _non_empty(payload.get("promoted_capability_ids", ()))
    return CapabilityProfilePREditPlan(
        ticket_id=str(payload.get("ticket_id") or ""),
        target_path=str(payload.get("target_path") or ""),
        patch_text=patch_text,
        promoted_capability_ids=promoted_ids,
        pr_refs=_non_empty(payload.get("pr_refs", ())),
        rollback_refs=_non_empty(payload.get("rollback_refs", ())),
        verification_refs=_non_empty(payload.get("verification_refs", ())),
        greptile_refs=_non_empty(payload.get("greptile_refs", ())),
        blockers=blockers,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _trace_payloads(paths: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in paths:
        loaded = _load_json_file(path, label="verification trace")
        if isinstance(loaded, list):
            for item in loaded:
                if not isinstance(item, dict):
                    raise SystemExit(f"verification trace file {path!r} contains a non-object trace")
                payloads.append(item)
        elif isinstance(loaded, dict):
            payloads.append(loaded)
        else:
            raise SystemExit(f"verification trace file {path!r} must decode to an object or list of objects")
    return payloads


def _verification_traces(paths: list[str]) -> tuple[ObservationTrace, ...]:
    traces: list[ObservationTrace] = []
    for index, payload in enumerate(_trace_payloads(paths)):
        try:
            trace = ObservationTrace(**payload)
            trace.validate()
        except TypeError as exc:
            trace_id = payload.get("trace_id") or f"index_{index}"
            raise SystemExit(f"invalid verification trace {trace_id!r}: {exc}") from exc
        except ValueError as exc:
            trace_id = payload.get("trace_id") or f"index_{index}"
            raise SystemExit(f"invalid verification trace {trace_id!r}: {exc}") from exc
        traces.append(trace)
    return tuple(traces)


def build_profile_edit_outcome_plan_from_args(args: argparse.Namespace) -> CapabilityProfileEditOutcomePlan:
    target_backends = _non_empty(args.target_backend) or (MemoryBackend.HINDSIGHT.value, MemoryBackend.GRAPHITI.value)
    return build_capability_profile_edit_outcome_plan(
        _pr_edit_plan(args.pr_edit_plan_json_file),
        verification_traces=_verification_traces(args.verification_trace_file),
        user_id=args.user_id,
        scope=args.scope,
        target_backends=target_backends,
        approval_refs=_non_empty(args.approval_ref),
        admission_id=args.admission_id,
        event_id=args.event_id,
        promotion_manifest=_load_text_file(args.promotion_manifest_file, label="promotion manifest"),
        metadata=_metadata(args.metadata_json),
    )


async def execute_profile_edit_outcome_plan_in_hindsight(
    plan: CapabilityProfileEditOutcomePlan,
    *,
    client: Any | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    if not plan.allowed_to_admit_memory or plan.admission_request is None or plan.admission_decision is None:
        return {
            "attempted": False,
            "retained": False,
            "reason": "profile_edit_outcome_not_admitted",
            "execution": None,
        }
    from hindsight_retain_candidates import HindsightRetainAdmissionError, build_admitted_hindsight_retain_plan
    from hindsight_retain_executor import execute_admitted_hindsight_retain_plan

    resolved_config = config or (client.config if client else None)
    try:
        retain_plan = build_admitted_hindsight_retain_plan(
            plan.admission_request,
            plan.admission_decision,
            config=resolved_config,
        )
    except HindsightRetainAdmissionError as exc:
        return {
            "attempted": False,
            "retained": False,
            "reason": str(exc),
            "execution": None,
        }
    execution = await execute_admitted_hindsight_retain_plan(retain_plan, client=client, config=resolved_config)
    return {
        "attempted": execution.attempted,
        "retained": execution.retained,
        "reason": execution.reason,
        "execution": execution.to_dict(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or execute a memory/trust outcome plan from a verified capability-profile PR edit.",
    )
    parser.add_argument("--pr-edit-plan-json-file", required=True)
    parser.add_argument("--verification-trace-file", action="append", default=[], help="JSON object or list of ObservationTrace objects. May be repeated.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--scope", default="project")
    parser.add_argument("--target-backend", action="append", default=[])
    parser.add_argument("--approval-ref", action="append", default=[])
    parser.add_argument("--admission-id")
    parser.add_argument("--event-id")
    parser.add_argument("--promotion-manifest-file")
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--execute-hindsight", action="store_true", help="Execute the admitted Hindsight retain plan. HindsightConfig still disables writes by default.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = build_profile_edit_outcome_plan_from_args(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise
    payload = plan.to_dict()
    execution = None
    if args.execute_hindsight:
        if plan.blockers:
            execution = {
                "attempted": False,
                "retained": False,
                "reason": "profile_edit_outcome_blocked",
                "execution": None,
            }
        else:
            try:
                execution = asyncio.run(execute_profile_edit_outcome_plan_in_hindsight(plan))
            except Exception as exc:
                execution = {
                    "attempted": False,
                    "retained": False,
                    "reason": "hindsight_execution_error",
                    "error": str(exc),
                    "execution": None,
                }
                payload["hindsight_execution"] = execution
                print(json.dumps(payload, indent=2, sort_keys=True))
                print(f"hindsight execution failed: {exc}", file=sys.stderr)
                return 2
        payload["hindsight_execution"] = execution
    print(json.dumps(payload, indent=2, sort_keys=True))
    if plan.blockers:
        return 1
    if args.execute_hindsight:
        return 0 if execution and execution.get("retained") is True else 1
    return 0 if plan.allowed_to_admit_memory else 1


if __name__ == "__main__":
    raise SystemExit(main())

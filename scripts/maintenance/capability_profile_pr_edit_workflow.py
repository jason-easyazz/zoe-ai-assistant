#!/usr/bin/env python3
"""Prepare capability-profile PR edits from reviewed Multica tickets.

This runner closes the operator workflow around the side-effect-free PR edit
gate. It validates a created ticket, current source, patch, promotion manifest,
and review evidence, then emits a JSON plan. It never applies patches, creates
branches, edits files, or calls GitHub/Multica.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))

from zoe_capability_profile_pr_edit_gate import (  # noqa: E402
    build_capability_profile_pr_edit_plan_from_ticket,
    render_capability_profile_pr_edit_patch,
)


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _non_empty_refs(values: list[str] | None) -> tuple[str, ...]:
    return tuple(value.strip() for value in values or () if value.strip())


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a capability-profile ticket before preparing a PR edit.",
    )
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--ticket-description-file", required=True)
    parser.add_argument("--current-source-file", required=True)
    parser.add_argument("--patch-file", required=True)
    parser.add_argument("--promotion-manifest-file", required=True)
    parser.add_argument("--pr-ref", action="append", default=[])
    parser.add_argument("--rollback-ref", action="append", default=[])
    parser.add_argument("--verification-ref", action="append", default=[])
    parser.add_argument("--greptile-ref", action="append", default=[])
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument(
        "--render-patch",
        action="store_true",
        help="Print the reviewed patch text instead of the JSON plan. Fails closed when blocked.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id=args.ticket_id,
        ticket_description=_read_text(args.ticket_description_file),
        current_source_text=_read_text(args.current_source_file),
        patch_text=_read_text(args.patch_file),
        promotion_manifest=_read_text(args.promotion_manifest_file),
        pr_refs=_non_empty_refs(args.pr_ref),
        rollback_refs=_non_empty_refs(args.rollback_ref),
        verification_refs=_non_empty_refs(args.verification_ref),
        greptile_refs=_non_empty_refs(args.greptile_ref),
        metadata=_metadata(args.metadata_json),
    )
    if args.render_patch:
        try:
            print(render_capability_profile_pr_edit_patch(plan), end="")
        except ValueError as exc:
            print(json.dumps(plan.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    return 0 if plan.allowed_to_prepare_pr_edit else 1


if __name__ == "__main__":
    raise SystemExit(main())

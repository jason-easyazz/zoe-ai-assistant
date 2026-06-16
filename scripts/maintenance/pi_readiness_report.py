#!/usr/bin/env python3
"""Print Zoe's current Pi hybrid promotion readiness report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_readiness_report import pi_readiness_report  # noqa: E402

_BLOCKED_STATES = {"configuration_blocked", "rollback_required"}


def _compact_summary(report: dict) -> dict:
    summary = dict(report.get("summary") or {})
    return {
        "report_kind": report.get("report_kind"),
        "state": report.get("state"),
        "summary": summary,
        "next_actions": list(report.get("next_actions") or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Zoe Pi readiness JSON without side effects")
    parser.add_argument("--output", "-o", help="Optional JSON output path; defaults to stdout")
    parser.add_argument("--summary", action="store_true", help="Print compact summary JSON instead of the full report")
    parser.add_argument(
        "--fail-when-blocked",
        action="store_true",
        help="Exit non-zero when the report says configuration is blocked or rollback is required",
    )
    args = parser.parse_args(argv)

    report = pi_readiness_report()
    payload = _compact_summary(report) if args.summary else report
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    if args.fail_when_blocked and str(report.get("state")) in _BLOCKED_STATES:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

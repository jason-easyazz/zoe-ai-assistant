#!/usr/bin/env python3
"""Print Zoe's current Pi hybrid promotion readiness report."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_readiness_report import pi_readiness_report  # noqa: E402

DEFAULT_ENV_FILES = (
    ROOT / ".env",
    ROOT / "services" / "zoe-data" / ".env",
    Path("/home/zoe/assistant/.env"),
    Path("/home/zoe/assistant/services/zoe-data/.env"),
)
_BLOCKED_STATES = {"configuration_blocked", "rollback_required"}


def load_zoe_env(env_files: Iterable[str | Path] = DEFAULT_ENV_FILES) -> dict[str, str]:
    """Load Zoe env files for operator reports without mutating os.environ."""
    values: dict[str, str] = {}
    for env_file in env_files:
        path = Path(env_file).expanduser()
        if path.exists():
            values.update(_parse_env_file(path))
    values.update(os.environ)
    return values


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key.startswith("#"):
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            parts = [raw_value.strip().strip("\"").strip("'")]
        parsed[key] = parts[0] if parts else ""
    return parsed


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
    parser.add_argument(
        "--env-file",
        action="append",
        default=None,
        help="Additional env file to load after Zoe defaults; may be repeated",
    )
    args = parser.parse_args(argv)

    env_files = [*DEFAULT_ENV_FILES, *(args.env_file or [])]
    report = pi_readiness_report(load_zoe_env(env_files))
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

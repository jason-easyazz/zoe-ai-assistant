#!/usr/bin/env python3
"""Plan or apply Pi promoted intent groups from a promotion report.

Default mode is dry-run and side-effect free. Applying requires both --apply and
--confirm APPLY_PI_PROMOTION, and only writes ZOE_PI_INTENT_PROMOTED_GROUPS to the
specified env file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

PROMOTION_ENV_KEY = "ZOE_PI_INTENT_PROMOTED_GROUPS"
CONFIRM_TOKEN = "APPLY_PI_PROMOTION"


class PiPromotionApplyError(ValueError):
    pass


def load_promotion_report(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise PiPromotionApplyError("promotion report JSON must be an object")
    report = data.get("promotion_report", data)
    if not isinstance(report, dict):
        raise PiPromotionApplyError("promotion_report must be an object")
    return report


def promotion_env_value(report: Mapping[str, Any]) -> str:
    actions = report.get("promotion_actions")
    if not isinstance(actions, Mapping):
        raise PiPromotionApplyError("promotion_actions missing from report")
    env = actions.get("env")
    if not isinstance(env, Mapping):
        raise PiPromotionApplyError("promotion_actions.env missing from report")
    extra_keys = sorted(set(env) - {PROMOTION_ENV_KEY})
    if extra_keys:
        raise PiPromotionApplyError(f"unsupported promotion env keys: {', '.join(extra_keys)}")
    value = env.get(PROMOTION_ENV_KEY)
    if value is None:
        raise PiPromotionApplyError(f"{PROMOTION_ENV_KEY} missing from promotion_actions.env")
    return _normalize_group_value(str(value))


def build_env_update(existing_text: str, value: str) -> tuple[str, dict[str, Any]]:
    normalized_value = _normalize_group_value(value)
    lines = existing_text.splitlines()
    changed = False
    found = False
    old_value: str | None = None
    next_lines: list[str] = []
    for line in lines:
        if _line_key(line) == PROMOTION_ENV_KEY:
            found = True
            old_value = line.split("=", 1)[1].strip().strip('"').strip("'") if "=" in line else ""
            replacement = f"{PROMOTION_ENV_KEY}={normalized_value}"
            next_lines.append(replacement)
            changed = changed or line != replacement
        else:
            next_lines.append(line)
    if not found:
        next_lines.append(f"{PROMOTION_ENV_KEY}={normalized_value}")
        changed = True
    output = "\n".join(next_lines).rstrip("\n") + "\n"
    return output, {
        "key": PROMOTION_ENV_KEY,
        "old_value": old_value,
        "next_value": normalized_value,
        "line_present": found,
        "changed": changed,
    }


def apply_pi_promotion_report(
    report: Mapping[str, Any],
    env_path: Path,
    *,
    apply_changes: bool,
    confirm: str | None,
) -> dict[str, Any]:
    next_value = promotion_env_value(report)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    updated, update = build_env_update(existing, next_value)
    actions = report.get("promotion_actions") if isinstance(report.get("promotion_actions"), Mapping) else {}
    if not apply_changes:
        return {
            "ok": True,
            "mode": "dry_run",
            "env_file": str(env_path),
            "update": update,
            "promotion_actions": actions,
        }
    if confirm != CONFIRM_TOKEN:
        return {
            "ok": False,
            "mode": "apply_rejected",
            "env_file": str(env_path),
            "error": f"--confirm {CONFIRM_TOKEN} is required with --apply",
            "update": update,
            "promotion_actions": actions,
        }
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(updated, encoding="utf-8")
    return {
        "ok": True,
        "mode": "applied",
        "env_file": str(env_path),
        "update": update,
        "promotion_actions": actions,
    }


def _line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def _normalize_group_value(value: str) -> str:
    groups = sorted({item.strip() for item in value.split(",") if item.strip()})
    return ",".join(groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="-", help="Promotion report JSON path, or - for stdin")
    parser.add_argument("--env-file", required=True, help="Env file to plan/update")
    parser.add_argument("--apply", action="store_true", help="Write the env-file update")
    parser.add_argument("--confirm", help=f"Required with --apply: {CONFIRM_TOKEN}")
    args = parser.parse_args(argv)
    try:
        report = load_promotion_report(args.report)
        result = apply_pi_promotion_report(report, Path(args.env_file), apply_changes=args.apply, confirm=args.confirm)
    except (OSError, json.JSONDecodeError, PiPromotionApplyError) as exc:
        result = {"ok": False, "mode": "error", "error": str(exc), "env_file": args.env_file}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

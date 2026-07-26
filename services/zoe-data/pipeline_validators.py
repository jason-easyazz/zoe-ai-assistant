"""Run repo audit validators and return structured pipeline evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from repo_paths import zoe_repo_root
from pipeline_evidence import EvidenceItem, content_hash

_VALIDATORS = (
    ("validate_structure.py", "python3 tools/audit/validate_structure.py"),
    ("validate_critical_files.py", "python3 tools/audit/validate_critical_files.py"),
)
_SNIPPET_MAX = 400


@dataclass(frozen=True)
class ValidatorRunResult:
    exit_code: int
    summary: str
    content_hash: str
    passed: bool


def _run_one(command: str, *, cwd: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    combined = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, combined.strip()


def run_repo_validators(*, repo_root: str | None = None) -> ValidatorRunResult:
    """Run validate_structure + validate_critical_files; return aggregate result."""
    root = repo_root or zoe_repo_root()
    parts: list[str] = []
    exit_code = 0
    for label, command in _VALIDATORS:
        code, output = _run_one(command, cwd=root)
        tail = output[-_SNIPPET_MAX:] if output else "(no output)"
        parts.append(f"{label}: exit {code}\n{tail}")
        if code != 0:
            exit_code = code
    summary = "\n---\n".join(parts)
    return ValidatorRunResult(
        exit_code=exit_code,
        summary=summary[:500],
        content_hash=content_hash(summary),
        passed=exit_code == 0,
    )


def validator_evidence_item(
    result: ValidatorRunResult,
    *,
    source: str = "harness",
    phase: str | None = None,
) -> EvidenceItem:
    meta: dict[str, object] = {"source": source}
    if phase:
        meta["phase"] = phase
    return EvidenceItem(
        kind="validator",
        summary=result.summary[:500],
        content_hash=result.content_hash,
        passed=result.passed,
        metadata=meta,
    )

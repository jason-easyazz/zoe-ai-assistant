"""Produce provenance-bound cross-review evidence for the Multica pipeline.

This module is deliberately not wired into ``pipeline_store``.  Its public
producer is flag-dark by default and accepts an injectable dispatch function so
unit tests (and future callers) never need to launch a live reviewer.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, TypedDict

from pipeline_evidence import EvidenceItem
from repo_paths import zoe_repo_root

_CROSS_REVIEW_REL = "scripts/maintenance/cross_review.sh"
_CROSS_REVIEW_TIMEOUT_S = 2500
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_VENDOR_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude_code": "anthropic",
    "codex": "openai",
    "chatgpt": "openai",
    "openai": "openai",
}


class CrossReviewVerdict(TypedDict):
    reviewer_vendor: str
    verdict: Literal["approve", "blocking"]
    reviewed_sha: str
    blocking_issues: list[Any]


@dataclass(frozen=True)
class CrossReviewRequest:
    acceptance_contract: str
    implementer_vendor: str
    head_sha: str
    pr_number: int | None = None
    diff_ref: str | None = None


ReviewDispatch = Callable[[CrossReviewRequest], object]


def cross_review_enabled() -> bool:
    """Return the per-call state of the default-OFF producer feature flag."""
    return os.getenv("ZOE_MULTICA_CROSS_REVIEW", "false").strip().lower() in _TRUE_VALUES


def _vendor_identity(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return _VENDOR_ALIASES.get(normalized, normalized)


def dispatch_via_cross_review_script(request: CrossReviewRequest) -> object:
    """Dispatch through the repository's existing polly cross-review wrapper.

    The wrapper currently addresses reviews by PR number. A non-PR diff ref is
    therefore refused rather than sent through a second, invented dispatch path.
    Stdout is returned verbatim for the strict machine-result parser below; the
    producer never infers a verdict from natural-language review prose.
    """
    if request.pr_number is None:
        return None
    script = os.path.join(zoe_repo_root(), _CROSS_REVIEW_REL)
    try:
        proc = subprocess.run(
            [script, str(request.pr_number), request.acceptance_contract],
            cwd=zoe_repo_root(),
            capture_output=True,
            text=True,
            timeout=_CROSS_REVIEW_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _machine_mapping(result: object) -> Mapping[str, Any] | None:
    if isinstance(result, Mapping):
        return result
    if not isinstance(result, (str, bytes, bytearray)):
        return None
    try:
        decoded = json.loads(result)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def parse_machine_verdict(result: object) -> CrossReviewVerdict | None:
    """Parse only an explicit machine payload; never classify reviewer prose."""
    data = _machine_mapping(result)
    if data is None:
        return None

    reviewer_vendor = data.get("reviewer_vendor")
    verdict = data.get("verdict")
    reviewed_sha = data.get("reviewed_sha")
    blocking_issues = data.get("blocking_issues")
    confidence = data.get("confidence")
    required_strings = (reviewer_vendor, verdict, reviewed_sha)
    if not all(isinstance(value, str) and value.strip() for value in required_strings):
        return None
    reviewer_vendor = _vendor_identity(reviewer_vendor)
    verdict = verdict.strip().lower()
    reviewed_sha = reviewed_sha.strip().lower()
    if verdict not in {"approve", "blocking"} or not isinstance(blocking_issues, list):
        return None
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or confidence < 0.8
        or confidence > 1.0
    ):
        return None
    if verdict == "approve" and blocking_issues:
        return None
    return {
        "reviewer_vendor": reviewer_vendor,
        "verdict": verdict,
        "reviewed_sha": reviewed_sha,
        "blocking_issues": blocking_issues,
    }


def produce_cross_review_evidence(
    *,
    acceptance_contract: str,
    implementer_vendor: str,
    head_sha: str,
    pr_number: int | None = None,
    diff_ref: str | None = None,
    dispatch: ReviewDispatch = dispatch_via_cross_review_script,
) -> EvidenceItem | None:
    """Dispatch a cross-review and return valid approving evidence, else ``None``.

    The default-OFF ``ZOE_MULTICA_CROSS_REVIEW`` flag is read at call time.
    """
    if not cross_review_enabled():
        return None

    contract = (acceptance_contract or "").strip()
    implementer = _vendor_identity(implementer_vendor or "")
    expected_sha = (head_sha or "").strip().lower()
    ref = (diff_ref or "").strip() or None
    if not contract or not implementer or not _SHA_RE.fullmatch(expected_sha):
        return None
    if pr_number is None and ref is None:
        return None
    invalid_pr = isinstance(pr_number, bool) or (
        pr_number is not None and (not isinstance(pr_number, int) or pr_number <= 0)
    )
    if invalid_pr:
        return None

    request = CrossReviewRequest(
        acceptance_contract=contract,
        implementer_vendor=implementer,
        head_sha=expected_sha,
        pr_number=pr_number,
        diff_ref=ref,
    )
    try:
        verdict = parse_machine_verdict(dispatch(request))
    except Exception:
        return None
    if verdict is None:
        return None
    if verdict["reviewer_vendor"] == implementer:
        return None
    if verdict["reviewed_sha"] != expected_sha:
        return None
    if verdict["verdict"] != "approve":
        return None

    reviewer = verdict["reviewer_vendor"]
    return EvidenceItem(
        kind="cross_review",
        summary=f"{reviewer} cross-review approved the diff at {expected_sha[:12]}",
        passed=True,
        artifact=json.dumps(verdict, sort_keys=True, separators=(",", ":")),
        metadata={
            "reviewer": reviewer,
            "vendor": reviewer,
            "verdict": "approve",
            "commit_sha": expected_sha,
            "reviewed_sha": expected_sha,
        },
    )

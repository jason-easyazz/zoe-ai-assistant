"""Promote a finished autoresearch run into the governed engineering pipeline.

An autoresearch run (see ``skills/autoresearch-engineer``) optimizes ONE approved
asset against ONE locked score on a throwaway ``autoresearch/<tag>`` branch,
logging each round to ``results.tsv``. When a run nets a real improvement, its
kept asset edits should not be silently committed to production — they must go
through the SAME review gate as any other change (Greptile + the harness).

This module is the bridge. It is intentionally side-effect free at import time:
pure functions parse the run, decide whether it earned promotion, and build the
two governed artifacts —

  1. a PR body describing the validated improvement (the diff still goes through
     Greptile review and required checks before merge), and
  2. an OPTIONAL Multica *audit record* — a non-dispatchable ticket that documents
     the run. It is deliberately built without ``dispatch_approved`` and with empty
     acceptance/evidence so ``multica_admission.ticket_is_dispatch_approved`` fails
     closed: an audit record can never be picked up as a work order.

The thin ``__main__`` CLI defaults to a dry run that prints the promotion plan as
JSON; emitting the audit ticket is opt-in behind ``--emit-audit``.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from multica_ticket_contract import describe_ticket

# Result-log status vocabulary (mirrors the autoresearch skill).
STATUS_BASELINE = "baseline"
STATUS_KEEP = "keep"
STATUS_DISCARD = "discard"
STATUS_CRASH = "crash"
STATUS_BLOCKED = "blocked"

AUDIT_ZOE_KIND = "autoresearch_audit"
AUDIT_SOURCE_PREFIX = "autoresearch_audit"


@dataclass
class Program:
    """Parsed, human-owned ``program.md`` contract for one autoresearch run."""

    goal: str = ""
    why: str = ""
    asset_paths: list[str] = field(default_factory=list)
    scoring_command: str = ""
    higher_is_better: bool = True
    target_score: float | None = None
    stop_condition: str = ""
    model: str | None = None


@dataclass
class RunSummary:
    """Derived outcome of a run's ``results.tsv`` rounds."""

    rounds: int = 0
    baseline_score: float | None = None
    final_score: float | None = None
    best_score: float | None = None
    kept: int = 0
    discarded: int = 0
    crashed: int = 0
    blocked: int = 0
    improved: bool = False
    net_improvement: float | None = None
    best_hypothesis: str = ""
    final_hypothesis: str = ""
    keeper_commits: list[str] = field(default_factory=list)


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_program(text: str | None) -> Program:
    """Parse the conventions used in ``program.md`` into a typed contract.

    Recognized prose lines (case-insensitive, leading list markers tolerated):
      Goal: ... / Why: ... / Editable asset allowlist: a.py, b.md only.
      Locked scoring command: <cmd> / Higher|Lower (score) is better.
      Stop condition: ... / Model: <provider/name>  (also "Run model:")
    """
    text = text or ""
    program = Program()
    program.goal = _first(r"^\s*[-*]?\s*Goal\s*:\s*(.+)$", text)
    program.why = _first(r"^\s*[-*]?\s*Why\s*:\s*(.+)$", text)
    program.scoring_command = _first(
        r"^\s*[-*]?\s*(?:Locked\s+)?scoring\s+command\s*:\s*(.+)$", text
    )
    program.stop_condition = _first(r"^\s*[-*]?\s*Stop\s+condition\s*:\s*(.+)$", text)

    # Direction: default higher-is-better unless the program says lower.
    if re.search(r"\blower\b[^.\n]*\bis\s+better\b", text, re.IGNORECASE):
        program.higher_is_better = False
    elif re.search(r"\bhigher\b[^.\n]*\bis\s+better\b", text, re.IGNORECASE):
        program.higher_is_better = True

    target_raw = _first(
        r"^\s*[-*]?\s*Target(?:\s+score)?\s*:\s*([-+]?\d+(?:\.\d+)?)", text
    )
    program.target_score = _to_float(target_raw) if target_raw else None

    # Optional model knob — the human picks which model runs the loop.
    program.model = (
        _first(r"^\s*[-*]?\s*(?:Run\s+)?model\s*:\s*(.+)$", text) or None
    )

    allowlist = _first(
        r"^\s*[-*]?\s*Editable\s+asset\s+allowlist\s*:\s*(.+)$", text
    )
    if allowlist:
        program.asset_paths = _split_asset_paths(allowlist)
    return program


def _split_asset_paths(raw: str) -> list[str]:
    # Strip a trailing "only." qualifier and split on commas / "and" / whitespace.
    cleaned = re.sub(r"\bonly\b\.?\s*$", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(". ")
    parts = re.split(r"\s*,\s*|\s+and\s+", cleaned)
    paths: list[str] = []
    for part in parts:
        token = part.strip().strip("`").rstrip(".")
        if token and token not in paths:
            paths.append(token)
    return paths


def parse_results(text: str | None) -> list[dict[str, str]]:
    """Parse the TSV result log into ordered row dicts."""
    text = text or ""
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


def _is_better(candidate: float, incumbent: float, higher_is_better: bool) -> bool:
    return candidate > incumbent if higher_is_better else candidate < incumbent


def summarize_run(rows: list[dict[str, str]], *, higher_is_better: bool = True) -> RunSummary:
    """Reduce result rounds to a promotion-ready outcome summary."""
    summary = RunSummary(rounds=len(rows))
    if not rows:
        return summary

    for row in rows:
        status = (row.get("status") or "").lower()
        if status == STATUS_KEEP:
            summary.kept += 1
            commit = row.get("commit") or ""
            if commit:
                summary.keeper_commits.append(commit)
        elif status == STATUS_DISCARD:
            summary.discarded += 1
        elif status == STATUS_CRASH:
            summary.crashed += 1
        elif status == STATUS_BLOCKED:
            summary.blocked += 1

    baseline_row = next(
        (r for r in rows if (r.get("status") or "").lower() == STATUS_BASELINE), None
    )
    summary.baseline_score = _to_float((baseline_row or {}).get("score"))

    # best_score  = best observed across baseline + kept rounds (informational).
    # final_score = score of the LAST kept round. Because worse rounds are reverted,
    # the run branch ends at the last kept commit, so final_score is the asset's
    # actual accumulated state — and that, not best-ever, is what gets promoted.
    best_score: float | None = summary.baseline_score
    final_score: float | None = summary.baseline_score
    best_hypothesis = ""
    final_hypothesis = ""
    for row in rows:
        status = (row.get("status") or "").lower()
        if status not in {STATUS_BASELINE, STATUS_KEEP}:
            continue
        score = _to_float(row.get("score"))
        if score is None:
            continue
        if best_score is None or _is_better(score, best_score, higher_is_better):
            best_score = score
            if status == STATUS_KEEP:
                best_hypothesis = row.get("description") or ""
        if status == STATUS_KEEP:
            final_score = score
            # The last kept round is the actual promoted end state, so its
            # description is what a reviewer should read to gauge the diff.
            final_hypothesis = row.get("description") or ""
    summary.best_score = best_score
    summary.final_score = final_score
    summary.best_hypothesis = best_hypothesis
    summary.final_hypothesis = final_hypothesis

    # Promotion keys off the committed end state (final_score), so a run that
    # regresses below baseline by its last kept round is never sold as a win.
    if (
        summary.baseline_score is not None
        and final_score is not None
        and _is_better(final_score, summary.baseline_score, higher_is_better)
    ):
        summary.improved = True
        raw_delta = round(final_score - summary.baseline_score, 6)
        # Normalize so an improvement is always reported as a positive magnitude,
        # regardless of whether higher or lower is better.
        summary.net_improvement = raw_delta if higher_is_better else -raw_delta
    return summary


def decide_promotion(program: Program, summary: RunSummary) -> dict[str, Any]:
    """Decide whether a finished run earned a governed PR.

    A run is promotable only when it produced at least one kept round AND a real
    net improvement over baseline in the locked direction. Everything else (no
    keepers, no improvement, only crashes/blocks) stays a no-op so a flat or
    regressed run never opens a PR.
    """
    if summary.rounds == 0:
        return {"promote": False, "reason": "no recorded rounds"}
    if summary.baseline_score is None:
        return {"promote": False, "reason": "no baseline score recorded"}
    if summary.kept == 0:
        return {"promote": False, "reason": "no kept rounds — nothing improved"}
    if not summary.improved:
        return {
            "promote": False,
            "reason": "no net improvement over baseline in the locked direction",
        }
    if not program.asset_paths:
        return {
            "promote": False,
            "reason": "program declares no asset allowlist to promote",
        }
    return {
        "promote": True,
        "reason": (
            f"net improvement {summary.net_improvement} "
            f"({summary.baseline_score} -> {summary.final_score}) across "
            f"{summary.kept} kept round(s)"
        ),
    }


def _direction_word(higher_is_better: bool) -> str:
    return "higher-is-better" if higher_is_better else "lower-is-better"


def build_pr_body(run_id: str, program: Program, summary: RunSummary) -> str:
    """Render a governed PR description for a promotable run.

    The diff this body accompanies still passes through Greptile review and all
    required status checks before merge — promotion does not bypass the gate.
    """
    paths = "\n".join(f"- `{p}`" for p in program.asset_paths) or "- (none declared)"
    lines = [
        f"## Autoresearch promotion — run `{run_id}`",
        "",
        "Validated keeper(s) from an autoresearch optimization loop. This PR goes "
        "through the standard review gate (Greptile + required checks); it is not "
        "auto-merged outside that gate.",
        "",
        f"**Goal:** {program.goal or '(unstated)'}",
        f"**Why:** {program.why or '(unstated)'}",
        f"**Model:** {program.model or '(session default)'}",
        f"**Scoring:** `{program.scoring_command or '(unstated)'}` "
        f"({_direction_word(program.higher_is_better)})",
        "",
        "### Result",
        f"- Baseline score: {summary.baseline_score}",
        f"- Final score: {summary.final_score}",
        f"- Net improvement: {summary.net_improvement}",
        f"- Rounds: {summary.rounds} (kept {summary.kept}, discarded "
        f"{summary.discarded}, crashed {summary.crashed}, blocked {summary.blocked})",
        f"- Promoted change (last kept round): "
        f"{summary.final_hypothesis or '(n/a)'}",
        f"- Best-scoring round (may be an intermediate, reverted state): "
        f"{summary.best_hypothesis or '(n/a)'}",
        "",
        "### Asset allowlist (only files this run was permitted to change)",
        paths,
        "",
        "### Review checklist",
        "- [ ] Diff is limited to the declared asset allowlist",
        "- [ ] Improvement is real, not a scorer/goalpost change",
        "- [ ] Greptile review passes at the required confidence",
    ]
    return "\n".join(lines)


def build_audit_ticket(
    run_id: str,
    program: Program,
    summary: RunSummary,
    *,
    pr_url: str | None = None,
) -> str:
    """Build a NON-dispatchable Multica audit record for a run.

    The returned description is deliberately missing ``dispatch_approved`` and has
    empty acceptance/evidence, so ``ticket_is_dispatch_approved`` fails closed and
    the admission gate can never pick this audit record up as a work order.
    """
    prose_lines = [
        f"Autoresearch run `{run_id}` audit record (not a work order).",
        "",
        f"Goal: {program.goal or '(unstated)'}",
        f"Model: {program.model or '(session default)'}",
        f"Scoring: {program.scoring_command or '(unstated)'} "
        f"({_direction_word(program.higher_is_better)})",
        f"Baseline {summary.baseline_score} -> final {summary.final_score} "
        f"(net {summary.net_improvement}).",
        f"Rounds: {summary.rounds}; kept {summary.kept}, discarded "
        f"{summary.discarded}, crashed {summary.crashed}, blocked {summary.blocked}.",
        f"Promoted change (last kept round): {summary.final_hypothesis or '(n/a)'}.",
        f"Best-scoring round (may be reverted): {summary.best_hypothesis or '(n/a)'}.",
        f"Assets: {', '.join(program.asset_paths) or '(none)'}.",
    ]
    if pr_url:
        prose_lines.append(f"Governed PR: {pr_url}")

    return describe_ticket(
        "\n".join(prose_lines),
        zoe_kind=AUDIT_ZOE_KIND,
        evidence_profile="code",
        engineering_mode="interactive",
        acceptance_criteria=[],
        evidence_expectations=[],
        source=f"{AUDIT_SOURCE_PREFIX}:{run_id}",
        metadata={
            "autoresearch_run_id": run_id,
            "autoresearch_model": program.model,
            "autoresearch_baseline_score": summary.baseline_score,
            "autoresearch_final_score": summary.final_score,
            "autoresearch_net_improvement": summary.net_improvement,
            "autoresearch_kept_rounds": summary.kept,
            "autoresearch_keeper_commits": summary.keeper_commits,
            "autoresearch_pr_url": pr_url,
        },
    )


def _read_run_files(run_dir: Path) -> tuple[str, str]:
    program_text = ""
    results_text = ""
    program_path = run_dir / "program.md"
    results_path = run_dir / "results.tsv"
    if program_path.exists():
        program_text = program_path.read_text(encoding="utf-8")
    if results_path.exists():
        results_text = results_path.read_text(encoding="utf-8")
    return program_text, results_text


def prepare_promotion(run_dir: str | Path, *, pr_url: str | None = None) -> dict[str, Any]:
    """Read a run directory and return a complete, side-effect-free promotion plan."""
    run_dir = Path(run_dir)
    run_id = run_dir.name
    program_text, results_text = _read_run_files(run_dir)
    program = parse_program(program_text)
    summary = summarize_run(parse_results(results_text), higher_is_better=program.higher_is_better)
    decision = decide_promotion(program, summary)

    plan: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "model": program.model,
        "promote": decision["promote"],
        "reason": decision["reason"],
        "program": asdict(program),
        "summary": asdict(summary),
    }
    if decision["promote"]:
        plan["pr_title"] = f"autoresearch({run_id}): promote validated keeper"
        plan["pr_body"] = build_pr_body(run_id, program, summary)
        plan["audit_description"] = build_audit_ticket(
            run_id, program, summary, pr_url=pr_url
        )
    return plan


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Autoresearch promotion bridge")
    parser.add_argument("run_dir", help="Path to data/autoresearch/<run>")
    parser.add_argument("--pr-url", default=None, help="Governed PR URL to record")
    parser.add_argument(
        "--emit-audit",
        action="store_true",
        help="Create the Multica audit record (default: dry run, plan only)",
    )
    args = parser.parse_args(argv)

    plan = prepare_promotion(args.run_dir, pr_url=args.pr_url)
    print(json.dumps(plan, indent=2, default=str))

    if args.emit_audit and plan.get("promote"):
        import asyncio

        from multica_client import get_multica_client

        async def _emit() -> dict[str, Any]:
            client = get_multica_client()
            return await client.create_issue(
                title=plan["pr_title"],
                description=plan["audit_description"],
                status="backlog",
            )

        result = asyncio.run(_emit())
        print(json.dumps({"audit_ticket": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

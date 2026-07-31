#!/usr/bin/env python3
"""Voice replay-gate heartbeat check — the deploy-path assertion.

"A gate that can silently not-run is not a gate." The voice replay gate
(scripts/maintenance/voice_regression_probe.py) writes a durable result artifact
on EVERY run (pass/fail/skip/error). This checker is the cheap counterpart the
deploy path invokes: before a change that touches the VOICE RUNTIME PATH is
allowed to go live, it asserts that a FRESH, PASSING gate artifact exists — the
same shape as the router self-train ratchet's `replay_gate_passed` check (a skip
is NOT a pass; an absent artifact is NOT a pass).

This checker NEVER runs the heavy Kokoro/replay harness itself (two ~2.3 GB
loads would OOM the box). It only reads the artifact the gate produced. If that
artifact is missing, stale, from a different baseline, or status != "pass", it
fails LOUDLY (non-zero exit + a clear message) so a voice-path deploy cannot
proceed on an unproven voice path.

PR-TIME wiring (.github/workflows/voice-gate.yml — the REQUIRED `voice-gate`
context): the same assertion runs BEFORE merge, not only at deploy. Without it
a voice-path PR could merge green and then be permanently refused by the deploy
gate — a "green main that will not deploy". The PR lane splits into two calls:
`--scope-only` classifies the PR diff on a hosted runner (always exit 0), and
only a VOICE verdict escalates to `--require` on the self-hosted Jetson, where
the artifact actually lives.

Deploy wiring: BOTH deploy paths call this with the incoming git range, between
the fetch and the tree-advance — the manual scripts/maintenance/deploy_live.sh
AND the continuous-deploy runner (.github/workflows/deploy.yml), which is how
changes actually reach the box. If that range changes no voice-path files the
check is a no-op pass (so ordinary non-voice deploys are frictionless). A block
on the CD path is fail-closed and keeps blocking until the probe is re-run; the
unwedge procedure is in docs/knowledge/merge-and-deploy.md. See
docs/knowledge/voice-pipeline.md for the artifact contract.

Exit codes: 0 = allowed (fresh pass, or no voice-path change); 1 = blocked.
`--scope-only` is the exception: it classifies and ALWAYS exits 0.

Examples:
    # deploy path: gate only if the incoming diff touches the voice runtime
    voice_gate_check.py --repo /home/zoe/assistant --diff HEAD..FETCH_HEAD
    # PR path, step 1: classify only (never blocks, never reads the artifact)
    voice_gate_check.py --scope-only --diff origin/main...HEAD
    # force the assertion regardless of any diff
    voice_gate_check.py --require
"""
from __future__ import annotations

import argparse
import calendar
import fnmatch
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
DEFAULT_BASELINE = Path.home() / ".cache" / "zoe" / "voice_regression_baseline.json"
# The voice RUNTIME path (STT / brain / TTS) whose changes MUST be replay-gated
# before deploy — mirrors the mandate in the root AGENTS.md / CLAUDE.md. Matched
# with fnmatch against repo-relative paths; override via ZOE_VOICE_GATE_PATHS
# (a ':'-separated glob list).
VOICE_PATH_PATTERNS = (
    "services/zoe-data/routers/voice_tts.py",
    "services/zoe-data/zoe_core_client.py",
    "services/zoe-data/fast_tiers.py",
    # W11 expressive delivery: the mapper decides per-reply TTS speed and the
    # waterfall applies it — both change what Zoe SOUNDS like. Codex P1 on
    # #1579: a deploy touching only these bypassed the gate entirely, and that
    # PR was itself such a deploy.
    "services/zoe-data/voice_delivery.py",
    "services/zoe-data/tts_waterfall.py",
    "*kokoro*",
    "*moonshine*",
    # The brain's serving config IS the voice path: flash-attn, KV format and
    # slot layout all change generation behaviour. Greptile P1 on #1494 — a
    # deploy touching this unit previously bypassed the gate entirely.
    "scripts/setup/systemd/llama-server.service",
)
DEFAULT_MAX_AGE_H = float(os.environ.get("ZOE_VOICE_GATE_MAX_AGE_H", "24"))


def voice_path_patterns() -> tuple[str, ...]:
    override = os.environ.get("ZOE_VOICE_GATE_PATHS", "").strip()
    if override:
        return tuple(p for p in override.split(":") if p)
    return VOICE_PATH_PATTERNS


def touched_voice_files(changed: list[str], patterns: tuple[str, ...]) -> list[str]:
    """Subset of `changed` that matches any voice-path glob."""
    hits = []
    for f in changed:
        if any(fnmatch.fnmatch(f, pat) for pat in patterns):
            hits.append(f)
    return hits


def parse_iso_z(ts: str | None) -> float | None:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' → UTC epoch seconds. None if unparseable.

    Uses calendar.timegm (the struct_time is UTC) so the age is correct
    regardless of the host's local timezone or DST — the probe writes the
    timestamp with time.gmtime()."""
    if not ts or not isinstance(ts, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return float(calendar.timegm(time.strptime(ts, fmt)))
        except (ValueError, OverflowError):
            continue
    return None


def revision_matches(artifact: dict[str, Any], expect_revision: str | None) -> tuple[bool, str]:
    """The gate result must come from exercising THIS revision.

    Freshness + status do not bind an artifact to the code under review. A passing
    run against `main`, or against an unrelated earlier commit, is fresh and
    passing — and would clear every voice PR for the whole freshness window. That
    is evidence for some OTHER code presented as evidence for this one.

    When `expect_revision` is supplied (the PR gate always supplies it) the
    artifact must carry `revision.commit` equal to it, and must not have come from
    a DIRTY tree: an uncommitted worktree cannot be attributed to a commit, so the
    recorded sha would be a claim about code that was not the code that ran."""
    if not expect_revision:
        return True, ""
    rev = artifact.get("revision")
    if not isinstance(rev, dict) or not rev.get("commit"):
        return False, ("voice replay-gate artifact records NO revision, so it cannot be "
                       f"attributed to {expect_revision[:8]} — re-run the probe against a "
                       "checkout of this PR's head")
    if rev.get("dirty"):
        return False, (f"voice replay-gate ran against a DIRTY worktree at "
                       f"{str(rev.get('commit'))[:8]} — an uncommitted tree cannot be "
                       "attributed to a commit; commit or stash, then re-run the probe")
    got = str(rev.get("commit"))
    if got != expect_revision:
        return False, (f"voice replay-gate ran against commit {got[:8]}, but this PR's head "
                       f"is {expect_revision[:8]} — that artifact is evidence for DIFFERENT "
                       "code. Re-run the probe against a checkout of this PR's head.")
    return True, ""


def baseline_matches(artifact: dict[str, Any], baseline: dict[str, Any] | None) -> tuple[bool, str]:
    """The gate result must have been produced against the CURRENT baseline.

    If the baseline was refreshed after the last gate run, the result is stale
    relative to the bar and must not clear a deploy. Lenient when the baseline
    (or its identity) is unavailable — this check can only tighten, never invent
    a mismatch out of missing data."""
    if not isinstance(baseline, dict):
        return True, ""
    base_created = baseline.get("created_at")
    if not base_created:
        return True, ""
    ref = artifact.get("baseline_ref") or {}
    ran_against = ref.get("created_at") if isinstance(ref, dict) else None
    if ran_against and ran_against != base_created:
        return False, (f"gate ran against baseline created_at={ran_against!r} but the "
                       f"current baseline is {base_created!r} — the bar moved; re-run "
                       "the voice replay gate against the current baseline")
    return True, ""


def evaluate(artifact: dict[str, Any] | None, *, now_epoch: float, max_age_s: float,
             baseline: dict[str, Any] | None = None,
             expect_revision: str | None = None) -> tuple[bool, str]:
    """The heartbeat check, pure and unit-testable. Returns (allowed, reason).

    Blocks unless the artifact exists, has status == "pass", is fresh (within
    max_age_s), was produced against the current baseline, and — when
    `expect_revision` is given — was produced by exercising that exact revision."""
    if artifact is None:
        return False, ("no voice replay-gate result artifact — the gate never ran "
                       "(a missing artifact is NOT a pass)")
    status = artifact.get("status")
    if status != "pass":
        return False, (f"voice replay-gate status={status!r} "
                       f"(reason: {artifact.get('reason', '') or 'n/a'}) — "
                       "skip/fail/error is NOT a pass")
    ts = artifact.get("timestamp") or artifact.get("created_at")
    produced = parse_iso_z(ts)
    if produced is None:
        return False, f"voice replay-gate artifact has no parseable timestamp ({ts!r})"
    age_s = now_epoch - produced
    if age_s > max_age_s:
        return False, (f"voice replay-gate result is STALE: {age_s / 3600:.1f}h old > "
                       f"{max_age_s / 3600:.1f}h freshness window — re-run the gate "
                       "before deploying a voice-path change")
    ok, why = baseline_matches(artifact, baseline)
    if not ok:
        return False, why
    ok, why = revision_matches(artifact, expect_revision)
    if not ok:
        return False, why
    bound = ""
    if expect_revision:
        bound = f", bound to {expect_revision[:8]}"
    return True, (f"voice replay-gate PASS ({age_s / 3600:.1f}h old, "
                  f"n={((artifact.get('summary') or {}).get('n_samples'))}{bound})")


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def git_changed_files(repo: Path, diff_range: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", diff_range],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git diff {diff_range} failed")
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def scope_verdict(changed: list[str] | None, patterns: tuple[str, ...]) -> tuple[bool, list[str], str]:
    """Classify a diff. Returns (needs_gate, hits, reason). Pure + unit-testable.

    `changed is None` means the diff could not be computed. That is NOT "no voice
    files changed" — it is "unknown", and unknown must demand the strongest
    evidence available rather than wave a possible voice-path change through.
    Same fail-closed rule as the deploy path."""
    if changed is None:
        return True, [], ("could not compute the diff — requiring the replay gate "
                          "(unknown is not a pass)")
    hits = touched_voice_files(changed, patterns)
    if hits:
        return True, hits, (f"voice-path change detected ({', '.join(hits)}) — a fresh "
                            "passing replay-gate result is REQUIRED")
    return False, [], (f"no voice-path files in the diff ({len(changed)} file(s) "
                       "changed) — replay gate not required")


def _emit_github_output(**pairs: str) -> None:
    """Append key=value lines to $GITHUB_OUTPUT when running under Actions."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a", encoding="utf-8") as fh:
            for key, value in pairs.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:  # pragma: no cover - runner filesystem failure
        print(f"voice-gate: could not write GITHUB_OUTPUT ({exc})", file=sys.stderr)


def read_changed_files(path: Path) -> list[str] | None:
    """Read a newline-separated changed-file list. None if unreadable.

    This is how the PR gate learns what a pull request touched WITHOUT checking
    out or executing any of its code: the file list comes from the GitHub API as
    inert data, and this trusted (base-branch) checker classifies it. Reading a
    filename can do nothing; running the PR's own checker could do anything."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"voice-gate: could not read changed-file list {path} ({exc})", file=sys.stderr)
        return None
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _scope_only(args: argparse.Namespace) -> int:
    """The scope job's whole body: classify the diff, publish the verdict, exit 0."""
    changed: list[str] | None
    if args.changed_files_from:
        # PR path: the file list is DATA supplied by the workflow (GitHub API).
        changed = read_changed_files(args.changed_files_from)
    elif not args.diff:
        # No range to narrow with: treat as "unknown", i.e. gate required.
        changed = None
    else:
        try:
            changed = git_changed_files(args.repo, args.diff)
        except RuntimeError as exc:
            print(f"voice-gate: could not compute diff {args.diff!r} ({exc})", file=sys.stderr)
            changed = None

    needs_gate, hits, reason = scope_verdict(changed, voice_path_patterns())
    print(f"voice-gate scope: {'VOICE' if needs_gate else 'CLEAR'} — {reason}")
    _emit_github_output(voice="true" if needs_gate else "false",
                        files=",".join(hits))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--artifact", type=Path,
                    default=Path(os.environ.get("ZOE_VOICE_RESULTS", DEFAULT_RESULTS)),
                    help="voice replay-gate result artifact (default: %(default)s)")
    ap.add_argument("--baseline", type=Path,
                    default=Path(os.environ.get("ZOE_VOICE_BASELINE", DEFAULT_BASELINE)),
                    help="current baseline to match the gate result against")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_H,
                    help="a gate result older than this is stale (default: %(default)s)")
    ap.add_argument("--repo", type=Path, default=Path.cwd(),
                    help="repo dir for --diff (default: cwd)")
    ap.add_argument("--diff", metavar="RANGE",
                    help="git range (e.g. HEAD..FETCH_HEAD); gate only if it touches "
                         "the voice path. Omit (or use --require) to always gate.")
    ap.add_argument("--require", action="store_true",
                    help="assert a fresh pass regardless of any diff")
    ap.add_argument("--no-baseline-check", action="store_true",
                    help="skip the baseline-identity match (freshness + status only)")
    ap.add_argument("--scope-only", action="store_true",
                    help="report whether the change touches the voice path and exit 0 "
                         "without asserting any artifact (PR-time scope job)")
    ap.add_argument("--changed-files-from", type=Path, metavar="PATH",
                    help="read the changed-file list from PATH (one per line) instead "
                         "of running git diff. The PR gate uses this so a pull request "
                         "is consumed as DATA, never executed.")
    ap.add_argument("--expect-revision", metavar="SHA",
                    help="require the artifact to record having exercised this exact "
                         "commit. The PR gate always passes the PR head sha; without it "
                         "any fresh passing artifact (e.g. from main) would clear the PR.")
    args = ap.parse_args(argv)

    # A revision is an opaque identifier that only ever gets COMPARED, but pin the
    # shape anyway: a malformed value should fail loudly here rather than silently
    # mismatch and read as an ordinary evidence failure.
    if args.expect_revision:
        rev = args.expect_revision.strip().lower()
        if len(rev) != 40 or any(c not in "0123456789abcdef" for c in rev):
            print(f"voice-gate: --expect-revision must be a 40-char hex sha, got "
                  f"{args.expect_revision!r}", file=sys.stderr)
            return 1
        args.expect_revision = rev

    # 0. SCOPE-ONLY: answer "does this diff touch voice?" and stop. Used by the
    #    PR-time gate's scope job, which runs on a GitHub-hosted runner where the
    #    replay artifact cannot exist. Splitting scope from assertion is what lets
    #    the required check report a conclusion on EVERY PR: no-voice PRs are
    #    decided here and never touch the Jetson. ALWAYS exits 0 — this step
    #    classifies, it does not enforce; the summary job turns the classification
    #    into the verdict.
    if args.scope_only:
        return _scope_only(args)

    # 1. Decide whether this deploy needs the gate at all. Default is to require
    #    it; --diff narrows that to "only when the incoming change touches voice".
    if not args.require and args.diff:
        try:
            changed = git_changed_files(args.repo, args.diff)
        except RuntimeError as exc:
            # Fail CLOSED: if we cannot tell what changed, require the gate rather
            # than wave a possible voice-path change through unproven.
            print(f"voice-gate: could not compute diff {args.diff!r} ({exc}); "
                  "requiring the gate to be safe.", file=sys.stderr)
        else:
            hits = touched_voice_files(changed, voice_path_patterns())
            if not hits:
                print(f"voice-gate: OK — no voice-path files in {args.diff} "
                      f"({len(changed)} file(s) changed); replay gate not required.")
                return 0
            print(f"voice-gate: voice-path change detected ({', '.join(hits)}) — "
                  "a fresh passing replay-gate result is REQUIRED.")

    # 2. Assert the artifact proves a fresh pass.
    artifact = load_json(args.artifact)
    baseline = None if args.no_baseline_check else load_json(args.baseline)
    allowed, reason = evaluate(
        artifact, now_epoch=time.time(),
        max_age_s=args.max_age_hours * 3600.0, baseline=baseline,
        expect_revision=args.expect_revision)

    if allowed:
        print(f"voice-gate: OK — {reason}  (artifact: {args.artifact})")
        return 0
    msg = [f"voice-gate: BLOCKED — {reason}", f"  artifact: {args.artifact}"]
    if args.expect_revision:
        # PR gate: the evidence must come from exercising THIS commit, so the
        # probe has to run against a checkout of it — not against the live tree.
        msg += [
            f"  Run the replay gate against THIS PR's head ({args.expect_revision[:8]}) "
            "on the Jetson as user zoe:",
            f"    git -C /home/zoe/assistant fetch origin {args.expect_revision}",
            f"    git worktree add ~/.worktrees/voice-gate {args.expect_revision}",
            "    flock /tmp/zoe-voice-harness.lock \\",
            "      python3 scripts/maintenance/voice_regression_probe.py \\",
            "        --samples 20 --service-dir ~/.worktrees/voice-gate/services/zoe-data",
            "  The probe records the commit it exercised; an artifact for any other",
            "  commit is evidence for different code and will not clear this PR.",
        ]
    else:
        msg += [
            "  Run the replay gate against the current baseline before deploying a "
            "voice-path change:",
            "    flock /tmp/zoe-voice-harness.lock \\",
            "      python3 scripts/maintenance/voice_regression_probe.py",
            "  (--service-dir auto-resolves to the live services/zoe-data, incl. "
            "from a git worktree)",
        ]
    print("\n".join(msg), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

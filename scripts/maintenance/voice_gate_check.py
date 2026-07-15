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

Deploy wiring: scripts/maintenance/deploy_live.sh calls this with the incoming
git range; if that range changes no voice-path files the check is a no-op pass
(so ordinary non-voice deploys are frictionless). See
docs/knowledge/voice-pipeline.md for the artifact contract.

Exit codes: 0 = allowed (fresh pass, or no voice-path change); 1 = blocked.

Examples:
    # deploy path: gate only if the incoming diff touches the voice runtime
    voice_gate_check.py --repo /home/zoe/assistant --diff HEAD..FETCH_HEAD
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
    "*kokoro*",
    "*moonshine*",
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
             baseline: dict[str, Any] | None = None) -> tuple[bool, str]:
    """The heartbeat check, pure and unit-testable. Returns (allowed, reason).

    Blocks unless the artifact exists, has status == "pass", is fresh (within
    max_age_s), and was produced against the current baseline."""
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
    return True, (f"voice replay-gate PASS ({age_s / 3600:.1f}h old, "
                  f"n={((artifact.get('summary') or {}).get('n_samples'))})")


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
    args = ap.parse_args(argv)

    # 1. Decide whether this deploy needs the gate at all.
    need_gate = True
    if not args.require and args.diff:
        patterns = voice_path_patterns()
        try:
            changed = git_changed_files(args.repo, args.diff)
        except RuntimeError as exc:
            # Fail CLOSED: if we cannot tell what changed, require the gate rather
            # than wave a possible voice-path change through unproven.
            print(f"voice-gate: could not compute diff {args.diff!r} ({exc}); "
                  "requiring the gate to be safe.", file=sys.stderr)
            changed, patterns = [], ()
        else:
            hits = touched_voice_files(changed, patterns)
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
        max_age_s=args.max_age_hours * 3600.0, baseline=baseline)

    if allowed:
        print(f"voice-gate: OK — {reason}  (artifact: {args.artifact})")
        return 0
    print(f"voice-gate: BLOCKED — {reason}\n"
          f"  artifact: {args.artifact}\n"
          "  Run the replay gate against the current baseline before deploying a "
          "voice-path change:\n"
          "    flock /tmp/zoe-voice-harness.lock \\\n"
          "      python3 scripts/maintenance/voice_regression_probe.py "
          "--service-dir /home/zoe/assistant/services/zoe-data",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

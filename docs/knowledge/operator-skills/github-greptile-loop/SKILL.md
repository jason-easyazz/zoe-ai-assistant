---
name: github-greptile-loop
description: Use when a GitHub pull request needs a Greptile-driven fix loop, confidence review, merge when ready, or repeated PR review triage until merged.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [github, greptile, pull-requests, code-review, automation]
    related_skills: [github-pr-workflow, github-code-review, github-auth, requesting-code-review]
---

# GitHub Greptile Loop

## Overview

Use this skill to drive a PR through Greptile review, fixes, re-review, squash merge when gates pass, and Multica closeout. This is the Hermes path for Zoe's `/grep-loop` / `zoe-greptile-loop` workflow. Hermes is the primary escalation agent for Zoe development work. OpenClaw remains fallback when browser automation or a workflow Hermes cannot execute is required.

This skill assumes Greptile is configured in the repository through `.greptile/config.json` and `.greptile/rules.md`.

## When To Use

- User asks to run the Greptile loop, review loop, or 5/5 PR confidence loop.
- A PR has Greptile comments that need triage and fixes.
- A PR needs repeated review after each fix until confidence is high.
- User asks Hermes to repair board/PR work that was previously handled by OpenClaw.

Do not use this for local pre-commit review only; use `requesting-code-review` first.

## Inputs

Accept one of:

- PR URL, e.g. `https://github.com/owner/repo/pull/123`
- Repo plus PR number, e.g. `owner/repo#123`
- Current git repo and current branch, if a PR already exists.

Optional flags:

- `--max-rounds N` default 5
- `--dry-run` inspect only
- `--no-push` prepare local fixes but do not push
- `--target-confidence N` default 5

## Authentication

Prefer `gh` when it is healthy:

```bash
gh auth status
```

If `gh` is not authenticated, use `GITHUB_TOKEN` from the environment or `~/.hermes/.env`. Do not print tokens.

```bash
if ! gh auth status >/dev/null 2>&1; then
  if [ -f ~/.hermes/.env ] && grep -q "^GITHUB_TOKEN=" ~/.hermes/.env; then
    export GITHUB_TOKEN="$(grep "^GITHUB_TOKEN=" ~/.hermes/.env | head -1 | cut -d= -f2-)"
  fi
fi
```

Stop and ask for refreshed GitHub auth if neither path works.

## Workflow

1. Resolve repo and PR number.

```bash
gh pr view --json number,title,url,headRefName,baseRefName,author,reviewDecision,statusCheckRollup
```

Fallback with curl:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR"
```

2. Run PR-size preflight.

Read the PR diff before editing. If the diff is too large for reliable review or bundles unrelated decisions, stop and suggest a split before starting the loop.

```bash
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
```

3. Check local safety.

```bash
git status --short
git branch --show-current
git fetch origin
```

If the tree is dirty, stop and ask before touching files. Never discard user changes.

4. Prefer Zoe's Greploop guard for bounded PR repair and merge when working in `/home/zoe/assistant`.

```bash
python3 scripts/maintenance/greploop_guard.py --pr <number> --once
python3 scripts/maintenance/greploop_guard.py --pr <number> --packet-only   # Cursor/cheap runner only
python3 scripts/maintenance/greploop_guard.py --pr <number> --merge-when-ready
python3 scripts/maintenance/greploop_guard.py --pr <number> --once --merge-when-ready
```

The guard CLI takes `--pr <number>` (PR number), not a task id. Resolve `<number>` from the PR
URL (the trailing `/pull/<number>`) or `gh pr view --json number`.

- `--once`: one bounded iteration (emit packet, run cheap agent, or wait for Greptile).
- `--packet-only`: build one repair packet and stop (for Cursor agents; never merges).
- `--merge-when-ready`: `gh pr merge --squash` only when Greptile confidence meets target,
  all unaddressed Greptile comments are cleared, and CI is green. Never uses `--admin` or force.

Cheap models must receive a single validated packet for one finding or CI failure, never broad
instructions such as "fix PR #123". Hermes closeout runs `--once` in a loop until ready, then
`--merge-when-ready`, then updates Multica with merge SHA.

5. Collect Greptile comments and check runs.

**Prefer live Greptile MCP data** (via `/home/zoe/bin/greptile-mcp.py` on the host):

```bash
/home/zoe/bin/greptile-mcp.py pr-status jason-easyazz/zoe-ai-assistant "$PR"
/home/zoe/bin/greptile-mcp.py pr-comments jason-easyazz/zoe-ai-assistant "$PR"
/home/zoe/bin/greptile-mcp.py trigger-review jason-easyazz/zoe-ai-assistant "$PR"
```

Use `reviewCompleteness`, `unaddressedComments`, and `suggestedCode` from that output before falling back to GitHub scraping:

```bash
gh pr view "$PR" --comments
gh api "repos/$OWNER/$REPO/pulls/$PR/comments" --paginate
gh api "repos/$OWNER/$REPO/commits/$HEAD_SHA/check-runs" --jq '.check_runs[] | {name,status,conclusion,details_url}'
```

6. Triage findings.

Classify every Greptile item:

- `fix_now`: correctness, security, data loss, missing user-facing behavior, failing test.
- `needs_user`: ambiguous product decision, destructive action, credential/auth step.
- `won't_fix`: false positive with reason.
- `already_fixed`: no longer applies to current diff.

Prefer real fixes over comment replies. Keep changes minimal.

7. Apply fixes and verify.

Use Zoe's project rules when in `/home/zoe/assistant`:

```bash
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 -m pytest services/zoe-data/tests/test_mempalace_integration.py -q
curl -sf http://127.0.0.1:8000/health
```

Adjust tests to the files changed. For database, chat, memory, agent, or UI changes, run the relevant focused tests and live smoke checks.

8. Commit and push only after verification.

```bash
git add <changed-files>
git commit -m "fix: address Greptile review findings"
git push
```

Do not bypass hooks.

9. Re-trigger or wait for Greptile.

Prefer an explicit re-review trigger:

```bash
/home/zoe/bin/greptile-mcp.py trigger-review jason-easyazz/zoe-ai-assistant "$PR"
```

If that fails, wait for Greptile auto-review on push (~60–120s) or ask the user to trigger from GitHub/Greptile UI.

Repeat until:

- Greptile confidence is at least target confidence (default 5/5).
- No unaddressed Greptile findings remain (every substantive item fixed or won't_fix with reason).
- CI is green (guard checks `statusCheckRollup` via `gh`).
- The loop reaches `--max-rounds`, then report remaining blockers instead of continuing blindly.

10. Merge when ready (Kanban closeout / operator request).

```bash
python3 scripts/maintenance/greploop_guard.py --pr <number> --merge-when-ready
```

Only after the gates above pass. Uses normal `gh pr merge --squash` — never `--admin`, never
force push, never `--no-verify`. If branch protection blocks merge, stop and report the `gh` error;
do not bypass protection.

11. Update Multica (or handoff) with PR URL, merge commit SHA, Greptile status, and summary.

## Reporting

Return:

- PR URL, merge commit SHA (if merged), and final confidence/check state.
- Findings fixed.
- Findings intentionally left with reasons.
- Tests and live checks run.
- Any manual blocker such as invalid GitHub auth, Greptile App not installed, or merge blocked by policy.

## Pitfalls

- Do not expose tokens from remotes, environment, or config files.
- Do not use `git reset --hard`, force push, or discard user changes.
- Do not treat Greptile style nits as higher priority than Zoe safety rules.
- Do not use this loop for massive PRs or unclear product decisions; split or ask the operator.
- Do not blindly accept every reviewer comment; classify false positives with reasons.
- If `gh` returns 401, stop and ask for auth refresh instead of guessing.

---
name: grep-loop-review-workflow
description: Use when you have a small Zoe PR or feature and want a repeated review-fix loop until Greptile findings are resolved, tests pass, and the change is merge-ready.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [agentic-engineering, code-review, pr, greptile, review-loop, zoe]
    related_skills: [agentic-engineering-workflow, github-greptile-loop, code-structure-cleanup]
---

# Grep Loop Review Workflow

This is Zoe's compatibility wrapper for the upstream review-loop idea.

For actual Zoe PR work, the implementation path is `github-greptile-loop`.

## When To Use

- A small PR or feature is ready for review.
- Greptile, another AI reviewer, or a human has provided specific feedback.
- Tests or focused checks can confirm the fix.
- You want Hermes to keep iterating until the diff is clean or a human decision is needed.

Do not use this on massive PRs or unclear product decisions.

## Workflow

1. Read the diff first.
2. Read the review feedback.
3. Classify findings into: fix now, needs user, won't fix, or already fixed.
4. Fix only relevant issues for this PR.
5. Add or update tests when possible.
6. Run relevant Zoe validators, tests, and smoke checks.
7. Re-run review through `github-greptile-loop` when working against a real GitHub PR.
8. Stop only when the PR is clean or blocked by a human decision.

## Preflight

Ask first:

```text
Is this PR too large for a reliable review loop? If yes, suggest how to split it.
```

If yes, split it before entering the loop.

## Verification Checklist

- [ ] PR is small enough to review reliably.
- [ ] Agent read the diff before editing.
- [ ] Agent fixed only relevant issues.
- [ ] Tests/typechecks/validators passed or blockers were stated.
- [ ] Final summary lists resolved review items.

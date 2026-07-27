---
type: Reference
title: Merge-queue switch — plan, blockers and the settings to flip
description: Why main should move from `strict: true` to a merge queue, what must change first, and the one unresolved decision (GitGuardian).
tags: [ci, branch-protection, merge-queue, review, greptile]
timestamp: 2026-07-27T00:00:00Z
---

# Merge-queue switch

**Status: PREPARED, NOT ENABLED.** The workflow change that makes it possible is merged
(`validate.yml` now triggers on `merge_group`). Nothing changes until the branch-protection
settings below are flipped, and one decision is still open (GitGuardian, §4).

## 1. Why

`strict: true` ("require branches up to date") froze `main` four separate times on
2026-07-26, all from the same root: it forces a branch update, which creates a new head,
which needs a fresh review.

- **Cascade.** With 8 PRs open, every merge pushes the other 7 behind. Each update bills a
  fresh review from every reviewer. The measured cost was **3.6 Greptile reviews per PR**
  across 112 PRs in July — concurrency, not oversized changes.
- **Stranded checks.** Greptile deduplicates by PR *diff*. A branch-update commit adds no
  diff, so it is correctly skipped — leaving the required check on the *previous* commit and
  the PR permanently unmergeable. #1560 sat in exactly this state with a paid, passing review
  one commit behind the head.

A merge queue gives the same guarantee — tested against the latest `main` — **without** the
author updating the branch. GitHub's own framing: it "does not require a pull request author
to update their pull request branch and wait for status checks to finish before trying to
merge."

## 2. The constraint that shapes everything

> Checks that only run on `pull_request` **will not execute** during merge queue operations.
> The merge will fail as the required status check will not be reported.

So **every required status check must report on `merge_group`**. GitHub Actions can simply add
the trigger. Third-party *App* checks generally cannot — and both of the current required
checks besides `validate` are App checks.

## 3. Greptile: stop making it a required CHECK, keep it a blocking REVIEWER

Greptile only runs on `pull_request`, so as a required check it would stall the queue forever.
That is the incompatibility, and it is real.

The resolution is not to drop Greptile but to change *how* it gates:

- **Remove `Greptile Review` from required status checks.**
- **Keep `required_conversation_resolution: true`** (already on). Greptile's findings become
  review threads, and unresolved threads still block the merge.
- Summon it deliberately via the `greptile` label (see `greptile-gate.yml`), which applies
  only once the other reviewers are green.

Net effect: Greptile's *findings* still block; Greptile's *availability* no longer can. That
matters because in one day it froze `main` via quota exhaustion, a 5/5 confidence threshold,
diff dedup, and repo-wide skips — none of which were code defects.

## 4. OPEN DECISION — GitGuardian

`GitGuardian Security Checks` is an App check and will not report on `merge_group`, so it
cannot stay in the required list once a queue is enabled. Three options, in preference order:

1. **Migrate to `GitGuardian/ggshield-action`** as a job in `validate.yml` (which already has
   the `merge_group` trigger). Needs a `GITGUARDIAN_API_KEY` repo secret. Keeps a blocking
   secret-scan gate on the merged result. **Recommended.**
2. Keep the App as a non-required check — it still scans and comments, but no longer blocks.
   A real reduction in a *security* gate; do not choose this by accident.
3. Do not enable the queue.

**RESOLVED 2026-07-27 — option 1 taken.** `GITGUARDIAN_API_KEY` is set as a repo secret
(personal access token) and a `secret-scan` job using `GitGuardian/ggshield-action` now runs
in `validate.yml`, inheriting its `merge_group` trigger.

Proven in both directions rather than assumed — a gate never seen to fail is not a gate:

| | result |
|---|---|
| clean branch | `secret-scan: completed/success` |
| planted credential (throwaway PR, since deleted) | **`secret-scan: completed/failure`** |

The planted value was randomly generated and never valid; the branch and PR were removed as
soon as the job reported red. The GitGuardian App failed on the same commit, so both agree.

Two details in that job are load-bearing: `fetch-depth: 0`, because ggshield scans branch
HISTORY and a shallow checkout silently scans far less than it appears to; and an explicit
pre-check that fails when `GITGUARDIAN_API_KEY` is empty, so an expired token cannot turn the
gate into a silent no-op. The token is a PAT, so it *will* expire.

**Still do not enable the merge queue before swapping the required check** — `secret-scan`
only exists on `main` once this PR merges, and until then the App is still the blocking gate.

**Original warning, kept for context:**
**Do not enable the merge queue before resolving this.** Dropping a secret-scanning gate to
fix a review-churn problem is a bad trade, and the whole point of the switch is to stop
solving process problems by weakening gates.

## 5. Settings to flip (once §4 is decided)

```bash
# 1. required checks: validate (+ ggshield job if migrated). NOT Greptile, NOT the GG App.
gh api -X PATCH repos/jason-easyazz/zoe-ai-assistant/branches/main/protection/required_status_checks \
  -F strict=false -f 'contexts[]=validate'

# 2. enable the merge queue for main (Settings → Branches, or via a ruleset)
```

`strict` becomes `false` — the queue supersedes it. Keep `enforce_admins` and
`required_conversation_resolution` exactly as they are.

## 6. Is it worth it at this volume?

Honestly: borderline. The guidance is that below ~10 PRs/day the manual rebase flow is fine,
and this repo runs ~4/day. The case for switching is not throughput, it is that the cascade
has already cost real money and a full day of blocked merges.

If the queue is enabled and the cascade stops being the dominant cost, it was right. If PR
volume drops to one or two in flight — which the "one workstream, one PR" guidance in
`AGENTS.md` is meant to achieve — plain `strict: false` plus serialising may be enough on its
own, and the queue is unnecessary complexity.

## 7. Rollback

Re-add `Greptile Review` to the required contexts, set `strict=true`, disable the queue. The
`merge_group` triggers are inert without a queue, so nothing needs reverting in code.

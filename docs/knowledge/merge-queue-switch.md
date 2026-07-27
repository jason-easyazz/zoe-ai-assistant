---
type: Reference
title: Merge-queue switch — decision record; blocked by plan availability, contexts swap decided
description: The merge queue CANNOT be enabled on this repo (user-owned; GitHub gates the feature to organization-owned repos). What IS decided — swap the GitGuardian App check for the first-party secret-scan job — plus the full enablement recipe if the repo ever moves to an org.
tags: [ci, branch-protection, merge-queue, review, greptile, gitguardian]
timestamp: 2026-07-27T00:00:00Z
---

# Merge-queue switch

**Status: BLOCKED — the feature is not available to this repo, and no setting we control
changes that.** GitHub's current docs (verified live 2026-07-27): *"Pull request merge queues
are available in any public repository owned by an organization, or in private repositories
owned by organizations using GitHub Enterprise Cloud."* `zoe-ai-assistant` is public but owned
by the personal account `jason-easyazz` (owner type `User`), so the queue setting never
appears, `mergeQueue(branch: "main")` returns `null`, and the repo has zero rulesets. This was
confirmed against the live API, not inferred.

What that leaves:

- **DECIDED — do now (independent of any queue):** swap the required `GitGuardian Security
  Checks` App check for the first-party `secret-scan` job (§5). It removes a required
  third-party App dependency that can freeze `main` exactly the way Greptile did, and it is
  the prerequisite for a queue if one ever becomes possible.
- **NEEDS JASON:** whether to transfer the repo to a (free) organization to unlock the queue
  at all (§6), and — only if he does — whether to accept the Greptile guarantee downgrade the
  queue forces (§4).
- **Recommendation: do not pursue the queue now.** Keep `strict: true` and serialise PRs
  (1–2 in flight ≈ ~2 reviews/PR, per AGENTS.md). At ~4 PRs/day the queue was already
  borderline (§7); an org transfer just to obtain it is not worth the migration risk today.

## 1. Why the queue was attractive

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
author updating the branch.

## 2. The constraint that shapes everything

> Checks that only run on `pull_request` **will not execute** during merge queue operations.
> The merge will fail as the required status check will not be reported.

And the docs make the two-stage mechanics explicit: *"Once a pull request has passed all
required branch protection checks, a user with write access to the repository can add the
pull request to the queue"*, after which *"the merge queue will ensure the pull request's
changes pass all required status checks when applied to the latest version of the target
branch and any pull requests already in the queue."*

So required checks are evaluated **twice — at queue entry (on the PR) and again on the merge
group commit** — and it is the *same single list* both times:

- **Branch protection** has one `contexts` list per branch. No per-event scoping exists.
- **Rulesets** do not fix this. A ruleset's `required_status_checks` rule is also
  branch-scoped, not event-scoped, and when multiple rulesets (or a ruleset plus classic
  protection) target the same branch the requirements are a **union** — the strictest
  aggregate applies. There is no mechanism, in either surface, to require a context on
  `pull_request` but exempt it on `merge_group`. The "design around it with rulesets" idea
  was checked and is not real.

Therefore **every required context must report on `merge_group`, full stop**. GitHub Actions
jobs can (add the trigger — done). App checks generally cannot, and *both* non-`validate`
required checks are App checks. That makes Greptile as much of a queue blocker as GitGuardian
ever was — see §4.

## 3. GitGuardian — RESOLVED 2026-07-27, and what the swap honestly trades

`GitGuardian Security Checks` (App, `app_id` 46505) cannot report on `merge_group`. The fix
shipped: a first-party **`secret-scan`** job using `GitGuardian/ggshield-action@v1` now lives
in `.github/workflows/validate.yml` (merged in #1565), inherits the workflow's `merge_group`
trigger, and reports as its own check-run named exactly `secret-scan` — verified green on the
current `main` head.

Proven in both directions rather than assumed — a gate never seen to fail is not a gate:

| | result |
|---|---|
| clean branch | `secret-scan: completed/success` |
| planted credential (throwaway PR, since deleted) | **`secret-scan: completed/failure`** |

The planted value was randomly generated and never valid; the branch and PR were removed as
soon as the job reported red. The GitGuardian App failed on the same commit, so both agree.

Load-bearing details of the job (all verified in the workflow):

- `fetch-depth: 0` — ggshield scans branch HISTORY; a shallow checkout silently scans far
  less than it appears to.
- On `merge_group` runs `pull_request.base.sha` is empty, so the job falls back to
  `merge_group.base_sha` — without a base ggshield has nothing to diff against and would
  silently under-scan. The queue case is handled, not just triggered.
- An explicit pre-check **fails the job when `GITGUARDIAN_API_KEY` is empty** — the token is
  a PAT and *will* expire; when it does the gate goes red, not silent. Fail-closed.
- Fork PRs are skipped (a fork cannot read the secret) and the job **succeeds** in that case.

Does the App add anything the action doesn't? Yes, three things — which is why it stays
**installed and informational**, just no longer *required*:

1. **Dashboard incidents + remediation workflow** — ggshield findings surface in CI logs
   only; the App files incidents in the GitGuardian dashboard.
2. **Historical / full-repo scanning** — the App watches the whole repo continuously; the
   action scans the commit range of each run.
3. **Fork-PR blocking** — after the swap, the required gate auto-passes on fork PRs (scan
   skipped, job green) and only the non-blocking App scans them. For this repo (single
   operator + agents, no external contributors) that gap is theoretical; note it and move on.

Failure modes, compared honestly:

- **App down, today (App required):** `main` freezes — same failure class as the Greptile
  freezes. This is an argument *for* the swap regardless of any queue.
- **App down, after the swap:** nothing blocks; ggshield remains the gate. Correct.
- **Action broken / token expired, after the swap:** `secret-scan` goes red and `main`
  freezes until the PAT is rotated. Fail-closed — the right direction for a security gate.

## 4. Greptile — the bigger blocker, stated plainly

`Greptile Review` (App, `app_id` 867647) reviews **pull requests, not merge groups**. As a
required context it passes at queue *entry* (the PR has a review) and then **never reports on
the merge-group commit**, so every queue entry stalls until the check timeout and fails out —
identical to the GitGuardian problem, with no ggshield-equivalent to migrate to. Since §2
rules out per-event contexts, **a queue requires removing `Greptile Review` from the required
list. There is no configuration that keeps both.**

The prepared mitigation (unchanged): keep `required_conversation_resolution: true` — it gates
queue entry, so Greptile's *findings* (threads) still block while its *availability* no
longer can — and summon Greptile deliberately via the `greptile` label once cheaper reviewers
are green (`greptile-gate.yml`, live).

But say the quiet part loudly: **a merge queue cannot preserve THE GUARANTEE as written.**
The merged commit under a queue is the merge-group result — the PR *plus* latest `main`
*plus* any PRs ahead in the queue. `validate` and `secret-scan` test that exact commit;
Greptile only ever reviewed the PR head. The guarantee downgrades from *"every merged commit
was reviewed at that exact commit"* to *"reviewed at the PR head; tested at the exact merged
commit."* A semantic conflict between two queued PRs would be caught by tests, not by review.
Today's `strict: true` + `triggerOnUpdates: true` setup is the only configuration that
delivers the full guarantee — that is precisely why `triggerOnUpdates: false` was reverted.
Adopting a queue means Jason deliberately trades exact-commit *review* for exact-commit
*testing* plus zero rebase churn. That trade is his to make, not ours — and it is moot until
§6 anyway.

## 5. DECIDED — the contexts swap (main session runs this; no queue required)

Live protection as read 2026-07-27: `strict: true`; required contexts `validate`
(app 15368 = GitHub Actions), `GitGuardian Security Checks` (46505), `Greptile Review`
(867647); `enforce_admins: true`; `required_conversation_resolution: true`.

The swap: **require `secret-scan`, stop requiring the GitGuardian App.** `Greptile Review`
stays required (no queue exists, so it cannot stall anything) and `strict` stays `true`. The
`app_id` pin matters — a bare context string can be satisfied by any app posting a status
with that name; pinning to GitHub Actions (15368) closes that hole.

```bash
gh api -X PATCH repos/jason-easyazz/zoe-ai-assistant/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{
  "strict": true,
  "checks": [
    { "context": "validate",       "app_id": 15368 },
    { "context": "secret-scan",    "app_id": 15368 },
    { "context": "Greptile Review", "app_id": 867647 }
  ]
}
JSON

# verify
gh api repos/jason-easyazz/zoe-ai-assistant/branches/main/protection/required_status_checks
```

Leave the GitGuardian App **installed** (dashboard, historical scans, fork-PR comments) —
this changes what *blocks*, not what *scans*.

### Rollback (restores today's exact state)

```bash
gh api -X PATCH repos/jason-easyazz/zoe-ai-assistant/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{
  "strict": true,
  "checks": [
    { "context": "validate",                   "app_id": 15368 },
    { "context": "GitGuardian Security Checks", "app_id": 46505 },
    { "context": "Greptile Review",             "app_id": 867647 }
  ]
}
JSON
```

## 6. NEEDS JASON — unlocking the queue at all (org transfer)

The only path to a merge queue is transferring the repo to an **organization** (a free-plan
org qualifies — the repo is public). That is an operator decision with real migration cost,
none of it insurmountable but none of it free:

- App installs are **per-owner**: Greptile, GitGuardian, Copilot, and the Actions runners'
  app grants must all be re-installed/re-authorised on the org.
- Verify after transfer that Actions secrets (`GITGUARDIAN_API_KEY`, `POSTGRES_CI_PASSWORD`),
  branch protection, and webhooks survived — do not assume.
- Git redirects old remotes, but agent configs, docs, and scripts across this codebase
  hard-code `jason-easyazz/zoe-ai-assistant` and would want a sweep.
- §4's guarantee downgrade must be explicitly accepted, because enabling the queue requires
  dropping `Greptile Review` from required contexts.

**Not recommended today**: the pain the queue solves (the `strict` cascade) is already
mitigated by the §5 swap plus serialising PRs, at zero migration risk.

### If Jason does it anyway — exact enablement, in order

```bash
# 0. Precondition: repo now lives at <ORG>/zoe-ai-assistant; apps reinstalled; secrets verified.

# 1. Required contexts: validate + secret-scan ONLY (both report on merge_group).
#    Greptile Review MUST come out (§4) and strict becomes false — the queue supersedes it.
#    required_conversation_resolution stays true: it is what keeps Greptile findings blocking.
gh api -X PATCH repos/<ORG>/zoe-ai-assistant/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{
  "strict": false,
  "checks": [
    { "context": "validate",    "app_id": 15368 },
    { "context": "secret-scan", "app_id": 15368 }
  ]
}
JSON

# 2. Enable the queue via a ruleset (squash-only, matching repo policy).
gh api -X POST repos/<ORG>/zoe-ai-assistant/rulesets --input - <<'JSON'
{
  "name": "main merge queue",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    {
      "type": "merge_queue",
      "parameters": {
        "merge_method": "SQUASH",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 0,
        "check_response_timeout_minutes": 60
      }
    }
  ]
}
JSON
```

### Queue rollback

```bash
# delete the ruleset (find <ID> via: gh api repos/<ORG>/zoe-ai-assistant/rulesets)
gh api -X DELETE repos/<ORG>/zoe-ai-assistant/rulesets/<ID>
# restore Greptile Review as required and strict=true (the §5 "swap" JSON with strict true
# and Greptile Review re-added). The merge_group triggers in validate.yml are inert without
# a queue — nothing to revert in code.
```

## 7. Is it worth it at this volume?

Honestly: no, not any more. The guidance is that below ~10 PRs/day the manual rebase flow is
fine, and this repo runs ~4/day. The cascade that motivated all this was concurrency — 8 PRs
in flight — and AGENTS.md now mandates serialising to 1–2, which settles at ~2 reviews/PR
with `strict: true` intact and the full guarantee intact. The queue would save roughly the
second review per PR at the cost of an org migration and a weakened review guarantee. If PR
volume ever grows past ~10/day *and* the repo has moved to an org for other reasons, reopen
this with §6.

## 8. Gate self-test — simulated against live PRs before merge

`greptile-gate.yml` was dry-run against the six open PRs by replaying its exact API calls.
Every one held on `reviewers=false`, and **would have held forever**: Copilot and Codex are
manual-trigger only, so the gate was waiting for reviews nobody would ever request. The label
would never have been applied and Greptile never summoned — a silent deadlock, in the very
component built to prevent one.

Fixed by making the gate SUMMON what it waits for. The two are requested differently and both
details are easy to get wrong:

- **Copilot** — `pulls.requestReviewers({ reviewers: ['Copilot'] })`. The `[bot]` login does
  NOT resolve; `copilot-pull-request-reviewer[bot]` fails with "Could not resolve user".
- **Codex** — an `@codex review` comment, tagged with a hidden per-SHA marker so it is asked
  once per head rather than on every workflow run.

Labels the Actions depend on (`greptile`, `oversized-ok`) did not exist either and have been
created; `addLabels` would have auto-created a bare one, but the `oversized-ok` override would
have been undiscoverable.

**Still unverified:** the label NAME must match the filter configured in the Greptile dashboard.
If the dashboard filters on a different string, the gate labels correctly and Greptile still
never runs. Confirm before relying on it.

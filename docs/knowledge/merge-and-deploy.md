---
type: Reference
title: Merge & Deploy Discipline
description: How code actually ships in Zoe — that a merged PR is not a deploy, the protected-main merge gates, and the Greptile/greploop gotchas (large-PR skip, thread resolution, REST-not-GraphQL verification, strict-mode cascade, GitGuardian history, validate.yml test enumeration, merge-queue prerequisites).
tags: [git, merge, deploy, greptile, ci, workflow]
timestamp: 2026-06-29T00:00:00Z
---

# Merge & Deploy Discipline

The non-obvious rules for getting a change reviewed, merged, and actually live. Binding workflow
prose lives in the root `AGENTS.md` (Greptile PR loop, Branching policy); this records *what is true*
so an agent doesn't relearn it the hard way. Runtime/deploy context: [runtime-topology.md](runtime-topology.md).

## Continuous deployment — merge to `main` AUTO-DEPLOYS

A **self-hosted GitHub Actions runner** on the Jetson runs `.github/workflows/deploy.yml` on **every
push to `main`**. It deploys straight into the live `/home/zoe/assistant` checkout:

    cd /home/zoe/assistant
    git fetch origin main   # 5 retries — the .git is shared, ref-lock races happen
    voice_gate_check.py --diff HEAD..FETCH_HEAD   # BLOCKS an ungated voice-path change
    git reset --hard "$target"                    # the gate-checked SHA, not a re-read FETCH_HEAD
    scripts/deploy/migrate.sh
    docker compose up -d --build zoe-auth
    systemctl --user restart zoe-data.service
    # + rebuild/restart flue-zoe-brain & flue-zoe-telegram sidecars IF their source changed

So **merging a PR to `main` ships it** — the runner's `reset --hard` is the intended CD contract (live
tree == main), and the live checkout is pinned to `main`, not a feature branch. Runs are serialized
runner-vs-runner by the `production` concurrency group (`cancel-in-progress: false`). Still run a
focused local check **before** merge — a green PR auto-deploys, so a bad merge is live.

### The voice replay-gate runs at PR time AND on the CD path

**PR time (`voice-gate`, required, since 2026-07-30).** The gate used to be post-merge only,
which left a real gap: a voice-path PR could go green, merge, and then be permanently
refused by the deploy gate — a *green main that will not deploy*, found after the fact with
the change already on the trunk. `.github/workflows/voice-gate.yml` now asserts the same
contract before merge. Its `scope` job classifies the PR diff on a hosted runner
(`voice_gate_check.py --scope-only`, always exit 0) and only a voice-path verdict escalates
to the self-hosted `replay-evidence` job, where the artifact actually lives. The `voice-gate`
summary job runs `if: always()` so the required context reports a conclusion on **every** PR:
trivially green when no voice-path file changed. Unblock a red one exactly as below — re-run
the probe on the Jetson, then re-run the workflow.

**Deploy time (unchanged, defence in depth).** CD is how changes actually reach the box, so
the **voice replay-gate also runs on the runner**, not only on the manual `deploy_live.sh`
path. Its pull step runs `scripts/maintenance/voice_gate_check.py --repo
/home/zoe/assistant --diff "${prev}..${target}"` **after** the fetch and **before** the `reset --hard`,
inside the same `flock /tmp/zoe-deploy.lock`. (Before this, merging a voice-path change auto-deployed it
with the mandatory gate never running — a gate that can silently not-run is not a gate.)

- **What blocks:** an incoming diff that touches the voice runtime path (STT/brain/TTS — see
  `VOICE_PATH_PATTERNS` in `voice_gate_check.py`, incl. `*kokoro*`/`*moonshine*`) **without** a fresh
  (<24h), passing, current-baseline artifact at `~/.cache/zoe/voice_regression_last.json`. Missing,
  stale, skipped or failed all block — a skip is not a pass. **Non-voice diffs are a no-op pass**, so
  ordinary deploys are frictionless. The check only *reads* the artifact; it never runs the ~2.3 GB
  Kokoro harness on the runner.
- **Blocking happens before the reset**, so the live tree stays at `prev` — nothing is migrated or
  restarted, and a retry re-evaluates the *same* change instead of fast-forwarding past it.
- **Fail-closed has a cost, and it is intended.** Once a voice-path change is on `main`, *every*
  subsequent push carries that diff in `prev..target`, so **all deploys stay blocked** until someone
  produces a fresh passing artifact. Unwedge on the Jetson as user `zoe`:

      flock /tmp/zoe-voice-harness.lock \
        python3 scripts/maintenance/voice_regression_probe.py --samples 20

  (no `--service-dir` needed — it auto-resolves to the live `services/zoe-data/.env`, from a git
  worktree too; see [voice-pipeline.md](voice-pipeline.md)) then **re-run the deploy workflow** (`gh run rerun <id>` or push). The `flock` is mandatory — two
  concurrent Kokoro loads OOM the box. The right move is to run the gate **before** merging a voice
  change, not after CD blocks. Detail: [voice-pipeline.md](voice-pipeline.md).
- The runner resets to the **gate-checked `$target`**, not a re-read `FETCH_HEAD` — a concurrent fetch
  on the shared `.git` could otherwise advance the tree to a commit pushed *after* the gate ran, a
  silent bypass. (Same fix as #1344 on the manual path.)

### Manual deploy + the shared lock

`scripts/maintenance/deploy_live.sh` is the blessed **manual** path (fetch main → voice-gate → `merge
--ff-only` → restart → health/rollback). It **double-drives the same `.git`/worktree** as the CD
runner. Both fetch+advance `main` on a merge event, so without coordination they lose the ref-lock race
and the manual path aborts mid-op. Both now take a shared **`flock /tmp/zoe-deploy.lock`** (runner: its
pull/reset step; script: across its whole mutating section) so they take turns instead of colliding.
The `production` concurrency group does **not** cover this — it only serializes runner-vs-runner.

**Lock scope is deliberately the git tree mutation, not the whole deploy.** The runner holds the lock
only across its pull/reset step (the tree mutation that caused the ref-lock abort), then releases it
before its migrate/restart steps — wrapping the whole multi-step job would mean restructuring the
production CD workflow, which is out of scope. Edge case for operators: if you run `deploy_live.sh` by
hand at the exact moment a merge is auto-deploying, the manual path can win the lock in the gap after
the runner's reset and restart `zoe-data` while the runner is also restarting it — worst case is one
redundant restart and a brief, self-correcting rollback on the manual path (both converge to the same
`main` SHA, so nothing diverges). Prefer **either** the manual path **or** letting CD run, not both at
once. A full-span lock (or an autostash before the runner's `reset --hard`) is a possible follow-up.

`deploy_live.sh`'s pre-pull gate blocks only on **uncommitted TRACKED** changes (a fast-forward would
clobber them); untracked runtime artifacts on the live tree (`data/chroma/`, `data/music-assistant/`
sidecars, HACS, …) do **not** block and are gitignored. The runner's `reset --hard` intentionally has
**no** clean-tree refuse — CD overwrites the tree to match `main` by contract.

## Protected `main` — the merge gates (re-tiered 2026-07-30)

**The required gate is DETERMINISTIC and locally runnable.** Anything judgement-shaped —
an LLM reading a diff — is advisory. Rationale, tiering and worker routing:
[`AGENTS.md`](../../AGENTS.md) "Review pipeline".

- `strict = true` — a PR must be **up to date with `main`** to merge (it can sit **BEHIND** if `main`
  races ahead; clear with update-branch / re-run).
- Required status checks: **`validate`**, **`secret-scan`**, **`voice-gate`**.
  - The GitGuardian App check is informational — the first-party `secret-scan` job replaced
    it as required so an App outage cannot freeze `main`.
  - **`voice-gate`** (added 2026-07-30) runs the replay-gate assertion at PR time, closing
    the "green main that will not deploy" gap. It **always reports a conclusion**: PRs with
    no voice-path files pass trivially; only a voice-path diff escalates to the self-hosted
    evidence job. The post-merge deploy assertion stays as defence in depth.
  - **`Greptile Review` is NOT required.** A note here previously claimed it was made
    required on 2026-07-27; the live branch-protection API never carried it (verified
    2026-07-30: contexts were `validate`, `secret-scan` only). It is now advisory by
    design — a non-deterministic SaaS reviewer is not on the required path.
- `required_conversation_resolution = true` — **every review thread must be resolved**, not just replied to.
- `0` required human approvals → green checks + resolved threads = mergeable; repo `allow_auto_merge = true`.
- **Never** `--admin` / `--force` (no bypassing protection). Merge with `gh pr merge <n> --squash --auto`.

### A required context that does not REPORT blocks forever

GitHub waits on a required context indefinitely — there is no timeout — and `enforce_admins
= true` means the owner cannot merge past it either. Three ways to create that state, all
of which have bitten this repo or were one step away from it:

- adding a context to branch protection **before** the workflow producing it is on `main`;
- a `paths:` filter on a required workflow (it simply does not report on unmatched PRs);
- a job-level `if:` without `always()` on the job carrying the required name.

`tests/unit/test_required_gate_workflows.py` asserts against all three.

The inverse matters for **auto-merge**: a required context that has not yet reported does
not hold the merge. Measured on #1587 — the PR merged 3 seconds before its review check
started, which then concluded `failure` on code already on `main`. "Green" means "every
required context has REPORTED success"; a missing context is not the same as a red one.

### Break-glass — recovery from a required-check outage

On 2026-07-27 a repo-wide Copilot outage deadlocked every open PR, including the one
carrying the fix. `.github/workflows/break-glass.yml` is the in-band escape:

1. Apply the **`break-glass`** label to the PR (create it once: `gh label create break-glass
   -d "audited admin override of a stuck required context"`), or run the workflow manually
   with a PR number and a reason.
2. It verifies the actor is a repo **admin** via the API — `write` (enough to apply a
   label) is deliberately not enough. A non-admin's label is stripped and refused.
3. It force-publishes **only** the required contexts that are not already green, each
   carrying an output naming the override, the actor and the context's real state.
4. It posts a permanent audit comment and **removes the label**. The override is
   single-use and bound to that head commit; a new push re-blocks normally.

It does **not** bypass `required_conversation_resolution` — unresolved threads still
block, deliberately, because an outage cannot make a thread unresolvable. Use it for
OUTAGES, never for a check that is red for a real reason.

## Greptile / greploop gotchas

Greptile is **advisory** as of 2026-07-30 — it no longer gates a merge, so none of these
stall a PR any more. They still decide whether you get the advisory review you paid for.

- **Greptile silently SKIPS large PRs** (>~50 files) and ignores `docs/archive/**`. The credit
  is spent and no review exists → **keep PRs small** (use `/split-to-prs` when a branch grows).
  `greptile-gate.yml` bounds re-summons at 3 per head and then says so, loudly, once.
- **Resolve threads via GraphQL `resolveReviewThread`** — replying to a Greptile comment does NOT
  satisfy `required_conversation_resolution`; the thread must be marked resolved. This one DOES
  still block a merge: thread resolution is required regardless of who opened the thread.
- **Verify a merge via REST, not GraphQL.** On this host the GraphQL `pr view` is unreliable (phantom
  merged states / SHAs). Trust `gh api repos/:owner/:repo/pulls/N --jq .merged` and the commits on
  `main`.
- **A green `Greptile Review` check ≠ threads resolved.** Greptile's *status check* can pass while
  review *threads* are still open; `required_conversation_resolution` is the gate that actually
  enforces "5/5 + every comment sorted." Always check unresolved-thread count, not just the check.

## Strict-mode cascade — draining a batch of ready PRs

With `strict = true` only **one** PR can be up-to-date at a time, so a batch of green PRs drains
**serially**, not in parallel:

- The instant one PR merges, every other open PR goes **BEHIND** and must be branch-updated
  (`gh pr update-branch <n>`) before it can merge. Cascade: nudge **one** PR per merge.
- **Each branch-update re-runs all checks AND triggers a fresh Greptile review on the new commit,**
  which frequently posts **new** threads — so updating a "clean" PR can un-clean it (the *re-review
  treadmill*). Don't mass-update the whole batch; it just re-triggers everyone's Greptile at once.
- Branch-update re-review findings are usually **real** (the new commit pulls in others' merged
  changes): fix-if-real / reply+resolve-if-addressed — don't just re-update and hope.
- **Arm auto-merge** so a PR self-merges the moment it's green + resolved + current:
  `gh pr merge <n> --squash --auto --delete-branch`. This is **not** a bypass — GitHub still holds it
  until every gate passes; it just removes the manual click.
- The purpose-built fix for this serial churn WOULD BE a **GitHub merge queue**, but it is
  **not available to this repo** (org-owned repos only; verdict 2026-07-27, see
  merge-queue-switch.md) — serialisation stays the answer.

## GitGuardian: secrets live in branch *history*

- GitGuardian scans the **whole branch history**, so a fake/test credential added in an *intermediate*
  commit fails the check **even when the head tree is clean**. Squash-at-merge does **not** help — the
  check runs pre-merge on the branch as-is.
- Force-pushing to rewrite that history is blocked by the blast-radius guard (workers *and*
  orchestrator alike).
- **Workaround (no force-push):** make a fresh single squashed commit of the final state, push it to a
  **new** branch (a normal create), open a **replacement PR**, and close the old one. Identical final
  diff, clean history → GitGuardian passes. (Used for the panel-authz and auth-limiter PRs.)
- **Carry the review state forward.** A replacement PR starts with a blank slate — the old PR's resolved
  threads, unresolved findings, and approvals do **not** transfer. Before closing the original, copy any
  still-open review findings into the new PR (or re-run the cross-review against the final diff) so a
  fix doesn't silently drop prior review context. Cross-link the old and new PR numbers.

## CI test discovery — `validate.yml` runs an ENUMERATED list

- Parts of `validate.yml` run an **explicit, enumerated list of test files**, not pytest
  path-discovery. A new `tests/**` file **silently does not run in CI** unless its lane picks it up —
  so a brand-new regression test can "pass" by never executing.
- Lanes that are already enumeration-free: `services/zoe-data/tests` + repo-root `tests/unit`
  (marker-based, `-m ci_safe`) and `services/zoe-auth/tests` (full-directory run since P-F5 — the old
  4-file list silently dropped `test_oidc_login`/`test_rbac`/`test_security`). Elsewhere,
  **whenever you add a test file, add it to `validate.yml`'s test list** and confirm it actually runs
  in the CI job. Greptile flags this repeatedly; don't rely on that as the backstop.

## Merge queue (VERDICT 2026-07-27: unavailable — org-owned repos only)

**Resolved:** GitHub gates merge queues to organization-owned repositories; this repo is
personal, so the queue cannot be enabled by any setting we control. Full analysis,
org-transfer trade-offs and the executed secret-scan contexts swap:
[`merge-queue-switch.md`](merge-queue-switch.md). The prerequisites below are kept as the
historical record of what enabling WOULD take after an org transfer.

A merge queue would drain a clean batch without the serial branch-update treadmill (it rebases, tests,
and merges each PR in order automatically). **Hard prerequisites — without them it stalls every queued
PR forever:**

- Repo must be on a **GitHub Team/Enterprise** plan (the option is absent on Free/personal).
- Required checks must run on the **`merge_group`** event — add `on: merge_group:` to `validate.yml`;
  the queue evaluates checks on a temporary `gh-readonly-queue/...` ref.
- **Greptile must post its `Greptile Review` status on `merge_group` commits.** If Greptile only reacts
  to `pull_request`, the queue waits on a status that never arrives. **Do not enable the queue until
  this is proven** — Greptile is a non-negotiable gate here.
- Changing branch protection / enabling the queue is an `enforce_admins`-protected operator action;
  agents prepare the `merge_group` CI wiring and verify Greptile, the human flips the setting.

## Worktree hygiene

- Work in a **dedicated git worktree** (`~/.worktrees/<slug>`), branch off fresh `origin/main`. Never
  switch the live checkout (`/home/zoe/assistant`) to a feature branch for agent work.
- **One checkout-driver at a time.** Never run a background PR-merge/greploop driver (which does
  `git checkout`/`reset`) against the same working tree you're editing — it has silently wiped
  uncommitted work. Commit first, or isolate in a separate worktree.
- Branches die at merge (`delete_branch_on_merge`); merged task worktrees are auto-reclaimed.

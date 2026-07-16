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

### The CD path enforces the voice replay-gate

CD is how changes actually reach the box, so the **voice replay-gate runs on the runner**, not only on
the manual `deploy_live.sh` path. Its pull step runs `scripts/maintenance/voice_gate_check.py --repo
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
        python3 scripts/maintenance/voice_regression_probe.py \
          --samples 20 --service-dir /home/zoe/assistant/services/zoe-data

  then **re-run the deploy workflow** (`gh run rerun <id>` or push). The `flock` is mandatory — two
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

## Protected `main` — the merge gates (verified live 2026-06-26)

- `strict = true` — a PR must be **up to date with `main`** to merge (it can sit **BEHIND** if `main`
  races ahead; clear with update-branch / re-run).
- Required status checks: **`validate`**, **`GitGuardian Security Checks`**, **`Greptile Review`**.
- `required_conversation_resolution = true` — **every review thread must be resolved**, not just replied to.
- `0` required human approvals → green checks + resolved threads = mergeable; repo `allow_auto_merge = true`.
- **Never** `--admin` / `--force` (no bypassing protection) unless the operator explicitly asks for that
  emergency path. Merge with `gh pr merge <n> --squash --auto`.

## Greptile / greploop gotchas

- **Greptile silently SKIPS large PRs** (>~50 files) and ignores `docs/archive/**`. If the
  `Greptile Review` check never posts, the PR can't satisfy the gate → **keep PRs small** (use
  `/split-to-prs` when a branch grows). This is *the* reason big PRs stall.
- **Resolve threads via GraphQL `resolveReviewThread`** — replying to a Greptile comment does NOT
  satisfy `required_conversation_resolution`; the thread must be marked resolved.
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
- The purpose-built fix for this serial churn is a **GitHub merge queue** (see below) — evaluated but
  not yet enabled.

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

## Merge queue (evaluated, NOT enabled as of 2026-06-29)

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

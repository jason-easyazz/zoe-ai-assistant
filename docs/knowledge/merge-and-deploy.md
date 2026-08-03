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

**PR time (`voice-gate` — INFORMATIONAL, fail-closed, NOT branch-protected).** The gate used
to run post-merge only, which left a real gap: a voice-path PR could go green, merge, and then
be permanently refused by the deploy gate — a *green main that will not deploy*, found after
the fact with the change already on the trunk. `.github/workflows/voice-gate.yml` now surfaces
the same answer before merge. Its `scope` job classifies the PR's changed-file list (fetched
from the API — never a checkout of the PR) and only a voice-path verdict escalates to the
self-hosted `replay-evidence` job, where the artifact actually lives. The `verdict` job runs
`if: always()` and publishes the `voice-gate` check against the PR head, so it reports a
conclusion on **every** PR: trivially green when no voice-path file changed.

It does **not** block the merge — see *Why `voice-gate` is not a required context* below. Red
is meant to be heeded, and merging past it means accepting that the deploy will be refused
until the probe is run.

Two things make it evidence rather than ceremony, and both cost something:
**the artifact must be BOUND to the PR head** (`--expect-revision`), so the probe has to be run
against a checkout of that commit and re-run after every push to it; and **the whole workflow runs
trusted code only** (see *The gate's own trust model* below). Unblock a red one with the
`git worktree add <head>` + probe recipe the check's output prints.

**Deploy time (unchanged, defence in depth).** CD is how changes actually reach the box, so
the **voice replay-gate also runs on the runner**, not only on the manual `deploy_live.sh`
path. Its pull step runs `scripts/maintenance/voice_gate_check.py --repo
/home/zoe/assistant --diff "${prev}..${target}"` **after** the fetch and **before** the `reset --hard`,
inside the same `flock /tmp/zoe-deploy.lock`. (Before this, merging a voice-path change auto-deployed it
with the mandatory gate never running — a gate that can silently not-run is not a gate.)

- **What blocks:** an incoming diff that touches the voice runtime path (STT/brain/TTS — see
  `VOICE_PATH_PATTERNS` in `voice_gate_check.py`, incl. `*kokoro*`/`*moonshine*`, the LIVE
  `ZOE_BRAIN_BACKEND=flue` lane, and the whole two-stage router — its model directory
  `services/zoe-data/models/*`, its `functiongemma-router.service` serving unit, and its
  `router_two_stage.py` + `semantic_router.py` decision modules — but not the unmerged `-2x`
  port, the offline training copies under `labs/setfit-router/artifacts/`, the routers' tests,
  or their offline eval/self-train tooling) **without** a fresh
  (<24h), passing, current-baseline artifact at `~/.cache/zoe/voice_regression_last.json`. Missing,
  stale, skipped or failed all block — a skip is not a pass. **Non-voice diffs are a no-op pass**, so
  ordinary deploys are frictionless. The check only *reads* the artifact; it never runs the ~2.3 GB
  Kokoro harness on the runner.
- **Blocking happens before the reset**, so the live tree stays at `prev` — nothing is migrated or
  restarted, and a retry re-evaluates the *same* change instead of fast-forwarding past it.
- **A new `VOICE_PATH_PATTERNS` entry is not in force for the deploy that carries it** — the checker
  runs from the live checkout at `prev`, i.e. the currently **deployed** copy. So extending the tuple
  is **deployed-first, not merged-first**: land the tuple change, wait for its deploy to go green (the
  live tree is now reset past it), and only then merge the change it is meant to gate. Merging both
  before the first has deployed lets one run's `prev..target` span both commits, where the old checker
  classifies the pair and takes the no-op pass. The queue is serialized (`concurrency: production`,
  `cancel-in-progress: false`) but each run fetches `main` at its own start and the memory-headroom
  gate can hold a run for ~9 minutes, so the window is real.
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

## Protected `main` — the merge gates (re-tiered 2026-07-31)

**The required gate is DETERMINISTIC and locally runnable.** Anything judgement-shaped —
an LLM reading a diff — is advisory. Rationale, tiering and worker routing:
[`AGENTS.md`](../../AGENTS.md) "Review pipeline".

- `strict = true` — a PR must be **up to date with `main`** to merge (it can sit **BEHIND** if `main`
  races ahead; clear with update-branch / re-run).
- Required status checks: **`validate`**, **`secret-scan`**. That is the whole list.
  - The GitGuardian App check is informational — the first-party `secret-scan` job replaced
    it as required so an App outage cannot freeze `main`.
  - **`voice-gate` is INFORMATIONAL, not required** (descoped 2026-07-31 — see below). It
    runs on every PR, fails closed, and publishes a visible `voice-gate` check on the PR
    head. Red must be heeded; it does not branch-protection-block on its own.
  - **`Greptile Review` was DEMOTED from required to advisory on 2026-07-31.** It was a
    required status check from 2026-07-27 until then. It came off the required path because
    a non-deterministic third-party service we do not control must not be able to freeze
    `main` — the same reasoning that moved secret scanning from the GitGuardian App to the
    first-party `secret-scan` job. Greptile still runs and still posts its review; nothing
    about its findings changed, only whether they hold the merge button.
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

### The gate's own trust model (why `voice-gate` is `pull_request_target`)

A `pull_request`-triggered workflow runs **the workflow file from the PR head**. For a workflow
that produces a REQUIRED context that is a self-referential hole: the PR authors the check that
gates it. A PR could delete the fork gate, repoint the "trusted" checkout at its own head, or make
the verdict `exit 0` — and for `voice-gate` the second of those means **arbitrary code execution on
the Jetson**, which also runs the live voice brain.

`voice-gate.yml` and `break-glass.yml` therefore both trigger on **`pull_request_target`**, which
reads the definition from the base ref. The PR under review cannot rewrite either one.

`pull_request_target` is dangerous by default — it runs with a write token and repository secrets —
and is safe here for exactly one reason: **neither workflow ever checks out or executes a byte the
PR author controls.** Every checkout pins `base.sha`; the PR enters only as an API-supplied
changed-file list and a 40-hex head sha. Adding a step that fetches the head ref, runs an install,
or executes a script from the PR tree converts this trigger into RCE with a write token.
`tests/unit/test_required_gate_workflows.py` asserts all of it: base-pinned checkouts,
`persist-credentials: false`, the fork gate, and that only these two workflows hold `checks: write`.

Because a `pull_request_target` run is associated with the **base** commit, the verdict cannot be a
job named `voice-gate` (it would report on the wrong SHA and the PR would wait forever). The
`verdict` job publishes a check run explicitly named `voice-gate` against `pull_request.head.sha`.

**What this trust model does and does not cover.** It protects the *self-hosted execution*
and the *honesty of the published result*: a PR cannot run its own code on the Jetson, and
cannot rewrite the verdict that gets published about it. It does NOT make the published check
unforgeable — any workflow job named `voice-gate` publishes a check of that name. That is
precisely why `voice-gate` is informational rather than required; see *Why `voice-gate` is not
a required context* below. A forged green `voice-gate` on an informational check buys an
attacker nothing except a misleading badge, and the deploy gate still refuses the change.

Three things bound the remaining exposure, in order of how much they actually do:

1. **`Require approval for all external contributors`** (repo setting; see below). A PR cannot
   disable it, and it stops fork workflows running at all until a maintainer approves. This is
   the real control for the self-hosted-runner exposure.
2. **Mandatory multi-agent review of every workflow change.** A `.github/workflows/**` diff is
   highly visible and is exactly what the cross-vendor review step exists to catch. This is
   process, not enforcement — treat it as such.
3. **The solo-owner threat model.** PRs come from the owner and trusted agents. This is the
   assumption everything above is proportionate to, and it is the assumption that would have to
   be revisited first if the repo ever accepted untrusted external contributions.

**Known residual, not closed:** a malicious edit to `voice-gate.yml` itself still lands if it
is MERGED (it cannot affect the PR that carries it, since the definition comes from the base
ref). That is a review problem, not a workflow one.

**The native GitHub settings that harden this further:**

- `Settings → Actions → General → Fork pull request workflows from outside collaborators →`
  **`Require approval for all external contributors`** — a maintainer must approve before ANY
  workflow runs for a fork PR. This is repo configuration, not workflow content, so a PR cannot
  disable it. It is the real control for the self-hosted-runner exposure; the `voice-gate-approved`
  label gate in the workflow is defence in depth on top of it, not a substitute.
- Branch protection **`Require review from Code Owners`** plus a `CODEOWNERS` entry for
  `/.github/**`, which forces a second party to approve any workflow change. This does not bite
  today (`required_approving_review_count` is 0 on a solo-owner repo) and turning it on would mean
  no PR can ever merge without a second human — which is why it is documented rather than enabled.

Running a self-hosted runner for a PUBLIC repo is something GitHub explicitly warns against. The
combination that makes it acceptable here is: trusted code only, fork PRs gated, and the external-
contributor approval setting above.

### Break-glass — recovery from a required-check outage

On 2026-07-27 a repo-wide Copilot outage deadlocked every open PR, including the one
carrying the fix. `.github/workflows/break-glass.yml` is the in-band escape:

1. Apply the **`break-glass`** label to the PR (create it once: `gh label create break-glass
   -d "audited admin override of a stuck required context"`), or run the workflow manually
   with a PR number and a reason.
2. It verifies the actor is a repo **admin** via the API — `write` (enough to apply a
   label) is deliberately not enough. A non-admin's label is stripped and refused.
3. It force-publishes the required contexts that are not already green, each carrying an
   output naming the override, the actor and the context's real state.
4. It posts a permanent audit comment and **removes the label**. The override is
   single-use and bound to that head commit; a new push re-blocks normally.

**What it actually overrides — stated plainly.** It cannot distinguish "the check never
reported because the vendor is down" from "the check ran and said no". **Any non-green
required context is forced, including a genuinely FAILING one.** `secret-scan` is in the
covered set, so this **can override a real secret-scan failure** and merge a commit the
scanner flagged. That is a deliberate, audited, owner-emergency capability: excluding
`secret-scan` would recreate the permanent freeze whenever GitGuardian is the thing that
is down, which is the failure this exists to fix. The audit comment records each context's
real state, so a forced `failure` is permanently distinguishable from a forced `missing`.

It does **not** bypass `required_conversation_resolution` — unresolved threads still block,
deliberately, because an outage cannot make a thread unresolvable. It also cannot force-push,
merge, or alter branch protection; it only publishes check runs through the normal API. The
intended use is an OUTAGE; using it on a genuinely red check is a decision to merge known-bad
code, permanently attributable to whoever did it.

### Cutover: nothing to change in branch protection

**The required set is already `validate` + `secret-scan` and stays that way.** `voice-gate`
is informational, so there is no context to add and no `gh api` call to run. The whole
post-merge step is creating the break-glass label:

```bash
gh label create break-glass -d "audited admin override of a stuck required context" -c B60205
```

If you want to confirm the required set is what this doc claims:

```bash
gh api repos/jason-easyazz/zoe-ai-assistant/branches/main/protection/required_status_checks \
  --jq '{strict, contexts}'
```

(An earlier draft of this work planned to add `voice-gate` here. That plan is withdrawn —
see *Why `voice-gate` is not a required context* below. If you are following an old runbook
or PR description that says to add it, do not.)

### Why `voice-gate` is not a required context

**A name-only required context cannot authenticate its PRODUCER.** Branch protection's legacy
`contexts` form matches purely by name, and GitHub publishes a check run for every workflow
job automatically — no `checks: write` needed. So if `voice-gate` were required, a PR could
satisfy it by adding any workflow with a job named `voice-gate`, on its own head, before
merge.

**A code-scanning guard against that is an arms race, and we lost the first round.** A guard
in the base-owned verdict job scanned the PR's changed workflow files for a competing
`voice-gate` producer. It keyed on a two-space-indented job id and was bypassed with **four
spaces**. Every fix in that direction is a new regex against an attacker with a YAML parser;
the guard is gone, along with the requirement that made it necessary.

**The requirement was never where voice enforcement lived.** What actually stops an unproven
voice change:

1. **The post-merge deploy gate — the real block, unchanged.** `deploy.yml` refuses to
   advance the live tree when the incoming diff touches the voice path without a fresh
   passing artifact. It is fail-closed, it runs on the box, and nothing in a PR can influence
   it. This is the control that matters.
2. **The visible PR-time result**, plus mandatory multi-agent review of the diff and
   `required_conversation_resolution`. A red `voice-gate` is unmissable on the PR and is meant
   to be heeded; it is enforced by review discipline, not by branch protection.

Note the cost of the descope, honestly: the "green main that will not deploy" gap is narrowed
rather than closed. A voice PR can still merge red and then be refused at deploy — the
difference is that now you were *told* before merging.

**The proper fix, deferred.** Authenticate the producer instead of its name:

- **Repository RULESETS → required workflows.** These authenticate by workflow **PATH**, not
  by check name, which is exactly the property missing here — and it is a settings change with
  no code to maintain and no regex to lose. This is the preferred path.
- **A dedicated GitHub App** publishing the context, with branch protection pinning the
  required context to its `app_id`.

Adopt either **only if this repo starts taking untrusted external contributors** — that is the
trigger condition, not a vague someday. Note that app-pinning also conflicts with break-glass
(a pinned context cannot be substituted by the Actions token), so that trade would have to be
re-decided at the same time.

**Still true of `validate` and `secret-scan`:** they are produced by `pull_request`-triggered
jobs, so a PR can weaken them in its own run. That is the general "a PR supplies its own CI"
property, covered by the same review discipline, and it is the reason the required set is kept
small and boring rather than clever.

## Greptile / greploop gotchas

Greptile is **advisory** as of 2026-07-31 — it no longer gates a merge, so none of these
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
- **Each branch-update re-runs all required checks.** It also re-triggers Greptile on any PR
  still carrying the `greptile` label, which frequently posts **new** threads — so updating a
  "clean" PR can un-clean it (the *re-review treadmill*). Since Greptile became advisory
  (2026-07-31) this is a cost and thread-resolution concern, not a merge blocker: only PRs you
  deliberately labelled for the advisory pass are affected. Still don't mass-update the batch.
- Branch-update re-review findings are usually **real** (the new commit pulls in others' merged
  changes): fix-if-real / reply+resolve-if-addressed — don't just re-update and hope. Note the
  resulting THREADS still block, because `required_conversation_resolution` applies to every
  thread regardless of who opened it.
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
- Every REQUIRED check must report on `merge_group`. The required set is `validate` + `secret-scan`,
  and both already carry `on: merge_group:`. **`voice-gate` is INFORMATIONAL, not required, so it is
  NOT a queue prerequisite** — the queue does not wait on it and it needs no `merge_group` path. (Its
  context is published against `pull_request.head.sha`; a merge_group event has no pull request, but
  since nothing gates on `voice-gate`, that is harmless rather than a stall.)
  **Greptile is no longer a factor here:** it was demoted to advisory on 2026-07-31, so a queue can
  never wait on a `Greptile Review` status that never arrives. Removing that dependency is one of
  the things the re-tiering bought.
- Changing branch protection / enabling the queue is an `enforce_admins`-protected operator action;
  agents prepare the `merge_group` CI wiring, the human flips the setting.

## Worktree hygiene

- Work in a **dedicated git worktree** (`~/.worktrees/<slug>`), branch off fresh `origin/main`. Never
  switch the live checkout (`/home/zoe/assistant`) to a feature branch for agent work.
- **One checkout-driver at a time.** Never run a background PR-merge/greploop driver (which does
  `git checkout`/`reset`) against the same working tree you're editing — it has silently wiped
  uncommitted work. Commit first, or isolate in a separate worktree.
- Branches die at merge (`delete_branch_on_merge`); merged task worktrees are auto-reclaimed.

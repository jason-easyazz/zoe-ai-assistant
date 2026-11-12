# Zoe self-heal loop (lab, v1)

Owner directive (2026-11-13): "if someone buys a zoe and adds in their chatgpt or claude
subscription then i want zoe to be able to evolve using this, but at the moment this box should
at least have the ability to heal itself and push those updates to github" - and - "self evolution
can work with github on this unit, but has to be able to work without github for people that
purchase zoe".

```
 detect (reads only)      ticket            engine (linked sub)      diff policy + gates        landing                     verify
 failing test on base ->  id, class,    ->  executor|codex|claude ->  forbidden paths, 300-line ->  github: DRAFT PR      ->  re-run the ORIGINAL
 (offline pytest lane)    evidence,         ~/.worktrees/heal-<id>    cap, test rule, struct,      local:  snapshot, ff,      reproduction; not
                          repro cmd                                   py_compile, pytest (flock)   apply, verify, rollback    healed -> parked
```

## One stack, three situations

The same stack runs on this unit and on a sold Zoe: Multica board + Flue executor + Omnigent
workers, the workers authenticated with whichever subscription is linked (the owner's here, the
buyer's on a sold unit). Idle cost [live, measured on this box 2026-11-13 by the coordinating
session]: Omnigent container ~237 MiB of its 4 GiB cap, Multica backend ~14 MiB, web ~3 MiB. A
WORKING worker's peak memory is unmeasured (the compose comment says a session adds
codebase-memory ~1.3 GB).

| situation | what the loop does |
|---|---|
| no subscription linked | local learning only (the router retrain loop), no code changes. This loop detects and tickets the fault, finds no engine, records `no_engine` ("no engine linked: ...") in the audit log and the ticket, touches nothing, exits 0. |
| a subscription, no GitHub (a sold Zoe) | same stack; `landing: local`: the FULL offline gate set, a snapshot ref, a compare-and-swap fast-forward of the local integration branch, apply, health checks, verify, and automatic rollback + park on any failure. No `gh`, GitHub Actions or cloud call in the landing path (tested with `gh` shadowed and the network blocked). Review is recorded as "not reviewed (single engine)". |
| a subscription + GitHub (this unit) | same stack; `landing: github`: one DRAFT PR per finding, labelled `self-heal`; the existing landing services take it only after a reviewer that is not the engine marks it ready. |

## Executor: what is reused, what is new

Engine adapters, tried in order `[engine, *engine_fallbacks]`; the first AVAILABLE one runs:

| engine | status |
|---|---|
| `flue_executor` (plan of record: Multica board -> Flue executor -> Omnigent worker; the executor already has the Omnigent lane, so there is no separate `omnigent` adapter) | Selected first in `self-heal.example.json`. Today it reports itself unavailable, with these blockers read live on 2026-11-13: (1) `~/.zoe/multica_dispatch_paused` exists, and the executor idles while it does (this loop never removes or bypasses it); (2) `flue-executor.service` is not installed; (3) there is no heal enqueue: the executor consumes `agent_task_queue` rows, its LOCAL lane's worker is synthetic (dead-end model, `labs/flue-executor/src/workflows/phase-worker.ts`), and its Omnigent lane runs in `zoe-omnigent`, which mounts only the live checkout (`/home/zoe/assistant` -> `/workspace`), not `~/.worktrees`, so a worker cannot see a heal worktree without editing the live checkout. |
| `codex` (thin CLI, `codex exec`, ChatGPT login) | The fallback when the executor path is unavailable (container down, dispatch paused). Available on this box (`codex login status`: logged in). Used for the real runs below. |
| `claude` (thin CLI, `claude -p`) | Fallback. NOT usable headless on this box today: `claude auth status` reports `loggedIn: false` for the host CLI binary. |
| `none` / nothing available | `no_engine`, see above. |

Smallest change that unblocks the executor path (each an owner decision, none made here):
(a) mount `~/.worktrees` into `zoe-omnigent` (compose; read-write, so a worker writes only its heal
worktree), (b) a ~40-line enqueue in this runner: INSERT one `agent_task_queue` row for the
executor's runtime with `work_dir` = the heal worktree, `context.lane = 'heavy'` and the
`build_prompt` brief, then wait for the row's terminal state with the same engine timeout,
(c) install + enable `flue-executor.service` in `full` dispatch, and (d) lift the Multica dispatch
kill switch, which the owner set and only the owner lifts. Until then the loop rides the thin adapter.

Reused as-is: `cross_review.sh --local` (second opinion), `night_busy.sh` + the voice-lander
`pgrep` (timing), the test-lane flock, `tools/audit/validate_structure.py`, the landing services
(merge + deploy of a ready PR), `multica_client.MULClient` (board mirror), the `codex` and
`claude` CLIs. New (this lab): detectors + tickets, the safety envelope, gates orchestration,
`gitops.py`, both landing adapters, the board mirror, verify/park, the audit log.

`worktree_bootstrap.ensure_worktree` is deliberately not reused: it always `git fetch`es and, if that
fails, silently falls back to the local base branch. A GitHub heal must start from a fresh
`origin/main` or not at all, and a local heal must make no network call. `selfheal/gitops.py`
does the few git operations the loop needs and logs each one.

`multica_board_runner.py` is not the driver either: it implements Multica issues through the
Omnigent executor and merges through the greploop; a heal's merge is the landing services' job,
and its claim loop is the paused Multica dispatch.

### Multica board mirror (best effort, optional)

`board: multica` mirrors each FAULT to one Multica issue via the existing `MULClient`: created in
`backlog` with NO assignee (never `todo`: the board runner claims `todo` issues and would run the
same fault a second time), a note on every state change, `in_review` when the PR opens, `done` on
healed, `blocked` on parked. Unconfigured or unreachable Multica = no mirror, audited, the heal
goes on. Tested only against a fake client; the lab created no real issue.

Owner decision, not built: let Multica DISPATCH drive heal tickets once it is unpaused (the heal
issue goes `todo`, the executor claims it). What must be true first: the Board Review autopilot
stops deferring to the retired Hermes cron; paused autopilots stop firing (the sync ignores
autopilot status, lane F); the executor path above is unblocked; and the heal-ticket safety
envelope (forbidden paths, diff cap, gates, verify, park) runs on the executor's result, not only
on this runner's.

### How a linked subscription reaches the workers (read-only findings; linking UI not built)

| vendor | how the worker authenticates today (names only) | what a linking flow would write |
|---|---|---|
| ChatGPT -> codex worker / thin `codex` | `codex login` ("Sign in with ChatGPT") stores auth under `CODEX_HOME` (`~/.codex` on the host; the `omnigent-codex` volume at `/root/.codex` in the container) | run `codex login --device-auth` (panel shows the QR/code, the buyer finishes on the phone) with `CODEX_HOME` pointing at the secret-backed dir that both the host engine and the container volume use |
| Claude -> polly brain / `claude_code` worker / thin `claude` | a stored `/login` credential in the `omnigent-claude` volume (`/root/.claude/.credentials.json`), refreshed by the CLI; or `CLAUDE_CODE_OAUTH_TOKEN` (one-year `claude setup-token`) via the omnigent env_file; `ZOE_OMNIGENT_ANTHROPIC_API_KEY` for polly's SDK brain only (`docs/knowledge/omnigent-cross-review.md`, "Polly Claude auth") | the `claude setup-token` result as `CLAUDE_CODE_OAUTH_TOKEN`, stored through the account-secrets seam (PR 2440's `account_secrets.py` design, not merged) and rendered into the omnigent env_file; never `ANTHROPIC_API_KEY` (it would flip every CLI to metered billing) |
| pi worker | `OPENROUTER_API_KEY` | not a buyer subscription; stays off on a sold unit |

Owner rule: connecting accounts is a QR on the panel, finished on the phone. No credential ever
enters a repo, log, ticket or PR body, and the engine's environment is an allow-list.

## Safety envelope (all in code, all tested with a fake engine)

| rule | where | test(s) in `tests/test_self_heal.py` |
|---|---|---|
| kill switch before every step (`~/.zoe/self_heal_paused`) | `Runner.step` | `test_kill_switch_refuses_before_anything`, `test_kill_switch_checked_before_every_step` |
| one heal at a time | `HealLock` (flock on `state_dir/heal.lock`) | `test_one_heal_at_a_time` |
| daily cap (3) / per-fault cap (2) / parked faults skipped | `State.check_caps` | `test_daily_cap`, `test_daily_cap_resets_next_day`, `test_per_fault_retry_cap`, rollback test |
| quiet hours 01:30-05:00 | `check_quiet_hours` | `test_quiet_hours` (6 boundaries), `test_quiet_hours_end_to_end` |
| not while a voice lander runs / night_busy says pause / night_busy missing (fail closed) | `check_busy` | `test_refuses_while_voice_lander_runs`, `test_night_busy` x2, `test_night_busy_exit_1_means_may_run` |
| forbidden paths (incl. `voice_gate_check.py`, every script deploy.yml / voice-gate.yml call, workflows, compose, units, landing, .env, alembic, zoe-auth, CANONICAL + invariants test, models, this lab, outside the repo) | `check_diff` on the staged diff after the engine | `test_forbidden_paths` (24 paths), `test_forbidden_path_discards_attempt_end_to_end` x3 |
| symlink escaping the repo | `gitops.diff_report` | `test_escaping_symlink_discarded` |
| diff cap (300 changed lines; binary = over cap) | `check_diff` | `test_diff_cap` |
| must include or update a test | `check_diff` (a failing-test ticket carries its regression test: the reproduction, verified from the ORIGINAL file) | `test_missing_test_rule` |
| empty diff / engine failure / engine timeout (process group killed) | runner, `engines._run` | `test_empty_diff_discarded`, `test_engine_failure_discards`, `test_engine_timeout_kills` |
| live checkout never touched | `Config.validate` + HEAD fingerprint around the engine | `test_config_refuses_paths_inside_live_checkout`, `test_live_checkout_guard` |
| engine env is an allow-list (no GH_*, MULTICA_*, DB URL, API keys) | `engines.engine_env` | `test_engine_env_is_an_allow_list` |
| executor unavailable -> thin fallback; nothing linked -> `no_engine`, no code touched, no cap spent | `engines.select_engine`, runner | `test_executor_unavailable_falls_back_to_thin_adapter`, `test_no_engine_linked_does_nothing_to_code` |
| board mirror: one issue per fault, `backlog`, no assignee, never `todo`; outage harmless; Multica + Omnigent + GitHub all absent still heals | `board.py` | `test_board_mirror_one_issue_per_fault_never_todo`, `test_board_mirror_done_on_heal`, `test_board_outage_is_harmless`, `test_runs_with_multica_omnigent_and_github_all_absent` |
| local gates (validate_structure, py_compile, relevant pytest under the flock) | `gates.py` | `test_gate_failure_discards` |
| a "fix" that weakens the test is not a heal | `gitops.run_repro` restores the reproducing file from the base commit | `test_full_local_landing_rolls_back_when_fix_only_weakened_the_test` |
| DRAFT only, label `self-heal`, marker block, one finding per PR, never ready / merge / --admin / --force | `GithubLanding` (no option exists to do otherwise) | `test_github_landing_opens_draft_only` |
| cross-review unavailable = not reviewed | `landing.cross_review` | `test_cross_review_unavailable_is_not_reviewed` |
| dispatch dry / local / full | `Runner` | `test_dry_plans_only`, `test_local_mode_commits_and_verifies_without_landing`, `test_full_local_landing_heals` |
| local landing: snapshot, CAS fast-forward, apply, health, verify, rollback, park | `LocalLanding` | `test_full_local_landing_heals`, `..._rolls_back_on_failed_health_check`, `..._when_fix_only_weakened_the_test`, `test_local_landing_refuses_when_integration_moved` |
| local landing uses no gh, no GitHub, no network | `LocalLanding` | `test_local_landing_needs_no_gh_and_no_network` (gh shadowed by a failing stub, `socket.connect` raises, command log has no fetch/push/pull/clone/gh) |
| verify after merge + deploy; not healed twice = parked | `Runner.verify_pending`, `_finish` | `test_verify_after_merge_heals_or_parks`, `test_not_healed_twice_parks` |
| audit log: every step, no content | `Runner.audit` | `test_audit_log_has_every_step_and_no_content` |

Marking a heal PR ready must come from a reviewer that is not the engine. There is no
`ready_when_green` option: the lander takes every open NON-DRAFT PR with resolved threads and
green checks, so draft is the only thing keeping a heal PR out of an unattended merge. A future
rule "ready only after a clean cross-vendor review by a different vendor than the engine" is an
owner decision and is not built. Note: `cross_review.sh` prefers codex, so with `engine: codex`
the review may be same-vendor; the PR body says so.

## Fault detectors

- **v1: failing test on the base branch** (`detectors.py`). Runs the configured offline lane
  (`detect_cmd`, default `-m ci_safe tests/unit`) in a throwaway detached worktree at the base
  commit with `--junitxml`, tickets each failure (`ignore_nodeids` skips known ones). Fault key =
  hash(class, node id), so caps and parking follow the fault across runs. Reproduction = the node id.
- **Not in v1: a service failing its health check with a traceback** (would read
  `zoe_ground_truth.sh` output + the service log's top frames, reproduction = the health probe),
  and **a failing nightly voice-regression artefact** (would read the probe's JSON, reproduction =
  the probe under its flock). Both need a reproduction that is safe to re-run unattended, and the
  voice one needs the voice replay gate as a local gate; v1 ships neither half-built.
- **Not in v1: struggles / evolution proposals.** They would plug in as a detector that turns an
  OWNER-ACCEPTED proposal into a ticket whose reproduction is a new failing test the engine writes
  first (test-first heal). Nothing is accepted today (lane F: 0 decisions since migration 0062).

## Local landing: the deployed `apply` (an owner step, not built)

The lab uses `NoopApplier`, which records what it would do. A deployed unit's apply would:
1. `git -C <live_checkout> merge --ff-only <integration sha>` (rollback: check out the snapshot sha);
2. restart only the units whose paths changed (`systemctl --user restart <unit>`);
3. wait for each unit's health (the `zoe_ground_truth.sh` checks) - then the loop's `health_checks`
   and the reproduction run; any failure rolls the integration branch AND the live checkout back to
   the snapshot and restarts again. The voice replay (`voice_replay_cmd`) runs as a gate BEFORE the
   merge; a voice-path diff with no replay configured fails closed.

## PROVE IT: results

Unit tests (fake engine, throwaway repos, no network):

```bash
cd ~/assistant   # or any checkout of this branch
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 -m pytest -p no:cacheprovider labs/self-heal/tests -q
# 2026-11-13: 77 passed in ~36 s
```

Negative controls (each mutation applied, the named tests run, the file restored; every one went red):
`voice_gate_check.py` dropped from the forbidden list -> 2 failed; verify not restoring the original
test file -> 1 failed (the weakened-test heal would have landed); kill switch not checked per step ->
2 failed; quiet hours skipped -> 1 failed; local rollback skipped -> 2 failed; PR opened without
`--draft` -> 1 failed; daily cap off -> 1 failed; diff cap off -> 1 failed.

Real engine, synthetic fault (`calc.add` subtracts; `tests/test_calc.py::test_add` fails), throwaway
repo under `/tmp`, codex-cli 0.163.0 on the owner's ChatGPT login, one attempt, 600 s engine timeout,
no push, no PR, nothing in the real repo:

```bash
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario local    --codex-bin "$(command -v codex)"
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario land     --codex-bin "$(command -v codex)"
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario rollback --codex-bin "$(command -v codex)"
```

| scenario | wall | engine | outcome |
|---|---|---|---|
| `local` (dispatch local) | 18 s | 15.4 s, "Changed `add()` to use addition instead of subtraction" | ticket -> worktree -> diff `calc.py` +1/-1 -> gates (structure skip: no validator in the scratch repo; py_compile pass; relevant pytest 2 passed) -> local commit -> verify (original test) `1 passed` -> `healed_local`; integration branch untouched |
| `land` (full, landing local) | 18 s | 15.0 s | full gate set green -> snapshot `refs/self-heal/snapshots/<id>` -> CAS fast-forward -> noop apply -> verify `1 passed` -> `healed`; integration main = the heal commit; review "not reviewed (single engine)" |
| `rollback` (full, landing local, health check `false`) | 17 s | 15.1 s | gates green -> snapshot -> fast-forward -> apply -> health check rc=1 -> rollback (integration main back to the snapshot, `restored: true`) -> fault `parked` |

Audit lines from the `local` run (abridged to the non-`start` rows):

```
detect   done        found=1
ticket   chosen      fault_class=failing_test repro="python3 -m pytest -p no:cacheprovider -q tests/test_calc.py::test_add"
engine   ok          detail="rc=0 Changed `add()` to use addition instead of subtraction; the specified test passes." seconds=15.4
diff_policy pass     files=["calc.py"] lines=2
gates    green       validate_structure skip, py_compile pass, relevant_pytest pass (2 passed)
commit   done        sha=0922773...
result   healed_local verify="rc=0 1 passed"
```

Not run, by design: `full` mode with `landing: github` (no push, no PR from the lab), any run against
the real repo's real faults, the Flue executor path (blocked, see above).

## Owner steps to switch it on (this unit)

Each step: run it, then look at what it names before the next. Off switch at any time:
`touch ~/.zoe/self_heal_paused` (the next step of a running heal refuses) or
`systemctl --user disable --now zoe-self-heal.timer`.

1. Prepare (nothing runs yet; the kill switch is armed first):
   ```bash
   touch ~/.zoe/self_heal_paused
   git -C ~/.worktrees/qa8-fixes fetch origin main
   git -C ~/.worktrees/qa8-fixes worktree add --detach ~/.worktrees/self-heal-base origin/main
   mkdir -p ~/.zoe/self-heal
   cp ~/assistant/labs/self-heal/self-heal.example.json ~/.zoe/self-heal/config.json
   gh label create self-heal --color 0e8a16 --description "opened by Zoe's self-heal loop"
   ```
   Look at: `~/.zoe/self-heal/config.json` (dispatch `dry`, engine `flue_executor` falling back to `codex`).
2. One dry run by hand:
   ```bash
   rm ~/.zoe/self_heal_paused
   cd ~/assistant/labs/self-heal && python3 -m selfheal run --config ~/.zoe/self-heal/config.json
   tail -n 20 ~/.zoe/self-heal/audit.jsonl
   ```
   Look at: outcome `planned` or `no_fault`; the `engine_select` row (expect `flue_executor` skipped
   with the three blockers, `codex` chosen); the detect wall time (the real lane's cost is unmeasured).
3. Install the timer in dry (three daytime passes a day):
   ```bash
   cp ~/assistant/labs/self-heal/systemd/zoe-self-heal.{service,timer} ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now zoe-self-heal.timer
   systemctl --user list-timers zoe-self-heal.timer
   ```
   Watch 6 runs (2 days): `grep -c '"step": "error"' ~/.zoe/self-heal/audit.jsonl` must stay 0; no
   `night_busy_missing`; the tickets it plans are faults you agree it should fix.
4. Switch to `local` (engine runs, gates run, commit stays on a local branch, nothing pushed):
   ```bash
   sed -i 's/"dispatch": "dry"/"dispatch": "local"/' ~/.zoe/self-heal/config.json
   ```
   Watch 3 heals: `git -C ~/.worktrees/self-heal-base branch --list 'self-heal/*'`, then
   `git -C ~/.worktrees/heal-<id> show` for each; outcomes `healed_local` vs `discarded` and why.
5. Switch to `full` (draft PRs):
   ```bash
   sed -i 's/"dispatch": "local"/"dispatch": "full"/' ~/.zoe/self-heal/config.json
   gh pr list --label self-heal --draft
   ```
   Look at: each PR's body (fault, evidence, gates, second opinion, the `zoe-self-heal:v1` block).
   A heal PR leaves draft only when a reviewer that is not the engine says so (`gh pr ready <n>`);
   the landers then merge and deploy it, and the timer's `verify` pass records healed / not healed.
6. Optional board mirror: put `MULTICA_BASE_URL`, `MULTICA_API_TOKEN`, `MULTICA_WORKSPACE_ID` in
   `~/.zoe/self-heal/multica.env`, uncomment `EnvironmentFile=` in the unit, `daemon-reload`.

## What a sold Zoe still needs

- The linking flow (QR on the panel, phone finish, secrets via the account-secrets seam) for both vendors, written to the places in the table above.
- The executor path unblocked (above), so the heal rides Multica + Flue executor + Omnigent on both kinds of unit; the thin CLI stays as the fallback.
- The live `apply` behind an owner step, with the local voice replay wired as `voice_replay_cmd`.
- A readable landing record for the owner ("what changed", undo) - today only the audit log and the snapshot refs.
- An `offline_lane_cmd` that runs on the sold box's own venv.

## Known risks before switching on (read these first)

- **Gates execute engine-written code unsandboxed.** The engine is sandboxed (codex `workspace-write`,
  claude allow-list), but `pytest` in the gates imports the changed code as the `zoe` user with full
  access. A malicious or confused change runs before any human sees it. Mitigation not built: run
  the gates inside the same sandbox (e.g. `codex sandbox` or bubblewrap with no network, write
  access only to the worktree).
- **Same-vendor second opinion.** With `engine: codex`, `cross_review.sh` (codex first) may review
  codex's own work. The PR body says so; the draft rule is what protects `main`.
- **Detector cost and flakes are unmeasured on the real lane.** A flaky test becomes a ticket; the
  per-fault cap (2) and parking bound it, but measure step 2's detect time first.
- **The `self-heal` label must exist** or `gh pr create` fails (step 1 creates it).
- **`verify` treats "deployed" as "an ancestor of local `refs/heads/main`"**, which is the live
  checkout's branch on this box (deploy fast-forwards it). A different deploy model needs a different check.

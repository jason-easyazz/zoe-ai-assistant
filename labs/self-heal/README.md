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

| rule | where | test(s) in `tests/test_self_heal.py` / `tests/test_self_heal_review.py` (R) |
|---|---|---|
| kill switch before every step; the built-in `~/.zoe/self_heal_paused` is always honoured, config can only ADD switches | `Runner.step`, `Config.all_kill_switches` | `test_kill_switch_refuses_before_anything`, `test_kill_switch_checked_before_every_step`, R `test_builtin_kill_switch_cannot_be_configured_away` |
| one heal at a time | `HealLock` (flock on `state_dir/heal.lock`) | `test_one_heal_at_a_time` |
| HARD FLOOR: daily cap <= 3, per-fault cap <= 2, diff cap <= 300, park-after <= 2; forbidden list may only grow; quiet window always includes 01:30-05:00. A weaker config is REFUSED at load (not clamped), and the limits are re-applied at use | `Config.validate`, `State.check_caps`, `check_diff`, `check_quiet_hours` | R `test_config_cannot_weaken_the_floor` (10 configs), `test_config_may_tighten`, `test_floor_holds_even_when_mutated_after_load`, `test_builtin_quiet_window_applies_whatever_the_config`, `test_daily_cap`, `test_per_fault_retry_cap` |
| quiet hours 01:30-05:00 | `check_quiet_hours` | `test_quiet_hours` (6 boundaries), `test_quiet_hours_end_to_end` |
| not while a voice lander runs / night_busy says pause / night_busy missing (fail closed) | `check_busy` | `test_refuses_while_voice_lander_runs`, `test_night_busy` x2, `test_night_busy_exit_1_means_may_run` |
| forbidden paths (incl. `voice_gate_check.py`, every script deploy.yml / voice-gate.yml call, workflows, compose, units, landing, .env, alembic, zoe-auth, CANONICAL + invariants test, models, this lab, outside the repo) | `check_diff` on the staged diff after the engine | `test_forbidden_paths` (24 paths), `test_forbidden_path_discards_attempt_end_to_end` x3 |
| the forbidden floor stays a LITERAL list (never computed from a workflow at run time); a test parses the `run:` blocks of `deploy.yml` and `voice-gate.yml` and fails, naming the script, when one runs a repo file the floor does not cover (`tools/audit/flag_inventory.py` added this way) | `config.DEFAULT_FORBIDDEN` | `test_every_workflow_run_script_is_forbidden`, `test_coverage_check_names_a_dropped_entry`, `test_forbidden_floor_is_a_literal_list` |
| inspect BEFORE staging: `git status` (untracked included) + symlink check, nothing staged; any violation discards the worktree; then only the checked paths are staged (no `add -A`) and the index is proven to hold exactly them; an engine that commits OR puts anything in the index (even an allowed file) is discarded: the runner alone stages | `gitops.inspect_worktree`, `gitops.stage_checked` | `test_escaping_symlink_discarded`, R `test_violation_never_stages_or_commits`, `test_only_checked_paths_are_committed`, `test_stage_checked_refuses_anything_extra`, `test_engine_commit_is_discarded`, `test_engine_made_index_or_commit_is_discarded` (stage allowed / stage forbidden / commit) |
| diff cap (300 changed lines; binary = over cap) | `check_diff` | `test_diff_cap` |
| must include or update a test | `check_diff` (a failing-test ticket carries its regression test: the reproduction, verified from the ORIGINAL file) | `test_missing_test_rule` |
| empty diff / engine failure / engine timeout (process group killed) | runner, `engines._run` | `test_empty_diff_discarded`, `test_engine_failure_discards`, `test_engine_timeout_kills` |
| live checkout never touched: `/home/zoe/assistant` (and its realpath) is HARD-CODED as refused for repo, worktree parent, state dir and apply target, and no command runs in it or with `git -C` into it; `live_checkout` in the config is an ADDITIONAL refused path; HEAD fingerprint around the engine | `config.inside_refused`, `gitops._guard`, `NoopApplier` | `test_config_refuses_paths_inside_live_checkout`, `test_live_checkout_guard`, R `test_live_checkout_refused_through_a_symlink` (4 fields x 2), `test_live_checkout_refused_even_if_config_names_another`, `test_gitops_never_runs_in_the_live_checkout`, `test_apply_target_refused` |
| engine env is an allow-list (no GH_*, MULTICA_*, DB URL, API keys) | `engines.engine_env` | `test_engine_env_is_an_allow_list` |
| engine argv is BUILT IN: `codex_args` / `claude_args` must be empty (refused at load), the one option is a bare `model` name, `codex_bin` / `claude_bin` must be named `codex` / `claude`; the final argv is asserted to carry each safety flag exactly once with the built-in value (codex `-C <worktree>`, `-s workspace-write`, `--ignore-user-config`; claude `--permission-mode dontAsk`, the fixed tool list, prompt after `--`) and no bypass / extra-dir / config / `--flag=value` token | `Config.validate`, `engines.check_codex_argv`, `check_claude_argv` | R `test_hostile_engine_config_is_refused` (14 configs), `test_model_name_is_the_one_allowed_option`, `test_final_codex_argv_assertion` (6), `test_final_claude_argv_assertion` (5) |
| ONE interpreter: `python` must be the running interpreter (`sys.executable`); a python health check, the detect / offline lanes (`<python> -m pytest <relative args>`) and the voice replay (`<python> <rel.py>`) must start with it; anything else is refused at load and never runs | `config.is_pinned_python`, `check_pytest_argv`, `check_script_argv`, `check_health_argv` | R `test_python_override_never_runs` (7 hostile configs, a recording executable that must never run), `test_python_is_pinned_even_if_mutated` |
| executor unavailable -> thin fallback; nothing linked -> `no_engine`, no code touched, no cap spent | `engines.select_engine`, runner | `test_executor_unavailable_falls_back_to_thin_adapter`, `test_no_engine_linked_does_nothing_to_code` |
| board mirror: one issue per fault, `backlog`, no assignee, never `todo`; outage harmless; Multica + Omnigent + GitHub all absent still heals | `board.py` | `test_board_mirror_one_issue_per_fault_never_todo`, `test_board_mirror_done_on_heal`, `test_board_outage_is_harmless`, `test_runs_with_multica_omnigent_and_github_all_absent` |
| local gates (validate_structure, py_compile, relevant pytest under the flock) | `gates.py` | `test_gate_failure_discards` |
| a "fix" that weakens the test is not a heal | `gitops.run_repro` restores the reproducing file from the base commit | `test_full_local_landing_rolls_back_when_fix_only_weakened_the_test` |
| DRAFT only, label `self-heal`, marker block, one finding per PR, never ready / merge / --admin / --force | `GithubLanding` (no option exists to do otherwise) | `test_github_landing_opens_draft_only` |
| cross-review unavailable = not reviewed; the PR carries the review STATUS only, never the reviewer's text | `landing.cross_review` | `test_cross_review_unavailable_is_not_reviewed` |
| dispatch dry / local / full | `Runner` | `test_dry_plans_only`, `test_local_mode_commits_and_verifies_without_landing`, `test_full_local_landing_heals` |
| local landing: snapshot, CAS fast-forward, apply, health, verify, rollback, park. Health checks are an ALLOW-LIST checked at load and again at run: `true`/`false`, `systemctl --user is-active <unit>`, `<python> -m <module>` or `<python> <rel/path.py>`; no shell strings, no network-capable binary or shell (curl, wget, gh, ssh, scp, rsync, nc, git, docker, bash, sh, env, ...), no absolute/`..` paths, no `python -c`; each runs with its own timeout in a FRESH worktree of the integration tip, never the live checkout | `LocalLanding`, `config.check_health_argv` | `test_full_local_landing_heals`, `..._rolls_back_on_failed_health_check`, `..._when_fix_only_weakened_the_test`, `test_local_landing_refuses_when_integration_moved`, R `test_health_check_not_allow_listed_fails_the_load` (18 argvs), `test_health_check_allowed_forms`, `test_health_check_runs_in_a_fresh_integration_worktree` |
| local landing rolls back on ANY exit that is not a confirmed heal: a failed check, an exception in apply / health / verify (recorded as `exception:<Class>`, never the message), even KeyboardInterrupt / SystemExit (rolled back and recorded, then re-raised); the fault is parked like a failed verify; a failing live rollback is recorded as `rollback_apply_failed:<Class>` with the ref still restored | `LocalLanding.land` | `tests/test_self_heal_round3.py`: `test_apply_raises`, `test_health_check_raises_when_systemctl_is_missing`, `test_verify_raises`, `test_base_exception_rolls_back_records_then_reraises` (x2), `test_rollback_apply_failure_is_recorded_and_ref_still_restored` |
| local landing uses no gh, no GitHub, no network | `LocalLanding` | `test_local_landing_needs_no_gh_and_no_network` (gh shadowed by a failing stub, `socket.connect` raises, command log has no fetch/push/pull/clone/gh) |
| verify after merge + deploy; not healed twice = parked | `Runner.verify_pending`, `_finish` | `test_verify_after_merge_heals_or_parks`, `test_not_healed_twice_parks` |
| NO CONTENT in any record: audit keys are an allow-list and every string value must be one whitespace-free token (else `<redacted>`); engine output is never kept (`rc=N` only); gate / verify results are `rc=` + counts; ticket evidence = node id, failure kind, a HASH of the failure text, base sha; the PUBLIC PR body = ticket id, fault class, test node id, engine, changed file names, gate names + pass/fail, review status, marker block; the board gets the same | `Runner.audit`, `safety.rc_summary`, `detectors`, `landing.pr_body` | `test_audit_log_has_every_step_and_no_content`, R `test_no_content_reaches_any_record` (github/local x engine ok/failed, planted token + sentence in the assertion message AND the engine output, scanned in every state-dir file, the PR body, board payloads, gh args and the git command log), `test_gate_failure_output_does_not_leak`, `test_audit_refuses_unknown_keys` |
| re-checked MID-RUN: kill switch + quiet hours + voice lander + night_busy before the engine, the gates, the landing, the local fast-forward, the push and the PR call, and before EACH pending item in `verify`; a failure = `interrupted` with the rule, worktree discarded, NO cap spent | `Runner.checkpoint`, `LandCtx.checkpoint` | R `test_interrupted_mid_run` (9 checkpoint x condition cases), `test_verify_pending_rechecks_before_each_item` |

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
# 2026-11-13: 195 passed in ~74 s
```

Negative controls (each mutation applied, the named tests run, the file restored; every one went red):
v1: `voice_gate_check.py` off the forbidden list, verify not restoring the original test, no per-step
kill-switch check, quiet hours skipped, local rollback skipped, PR without `--draft`, daily cap off,
diff cap off. Review round (PR 2443), 16 mutations: forbidden list allowed to shrink (2 red), caps
allowed to rise (5), quiet window allowed to narrow (3), built-in kill switch dropped (1), hard-coded
live path dropped (6), realpath not resolved (4), blanket `add -A` before the checks (2), no
staged-set proof (1), health allow-list off at load (18), detector keeps failure text (9), engine
keeps model output (1), runner audits engine output (4), audit sanitizer off (1), rc_summary keeps
output (1), no mid-run checkpoints (7), cap spent on interrupt (9). Re-review round 2, 15 mutations: engine args accepted at load (9), model
not validated (3), engine binary name unchecked (2), codex flag-set check off (1), codex `-s` value
unchecked (1), claude flag-set check off (1), python not pinned (2), health argv0 not pinned (1),
lane argv0 not pinned (1), engine-made index accepted (2), verify re-checked once only (1). The codex
danger-prefix list alone is redundant behind the exact flag-set check (removing only it stays green). The privacy rule has several
layers (source stripping, then the audit's allow-listed keys and single-token values); each layer
has its own test and its own negative control.

Real engine, synthetic fault (`calc.add` subtracts; `tests/test_calc.py::test_add` fails), throwaway
repo under `/tmp`, codex-cli 0.163.0 on the owner's ChatGPT login, one attempt, 600 s engine timeout,
no push, no PR, nothing in the real repo (re-run after the review fixes):

```bash
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario local    --codex-bin "$(command -v codex)"
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario land     --codex-bin "$(command -v codex)"
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/self-heal/e2e_scratch.py --engine codex --scenario rollback --codex-bin "$(command -v codex)"
```

| scenario | wall | engine | outcome |
|---|---|---|---|
| `local` (dispatch local) | 20 s | 18.5 s, rc=0 | ticket -> worktree -> inspected `calc.py` (2 lines, nothing staged yet) -> gates (structure skip: no validator in the scratch repo; py_compile pass; relevant pytest 2 passed) -> stage `calc.py` only -> local commit -> verify (original test) `rc=0,passed=1` -> `healed_local`; integration branch untouched |
| `land` (full, landing local) | 20 s | 17.4 s, rc=0 | full gate set (incl. offline lane) green -> snapshot `refs/self-heal/snapshots/<id>` -> CAS fast-forward -> noop apply -> verify passes -> `healed`; review `not_reviewed_single_engine` |
| `rollback` (full, landing local, health check `false`) | 18 s | 15.9 s, rc=0 | gates green -> snapshot -> fast-forward -> apply -> `health_failed:rc=1` -> integration main back to the snapshot (`restored: true`) -> fault `parked` |

Audit lines from the `local` run (the non-`start` rows; this is the whole record, no content):

```
detect      done          found=1
ticket      chosen        fault_class=failing_test fault_key=a374bd420177 test=tests/test_calc.py::test_add
engine      ok            rc=rc=0 seconds=18.5
diff_policy pass          files=["calc.py"] lines=2
gates       green         validate_structure skip no_validator | py_compile pass files=1,rc=0 | relevant_pytest pass files=1,rc=0,passed=2
commit      done          sha=2d14fd0...
result      healed_local  branch=self-heal/<id> sha=2d14fd0... verify=rc=0,passed=1
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
   cd ~/assistant/labs/self-heal && ~/.zoe/venvs/zoe-data-py312/bin/python -m selfheal run --config ~/.zoe/self-heal/config.json
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
   Look at: each PR's body (ticket id, fault class, test node id, engine, changed file names, gate
   pass/fail, review status, the `zoe-self-heal:v1` block; no failure text, model output or diff by design).
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

# labs/self-heal - Zoe's self-heal loop (lab)

## Purpose

One autonomous loop: Zoe notices a fault in herself (v1: a failing test on the base branch),
a coding engine (codex or claude, the buyer's subscription) fixes it in an isolated git
worktree, local gates pass, and the fix lands through a landing adapter (`github`: a DRAFT PR
for the existing landing services; `local`: a snapshot-guarded local merge with automatic
rollback, for a Zoe with no GitHub). A verify step re-runs the fault's reproduction and records
healed / not healed; a fault that is not healed is parked, not retried forever.

## Ownership

`selfheal/` (config, safety, sandbox, binaries, detectors, engines, gates, gitops, landing, runner, CLI),
`tests/` (fake-engine tests on throwaway repos), `e2e_scratch.py` (synthetic end-to-end proof),
`systemd/` (user unit + timer TEMPLATES), `self-heal.example.json`, `README.md`.

## Local Contracts

- Dispatch `dry` (default: plan only) | `local` (engine + gates + local commit + verify, no landing) | `full` (land).
- Landing `github` (default) only ever opens a DRAFT PR labelled `self-heal`, one finding per PR,
  with the `zoe-self-heal:v1` marker block. Landing `local` makes no `gh`, GitHub or network call.
- Every step checks the kill switch first and appends a row to `<state_dir>/audit.jsonl` (allow-listed
  keys, single-token values). Before the engine, gates, landing, push, PR call and each pending verify item the run re-checks
  kill switch, quiet hours, voice lander and night_busy.
- Engines are tried in order `[engine, *engine_fallbacks]`; the first available runs. `flue_executor`
  (Multica + Flue executor + Omnigent, the plan of record) reports its blockers until they are lifted;
  `codex` / `claude` are thin CLI fallbacks whose flags come from the CLI's own `--help`. No engine
  available = `no_engine`, nothing touched. The engine environment is an allow-list (no tokens).
- `board: multica` mirrors one issue per fault (backlog, no assignee, never `todo`); best effort,
  never required, never touches Multica dispatch or its kill switch.

## Forbidden

The loop (and anyone running it) must never:

- merge a PR, mark one ready, or use `--admin` / `--force` (no option to do so exists: keep it that way);
- run any command in the live checkout or `git -C` into it, or use it (or anything resolving into it,
  symlinks included) as repo, worktree parent, state dir or apply target: `/home/zoe/assistant` is
  hard-coded as refused; `live_checkout` in the config only ADDS a refused path;
- let a config loosen the hard floor: the forbidden list and kill switches may only grow, caps only
  shrink (daily 3, per fault 2, diff 300, park after 2), quiet hours only widen around 01:30-05:00;
  a weaker config is refused at load, never clamped;
- stage anything before the diff policy passed, stage with `add -A`, or stage a path it did not check;
- run a health check that is not on the allow-list in `config.check_health_argv`, or any interpreter but
  the running one (`python`, lanes, replay and health checks are pinned to `sys.executable`);
- accept extra engine arguments (`codex_args` / `claude_args` must be empty), a non-`codex` / `claude`
  engine binary, or a final engine argv whose sandbox / approval / cwd flags differ from the built-ins;
- accept an index or a commit the engine made (the runner alone stages, after the checks);
- put failure text, assertion text, tracebacks, model or reviewer output, or diff excerpts into the
  audit log, a ticket, the PR body (public) or the board; ids, classes, counts, durations, file
  names, test node ids and exit codes only;
- continue a heal when a mid-run re-check (kill switch, quiet hours, voice lander, night_busy) fails:
  stop, discard the worktree, record `interrupted`, spend no cap;
- let an engine diff touch the forbidden list in `selfheal/config.py` (`.github/workflows/`, compose files,
  systemd units, `scripts/landing/`, `.env*`, alembic, zoe-auth, `docs/CANONICAL.md` and its invariants
  test, the voice gate and every script `deploy.yml` / `voice-gate.yml` call, model files, this lab, any
  path outside the repo); a violation discards the attempt;
  the list is literal and `tests/test_self_heal_round3.py` fails when a workflow runs a repo script it misses;
- leave the integration ref advanced after a local landing that did not confirm a heal: every exit,
  exceptions included, restores the snapshot;
- run ANY repo code from a heal or base worktree (detector, gates, verify, python health checks) outside
  `selfheal/sandbox.py`; select a backend weaker than the best one that works on the box (`none` while
  bwrap works); add a config knob for network or writable paths; raise the sandbox limits; run
  `local` / `full` when no sandbox backend works (only `dry` may run, reason audited);
- count a heal as healed on exit code alone: the reproduction NODE must have passed in the junit report;
  accept an engine diff that adds a skip / xfail to a test file or touches `conftest.py` / pytest config;
- heal from a detect run that has collection errors or no junit report;
- start a second heal for a fault that has an open heal PR or an unlanded local heal branch;
- delete a remote branch that is not the exact `self-heal/<id>` of a recorded ticket, or one that has a PR;
- advance the integration ref without a write-ahead `landing_intent`, or clear it before a confirmed heal
  or a VERIFIED rollback (ref read back == snapshot); while a rollback is `rollback_stuck`, run anything
  (dry included) but the retry;
- give a sandboxed step a writable `/tmp` or any scratch outside the worktree, or skip the worktree
  growth cap (`sandbox_disk_mb` may only be lowered);
- accept a diff that deletes or renames any file under `tests/`, `test_*.py`, `*_test.py` or `conftest.py`;
- pass a heal whose pytest gate shows, against the base-commit baseline, a previously-run node now
  skipped / xfailed / deselected / missing, or a reproduction node that did not go failing -> passed;
- run a binary from the inherited PATH, or one that is group/world-writable, foreign-owned, or under the
  worktrees, the state dir or a temp dir (`selfheal/binaries.py`);
- run between 01:30 and 05:00, while a voice lander runs, or while `night_busy.sh deploy` says pause;
- record a missing or failed cross-review as reviewed;
- remove or bypass `~/.zoe/multica_dispatch_paused` or any other kill switch, or create a board issue in `todo`;
- be installed or enabled as a unit by an agent: the units under `systemd/` are operator opt-in templates;
- run against the real repo's real faults in a lab session (scratch repos only, see `e2e_scratch.py`);
- implement a live `apply` (fast-forward the live checkout, restart services) without an owner step.

## Work Guidance

- Adopt, don't rebuild: reuse `cross_review.sh`, `night_busy.sh`, the test-lane flock and the landing
  services; the README says why this is a thin runner rather than the board runner or the Flue executor.
- New fault detectors produce a `Ticket` with a machine reproduction; a detector only reads.

## Verification

`flock -x -w 14000 /tmp/zoe-test-lane.lock python3 -m pytest -p no:cacheprovider labs/self-heal/tests -q`
(local net; labs are not in CI by design). End-to-end: `python3 labs/self-heal/e2e_scratch.py --help`.

## Child DOX Index

None.

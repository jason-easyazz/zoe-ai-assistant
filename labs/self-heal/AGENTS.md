# labs/self-heal - Zoe's self-heal loop (lab)

## Purpose

One autonomous loop: Zoe notices a fault in herself (v1: a failing test on the base branch),
a coding engine (codex or claude, the buyer's subscription) fixes it in an isolated git
worktree, local gates pass, and the fix lands through a landing adapter (`github`: a DRAFT PR
for the existing landing services; `local`: a snapshot-guarded local merge with automatic
rollback, for a Zoe with no GitHub). A verify step re-runs the fault's reproduction and records
healed / not healed; a fault that is not healed is parked, not retried forever.

## Ownership

`selfheal/` (config, safety, detectors, engines, gates, gitops, landing, runner, CLI),
`tests/` (fake-engine tests on throwaway repos), `e2e_scratch.py` (synthetic end-to-end proof),
`systemd/` (user unit + timer TEMPLATES), `self-heal.example.json`, `README.md`.

## Local Contracts

- Dispatch `dry` (default: plan only) | `local` (engine + gates + local commit + verify, no landing) | `full` (land).
- Landing `github` (default) only ever opens a DRAFT PR labelled `self-heal`, one finding per PR,
  with the `zoe-self-heal:v1` marker block. Landing `local` makes no `gh`, GitHub or network call.
- Every step checks the kill switch first and appends a row to `<state_dir>/audit.jsonl`.
- Engines are tried in order `[engine, *engine_fallbacks]`; the first available runs. `flue_executor`
  (Multica + Flue executor + Omnigent, the plan of record) reports its blockers until they are lifted;
  `codex` / `claude` are thin CLI fallbacks whose flags come from the CLI's own `--help`. No engine
  available = `no_engine`, nothing touched. The engine environment is an allow-list (no tokens).
- `board: multica` mirrors one issue per fault (backlog, no assignee, never `todo`); best effort,
  never required, never touches Multica dispatch or its kill switch.

## Forbidden

The loop (and anyone running it) must never:

- merge a PR, mark one ready, or use `--admin` / `--force` (no option to do so exists: keep it that way);
- run git with the live checkout (`/home/zoe/assistant`) as its working tree, or write anything in it;
  `repo`, `worktree_root` and `state_dir` inside the live checkout are refused by `Config.validate`;
- let an engine diff touch the forbidden list in `selfheal/config.py` (`.github/workflows/`, compose files,
  systemd units, `scripts/landing/`, `.env*`, alembic, zoe-auth, `docs/CANONICAL.md` and its invariants
  test, the voice gate and every script `deploy.yml` / `voice-gate.yml` call, model files, this lab, any
  path outside the repo); a violation discards the attempt;
- run between 01:30 and 05:00, while a voice lander runs, or while `night_busy.sh deploy` says pause;
- record a missing or failed cross-review as reviewed;
- remove or bypass `~/.zoe/multica_dispatch_paused` or any other kill switch, or create a board issue in `todo`;
- be installed or enabled as a unit by an agent: the units under `systemd/` are operator opt-in templates;
- run against the real repo's real faults in a lab session (scratch repos only, see `e2e_scratch.py`);
- write household content (utterances, names, rows) or secrets into the audit log, a ticket or a PR body;
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

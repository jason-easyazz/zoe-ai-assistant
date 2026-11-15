# speed-loop

## Purpose
[code] Unwired response-speed research loop, fake by default; owner-started guarded
real pre-TTS replay via run_window.sh. No measured production improvement yet.

## Ownership
The builder owns lab artifacts. Owner/coordinator grants windows, runs real replay,
reviews proposals, and owns any separate product PR or deployment.

## Forbidden
- No production/config edits, service control, commit, push, deploy or automatic apply.
- No safety-floor, forget/erase, auth, policy or model-selection targets.
- No real replay without all guards and exclusive harness lock.
- No household text in artifacts, raw instrument output files, or env-value printing.
- No lab environment names starting with ZOE_; registered product flags are data.

## Work Guidance
Read README.md and program.md. windows.json is a proposal, not occupancy evidence.
Never bypass a refused guard. Only FakeInstrument may run during offline testing.
User unit templates are owner opt-in files only, never installed by a builder.

## Verification
Hand-run test_speed_loop.py and test_replay.py under root quiet/memory/test-lane
rules. Neither is ci_safe or wired into CI. Report unrun tests explicitly.

## Child DOX Index
- loop.py: original offline loop and AST-verified surface.
- replay.py: guards, child isolation, interleaving, calibration, scheduler and artifacts.
- instrument.py: memory-only canonical replay projection; coordinator window only.
- fake_instrument.py: synthetic child for offline adapter tests.
- run_window.sh: single owner entry; service/timer files are uninstalled templates.
- README.md: classification/evidence, statistics, commands and unproven claims.
- surface.json/windows.json: public contracts; runs/: ignored private numeric evidence.

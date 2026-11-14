---
type: Reference
title: Offline test tier
description: The hermetic subset of the ci_safe lanes a Zoe box runs with no network - how it was measured (every ci_safe file alone, network blocked, throwaway HOME), what needed the network, a model or an external binary, the selection rule and its coverage of services/zoe-data, the time and memory numbers, the tool (tools/ci/offline_tier.py) and how the self-heal lab calls it.
tags: [testing, offline, hermetic, ci_safe, self-heal, pytest]
timestamp: 2026-11-14T12:00:00Z
---

# Offline test tier

Owner constraint: **local tests must work offline.** A sold unit may have no GitHub, and its self-evolution loop
gates on tests it runs on the box. The offline tier is the declared set for that: hermetic, no network, fast.

| piece | what it is |
|---|---|
| `tools/ci/offline_tier.json` | the tier: a GENERATED, committed manifest of test files, with per-file seconds, peak RSS and the zoe-data modules each file is the designated cover for |
| `tools/ci/offline_guard.py` | pytest plugin: refuses and records any non-loopback socket call; records started programs, per-file time, peak RSS, executed zoe-data modules |
| `tools/ci/offline_tier.py` | prints the selection, verifies it offline, measures every `ci_safe` file, regenerates the manifest |
| `tests/unit/test_offline_tier.py`, `test_offline_guard.py` | the pins (synthetic trees; negative controls) |

## 1. The measurement (2026-11-14)

Method: `offline_tier.py measure` ran each of the 1,146 `ci_safe` test files of the two marker lanes
(`services/zoe-data/tests` 861, `tests/unit` 285) **alone, in its own pytest process**, with the lane's `validate.yml`
environment minus `CI` and `POSTGRES_URL`, and:

- inside an **empty network namespace** (loopback only), so a child process cannot reach the network either;
- with the guard in `block` mode: a `connect` / `sendto` / `sendmsg` to a non-loopback address or a name lookup
  fails like it does on a box with no network, and is recorded against the test that made it;
- with a **throwaway `HOME`**, listed afterwards: anything written there is live per-user state on a real box;
- every program started (`subprocess`, `os.exec*`, `posix_spawn`, `os.system`) recorded by name.

Host: x86_64 cloud container, 4 CPUs, Python 3.12.3, the slim dependency list of `.github/actions/ci-deps`, four
files at a time. **Not measured on the Jetson** (see Limits).

| | |
|---|---|
| tests | 31,991 passed, 107 skipped, 9 failed, 2 errors |
| per file, own process (interpreter + conftest + tests) | median 1.1 s, p95 6.3 s, max 108 s (`test_landing_throughput.py`); sum 2,517 s |
| peak RSS per file | median 62 MB, p95 127 MB, max 353 MB (`test_omi_panel_anchor.py`); largest child process 196 MB |
| hermetic and green offline | 1,055 files (31,172 tests) |

### What needed the network

No `ci_safe` test attempted a model download or a DNS lookup of a real host. What did reach out:

| finding | files | status |
|---|---|---|
| `git commit` in a temp repo ran the caller's global git config; here that names a commit-signing program that needs its server. Offline: 242 failures and errors in the `tests/unit` files alone | 21 (18 `tests/unit`, 3 zoe-data) | fixed, PR 2531 (both conftests pin a neutral git config) |
| importing `main` "connects" a UDP socket to a public address to learn the LAN interface (`_self_lan_origins()`, at import). Nothing is sent, but the allow-list under test then depends on the machine | 17 (zoe-data) | fixed, PR 2532 (conftest pins `ZOE_HOST_LAN_IP`) |
| `test_voice_daemon_platform.py`: two tests serve on this machine's LAN address and need a routed interface; they skip offline by design | 1 | not in the tier; nothing to fix |

### What wrote outside its sandbox

24 files wrote under `HOME`: 22 wrote `~/.zoe/forget_session_epochs.json` (`forget_session` has no env knob; a real
member id was among the test users), one `~/.zoe/night-extract/`, one `~/.cache/zoe/voice_probe_selftest_trend.jsonl`.
The first also made results depend on history: on a home that had run the suite, 11 tests in the three flue
wire / backend / abort files failed. Fixed in PR 2536 (tests only). Still open: `forget_session.py` deserves an
env knob (product code), so a harness in a child process is covered too.

### What needs an external binary

| program | files | in the tier? |
|---|---|---|
| `git`, `bash`/`sh`, python, POSIX tools (`flock`, `pgrep`, `sleep`, `sed`, `awk`, `tail`, `which`, `date`, `uname`, `echo`, `true`), `ldconfig`, `lscpu`; repo scripts; a test's own temp-dir fakes | 50 use git, 48 bash, 36 python | allowed (the base set, `BASE_BINARIES`) |
| `node` (the JS harness tests; one also `npm`) | 52 | no |
| `unshare` | 2 (`test_brain_cold_start.py`, `test_panel_away_p1.py`) | no (kernel-dependent) |
| `gh`, `openssl`, `jq`, `visudo` | 1 each (`test_pipeline_store.py`, `test_relay_nginx_listener.py`, `test_required_gate_workflows.py`, `test_p0_deroot_files.py`) | no |

### Not offline findings, recorded so nobody re-derives them

- 24 files carry the text `ci_safe` but select no test (deselected or skipped at module level) and 2 more skip
  every test: under the slim list their packages or model files are absent. They neither ran nor reached out.
- `test_restore_drill.py` (2 tests) and `test_rotate_service_logs.py` (1) fail in this container online too; the
  container runs as root and at least the second relies on a permission failure. Unverified elsewhere.
- `test_router_off_loop.py::test_fast_tiers_resolve_routes_off_the_loop` fails here alone and online (0.89 s
  against a 0.2 s bound); `test_intent_reminder_latency.py` passes alone and fails beside three other processes
  (152 ms against 100 ms). Wall-clock bounds; both are kept out of the tier.

## 2. The tier

Rule (`offline_tier.py select`): a file is a **candidate** when, alone and offline, it exits 0 with at least one
test passed, made no non-loopback socket call, wrote nothing under `HOME` and started only base-set programs.
From the candidates, a greedy weighted set cover picks the files with the most not-yet-covered zoe-data modules
per second, then drops any pick the others made redundant. A module is **covered** when a function or class body
in it EXECUTED during the file's run (`sys.monitoring`); importing a module does not count, except for the
modules that have no function or class at all.

| | |
|---|---|
| files | 117 of 1,146 (114 zoe-data lane, 3 `tests/unit`) |
| tests | 2,740 passed, 10 skipped |
| `verify`, two lane processes, empty netns | 101 s (97.5 + 3.8), peak RSS 271 MB |
| plain pytest, both lanes in ONE process | 70 s; also under a 2,048 MB address-space cap |
| stated budget | **600 s, 1,024 MB peak RSS** (about 6x the x86 time; the ARM time is unmeasured) |
| coverage | 489 of 499 `services/zoe-data` modules (tests and alembic excluded) |

The coverage map is the manifest itself: each file's `covers` lists the modules it is the designated cover for,
and `uncovered` gives the reason for each of the 10 modules left: `palace_embed.py` (its only test skips under
the slim list) and nine that no `ci_safe` test executes at all (`database.py`, `intent_classifier_llm.py`,
`ui_orchestrator.py`, `voice_turn.py`, `routers/capability_matrix.py`, `routers/change_notices.py`,
`routers/voice_settings.py`, `proactive/adapters/__init__.py`, `proactive/triggers/people_health.py`).
Covered means "executed at least once", not "well tested": the tier is a smoke floor, the full `ci_safe` lanes
stay the real check wherever they can run.

## 3. Using it

```bash
python3 tools/ci/offline_tier.py                  # print the selection, then verify it
python3 tools/ci/offline_tier.py list
python3 tools/ci/offline_tier.py verify --json -  # one JSON result on stdout; human lines on stderr
python3 tools/ci/offline_tier.py verify --enforce-budget
```

`verify` exits 1 when a test fails, a test reaches for a non-loopback address (named), a program outside the base
set starts, something is written under `HOME`, a selected file is missing or runs nothing, or (with
`--enforce-budget`) the run is over the manifest's time or memory budget. It reports wall time and peak RSS per
lane. It makes no network, GitHub or `gh` call. Where the kernel refuses a user namespace (inside another
sandbox) it says `netns=off` and relies on the in-process guard.

In the required `ci_safe` lanes both lane conftests register the guard, and it arms itself for exactly the
manifest's files: a tier test that starts reaching for the network fails there, by name. That is what keeps the
tier hermetic between regenerations.

Checked 2026-11-14 with the guard armed this way and the three hermeticity fixes in: the full zoe-data `ci_safe`
lane ran 24,982 passed, 0 failed (23 min, one process); the full `tests/unit` lane ran 7,025 passed plus the three
container-only failures listed above. Neither lane wrote anything under its throwaway `HOME`.

Regenerate by hand, never per PR (about 15 minutes with `--jobs 4`; on the box, under
`flock /tmp/zoe-test-lane.lock`, outside the night window):

```bash
python3 tools/ci/offline_tier.py measure --jobs 4      # writes ~/.cache/zoe/offline-tier/measurement.json
python3 tools/ci/offline_tier.py select --budget-s 600 --budget-rss-mb 1024
python3 tools/ci/offline_tier.py verify
```

A stale manifest only makes the tier thinner or slower; a deleted tier file fails `verify` and
`test_committed_manifest_is_sound`.

## 4. How the self-heal lab calls it

`labs/self-heal` accepts only `<running python> -m pytest <relative args>` for `detect_cmd` and
`offline_lane_cmd`. The tier has that form (checked against `selfheal.config.check_pytest_argv`):

```json
"offline_lane_cmd": ["<python>", "-m", "pytest", "-p", "no:cacheprovider", "-q",
                     "-p", "tools.ci.offline_guard", "--offline-tier",
                     "services/zoe-data/tests", "tests/unit"]
```

`--offline-tier` collects only the manifest's files and guards every one of them; both lanes run in one process
(measured: 2,740 passed in 70 s, inside an empty network namespace, with a throwaway `HOME` and
`prlimit --as` 2,048 MB, the lab's sandbox ceiling). The same argv is a fast `detect_cmd`. The exit code is
pytest's. Run from the worktree root (`python -m pytest` puts it on `sys.path`; no `PYTHONPATH` needed).

Owner decisions, not made here: add `tools/ci/offline_guard.py`, `tools/ci/offline_tier.py` and
`tools/ci/offline_tier.json` to the lab's forbidden list (an engine must not edit the gate it is judged by), and
whether the unattended `full` + `local` landing should run the tier or the whole `ci_safe` lanes when time allows.

## Limits

- **ARM time is unmeasured.** Every number above is from an x86_64 container. Run `verify` on the box, then set
  the budget from that run and use `--enforce-budget`.
- The guard is per interpreter: a CHILD process's socket call is refused only by the network namespace, and is
  not attributed to a test.
- The zoe-auth lane (its own interpreter and manifest) and the node harness tests are outside the tier.
- A UDP `connect` sends nothing; it is still refused, because the result depends on the host's routes.

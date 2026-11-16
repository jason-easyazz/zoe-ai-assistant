---
type: Reference
title: Test-suite health pass (2026-11-14)
description: Baseline of the three ci_safe lanes, then a hunt for order dependence, home/state leaks, timezone and clock dependence, and slow or hanging tests; every finding with its evidence, what was fixed in which PR, and what was left.
tags: [tests, ci, isolation, order-dependence, time-bombs, home-write-guard]
timestamp: 2026-11-14T00:00:00Z
---

# Test-suite health pass (2026-11-14)

Trigger: a test was found writing a real state file under the home directory, and another changed result
depending on run order (PR 2496 fixes that one file). This pass asked how much more of that there is.

**Short answer.** In file order the suite is green. In any other order the zoe-data lane fails 70 to 423
tests, almost all from ONE cause (a bench arm that leaves a secret in `os.environ`). Nine tests were due to
go red on 2026-11-22. Five more places wrote under the home directory or into the checkout. One test cost
357 s. Timezone is clean. zoe-auth is clean on every axis.

## How it was measured

- A container, not the box: Linux x86_64, 4 cores, UTC, **running as root**. Nothing here ran on the Jetson.
- Environments built as `validate.yml` does: Python 3.10 with the slim list from `.github/actions/ci-deps`
  for the two marker lanes, Python 3.11 with `services/zoe-auth/requirements.txt` for zoe-auth. A throwaway
  local Postgres stood in for the `tests/unit` job's service container. Python 3.12 was not run.
- Commit `95b111d` (main on the morning of 2026-11-14) for the baseline and the hunt.
- Scratch tooling only, none of it in the repo: `pytest-randomly` (order), `time-machine` (clock), and four
  small `-p` plugins (a write recorder, a per-test state differ, an order replayer, a clock shifter).
- After the first batch of runs, PR 2496's patch was applied to the analysis tree so its known leak stopped
  showing up as new findings. Where a number comes from a tree with that patch, it says so.

## Baseline

| lane | command shape | passed | failed | skipped | time |
|---|---|---|---|---|---|
| zoe-data `ci_safe` | 4 shards, as CI | 24,978 | 0 | 84 | 609 + 250 + 199 + 209 s |
| `tests/unit` `ci_safe` | 2 shards, as CI | 6,855 | 3 | 37 | 464 + 298 s |
| zoe-auth | full directory | 156 | 0 | 0 | 21 s |

The 3 `tests/unit` failures are the root-only tests (finding E1): they pass on a GitHub runner, which is
not root. The first `tests/unit` run also failed three `test_shard_ledger.py` tests, because the nested
pytest they start picked up `pytest-randomly` from my environment; with that plugin moved out of the
environment they pass. That is my artefact, not a finding.

## Findings

### Order dependence

Method: each lane in random order with five seeds (101, 202, 303, 404, 505), then replaying a failing
order and bisecting to the polluting test.

| lane | in file order | random order, per seed |
|---|---|---|
| zoe-data | 0 failed | 70, 173 (+7 errors), 254, 423 (+7 errors); seed 101 **hung** at 98% |
| `tests/unit` | 3 failed (root-only) | 1 more failure in every seed, always in `test_zmb_night_mind.py` |
| zoe-auth | 0 failed | 0 failed in all five |

482 distinct zoe-data tests failed in at least one of the four completed random runs.

- **O1. A bench arm leaves the forget-ledger secret in the environment. FINDING (bench code).**
  `ZMAArm.__init__` in `scripts/perf/zmb/arms/zma.py` does
  `os.environ.setdefault("ZOE_FORGET_LEDGER_SALT", ...)` and nothing removes it; `close()` does not, because
  the arm did not record setting it. Every test that runs after the first `test_zmb_zma.py` test then sees
  the ledger as configured with no database behind it. Direct evidence: `test_transcript_check.py`,
  `test_named_guests.py` and `test_omi_person_facts.py` are 136 passed on their own, and **81 failed + 7
  errors** when run right after `test_zmb_zma.py`. In file order that file runs almost last, which is why
  the lane is green.
  Closing the arm in the test does not help: `close()` never removes the secret, and with that change alone the
  seeded runs were unchanged (410 failed on seed 505, against 411 without it). I tried that first.
- **O2. Four other tests leave a secret in the environment. Not fixed:** `ZOE_JUKEBOX_SECRET` and
  `ZOE_MUSIC_SETUP_SECRET` (`test_arch_a2_auth_gaps.py`), `HERMES_API_KEY` (`test_runtime_env.py`),
  `ZOE_HOME_SETUP_SECRET` (`test_setup_token_not_in_query.py`). No failure was traced to them.
  A conftest fixture that restores the whole environment after every test would close this class. I built
  one and measured it only in random order (seeds 505 / 404 / 303: 13 / 3 / 4 failed, the same residue as the
  file-local fix). I did not get a file-order run of it, so it is not in the PR.
- **O3. `tests/unit/test_zmb_night_mind.py`:** the `z0n` arm was a module-scoped fixture. An open arm owns
  the process-wide in-process stores and every other test in the file opens and closes arms of its own.
  Six seeds: 1 failed / 35 passed on main every time; 36 passed every time with the fixture per test.
- **O4. `test_memory_retire.py`:** `lab_driver.load_service()` returns the same service object to every
  test and its forget tombstones (a 300 s shield) live on it. The one test that really forgets "cello"
  left the shield up. Six seeds of the file alone: main failed 3 of 6 (11, 22 and 23 failures); with the
  tombstones cleared in the fixture, 6 of 6 pass.
- **O5. `test_personalisation_hop.py`:** `restraint` keeps the speaker verdict of a member's last turn,
  process-wide. `test_a_sensitive_fact_is_not_put_in_front_of_the_brain_for_an_unconfirmed_voice` asks for
  facts without noting a turn of its own, so after `test_preamble_placement_is_the_block_before_the_words`
  it reads that test's mark and is handed the fact it asserts is withheld. It failed with all four random
  seeds on main; with `restraint._reset_state()` before every test in that file it passes with all four. I read this as
  a test reading stale state, not a product leak, because a real turn always notes itself first. That
  reading is from the code, not from a product test.
- **O6. The seed-101 hang:** `test_memory_curation.py::test_an_opt_out_cannot_interleave_with_a_curators_in_flight_add`
  waits on an event with no bound. In that order the add returned before its ingest and the lane never
  ended. `faulthandler` printed the stack after 300 s; nothing kills the test.
- **O7. PR 2496's class, seen across runs:** a later in-order run failed 13 Flue tests with
  `…/agents/zoe/s1~f1794655274132` in the URL. The epoch came from `~/.zoe/forget_session_epochs.json`
  left by an *earlier* run. One of my shifted-clock runs had written an epoch dated December 2027 into it.
- **O8. Not fixed.** After the fixes below, four things still fail in some orders: the Flue URL tests
  (up to 12 per seed; that is O7, and they did not fail in runs with PR 2496 applied),
  `test_reply_ledger.py` (`ValueError: The future belongs to a different loop`; passes alone with every
  seed, so the polluter is another file), `test_async_subprocess.py` (`registry leaked a finished
  child`; fails with 2 of 4 seeds on its own) and one test of `test_forget_alias_sweep.py` in one seed.
- **O9. Inventory, not fixed.** With a per-test differ on the in-order zoe-data lane, 2,499 of 24,981 tests
  left process state behind in 91 service modules. Most is harmless (counters, caches). The ones that
  look able to bite: `turn_outcomes._backend`, `zoe_core_client._BRAIN_SEM`,
  `memory_service._service_singleton`, `proactive.engine._engine_loop` and four module-level asyncio locks
  are left bound by a test; `test_voice_invariants.py` reloads `zoe_agent` 253 times. 52 service modules
  already have a reset function for tests. Calling every one of them before each test changed nothing
  (410 vs 411 failures on seed 505), so the state they reset is not what makes the lane order-dependent.

### Home and state leaks

Method: `HOME` pointed at an empty directory with a recorder on every in-process write; then the guard
itself in a simulated runner layout (checkout under `HOME`).

| # | what | written by | status |
|---|---|---|---|
| H1 | `~/.zoe/forget_session_epochs.json` | 112 tests in 20 zoe-data files | PR 2496 |
| H2 | `services/zoe-data/data/music_prefs.json`, in the checkout | `test_ma_ondemand.py`, `test_music_bridge.py` | fixed |
| H3 | `~/.zoe-ytmusic/viewer-token` **removed** | 20 tests in `test_ytmusic_signin.py` | fixed |
| H4 | `~/.zoe/night-extract/.lock`, `.erased.json` | 31 `tests/unit` tests in 3 files | fixed |
| H5 | `~/.zoe-panel-agent/`, created at import | `test_panel_agent_access.py` | fixed |
| H6 | `~/.cache/zoe/voice_probe_selftest_trend.jsonl`, 5 rows per run | `test_voice_probe_clip_checks.py` | fixed |
| H7 | `tests/unit/stubguard_*/`, in the checkout | `test_sys_modules_stub_guard.py`, on purpose | allowlisted |

H2 matters most on the box: `music_service._PREFS_PATH` sits beside the module with no env knob, so in the
live checkout that file is the household's default speaker. H3 is the destructive one. The `tests/unit`
lane also reached `~/.zoe/recall-gate-trace` through `delete_user` without writing in a clean home; it is
pinned too. zoe-auth wrote nothing.

### Timezone

Each lane in file order under `TZ=Australia/Perth` (the baseline is UTC). `tests/unit`: only the three
root-only failures. zoe-auth: 156 passed. zoe-data: 13 failures, all of them O7 (the stale epoch file),
none caused by the timezone; the 32 tests of the worst file pass alone under both zones. **No timezone
finding.** A clean Perth run of the whole zoe-data lane on a tree with PR 2496 was started and not
finished.

### Time bombs

Method: each lane with the process clock shifted +26 h, +8 days and +400 days.

- **T1. Nine tests go red on 2026-11-22 09:00 UTC.** `test_named_guest_learning.py` and
  `test_named_guest_clips.py` hear their voices at a fixed `T0 = 2026-11-08` passed as `now=`, the voices
  expire 14 days later, and the calls that take no `now=` read the wall clock. 26 passed at +1 to +7 days;
  9 failed at +8 days and at +400 days. `tests/unit/test_relative_date_timebombs.py` cannot see this shape.
- **Not time bombs.** Nine other tests failed under a shifted clock and all nine compare a real file
  mtime, or SQLite's own `datetime('now')`, with the shifted Python clock: `test_forget_to_the_box`
  (`test_w2…ledger_alone`), `test_ma_ondemand` ×2, `test_omi_repass_multi` (sweep), `test_recall_gate_trace`
  (rotation), `test_worktree_bootstrap` (min age), `tests/unit/test_curate_voice_corpus`,
  `test_ops_hygiene_b9`, `test_purge_predicates`. They are artefacts of shifting one clock and not the other.
- zoe-auth: 156 passed at all three shifts.

### Slow and hanging

- **S1. 357 s in one test.** `test_db_pool_adapt_sql_cache.py::test_a_dropped_rule_cannot_be_impersonated_by_a_new_one_at_the_same_address`
  ran 600 `gc.collect()` calls on the heap the whole lane had built. It is why shard 1 took 609 s and the
  others 199 to 250 s. It also could not fail for its stated reason (`monkeypatch` kept every rule alive);
  on a memo keyed by `id(rule)` it passed.
- **S2. Two tests pay production waits. FINDING, not fixed** (the wait is in `scripts/landing`, which this
  pass was told not to edit): `test_landing_throughput.py::test_taking_the_hold_fails_loudly…` 60.07 s
  (`flock -w 60` in `landing_lib.sh::_hold_locked`) and `test_autoland_starts_no_chain_pass…` 30.55 s.
- **S3. Waits with no bound.** 209 of 378 `subprocess.run` / `check_*` calls in the three lanes have no
  `timeout=` (72 files; `test_worktree_bootstrap.py` has 35). About 40 `await <event>.wait()` sites are
  bare. One of them hung (O6). CI's only backstop is the step timeout.

Slowest 20, zoe-data lane (baseline):

| time | phase | test |
|---|---|---|
| 356.93s | call | `test_db_pool_adapt_sql_cache.py::test_a_dropped_rule_cannot_be_impersonated_by_a_new_one_at_...` |
| 18.05s | call | `test_zmb_lab.py::test_a_full_runner_run_leaves_an_artifact_with_no_household_text_and_zero_g...` |
| 15.69s | call | `test_router_heads_numpy.py::test_backend_flag_is_in_the_inventory` |
| 13.88s | call | `test_no_hermes_exec.py::test_no_python_module_can_launch_hermes_or_set_yolo` |
| 9.38s | call | `test_forget_reach_inventory.py::test_every_file_sink_in_the_service_is_covered_by_a_marker` |
| 9.17s | setup | `test_stt_snap.py::test_corpus_snap_makes_no_wrong_write` |
| 9.00s | call | `test_memory_loss_class.py::test_delete_ids_has_exactly_the_two_audited_callers` |
| 8.89s | call | `test_omi_recordings.py::test_transcribing_a_five_minute_recording_holds_no_whole_file_in_python` |
| 7.67s | call | `test_conversation_turn_quality.py::test_the_same_panel_never_hears_the_same_filler_twice_run...` |
| 7.42s | call | `test_zmb_lab.py::test_a_broken_control_switch_is_caught_and_the_run_is_refused` |
| 7.35s | call | `test_forget_reach_inventory.py::test_every_chroma_collection_open_is_an_inventory_entry` |
| 6.84s | call | `test_memory_loss_class.py::test_no_module_removes_palace_rows_outside_the_audited_paths` |
| 6.69s | call | `test_zmb_lab.py::test_the_new_axes_refuse_when_a_control_switch_is_wired_to_nothing` |
| 6.63s | call | `test_memory_opt_out_writer_coverage.py::test_every_writer_source_in_the_codebase_is_classified` |
| 6.41s | call | `test_log_noise_2026_10_04.py::test_no_logger_call_in_the_service_slices_a_credential` |
| 5.93s | call | `test_zmb_lab.py::test_the_same_cells_give_the_same_verdicts_on_a_held_out_seed` |
| 5.56s | call | `test_speaker_resemblyzer_retired.py::test_no_zoe_data_runtime_module_imports_the_retired_stack` |
| 5.21s | setup | `test_zmb_lab.py::test_z0_measures_as_documented` |
| 5.00s | call | `test_memory_index_compaction.py::test_leased_drawers_holds_the_lease_only_for_each_call` |
| 3.66s | call | `test_forget_reach_inventory.py::test_reached_stores_name_a_real_eraser_that_the_forget_path_...` |

Slowest 20, `tests/unit` lane (baseline):

| time | phase | test |
|---|---|---|
| 60.07s | call | `test_landing_throughput.py::test_taking_the_hold_fails_loudly_when_its_lock_is_busy_or_the_p...` |
| 30.55s | call | `test_landing_throughput.py::test_autoland_starts_no_chain_pass_while_a_live_voice_hold_exists` |
| 27.77s | call | `test_flag_inventory.py::test_the_generator_runs_cleanly_on_this_tree` |
| 24.07s | call | `test_roadmap_flag_claims.py::test_the_generator_writes_exactly_the_derivation` |
| 21.02s | call | `test_speaker_lab.py::test_every_experiment_runs_end_to_end_on_a_synthetic_scratch_dir` |
| 20.49s | call | `test_affected_tests.py::test_real_repo_selection_is_sane` |
| 19.79s | setup | `test_roadmap_flag_claims.py::test_roadmap_flag_claims_match_code_defaults` |
| 19.64s | call | `test_memory_writer_map.py::test_the_generator_runs_cleanly_and_writes_the_rendered_map` |
| 19.60s | call | `test_night_mind_slow_model.py::test_a_k_cell_on_a_slow_model_is_an_error_naming_the_budget_a...` |
| 14.74s | setup | `test_flag_registry.py::test_every_zoe_flag_read_is_registered` |
| 14.07s | call | `test_flag_registry.py::test_no_bool_declared_flag_is_read_as_a_number` |
| 8.34s | call | `test_relative_date_timebombs.py::test_no_test_asserts_a_relative_date_against_the_real_clock` |
| 6.31s | setup | `test_memory_writer_map.py::test_every_writer_module_has_a_role_note` |
| 5.79s | call | `test_night_mind_slow_model.py::test_cell_budget_stops_starting_cells_that_would_overrun_and_...` |
| 5.29s | call | `test_zmb_bakeoff.py::test_the_capability_cells_run_on_seed_one_only_and_each_arm_runs_the_on...` |
| 5.20s | call | `test_night_mind_slow_model.py::test_a_timed_out_night_logs_llm_timeout_with_the_budget_and_s...` |
| 5.03s | call | `test_night_mind_slow_model.py::test_pretty_is_the_only_way_to_get_a_multi_line_stdout` |
| 4.65s | call | `test_zmb_night_mind.py::test_the_cli_can_score_the_k_cells_against_a_server` |
| 4.59s | call | `test_landing_throughput.py::test_the_probe_side_waits_for_an_in_flight_turn_and_blocks_new_ones` |
| 4.53s | call | `test_night_mind_slow_model.py::test_cells_mode_aggregates_the_model_counters_and_names_the_r...` |

zoe-auth: the slowest test is 3.1 s and the whole lane 21 s.

### Environment

- **E1. Three tests cannot pass as root:** `test_restore_drill.py` ×2 (the HA export helper ignores
  `ZOE_HA_CONFIG_DIR` when root, by design) and `test_rotate_service_logs.py` ×1 (a `chmod`-based failure
  does not bind root).

## What was changed

| PR | class | what |
|---|---|---|
| [#2538](https://github.com/jason-easyazz/zoe-ai-assistant/pull/2538) | time bombs | T1: the two fixtures pin the learning module's clock |
| [#2539](https://github.com/jason-easyazz/zoe-ai-assistant/pull/2539) | slow and hanging | S1 rewritten (357 s to under 0.2 s, and it now fails on the bug); O6 bounded to 10 s |
| [#2550](https://github.com/jason-easyazz/zoe-ai-assistant/pull/2550) | home and state leaks | H2 to H6 pinned; the guard below |
| [#2562](https://github.com/jason-easyazz/zoe-ai-assistant/pull/2562) | order and environment | O1 (the secret is put back after every test in `test_zmb_zma.py`), O3, O4, O5, E1; each fix lives in its own test file |

All four are drafts. None changes product code.

### The guard

`tools/ci/home_write_guard.py`: an audit hook that records every in-process write under the real home
directory or the checkout, and an autouse fixture in each lane's conftest that fails the `ci_safe` test
that made it. Contract and allowlist rules are in `tests/AGENTS.md`. Two things worth knowing:

- Its first version exempted every path under the system temp dir, so with a checkout in `/tmp` it guarded
  nothing and my first green run proved nothing. Removing a pin and watching the guard *not* fire is what
  caught it. H3 was found only after that fix.
- It carries three allowlist entries for H1, marked pending PR 2496.

## Before and after

| measure | before | after |
|---|---|---|
| zoe-data, file order | 24,978 passed, 0 failed | 25,037 passed, 0 failed (CI's 4-shard cut; the branch sits on a newer main) |
| zoe-data, seed 505 | 423 failed + 7 errors | 13 failed |
| zoe-data, seed 404 | 254 failed | 4 failed |
| zoe-data, seed 303 | 173 failed + 7 errors | 4 failed |
| `tests/unit`, random order | 1 order failure in each of 5 seeds | 0 in 6 seeds of the one affected file; the lane was not re-run in random order |
| tests due to fail on 2026-11-22 | 9 | 0 |
| slowest test in the gate | 357 s | 60 s (S2) |
| paths written under home or the checkout by a `ci_safe` run | 6 (H1 to H6) | 1 (H1, PR 2496's) |

The "after" rows are per PR, each measured on its own branch. The four branches were never run together.

## Not done

- The order PR's random-order numbers are for three seeds, not five. Its `tests/unit` changes were checked
  file by file, not with a full lane.
- O2, O8 and O9 are open.
- **An error in my own method.** For the later experimental runs, `pytest-randomly` was on `PYTHONPATH` and
  loaded itself, so runs I had labelled "file order" were random order with an unrecorded seed. I caught it
  by comparing the order of tests in the junit files. No number in this record that is called file order
  comes from those runs; the file-order result for the order PR is from a re-run with the plugin off the path.
- S2 and S3 are open.
- Nothing was run on the Jetson, on Python 3.12, or under `CI=true`.
- The unmarked (device-only) tests were not examined at all. The guard records their writes and does not
  enforce them.

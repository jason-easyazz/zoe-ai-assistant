# Lane 2-K: test-suite truthfulness. Does the suite prove what it says it proves?

Second-round deep dive, 2026-10-19. Static analysis only. No test was run. Code reviewed: `origin/main` @ `4b4ceac76`
(15 commits past the review worktree's `24d00c14`, including #2097, #2099, #2108, #2117, #2119 and #2120). I took a
`git archive` snapshot into the scratchpad and ran AST scans over it. Live reads were all read-only: the
nondeterministic-failure log, GitHub run history (`gh run list` / `gh run view --log[-failed]`), branch protection
(`gh api`), the instruments' own result and baseline JSONs under `~/.cache/zoe/` (counts and verdicts only, no
content), corpus file counts, and the installed `llama-server` unit plus its drop-ins.

Labels: **[verified]** means I read it in code, logs or artifacts. **[inferred]** means it is reasoned from them and
not demonstrated. Paths are relative to the repo root unless they start with `~`.

---

## 0. Headline findings

1. **The nightly "catch-all safety net" has caught nothing for at least 14 nights, and it reported green every time.**
   [verified]
   - Every pytest step in `.github/workflows/self-hosted-tests.yml` is `continue-on-error: true` (lines 129, 146, 179,
     190, 204), so the run concludes `success` whatever its suites do. The last 14 nightly runs were all `success`.
   - Their logs say otherwise:
     - `tests/integration tests/e2e`: *Interrupted: 5 errors during collection*, so zero tests ran.
     - The full zoe-data suite: 16 to 19 failed.
     - The full `tests/unit` catch-all: *Interrupted: 3 errors during collection*, so zero tests ran.
   - The 3 `tests/unit` collection errors come from three workflow test files that `raise RuntimeError` at import
     when `node` is missing. The 18 zoe-data failures are all Node-harness tests failing closed: *"Node.js is not
     installed on this CI host … A skip here would hide a real regression."* Among them are both XSS guards and the
     auth-no-demo-bypass harness.
   - So the fail-closed design of those tests is defeated by the workflow's fail-open design.
   - Consequence: the **10 unmarked `tests/unit` files (143 tests) run nowhere at all**.
2. **`-x` in the required gate turns one failure into thousands of unknowns.** [verified] On run 39131392311
   (`fix/voice-batch-a1-a11`), one source-text test failed and the zoe-data lane stopped at *"1 failed, 211 passed …
   480 deselected"*. That is about 211 of about 18,500 cases. Because the step is one bash block, the `tests/unit`
   and zoe-auth invocations behind it never ran either. `validate.yml:210-236`.
3. **The voice replay gate checks "did not refuse", not "said versus did".** [verified]
   - A clip scores OK unless the reply is empty, contains a fallback or can't-do phrase, or the run errored
     (`services/zoe-data/tests/replay_samples.py:269-288`).
   - `compare()` gates only on the aggregate OK rate and the CANT_DO/ERROR count, plus per-stage medians
     (`scripts/maintenance/voice_regression_probe.py:713-766`).
   - Per-clip routes are recorded but are consulted only for clips that already fail (flake tolerance, `:292-330`).
   - So "turn on the kitchen light" re-routed to music with a confident reply is a PASS.
   - The gate replays 20 pinned clips out of 1,485.
   - It calls internal functions (`ft.resolve`, `brain_oneshot`) as a hard-coded member. It never touches the HTTP
     route, server-decided identity, panel targeting, TTS, LiveKit or the Pi daemon.
4. **The gate's scope list misses most of the code it replays.** [verified]
   - `intent_router.py` (6,202 LOC, imported by the replay itself), `stt_snap.py`, `memory_service.py`, `auth.py`
     and `panel_principal.py` are not in `VOICE_PATH_PATTERNS` (`scripts/maintenance/voice_gate_check.py:75`).
   - By static import closure, 202 of the 214 zoe-data modules reachable from the replayed path are out of scope.
     This over-approximates, because lazy imports are counted.
   - A change to them deploys without a fresh replay artifact.
5. **The Samantha bar protects 8 of its 23 scenarios.** [verified] The live baseline
   (`~/.cache/zoe/samantha_bar_baseline.json`, 2026-09-30) holds S1 to S8 only. `compare_baseline` can only redden a
   baseline PASS (`scripts/perf/samantha_bar.py:1248-1266`), so S10 to S31 are `new` and cannot regress. The last
   full run was 2026-10-09. 41 of the 67 trend rows were from dirty trees. The day-sim and the person bench have
   **no baseline file at all**.
6. **Most of the suite's tests exercise real code; very few touch the assembled app.** [verified] In a seeded sample
   of 1,039 test functions:
   - about 66% are pure-input unit tests;
   - about 28% mock collaborators;
   - about 6% are mock-only, doc/source or can't-fail tests.

   Against that, only **7 zoe-data test files drive the real `main.app`**. 74 build their own `FastAPI()`. 115
   files hand-roll a fake DB and 54 use SQLite. Only one Postgres-backed file runs in the required gate. Production
   is Postgres on Python 3.12.13; **the required gate runs Python 3.10** (`validate.yml:44`).

---

## 1. Shape [verified unless marked]

**Totals.**
- 959 test files.
- **14,721 `def test*` functions** by AST. That includes 1,524 parametrize decorators.
- 293,353 test LOC.
- CI collects about 18,518 zoe-data cases, 4,466 `tests/unit` cases, 147 zoe-auth cases and 83 in the HA bridge
  (run 39141723193).

| directory | files | test functions | LOC |
|---|---:|---:|---:|
| services/zoe-data/tests | 700 | 10,656 | 214,475 |
| tests/unit | 191 | 3,538 | 61,724 |
| tests/integration | 20 | 158 | 5,842 |
| services/zoe-auth/tests | 13 | 102 | 2,408 |
| labs/* | 8 | 163 | 2,157 |
| scripts/* (outside every testpath) | 8 | 15 | 2,738 |
| tools/* (outside every testpath) | 5 | 11 | 1,162 |
| services/homeassistant-mcp-bridge, zoe-core/test, e2e, production, voice, intent_system | 14 | 76 | 2,847 |

**Markers.**
- Module-level `pytestmark = ci_safe`: 846 files.
- `skipif` / `importorskip`: 30 / 62 files (40 / 157 sites).
- `integration`: 22 decorator uses plus 1 module.
- `skip` (unconditional, whole-module): 2 files, 43 tests: `tests/unit/test_auth_security.py:21` and
  `tests/unit/test_experts.py:24`.
- `xfail(strict)`: 2.
- There are no `slow` or `live` markers. "Live" is an env gate (`ZOE_LIVE_TESTS`, `samantha_live/`).
- **104 files (865 tests) carry no `ci_safe` and never run in the required gate**: 35 zoe-data files (336 tests),
  10 `tests/unit` files (143), all of `tests/integration`, 13 zoe-auth files run by path, and so on.

**File size.**
- p10 60, p25 110, median 216, p75 396, p90 650 and p99 1,476 lines; max 6,172.
- 27 files are at least 1,000 lines.
- Tests per file: median 10, p90 34.

**Ten largest test files.**

| LOC | tests | file | what it tests |
|---:|---:|---|---|
| 6,172 | 253 | services/zoe-data/tests/test_kanban_adapter.py | Multica board / executor adapter (dev-fleet) |
| 3,754 | 128 | services/zoe-data/tests/test_greploop_guard.py | PR review loop guard (dev-fleet; 399 patch sites) |
| 2,941 | 181 | tests/unit/test_zmb_bakeoff.py | memory-system bake-off harness (decision already taken) |
| 2,240 | 137 | tests/unit/test_night_window.py | 12B night window, FakeHost seam |
| 1,975 | 53 | services/zoe-data/tests/test_brain_lane_failover.py | brain lane failover |
| 1,903 | 64 | services/zoe-data/tests/test_pipeline_store.py | engineering pipeline store (dev-fleet) |
| 1,806 | 69 | services/zoe-data/tests/test_skybridge_service.py | skybridge service |
| 1,804 | 141 | tests/unit/test_samantha_bar.py | the Samantha bar harness itself |
| 1,532 | 96 | tests/unit/test_voice_gate_check.py | voice gate checker |
| 1,476 | 63 | tests/unit/test_cross_review_poll.py | cross-review poller (dev-fleet) |

Six of the ten test **dev tooling or harnesses**, not the household product. By file name, 67 dev-fleet files
(1,447 tests, 32k LOC), 16 bake-off files (626 tests) and 5 Samantha-harness files (296 tests) make up about 2,370
tests, roughly 16% of the suite.

**Tests per production module.** Columns: test files referencing the module, the test functions in those files, and
files patching it. Heuristic: import or string reference.

| module (LOC) | files | tests | patching |
|---|---:|---:|---:|
| routers/voice_tts.py (7,454) | 94 (+17 source-read only) | 2,098 | 84 by grep (`monkeypatch.setattr` in a file naming voice_tts) |
| intent_router.py (6,202) | 89 | 1,764 | 32 |
| zoe_agent.py (4,573) | 61 | 1,132 | 16 |
| skybridge_service.py (4,385) | 34 | 862 | 10 |
| memory_service.py (4,289) | 128 | 2,811 | 90 |
| memory_digest.py (3,501) | 66 | 1,335 | 27 |
| mcp_server.py (3,501) | 15 | 175 | 7 |
| routers/chat.py (3,438) | 54 (+13 source-read only) | 941 | 16 |
| main.py (3,366) | 60 | 1,486 | 11 |
| routers/system.py (3,091) | 24 | 393 | 3 |
| zoe_flue_client.py (2,648) | 59 | 1,691 | 32 |
| executors/kanban_adapter.py (2,474) | 4 | 306 | 0 |
| greploop_guard.py (2,390) | 4 | 159 | 1 |
| night_mind.py (2,009) | 15 | 344 | 0 |
| music_service.py (1,811) | 17 | 341 | 15 |
| routers/voice_livekit.py (1,753) | 14 | 258 | 4 |
| routers/memories.py (1,566) | 39 | 1,097 | 23 |
| provenance_answers.py / person_extractor.py / memory_authority.py | 8 / 42 / 29 | 282 / 830 / 703 | 1 / 16 / 1 |

`mcp_server.py` is 3,501 LOC with 175 tests in 15 files. Its correctness file (`test_mcp_server_correctness.py`, 32
tests, Postgres) is **unmarked**, so it runs nightly only.

---

## 2. Mock density

**Sample.** Seed `20261019`, `random.sample` of 60 files over the sorted list of 959. The files:

labs/b3-1-supersession/test_supersession_lab.py, labs/relay/tests/test_plugin.py,
services/zoe-auth/tests/{test_guest_user_profile,test_session_db_fallback}.py,
services/zoe-data/tests/samantha_live/test_live_core.py, and in services/zoe-data/tests/:
test_calendar_service, test_calendar_utils, test_canonical_invariants, test_chat_wave_f_harness,
test_cross_writer_supersede, test_distress_handoff, test_greptile_client, test_guest_memory_report,
test_intent_reminder_latency, test_list_show_direct_execution, test_mcporter_receipt_verified,
test_memory_compose_packet, test_memory_continuity_mode, test_memory_digest_idle_verdict, test_memory_lint,
test_memory_loop_observability, test_memory_supersede_named_person, test_migration_0031_auth_handoffs,
test_multica_admission, test_multica_client, test_music_bridge, test_music_generic_play, test_night_mind,
test_owner_retraction_lands, test_panel_auth_voice_identity, test_pending_suggestions_person_create,
test_pi_intent_shadow, test_pipeline_evidence, test_proactive_trigger_pg_interval, test_speaker_adaptation,
test_structural_claims, test_struggle_signals, test_stt_snap, test_touch_voice_session_harness,
test_ui_compose_render, test_user_filters, test_voice_invariants, test_voice_speaker_verdict,
test_web_search_provider, test_zoe_multica_memory_admission, test_zoe_observation_trace_collector.
Then tests/integration/{test_all_capabilities,test_code_execution_chat}.py, and in tests/unit/:
test_curate_voice_corpus, test_deploy_docs_only_skip, test_night_precheck, test_prune_worktrees_shared_branch,
test_reap_stale_serena, test_samantha_bar_conv, test_samantha_person, test_voice_probe_flaky_retry,
test_voice_probe_vad_stage, test_zmb_embed_shim. Finally tests/voice/comprehensive_conversation_test.py and
tools/test_user_memory_comprehensive.py.

**Method.** An AST classifier, hand-calibrated on 24 randomly drawn functions. The script is
`scratchpad/classify.py`.
- (e) no assert, raises or assert-helper; only trivial asserts; asserts swallowed by try/except; or code after a
  bare return.
- (d) the body reads a source, doc or config file.
- (c) every assert is on mock attributes or recorded calls, and the test patches.
- (b) patches with monkeypatch or patch.
- (a) everything else.

| class | count | share |
|---|---:|---:|
| (a) real code, real inputs | 682 | 65.6% |
| (b) real code, collaborators mocked | 293 | 28.2% |
| (c) asserts on mock calls only | 18 | 1.7% |
| (d) asserts on source/doc/config text | 27 | 2.6% |
| (e) cannot fail | 19 | 1.8% |
| total | 1,039 | |

Calibration notes.
- Only 73 of the 687 (a) candidates inject a fake through an argument or constructor. So (a) is mostly genuinely
  pure, for example the supersession lab's interval tests at `labs/b3-1-supersession/test_supersession_lab.py:90-141`.
- All 19 (e) cases sit in legacy script-style files. `tests/integration/test_all_capabilities.py` defines its
  `test_*` as methods of `class ZoeCapabilityTest` (not `Test*`), so **pytest never collects them**. The same goes
  for `tools/test_user_memory_comprehensive.py`, which is in no testpath.
- (c) examples:
  - `test_music_bridge.py:105 test_pause_calls_control`
  - `test_reap_stale_serena.py:149 test_reap_bails_on_pid_reuse`
  - `test_voice_speaker_verdict.py:196 test_no_verdict_is_the_exact_call_the_lane_always_made`
- (d) examples:
  - `test_canonical_invariants.py:160`, where `marker in main` is satisfied by a comment;
  - `test_proactive_trigger_pg_interval.py:37`;
  - `test_speaker_adaptation.py:688 test_router_is_registered_in_main`.

**Suite-wide counts** (AST plus grep):
- `monkeypatch.setattr/setitem/delattr`: 5,756 sites.
- `monkeypatch.setenv/delenv`: 2,969.
- `patch(`: 61.
- Resolvable patch targets: 5,421 sites in 571 of 959 files. Per file: median 2, p90 14, p99 54, max 399
  (`test_greploop_guard.py`).
- About 307 test functions read a production source file (`getsource` or `read_text` on a `.py`), across 160 files.
  154 test names contain source, wired, registered or similar.
- 338 assert lines assert on SQL text, in 70 files. 36 fake DBs dispatch on SQL substrings.

**The 15 most-patched production symbols.** Sites, then distinct files.

| sites | files | symbol |
|---:|---:|---|
| 121 | 74 | memory_service.get_memory_service |
| 56 | 1 | greploop_guard.STATE_ROOT |
| 52 | 7 | music_service._ma |
| 49 | 33 | db_pool.get_db_ctx |
| 47 | 21 | auth._ZOE_INTERNAL_TOKEN |
| 41 | 15 | database.get_db_ctx |
| 29 | 20 | routers.memories._svc |
| 29 | 5 | expert_dispatch.dispatch |
| 28 | 18 | db_compat.get_compat_db |
| 27 | 4 | music_service.get_players |
| 26 | 8 | intent_router._run_mcporter |
| 23 | 22 | memory_service._user_opted_out |
| 23 | 11 | semantic_router.is_enabled |
| 20 | 12 | routers.voice_tts._transcribe_audio |
| 19 | 15 | memory_service.is_guest_memory_user |

The last row is a truthfulness bug class in its own right. [verified]
- `is_guest_memory_user` is a pure, one-line function (`memory_service.py:1105-1108`), so there is no reason to
  mock it. Yet 20 call sites replace it with a **different** predicate: `uid == "guest"`, `not u`,
  `u in ("guest","")`, and so on.
- Production itself has at least 8 divergent guest predicates:
  - `memory_service.py:1108`: no `guest-` prefix, no `voice-daemon`.
  - `panel_principal.py:87`: has the prefix, no `anonymous`.
  - `user_filters.py:17,47`: everything.
  - `skybridge_service.py:2240`: `guest` and `voice-guest` only.
  - `provenance_answers.py:64`, `distress_handoff.py:42`, `zoe_flue_client.py:904,1449`.
- A `guest-<id>` principal is refused at the write wall (`user_filters.is_unidentified`, pinned by
  `test_guest_memory_no_write.py`). But it passes the **read** guard `is_guest_memory_user` used by `load_for_prompt`
  (`memory_service.py:1743`), `_memory_visible_to_user` (`:1094`), and the voice/chat recall paths.
- zoe-auth currently mints a plain `guest` (`services/zoe-auth/api/auth.py:222`), so this is latent. [inferred]
- No test pins one predicate across modules.

---

## 3. The voice replay gate as an instrument

**What it compares.** [verified]
- *Function:* the aggregate `ok_rate` over scoreable clips (EMPTY is excluded) must not drop, and the CANT_DO+ERROR
  count must not rise (`voice_regression_probe.py:732-746`).
- *Speed:* the medians of `stt_ms`, `brain_ms` and `e2e_ms` must stay under `ratio >= 1.5 and delta >= 100 ms` or
  `delta >= 1500 ms`, plus STT ms per audio-second (`:748-765`).
- VAD: a separate stage runs the real `voice_vad.SileroVAD` over the newest 24 clips, with a floor of 60% (`:135`).
- A recall gate fails closed when the replay ran without memory (`:701-711`). This was well done.

**What "OK" means.** [verified] `_classify` (`replay_samples.py:269-288`) returns OK unless:
- the transcript is empty;
- the outcome starts with `<`;
- the brain fallback text appears;
- `_CANT_DO_RE` matches (`:73-80`);
- the brain returned nothing.

There are **no expected labels per clip** (no intent, slots or action). "Said-vs-did" is really "did not refuse".
The per-clip `route_hash` is stored in the baseline profile but is read only inside `flaky_candidates` for clips
that already fail (`voice_regression_probe.py:292-330`). A wrong-intent action with a confident reply is invisible.

**What runs.**
- `replay_samples.py` reassembles the pipeline from internals: `_transcribe_audio_impl` → `sr.route` →
  `ir.detect_intent` → `ft.resolve(... channel="voice", allow_writes=False, extra_ctx={"panel_id": "replay"})` →
  `brain_oneshot(..., replay_isolation=True)` (`replay_samples.py:476-550`).
- The user is a hard-coded member id (`--user` default at `:622` and `voice_regression_probe.py:1352`).
- So it cannot see: the `/api/voice/*` HTTP route and its ordering, server-decided identity (#2097), speaker
  verdicts, panel targeting (#2117), TTS (the harness stops before TTS), the LiveKit lane, the Pi daemon, wake word
  and barge-in.
- A real-route test with the full stack is the only thing that could catch drift between `voice_tts`'s
  orchestration and the harness's copy of it. [inferred]

**Baseline provenance.** [verified]
- `~/.cache/zoe/voice_regression_baseline.json` was created 2026-10-15.
- Its keys are only `created_at` and `summary`. **No commit, tree or dirty flag records what code produced the
  baseline**; "known-good" is the operator's assertion.
- 20 pinned clip ids. 19 OK and 1 EMPTY, so `ok_rate` 1.0 and `fail` 0, and any single CANT_DO fails the run.
- Interpreter: py3.12.13, chromadb 1.5.9.
- Older baselines sit next to it as `.bad-20261012`, `.bak-20261012` and `.pre-rerecord-20260805`.

**Corpus.** [verified, counts only] 1,485 top-level WAVs and four quarantine folders (6 / 51 / 63 / 5). There are
no category labels. The gate replays **20** clips (1.3%).

**Run history.** [verified] The trend file has 739 rows. Since 2026-10-01: 288 runs, with 207 pass, 78 fail, 2
error and 1 skip. 39 of them came from dirty trees. Most failures are *"OK rate 0.950 vs 1.000 (fail 1 vs 0)"*, a
single clip. The flaky-retry tolerance (`:269-430`) exists because the same code scored OK, OK, CANT_DO.

**Artifact binding.** [verified] This part is sound.
- `--expect-revision` requires the artifact's commit to equal the PR head and refuses `dirty` or
  `clean_verified is False` (`voice_gate_check.py:566-610`).
- `--expect-tree-of` accepts a tree-identical squash (`:612-660`).
- The latest artifact (2026-10-18 15:48, `pass`) is `dirty: true`, so it is correctly unusable as evidence.

**Blind spots called out by the first dive.**
- requirements pins: `requirements*.txt` are now *in scope* (`voice_gate_check.py:90-96`). But the replay runs the
  already-installed venv, so a pin bump PR is "gated" by a replay that never installed the bump. [inferred]
- The LiveKit lane: `routers/voice_livekit.py` and `livekit_aiortc.py` are in scope, but the replay never exercises
  them.
- The Pi daemon: not exercised.

**Negative control.** **None end to end.** [verified by search]
- The scorer has unit tests on synthetic rows: `tests/unit/test_voice_regression_probe_scoring.py`,
  `test_voice_probe_*`.
- The VAD stage has a real historical control (the v6.2.1 model file).
- There is no known-bad commit or fault injection (for example, STT returns garbage, or the router misroutes
  everything) that the probe is required to fail before its pass counts. The bar-person bench has this; the voice
  gate does not.

---

## 4. Samantha bar, day-sim and person bench

**Samantha bar** (`scripts/perf/samantha_bar.py`, `docs/knowledge/samantha-bar.md`). [verified]
- *What a cell asserts:* deterministic needles and anti-needles first. The brain-as-judge (same Gemma, temp 0,
  top_k 1, seed 0) decides only the ambiguous rest. The rubric is pinned by sha, and the baseline sha equals the
  current one (`8b7b78…`).
- *Unexercised evidence:* #1751 had about 15 P1s of exactly this class:
  - S6 passing when B's packet read failed;
  - S8 passing with failed filler turns;
  - S2 passing when the old fact never landed;
  - capture "success" when the extractor raised;
  - backdate counting UPDATEs instead of re-reading;
  - compare with no baseline reporting `ok`.

  All were fixed, through `setup_ok` gates, `ERROR: setup not exercised`, re-read backdate and refusal without a
  baseline. That is good work, and the reason this instrument is now better than the voice gate on that axis.
- *What it cannot see:*
  - `compare_baseline` (`samantha_bar.py:1248-1266`) reddens only a baseline PASS. The **live baseline holds 8
    scenarios (S1 to S8, samples 3, clean, 2026-09-30)**, while the last full run scored 23. So 15 cells (S10 to
    S31) are `new` and **cannot regress**.
  - The last full run (2026-10-09) had S10, S21 and S24 FAIL and S12 and S16 SKIP.
  - 41 of the 67 trend rows are `dirty: true`; 32 are `partial`.
  - The judge is self-judging (documented in "Known limits").
- *Control arms:* only BM5 (S23 to S25) documents controls and a 22-line mutation run. Other cells have no control
  that shows the cell red without the feature.

**Day simulation** (`samantha_day_sim.py`). [verified]
- 16 asks with pre-committed PASS criteria (`docs/knowledge/samantha-bar.md:763-782`).
- **There is no baseline file.** The status is `ok` whenever it ran, including runs with a FAIL. The last run
  (2026-10-12) had 9 PASS, 6 SKIP and 1 FAIL.
- It is an observation, not a gate. 6 of 16 asks SKIP, and a SKIP is "no opinion".

**Person bench** (`samantha_person.py`, `docs/knowledge/samantha-person.md`). [verified] This is the best-built
instrument in the repo.
- Twelve cells, P1 to P12.
- Wilson-bound pre-registered bars. The pre-registration is sha-pinned; a bar may be loosened only in a PR.
- A **mandatory offline instrument proof**: more than 13 stub control arms (sycophant, gusher, parrot, shuffled,
  mute and others) must each turn their halves red, and a `gold` arm must pass. If a control stays green, the live
  run refuses to start and CI fails (`tests/unit/test_samantha_person_checkout.py`).
- "A gating half with NOT ONE exercised ask is the instrument, not a result."
- But there is **no baseline file**. The last full run was 2026-10-09; the last 4 of 7 runs were partial or error,
  and the latest is `dirty: true`.

**Pre-registered bars.** Only the person bench has them (Wilson `min` / `max` / `all` per half). The bar and the
day-sim use deterministic needles plus the judge, with no statistical bar and samples of 1 or 3.

---

## 5. Contract tests: what each pins, and how each could pass vacuously

| test | pins | vacuous pass |
|---|---|---|
| `services/zoe-data/tests/test_canonical_invariants.py` | the rocks in `docs/CANONICAL.md`; env defaults in code; TTS waterfall order; Moonshine loader marker; llama-server template | substring positions in source (`:121-131`), so a comment or dead branch naming `_synthesize_kokoro_sidecar` before `_synthesize_edge_tts` satisfies it; `marker in main` (`:160`) is satisfied by a comment; reads the **repo template**, not the served unit |
| `tests/unit/test_required_gate_workflows.py` | required contexts are produced by a job; voice-gate trust model (`pull_request_target`, base.sha checkouts, `checks:write` scope); break-glass | `REQUIRED_CONTEXTS = ("validate","secret-scan")` is a **hard-coded constant** (`:49`), never read from branch protection. It matches live today [verified via `gh api`], but the test cannot see a protection change, which is the same class as `REQUIRED_CHECKS = []`. Any job in any workflow named `validate` satisfies `:61-77`. A job-level `if:` that skips the job counts as success on GitHub, and this is not pinned. `secret-scan` skips its scan steps on fork PRs and goes green (`validate.yml` secret-scan job), which is documented but not pinned |
| `tests/unit/test_greptile_gate_workflow.py` | EXECUTES the embedded JS against stubs with node | strong. It raises at import without node in CI, which is right on GitHub but is what blinds the nightly `tests/unit` catch-all (§0.1) |
| `tests/unit/test_agent_mcp_memory_bounds.py` | Serena by URL, codebase-memory via the capped launcher, in repo-tracked configs | only **repo-tracked** configs are checked, not `~/.codex`, `~/.claude.json` or worktree copies, where the measured incidents could recur; `prop in body` (`:90-95`) is satisfied by a comment. It has real negative controls (`:169-218`) |
| `tests/unit/test_llama_server_unit_flags.py` | FA/q8 coupling, b11194 flags, MTP means one slot, ctx/cache sizes | pins `scripts/setup/systemd/llama-server.service` only. **The served `ExecStart` comes from an untracked drop-in** `~/.config/systemd/user/llama-server.service.d/80-swa-full.conf`. The installed base unit differs from the template (`--cache-ram 2048`, no `--swa-full`), and the drop-in currently matches the template [verified]. `unit_drift_check.py` exists but is a script, not a gate |
| `tests/unit/test_flag_inventory.py` | the extractor's behaviour on a fixture, plus generated-doc freshness | freshness is a change detector, red in one of 11 recent CI failures. The inventory says 695 flags, 645 undocumented, and asserts nothing about live values |
| `tools/audit/validate_structure.py`, `validate_critical_files.py` | root orphan files; critical files **exist** | existence only (`validate_critical_files.py:97-100`), so an empty `main.py` passes. The orphan check did catch a real stray (`nice`) 5 times on one branch |
| `test_mutating_routes_declare_auth.py` (new, A2) | every POST/PUT/PATCH/DELETE names an auth dependency | it has negative controls and a "the scan sees ≥200 routes" guard (`:93-96`), which is good. It matches by **name**, so any local function called `_member` qualifies, and `get_current_user` admits a guest (stated). GET routes are out of scope, yet the house-state and settings read leaks (#2099, A2) were GETs |

The same class as `REQUIRED_CHECKS = []` shows up in three places [verified]:
1. **`self-hosted-tests.yml`'s `continue-on-error` on every suite.** The run's conclusion is vacuously `success`.
2. **Samantha bar baseline coverage.** `regressions` iterates the baseline's keys, so a scenario missing from the
   baseline is vacuously "not regressed".
3. **Voice compare `ok_rate`.** An all-OK baseline plus "OK = no refusal phrase" means a wrong action is vacuously OK.

---

## 6. Flakiness and time bombs

**The nondeterministic-failure log** (`~/.zoe-logs/nondeterministic-test-failures.jsonl`). [verified]
- 36 records, all from `test_zoe_core_client.py::test_tool_action_dispatches`.
- Prompts: "Add bread to my shopping list." 34 times, "Add milk…" twice.
- 26 of the 36 are from a single day (2026-08-03). The rest span 2026-07-27 to 2026-09-27, and there have been none
  since.
- **All 36 show `dispatches: []`, `intents: []` and exactly one request (`/api/memories/for-prompt`).** None shows
  `memory_store`, although the docstring (`test_zoe_core_client.py:323-325`) says the failure "dispatches
  `memory_store`".
- The test is `ci_safe` at module level but `skipif`s without a live brain (`:62-80`). So it **only ever runs
  against the live household brain** (`--parallel 1`) on the box and always skips on GitHub. It asserts on a
  temp-0.7 sampled choice (`:36-42`): a model evaluation dressed as a unit test.

**Validate history.** [verified]
- The last 60 runs (2026-10-18 09:07 to 16:06): 38 success, 11 failure, 8 cancelled, 3 in progress, so a 78% pass
  rate of completed-not-cancelled. Median 14.4 min, p90 16.4.
- The last 200 runs: 84% pass of success+failure. Median successful run 15.6 min.
- **Zero reruns:** `attempt > 1` is 0, and no SHA has both a failure and a success.
- Of the 11 failures, none was a flake. I checked every log:
  - 7 were a stray root file `nice` (structure validator), across 3 branches;
  - 1 was a stale flag inventory;
  - 1 was the source-order test `test_ask_to_remember.py:427-430` (`src.index("_ask_to_remember_tier") <
    src.index("_tier0(") < src.index("_sr.route, text")`), which broke on A1's `to_thread` refactor;
  - 1 was a test reading the deleted `routers/openclaw.py`;
  - 1 was an Omi route assertion.
- The nondeterminism lives on the box, not in CI.

**Time and date dependence.** [verified counts]
- `datetime.now/utcnow/today` in 65 test files (174 sites); `time.time()` in 45 files (114 sites).
- Freezegun or time-machine: **1 file**. Most tests inject `now=` instead, which is good.
- One candidate time bomb: `test_omi_voices.py:420-429` seeds a voiceprint expiring `2026-10-31T00:00:00Z` and
  asserts it is unchanged, while sibling tests sweep with `datetime.now(UTC)` (`:597-672`). It is safe only while
  nothing on that path sweeps expired prints. [inferred, low]
- `tests/conftest.py:52` defines a session-scoped `event_loop` fixture, which pytest-asyncio deprecated, while
  `pytest.ini` sets the function loop scope. That is the likely source of the "Event loop is closed" noise in failing
  CI logs. [inferred]

**Other sources of nondeterminism.**
- Sleep-based waits: `time.sleep` in 26 files (56 sites); `asyncio.sleep(>0)` in 57 files (133 sites).
  `test_router_off_loop.py` uses real 400 ms and 10 ms timings with a margin of 0.2 s on shared CI. It is robust
  by design but is a timing test.
- Network: only 3 sites call localhost directly, all in the legacy integration files (`tests/integration/*`, live
  `:8000`).
- Ordering and global state:
  - `monkeypatch.setitem(sys.modules, …)`: 238 sites in 108 files.
  - Raw `sys.modules[...] =`: 85 sites in 77 files. Most of these load a script by path and are benign.
  - `importlib.reload`: 9 files.
  - The documented auth-module identity leak is fixed (`validate.yml:204-209`).
  - Module-level `os.environ` writes in 2 files; `tests/integration/test_people_mempalace_sync.py:22-30` loads the
    **live service `.env`** into the environment at import.

**Fail-fast.** `-x` is on every required pytest invocation (`validate.yml:212,217,230,236`), with one bash block per
step (§0.2).

**Version skew.** CI runs Python 3.10 (`validate.yml:44`); the live voice and brain lane runs 3.12.13 (probe
artifact interpreter). chromadb is not installed in CI after #2120.

---

## 7. Risk paths: what would fail if the behaviour broke

| risk | test(s) that would go red | strength |
|---|---|---|
| auth on every mutating route | `test_mutating_routes_declare_auth.py` (static, name-based, with controls); per-route runtime tests on a self-built `FastAPI()` (`test_arch_a2_auth_gaps.py`) | floor only. Reads are uncovered, there is no member-vs-guest matrix, and the real `main.app` assembly is not used |
| guest cannot write personal data | `test_guest_memory_no_write.py` (all sentinels including `guest-<id>`, member control, break-the-fix note) | good for **writes**. Reads use a different predicate (§2) |
| forget removes from every store | `test_forget_to_the_box.py` (W1 to W5), `test_forget_cascade_fail_loud.py`, `test_forget_cascade_durable_retry.py`, `test_memory_physical_erase.py` | **no completeness test**: nothing derives "every table with a user_id or personal text" from the migrations and checks it against `memory_forget_cascade.DB_STEPS` (`memory_forget_cascade.py:63`). A new table is silently out of the cascade |
| router call does not block the loop | `test_router_off_loop.py` (#2117: behavioural ticker with a negative control, a static pin over 5 files, a lag gauge) | good for the router. No general "no sync I/O in an async def" lint; the static pin covers `.route` / `.route_for` only (`:140-200`) |
| voice-turn identity is server-decided (#2097) | `test_voice_path_identity.py` (29 tests including relay, device-borrow and the replay id resolving no member) | good. One of the few files on the real `main.app` |
| device tokens never admin (#2099) | `test_device_token_principal.py:71-87` (parametrized `token_role` including `admin` → `require_admin` 403) | good |
| night window restores services | `tests/unit/test_night_window.py` (FakeHost; restore on exception, `:744`; 12B fails to start, `:755`; memory floor, `:665-672`) | logic only. The real restore is proven only by night runs |
| backup restores (#2119) | `tests/unit/test_zoe_offbox.py` (mirror, policy, drill-state, path-escape) | **none for an actual restore.** `fake_encrypt` reverses bytes (`:33-38`); there is no age/restic round trip and no DB restore. The live monthly drill is the instrument |
| panel targeting (#2117) | `test_panel_targeting.py` (with a negative control `:120`) plus the JS `test_touch_panel_targeting.js` | good |
| Telegram token name (#2108) | `test_auth_handoff.py:355-372` (`ZOE_TELEGRAM_BOT_TOKEN` wins, blank falls through, loopback notify) | precedence only. The revoked-legacy-token case is a live fact no test can see |
| intent-dispatch token gate | `test_intent_dispatch_auth_gate.py` (fake Request, flag on and off) plus a static route pin | the **code default is OFF** and live is ON via env. No test or probe catches a deploy losing the env pin [inferred] |
| Chroma crash isolation | **none.** The property does not exist: Chroma runs in-process (`memory_service.py:124`, deep-dive lane A §10) |
| TTS waterfall fallback | `test_voice_smoke_ci.py:73-98` (behavioural: all providers mocked, order asserted on `/synthesize`) plus `test_canonical_invariants` (static, `/synthesize` and `/stream`) | `/stream` is static-only |

---

## 8. Test hygiene

- **The household palace pin is real.** [verified]
  - `services/zoe-data/tests/conftest.py:34-57` pins `MEMPALACE_DATA_DIR`, the STT log, the reject ledger and
    `ZOE_LOG_DIR` into a temp dir *before* import, and raises if `memory_service` was imported first.
  - `tests/conftest.py` mirrors it.
  - `test_live_store_isolation.py` is the stated negative control (it fails under `--noconftest`).
- **The suite runs a non-production config.** The conftest also forces `ZOE_STRUCTURAL_VERIFIER=off` and
  `ZOE_BRAIN_SLOT_GUARD=off` for every test. [verified] The default production behaviour of those guards is
  exercised only in their own files.
- **The live database.**
  - `tests/integration/test_people_mempalace_sync.py:22-30` imports the live `services/zoe-data/.env`. In the live
    checkout, that is the household database. [verified code; where it runs is inferred]
  - The nightly runs `purge_orphaned_test_data.py --execute --yes --expect-db zoe --expect-host localhost:5432`
    after the integration step. That is a cleanup against the **live** database, needed because integration tests
    write there by design (`self-hosted-tests.yml:153-160`).
  - Several integration files post to the live `:8000`.
- **Writes to `~/`.** `test_zoe_core_client.py:105-131` appends to `~/.zoe-logs/` by default. No test writes into
  the repo tree; a static search found none.
- **Leftovers.** `tools/cleanup/archive/orphaned_20251008/` still holds two `*_test.py` files, an archive graveyard
  the canonical rules forbid. 13 test-shaped files live outside every testpath (`tools/`, `scripts/`).
- **Test-only production modules.** [inferred, generous heuristic that counts string references]
  - Imported only by tests: `autoresearch_bridge` (438 LOC), `zoe_capability_outcome_profile_handoff` (135) and
    `zoe_multica_memory_admission` (194).
  - Referenced by nothing: `routers/capability_matrix`.
  - The first dive counted 7, and some have since gone.
- **Unconditionally dead tests.** `tests/unit/test_auth_security.py` is a "security" test, skipped since 2026-07-06
  because its JWT harness no longer matches the app. `tests/unit/test_experts.py` (38 tests) has been skipped since
  2025-11.

---

## 9. What a truthful suite for this system would look like

1. **A thin real-app layer per lane.** About 40 to 80 tests total, each a lane's happy path plus its refusal:
   - `TestClient(main.app)` with the real lifespan, over `pgvector/pgvector:pg17`, the service container
     `validate.yml` already starts and migrates to head.
   - Fakes only at the HTTP boundary: the brain on `:11434`, STT, Kokoro and Home Assistant as `respx` or small
     ASGI fakes, and Chroma behind its existing seam.
   - One `/api/voice/command` test per identity class (member, panel, device token, guest, relay).
   - This replaces the 74 private `FastAPI()` assemblies as the place where wiring bugs (route inclusion, dependency
     order, blocking calls, fallbacks) can be seen.
2. **Generated property tests for the auth matrix.**
   - Enumerate `main.app.routes` at runtime, not by AST.
   - For each method×path × principal (anonymous, guest, `guest-<id>`, panel device, kiosk, member, admin, internal
     token), call it with a minimal body. Assert 401/403 or success per a declared policy table that sits next to
     the route.
   - Unknown routes fail. GET is included.
   - This is a superset of today's static A2 pin, and it would have caught the music, house-state and settings
     leaks.
3. **Negative controls as a required part of every gate script.**
   - Each instrument (voice probe, bar, day-sim, flag and canonical pins, nightly workflow) ships a `--self-test`
     that must go red on a planted fault before its green counts. Examples: STT returns `""`, the router returns
     `music` for everything, Kokoro raises, a required context is renamed.
   - The person bench already does this. Generalise its `--controls` contract.
4. **A mock budget rule.**
   - Never patch a pure function of the module under test, such as `is_guest_memory_user`.
   - Patch only at I/O seams (`httpx`, `db_pool`, `get_memory_service`).
   - Cap patch sites per test at about 3. Above that, the test moves to the real-app layer.
   - Enforce it with a cheap AST check in `validate_structure.py` over **new or changed** test files only.
5. **Remove can't-fail tests and dead lanes.** Delete them, don't archive.
   - **About 188 tests can be deleted with no property lost** [verified per file]:
     - 5 integration files importing retired modules (53 tests);
     - 11 live-`:8000` and script-style files with tolerant asserts or none collected (66);
     - 2 unconditionally skipped files (43);
     - 13 test files outside any testpath (26).
   - A further **~300 to 450 source-text tests** (`getsource` / `read_text` / `.index()` ordering) could be replaced
     by about 50 behavioural tests. Many duplicate a behavioural test already beside them (for example
     `test_ask_to_remember.py:427` next to `:433`, and the static waterfall next to `test_voice_smoke_ci`). That is
     an estimate, so it is [inferred].
   - The 6 live-brain sampling tests in `test_zoe_core_client.py` belong in an evaluation with a rate and an
     interval, not in pytest.

---

## 10. Ten changes, ranked by payoff over size

| # | change | evidence | size | payoff |
|---|---|---|---|---|
| 1 | Make the nightly honest: drop `continue-on-error` or fail the job at the end when any suite is not `success`; install node on the self-hosted runner; pass `--continue-on-collection-errors` | §0.1: 14 green nights with 0 integration tests, 0 `tests/unit` catch-all tests and 18 failures each | S (YAML plus a runner package) | very high: 143 never-run tests and 336 nightly-only tests start meaning something; the XSS and auth harnesses get a second runner |
| 2 | Remove `-x` from the required lanes and split the bash block, or use `set +e` and aggregate | §0.2: 211 of about 18,500 ran on one failure | XS | high: every red run shows every failure, which means fewer pushes per PR |
| 3 | Give the voice probe expected outcomes: a per-clip route/intent label in the baseline profile; regress on **any** previously-OK clip whose route hash changes; add a `--self-test` that injects a misrouting router and a blank STT | §3: OK means no refusal phrase; `route_hash` is unused for OK clips | M | high: turns "didn't refuse" into "did the same thing" |
| 4 | Widen `VOICE_PATH_PATTERNS` to the replay's own import closure, or compute it from imports | §0.4: `intent_router`, `stt_snap`, `memory_service` and `auth` are out of scope | S | high: the deploy gate covers what the replay exercises |
| 5 | Re-record the Samantha bar baseline with all 23 scenarios (samples 3, clean tree); refuse `--compare-baseline` when the current run has cells the baseline lacks; give the day-sim and person bench baselines | §0.5: 15 of 23 cells cannot regress, and 2 benches have no baseline | S (a box run) plus XS of code | high for the Samantha program |
| 6 | A real-app plus Postgres CI layer (`TestClient(main.app)` on the existing pg17 service), starting with voice, chat, memory forget and auth | §0.6: 7 real-app files; 1 Postgres file in the gate; 338 SQL-text asserts | M to L | high: the class of defects the first dive found |
| 7 | Generated auth-matrix property test from `app.routes`, GET included, with a policy table | §5 and §7 | M | high: closes read leaks and name-based vacuity |
| 8 | One guest predicate (`user_filters.is_unidentified`) used everywhere; ban patching it; one cross-module pin | §2: 8 predicates and 20 divergent mocks | S | medium to high: removes a latent `guest-<id>` read path |
| 9 | Delete the 188 dead tests, and move the 6 live-brain sampling tests to an eval script that records rates | §9.5, §6 | S | medium: a smaller, honest suite and less noise in the nightly |
| 10 | Pin the served config, not the template: make `unit_drift_check.py` part of `zoe_ground_truth.sh` and the deploy gate; have `test_required_gate_workflows` compare `REQUIRED_CONTEXTS` with `gh api …/protection` in the deploy job; add a ggshield planted-secret canary | §5 | S | medium: closes the remaining `REQUIRED_CHECKS = []`-class vacuities |

Also: run `validate` on Python 3.12 (the live interpreter), or add a 3.12 lane; and swap the session `event_loop`
fixture for the pytest-asyncio loop-scope setting (`tests/conftest.py:52`).

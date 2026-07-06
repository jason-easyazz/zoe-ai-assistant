---
type: executable-plan
audience: mid-tier execution agents (Sonnet/Codex) + Jason
status: planned
scope: Wave 4 god-file split of services/zoe-data/routers/voice_tts.py (4,812 lines)
source: tech-debt-remediation-plan.md Wave 4 ("voice_tts.py → router/session/STT")
verification: every symbol name + test patch site below verified against main@5c95e66a
  (2026-07-06) by grep/read of the real files. Line numbers WILL drift — re-verify with
  the grep commands in §2 before each PR; symbol NAMES are the contract.
---
# voice_tts.py split — per-PR execution packet 🔪

Design-only packet: six small, sequenced, **behavior-preserving move PRs** that extract
the mechanical seams out of `services/zoe-data/routers/voice_tts.py` (4,812 lines, on the
LIVE voice path), following the extraction pattern already proven by `tts_waterfall.py`
and `stt_wake_strip.py` (see `services/zoe-data/AGENTS.md`). Each PR is one seam,
verbatim moves only, re-export shim in the router, **replay-gated before merge**.

> ⚠️ **This file is on the live voice path.** Every PR here is voice-path work: the
> mandatory replay gate (root `AGENTS.md`) applies to ALL six steps, no exceptions.

## 1. The rules that make this safe (read before every PR)

### 1.1 The extraction pattern (established precedent — copy it exactly)
- New modules live **flat in `services/zoe-data/`** (like `tts_waterfall.py`,
  `stt_wake_strip.py`, `conversation_opener.py`), NOT under `routers/`.
- Moves are **verbatim**: same names, same bodies, same comments/docstrings. No renames,
  no reformatting, no "while I'm here" fixes. A reviewer must be able to diff
  moved-out == moved-in.
- The router keeps a **re-export shim** so every importer and every monkeypatching test
  keeps targeting `routers.voice_tts` unchanged:
  ```python
  # <Seam> mechanics live in <module>; re-exported here so existing importers
  # (main.py, voice_livekit.py, tests that monkeypatch this module,
  # tests/replay_samples.py, scripts/perf/*) keep working unchanged.
  from <module> import (name1, name2, ...)
  ```
  Explicit names only — never `import *`. Re-exports are permanent API, not a
  transition aid; do not schedule their removal.

### 1.2 The monkeypatch-topology rule (THE thing that breaks if you're careless)
Tests patch symbols **on the router module** (`monkeypatch.setattr(voice_tts, "X", ...)`).
A patch only intercepts when the **call site resolves the name through `voice_tts`
module globals**. Therefore:

- **Leaves move; callers, policy, and mutable state stay in the router.** A moved
  function must not be the *caller* of another symbol that any test patches on
  `voice_tts` — after the move it would resolve its own module's global and bypass the
  patch.
- Verified consequences baked into the step design below:
  - `_transcribe_audio` / `_transcribe_audio_impl` **stay** in the router
    (`test_voice_smoke_ci.py` patches `vt._run_moonshine`; the caller must stay).
  - `_voice_brain_memory` **stays** in the router (`test_voice_identity.py:61-62`
    patches `v._voice_user_identity` + `v._voice_recall_packet`, which it calls).
  - `_prewarm_stt_on_wake` / `_prewarm_brain_for_panel` **stay**
    (`test_stt_prewarm_on_wake.py` patches `v._ensure_moonshine`;
    `test_voice_facts_warm.py` patches `v._voice_brain_memory`, `v._VOICE_SESSIONS`).
  - Mutable state dicts (`_VOICE_SESSIONS`, `_PENDING_VOICE_IDENT`,
    `_PENDING_CONFIRMATIONS`, `_BG_TASKS`) **never move**: tests REBIND them
    (`monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})` in
    `test_voice_weather_queue.py`), and `routers/panel_auth.py:753` imports
    `_PENDING_VOICE_IDENT`, `_VOICE_SESSIONS`, `voice_command` from
    `routers.voice_tts`. A moved accessor reading its own module global would silently
    escape the rebind.
- Env-flag helpers (e.g. `_skybridge_only`) read `os.environ` per call and are tested
  via `setenv`, not `setattr` — safe to move with their callers (verified:
  `test_voice_skybridge_only.py` uses `monkeypatch.setenv`).

### 1.3 CI locks that pin source BY FILE PATH (update path, never values)
- `services/zoe-data/tests/test_canonical_invariants.py::test_stt_live_default_arch_is_moonshine_medium`
  reads `routers/voice_tts.py` for the `ZOE_MOONSHINE_ARCH` default and the
  `ModelArch.MEDIUM_STREAMING` fallback string. PR 3 moves that code → the test's file
  path must be updated **in the same PR**, with the asserted VALUES byte-identical.
  This is the guard following the code, NOT a rock swap; the `rocks:` yaml block in
  `docs/CANONICAL.md` is untouched. Say exactly that in the PR description.
- `test_canonical_invariants.py::test_tts_live_waterfall_keeps_kokoro_before_edge_before_espeak`
  and `::test_kokoro_tts_is_primary_live_voice_engine` pin the **waterfall ORDER inside
  the router handlers**. Nothing in this packet touches those handlers — if either test
  fails, you moved something you shouldn't have. Revert.
- `services/zoe-data/tests/test_voice_invariants.py::test_fast_first_audio_present_and_wired`
  asserts `"def _extract_first_unit" in` the **router source**. PR 2 moves that def →
  update the assertion to point at the new module in the same PR; keep the
  wired-into-stream-loop assertion (`_emit_sentence(first_unit)`) on the router, where
  the loop stays.

### 1.4 The gate — every PR, before merge (MANDATORY)
1. Focused pytest for the seam (listed per step) on the Jetson
   (GitHub-hosted runners can't run these — see `services/zoe-data/tests/AGENTS.md`).
2. Replay gate, baseline-compared, under the shared lock:
   ```bash
   flock /tmp/zoe-voice-harness.lock \
     python3 scripts/maintenance/voice_regression_probe.py
   ```
   Non-zero exit / `WARN` = regression = **do not merge** (a previously-working corpus
   command that now fails is a bug; per-stage speed must not regress). Numbers are
   RELATIVE drift vs baseline (warm harness) — never quote them as live performance.
   Full tool doc: `docs/knowledge/voice-pipeline.md`.
3. Greptile loop to merge: reply to AND resolve every thread (GraphQL
   `resolveReviewThread`), then `gh pr merge <n> --squash --auto`. Never
   `--admin`/`--force`. Verify merge via REST (`repos/.../pulls/N` → `.merged`).
4. DOX closeout: update the "Voice capability helpers" bullet in
   `services/zoe-data/AGENTS.md` to name the new module (one line, same style as the
   `tts_waterfall.py` entry), and tick the step's checkbox in §4 of this doc. PR 3
   additionally touches `docs/knowledge/voice-pipeline.md` (it cites
   `routers/voice_tts.py` for `warm_moonshine`/`_run_moonshine`).
5. Merged ≠ live: deploy is an operator `systemctl --user restart zoe-data.service`
   from the live checkout. Note it in the PR; don't restart prod yourself.

No new test files are planned. If a step does add one, it must be reachable by CI:
mark it `@pytest.mark.ci_safe` only if it runs slim-dep, and remember the self-hosted
lane runs the full dir (see tech-debt plan Wave 2).

## 2. Verify-first commands (run before each PR — line numbers drift)

```bash
# symbol map of the router
grep -n -E "^(class |def |async def |@router\.)" services/zoe-data/routers/voice_tts.py
# who imports voice_tts from outside
grep -rn "routers.voice_tts" services/zoe-data --include=*.py | grep -v routers/voice_tts.py
# which tests patch what on the router module (monkeypatch.setattr style)
grep -rn "setattr(v\(oice_tts\|t\)\?, \|setattr(vt, \|setattr(v, " services/zoe-data/tests
# ...and decorator/context-manager patch styles (@patch / mock.patch / mocker.patch).
# None exist today, but a moved symbol would silently escape one — always re-check.
grep -rn "patch(.*voice_tts" services/zoe-data/tests tests
```
External importers as of main@5c95e66a (all keep working via the shim — do not edit
them): `main.py` (health detail, `warm_moonshine`, `_transcribe_audio`,
`_synthesize_kokoro_sidecar`, `_extract_complete_sentences`, `synthesize`),
`routers/voice_livekit.py` (`_transcribe_audio`, `synthesize`), `routers/chat.py`
(`_VOICE_SYSTEM_PROMPT_SUFFIX`), `routers/panel_auth.py` (`_PENDING_VOICE_IDENT`,
`_VOICE_SESSIONS`, `voice_command`), `tests/replay_samples.py`
(`_transcribe_audio_impl`, `_clean_for_speech`, `_voice_brain_memory`,
`_voice_domain_context`, `_merge_brain_context`), `scripts/perf/measure_tts.py`
(`_extract_first_unit`, `_clean_for_speech`, `_fast_first_audio_enabled`).

## 3. The PR sequence (one seam per PR, in this order)

### PR 1 — speaker-ID mechanics → `voice_speaker_id.py` 📝 *(warm-up: zero test edits)*
- **Move (verbatim):** `_compute_resemblyzer_embedding` (~4448), `_cosine_similarity`
  (~4468).
- **Stays:** the `/enroll`, `/identify`, `/profiles` handlers (policy + DB).
- **Shim:** re-export both names from the router.
- **Tests:** no test references these directly (verified); run
  `test_voice_smoke_ci.py` + `test_canonical_invariants.py` as smoke.
- **Gate:** §1.4 in full (replay gate mandatory — `/identify` sits on the live wake path).

### PR 2 — text/segmentation mechanics → `voice_text_units.py` 📝
- **Move (verbatim):** `_ABBREV` (~356), `_UNIT_RE` (~369), `_voice_preprocess` (~382),
  `_split_sentences` (~523), `_extract_complete_sentences` (~537),
  `_FIRST_UNIT_MIN_CHARS` / `_FIRST_UNIT_SOFT_CAP` (~551), `_fast_first_audio_enabled`
  (~555), `_VOICE_TOOL_SENTINEL_PREFIXES` (~562), `_voice_tool_filler_enabled` (~565),
  `_VOICE_TOOL_FILLERS` (~574), `_VOICE_TOOL_FILLER_DEFAULT` (~585), `_voice_tool_filler`
  (~588), `_voice_tool_name_from_sentinel` (~604), `_extract_first_unit` (~624),
  `_parse_voice_escalation_delta` (~345) + `_VOICE_ESCALATION_MARKERS` (~342),
  `_normalize_voice_command_text` (~1701).
- **Stays:** every caller — the `/synthesize`, `/stream` handlers and the
  `voice_command` / `_generate_voice_stream` loops (all in the router), plus
  reply-shaping policy `_cap_voice_list_show_reply`, `_contains_decision_keyword`,
  `_should_handoff_calendar`.
- **Why safe:** all moved symbols are pure leaves; the tests that patch them
  (`test_voice_fastpath.py` patches `_extract_complete_sentences`) exercise callers
  that stay in the router, and direct-call tests reach them through the shim.
- **Test edit required (same PR, behavior-identical):**
  `test_voice_invariants.py::test_fast_first_audio_present_and_wired` — point the
  `"def _extract_first_unit"` / `"_fast_first_audio_enabled"` source checks at
  `voice_text_units.py`; keep the `"_emit_sentence(first_unit)"` wiring check on
  `routers/voice_tts.py`.
- **Tests:** `test_voice_invariants.py`, `test_voice_first_audio.py`,
  `test_voice_tool_filler.py`, `test_voice_fastpath.py`, `test_voice_transcribe.py`
  (calls `_normalize_voice_command_text`), `test_voice_smoke_ci.py`.
- **Gate:** §1.4 in full.

### PR 3 — Moonshine STT engine → `stt_moonshine.py` 📝 *(the invariant-path PR — care)*
- **Move (verbatim):** `_moonshine_model` / `_moonshine_load_error` / `_moonshine_lock`
  / `_moonshine_infer_lock` singletons (~1803-1809), `moonshine_arch` (~1812),
  `moonshine_ready` (~1816), `moonshine_error` (~1820), `_ensure_moonshine` (~1824),
  `_MOONSHINE_SAMPLE_RATE` (~1848), `_prepare_audio_for_moonshine` (~1851),
  `_run_moonshine` (~1912), `warm_moonshine` (~1947); plus the STT capture/log leaves:
  `_env_float` (~1717), `_env_int` (~1724), `_voice_stt_log_path` (~1731),
  `_wav_duration_seconds` (~1738), `_rotate_voice_stt_log` (~1749),
  `_log_voice_stt_sample` (~1764), `_maybe_capture_stt` (~1962).
  (`_run_moonshine` keeps its `from stt_wake_strip import _strip_wake_word` dependency —
  move the import with it.)
- **Stays:** `_transcribe_audio` (~1985), `_transcribe_audio_impl` (~1998) — the
  callers `test_voice_smoke_ci.py` patches through (`vt._run_moonshine`); the
  `/transcribe` handler; `_prewarm_stt_on_wake` + `_stt_prewarm_on_wake_enabled`
  (`test_stt_prewarm_on_wake.py` patches `v._ensure_moonshine` and calls the prewarm —
  the caller must keep resolving through router globals).
- **Shim:** re-export the moved CALLABLES and constants (main.py health reads
  `moonshine_ready` / `moonshine_error` / `moonshine_arch` off the router module;
  `main.py:942` imports `warm_moonshine` from `routers.voice_tts`). Safe because those
  accessors are *functions* that read `stt_moonshine`'s own globals at call time
  (`moonshine_ready()` → `_moonshine_model is not None`) — a `from`-import of a
  function stays live. **Do NOT re-export the mutable singletons**
  (`_moonshine_model`, `_moonshine_load_error`, the two locks): a `from`-import of a
  later-reassigned module variable is a frozen snapshot — the router's copy would stay
  `None` after warmup and lie to anything reading it. They have zero readers outside
  the moved functions today (verified by grep); keep them private to
  `stt_moonshine.py`, reached only through the accessor functions.
- **Test edit required (same PR, values byte-identical):**
  `test_canonical_invariants.py::test_stt_live_default_arch_is_moonshine_medium` —
  change the two file paths (`routers/voice_tts.py` → `stt_moonshine.py`) and the
  path-hint comment near the top of the file. The asserted default (`MEDIUM` arch) and
  fallback pin (`ModelArch.MEDIUM_STREAMING`) MUST NOT change. `docs/CANONICAL.md`
  untouched. State this explicitly in the PR body so the reviewer sees the guard moved
  WITH the code.
- **Doc edit (same PR, OKF):** `docs/knowledge/voice-pipeline.md` cites
  `routers/voice_tts.py` for warmup/`_run_moonshine` — note the engine now lives in
  `stt_moonshine.py`, re-exported by the router.
- **Tests:** `test_voice_transcribe.py`, `test_stt_prewarm_on_wake.py`,
  `test_canonical_invariants.py`, `test_voice_smoke_ci.py`, `test_health_readiness.py`.
- **Gate:** §1.4 in full — this PR touches the STT stage itself; treat the replay-gate
  said-vs-did diff as the primary verdict.

### PR 4 — panel UI broadcast helpers → `voice_ui_broadcast.py` 📝 *(biggest chunk, ~600 lines)*
- **Move (verbatim):** `_skybridge_only` (~648), `_status_card` (~659),
  `_should_supersede_voice_weather_action` (~477),
  `_should_supersede_voice_skybridge_action` (~493), `_broadcast_weather_ui` (~671),
  `_broadcast_calendar_ui` (~774), `_broadcast_skybridge_ui` (~848),
  `_broadcast_lets_talk_ui` (~969), `_parse_voice_form_field` (~1034),
  `_broadcast_action_form_panel` (~1111), `_broadcast_reminder_ui` (~1171),
  `_request_auth_ui` (~1244).
- **Why this grouping:** the broadcasts call `_skybridge_only()` and
  `_should_supersede_*` **internally** (verified ~677/780/930/980/1176) — those must
  move in the SAME module or the internal calls dangle. `_skybridge_only` is env-read
  per call and tested via `setenv` (`test_voice_skybridge_only.py`), so moving it does
  not break patching. All helpers are self-contained (function-local imports of
  `push.broadcaster`, `ui_orchestrator`, `database`, `panel_form_state`; no module
  state — verified).
- **Stays:** `_request_voice_identity_challenge` (~1301) — it writes
  `_PENDING_VOICE_IDENT` (state stays, so its writer stays); `VOICE_PROFILES` (~467);
  all callers (`voice_command` etc.), so `test_voice_weather_queue.py`'s patches of
  `voice_tts._broadcast_skybridge_ui` / `voice_tts.synthesize` keep intercepting.
- **Shim:** re-export all moved names.
- **Tests:** `test_voice_weather_queue.py` (also calls `_request_auth_ui` and the
  supersede helpers directly), `test_voice_skybridge_only.py`, `test_voice_presence.py`,
  `test_voice_smoke_ci.py`.
- **Gate:** §1.4 in full.

### PR 5 — recall/memory leaf builders → `voice_recall.py` 📝
- **Move (verbatim):** `_VOICE_RECALL_SEARCH_LIMIT` / `_VOICE_RECALL_MAX_FACTS` /
  `_VOICE_RECALL_FACT_CHARS` / `_VOICE_RECALL_MAX_LINES` (~125-134),
  `_voice_recall_packet` (~137), `_voice_relational_lines` (~208),
  `_voice_recall_fallback` (~240), `_voice_user_identity` (~289),
  `_VOICE_BRAIN_DOMAIN_CONTEXT` (~310), `_voice_domain_context` (~317),
  `_merge_brain_context` (~337).
- **Stays:** `_voice_brain_memory` (~252) — the composer. It calls
  `_voice_recall_packet` and `_voice_user_identity`, and `test_voice_identity.py:61-62`
  patches BOTH on the router module before calling it; keeping the composer in the
  router preserves that patch-through via the re-exported names. Also stays:
  `_prewarm_brain_for_panel`, `voice_command`/`voice_turn*` callers.
- **Why the moved set is safe:** internal calls within the set
  (`_voice_recall_packet` → `_voice_relational_lines` / `_voice_recall_fallback`) are
  patched by NO test on the router module (verified: `test_voice_recall_packet.py`,
  `test_voice_recall_compose_2c.py`, `test_voice_brain_memory.py` patch the *source*
  modules — `memory_service`, `zoe_agent`, `user_portrait`, `db_pool`,
  `intent_router` — which keeps working regardless of where the caller lives).
- **Shim:** re-export all moved names (replay_samples imports `_voice_domain_context`
  and `_merge_brain_context` from the router).
- **Tests:** `test_voice_recall_packet.py`, `test_voice_recall_compose_2c.py`,
  `test_voice_brain_memory.py`, `test_voice_domain_context.py`,
  `test_voice_identity.py`, `test_voice_facts_warm.py`, `test_voice_invariants.py`
  (its `_voice_brain_memory` wiring checks stay green — the call sites stay put).
- **Gate:** §1.4 in full.

### PR 6 — stateless session/user resolution → `voice_session.py` 📝
- **Move (verbatim):** `_load_voice_history` (~104), `_resolve_panel_default_user`
  (~1393), `_panel_session_trust_window_s` (~1418),
  `_resolve_recent_panel_session_user` (~1428).
- **Stays (deliberately — this is the fiddly one):** `_VOICE_SESSIONS` +
  `_VOICE_SESSION_TTL_S` + `_get_or_create_voice_session` (~72-102),
  `_PENDING_VOICE_IDENT` (~76), `_PENDING_CONFIRMATIONS` + confirm/cancel keyword
  policy (~4797-4812), `_spawn_bg`/`_BG_TASKS`. Reasons: (a) `panel_auth.py:753`
  imports the state dicts from `routers.voice_tts`; (b)
  `test_voice_weather_queue.py`/`test_voice_facts_warm.py` REBIND
  `voice_tts._VOICE_SESSIONS` — a moved accessor would read its own module's global
  and escape the rebind; (c) `test_voice_transcribe.py` mutates
  `voice_tts._VOICE_SESSIONS["p1"]` then calls `_get_or_create_voice_session`. Session
  STATE is router policy; only the stateless DB/env lookups move.
- **Why safe:** the four moved helpers touch no module state (DB + env reads only —
  verified); `test_voice_weather_queue.py` patches `_resolve_recent_panel_session_user`
  / `_resolve_panel_default_user` on the router and the caller (`voice_command`) stays
  there; `test_voice_transcribe.py` calls `_resolve_recent_panel_session_user` directly
  through the shim.
- **Shim:** re-export the four names.
- **Tests:** `test_voice_transcribe.py`, `test_voice_weather_queue.py`,
  `test_voice_facts_warm.py`, `test_voice_smoke_ci.py`.
- **Gate:** §1.4 in full.

## 4. Status checklist (tick on merge, note the PR #)

- [ ] PR 1 — `voice_speaker_id.py`
- [ ] PR 2 — `voice_text_units.py`
- [ ] PR 3 — `stt_moonshine.py`
- [ ] PR 4 — `voice_ui_broadcast.py`
- [ ] PR 5 — `voice_recall.py`
- [ ] PR 6 — `voice_session.py`

**End state (honest):** ~1,450 lines of mechanics leave; `voice_tts.py` lands ≈3,350
lines — the route handlers, the TTS waterfall ORDER, session/confirmation state, and
the policy composers. The residual bulk is `voice_command` (~1,640 lines) +
`voice_turn_stream`: decomposing those is a **behavior-risky refactor, not a move**,
and is explicitly OUT of this packet — if wanted later it gets its own design doc and
its own replay-gated plan.

## 5. Do NOT (hard rules)

- **Do NOT create parallel files** — no `voice_tts_v2.py`, `voice_tts_new.py`,
  `_old`/backup copies, and no second voice router. Same contract as the chat-router
  rule in `services/zoe-data/routers/AGENTS.md`. Branches, not file duplication.
- **NEVER move the TTS waterfall ORDER out of the router.** Kokoro → Edge → espeak
  ordering in `/synthesize`, `/speak`, `/stream` is policy, pinned by
  `test_canonical_invariants.py` — it stays inline in `routers/voice_tts.py` by
  contract (`services/zoe-data/AGENTS.md`).
- **No behavior changes mixed with moves.** A move PR changes zero runtime behavior; if
  you spot a bug while moving, ship the move, file the bug separately. No renames, no
  signature changes, no "cleanup" of the moved bodies.
- **Never weaken a guard to make a move pass:** invariant tests may have their FILE
  PATHS updated to follow moved code (PR 2/PR 3, enumerated above); asserted values,
  rocks, and the `docs/CANONICAL.md` rocks block never change.
- **Never move mutable module state** (`_VOICE_SESSIONS`, `_PENDING_VOICE_IDENT`,
  `_PENDING_CONFIRMATIONS`, `_BG_TASKS`) or the functions that write it.
- **Do NOT propose swapping the rocks** while in here (Moonshine v2 Medium, Gemma 4
  E4B+MTP, Kokoro are fixed; optimise around them).
- **No merge without the replay gate** (§1.4), no `--admin`/`--force`, no
  batch-merging multiple seam PRs — land them serially, each baselined against the
  previous merge.
- **Don't run the harness without `flock /tmp/zoe-voice-harness.lock`** (two Kokoro
  loads ≈2.3 GB each OOM the box), and don't quote its warm numbers as live latency.

# Lane A — zoe-data core request path and structure (architecture review, 2026-10-18)

Scope: `services/zoe-data` request path, god files, brain lanes, flags, lifecycle, data layer, auth, observability, tests.
Method: read-only grep / sed / AST scans over the worktree at `62f1e7e83`. No tests run, no MCP servers used.
Labels: **[verified]** = I read the code at the cited lines. **[inferred]** = a reasoned consequence I did not execute or observe live.
All paths are relative to the repo root unless they start with `routers/`, which means `services/zoe-data/routers/`. Bare `*.py` names are in `services/zoe-data/`.

---

## 0. Headline findings (ranked)

1. **The two-stage router does a blocking HTTP call on the event loop for every routed turn.** [verified] `semantic_router.route()` is synchronous (semantic_router.py:696). With `ZOE_ROUTER_HEAD=active` it calls `_two_stage_active` → `router_two_stage.decide` (semantic_router.py:721-722, 467-474). That makes a synchronous `urllib.request.urlopen(..., timeout=1.5s)` to the `:11436` sidecar (router_two_stage.py:298-302, 39). Its own comment says the call takes about 400 ms (semantic_router.py:455). It is called directly inside async handlers: `routers/voice_tts.py:3446` (every `/command`, `/turn` and `/turn_stream` turn), `fast_tiers.py:618` (chat and LiveKit, where no decision is passed in), `stt_snap.py:477` and `main.py:2391`. Only `skybridge_service.py:2050` wraps it in `asyncio.to_thread`. Because zoe-data runs as ONE uvicorn worker (scripts/setup/systemd/zoe-data.service.d/60-py312-venv.conf), every other request stalls for that long: panel WebSockets, `/health`, intent-dispatch callbacks from the brain, and TTS streaming of another turn. [inferred] This is the same failure class as the documented accept-queue hangs.
2. **Production code paths fall back to writing as `family-admin`.** [verified] When a direct intent handler returns None, `execute_intent` falls through to an `mcporter` subprocess (intent_router.py:4755-4761). For `list_show`, `list_remove` and `calendar_create`, `_build_command` builds the command **without `user_id`** (intent_router.py:5609-5626). `mcp_server.py:42-45` then "falls back to family-admin", because `ZOE_MCP_STRICT_USER_ID` defaults to false. User text is also spliced unescaped into the command (`text="{item}"`, `title="{title}"`) before `shlex.split` (intent_router.py:5606, 5620). [inferred] A typed `"` can inject extra `key=value` arguments, including `user_id=`.
3. **There are five or more voice entry lanes with different tier stacks.** [verified]
   - `/api/voice/command`, `/turn` and `/turn_stream` (voice_tts) run `fast_tiers` (conversation and domain phases) plus guest_policy plus the scope gate.
   - `/ws/voice/` (main.py:2991-3334, used by `services/zoe-ui/dist/voice.html` and `touch/voice.html`) runs its own Skybridge, instant-intent and brain sequence. It never calls `fast_tiers`, so it skips the distress, provenance, feedback, correction, self-model and identity tiers.
   - LiveKit (`routers/voice_livekit.py:559`, channel `livekit`).
   - Telegram, through chat with `channel=telegram`.
   - The HA conversation component.
   - The parity rig covers only `voice_command` against `chat_stream_generator` (services/zoe-data/tests/test_voice_lane_parity.py:3-4).
4. **The dispatcher's in-code default is still the dormant `core` lane, and that lane has drifted from flue.** [verified] `use_flue_brain()` returns True only when `ZOE_BRAIN_BACKEND == 'flue'`, and the default is `'core'` (brain_dispatch.py:154-164). Drift, as grep hit counts (flue / core): `clean_goodbye` 4/0, `role_guess_guard` 11/0, `brain_slot_guard` 9/0, recall floor 14/0, `seam_recall` 7/0, speculation 22/0, `replay_isolation` 3/0, manner 20/0. A lost `.env` line, or a failover to core, therefore serves turns without those guards. Replay isolation is refused outright off-flue (brain_dispatch.py:434-456). Persona has drifted too: the flue soul carries a `RECALL` paragraph that is absent from `services/zoe-core/SOUL.md`, although `labs/flue-zoe-brain-2x/src/soul.ts:5` claims it is a "verbatim copy" (soul.ts:34-35). [verified by string diff]
5. **The legacy `zoe_agent.py` (4,569 lines) is a dormant lane but a live library.** [verified]
   - On every production boot it warms the KV cache with its own prompts: `JETSON_AGENT_MODE=true` is set in zoe-data.service:13, which triggers main.py:1253-1258 → zoe_agent.py:3562-3607 (`_ZOE_SOUL_STATIC`, `_ZOE_SOUL_VOICE`). Flue never sends those prefixes. [inferred] They waste prefill and llama `--cache-ram` slots.
   - Live code imports its internals: `routers/voice_tts.py:367`, `expert_dispatch.py:762`, `memory_digest.py:983,1249,1289`, `memory_service.py:1162`, `memory_extractor.py:1194`, `mcp_server.py:1992-2028` (web search and browse), `routers/chat.py:346-349`, `main.py:925-927`.
6. **The music API has 17 mutating endpoints with no auth dependency at all**, plus one leak and two fake-success endpoints. [verified]
   - Unauthenticated music mutations: routers/music.py:441-735 (control, play, queue clear/save, playlists add, favourite, transfer, group). `/api/` is proxied by nginx (services/zoe-ui/nginx.d/locations.inc:125).
   - `GET /api/settings` (routers/stubs.py:74-93) returns **every** `system_preferences` row to a guest. That includes the household default weather lat/lon written at routers/weather.py:647-653. It also shadows a second `/api/settings` at main.py:2394, which is dead because `include_router(stubs_router)` at main.py:2231 registers first.
   - Fake success: `board_cancel` returns "cancelled" and does nothing (routers/system.py:1880-1882). `save_user_layout` returns ok and does not save (routers/stubs.py:45-47).
7. **Engineering-harness code (PR pipeline, Multica, greploop, git push / gh pr create) runs inside the household voice API process.** [verified] 58 harness modules totalling 19,668 LOC sit in `services/zoe-data/*.py`. Examples: `pi_executor.py:261-263` (`git push`, `gh pr create`), `greploop_guard.py` (2,390 lines), `pipeline_store.py`, `kanban_phase_budget.py`. main.py also carries about 470 lines of Multica chain helpers (main.py:315-783) and a 550-line poll loop defined inline in `lifespan` (main.py:1455-2005). `omnigent_issue_executor.py:226,309,319` calls `time.sleep`. Wave 4 of the tech-debt plan ("fence the harness out of the prod process") never started (docs/architecture/tech-debt-remediation-plan.md:54).
8. **The god files grew after their split plans were written.** [verified]
   - `voice_tts.py`: 4,812 lines (2026-07-06, plan written) → 7,370 now. 71 commits since. Split plan 1 of 6 done (docs/architecture/voice-tts-split-plan.md:292-297).
   - `voice_command` alone is **2,216 lines** (routers/voice_tts.py:3381-5596).
   - `intent_router.py`: 4,414 → 6,193. `detect_intent` is 956 lines (778-1733). `execute_intent` is 1,191 lines (3609-4799).
   - `main.py`: 2,650 → 3,334.
   - `chat.py` is the one success: 4,035 → 3,437 after W4-C2/C3 (#1435, #1541).
9. **Config is `os.getenv` everywhere, and the flag inventory undercounts.** [verified]
   - 647 raw env reads outside tests, against 164 `typed_env` call sites in 67 files.
   - The inventory only matches `ZOE_*` names, so 68 distinct non-ZOE env names read by runtime code are invisible. Several gate the core chat path: `HERMES_FAST_PATH`, `JETSON_AGENT_MODE`, `OPENCLAW_ALL_TOOLS_ENABLED`, `OPENCLAW_GUARDED_AUTO` (routers/chat.py:548-610). The chat tool fast path is gated by an **OPENCLAW_** flag (routers/chat.py:609, 1782) even though OpenClaw is being retired.
   - The inventory's ".env.example coverage" metric reads the wrong file. `tools/audit/flag_inventory.py:201` checks the root `.env.example` (4 ZOE entries), so it reports 2 of 673 documented. `services/zoe-data/.env.example` actually documents 48 of 673.
10. **Guest and identity semantics are copy-pasted at least 10 times, inconsistently.** [verified] Guest-sentinel sets live in provenance_answers.py:64, distress_handoff.py:42, memory_provenance.py:202, conversation_feedback.py:39, struggle_detector.py:57, voice_session.py:60 (includes `family-admin`), identity_facts.py:73 (adds `default` and `system`), routers/voice_tts.py:161, panel_principal.py:54 and music_history.py:45 (missing `anonymous` and `voice-daemon`). A canonical `user_filters.is_unidentified` exists, but only 35 modules import it. The comment at intent_router.py:3545 records a real bug of this class ("anonymous / voice-daemon used to slip through").

---

## 1. Request-path maps

### 1a. Voice turn (panel Pi daemon → reply audio) [verified]
1. `scripts/setup/zoe_voice_daemon.py:3119/3267/3296/3362` sends `POST /api/voice/turn_stream`.
2. `voice_turn_stream` (routers/voice_tts.py:5749) runs these stages:
   - Base64 decode.
   - Speculation gate `_speculation.open_gate` (around 5782).
   - `_take_stt_stream_text` (under-speech stream, 6281).
   - Batch STT through in-process Moonshine: `_transcribe_audio` (2952) → `_transcribe_audio_impl` (2978) → `_run_moonshine` (2879).
   - Then it calls `voice_command(stream=True)` (docstring 5758).
3. `voice_command` (3381-5596) runs these steps in order:
   1. Identity resolution and the voice-session decision (3405-3700). `_voice_session` is a different concept from `_VOICE_SESSIONS`, which is the 5-minute history dict at 143-144.
   2. Synchronous semantic and two-stage router (3437-3450). **This is where the event loop blocks.**
   3. Spacer and speculation pre-detect with `detect_intent` and `classify_skybridge_intent` (3471-3475).
   4. Active action-form field filling (3735-3900), which calls `execute_intent` directly.
   5. Pending yes/no confirmations (3903-3985).
   6. `fast_tiers.resolve(phase="conversation")` (4032-4050).
   7. STT snap (4054-4099).
   8. Skybridge resolve, cards and auth challenge (4203-4330).
   9. Write-intent confirmation gate with `detect_and_extract_intent` (4361-4740), which runs the list, calendar, reminder and people gates using `execute_intent` and `fast_tiers.keyword_intent_allowed`.
   10. Public-intent short circuit: `detect_intent`, `guest_policy.can_use_voice_intent`, `execute_intent`, `synthesize` (4746-4842).
   11. Weather fallback, a third `detect_intent` (4846-4865).
   12. `fast_tiers.resolve(phase="domain")` → expert_dispatch (4867-4897).
   13. B3/B4 scope gate and PIN challenge (4899-4990).
   14. `brain_dispatch.brain_streaming` (4991, 5179, 5342) → sentence split (`_extract_first_unit` 1170, `_extract_complete_sentences` 991) → `synthesize` (2385) → Kokoro sidecar waterfall → streamed back.
4. Brain: `brain_dispatch.brain_streaming` (brain_dispatch.py:942) → `_flue_streaming_labeled` (749) → `zoe_flue_client.run_flue_brain_streaming` (zoe_flue_client.py:2050). The message carries an in-band envelope: `" zoe-spec:… zoe-replay:… zoe-persona:… zoe-manner:… zoe-uid:…\n<blocks>\n<msg>"` (zoe_flue_client.py:525, 587). It goes to the sidecar on `:3579` (`labs/flue-zoe-brain-2x`).
5. Tool calls: the sidecar's `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` (22 tools) calls back to `POST /api/system/intent-dispatch` (routers/system.py:2800). That passes through the allowlist (2772-2788), `voice_speculation.hold_speculative_dispatch`, and `intent_router.execute_intent` (3609). On a direct-handler miss it falls through to the `mcporter` subprocess and the `mcp_server.py` stdio worker (intent_router.py:4755-4761). Recall goes through `GET /api/memories/for-prompt` (routers/memories.py:895; zoe-tools.ts:350).
6. The reply is persisted by `_schedule_voice_chat_save` (3208) and `_run_voice_memory_passes` (3264) on background tasks (`_spawn_bg`, 121).

Measured inside one `voice_command` [verified by grep counts within 3380-5597]:
- 8 `detect_intent` / `detect_and_extract_intent` call sites, with up to about 4-5 regex passes on one turn [inferred from the fall-through order].
- 23 `return {` reply envelopes.
- Across the file: 15 `_schedule_voice_chat_save` calls, 29 `await synthesize(` calls, 36 `b64encode` calls and 14 `voice:responding` broadcasts. The same "speak → save → memory passes → envelope" tail is open-coded about 15 times. `_answer_from_tier` (around 3999) is the one shared helper.

### 1b. Chat turn (web / Telegram) [verified]
1. `POST /api/chat/` → `chat()` (routers/chat.py:2767-2768, `Depends(resolve_acting_user)`) → `locked_chat_stream` (2678) → `chat_stream_generator` (1508-2677, **1,170 lines**).
2. The generator runs these stages in order:
   - Hermes `force_agent` path (1629-1700).
   - `use_intent_fast_path` gate, which depends on `OPENCLAW_ALL_TOOLS_ENABLED` (1782).
   - `fast_tiers.resolve(channel=…)` (1792-1804).
   - Pi-hybrid production lane `_run_chat_pi_hybrid_lane` (635, used at 1831). It is default-off (`pi_hybrid_production.py:87`).
   - `detect_and_extract_intent` and `execute_intent` (around 1893, 2032).
   - `_brain_oneshot` / `_brain_streaming` (2100, 2236).
   - Escalation to Hermes (2374-2420) or the OpenClaw ACP stream (2507) and `run_openclaw_agent` (2137, 2597).
3. The non-stream branch of `chat()` (2768-3155) **re-implements the same sequence**: fast-path gate 2909-2916 duplicates 1782-1789, `fast_tiers.resolve` 2918-2936 duplicates 1792-1804, the intent gate 2984-3007 duplicates 1893-2032, and the brain call at 3047 duplicates 2236. [verified side by side]
4. Brain and tools are the same as voice steps 4-5.

### 1c. Duplicated mechanics (pairs with lines)
| Mechanic | Implementations |
|---|---|
| Turn pipeline (tier order) | `voice_command` (voice_tts.py:3381-5596) · `chat_stream_generator` (chat.py:1508-2677) · `chat()` non-stream (chat.py:2768-3155) · `/ws/voice/` (main.py:2991-3334) · LiveKit (voice_livekit.py:559, 683, 753, 1648). `fast_tiers.resolve` is the intended shared core (fast_tiers.py:1-17), but voice, chat and `/ws/voice` each wrap it with their own pre and post logic, and `/ws/voice` skips it. |
| Intent parsing | `intent_router.detect_intent` regex (778-1733) · `detect_and_extract_intent` (2179) · `semantic_router` / two-stage (route 696) · `skybridge_service.classify_skybridge_intent` · `intent_classifier_llm.py` / `pi_intent_classifier.py` (shadow) · Flue model tool choice. |
| Tool schemas | `mcp_server.TOOLS` (75 entries, mcp_server.py:131) · `zoe_agent._TOOLS` (26, zoe_agent.py:434) and `_VOICE_TOOLS` (17, :309) · `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` (22) · `services/zoe-core/abilities/*.ts` (11 names) · `system._DISPATCHABLE_INTENTS` (29 intents, system.py:2772). Nothing derives one from another [inferred: no generator found by grep]. |
| Persona / system prompt | `services/zoe-core/SOUL.md` · `labs/flue-zoe-brain-2x/src/soul.ts` (drifted, includes RECALL) · `zoe_agent.py:204` `_ZOE_SOUL_BASE`, `:275` `_STATIC`, `:287` `_VOICE`, `:3444` inline · `persona_layer.py:337` `_FIXED_LEAD` · `routers/voice_tts.py:803` `_VOICE_SYSTEM_PROMPT_SUFFIX` (imported by chat) · `agent_sync.py:26,438` patches `~/.hermes/SOUL.md`. |
| Memory recall injection | `/api/memories/for-prompt` (memories.py:895, used by core `memory.ts:129` and the flue `recall_memory` tool) · flue seam recall floor (zoe_flue_client.py:658-1042, `ZOE_SEAM_RECALL_INJECT`) · voice `_voice_recall_packet` / `_voice_brain_memory` (voice_tts.py:246-410), which calls `MemoryService.search` directly · `zoe_agent._build_memory_context` (1529) / `_mempalace_load_user_facts` (1419) · chat `db_memory_context` with `ZOE_CHAT_INJECT_DB_MEMORY` (chat.py:567). |
| Guest/identity sentinel | At least 10 frozensets (see headline 10) · `auth.GUEST_USER_ID` (auth.py:32) · `user_filters.is_unidentified` · `intent_router._is_guest_identity` (3543). |
| Auth for voice | `_validate_device_token` / `_require_voice_auth` / `_require_livekit_auth` (voice_tts.py:812-850) · `auth.get_current_user` device-token branch (auth.py:195-204) · `panel_principal` · `panel_auth._resolve_device_token_user`. |
| "Voice session" | `_VOICE_SESSIONS` (5-minute TTL history + skybridge context, voice_tts.py:143-144) · `voice_session.py` (identity session, `ZOE_VOICE_SESSION_TTL_S` = 120, voice_session.py:1-20) · panel sessions (`_touch_panel_session`, voice_tts.py:2068; `ui_panel_sessions`). The split plan's PR 6 target file name `voice_session.py` is already taken by a different concept (docs/architecture/voice-tts-split-plan.md:297). |
| Env-bool parsing | `brain_dispatch.use_core_brain` uses the not-in-falsy rule (brain_dispatch.py:143-151) · `.lower()=="true"` (chat.py:548-610, main.py:1323, 1337) · `in ("on","true","1")` (main.py:1359, 1402, 1434) · `typed_env.env_bool`. The plan doc already names this as a bug class (chat-split-and-typed-config-plan.md:251-256). |

---

## 2. God files: responsibilities and obvious split

### `routers/voice_tts.py` (7,370 lines; 21 routes)
Responsibilities:
1. Turn orchestration (`voice_command` 3381-5596, `voice_turn` 5597, `voice_turn_stream` 5749-6280).
2. Moonshine STT engine and STT logging (2600-2998).
3. Recall and brain context (`_voice_recall_packet` … `_voice_brain_kwargs`, 234-660).
4. Sentence segmentation and first audio (977-1220).
5. UI broadcasts (weather, calendar, skybridge, lets-talk, reminder and forms, 1242-1910).
6. Panel identity and session trust windows (1911-2212).
7. Confirmation and decision lexicon (2213-2383, 7349-7370).
8. TTS endpoints `/synthesize`, `/speak`, `/stream` and announcements (2384-2599).
9. Wake, ambient and prewarm (6404-6667).
10. Speaker ID, enrolment and profiles (6668-7237).
11. LiveKit token (7244).
12. ChatGPT auth status (7238).

Stats: 189 in-function lazy imports, 226 broad `except Exception`, 73 `except: pass`.

Split plan: docs/architecture/voice-tts-split-plan.md. **1 of 6 done** (PR 1, `voice_speaker_id.py`, #1540, 2026-07-26). PRs 2-6 are not started (lines 293-297), and the plan's line numbers are stale by about 2,500 lines. The obvious split:
- Stateless leaves out first (text units, Moonshine engine, UI broadcast).
- Then a `VoiceTurnPipeline` object whose stages are the steps in §1a. Each stage returns a `TierResult`, and one `_finish_turn` owns speak, save and memory.
- The monkeypatch-topology rule (plan §1.2) is what blocks a naive move. Tests patch about 87 test files' worth of `routers.voice_tts` symbols, so a facade-plus-shim is mandatory.

### `intent_router.py` (6,193 lines)
Responsibilities:
1. Regex NLU `detect_intent` (956 lines) plus slot extractors (reminders 1734-1835, lists 2446-2539, calendar 2420).
2. Offer and follow-up matching (2003-2178).
3. LLM-assisted `detect_and_extract_intent` (2179-2419).
4. About 20 `_execute_*_direct` fulfilment handlers that hit the DB directly (2575-3482).
5. The `execute_intent` dispatcher (1,191 lines).
6. The `mcporter` subprocess fallback (2540-2574, 4755-4799, 5599-5927 `_build_command` / `_format_response`).
7. Daily briefing (4823-4952).
8. Weather (4953).
9. Music and HA media control (5104-5598).
10. Smart home, volume, calculator and greeting (5927-6087).
11. Date and time parsing (6088-6193).
12. OpenClaw message shaping (`openclaw_user_message`, 301).

Split: `intent_detect/` (patterns + extractors), `intent_exec/<domain>.py` (one per domain: lists, calendar, reminders, people, music, home, briefing) behind a registry keyed by intent name, `nlu_time.py`. **Delete** the mcporter path once every dispatchable intent has a direct handler. No split plan exists.

### `main.py` (3,334 lines)
Responsibilities:
1. Readiness probes (112-293).
2. **Multica chain orchestration** (315-783, plus the poll loop 1455-2005).
3. Memory capture probe and retry (784-863).
4. Runtime probes (864-902).
5. MemPalace migration gate (903-971).
6. Music discovery batch job (972-1029).
7. Router self-train job (1030-1106).
8. `lifespan` (1107-2062): about 20 background tasks, APScheduler jobs and trigger registration.
9. CORS and WS-origin policy (2089-2190).
10. Router inclusion (2203-2280).
11. Root routes `/health`, `/readyz`, `/metrics`, `/api/router/classify`, `/api/settings` (dead duplicate) and `/api/internal/broadcast` (2311-2435).
12. Push WS plus 7 per-domain WS endpoints (2436-2933).
13. **A whole voice lane** `/ws/voice/` (2934-3334).

Split: `app_factory.py` (routers, middleware, CORS), `lifecycle/` (one `register(app, scheduler)` per subsystem), `ws/` (push and domain sockets), `voice_ws.py` (or delete it if `voice.html` is retired), and Multica helpers into the executor service outside zoe-data.

### `routers/system.py` (3,080 lines; 70 routes on two routers)
Responsibilities:
1. Platform, health and status (106-254).
2. pi-intent lab status and labels (263-487).
3. Hermes model profiles (488-578).
4. OpenClaw prefs, upgrade and a background loop (579-730).
5. **Memory digest and consolidation background loops** (731-928, started from main.py:1186-1187).
6. Memory admin triggers (967-1032).
7. A2A agent card and tasks on `_agent_card_router` (1066-1368).
8. Engineering workflow and greploop guard (1369-1462).
9. Agent registry, delegation, squad, costs and LLM stats (1463-1764).
10. Agent board and Multica webhook (1765-1972).
11. Evolution proposals (1974-2385).
12. Updates install (2386-2447).
13. Display brightness, volume, prefs and panel idle-logout proxied to the Pi (2448-2771).
14. **The brain callback seam** `/intent-dispatch`, `/web-search`, `/delegate-sync` (2772-2963).
15. Telegram link and register (2964-3080).

Split: `routers/brain_seam.py` (the load-bearing part, intent-dispatch), `routers/display.py`, `routers/agents_a2a.py` (or out of process), and move the loops into lifecycle modules. No plan exists.

### `routers/chat.py` (3,437 lines)
Responsibilities:
1. Intent card and form payload builders (106-530).
2. Compose hook (577).
3. Pi-hybrid lane (635-732).
4. Frustration detector (733-773).
5. Memory candidate extraction and persistence (915-1117).
6. Session and message persistence (1135-1288).
7. OpenClaw and Hermes agent runners (1289-1336).
8. Research evidence package and screenshot (1337-1433).
9. Approvals and run state (1434-1507).
10. The streaming generator (1508-2677).
11. The non-stream `chat()` (2768-3155).
12. Sessions, runs, feedback and tasks CRUD (3156-3437).

Plan: docs/architecture/chat-split-and-typed-config-plan.md. **C2 and C3 done** (`chat_stream_protocol.py` #1435, `chat_hermes_stream.py` #1541). C1 (brain dispatch dedup) is effectively done: chat imports `brain_dispatch` (chat.py:353-358). C4 (card builders) is not done. The next obvious step is to collapse `chat()` non-stream into "run the generator and collect", which removes about 390 duplicated lines.

---

## 3. Three brain lanes

- **Lane-specific code** [verified line counts]:
  - flue: `zoe_flue_client.py` 2,648 + `labs/flue-zoe-brain-2x/src` 5,723 TS (+ 7,223 TS test lines; 56 Python test files mention the client).
  - core: `zoe_core_client.py` 1,391 + `services/zoe-core` extensions/abilities 1,730 TS (+ 1,647 test lines; 27 Python test files).
  - legacy: `zoe_agent.py` 4,569 (71 test files).
  - Shared selector `brain_dispatch.py`: 986 lines. Roughly 6,000 lines of the "brain lane" Python is redundant for the live configuration [inferred: core + legacy + the multi-lane failover machinery].
- **Is legacy reachable in production?** Yes, three ways [verified]:
  - (a) `_fallback_lane()` returns `"legacy"` when `ZOE_USE_CORE_BRAIN` is off (brain_dispatch.py:377-379).
  - (b) Selected outright when the backend is not flue and core is off (brain_dispatch.py:518-562).
  - (c) `pi_intent_lab.py:305-312` runs `run_zoe_agent` from a production-mounted router (main.py:2257), and it mutates `os.environ` process-wide via `_temporary_env` during an await. [inferred] That races any concurrent turn that reads `ZOE_PI_INTENT_*`.
  - Separately, its **library functions and KV warmup run on every boot** (headline 5).
- **What three lanes cost:**
  - Six or more persona definitions (§1c).
  - Five tool schema lists.
  - Four recall-injection paths.
  - `_drop_flue_only_kwargs` / `_LOADABLE_CONTEXT_KEYS` / `context_loader` machinery exists only because non-flue lanes take different kwargs (brain_dispatch.py:434-508).
  - A failover lane that cannot honour replay isolation (refuses, 446-456) and lacks the flue guards.
- **Drift (flue vs dormant core)** [verified grep counts; zoe_flue_client.py vs zoe_core_client.py]: `clean_goodbye`, `role_guess_guard`, `brain_slot_guard`, recall floor / `seam_recall`, speculation, `replay_isolation`, provenance, distress, identity_facts and manner are absent from core. `restraint` and `mute` are present in both (7/4, 4/2), and a comment at zoe_core_client.py:1215 shows mute write-back was a past drift. Persona: the `RECALL` paragraph exists only in flue (soul.ts:34-35). No test diffs SOUL.md against soul.ts [inferred from grep: `persona_layer.test.ts` pins flue's own sha256 only].
- **Doctrine contradiction:** VISION.md says "Pi (`zoe-core`) is the brain". CANONICAL.md calls core "not retired; extend it". Live is flue, which lives under `labs/` even though `labs/AGENTS.md` describes labs as isolated from the runtime. The code comments `# zoe-core by default` at main.py:3205 and voice_tts.py:4991, and brain_dispatch.py:1-14, are stale.

---

## 4. Flag sprawl (method + counts)

Method:
- `docs/knowledge/flag-inventory.json` lists 681 flags: `prod` 673, `lab` 8. It is AST-scanned from Python only (`tools/audit/flag_inventory.py:150-232`), excludes tests (176-183), and matches `ZOE_[A-Z0-9_]+` literal keys only.
- I classified each prod flag by its literal default set.

Counts:
- **Readers:** 624 of 673 (93%) are read in exactly one file. Reader location: zoe-data 570, scripts 116, zoe-auth 5, zoe-core (py) 3. 95 flags are read only by `scripts/` or `tools/`, never by the runtime.
- **Defaults:**
  - Literal off (`'0'`, `'false'`, `False`, `'off'`): 72.
  - `'shadow'`: 9.
  - Literal on: 45.
  - Unset/empty (`-`, `''`): 177. Most of these are opt-in booleans parsed against a truthy set, so they are effectively off. [inferred, not per-site verified]
  - Numeric/string config: 341.
  - Conflicting defaults across readers: 29. 18 of those are literal-vs-literal conflicts, for example `ZOE_BASE_URL` is `'http://localhost:8000'` / `'http://zoe.local'` / `'https://192.168.1.218'`, and `ZOE_KOKORO_VOICE` is `''` / `'af_sky'`.
  - Hard-coded LAN IPs as defaults: `ZOE_PI_HOST="192.168.1.61"` (routers/system.py:2470) and `ZOE_BASE_URL`.
- **Flag-dark estimate:** 81 explicit (off or shadow), up to about 258 counting unset opt-ins.
- **Dead (read nowhere outside tests/docs):** 0 by construction, because the inventory excludes test readers. A better proxy is flags whose only readers are orphan modules imported by nothing but tests. I found 7 such modules (2,190 LOC): `multica_triage_judge` 533, `autoresearch_bridge` 438, `calendar_ical` 394, `multica_board_runner` 295, `pipeline_cross_review` 201, `zoe_multica_memory_admission` 194, `zoe_capability_outcome_profile_handoff` 135. They carry 4 runtime-dead flags: `ZOE_BOARD_POLL_S`, `ZOE_MULTICA_CROSS_REVIEW`, `ZOE_MULTICA_KILL_SWITCH`, `ZOE_MULTICA_TRIAGE_JUDGE`. [verified: grep found no non-test importer.]
- **Invisible to the inventory:** 68 distinct non-ZOE env names read by runtime code, for example `HERMES_FAST_PATH`, `JETSON_AGENT_MODE`, `OPENCLAW_*`, `MEMORY_AUTO_INGEST`, `WHATSAPP_FLOW_ENABLED` and `PORTRAIT_*`. TypeScript reads (flue sidecar, zoe-core) are also not inventoried.
- **Typed config:** none. `typed_env.py` (115 lines) gives parsers only (no schema or registry, by design, chat-split plan :258-282). 157 of 673 flags go through it. Migration stalled at T3 because byte-equivalence hit diminishing returns (plan :304-312). 647 raw reads remain.

---

## 5. Startup and lifecycle

`lifespan` (main.py:1107-2062) starts, in-process:
- Logging (1119).
- `.env` bootstrap (1124). This runs **after** module import, so import-time snapshots such as `auth._ZOE_INTERNAL_TOKEN` (auth.py:346) and `chat._USE_ZOE_CORE` (chat.py:559) see only the systemd env [inferred hazard for non-systemd launches].
- DB init.
- Brain readiness probe (1138).
- MemPalace migration (1141).
- Memory capture retry (1184).
- OpenClaw version loop (1185, flag).
- Nightly digest and consolidation loops (1186-1187, defined in routers/system.py).
- Idle consolidation (1192).
- Voice-stitch prewarm (1198).
- Update loop (1207).
- **Moonshine warmup** (1211, deliberate).
- Semantic router ONNX warm (1216).
- **Omi pendant transcription and day-review loops** (1225-1236).
- Runtime health loop (1242).
- Task watchdog (1247).
- **Legacy KV warmup** (1253-1258).
- APScheduler proactive engine with 8 triggers, including a no-op `OpenClawTrigger` base with an empty prompt (1278-1305; proactive/triggers/openclaw_trigger.py:40-50).
- Jobs: Multica sync, YT Music refresh, music history, kiosk token revoke, music discovery, **router self-train** (1323-1450).
- **Multica poll loop** (1455-2007).
- LiveKit agent (2011-2013).

Things that arguably belong out of process (beyond Moonshine):
1. **Chroma `PersistentClient`** (memory_service.py:113-142). The documented chroma 0.6.3 HNSW segfault crash-loops the entire voice API (memory: `reference_chroma_hnsw_segfault_recovery`). A native crash in one dependency takes down STT, chat and the brain seam together.
2. **Omi transcription and day review**, which are batch ASR and LLM work in the API process.
3. **Memory digest, consolidation and idle-consolidation loops**, which are LLM-heavy and night-window work.
4. **The Multica / PR harness** (headline 7).
5. **Router self-train**, which spawns an 8-hour training via `run_to_completion` from the API's scheduler (main.py:1030-1060).
6. **The two-stage router sidecar call** (should at least be in a thread).

Single-process risks [verified config, inferred effect]:
- One uvicorn worker with no `--limit-concurrency` and no `--timeout-graceful-shutdown`, bound to `0.0.0.0:8000` so it is reachable from the LAN, bypassing nginx (zoe-data.service:10 and the drop-in).
- Synchronous HTTP on the loop (headline 1).
- 189 lazy imports in voice_tts alone, and 2,274 across the package. The first hit of an import inside a turn pays the import cost on the loop.
- `BaseHTTPMiddleware` (`StructuredLoggingMiddleware`, middleware/logging.py:205) wraps every streaming voice response. [inferred: known Starlette overhead and contextvar issues]
- `EnvironmentFile=-%h/.hermes/.env` loads the retired Hermes env into the household API (zoe-data.service).

---

## 6. Data layer

Persistence locations [verified]:
- **Postgres** through `db_pool` (asyncpg) with an aiosqlite-style compat shim (`db_compat.py`, `db_pool.AsyncpgCompat`). 50 non-test files still use `?` placeholders, against 38 that use `$1`. Alembic has 54 migrations. Runtime DDL survives only in `music_history.py` and `memory_idle_consolidation.py`.
- **Chroma** PersistentClient, encapsulated in `memory_service.py` only (good). `sqlite3` is used read-only against Chroma's sqlite in `memory_residue.py` and `memory_index_health.py`.
- **JSON / JSONL on disk:**
  - `data/vapid_keys.json` (routers/push.py:18,46): a private key inside the checkout tree.
  - `data/music_prefs.json`: non-atomic write (music_service.py:315,333).
  - `music_discovery` recommendations (music_discovery.py:185).
  - `self_model` snapshot (self_model.py:766).
  - `voice_greeting` state (atomic, voice_greeting.py:90-92).
  - `memory_reject_ledger` (atomic, :94-96).
  - JSONL logs: `~/training/data/intent-misses.jsonl` (intent_router.py:1714-1721), `~/training/data/production-feedback.jsonl` (zoe_agent.py:3418), router head log (semantic_router.py:184-193), STT sample log (voice_tts.py:2692), pi_intent shadow and evidence, hermes model-profile audit, greploop state.
  - `agent_sync.py:330-458` rewrites `~/.hermes/SOUL.md` and OpenClaw self files from the API process.
  - The YT Music username file (ytmusic_signin.py:275).
- **In-process state** that is lost on restart: `_VOICE_SESSIONS`, `_PENDING_VOICE_IDENT`, `_PENDING_CONFIRMATIONS`, `_BG_TASKS` (voice_tts.py:59-153, 7349); chat `_CHAT_CONTEXTS`; brain breaker state.

There is no consistent repository layer.
- **30 of 52 routers execute raw SQL** on the request `db`. Top counts: voice_tts 36, people 35, chat 31, system 29, panel_auth 29, lists 21, panel_provision 19, ui_actions 18, reminders 18, rooms 16.
- **33 routers import `database` / `db_pool` / `db_compat`.**
- `intent_router` duplicates fulfilment against the same tables (`_execute_*_direct`, 2575-3482). `mcp_server.py` (3,493 lines) is a third writer to the same tables from a subprocess. [verified] So lists, calendar and reminders each have three or more write paths: the router endpoint, the intent_router direct handler and the mcp_server tool.

---

## 7. Auth model and mutating endpoints that admit the guest

Model [verified]:
- `get_current_user` (auth.py:181-251):
  - With no `X-Session-ID` it returns the **guest principal** (215-220), or a family-admin promotion when `ZOE_UNAUTHENTICATED_ROLE=family-admin` (205-214).
  - With an `X-Device-Token` it returns the panel principal (195-204).
  - An invalid session gives 401. An auth-service outage is fail-closed (503) by default.
- `require_admin` / `require_signed_in` (270-295) are the real gates.
- Internal trust is loopback **or** `X-Internal-Token` (349-364). `require_intent_dispatch_auth` (386-428) requires the token when `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN=1`, which is live. The body-asserted `user_id` on intent-dispatch is trusted once the caller passes that gate (system.py:2810-2826); guest gating then happens inside `execute_intent` (intent_router.py:3614-3624).
- nginx runs in Docker and reaches `host.docker.internal:8000`, so proxied requests are not loopback (docker-compose.yml:39-40). Good.

AST scan of 447 routes (246 mutating):
- **26 mutating routes declare no `Depends` at all.** 24 of them are real gaps or need review:
  - routers/music.py 441, 453, 475, 497, 548, 572, 588, 621, 641, 650, 658, 665, 673, 695, 703, 710, 735: **17 endpoints, no auth**.
  - music_setup.py 119, 156, 194, 240: token-in-body or one-time link, intentional.
  - proactive.py:105: pending id as bearer, intentional.
  - proactive.py:213 and memories.py:1438: inline checks.
  - system.py:1899 `multica_webhook`: the **status UPDATE happens before the secret check**. The `UPDATE evolution_proposals SET status` runs at about system.py:1925; `_multica_webhook_dispatch_allowed` is checked only before dispatch, at about :1944. So an unauthenticated caller can flip proposal status.
- **114 mutating routes depend only on `get_current_user`-class helpers**, which admit the guest. In 35 of them the handler body has no role, guest, require or 403 token. Notable cases:
  - Household-wide guest writes: rooms create/update/delete (rooms.py:274, 306, 347, 366, 416).
  - Panel config (panel_config.py:652).
  - Display prefs with a persisted `pi_host`, brightness and volume (system.py:2557, 2651, 2699; SSRF-guarded).
  - `board_review` creates a Multica issue (system.py:1862).
  - OpenClaw prefs (system.py:603).
  - Dashboard layout and widgets (dashboard.py:97, 116, 163).
  - Chat session delete, resume and cancel (chat.py:3242, 3340, 3358; user-scoped [inferred]).
  - Weather prefs (weather.py:598).
  - Stubs that fake success (stubs.py:24, 45, 118).

  Some of these are household-policy-intentional (the "fridge rule"). The point is that the policy is implicit per handler, not declared.
- **Read leak:** `GET /api/settings` dumps all `system_preferences` rows to a guest (stubs.py:74-93), including the household weather location.

---

## 8. Error handling and observability

- **Logging is written twice in two formats** [verified both handlers]. `setup_json_logging()` at import (main.py:55-59) replaces root handlers with a JSON stdout handler, which systemd appends to `~/.zoe-logs/zoe-data.stdout.log` (20-capture-output.conf). `configure_logging()` in lifespan (main.py:1119; logging_setup.py:50, 175-197) then adds a plain-text rotating file in `~/.zoe-logs`. [inferred] Every line lands twice. logging_setup's own docstring ("without this the root logger has no handler", main.py:1110-1113) contradicts main.py:59.
- **Metrics:** prometheus-client 0.26.0 is in use (`voice_metrics.py`, `memory_metrics.py`, `guest_policy.py`). `/metrics` is internal-token gated (main.py:2403-2421). Nobody scrapes it [inferred: no Prometheus or Grafana unit in scripts/setup/systemd].
- **Tracing:** none on the live path. OpenTelemetry exists only in legacy `zoe_agent.py:59-65`. `request_id` is minted per HTTP request (middleware/logging.py:205-225), but it is not propagated to the flue sidecar or back through `/intent-dispatch`. The turn's identity, replay and speculation context travels as in-band text envelopes inside the user message (zoe_flue_client.py:418-640). The BRAIN_LANE log line is the only per-turn correlation (brain_dispatch.py:57-60).
- **Health:** `/health`, `/readyz` (main.py:2341-2382), `/api/system/health`, `/api/system/status` plus about 20 subsystem status routes. A readiness report covers brain, STT and TTS (main.py:112-293). [inferred] There is no event-loop-lag metric, which is the one signal that would have caught headline 1 and the accept-queue hangs.
- **Error style:** 2,037 broad `except Exception` and 397 `except …: pass` across 184k LOC (voice_tts 226/73, zoe_agent 79/25). Failures are mostly silent degradation by design ("never break a turn"). Without error counters per tier, silent degradation is unobservable.
- **Missing for a system this size:**
  - Event-loop lag and blocked-call detection (asyncio debug slow-callback logging, or a lag gauge).
  - Per-tier hit and failure counters for the about 20 `fast_tiers` stages.
  - Turn-id propagation across zoe-data, the sidecar and intent-dispatch.
  - A single structured log stream.
  - Crash isolation for Chroma.

---

## 9. Tests

- `services/zoe-data/tests`: 681 `test_*.py` (684 including subdirs), of which **649 are `ci_safe`-marked**.
- `tests/unit`: 181 (173 `ci_safe`). `tests/integration`: 20. `tests/e2e`: 2. `services/zoe-auth/tests`: 13. labs TS: 52. Total Python test files: 936.
- Ratio: about 95% unit and contract tests, about 2% integration, a negligible e2e share. Only 18 zoe-data test files drive `main.app` through TestClient.
- 87 test files import `routers.voice_tts` and patch module globals. That is the main brake on splitting it (split plan §1.2).
- Coverage of the core path is dense:
  - `turn_stream`: 19 files.
  - `/ws/voice`: 6 (activity frames and origin only, not tier parity).
  - Chat non-stream: 12.
  - intent-dispatch: 10.
  - mcporter: 10.
  - LiveKit: 11.
- Gaps [verified by grep]:
  - `routers/capability_matrix.py` has 0 test references.
  - No test asserts auth on the 17 music endpoints.
  - No test checks `GET /api/settings` contents for a guest.
  - No test checks that `/ws/voice/` runs the same tiers as `voice_command`.
  - No test pins `soul.ts` against `SOUL.md`.
  - No test fails when a blocking call (urllib or the sync router) runs on the loop.
  - No test checks that a `_build_command` fallback carries `user_id`.

---

## 10. Top-10 structural recommendations

| # | Problem | Evidence | What to do | Size | Voice-path risk |
|---|---|---|---|---|---|
| 1 | Sync sidecar HTTP plus ONNX on the event loop | semantic_router.py:696-722, router_two_stage.py:298-302, voice_tts.py:3446, fast_tiers.py:618 | Wrap `_sr.route` in `await asyncio.to_thread(...)` at the 4 call sites (as skybridge_service.py:2050 already does), or make `decide` use async httpx. Add an event-loop-lag gauge to `/metrics`. | S | Medium. On the voice path, so replay-gated. Behaviour should be identical and latency should improve under concurrency. |
| 2 | mcporter fallback writes as family-admin and accepts arg injection | intent_router.py:4755-4761, 5606-5626; mcp_server.py:42-45 | Give every `_DISPATCHABLE_INTENTS` intent a direct handler, delete `_run_mcporter`/`_build_command`/`_format_response` (about 400 lines), and set `ZOE_MCP_STRICT_USER_ID=true` for the remaining stdio users. | M | Medium. `execute_intent` sits on every lane, so it needs a replay. |
| 3 | Five voice lanes, `/ws/voice` skips fast_tiers | main.py:2991-3334 vs voice_tts.py:4032-4897 | Retire `/ws/voice/` and `voice.html`, or route it through `voice_command`. Extend `lane_parity_rig` to LiveKit and chat non-stream. Collapse `chat()` non-stream into "collect the generator". | M | Low for panel (not that lane). High value for safety (distress tier). |
| 4 | Dormant lanes cost about 6k lines and have drifted, with a `core` default | brain_dispatch.py:154-164, 377-379; zoe_flue_client vs zoe_core_client grep; soul.ts:34 | Owner decision: either (a) make `flue` the in-code default and pin it in `test_canonical_invariants`, then retire legacy as a lane (move its library functions such as `_mempalace_load_user_facts`, `_web_search_ddg` and `warmup_kv_cache` into neutral modules, and drop the boot warmup), or (b) keep core but add a lane-parity test for guards and persona. Update VISION/CANONICAL, which disagree. | L | High. Brain dispatch is voice-path, so replay-gate each step. |
| 5 | Engineering harness runs in the voice API process | 58 modules / 19.7k LOC; main.py:315-783, 1455-2005; pi_executor.py:261-263 | Execute Wave 4 "fence the harness": move Multica poll, greploop, pipeline_* and pi_executor into the Flue executor or their own unit. Delete the 7 orphan modules (2.2k LOC). | L | Low (not on the turn path), but it frees RAM and loop time on the box. |
| 6 | Unauthenticated mutations and a settings leak | music.py:441-735; stubs.py:74-93; system.py:1899-1944 | Add `Depends(panel_or_member)` to the music router (router-level `dependencies=`), make `/api/settings` return an allowlist, move the webhook auth check before the UPDATE, and delete the fake-success stubs (system.py:1880, stubs.py:45). Add a CI test: "every mutating route declares an auth dependency from an allowlist". | S | Low (the panel sends a device token, which needs verifying with the panel music bar). |
| 7 | `voice_tts.py` at 7.4k lines with a 2.2k-line function | voice_tts.py:3381-5596; split plan 1/6 | Resume plan PRs 2-5 (leaves first). Then extract `_finish_turn` (speak, save, memory, envelope, used about 15 times) and the stage list. Rename plan PR 6 (`voice_session.py` is taken). Freeze growth: add a test that fails when the file exceeds N lines. | L | High. Every PR is replay-gated (that is the plan's design). |
| 8 | `intent_router.py` at 6.2k lines with 3 or more write paths per table | detect_intent 778-1733, execute_intent 3609-4799, `_execute_*_direct` 2575-3482, mcp_server TOOLS | Split into a domain-per-module executor registry and make each domain service the single writer that routers, intent_router and mcp_server all call. Derive tool schemas (mcp, flue zoe-tools, `_DISPATCHABLE_INTENTS`) from one registry and generate the TS list. | L | Medium-high (on the voice path; replay). |
| 9 | Config: 647 raw env reads, 68 un-inventoried names, broken doc metric, conflicting defaults | flag_inventory.py:201; chat.py:548-610; inventory conflicts | Fix the inventory to read `services/zoe-data/.env.example` and include non-ZOE plus TS reads. Add a "conflicting default" CI check. Rename the `OPENCLAW_*` / `HERMES_FAST_PATH` / `JETSON_AGENT_MODE` gates that drive the live chat path to `ZOE_*`, or delete them. Then do a sign-off-driven "deliberate delta" normalisation pass, as the plan already concluded. | M | Low-medium (voice_tts flags need replay). |
| 10 | Observability and crash isolation | double logging main.py:59 + 1119; Chroma in-process memory_service.py:142; no turn-id propagation | One logging setup (JSON to a rotating file, nothing on stdout). Pass `request_id` / `turn_id` as a header to the sidecar and back on intent-dispatch. In wire 3, replace the in-band `zoe-*` envelopes with structured JSON fields. Put Chroma behind a small sidecar or subprocess so a segfault cannot take the voice API down. Add per-tier counters in `fast_tiers.resolve`. | M | Medium (envelope and wire change on the brain path; Chroma move touches recall latency). |

Smaller items worth a ticket:
- Unify the guest-sentinel frozensets on `user_filters.is_unidentified` (10 sites listed in headline 10).
- Delete the dead duplicate `/api/settings` at main.py:2394.
- Delete the no-op `OpenClawTrigger` registration (main.py:1282, 1289).
- Remove `EnvironmentFile=-%h/.hermes/.env` from zoe-data.service.
- Make the `music_prefs.json` write atomic.
- Move `vapid_keys.json` out of the checkout.
- `pi_intent_lab._temporary_env` mutates process env during an await (pi_intent_lab.py:310). Pass the flags explicitly instead.
- `services/zoe-data/AGENTS.md` is 305 KB in 200 lines; the longest line is 11,090 chars and 54 lines are over 2,000 chars. A contract nobody can read in full defeats DOX. Move the history into OKF records and keep the contract under about 20 KB.
- The live brain lives under `labs/` (`labs/flue-zoe-brain-2x`) against `labs/AGENTS.md`'s isolation rule. Promote it to `services/`.

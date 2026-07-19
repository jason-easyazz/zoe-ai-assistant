---
type: Reference
title: ZOE_* flag inventory (GENERATED)
description: Auto-generated inventory of every ZOE_* environment flag read in the codebase — defaults, readers, typed_env adoption, and .env.example coverage.
tags: [flags, env, configuration, generated]
timestamp: 2026-07-19T00:00:00Z
---

# ZOE_* flag inventory

**STATUS: GENERATED — do not hand-edit.** Regenerate with:

```
python3 tools/audit/flag_inventory.py
```

Last generated: 2026-07-19. The table body is deterministic (sorted, no
timestamps) so regeneration diffs show real flag changes only.

Default `dynamic` = not statically extractable; `(required)` = bare
`os.environ[...]` subscript (raises if unset); `-` = no default argument.

## Production flags

392 flags; 392 not documented in `.env.example`.

| Flag | Default(s) | typed_env | .env.example | Readers |
|---|---|---|---|---|
| `ZOE_A2A_CLIENT_TIMEOUT_S` | `'30'` | no | NO | `services/zoe-data/a2a_client.py` |
| `ZOE_A2A_TOKEN` | `''` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_ACP_DELIVERY_MODE` | `'live'` | no | NO | `services/zoe-data/zoe_acp_client.py` |
| `ZOE_AGENT_LLM_TIMEOUT` | `'120.0'`, `dynamic` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_MAX_TOOL_ITERS` | `'5'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_TOOL_TIMEOUT` | `'10.0'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_VOICE_LLM_TIMEOUT` | `dynamic` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_ALLOWED_WS_ORIGINS` | `-` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_ANNOUNCE_POLL_ENABLED` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ANNOUNCE_POLL_S` | `'5.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ANNOUNCE_STRICT_PANEL` | `''` | no | NO | `services/zoe-data/voice_announce.py` |
| `ZOE_ANNOUNCE_TTL_S` | `''` | no | NO | `services/zoe-data/voice_announce.py` |
| `ZOE_ASSISTANT_ROOT` | `-`, `dynamic` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py`<br>`services/zoe-data/greploop_guard.py`<br>`services/zoe-data/multica_ticket_contract.py` |
| `ZOE_AUTH_ALLOWED_ORIGINS` | `'http://localhost,http://localhost:3000,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,https://zoe.the411.life,http://zoe.local'` | no | NO | `services/zoe-auth/main.py` |
| `ZOE_AUTH_FAIL_CLOSED` | `'true'` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_AUTH_SETUP_TOKEN` | `''` | no | NO | `services/zoe-auth/core/account_setup.py` |
| `ZOE_AUTH_URL` | `'http://localhost:8002'` | no | NO | `services/zoe-auth/touch_panel/quick_auth.py`<br>`services/zoe-data/auth.py`<br>`services/zoe-data/main.py`<br>`services/zoe-data/routers/auth.py`<br>`services/zoe-data/routers/panel_auth.py` |
| `ZOE_AUTORESEARCH_RUN_ROOT` | `'data/autoresearch'` | no | NO | `services/zoe-data/routers/autoresearch.py` |
| `ZOE_AUTO_APPROVE_THRESHOLD` | `'0'` | no | NO | `services/zoe-data/evolution_notice.py` |
| `ZOE_BARGE_MIN_MS` | `'192'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_BARGE_SPEECH_THRESHOLD` | `'0.30'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_BASE_URL` | `'http://localhost:8000'`, `'http://zoe.local'`, `'https://192.168.1.218'` | no | NO | `services/zoe-auth/oidc/startup.py`<br>`services/zoe-data/routers/panel_provision.py`<br>`services/zoe-data/routers/system.py` |
| `ZOE_BENCHMARK_OUTPUT` | `dynamic` | no | NO | `scripts/utilities/gemma4_trial_benchmark.py` |
| `ZOE_BENCH_REPEATS` | `'3'` | no | NO | `services/zoe-core/bench/pi_brain_latency.py` |
| `ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED` | `'false'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_BRAIN_BACKEND` | `'core'` | no | NO | `services/zoe-data/brain_dispatch.py` |
| `ZOE_BRAIN_PREWARM_ON_WAKE` | `'1'`, `True` | yes | NO | `services/zoe-data/routers/voice_livekit.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `ZOE_BRAIN_TOKEN` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_BRAIN_UNIT` | `'llama-server.service'` | no | NO | `scripts/maintenance/router_selftrain.py`<br>`services/zoe-data/main.py` |
| `ZOE_BRAIN_URL` | `-` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_BUFFER_DELAY_S` | `'0.8'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_BUFFER_PHRASES` | `'1'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_CAP_A2A_DELEGATE` | `'3000'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_ROWS` | `'10'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_SEARCH` | `'0'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_TRANSCRIPT` | `'150'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_MEMORY_LIST` | `'0'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_MEMORY_LIST_ROWS` | `'25'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_SELF_CAPS` | `'2000'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_WEB_RESEARCH` | `'6000'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CHAT_CONTEXT_MAX_SESSIONS` | `'2000'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_CHAT_INJECT_DB_MEMORY` | `'0'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_CHAT_URL` | `'http://localhost:8000'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CHEAP_PR_AGENT_API_URL` | `'https://openrouter.ai/api/v1/chat/completions'` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py` |
| `ZOE_CHEAP_PR_AGENT_CMD` | `-` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD` | `'0'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_MODEL` | `'cheap-pr-agent'`, `'deepseek/deepseek-chat-v3.1'` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py`<br>`services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_URL` | `-` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_COMPOSE_MAX_TOKENS` | `'700'` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_STREAM_BUDGET_S` | `'6'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_COMPOSE_TIMEOUT_S` | `'14'` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_UI` | `''` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_VOICE_BUDGET_S` | `'8'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_CONTACT_BACKFILL_ENABLED` | `''` | no | NO | `services/zoe-data/contact_backfill.py` |
| `ZOE_CONTEXT_TOKEN_BUDGET` | `'5500'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CONVERSATION_ENDER_ACKS` | `dynamic` | no | NO | `services/zoe-data/conversation_opener.py` |
| `ZOE_CORE_DATA_URL` | `-` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_IDLE_TIMEOUT_S` | `'20'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MAX_CONCURRENCY` | `'2'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MAX_WORKERS` | `'4'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MODEL_ID` | `'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf'` | no | NO | `services/zoe-core/bench/pi_brain_latency.py`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MODEL_URL` | `-` | no | NO | `services/zoe-core/bench/pi_brain_latency.py` |
| `ZOE_CORE_PI_COMMAND` | `'pi'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_PROVIDER` | `'local-gemma'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_TIMEOUT_S` | `'180'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_VOICE_MODEL_MAXTOKENS` | `'512'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_DAILY_BRIEFING_CACHE_MAX_USERS` | `'64'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DAILY_BRIEFING_CACHE_TTL_SECONDS` | `'120'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DATA_DB` | `dynamic` | no | NO | `services/zoe-data/database.py` |
| `ZOE_DATA_URL` | `'http://127.0.0.1:8000'`, `-` | no | NO | `scripts/maintenance/check_emotional_thread.py`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_DB_ACQUIRE_TIMEOUT_S` | `''` | no | NO | `services/zoe-data/db_pool.py` |
| `ZOE_DEFAULT_LIGHT_ENTITY` | `'light.all'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DEFAULT_MEDIA_PLAYER` | `'media_player.all'` | no | NO | `modules/zoe-music/main.py`<br>`services/zoe-data/intent_router.py` |
| `ZOE_DEVICE_TOKEN` | `-` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_DIGARR_AI_BASE_URL` | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_AI_MODEL` | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_AI_TIMEOUT_S` | `'300'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_IMAGE` | `'docker.io/iuliandita/digarr:latest'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_PASSWORD` | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_PORT` | `'3199'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_USER` | `'zoe'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_DEFAULT_MOOD` | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MAX_USERS` | `'4'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MIN_FREE_MB` | `'1500'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MIN_PLAYS` | `'10'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_EDGE_TTS_TIMEOUT_S` | `'5'` | no | NO | `services/zoe-data/tts_waterfall.py` |
| `ZOE_EDGE_TTS_VOICE` | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_EMBEDDING_MODEL_VERSION` | `'minilm-v1'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_EMOTIONAL_FOLLOWUP_ENABLED` | `''` | no | NO | `services/zoe-data/proactive/triggers/emotional_followup.py` |
| `ZOE_EMOTIONAL_RECALL_ENABLED` | `''` | no | NO | `services/zoe-data/routers/memories.py` |
| `ZOE_ENABLE_OPENCLAW_BROWSER_FALLBACK` | `'false'` | no | NO | `services/zoe-data/browser_broker.py` |
| `ZOE_ENABLE_OPENCLAW_EXECUTION` | `'false'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_ENGINEERING_MODE` | `-` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_ESPEAK_PITCH` | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_ESPEAK_SPEED` | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_ESPEAK_VOLUME` | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_EXPERT_ACTIVE_DOMAINS` | `dynamic` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_ALLOW_WRITES` | `'0'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_ENABLED` | `'1'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_MODE` | `'shadow'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_FACE_ID_ENABLED` | `'false'` | no | NO | `services/zoe-data/routers/face_id.py` |
| `ZOE_FACE_ID_THRESHOLD` | `'0.45'` | no | NO | `services/zoe-data/routers/face_id.py` |
| `ZOE_FLUE_BRAIN_TIMEOUT_S` | `dynamic` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_BRAIN_URL` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_STREAM_ENABLED` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FRUSTRATION_MAX_SESSIONS` | `'2000'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_GITHUB_DEFAULT_BRANCH` | `'main'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_GITHUB_REPO` | `'jason-easyazz/zoe-ai-assistant'` | no | NO | `services/zoe-data/greploop_guard.py`<br>`services/zoe-data/greptile_client.py` |
| `ZOE_GRAPH_RECALL_BOOST` | `''` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_GRAPH_RECALL_WEIGHT` | `dynamic` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_HA_BRIDGE_URL` | `''`, `'http://127.0.0.1:8007'`, `'http://localhost:8007'` | no | NO | `modules/zoe-music/main.py`<br>`services/zoe-data/intent_router.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/ha_control.py`<br>`services/zoe-data/routers/stubs.py`<br>`services/zoe-data/routers/system.py`<br>`services/zoe-data/smart_home_service.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_HA_URL` | `dynamic` | no | NO | `services/zoe-data/routers/stubs.py` |
| `ZOE_HA_VOICE_INGRESS_URL` | `'http://host.docker.internal:8000'` | no | NO | `services/homeassistant-mcp-bridge/main.py` |
| `ZOE_HA_VOICE_TOKEN` | `''` | no | NO | `services/homeassistant-mcp-bridge/main.py` |
| `ZOE_HEALTH_CHECK_SCRIPT` | `dynamic` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_HEALTH_CHECK_TIMEOUT_S` | `'120'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_HERMES_AUTO_ESCALATE` | `'true'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_HOME_SETUP_SECRET` | `-` | no | NO | `services/zoe-data/smart_home_setup.py` |
| `ZOE_HOME_SETUP_TTL_S` | `'900'` | no | NO | `services/zoe-data/smart_home_setup.py` |
| `ZOE_HOST_LAN_IP` | `'192.168.1.218'`, `-` | yes | NO | `services/zoe-data/main.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_HYBRID_RETRIEVAL_ENABLED` | `''` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_IDLE_CONSOLIDATION_ENABLED` | `'0'` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN` | `''` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_INTERNAL_TOKEN` | `''`, `-` | yes | NO | `scripts/maintenance/check_emotional_thread.py`<br>`services/zoe-data/auth.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/music_setup.py`<br>`services/zoe-data/smart_home_setup.py`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_KANBAN_BOARD` | `'default'` | no | NO | `services/zoe-data/executors/kanban_adapter.py`<br>`services/zoe-data/worktree_bootstrap.py` |
| `ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET` | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_CONVERGE_NOOP_IMPLEMENT` | `'true'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_DB_PATH` | `''` | no | NO | `services/zoe-data/worktree_bootstrap.py` |
| `ZOE_KANBAN_DEAD_WORKER_GRACE_S` | `'180'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_ESCALATION_MAX_RUNTIME` | `'90m'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_FOCUSED_TEST_READ_BUDGET` | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_GREP_BUDGET` | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_READ_BUDGET` | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET` | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET` | `'12'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET` | `'6'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_INTENT_GAP_PRE_EDIT_EXPLORE_BUDGET` | `'6'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_INTENT_GAP_REPEAT_READ_BUDGET` | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_MAX_RUNTIME` | `'45m'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME` | `'6h'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_PROTOCOL_VIOLATION_LIMIT` | `'2'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_REAP_DEAD_WORKERS` | `'true'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE` | `'3'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_SKIP_SCOUT` | `''` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_TERMINAL_TOOL_GRACE` | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KOKORO_BACKEND` | `-` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_BRAIN_HEALTH_URL` | `'http://127.0.0.1:11434/health'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_BRAIN_POLL_S` | `'2'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_BRAIN_WAIT_S` | `'180'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_DIR` | `-` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CUDA_ATTEMPTS` | `'3'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CUDA_RETRY_DELAY_S` | `'6'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_MODEL` | `''`, `'/home/zoe/models/kokoro-v1.0.onnx'` | no | NO | `scripts/setup/kokoro_sidecar.py`<br>`services/zoe-data/tts_waterfall.py` |
| `ZOE_KOKORO_SIDECAR_URL` | `'http://127.0.0.1:10201'` | no | NO | `scripts/perf/measure_tts.py`<br>`services/zoe-data/main.py`<br>`services/zoe-data/tts_waterfall.py` |
| `ZOE_KOKORO_SPEED` | `'1.15'` | no | NO | `services/zoe-data/tts_waterfall.py` |
| `ZOE_KOKORO_VOICE` | `''`, `'af_sky'` | no | NO | `scripts/perf/measure_tts.py`<br>`services/zoe-data/voice_settings.py` |
| `ZOE_KOKORO_VOICES` | `''`, `'/home/zoe/models/voices-v1.0.bin'` | no | NO | `scripts/setup/kokoro_sidecar.py`<br>`services/zoe-data/tts_waterfall.py`<br>`services/zoe-data/voice_settings.py` |
| `ZOE_LATENCY_BASELINE` | `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_RESULTS` | `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_SAMPLES` | `'2'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_SESSION_ID` | `'zoe-latency-probe'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_TIMEOUT_S` | `'30'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_WARN_MS` | `'500'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_WARN_RATIO` | `'1.5'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LAYOUT_MEMORY` | `''` | no | NO | `services/zoe-data/ui_layouts.py` |
| `ZOE_LIVEKIT_BRAIN_TIMEOUT_S` | `'20'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_CONTAINER` | `'livekit'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_FAST_TIERS` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_IDLE_TIMEOUT_S` | `'300'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_ONDEMAND` | `'true'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVE_REPO_ROOT` | `'/home/zoe/assistant'` | no | NO | `scripts/maintenance/zoe_apply_intent_gap_contract.py` |
| `ZOE_LIVE_SERVICE_DIR` | `'/home/zoe/assistant/services/zoe-data'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_LK_COOLDOWN_TIMEOUT_S` | `'4.0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_ENERGY_THRESHOLD` | `'400'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_MIN_SPEECH_FRAMES` | `'5'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_SILENCE_FRAMES` | `'20'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_USE_AIORTC` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LLAMA_URL` | `'http://127.0.0.1:11434'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_LLM_MODEL` | `'gemma'` | no | NO | `services/zoe-data/memory_digest.py` |
| `ZOE_LOCAL_MODEL` | `'Gemma 4 E4B-QAT'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_LOCAL_TTS_URL` | `''` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_LOCATION_CITY` | `'Geraldton'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py`<br>`services/zoe-data/voice_stitch.py` |
| `ZOE_LOCATION_COUNTRY` | `dynamic` | no | NO | `services/zoe-data/routers/weather.py` |
| `ZOE_LOCATION_LAT` | `'-28.7774'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py` |
| `ZOE_LOCATION_LON` | `'114.6158'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py` |
| `ZOE_MAX_BROWSER_TABS` | `'5'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_MCP_ACTOR_ROLE` | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_ACTOR_USER_ID` | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_STRICT_USER_ID` | `'false'` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_USER_ID` | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_USER_ROLE` | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MEMORY_AUDIT_COLLECTION` | `'mempalace_audit'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_MEMORY_COMPOSE_ENABLED` | `''` | no | NO | `services/zoe-data/zoe_memory_compose.py` |
| `ZOE_MEMORY_LINK_RESOLVER_ENABLED` | `''` | no | NO | `services/zoe-data/memory_digest.py` |
| `ZOE_MEMORY_LINT_IN_DREAMING` | `''` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LINT_NEAR_DUP_RATIO` | `'0.92'` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LINT_STALE_DAYS` | `'365'` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LOOP_LOG_PATH` | `'~/.zoe/zoe-data-memory-loops.log'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_MEMORY_STARTUP_STRICT` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MERGE_QUEUE_ENABLED` | `''` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MERGE_QUEUE_LABEL` | `'auto-merge'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MERGE_QUEUE_MAX_CANDIDATES` | `'50'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MIRROR_INTENTS_TO_OPENCLAW` | `'false'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_MOONSHINE_ARCH` | `'MEDIUM_STREAMING'`, `'v2'` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_MULTICA` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES` | `-` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES_FOR` | `-` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPILOT_STALE_HOURS` | `-` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES` | `'false'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES_FOR` | `''` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_STALE_HOURS` | `'2'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTO_ADMIT` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_BLOCKED_RESUME_BUDGET` | `4` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_DISPATCH_PAUSE_FILE` | `''` | no | NO | `services/zoe-data/multica_dispatch_control.py` |
| `ZOE_MULTICA_NATIVE_COMMENT_GUARD` | `-` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `ZOE_MULTICA_PAUSED_POLL_S` | `300.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_POLL_DISPATCH_LIMIT` | `1` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_POLL_REF_TIMEOUT_S` | `20.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_STALE_IN_PROGRESS_HOURS` | `6.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_BIND_HOST` | `'0.0.0.0'` | no | NO | `modules/zoe-music/main.py` |
| `ZOE_MUSIC_DISCOVERY` | `'off'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_DOW` | `'sun'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_HOUR` | `'3'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_JSON` | `dynamic` | no | NO | `services/zoe-data/music_discovery.py` |
| `ZOE_MUSIC_HISTORY` | `'on'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_HISTORY_INTERVAL_S` | `'300'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_MODULE_URL` | `'http://zoe-music:8100'` | no | NO | `modules/zoe-music/intents/handlers.py` |
| `ZOE_MUSIC_OAUTH_TTL_S` | `'150'` | no | NO | `services/zoe-data/music_oauth.py` |
| `ZOE_MUSIC_OBSERVE_ATTRIB_MIN` | `'30'` | no | NO | `services/zoe-data/music_history.py` |
| `ZOE_MUSIC_OBSERVE_DEDUP_H` | `'12'` | no | NO | `services/zoe-data/music_history.py` |
| `ZOE_MUSIC_SERVICE_TOKEN` | `''` | no | NO | `modules/zoe-music/intents/handlers.py`<br>`modules/zoe-music/main.py` |
| `ZOE_MUSIC_SETUP_SECRET` | `-` | no | NO | `services/zoe-data/music_setup.py` |
| `ZOE_MUSIC_SETUP_TTL_S` | `'900'` | no | NO | `services/zoe-data/music_setup.py` |
| `ZOE_NVM_NODE_BIN` | `-` | no | NO | `services/zoe-data/pi_intent_classifier.py` |
| `ZOE_OPENCLAW_GW` | `'http://127.0.0.1:18789'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/chat.py` |
| `ZOE_OTEL_ENABLED` | `''` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_PANEL_AGENT_PORT` | `'8765'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PANEL_ALLOWED_HOSTS` | `''` | no | NO | `services/zoe-data/agent_safety.py` |
| `ZOE_PANEL_ID` | `'post-merge-probe'`, `'zoe-touch-pi'` | no | NO | `scripts/maintenance/zoe_latency_probe.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_PANEL_SESSION_TRUST_WINDOW_S` | `'900'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_PERF` | `-` | no | NO | `scripts/perf/measure_speed.py`<br>`scripts/perf/measure_tts.py`<br>`scripts/perf/measure_voice.py` |
| `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` | `''` | no | NO | `services/zoe-data/person_extractor.py` |
| `ZOE_PERSON_DOSSIER_ENABLED` | `''` | no | NO | `services/zoe-data/zoe_memory_compose.py` |
| `ZOE_PERSON_LLM_CONFIDENCE_GATE` | `''` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_LLM_CONFIDENCE_MIN` | `'0.4'` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_LLM_PREFILTER` | `''` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_MERGE_ENABLED` | `''` | no | NO | `services/zoe-data/person_merge.py` |
| `ZOE_PERSON_SUGGEST_ENABLED` | `''` | no | NO | `services/zoe-data/pending_suggestions.py` |
| `ZOE_PIN_CHALLENGE_TTL_S` | `'120'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIN_LOCKOUT_S` | `'300'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIN_MAX_ATTEMPTS` | `'5'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIPELINE_HARNESS_CLOSEOUT_MERGE` | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_HARNESS_REVIEW_APPROVE` | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_HARNESS_VERIFY_TESTS` | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_STORE_PATH` | `''` | no | NO | `scripts/maintenance/compact_pipeline_state.py`<br>`scripts/maintenance/engineering_harness_loop.py`<br>`services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT` | `'1'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PI_EXECUTOR_COMMAND` | `-` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_MODE` | `-` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_MODEL` | `-` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_PROVIDER` | `-` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_TIMEOUT_S` | `'900'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_HOST` | `'192.168.1.61'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH` | `-` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH` | `-` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PI_INTENT_MODEL` | `-` | no | NO | `scripts/maintenance/pi_intent_probe.py` |
| `ZOE_PI_INTENT_PREFILTER_ENABLED` | `'true'` | no | NO | `scripts/maintenance/pi_promotion_eval.py` |
| `ZOE_PI_INTENT_PROMOTED_GROUPS` | `-` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_PI_INTENT_TIMEOUT_SECONDS` | `-` | no | NO | `scripts/maintenance/pi_promotion_eval.py` |
| `ZOE_PRESENCE_WINDOW_S` | `''` | no | NO | `services/zoe-data/proactive/presence.py` |
| `ZOE_PROACTIVE_SLOW_LOOP_S` | `'300'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROACTIVE_SPOKEN` | `''` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROACTIVE_SPOKEN_TRIGGERS` | `'morning_checkin'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROVISION_CODE_TTL_S` | `'300'` | no | NO | `services/zoe-data/routers/panel_provision.py` |
| `ZOE_PR_GUARD_ACTIVE_GREPTILE_STALE_SECONDS` | `dynamic` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_GREPTILE_WAIT_POLL_SECONDS` | `'120'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_GREPTILE_WAIT_TIMEOUT_SECONDS` | `'1800'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_HEAD_STABILITY_SECONDS` | `'90'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_ACTIVE_GREPTILE_REVIEWS` | `'1'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_COST_USD` | `'0.25'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_ITERATIONS` | `'5'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_OUTPUT_CHARS` | `'12000'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_NO_FINDINGS_RETRIGGER_LIMIT` | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_NO_PROGRESS_LIMIT` | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_SAME_ERROR_LIMIT` | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_STATE_DIR` | `'/home/zoe/assistant/.cursor/tmp/pr_guard'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_TRIGGER_COOLDOWN_SECONDS` | `'900'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PUBLIC_URL` | `''` | no | NO | `services/zoe-data/routers/music_setup.py`<br>`services/zoe-data/routers/smart_home_setup.py` |
| `ZOE_QUIET_END_HOUR` | `'7'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_QUIET_START_HOUR` | `'22'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_RELATIONSHIP_GRAPH_ENABLED` | `''` | no | NO | `services/zoe-data/relationship_graph.py` |
| `ZOE_REMINDER_MAX_ATTEMPTS` | `'5'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_REMINDER_STUCK_CLAIM_S` | `'600'` | no | NO | `services/zoe-data/proactive/triggers/reminders.py` |
| `ZOE_REPO_ROOT` | `''` | no | NO | `services/zoe-data/hermes_http.py` |
| `ZOE_RIG_BIND` | `-` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_DISPLAY` | `':99'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_GEOMETRY` | `'1280x800x24'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_LOGIN_URL` | `'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmusic.youtube.com%2F'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_NOVNC_PORT` | `'6080'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_USER_AGENT` | `-` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_VNC_PORT` | `'5900'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_ROUTER_ARCHIVE_KEEP` | `'3'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_BASE_HF` | `'/home/zoe/models/lab/functiongemma-270m-it-hf'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_ENABLED` | `'1'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD` | `'off'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD_LOG` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD_MLP_PATH` | `dynamic` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_HEAD_PATH` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD_THRESHOLD` | `'0.4'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MARGIN` | `'0.05'` | no | NO | `services/zoe-data/fast_tiers.py` |
| `ZOE_ROUTER_MODE` | `'shadow'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MODEL` | `'BAAI/bge-small-en-v1.5'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MODEL_DIR` | `'/home/zoe/models/functiongemma-router'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_SCRATCH_PORT` | `'11437'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_SELFTRAIN` | `'off'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_DOW` | `'sat'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_HOUR` | `'1'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_TIMEOUT_S` | `'28800'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SHADOW_KEEP` | `4` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SHADOW_MAX_BYTES` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SHADOW_TEXT` | `''` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SIDECAR_URL` | `-` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_THRESHOLD` | `'0.62'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_TWO_STAGE_GATE` | `'0.5'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_TWO_STAGE_TIMEOUT_S` | `'1.5'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_WARM_START` | `dynamic` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_SEARCH_HOTNESS_WEIGHT` | `'0.05'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_SESSION_LOCK_TIMEOUT_S` | `'5'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_SILERO_VAD_MODEL` | `''` | no | NO | `services/zoe-data/voice_vad.py` |
| `ZOE_SKYBRIDGE_ONLY` | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_SKYBRIDGE_TIMEZONE` | `-` | no | NO | `services/zoe-data/skybridge_service.py` |
| `ZOE_SMART_TURN_ENABLED` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SMART_TURN_MAX_CHECKS` | `'2'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SMART_TURN_THREADS` | `'1'` | no | NO | `services/zoe-data/voice_turn.py` |
| `ZOE_SMART_TURN_THRESHOLD` | `'0.5'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SPEAKER_ID_THRESHOLD` | `'0.82'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_STT_BACKEND` | `'moonshine'` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_STT_PREWARM_ON_WAKE` | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_TASK_TIMEOUT_S` | `'900'` | no | NO | `services/zoe-data/background_runner.py` |
| `ZOE_TELEGRAM_BOT_USERNAME` | `''` | no | NO | `services/zoe-data/telegram_link.py` |
| `ZOE_TELEGRAM_LINK_SECRET` | `-` | no | NO | `services/zoe-data/telegram_link.py` |
| `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED` | `''` | no | NO | `services/zoe-data/person_extractor.py` |
| `ZOE_TIMEZONE` | `'Australia/Perth'`, `-` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/memory_digest.py`<br>`services/zoe-data/multica_autopilot_sync.py`<br>`services/zoe-data/proactive/engine.py`<br>`services/zoe-data/proactive/triggers/emotional_followup.py`<br>`services/zoe-data/proactive/triggers/evening_windown.py`<br>`services/zoe-data/proactive/triggers/evolution_weekly_digest.py`<br>`services/zoe-data/proactive/triggers/morning_checkin.py`<br>`services/zoe-data/proactive/triggers/people_birthday.py`<br>`services/zoe-data/proactive/triggers/people_health.py`<br>`services/zoe-data/proactive/triggers/reminder_scan.py`<br>`services/zoe-data/routers/weather.py`<br>`services/zoe-data/time_utils.py`<br>`services/zoe-data/voice_greeting.py` |
| `ZOE_TOUCH_PROBE_DEVICE_TOKEN` | `''` | no | NO | `scripts/maintenance/pi_touch_hybrid_production_probe.py` |
| `ZOE_TOUCH_PROBE_PANEL_ID` | `'zoe-touch-pi'` | no | NO | `scripts/maintenance/pi_touch_hybrid_production_probe.py` |
| `ZOE_TTS_MODE` | `'hybrid'` | yes | NO | `services/zoe-data/main.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `ZOE_TTS_TRIM_SILENCE` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_UNAUTHENTICATED_ROLE` | `'guest'` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_UPDATE_CHECK_ENABLED` | `'true'` | no | NO | `services/zoe-data/system_updates.py` |
| `ZOE_URL` | `'http://127.0.0.1:8000'`, `'https://zoe.local'` | no | NO | `scripts/maintenance/zoe_latency_probe.py`<br>`scripts/setup/zoe_face_id.py`<br>`scripts/setup/zoe_voice_daemon.py` |
| `ZOE_USE_CORE_BRAIN` | `'true'` | no | NO | `services/zoe-data/brain_dispatch.py` |
| `ZOE_USE_PI_EXECUTOR` | `'false'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_VAD_SPEECH_THRESHOLD` | `'0.5'` | no | NO | `services/zoe-data/voice_vad.py` |
| `ZOE_VOICE_BARGE_IN` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_VOICE_BASELINE` | `dynamic` | no | NO | `scripts/maintenance/voice_gate_check.py`<br>`scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_CHAT_TIMEOUT_S` | `'20'` | no | NO | `services/zoe-data/routers/voice_tts.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_VOICE_FAST_FIRST_AUDIO` | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_AFTER_S` | `'1.6'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_ENABLED` | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_PHRASES` | `'Let me check.|One sec.|Hmm, let me look.'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_GATE_MAX_AGE_H` | `'24'` | no | NO | `scripts/maintenance/voice_gate_check.py` |
| `ZOE_VOICE_GATE_PATHS` | `''` | no | NO | `scripts/maintenance/voice_gate_check.py` |
| `ZOE_VOICE_GREETING_ENABLED` | `'0'` | no | NO | `services/zoe-data/voice_greeting.py` |
| `ZOE_VOICE_GREETING_STATE_PATH` | `dynamic` | no | NO | `services/zoe-data/voice_greeting.py` |
| `ZOE_VOICE_HERMES_TIMEOUT_S` | `'45'`, `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_IDENT` | `''` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_LOG` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VOICE_PROBE_MIN_MEM_MB` | `'1500'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_SAMPLES` | `'20'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_TIMEOUT_S` | `'900'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_USER` | `'jason'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROFILE` | `'zoe_au_natural_v1'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_RESULTS` | `dynamic` | no | NO | `scripts/maintenance/voice_gate_check.py`<br>`scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_SAMPLE_DIR` | `-` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_SAVE_AUDIO` | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_STITCH_ENABLED` | `'0'` | no | NO | `services/zoe-data/main.py`<br>`services/zoe-data/voice_stitch.py` |
| `ZOE_VOICE_STREAM` | `'1'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VOICE_STT_LOG` | `-` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_TOOL_FILLER` | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_TREND` | `dynamic` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_WARN_MS` | `'1500'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_WARN_RATIO` | `'1.5'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_WAKE_ACK_PHRASE` | `-` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_WEATHER_CACHE_TTL_S` | `'600'` | no | NO | `services/zoe-data/routers/weather.py` |
| `ZOE_WORKTREE_PRUNE_INTERVAL_S` | `86400.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_WORKTREE_ROOT` | `''` | no | NO | `services/zoe-data/worktree_bootstrap.py` |
| `ZOE_WS_IDLE_TIMEOUT_SECONDS` | `120.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_POLL_S` | `'2.0'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_YTMUSIC_POTOKEN_URL` | `'http://localhost:4416'` | no | NO | `services/zoe-data/music_service.py` |
| `ZOE_YTMUSIC_REFRESH_ENABLED` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_REFRESH_HOURS` | `'12'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_SECRET_DIR` | `dynamic` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_YTMUSIC_SESSION_TIMEOUT_S` | `'300'` | no | NO | `services/zoe-data/ytmusic_signin.py` |

## Lab flags (`labs/` — not prod)

6 flags; 6 not documented in `.env.example`.

| Flag | Default(s) | typed_env | .env.example | Readers |
|---|---|---|---|---|
| `ZOE_KOKORO_MODEL` | `'/home/zoe/models/kokoro-v1.0.onnx'` | no | NO | `labs/kokoro-voice-blend/blend_zoe_voices.py` |
| `ZOE_KOKORO_VOICES` | `'/home/zoe/models/voices-v1.0.bin'` | no | NO | `labs/kokoro-voice-blend/blend_zoe_voices.py` |
| `ZOE_LIVE_ROOT` | `-` | no | NO | `labs/flue-zoe-brain/parity/gatelib.py` |
| `ZOE_PGU_SID` | `-` | no | NO | `labs/flue-zoe-brain/parity/tool_breadth_gate.py` |
| `ZOE_ROUTER_HEAD_LOG` | `dynamic` | no | NO | `labs/router-selftrain/mine_candidates.py` |
| `ZOE_ROUTER_SIDECAR_PORT` | `'11436'` | no | NO | `labs/router-90-campaign/prod_path_eval.py` |

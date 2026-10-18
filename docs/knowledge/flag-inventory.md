---
type: Reference
title: ZOE_* flag inventory (GENERATED)
description: Generated inventory of every ZOE_* environment flag — the declared spec from the flag registry (type, default, allowed values, owner doc) joined with what the scanner finds (readers, the defaults they hard-code, typed_env adoption, .env.example coverage), plus the conflicting-default check and the legacy direct-read ratchet.
tags: [flags, env, configuration, generated]
timestamp: 2026-10-18T00:00:00Z
---

# ZOE_* flag inventory

**STATUS: GENERATED — do not hand-edit.** Regenerate with:

```
python3 tools/audit/flag_inventory.py
```

Last generated: 2026-10-18. The table body is deterministic (sorted, no
timestamps) so regeneration diffs show real flag changes only.

Declared columns (type, default, allowed, owner doc) come from the flag registry,
`services/zoe-data/flag_registry.py` — each flag declares itself there once; add a new
flag with `python3 tools/audit/flag_inventory.py --sync-registry`, then correct its spec
by hand. Read facts (read defaults, readers) come from the scanner.
Read default `dynamic` = not statically extractable; `(required)` = bare
`os.environ[...]` subscript (raises if unset); `-` = no default argument.

## Summary

- 705 prod flags; 160 read through `typed_env`.
- 697 legacy direct read sites in prod code across 203 files (ceiling in `docs/knowledge/flag-ratchet.json`; may only go down).
- 14 flags whose readers hard-code different effective defaults (table below).
- 245 non-`ZOE_` env names read by prod code (table below).
- 0 flags read but not registered (CI fails on any).
- 281 prod flags with no owner doc yet.
- 0 registered flags with no reader in the scanned sources.

## Conflicting read defaults

Same flag, readers hard-coding different EFFECTIVE defaults (`'1'` ≡ `True`; `''` ≡ unset). Each must equal the registry default, or the flag carries `divergent_ok` with a reason.

| Flag | Registry default | Effective read defaults | Divergence acknowledged |
|---|---|---|---|
| `ZOE_BASE_URL` | `'https://192.168.1.218'` | `'http://localhost:8000'`, `'http://zoe.local'`, `'https://192.168.1.218'` | `services/zoe-auth/oidc/startup.py`, `services/zoe-data/routers/system.py` |
| `ZOE_CHEAP_PR_AGENT_MODEL` | `'deepseek/deepseek-chat-v3.1'` | `'cheap-pr-agent'`, `'deepseek/deepseek-chat-v3.1'` | `services/zoe-data/greploop_guard.py` |
| `ZOE_DATA_URL` | `'http://127.0.0.1:8000'` | `'http://127.0.0.1:8000'`, `'http://127.0.0.1:8011'` | `services/zoe-data/zoe_core_client.py` |
| `ZOE_DIGEST_CHUNKED` | `True` | `True`, `unset` | `services/zoe-data/digest_pack.py` |
| `ZOE_ENV_FILE` | `'/home/zoe/assistant/services/zoe-data/.env'` | `'/home/zoe/assistant/services/zoe-data/.env'`, `unset` | `scripts/maintenance/retire_hermes_openclaw_board.py`, `scripts/maintenance/verify_executor_queue_backend.py` |
| `ZOE_HA_BRIDGE_URL` | `'http://127.0.0.1:8007'` | `'http://127.0.0.1:8007'`, `unset` | `services/zoe-data/routers/stubs.py` |
| `ZOE_HOST_LAN_IP` | `''` | `'192.168.1.218'`, `unset` | `services/zoe-data/main.py`, `services/zoe-data/zoe_agent.py` |
| `ZOE_LOCATION_CITY` | `'Geraldton'` | `'Geraldton'`, `unset` | `services/zoe-data/identity_facts.py` |
| `ZOE_LOCATION_COUNTRY` | `'AU'` | `'AU'`, `unset` | `services/zoe-data/distress_handoff.py`, `services/zoe-data/identity_facts.py` |
| `ZOE_MOONSHINE_ARCH` | `'MEDIUM_STREAMING'` | `'MEDIUM_STREAMING'`, `'v2'` | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_PANEL_ID` | `'zoe-touch-pi'` | `'post-merge-probe'`, `'zoe-touch-pi'` | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_SPEAKER_MODEL` | `'resemblyzer'` | `'resemblyzer'`, `unset` | `scripts/setup/zoe_enroll_flow.py`, `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_TIMEZONE` | `'Australia/Perth'` | `'Australia/Perth'`, `unset` | `services/zoe-data/identity_facts.py` |
| `ZOE_URL` | `'https://zoe.local'` | `'http://127.0.0.1:8000'`, `'https://zoe.local'` | `scripts/maintenance/zoe_latency_probe.py` |

## Production flags

705 flags; 655 not documented in `.env.example`.

| Flag | Type | Default | Allowed | Owner doc | Read default(s) | typed_env | .env.example | Readers |
|---|---|---|---|---|---|---|---|---|
| `ZOE_A2A_CLIENT_TIMEOUT_S` | int | `30` | - | - | `'30'` | no | NO | `services/zoe-data/a2a_client.py` |
| `ZOE_A2A_TOKEN` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_ACP_DELIVERY_MODE` | str | `'live'` | - | - | `'live'` | no | NO | `services/zoe-data/zoe_acp_client.py` |
| `ZOE_AFFECT_CONSENT_GATE` | str | `''` | - | `docs/knowledge/memory-authority.md` | `-` | no | NO | `services/zoe-data/memory_authority.py` |
| `ZOE_AGENT_LLM_TIMEOUT` | float | `120.0` | - | - | `'120.0'`, `dynamic` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_MAX_TOOL_ITERS` | int | `5` | - | - | `5` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_TOOL_TIMEOUT` | float | `10.0` | - | - | `'10.0'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_AGENT_VOICE_LLM_TIMEOUT` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_ALLOWED_WS_ORIGINS` | list | `()` | - | `docs/knowledge/incident-runbook.md` | `-` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_ANNOUNCE_POLL_ENABLED` | bool | `True` | - | `scripts/AGENTS.md` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ANNOUNCE_POLL_S` | float | `5.0` | - | `docs/knowledge/mac-virtual-panel.md` | `'5.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ANNOUNCE_STRICT_PANEL` | str | `''` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `''` | no | yes | `services/zoe-data/voice_announce.py` |
| `ZOE_ANNOUNCE_TTL_S` | str | `''` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | yes | `services/zoe-data/voice_announce.py` |
| `ZOE_APP_LOG` | str | `''` | - | - | `dynamic` | no | NO | `scripts/perf/user_model_ab.py` |
| `ZOE_ASK_TO_REMEMBER` | bool | `True` | - | `docs/knowledge/samantha-bar.md` | `True` | yes | NO | `services/zoe-data/ask_to_remember.py` |
| `ZOE_ASK_WHEN_AMBIGUOUS` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/person-half-enforce-pack-2026-10-09.md` | `-` | yes | NO | `services/zoe-data/ask_when_ambiguous.py` |
| `ZOE_ASSISTANT_ROOT` | str | `'/home/zoe/assistant'` | - | - | `'/home/zoe/assistant'`, `dynamic` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py`<br>`services/zoe-data/greploop_guard.py`<br>`services/zoe-data/multica_ticket_contract.py` |
| `ZOE_AUTH_ALLOWED_ORIGINS` | str | `'http://localhost,http://localhost:3000,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,https://zoe.the411.life,http://zoe.local'` | - | - | `'http://localhost,http://localhost:3000,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,https://zoe.the411.life,http://zoe.local'` | no | NO | `services/zoe-auth/main.py` |
| `ZOE_AUTH_FAIL_CLOSED` | bool | `True` | - | `services/zoe-data/routers/AGENTS.md` | `'true'` | no | NO | `services/zoe-data/auth.py` |
| `ZOE_AUTH_SETUP_TOKEN` | str | `''` | - | `services/zoe-auth/AGENTS.md` | `''` | no | NO | `services/zoe-auth/core/account_setup.py` |
| `ZOE_AUTH_URL` | str | `'http://localhost:8002'` | - | `docs/reviews/issue-register-2026-07-07.md` | `'http://localhost:8002'` | no | yes | `services/zoe-auth/touch_panel/quick_auth.py`<br>`services/zoe-data/auth.py`<br>`services/zoe-data/main.py`<br>`services/zoe-data/routers/auth.py`<br>`services/zoe-data/routers/panel_auth.py` |
| `ZOE_AUTOPILOT_QUEUE_WAIT_S` | float | `1200.0` | - | - | `1200.0` | yes | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_AUTORESEARCH_RUN_ROOT` | str | `'data/autoresearch'` | - | - | `'data/autoresearch'` | no | NO | `services/zoe-data/routers/autoresearch.py` |
| `ZOE_AUTO_APPROVE_THRESHOLD` | bool | `False` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `'0'` | no | NO | `services/zoe-data/evolution_notice.py` |
| `ZOE_BACKUP_DIR` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/reset_engineering_boards.py` |
| `ZOE_BARGE_MIN_MS` | int | `192` | - | `docs/knowledge/voice-pipeline.md` | `'192'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_BARGE_SPEECH_THRESHOLD` | float | `0.3` | - | `docs/knowledge/voice-pipeline.md` | `'0.30'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_BAR_ADMIN_SESSION` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `''` | no | NO | `scripts/perf/recall_evidence_probe.py`<br>`scripts/perf/samantha_bar.py`<br>`scripts/perf/samantha_day_sim.py`<br>`scripts/perf/samantha_person.py`<br>`scripts/perf/user_model_ab.py` |
| `ZOE_BAR_BRAIN_URL` | str | `'http://127.0.0.1:11434'` | - | `docs/knowledge/samantha-person.md` | `'http://127.0.0.1:11434'` | no | NO | `scripts/perf/samantha_bar.py` |
| `ZOE_BASE_URL` | str | `'https://192.168.1.218'` | - | `docs/knowledge/incident-runbook.md` | `'http://localhost:8000'`, `'http://zoe.local'`, `'https://192.168.1.218'` | no | NO | `services/zoe-auth/oidc/startup.py`<br>`services/zoe-data/routers/panel_provision.py`<br>`services/zoe-data/routers/system.py` |
| `ZOE_BENCHMARK_OUTPUT` | str | `''` | - | - | `dynamic` | no | NO | `scripts/utilities/gemma4_trial_benchmark.py` |
| `ZOE_BENCH_REPEATS` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-core/bench/pi_brain_latency.py` |
| `ZOE_BLE_RADIO_LOCK` | str | `''` | - | `docs/knowledge/bluetooth-settings.md` | `dynamic` | no | NO | `services/omi-sync/omi_sync/radio_lock.py` |
| `ZOE_BLUETOOTH_AUDIT_PATH` | str | `''` | - | `docs/knowledge/bluetooth-settings.md` | `''` | yes | NO | `services/zoe-data/bluetooth_broker.py` |
| `ZOE_BLUETOOTH_SETTINGS` | bool | `False` | - | `docs/knowledge/bluetooth-settings.md` | `False` | yes | NO | `services/zoe-data/bluetooth_broker.py` |
| `ZOE_BOARD_POLL_S` | int | `60` | - | - | `'60'` | no | NO | `services/zoe-data/multica_board_runner.py` |
| `ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED` | bool | `False` | - | `docs/guides/MULTICA_HERMES_PR_LOOP.md` | `'false'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_BRAIN_BACKEND` | str | `'core'` | - | `docs/knowledge/memory-pressure-profile.md` | `'core'` | no | yes | `services/zoe-data/brain_dispatch.py` |
| `ZOE_BRAIN_FAILOVER` | bool | `False` | - | `docs/knowledge/state-of-zoe-review-2026-09-25.md` | `'0'` | no | yes | `services/zoe-data/brain_dispatch.py` |
| `ZOE_BRAIN_FAILOVER_COOLDOWN_S` | int | `45` | - | `services/zoe-data/AGENTS.md` | `'45'` | no | yes | `services/zoe-data/brain_dispatch.py` |
| `ZOE_BRAIN_KV_WARM_ON_WAKE` | bool | `True` | - | `docs/knowledge/brain-prefix-cache-2026-10-12.md` | `'1'` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_BRAIN_PREWARM_ON_WAKE` | bool | `True` | - | - | `'1'`, `True` | yes | NO | `services/zoe-data/routers/voice_livekit.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `ZOE_BRAIN_SLOT_GUARD` | bool | `True` | - | `docs/knowledge/brain-prefix-cache-2026-10-12.md` | `'on'` | no | NO | `services/zoe-data/brain_slot_guard.py` |
| `ZOE_BRAIN_SLOT_MAX_DEFER_S` | str | `''` | - | `docs/knowledge/brain-prefix-cache-2026-10-12.md` | `''` | no | NO | `services/zoe-data/brain_slot_guard.py` |
| `ZOE_BRAIN_SLOT_QUIET_S` | str | `''` | - | `docs/knowledge/brain-prefix-cache-2026-10-12.md` | `''` | no | NO | `services/zoe-data/brain_slot_guard.py` |
| `ZOE_BRAIN_SLOT_TOKENS` | int | `8192` | - | `docs/knowledge/brain-flags-tuning-2026-09.md` | `'8192'`, `8192` | yes | NO | `services/zoe-data/digest_pack.py`<br>`services/zoe-data/memory_digest.py`<br>`services/zoe-data/night_mind.py`<br>`services/zoe-data/omi_day_review.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_BRAIN_STARTUP_WAIT_S` | float | `30.0` | - | - | `30.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_BRAIN_TOKEN` | str | `''` | - | `docs/knowledge/self-model.md` | `-` | no | NO | `scripts/perf/hop_placement_ab.py`<br>`scripts/perf/measure_tts_cadence.py`<br>`scripts/perf/person_half_enforce_ab.py`<br>`scripts/perf/user_model_ab.py`<br>`services/zoe-data/zoe_flue_client.py` |
| `ZOE_BRAIN_UNIT` | str | `'llama-server.service'` | - | - | `'llama-server.service'` | no | NO | `scripts/maintenance/router_selftrain.py`<br>`services/zoe-data/main.py` |
| `ZOE_BRAIN_URL` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_BRIEF_ON_FIRST_TURN` | str | `''` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | NO | `services/zoe-data/brief_first_turn.py` |
| `ZOE_BRIEF_WINDOW_END` | str | `'12:00'` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `'12:00'` | no | NO | `services/zoe-data/brief_first_turn.py` |
| `ZOE_BRIEF_WINDOW_START` | str | `'05:00'` | - | `docs/knowledge/samantha-bar.md` | `'05:00'` | no | NO | `services/zoe-data/brief_first_turn.py` |
| `ZOE_BUFFER_DELAY_S` | float | `0.8` | - | - | `'0.8'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_BUFFER_PHRASES` | bool | `True` | - | - | `'1'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_CALENDAR_FOLLOWUP_TTL_S` | int | `90` | - | `docs/knowledge/voice-verified-session.md` | `'90'` | no | NO | `services/zoe-data/conversation_cues.py` |
| `ZOE_CALENDAR_HEALTH_PRIVATE` | bool | `True` | - | `docs/research/samantha-flags-ab-2026-10-06.md` | `True` | yes | NO | `services/zoe-data/calendar_service.py` |
| `ZOE_CAP_A2A_DELEGATE` | int | `3000` | - | - | `3000` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_ROWS` | int | `10` | - | - | `10` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_SEARCH` | int | `0` | - | - | `0` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_AMBIENT_TRANSCRIPT` | int | `150` | - | - | `150` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_MEMORY_LIST` | int | `0` | - | - | `0` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_MEMORY_LIST_ROWS` | int | `25` | - | - | `25` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_SELF_CAPS` | int | `2000` | - | - | `2000` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_WEB_BROWSE` | int | `6000` | - | - | `6000` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CAP_WEB_RESEARCH` | int | `6000` | - | - | `6000` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CHAT_CONTEXT_MAX_SESSIONS` | int | `2000` | - | - | `'2000'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_CHAT_INJECT_DB_MEMORY` | bool | `False` | - | - | `'0'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_CHAT_URL` | str | `'http://localhost:8000'` | - | `docs/guides/OPERATOR_RUNBOOK.md` | `'http://localhost:8000'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CHEAP_PR_AGENT_API_URL` | str | `'https://openrouter.ai/api/v1/chat/completions'` | - | - | `'https://openrouter.ai/api/v1/chat/completions'` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py` |
| `ZOE_CHEAP_PR_AGENT_CMD` | str | `''` | - | `docs/architecture/zoe-flue-integration.md` | `-` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD` | bool | `False` | - | - | `'0'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_MODEL` | str | `'deepseek/deepseek-chat-v3.1'` | - | - | `'cheap-pr-agent'`, `'deepseek/deepseek-chat-v3.1'` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py`<br>`services/zoe-data/greploop_guard.py` |
| `ZOE_CHEAP_PR_AGENT_URL` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_CLEAN_GOODBYE` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/person-half-enforce-pack-2026-10-09.md` | `-` | yes | NO | `services/zoe-data/clean_goodbye.py` |
| `ZOE_COMMITMENTS` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/samantha-bar.md` | `'shadow'` | yes | NO | `services/zoe-data/commitments.py` |
| `ZOE_COMPOSE_MAX_TOKENS` | int | `700` | - | - | `'700'` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_STREAM_BUDGET_S` | int | `6` | - | - | `'6'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_COMPOSE_TIMEOUT_S` | int | `14` | - | - | `'14'` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_UI` | str | `''` | - | `docs/knowledge/state-of-zoe-review-2026-09-25.md` | `''` | no | NO | `services/zoe-data/ui_compose.py` |
| `ZOE_COMPOSE_VOICE_BUDGET_S` | int | `8` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'8'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_CONTACTS_API` | str | `''` | - | `docs/architecture/contacts-data-model.md` | `''` | no | NO | `services/zoe-data/routers/contacts.py` |
| `ZOE_CONTACTS_CONVERSATIONAL` | str | `''` | - | `docs/knowledge/contacts-conversation.md` | `''` | no | NO | `services/zoe-data/contacts_conversation.py` |
| `ZOE_CONTACT_BACKFILL_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/contact_backfill.py` |
| `ZOE_CONTACT_OFFER_BATCH` | str | `''` | - | `docs/knowledge/contacts-conversation.md` | `''` | no | NO | `services/zoe-data/contacts_conversation.py` |
| `ZOE_CONTEXT_TOKEN_BUDGET` | int | `5500` | - | - | `5500` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_CONTINUE_WINDOW_S` | float | `2.0` | - | `docs/knowledge/voice-pipeline.md` | `2.0` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_CONVERSATION_ENDER_ACKS` | str | `"Okay — I'm here when you need me.\|No worries. Talk soon.\|Okay."` | - | - | `"Okay — I'm here when you need me.\|No worries. Talk soon.\|Okay."` | no | NO | `services/zoe-data/conversation_opener.py` |
| `ZOE_CONVERSATION_FEEDBACK` | bool | `True` | - | `docs/knowledge/voice-lane-parity.md` | `True` | yes | NO | `services/zoe-data/conversation_feedback.py` |
| `ZOE_CORE_ALLOW_WRITES` | bool | `False` | - | - | `'false'` | no | NO | `services/zoe-core/extensions/abilities.ts` |
| `ZOE_CORE_DATA_URL` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_DELEGATE_TIMEOUT_MS` | int | `120000` | - | - | `120000` | no | NO | `services/zoe-core/abilities/delegate.ts` |
| `ZOE_CORE_DISCLOSURE_WINDOW_TURNS` | str | `''` | - | `services/AGENTS.md` | `-` | no | NO | `services/zoe-core/extensions/abilities.ts` |
| `ZOE_CORE_DISPATCH_TIMEOUT_MS` | int | `8000` | - | - | `8000` | no | NO | `services/zoe-core/abilities/_dispatch.ts` |
| `ZOE_CORE_IDLE_TIMEOUT_S` | int | `20` | - | - | `'20'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MAX_CONCURRENCY` | int | `2` | - | - | `'2'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MAX_WORKERS` | int | `4` | - | `docs/architecture/brain-tool-selection-reliability.md` | `'4'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MEMORY_TIMEOUT_MS` | int | `2000` | - | - | `'2000'`, `2000` | no | NO | `services/zoe-core/extensions/memory.ts`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MODEL_API_KEY` | str | `'local'` | - | - | `'local'` | no | NO | `services/zoe-core/extensions/provider-local-gemma.ts` |
| `ZOE_CORE_MODEL_CONTEXT` | str | `''` | - | - | `-` | no | NO | `services/zoe-core/extensions/provider-local-gemma.ts` |
| `ZOE_CORE_MODEL_ID` | str | `'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf'` | - | `services/zoe-data/tests/AGENTS.md` | `'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf'` | no | NO | `services/zoe-core/bench/pi_brain_latency.py`<br>`services/zoe-core/extensions/provider-local-gemma.ts`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_MODEL_MAXTOKENS` | str | `''` | - | - | `-` | no | NO | `services/zoe-core/extensions/provider-local-gemma.ts` |
| `ZOE_CORE_MODEL_URL` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-core/bench/pi_brain_latency.py`<br>`services/zoe-core/extensions/provider-local-gemma.ts` |
| `ZOE_CORE_PI_COMMAND` | str | `'pi'` | - | - | `'pi'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_PROVIDER` | str | `'local-gemma'` | - | - | `'local-gemma'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_SOUL_PATH` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-core/extensions/soul.ts` |
| `ZOE_CORE_TIMEOUT_S` | int | `180` | - | - | `'180'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORE_USER_ID` | str | `''` | - | `docs/strategy/zoe-samantha-harness-plan.md` | `''` | no | NO | `services/zoe-core/extensions/abilities.ts`<br>`services/zoe-core/extensions/memory.ts` |
| `ZOE_CORE_VOICE_MODEL_MAXTOKENS` | int | `512` | - | - | `'512'` | no | NO | `services/zoe-data/zoe_core_client.py` |
| `ZOE_CORRECTION_APPLY` | bool | `False` | - | `docs/knowledge/samantha-bar.md` | `False` | yes | NO | `services/zoe-data/correction_apply.py` |
| `ZOE_DAILY_BRIEFING_CACHE_MAX_USERS` | int | `64` | - | - | `'64'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DAILY_BRIEFING_CACHE_TTL_SECONDS` | int | `120` | - | - | `'120'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DATA_DB` | str | `''` | - | `docs/knowledge/python-312-venv-migration.md` | `dynamic` | no | yes | `services/zoe-data/database.py` |
| `ZOE_DATA_URL` | str | `'http://127.0.0.1:8000'` | - | `docs/research/telegram-calls-voice-photos-2026-10-04.md` | `'http://127.0.0.1:8000'`, `'http://127.0.0.1:8011'` | no | NO | `scripts/maintenance/check_emotional_thread.py`<br>`scripts/maintenance/zoe-nightly-dreaming.py`<br>`scripts/perf/samantha_bar.py`<br>`services/zoe-core/extensions/abilities.ts`<br>`services/zoe-core/extensions/memory.ts`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_DATE_HINT` | bool | `True` | - | `docs/knowledge/contacts-people-memory.md` | `True` | yes | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_DATE_ORDER` | str | `''` | - | `docs/knowledge/contacts-people-memory.md` | `-` | no | NO | `services/zoe-data/date_locale.py` |
| `ZOE_DB_ACQUIRE_TIMEOUT_S` | str | `''` | - | `services/zoe-data/AGENTS.md` | `''` | no | NO | `services/zoe-data/db_pool.py` |
| `ZOE_DB_CONTAINER` | str | `'zoe-database'` | - | - | `'zoe-database'` | no | NO | `scripts/maintenance/reset_engineering_boards.py` |
| `ZOE_DEFAULT_MEDIA_PLAYER` | str | `'media_player.all'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'media_player.all'` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_DELIVERY_LEDGER` | str | `'shadow'` | `off`, `shadow`, `on` | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | yes | NO | `services/zoe-data/proactive/lines.py` |
| `ZOE_DEVICE_TOKEN` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_DEVICE_TOKEN_BINDS_MEMBER` | str | `''` | - | `docs/architecture/panel-anywhere.md` | `''` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_DEVICE_TOKEN_TTL_DAYS` | str | `''` | - | `docs/guides/OPERATOR_RUNBOOK.md` | `''` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_DIGARR_AI_BASE_URL` | str | `''` | - | - | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_AI_MODEL` | str | `''` | - | - | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_AI_TIMEOUT_S` | int | `300` | - | - | `'300'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_IMAGE` | str | `'docker.io/iuliandita/digarr:latest'` | - | - | `'docker.io/iuliandita/digarr:latest'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_PASSWORD` | str | `''` | - | - | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_PORT` | int | `3199` | - | - | `'3199'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGARR_USER` | str | `'zoe'` | - | - | `'zoe'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DIGEST_CHUNKED` | bool | `True` | - | `docs/knowledge/digest-chunked-passes.md` | `-`, `True` | yes | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DIGEST_CHUNK_TOKENS` | int | `0` | - | - | `0` | no | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DIGEST_DECODE_TOK_S` | float | `60.0` | - | `docs/knowledge/digest-chunked-passes.md` | `60.0` | no | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DIGEST_LLM_TIMEOUT_SCALE` | float | `1.0` | - | `docs/knowledge/night-window.md` | `'1'`, `1.0` | yes | NO | `scripts/night/jobs/night_digest.py`<br>`services/zoe-data/memory_digest.py` |
| `ZOE_DIGEST_MAX_CHUNKS` | int | `5` | - | `docs/knowledge/digest-chunked-passes.md` | `5` | no | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DIGEST_MAX_FACTS` | int | `40` | - | `docs/knowledge/digest-chunked-passes.md` | `40` | no | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DIGEST_OBSERVATION_GATE` | str | `'enforce'` | `off`, `shadow`, `enforce` | `docs/knowledge/zoe-memory-bench.md` | `-` | no | NO | `services/zoe-data/memory_authority.py` |
| `ZOE_DIGEST_PREFILL_TOK_S` | float | `650.0` | - | - | `650.0` | no | NO | `services/zoe-data/digest_pack.py` |
| `ZOE_DISCOVERY_DEFAULT_MOOD` | str | `''` | - | - | `''` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MAX_USERS` | int | `4` | - | `services/zoe-data/AGENTS.md` | `'4'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MIN_FREE_MB` | int | `1500` | - | `scripts/AGENTS.md` | `'1500'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISCOVERY_MIN_PLAYS` | int | `10` | - | `services/zoe-data/AGENTS.md` | `'10'` | no | NO | `scripts/maintenance/music_discovery_batch.py` |
| `ZOE_DISTRESS_CONTACT_NAME` | str | `''` | - | `docs/knowledge/distress-handoff.md` | `''` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_DISTRESS_CONTACT_USER` | str | `''` | - | `docs/knowledge/distress-handoff.md` | `''` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_DISTRESS_GENTLE` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/distress-handoff.md` | `'shadow'` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_DISTRESS_HANDOFF` | str | `'enforce'` | `off`, `shadow`, `enforce` | `docs/knowledge/distress-handoff.md` | `'enforce'` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_DISTRESS_LINES_FILE` | str | `''` | - | `docs/knowledge/distress-handoff.md` | `''` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_DISTRESS_NOTIFY` | str | `'all'` | - | `docs/knowledge/distress-handoff.md` | `'all'` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_EDGE_TTS_TIMEOUT_S` | int | `5` | - | - | `'5'` | no | yes | `services/zoe-data/tts_waterfall.py` |
| `ZOE_EDGE_TTS_VOICE` | str | `''` | - | - | `dynamic` | no | yes | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_EMBEDDING_MODEL_VERSION` | str | `'minilm-v1'` | - | - | `'minilm-v1'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_EMOTIONAL_FOLLOWUP_ENABLED` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/proactive/triggers/emotional_followup.py` |
| `ZOE_EMOTIONAL_RECALL_ENABLED` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/routers/memories.py` |
| `ZOE_ENABLE_OPENCLAW_EXECUTION` | bool | `False` | - | - | `'false'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_ENGINEERING_MODE` | str | `'interactive'` | - | `docs/guides/MULTICA_HERMES_PR_LOOP.md` | `'interactive'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_ENROLL_DECLINE_DAYS` | int | `60` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `60` | yes | NO | `services/zoe-data/enrollment_state.py` |
| `ZOE_ENROLL_MODE_ENABLED` | bool | `True` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ENROLL_OFFER` | str | `'off'` | `off`, `shadow`, `on` | `docs/knowledge/zoe-driven-voice-enrollment.md` | `'off'` | yes | NO | `services/zoe-data/enrollment_state.py` |
| `ZOE_ENROLL_OFFER_KIDS` | bool | `False` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `False` | yes | NO | `services/zoe-data/enrollment_state.py` |
| `ZOE_ENROLL_OFFER_PIN_DELAY_S` | int | `20` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `20` | yes | NO | `services/zoe-data/voice_enrollment.py` |
| `ZOE_ENROLL_RECORD_MAX_S` | float | `8.0` | - | `docs/knowledge/zoe-driven-voice-enrollment.md` | `'8.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_ENV_FILE` | str | `'/home/zoe/assistant/services/zoe-data/.env'` | - | - | `''`, `'/home/zoe/assistant/services/zoe-data/.env'` | no | NO | `scripts/maintenance/retire_hermes_openclaw_board.py`<br>`scripts/maintenance/verify_executor_queue_backend.py`<br>`services/zoe-data/multica_board_runner.py` |
| `ZOE_ESPEAK_PITCH` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_ESPEAK_SPEED` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_ESPEAK_VOLUME` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_EXACT_WORDS` | bool | `True` | - | `docs/knowledge/zoe-memory-bench.md` | `'on'` | no | NO | `services/zoe-data/exact_words.py` |
| `ZOE_EXACT_WORDS_BACKFILL_HOURS` | int | `36` | - | `services/zoe-data/AGENTS.md` | `'36'` | no | NO | `services/zoe-data/exact_words.py` |
| `ZOE_EXPERT_ACTIVE_DOMAINS` | str | `'weather,time,lists,calendar,reminders,people,memory'` | - | - | `'weather,time,lists,calendar,reminders,people,memory'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_ALLOW_WRITES` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_ENABLED` | bool | `True` | - | - | `'1'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPERT_MODE` | str | `'shadow'` | `off`, `shadow`, `active` | `docs/knowledge/feature-audit-2026-09-25.md` | `'shadow'` | no | NO | `services/zoe-data/expert_dispatch.py` |
| `ZOE_EXPRESSIVE_GENTLE_FROM_H` | int | `22` | - | - | `22` | yes | NO | `services/zoe-data/voice_delivery.py` |
| `ZOE_EXPRESSIVE_GENTLE_TO_H` | int | `7` | - | - | `7` | yes | NO | `services/zoe-data/voice_delivery.py` |
| `ZOE_EXPRESSIVE_MIN_CHARS` | int | `60` | - | `docs/architecture/samantha-evolution-plan.md` | `60` | yes | NO | `services/zoe-data/voice_delivery.py` |
| `ZOE_EXPRESSIVE_TTS` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `False` | yes | NO | `services/zoe-data/voice_delivery.py` |
| `ZOE_FACE_ID_ENABLED` | bool | `False` | - | `docs/knowledge/state-of-zoe-review-2026-09-25.md` | `'false'` | no | NO | `services/zoe-data/routers/face_id.py` |
| `ZOE_FACE_ID_THRESHOLD` | float | `0.45` | - | `docs/research/presence-without-sensors-2026-10-04.md` | `'0.45'` | no | NO | `services/zoe-data/routers/face_id.py` |
| `ZOE_FIRST_SOUND_CLAUSE` | bool | `False` | - | `docs/knowledge/little-gemma-deep-dive-2026-10-10.md` | `'0'`, `False` | yes | NO | `services/zoe-data/narration_filter.py`<br>`services/zoe-data/voice_first_sound.py` |
| `ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS` | int | `24` | - | - | `24` | yes | NO | `services/zoe-data/voice_first_sound.py` |
| `ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS` | int | `4` | - | - | `4` | yes | NO | `services/zoe-data/voice_first_sound.py` |
| `ZOE_FIRST_SOUND_NARRATION_EARLY` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/narration_filter.py` |
| `ZOE_FIRST_SOUND_TOOL_ACK` | bool | `False` | - | `docs/knowledge/first-sound-latency-2026-10-09.md` | `False` | yes | NO | `services/zoe-data/voice_first_sound.py` |
| `ZOE_FLUE_ABORT_ON_CANCEL` | str | `''` | - | `docs/knowledge/log-review-units-2026-10-04.md` | `-` | no | yes | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_BRAIN_TIMEOUT_S` | float | `180.0` | - | - | `180.0` | no | yes | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_BRAIN_URL` | str | `'http://127.0.0.1:3579'` | - | `docs/knowledge/clause-first-prompt-2026-10-10.md` | `'http://127.0.0.1:3579'` | no | yes | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_STREAM_ENABLED` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `-` | no | yes | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FLUE_WIRE` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `-` | no | yes | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_FOLLOWUP_ADDRESSED` | str | `'off'` | `off`, `on`, `strict` | `docs/knowledge/voice-pipeline.md` | `'off'` | no | NO | `services/zoe-data/voice_utterance_shape.py` |
| `ZOE_FORGET_ALIAS_SWEEP` | str | `''` | - | `services/zoe-data/AGENTS.md` | `-` | no | NO | `services/zoe-data/memory_forget_alias.py` |
| `ZOE_FORGET_LEDGER_SALT` | str | `''` | - | `docs/knowledge/live-acceptance-2026-10.md` | `-` | no | yes | `services/zoe-data/memory_forgotten.py` |
| `ZOE_FORGET_NEAR` | str | `''` | - | `docs/knowledge/memory-authority.md` | `-` | no | NO | `services/zoe-data/memory_forgotten.py` |
| `ZOE_FORGET_REDACT` | str | `''` | - | `docs/knowledge/memory-authority.md` | `-` | no | NO | `services/zoe-data/forget_redact.py` |
| `ZOE_FORGOTTEN_SHIELD_DAYS` | str | `''` | - | `docs/knowledge/memory-authority.md` | `-` | no | yes | `services/zoe-data/memory_forgotten.py` |
| `ZOE_FRUSTRATION_MAX_SESSIONS` | int | `2000` | - | - | `'2000'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_GITHUB_DEFAULT_BRANCH` | str | `'main'` | - | - | `'main'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_GITHUB_REPO` | str | `'jason-easyazz/zoe-ai-assistant'` | - | - | `'jason-easyazz/zoe-ai-assistant'` | no | NO | `services/zoe-data/greploop_guard.py`<br>`services/zoe-data/greptile_client.py` |
| `ZOE_GRAPH_RECALL_BOOST` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_GRAPH_RECALL_WEIGHT` | float | `0.3` | - | `services/zoe-data/AGENTS.md` | `0.3` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_HARNESS` | str | `''` | - | `docs/knowledge/memory-loss-audit-2026-10-05.md` | `-` | no | NO | `services/zoe-data/live_store_guard.py` |
| `ZOE_HA_BRIDGE_URL` | str | `'http://127.0.0.1:8007'` | - | `docs/knowledge/operator-skills/openclaw/ha-patterns/SKILL.md` | `''`, `'http://127.0.0.1:8007'` | no | NO | `services/zoe-data/intent_router.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/ha_control.py`<br>`services/zoe-data/routers/stubs.py`<br>`services/zoe-data/routers/system.py`<br>`services/zoe-data/smart_home_service.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_HA_CONTROL_SCOPE` | str | `'panel'` | - | `services/zoe-data/AGENTS.md` | `'panel'` | no | NO | `services/zoe-data/panel_principal.py` |
| `ZOE_HA_URL` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/routers/stubs.py` |
| `ZOE_HA_VOICE_INGRESS_URL` | str | `'http://host.docker.internal:8000'` | - | `docs/research/integration-stack-docs-vs-zoe-2026-10-05.md` | `'http://host.docker.internal:8000'` | no | NO | `services/homeassistant-mcp-bridge/main.py` |
| `ZOE_HA_VOICE_TOKEN` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/homeassistant-mcp-bridge/main.py` |
| `ZOE_HEALTH_CHECK_SCRIPT` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_HEALTH_CHECK_TIMEOUT_S` | int | `120` | - | - | `'120'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_HERMES_AUTO_ESCALATE` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_HOLD_THE_FACT` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/person-half-enforce-pack-2026-10-09.md` | `-` | yes | NO | `services/zoe-data/hold_the_fact.py` |
| `ZOE_HOME_SETUP_SECRET` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/smart_home_setup.py` |
| `ZOE_HOME_SETUP_TTL_S` | int | `900` | - | - | `'900'` | no | NO | `services/zoe-data/smart_home_setup.py` |
| `ZOE_HOST_LAN_IP` | str | `''` | - | `docs/architecture/brain-prompt-tools-audit.md` | `'192.168.1.218'`, `-` | yes | NO | `services/zoe-data/main.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_HOUSEHOLD_COUNTRY` | str | `''` | - | `docs/knowledge/distress-handoff.md` | `dynamic` | yes | NO | `services/zoe-data/distress_handoff.py` |
| `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE` | str | `'registered'` | - | `docs/architecture/panel-remote-access.md` | `'registered'` | no | NO | `services/zoe-data/panel_principal.py` |
| `ZOE_HYBRID_RETRIEVAL_ENABLED` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_IDENTITY_BLOCK` | bool | `True` | - | `docs/knowledge/identity-and-session-continuity.md` | `True` | yes | NO | `services/zoe-data/identity_facts.py` |
| `ZOE_IDLE_CONSOLIDATION_CHECK_S` | int | `60` | - | `docs/architecture/zoe-memory-samantha-buildplan.md` | `60` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_IDLE_CONSOLIDATION_ENABLED` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_IDLE_CONSOLIDATION_IDLE_S` | int | `180` | - | `docs/architecture/samantha-evolution-packets.md` | `180` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_IDLE_CONSOLIDATION_LOOKBACK_S` | int | `3600` | - | `docs/architecture/zoe-memory-samantha-buildplan.md` | `3600` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_IDLE_CONSOLIDATION_MIN_TURNS` | int | `2` | - | - | `2` | no | NO | `services/zoe-data/memory_idle_consolidation.py` |
| `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN` | str | `''` | - | `docs/knowledge/fable-handoff-2026-07-06.md` | `''` | no | yes | `services/zoe-data/auth.py` |
| `ZOE_INTENT_ROUTER_GATE` | bool | `True` | - | `docs/architecture/beat-the-bar-2026-program.md` | `'1'` | no | NO | `services/zoe-data/fast_tiers.py` |
| `ZOE_INTERNAL_TOKEN` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `''`, `-`, `dynamic` | yes | yes | `scripts/maintenance/check_emotional_thread.py`<br>`scripts/maintenance/zoe-nightly-dreaming.py`<br>`services/zoe-core/extensions/abilities.ts`<br>`services/zoe-core/extensions/memory.ts`<br>`services/zoe-data/auth.py`<br>`services/zoe-data/auth_handoff.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/panel_kiosk_credential.py`<br>`services/zoe-data/zoe_core_client.py` |
| `ZOE_KANBAN_BACKEND` | str | `'executor'` | - | `docs/architecture/multica-executor-migration.md` | `'executor'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_BOARD` | str | `'default'` | - | - | `'default'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET` | int | `2` | - | - | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_CONVERGE_NOOP_IMPLEMENT` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_DEAD_WORKER_GRACE_S` | int | `180` | - | - | `'180'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_ESCALATION_MAX_RUNTIME` | str | `'90m'` | - | - | `'90m'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_FOCUSED_TEST_READ_BUDGET` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_GREP_BUDGET` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_FOCUS_READ_BUDGET` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET` | int | `2` | - | - | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET` | int | `12` | - | - | `'12'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET` | int | `6` | - | - | `'6'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_INTENT_GAP_PRE_EDIT_EXPLORE_BUDGET` | int | `6` | - | - | `'6'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_INTENT_GAP_REPEAT_READ_BUDGET` | int | `2` | - | - | `'2'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_MAX_RUNTIME` | str | `'45m'` | - | - | `'45m'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME` | str | `'6h'` | - | - | `'6h'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_PROTOCOL_VIOLATION_LIMIT` | int | `2` | - | `docs/guides/MULTICA_HERMES_PR_LOOP.md` | `'2'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_REAP_DEAD_WORKERS` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KANBAN_SKIP_SCOUT` | str | `''` | - | `docs/guides/ENGINEERING_HARNESS_LOOP.md` | `''` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `ZOE_KANBAN_TERMINAL_TOOL_GRACE` | int | `2` | - | - | `2` | no | NO | `services/zoe-data/kanban_phase_budget.py` |
| `ZOE_KIOSK_GATE_PORT` | int | `7781` | - | - | `'7781'` | no | NO | `scripts/setup/touchscreen/zoe-kiosk-gate.py` |
| `ZOE_KOKORO_BRAIN_HEALTH_URL` | str | `'http://127.0.0.1:11434/health'` | - | - | `'http://127.0.0.1:11434/health'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_BRAIN_POLL_S` | int | `2` | - | - | `'2'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_BRAIN_WAIT_S` | int | `180` | - | - | `'180'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_DIR` | str | `''` | - | `docs/knowledge/kokoro-onnx-migration.md` | `dynamic` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_FLUSH_INTERVAL_S` | int | `60` | - | - | `60` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_MAX_DISK` | int | `1000` | - | `scripts/AGENTS.md` | `1000` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_MAX_DISK_BYTES` | str | `''` | - | `scripts/AGENTS.md` | `dynamic` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CACHE_PERSIST` | bool | `True` | - | `scripts/AGENTS.md` | `True` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CUDA_ATTEMPTS` | int | `3` | - | - | `'3'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_CUDA_RETRY_DELAY_S` | int | `6` | - | - | `'6'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `ZOE_KOKORO_IMPORT_BLOCK` | bool | `True` | - | `docs/knowledge/voice-pipeline.md` | `'1'` | no | NO | `scripts/setup/kokoro_import_block.py` |
| `ZOE_KOKORO_SIDECAR_URL` | str | `'http://127.0.0.1:10201'` | - | `docs/knowledge/kokoro-onnx-migration.md` | `'http://127.0.0.1:10201'` | no | NO | `scripts/perf/measure_tts.py`<br>`services/zoe-data/main.py`<br>`services/zoe-data/tts_waterfall.py` |
| `ZOE_KOKORO_VOICE` | str | `'af_sky'` | - | `docs/knowledge/voice-pipeline.md` | `'af_sky'`, `dynamic` | no | yes | `scripts/perf/measure_tts.py`<br>`services/zoe-data/voice_settings.py` |
| `ZOE_KOKORO_VOICES` | str | `''` | - | `docs/knowledge/voice-pipeline.md` | `''` | no | NO | `services/zoe-data/voice_settings.py` |
| `ZOE_LATENCY_BASELINE` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_RESULTS` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_SAMPLES` | int | `2` | - | - | `'2'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_SESSION_ID` | str | `'zoe-latency-probe'` | - | - | `'zoe-latency-probe'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_TIMEOUT_S` | int | `30` | - | - | `'30'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_WARN_MS` | int | `500` | - | - | `'500'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LATENCY_WARN_RATIO` | float | `1.5` | - | - | `'1.5'` | no | NO | `scripts/maintenance/zoe_latency_probe.py` |
| `ZOE_LAYOUT_MEMORY` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/ui_layouts.py` |
| `ZOE_LIVEKIT_BRAIN_TIMEOUT_S` | int | `20` | - | `docs/architecture/remediation-packets-2026-07.md` | `'20'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_CONTAINER` | str | `'livekit'` | - | - | `'livekit'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_FAST_TIERS` | bool | `False` | - | `docs/knowledge/contacts-conversation.md` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_IDLE_TIMEOUT_S` | int | `300` | - | `docs/knowledge/memory-pressure-profile.md` | `'300'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_ONDEMAND` | bool | `True` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'true'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVEKIT_STREAM_TTS` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LIVE_PALACE_DIR` | str | `''` | - | `docs/knowledge/memory-loss-audit-2026-10-05.md` | `''` | no | NO | `services/zoe-data/live_store_guard.py` |
| `ZOE_LIVE_REPO_ROOT` | str | `'/home/zoe/assistant'` | - | - | `'/home/zoe/assistant'` | no | NO | `scripts/maintenance/zoe_apply_intent_gap_contract.py` |
| `ZOE_LIVE_SERVICE_DIR` | str | `'/home/zoe/assistant/services/zoe-data'` | - | `docs/knowledge/router-selftrain-loop.md` | `'/home/zoe/assistant/services/zoe-data'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_LK_COOLDOWN_TIMEOUT_S` | float | `4.0` | - | - | `'4.0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_ENERGY_THRESHOLD` | int | `400` | - | - | `'400'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_MIN_SPEECH_FRAMES` | int | `5` | - | - | `'5'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_SILENCE_FRAMES` | int | `20` | - | - | `'20'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LK_USE_AIORTC` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_LLAMA_URL` | str | `'http://127.0.0.1:11434'` | - | - | `'http://127.0.0.1:11434'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_LLM_MODEL` | str | `'gemma'` | - | - | `'gemma'` | no | NO | `services/zoe-data/memory_digest.py` |
| `ZOE_LOCAL_MODEL` | str | `'Gemma 4 E4B-QAT'` | - | - | `'Gemma 4 E4B-QAT'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_LOCAL_TTS_URL` | str | `''` | - | - | `''` | no | yes | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_LOCATION_CITY` | str | `'Geraldton'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''`, `'Geraldton'` | no | NO | `services/zoe-data/identity_facts.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py`<br>`services/zoe-data/voice_stitch.py` |
| `ZOE_LOCATION_COUNTRY` | str | `'AU'` | - | `docs/knowledge/distress-handoff.md` | `''`, `'AU'` | yes | NO | `services/zoe-data/distress_handoff.py`<br>`services/zoe-data/identity_facts.py`<br>`services/zoe-data/routers/weather.py` |
| `ZOE_LOCATION_LAT` | float | `-28.7774` | - | - | `'-28.7774'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py` |
| `ZOE_LOCATION_LON` | float | `114.6158` | - | - | `'114.6158'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py` |
| `ZOE_LOCATION_REGION` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/identity_facts.py` |
| `ZOE_LOG_BACKUP_COUNT` | int | `5` | - | `services/zoe-data/AGENTS.md` | `5` | no | yes | `services/zoe-data/logging_setup.py` |
| `ZOE_LOG_CHATTY_LIBS_LEVEL` | str | `'WARNING'` | - | `docs/knowledge/log-review-2026-10-04.md` | `'WARNING'` | no | NO | `services/zoe-data/logging_setup.py` |
| `ZOE_LOG_DIR` | str | `'~/.zoe-logs'` | - | `services/zoe-data/AGENTS.md` | `'~/.zoe-logs'` | no | yes | `services/zoe-data/logging_setup.py` |
| `ZOE_LOG_LEVEL` | str | `''` | - | `services/zoe-data/AGENTS.md` | `-` | no | yes | `services/zoe-data/logging_setup.py` |
| `ZOE_LOG_MAX_BYTES` | str | `''` | - | `services/zoe-data/AGENTS.md` | `dynamic` | no | yes | `services/zoe-data/logging_setup.py` |
| `ZOE_LOG_QUIET_POLL_PATHS` | str | `''` | - | `docs/knowledge/log-review-2026-10-04.md` | `-` | no | NO | `services/zoe-data/middleware/logging.py` |
| `ZOE_LOG_QUIET_POLL_SLOW_MS` | int | `1000` | - | `docs/knowledge/incident-runbook.md` | `1000` | no | NO | `services/zoe-data/middleware/logging.py` |
| `ZOE_LOG_REPEAT_WINDOW_S` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `-` | no | NO | `services/zoe-data/log_throttle.py` |
| `ZOE_LOOP_LIFECYCLE` | bool | `False` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `False` | yes | NO | `services/zoe-data/open_loop_lifecycle.py` |
| `ZOE_MANNER_BLOCK` | str | `'off'` | `off`, `on` | `docs/knowledge/converse-not-mirror.md` | `'off'` | yes | NO | `services/zoe-data/manner_block.py` |
| `ZOE_MAX_BROWSER_TABS` | int | `5` | - | - | `5` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_MA_CONTAINER` | str | `'zoe-music-assistant'` | - | - | `'zoe-music-assistant'` | no | NO | `scripts/maintenance/ma_idle_reap.py`<br>`services/zoe-data/ma_ondemand.py` |
| `ZOE_MA_IDLE_MIN` | int | `45` | - | `docs/knowledge/music-assistant-idle-reap.md` | `45` | no | NO | `scripts/maintenance/ma_idle_reap.py` |
| `ZOE_MA_IDLE_REAP` | bool | `False` | - | `docs/knowledge/music-assistant-idle-reap.md` | `'0'` | no | NO | `scripts/maintenance/ma_idle_reap.py`<br>`services/zoe-data/ma_ondemand.py` |
| `ZOE_MA_INFLIGHT_GRACE_S` | int | `120` | - | `docs/knowledge/music-assistant-idle-reap.md` | `120` | no | NO | `scripts/maintenance/ma_idle_reap.py` |
| `ZOE_MA_REAP_QUIET_HOURS` | str | `''` | - | `docs/knowledge/music-assistant-idle-reap.md` | `-` | no | NO | `scripts/maintenance/ma_idle_reap.py` |
| `ZOE_MA_REAP_STATE_DIR` | str | `'~/.cache/zoe/ma-reap'` | - | `services/zoe-data/AGENTS.md` | `'~/.cache/zoe/ma-reap'` | no | NO | `scripts/maintenance/ma_idle_reap.py`<br>`services/zoe-data/ma_ondemand.py` |
| `ZOE_MA_START_TIMEOUT_S` | int | `25` | - | `docs/knowledge/music-assistant-idle-reap.md` | `'25'` | no | NO | `services/zoe-data/ma_ondemand.py` |
| `ZOE_MCP_ACTOR_ROLE` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_ACTOR_USER_ID` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_STRICT_USER_ID` | bool | `False` | - | `docs/research/zoe-abilities-connectors-2026-10.md` | `'false'` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_USER_ID` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MCP_USER_ROLE` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/mcp_server.py` |
| `ZOE_MEMORY_AUDIT_COLLECTION` | str | `'mempalace_audit'` | - | - | `'mempalace_audit'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_MEMORY_AUTHORITY` | str | `'enforce'` | `off`, `shadow`, `enforce` | `docs/knowledge/memory-authority.md` | `-` | no | NO | `services/zoe-data/memory_authority.py` |
| `ZOE_MEMORY_COMPOSE_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/zoe_memory_compose.py` |
| `ZOE_MEMORY_DIGEST_LOOKBACK_HOURS` | str | `''` | - | `docs/knowledge/live-acceptance-2026-10.md` | `-` | no | NO | `services/zoe-data/memory_digest.py` |
| `ZOE_MEMORY_HEAP_SCRUB` | str | `''` | - | `docs/knowledge/forgotten-text-physical-erase.md` | `''` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_MEMORY_IMPLICIT_SUPERSEDE` | bool | `False` | - | `docs/knowledge/memory-supersession.md` | `False` | yes | NO | `services/zoe-data/memory_supersede.py` |
| `ZOE_MEMORY_INDEX_COMPACT` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `''` | no | yes | `services/zoe-data/memory_service.py` |
| `ZOE_MEMORY_INDEX_COMPACT_DAY` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `-` | no | yes | `scripts/maintenance/zoe-nightly-dreaming.py` |
| `ZOE_MEMORY_LINK_RESOLVER_ENABLED` | str | `''` | - | `docs/knowledge/contacts-conversation.md` | `''` | no | NO | `services/zoe-data/memory_digest.py` |
| `ZOE_MEMORY_LINT_IN_DREAMING` | str | `''` | - | `docs/research/memory-fidelity-audit-2026-10-05.md` | `''` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LINT_NEAR_DUP_RATIO` | float | `0.92` | - | - | `'0.92'` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LINT_STALE_DAYS` | int | `365` | - | - | `'365'` | no | NO | `services/zoe-data/memory_lint.py` |
| `ZOE_MEMORY_LOOP_LOG_PATH` | str | `'~/.zoe/zoe-data-memory-loops.log'` | - | `services/zoe-data/AGENTS.md` | `'~/.zoe/zoe-data-memory-loops.log'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `-` | no | NO | `services/zoe-data/memory_metrics.py` |
| `ZOE_MEMORY_PHYSICAL_ERASE` | bool | `True` | - | `docs/knowledge/forgotten-text-physical-erase.md` | `'1'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_MEMORY_PROVENANCE_ANSWERS` | bool | `True` | - | `docs/knowledge/samantha-bar.md` | `True` | yes | NO | `services/zoe-data/memory_provenance.py` |
| `ZOE_MEMORY_REJECT_LEDGER` | str | `''` | - | `services/zoe-data/AGENTS.md` | `-` | no | NO | `services/zoe-data/memory_reject_ledger.py` |
| `ZOE_MEMORY_STARTUP_STRICT` | bool | `False` | - | `docs/knowledge/python-312-venv-migration.md` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MERGE_QUEUE_ENABLED` | str | `''` | - | `services/zoe-data/AGENTS.md` | `''` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MERGE_QUEUE_LABEL` | str | `'auto-merge'` | - | - | `'auto-merge'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MERGE_QUEUE_MAX_CANDIDATES` | int | `50` | - | - | `'50'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_MIRROR_GUARD` | str | `'shadow'` | `off`, `shadow`, `on` | `docs/knowledge/converse-not-mirror.md` | `-` | yes | NO | `services/zoe-data/mirror_guard.py` |
| `ZOE_MIRROR_INTENTS_TO_OPENCLAW` | bool | `False` | - | - | `'false'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_MISHEAR_CLARIFY` | str | `'off'` | `off`, `shadow`, `on` | `docs/knowledge/voice-pipeline.md` | `'off'` | yes | NO | `services/zoe-data/mishear_clarify.py` |
| `ZOE_MOONSHINE_ARCH` | str | `'MEDIUM_STREAMING'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'MEDIUM_STREAMING'`, `'v2'` | yes | yes | `scripts/perf/measure_moonshine_vad_threshold.py`<br>`scripts/perf/measure_stt_under_speech.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `ZOE_MOONSHINE_KEYTERMS` | list | `()` | - | `docs/knowledge/moonshine-0-1-5-upgrade.md` | `-` | yes | yes | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_MULTICA` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES_FOR` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPILOT_STALE_HOURS` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES` | bool | `False` | - | - | `'false'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES_FOR` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTOPIOT_STALE_HOURS` | int | `2` | - | - | `'2'` | no | NO | `services/zoe-data/multica_autopilot_sync.py` |
| `ZOE_MULTICA_AUTO_ADMIT` | bool | `False` | - | `docs/architecture/multica-retirement-gates.md` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_BLOCKED_RESUME_BUDGET` | int | `4` | - | `docs/architecture/multica-retirement-gates.md` | `4` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_CROSS_REVIEW` | bool | `False` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `'false'` | no | NO | `services/zoe-data/pipeline_cross_review.py` |
| `ZOE_MULTICA_DISPATCH_PAUSE_FILE` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/multica_dispatch_control.py` |
| `ZOE_MULTICA_KILL_SWITCH` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/multica_board_runner.py` |
| `ZOE_MULTICA_NATIVE_COMMENT_GUARD` | str | `''` | - | - | `-` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `ZOE_MULTICA_PAUSED_POLL_S` | float | `300.0` | - | `docs/architecture/multica-retirement-gates.md` | `300.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_POLL_DISPATCH_LIMIT` | int | `1` | - | `docs/architecture/multica-retirement-gates.md` | `1` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_POLL_REF_TIMEOUT_S` | float | `20.0` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `20.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_POOL_MAX` | int | `4` | - | - | `'4'` | no | NO | `services/zoe-data/executors/executor_queue_backend.py` |
| `ZOE_MULTICA_STALE_IN_PROGRESS_HOURS` | float | `6.0` | - | `docs/architecture/multica-retirement-gates.md` | `6.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_MULTICA_TRIAGE_JUDGE` | bool | `False` | - | `docs/PLANS.md` | `False` | yes | NO | `services/zoe-data/multica_triage_judge.py` |
| `ZOE_MULTICA_WORKSPACE_ID` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/executors/executor_queue_backend.py` |
| `ZOE_MULTI_HOP_RECALL` | bool | `True` | - | `docs/knowledge/zoe-memory-bench.md` | `'on'` | no | NO | `services/zoe-data/multi_hop_recall.py` |
| `ZOE_MUSIC_DISCOVERY` | str | `'off'` | `off`, `on` | `docs/knowledge/state-of-zoe-review-2026-09-25.md` | `'off'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_DOW` | str | `'sun'` | - | - | `'sun'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_HOUR` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_DISCOVERY_JSON` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/music_discovery.py` |
| `ZOE_MUSIC_HISTORY` | bool | `True` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'on'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_HISTORY_INTERVAL_S` | int | `300` | - | `services/zoe-data/AGENTS.md` | `'300'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_MUSIC_OAUTH_TTL_S` | int | `150` | - | - | `'150'` | no | NO | `services/zoe-data/music_oauth.py` |
| `ZOE_MUSIC_OBSERVE_ATTRIB_MIN` | int | `30` | - | - | `'30'` | no | NO | `services/zoe-data/music_history.py` |
| `ZOE_MUSIC_OBSERVE_DEDUP_H` | int | `12` | - | - | `'12'` | no | NO | `services/zoe-data/music_history.py` |
| `ZOE_MUSIC_SETUP_SECRET` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/music_setup.py` |
| `ZOE_MUSIC_SETUP_TTL_S` | int | `900` | - | `docs/research/zoe-abilities-connectors-2026-10.md` | `'900'` | no | NO | `services/zoe-data/music_setup.py` |
| `ZOE_NIGHT_MIND` | str | `'off'` | `off`, `shadow`, `enforce` | `docs/knowledge/night-mind.md` | `''`, `-` | no | NO | `scripts/maintenance/zoe-night-mind.py`<br>`scripts/perf/zmb/arms/z0.py`<br>`services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_CHUNK_TOKENS` | int | `0` | - | - | `0` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_CTX_TOKENS` | int | `0` | - | - | `0` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_DECODE_TOK_S` | float | `8.0` | - | `docs/knowledge/night-mind.md` | `8.0` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_MAX_CALLS` | int | `7` | - | `services/zoe-data/AGENTS.md` | `7` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_MODEL` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_PREFILL_TOK_S` | float | `650.0` | - | `docs/knowledge/night-mind.md` | `650.0` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_SCHEMA` | str | `''` | - | `docs/knowledge/night-mind.md` | `-` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NIGHT_MIND_URL` | str | `''` | - | `docs/knowledge/night-mind.md` | `dynamic` | no | NO | `services/zoe-data/night_mind.py` |
| `ZOE_NVM_NODE_BIN` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/pi_intent_classifier.py` |
| `ZOE_OMI_AUDIO_DIR` | str | `''` | - | `docs/architecture/omi-integration-plan.md` | `''` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_AUDIO_RETENTION_DAYS` | int | `7` | - | `docs/architecture/omi-integration-plan.md` | `7` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_AUDIT_PATH` | str | `''` | - | `docs/knowledge/bluetooth-settings.md` | `''` | yes | NO | `services/zoe-data/omi_devices.py` |
| `ZOE_OMI_DAY_REVIEW` | str | `'off'` | `off`, `shadow`, `on` | `docs/knowledge/night-window.md` | `-` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DAY_REVIEW_IDLE_S` | int | `600` | - | `docs/architecture/memories-space.md` | `600` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DAY_REVIEW_LANE` | str | `'both'` | - | `docs/architecture/memories-space.md` | `'both'` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DAY_REVIEW_MAX_AGE_DAYS` | int | `7` | - | - | `7` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DAY_REVIEW_MAX_CALLS` | int | `8` | - | `docs/architecture/memories-space.md` | `8` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DAY_REVIEW_MIN_MEM_MB` | int | `1000` | - | `services/zoe-data/AGENTS.md` | `1000` | no | NO | `services/zoe-data/omi_day_review.py` |
| `ZOE_OMI_DEVICE_OWNERS` | bool | `False` | - | `docs/knowledge/bluetooth-settings.md` | `False` | yes | NO | `services/zoe-data/omi_devices.py` |
| `ZOE_OMI_DIAR_LINK_MIN` | float | `0.45` | - | `docs/knowledge/omi-voice-tagging.md` | `0.45` | yes | NO | `services/zoe-data/omi_diarize.py` |
| `ZOE_OMI_HEALTH` | bool | `True` | - | `docs/knowledge/omi-pendant-health.md` | `True` | yes | NO | `services/zoe-data/omi_health.py` |
| `ZOE_OMI_IDLE_S` | int | `120` | - | `docs/architecture/omi-integration-plan.md` | `120` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_KEEP_AUDIO` | bool | `False` | - | `docs/knowledge/omi-voice-tagging.md` | `False` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_LEARN_VOICES` | bool | `False` | - | `docs/knowledge/omi-voice-tagging.md` | `False` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_MIN_MEM_MB` | int | `800` | - | `docs/architecture/omi-integration-plan.md` | `800` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_OWNER_USER_ID` | str | `''` | - | `docs/knowledge/bluetooth-settings.md` | `''` | yes | NO | `services/zoe-data/omi_devices.py`<br>`services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_SEGMENT_EMBED_TTL_DAYS` | int | `14` | - | `docs/knowledge/omi-voice-tagging.md` | `14` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_SPEAKER_TAG` | str | `'False'` | - | `docs/knowledge/omi-voice-tagging.md` | `'off'` | yes | NO | `services/zoe-data/omi_diarize.py` |
| `ZOE_OMI_SUGGEST_MARGIN` | float | `0.1` | - | `docs/knowledge/omi-voice-tagging.md` | `0.1` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_SUGGEST_MIN` | float | `0.45` | - | `docs/knowledge/omi-voice-tagging.md` | `0.45` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_SYNC_ENABLED` | bool | `False` | - | `docs/knowledge/omi-pendant-health.md` | `False` | yes | NO | `services/zoe-data/omi_recordings.py` |
| `ZOE_OMI_TAG_MODEL_DIR` | str | `''` | - | `docs/knowledge/omi-voice-tagging.md` | `dynamic` | yes | NO | `services/zoe-data/omi_diarize.py` |
| `ZOE_OMI_TAG_OTHER_MAX` | float | `0.0` | - | `docs/architecture/memories-space.md` | `dynamic` | yes | NO | `services/zoe-data/omi_diarize.py` |
| `ZOE_OMI_TAG_YOU_MIN` | float | `0.0` | - | `docs/knowledge/omi-voice-tagging.md` | `dynamic` | yes | NO | `services/zoe-data/omi_diarize.py` |
| `ZOE_OMI_VOICEPRINT_IDLE_DAYS` | int | `365` | - | `docs/architecture/memories-space.md` | `365` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_VOICEPRINT_PROVISIONAL_DAYS` | int | `30` | - | `docs/knowledge/omi-voice-tagging.md` | `30` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMI_VOICE_CLUSTER_MIN` | float | `0.45` | - | `docs/knowledge/omi-voice-tagging.md` | `0.45` | yes | NO | `services/zoe-data/omi_voices.py` |
| `ZOE_OMNIGENT_AGENT_ID` | str | `'057995d1517418e6839f51d340785dd6'` | - | - | `'057995d1517418e6839f51d340785dd6'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OMNIGENT_CLOSE_POLL_S` | int | `60` | - | - | `'60'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OMNIGENT_CLOSE_TIMEOUT_S` | int | `2400` | - | - | `'2400'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OMNIGENT_CONTAINER` | str | `'zoe-omnigent'` | - | - | `'zoe-omnigent'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OMNIGENT_IMPLEMENT_TIMEOUT_S` | int | `1800` | - | - | `'1800'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OMNIGENT_URL` | str | `'http://127.0.0.1:6767'` | - | `docs/guides/MODULE_SYSTEM.md` | `'http://127.0.0.1:6767'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_OPENCLAW_GW` | str | `'http://127.0.0.1:18789'` | - | - | `'http://127.0.0.1:18789'`, `dynamic` | no | NO | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/chat.py` |
| `ZOE_OTEL_ENABLED` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_OWN_FACT_PRECEDENCE` | str | `''` | - | `docs/knowledge/conversation-quality-classes.md` | `-` | no | NO | `services/zoe-data/semantic_router.py`<br>`services/zoe-data/zoe_flue_client.py` |
| `ZOE_PANEL_AGENT_PORT` | int | `8765` | - | - | `'8765'` | no | NO | `services/zoe-data/auth_handoff.py`<br>`services/zoe-data/routers/system.py` |
| `ZOE_PANEL_ALLOWED_HOSTS` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/agent_safety.py` |
| `ZOE_PANEL_ID` | str | `'zoe-touch-pi'` | - | - | `'post-merge-probe'`, `'zoe-touch-pi'` | no | NO | `scripts/maintenance/zoe_latency_probe.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_PANEL_KIOSK_CREDENTIAL` | str | `''` | - | `docs/knowledge/panel-away-p1.md` | `''` | no | NO | `services/zoe-data/panel_kiosk_credential.py` |
| `ZOE_PANEL_KIOSK_CREDENTIAL_TTL_S` | str | `''` | - | `docs/knowledge/panel-away-p1.md` | `dynamic` | no | NO | `services/zoe-data/panel_kiosk_credential.py` |
| `ZOE_PANEL_KIOSK_SECRET` | str | `''` | - | `docs/knowledge/panel-away-p1.md` | `dynamic` | no | NO | `services/zoe-data/panel_kiosk_credential.py` |
| `ZOE_PANEL_KIOSK_TICKET_TTL_S` | int | `60` | - | `docs/knowledge/panel-away-p1.md` | `60` | no | NO | `services/zoe-data/panel_kiosk_credential.py` |
| `ZOE_PANEL_SESSION_TRUST_WINDOW_S` | int | `900` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'900'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_PERF` | str | `''` | - | `docs/knowledge/person-half-enforce-pack-2026-10-09.md` | `-` | no | NO | `scripts/perf/engine_bench.py`<br>`scripts/perf/hop_placement_ab.py`<br>`scripts/perf/kokoro_first_chunk_probe.py`<br>`scripts/perf/measure_first_sound.py`<br>`scripts/perf/measure_speed.py`<br>`scripts/perf/measure_tts.py`<br>`scripts/perf/measure_voice.py`<br>`scripts/perf/person_half_enforce_ab.py`<br>`scripts/perf/recall_evidence_probe.py`<br>`scripts/perf/samantha_bar.py`<br>`scripts/perf/samantha_day_sim.py`<br>`scripts/perf/samantha_person.py`<br>`scripts/perf/tool_use_bench.py`<br>`scripts/perf/user_model_ab.py` |
| `ZOE_PERSONALISATION_HOP` | bool | `True` | - | `docs/knowledge/samantha-bar.md` | `True` | yes | NO | `services/zoe-data/personalisation_hop.py` |
| `ZOE_PERSONALISATION_HOP_PLACEMENT` | str | `'block'` | - | `docs/knowledge/samantha-bar.md` | `'block'` | yes | NO | `services/zoe-data/personalisation_hop.py` |
| `ZOE_PERSONALISATION_HOP_WORDING` | str | `'natural'` | - | `docs/knowledge/samantha-bar.md` | `'natural'` | yes | NO | `services/zoe-data/personalisation_hop.py` |
| `ZOE_PERSONA_DRIFT` | bool | `False` | - | `docs/knowledge/persona-layer.md` | `False` | yes | yes | `services/zoe-data/persona_drift.py` |
| `ZOE_PERSONA_LAYER` | bool | `False` | - | `docs/knowledge/persona-layer.md` | `False` | yes | yes | `services/zoe-data/persona_layer.py` |
| `ZOE_PERSON_BENCH_DIR` | str | `''` | - | - | `-` | no | NO | `scripts/perf/person_half_replay.py` |
| `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/person_extractor.py` |
| `ZOE_PERSON_DOSSIER_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/zoe_memory_compose.py` |
| `ZOE_PERSON_LLM_CONFIDENCE_GATE` | str | `''` | - | `docs/knowledge/contacts-people-memory.md` | `''` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_LLM_CONFIDENCE_MIN` | float | `0.4` | - | `docs/knowledge/contacts-people-memory.md` | `'0.4'` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_LLM_PREFILTER` | str | `''` | - | `docs/research/brain-extraction-stack-best-practice-2026-10-05.md` | `''` | no | NO | `services/zoe-data/person_extractor_llm.py` |
| `ZOE_PERSON_MERGE_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/person_merge.py` |
| `ZOE_PERSON_RECALL_FLOOR` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `-` | no | NO | `services/zoe-data/memory_gate.py` |
| `ZOE_PERSON_SUGGEST_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/pending_suggestions.py` |
| `ZOE_PIN_CHALLENGE_TTL_S` | int | `120` | - | `docs/guides/OPERATOR_RUNBOOK.md` | `'120'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIN_LOCKOUT_S` | int | `300` | - | `docs/research/identity-presence-scorecard-2026-10.md` | `'300'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIN_MAX_ATTEMPTS` | int | `5` | - | `docs/guides/OPERATOR_RUNBOOK.md` | `'5'` | no | NO | `services/zoe-data/routers/panel_auth.py` |
| `ZOE_PIPELINE_HARNESS_CLOSEOUT_MERGE` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_HARNESS_REVIEW_APPROVE` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_HARNESS_VERIFY_TESTS` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_STORE_PATH` | str | `''` | - | `docs/guides/ENGINEERING_HARNESS_LOOP.md` | `''` | no | NO | `scripts/maintenance/compact_pipeline_state.py`<br>`scripts/maintenance/engineering_harness_loop.py`<br>`services/zoe-data/pipeline_store.py` |
| `ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT` | bool | `True` | - | - | `'1'` | no | NO | `services/zoe-data/pipeline_store.py` |
| `ZOE_PI_EXECUTOR_COMMAND` | str | `'pi'` | - | - | `'pi'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_MODE` | str | `'text'` | - | - | `'text'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_MODEL` | str | `'minimax/minimax-m3'` | - | - | `'minimax/minimax-m3'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_PROVIDER` | str | `'openrouter'` | - | - | `'openrouter'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_EXECUTOR_TIMEOUT_S` | int | `900` | - | - | `'900'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_PI_HOST` | str | `'192.168.1.61'` | - | `docs/knowledge/app-connection-handoff.md` | `'192.168.1.61'` | no | NO | `services/zoe-data/auth_handoff.py`<br>`services/zoe-data/routers/system.py` |
| `ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH` | str | `'~/.zoe/data/pi-hybrid-production-evidence.jsonl'` | - | - | `'~/.zoe/data/pi-hybrid-production-evidence.jsonl'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH` | str | `'~/.zoe/data/pi-hybrid-production-labels.jsonl'` | - | - | `'~/.zoe/data/pi-hybrid-production-labels.jsonl'` | no | NO | `services/zoe-data/routers/system.py` |
| `ZOE_PI_INTENT_ENABLED` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `-` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_PI_INTENT_MODEL` | str | `'gemma-4-E2B-it-Q4_K_M.gguf'` | - | - | `'gemma-4-E2B-it-Q4_K_M.gguf'` | no | NO | `scripts/maintenance/pi_intent_probe.py` |
| `ZOE_PI_INTENT_PREFILTER_ENABLED` | bool | `True` | - | `docs/architecture/zoe-pi-runtime-harness.md` | `'true'` | no | NO | `scripts/maintenance/pi_promotion_eval.py` |
| `ZOE_PI_INTENT_PROMOTED_GROUPS` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `-` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_PI_INTENT_SHADOW_ENABLED` | str | `''` | - | `docs/knowledge/memory-pressure-profile-2026-10-03.md` | `-` | no | NO | `services/zoe-data/intent_router.py` |
| `ZOE_PI_INTENT_TIMEOUT_SECONDS` | float | `4.0` | - | - | `4.0` | no | NO | `scripts/maintenance/pi_promotion_eval.py` |
| `ZOE_PI_LAB_MIN_AVAILABLE_MB` | float | `2048.0` | - | - | `2048.0` | no | NO | `services/zoe-data/routers/pi_intent_lab.py` |
| `ZOE_PI_LAB_MIN_SWAP_FREE_MB` | float | `256.0` | - | - | `256.0` | no | NO | `services/zoe-data/routers/pi_intent_lab.py` |
| `ZOE_PI_LAB_RESOURCE_GUARD_ENABLED` | bool | `True` | - | - | `True` | no | NO | `services/zoe-data/routers/pi_intent_lab.py` |
| `ZOE_PRESENCE_WINDOW_S` | str | `''` | - | `docs/architecture/samantha-evolution-packets.md` | `''` | no | yes | `services/zoe-data/proactive/presence.py` |
| `ZOE_PROACTIVE_ARRIVAL_RESPONSE_S` | str | `''` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | NO | `services/zoe-data/proactive/arrival.py` |
| `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL` | str | `''` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | NO | `services/zoe-data/proactive/arrival.py` |
| `ZOE_PROACTIVE_LEDGER` | str | `''` | - | `docs/knowledge/person-half-enforce-pack-2026-10-09.md` | `''` | no | NO | `services/zoe-data/proactive/ledger.py` |
| `ZOE_PROACTIVE_RAISE_GAP_S` | int | `7200` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `7200` | yes | NO | `services/zoe-data/proactive/selector.py` |
| `ZOE_PROACTIVE_RAISE_PER_DAY` | int | `2` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `2` | yes | NO | `services/zoe-data/proactive/selector.py` |
| `ZOE_PROACTIVE_SELECTOR` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `''` | no | NO | `services/zoe-data/proactive/selector.py` |
| `ZOE_PROACTIVE_SLOW_LOOP_S` | int | `300` | - | - | `'300'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROACTIVE_SPOKEN` | str | `''` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | yes | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROACTIVE_SPOKEN_TRIGGERS` | str | `'morning_checkin'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'morning_checkin'` | no | yes | `services/zoe-data/proactive/engine.py` |
| `ZOE_PROVENANCE_PERSIST` | bool | `True` | - | `docs/knowledge/samantha-bar.md` | `True` | yes | NO | `services/zoe-data/reply_ledger.py` |
| `ZOE_PROVISION_CODE_TTL_S` | int | `300` | - | `docs/architecture/panel-anywhere.md` | `'300'` | no | NO | `services/zoe-data/routers/panel_provision.py` |
| `ZOE_PROVISION_PICKUP_GRACE_S` | int | `120` | - | `services/zoe-data/routers/AGENTS.md` | `'120'` | no | NO | `services/zoe-data/routers/panel_provision.py` |
| `ZOE_PR_GUARD_ACTIVE_GREPTILE_STALE_SECONDS` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_AUTO_RESOLVE_THREADS` | bool | `False` | - | - | `'0'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_AUTO_UPDATE_BRANCH` | bool | `False` | - | - | `'0'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_CLEAN_MERGE_IGNORES_CONFIDENCE` | bool | `True` | - | - | `True` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_GREPTILE_WAIT_POLL_SECONDS` | int | `120` | - | - | `'120'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_GREPTILE_WAIT_TIMEOUT_SECONDS` | int | `1800` | - | - | `'1800'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_HEAD_STABILITY_SECONDS` | int | `90` | - | - | `'90'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_ACTIVE_GREPTILE_REVIEWS` | bool | `True` | - | - | `'1'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_COST_USD` | float | `0.25` | - | - | `'0.25'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_ITERATIONS` | int | `5` | - | - | `'5'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_MAX_OUTPUT_CHARS` | int | `12000` | - | - | `'12000'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_NO_FINDINGS_RETRIGGER_LIMIT` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_NO_PROGRESS_LIMIT` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_SAME_ERROR_LIMIT` | int | `3` | - | - | `'3'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_STATE_DIR` | str | `'/home/zoe/assistant/.cursor/tmp/pr_guard'` | - | - | `'/home/zoe/assistant/.cursor/tmp/pr_guard'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_TRIGGER_COOLDOWN_SECONDS` | int | `900` | - | - | `'900'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PR_GUARD_UPDATE_BRANCH_COOLDOWN_SECONDS` | int | `300` | - | - | `'300'` | no | NO | `services/zoe-data/greploop_guard.py` |
| `ZOE_PUBLIC_URL` | str | `''` | - | `docs/architecture/zoe-abilities.md` | `''` | no | NO | `services/zoe-data/auth_handoff.py` |
| `ZOE_PULL_NOT_PUSH` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `''` | yes | NO | `services/zoe-data/proactive/pull.py` |
| `ZOE_QUIET_END_HOUR` | int | `7` | - | `docs/architecture/zoe-abilities.md` | `'7'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_QUIET_START_HOUR` | int | `22` | - | `docs/architecture/communication-preferences.md` | `'22'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_QUOTE_RETIRE` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/memory-quote-retire.md` | `'shadow'` | yes | NO | `services/zoe-data/memory_retire.py` |
| `ZOE_READINESS_CACHE_TTL_S` | float | `3.0` | - | - | `3.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_READINESS_TIMEOUT_S` | float | `4.0` | - | - | `4.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_RECALL_DURABLE_NO_DECAY` | bool | `True` | - | `docs/knowledge/zoe-memory-bench.md` | `'1'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_RECALL_EVIDENCE` | bool | `False` | - | `docs/knowledge/recall-gate.md` | `False` | yes | NO | `services/zoe-data/recall_evidence.py` |
| `ZOE_RECALL_GATE` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/recall-gate.md` | `'shadow'` | yes | NO | `services/zoe-data/recall_gate.py` |
| `ZOE_RECALL_PRESENT_STATE_SHAPES` | str | `''` | - | `docs/architecture/beat-the-bar-2026-program.md` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_RELATIONSHIP_GRAPH_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/relationship_graph.py` |
| `ZOE_REMINDER_DEFAULT_TIME` | str | `''` | - | `services/zoe-data/AGENTS.md` | `''` | no | NO | `services/zoe-data/proactive/triggers/reminder_scan.py` |
| `ZOE_REMINDER_MAX_ATTEMPTS` | int | `5` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'5'` | no | NO | `services/zoe-data/proactive/engine.py` |
| `ZOE_REMINDER_STUCK_CLAIM_S` | int | `600` | - | - | `'600'` | no | NO | `services/zoe-data/proactive/triggers/reminders.py` |
| `ZOE_REPO_ROOT` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/repo_paths.py` |
| `ZOE_RESTRAINT` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/restraint.md` | `-` | no | NO | `scripts/perf/samantha_person.py`<br>`services/zoe-data/restraint.py` |
| `ZOE_RIG_BIND` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_DISPLAY` | str | `':99'` | - | - | `':99'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_GEOMETRY` | str | `'1280x800x24'` | - | - | `'1280x800x24'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_LOGIN_URL` | str | `'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmusic.youtube.com%2F'` | - | - | `'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmusic.youtube.com%2F'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_NOVNC_PORT` | int | `6080` | - | - | `'6080'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_USER_AGENT` | str | `''` | - | - | `-` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RIG_VNC_PORT` | int | `5900` | - | - | `'5900'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_RISK_CUES` | str | `'off'` | `off`, `shadow`, `on` | `docs/knowledge/risk-cue-tier.md` | `'off'` | yes | NO | `services/zoe-data/risk_cues.py` |
| `ZOE_ROLE_GUESS_GUARD` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `-` | no | NO | `services/zoe-data/role_guess_guard.py` |
| `ZOE_ROSTER_NEUTRAL_ASK` | bool | `False` | - | `docs/knowledge/contacts-people-memory.md` | `False` | yes | NO | `services/zoe-data/fast_tiers.py` |
| `ZOE_ROUTER_ARCHIVE_KEEP` | int | `3` | - | `docs/knowledge/router-selftrain-loop.md` | `'3'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_BASE_HF` | str | `'/home/zoe/models/lab/functiongemma-270m-it-hf'` | - | - | `'/home/zoe/models/lab/functiongemma-270m-it-hf'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_ENABLED` | bool | `True` | - | - | `'1'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_EVENT_TIME_PRECEDENCE` | str | `''` | - | `docs/architecture/beat-the-bar-2026-program.md` | `-` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD` | str | `'off'` | `off`, `shadow`, `active` | `docs/knowledge/two-stage-router-rollout.md` | `'off'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEADS_BACKEND` | str | `'numpy'` | - | `docs/knowledge/numpy2-jetson-migration.md` | `'numpy'` | no | NO | `services/zoe-data/router_heads_numpy.py` |
| `ZOE_ROUTER_HEAD_LOG` | str | `''` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD_MIN_CONF` | float | `0.7` | - | `docs/knowledge/two-stage-router-rollout.md` | `'0.70'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_HEAD_MLP_PATH` | str | `''` | - | `docs/knowledge/two-stage-router-rollout.md` | `dynamic` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_HEAD_PATH` | str | `''` | - | `docs/knowledge/two-stage-router-rollout.md` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_HEAD_THRESHOLD` | float | `0.4` | - | - | `'0.4'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MARGIN` | float | `0.05` | - | - | `'0.05'` | no | NO | `services/zoe-data/fast_tiers.py` |
| `ZOE_ROUTER_MODE` | str | `'shadow'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'shadow'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MODEL` | str | `'BAAI/bge-small-en-v1.5'` | - | `docs/knowledge/restraint.md` | `'BAAI/bge-small-en-v1.5'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_MODEL_DIR` | str | `'/home/zoe/models/functiongemma-router'` | - | - | `'/home/zoe/models/functiongemma-router'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_SCRATCH_PORT` | int | `11437` | - | `docs/knowledge/router-selftrain-loop.md` | `'11437'` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_ROUTER_SELFTRAIN` | bool | `False` | - | `docs/knowledge/router-selftrain-loop.md` | `'off'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_DOW` | str | `'sat'` | - | `docs/knowledge/router-selftrain-loop.md` | `'sat'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_HOUR` | bool | `True` | - | `docs/knowledge/router-selftrain-loop.md` | `'1'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SELFTRAIN_TIMEOUT_S` | int | `28800` | - | `docs/knowledge/router-selftrain-loop.md` | `'28800'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_ROUTER_SHADOW_KEEP` | int | `4` | - | `services/zoe-data/AGENTS.md` | `4` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SHADOW_MAX_BYTES` | str | `''` | - | `services/zoe-data/AGENTS.md` | `dynamic` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SHADOW_TEXT` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_SIDECAR_URL` | str | `'http://127.0.0.1:11436'` | - | `docs/knowledge/router-selftrain-loop.md` | `'http://127.0.0.1:11436'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_THRESHOLD` | float | `0.62` | - | - | `'0.62'` | no | NO | `services/zoe-data/semantic_router.py` |
| `ZOE_ROUTER_TWO_STAGE_GATE` | float | `0.5` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `'0.5'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_TWO_STAGE_TIMEOUT_S` | float | `1.5` | - | `docs/knowledge/log-review-units-2026-10-04.md` | `'1.5'` | no | NO | `services/zoe-data/router_two_stage.py` |
| `ZOE_ROUTER_WARM_START` | str | `''` | - | `docs/knowledge/router-selftrain-loop.md` | `dynamic` | no | NO | `scripts/maintenance/router_selftrain.py` |
| `ZOE_SCHEDULED_QUEUE_WAIT_S` | float | `1200.0` | - | - | `1200.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_SEAM_CONTINUITY_DEBUG` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_SEAM_CONTINUITY_INJECT` | bool | `True` | - | `docs/knowledge/samantha-bar.md` | `'true'` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_SEAM_OFFER_INJECT` | str | `''` | - | `docs/knowledge/contacts-conversation.md` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_SEAM_RECALL_INJECT` | str | `''` | - | `docs/knowledge/samantha-bar.md` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_SEARCH_HOTNESS_WEIGHT` | float | `0.05` | - | - | `'0.05'` | no | NO | `services/zoe-data/memory_service.py` |
| `ZOE_SEARCH_PROVIDER` | str | `'auto'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'auto'` | yes | yes | `services/zoe-data/web_search_provider.py` |
| `ZOE_SELF_MODEL` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/self-model.md` | `'shadow'` | yes | NO | `services/zoe-data/self_model.py` |
| `ZOE_SESSION_LOCK_TIMEOUT_S` | int | `5` | - | `docs/knowledge/identity-and-session-continuity.md` | `'5'` | no | NO | `services/zoe-data/routers/chat.py` |
| `ZOE_SETUP_QR_HANDLE_TTL_S` | int | `120` | - | - | `'120'` | no | NO | `services/zoe-data/setup_qr.py` |
| `ZOE_SILERO_VAD_MODEL` | str | `'/home/zoe/models/silero_vad.onnx'` | - | `docs/knowledge/voice-pipeline.md` | `'/home/zoe/models/silero_vad.onnx'` | no | NO | `scripts/maintenance/curate_voice_corpus.py`<br>`scripts/maintenance/voice_regression_probe.py`<br>`services/zoe-data/voice_vad.py` |
| `ZOE_SKYBRIDGE_ONLY` | bool | `False` | - | `docs/knowledge/voice-verified-session.md` | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_SKYBRIDGE_ROUTER_VETO` | bool | `True` | - | `docs/knowledge/incident-runbook.md` | `'1'` | no | NO | `services/zoe-data/skybridge_service.py` |
| `ZOE_SKYBRIDGE_TIMEZONE` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/skybridge_service.py` |
| `ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE` | float | `0.45` | - | `docs/knowledge/write-claim-guard.md` | `0.45` | no | NO | `services/zoe-data/skybridge_service.py` |
| `ZOE_SMART_HOME_FOLLOWUP_TTL_S` | int | `60` | - | `services/zoe-data/AGENTS.md` | `'60'` | no | NO | `services/zoe-data/conversation_cues.py` |
| `ZOE_SMART_TURN_ENABLED` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SMART_TURN_MAX_CHECKS` | int | `2` | - | `docs/knowledge/voice-pipeline.md` | `'2'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SMART_TURN_MODEL` | str | `'/home/zoe/models/smart-turn-v3.2-cpu.onnx'` | - | `docs/knowledge/voice-pipeline.md` | `'/home/zoe/models/smart-turn-v3.2-cpu.onnx'` | no | NO | `services/zoe-data/voice_turn.py` |
| `ZOE_SMART_TURN_THREADS` | bool | `True` | - | `docs/research/incomplete-turn-marker-2026-10-04.md` | `'1'` | no | NO | `services/zoe-data/voice_turn.py` |
| `ZOE_SMART_TURN_THRESHOLD` | float | `0.5` | - | `docs/knowledge/voice-pipeline.md` | `'0.5'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_SPEAKER_ADAPT` | str | `'off'` | `off`, `shadow`, `on` | `docs/knowledge/speaker-resnet34-switch.md` | `'off'` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_ALPHA` | float | `0.05` | - | - | `0.05` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_ANCHOR_FLOOR` | float | `0.8` | - | `docs/research/speaker-resnet34-optimisation-2026-10.md` | `0.8` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_FACE_MIN` | float | `0.6` | - | - | `0.6` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MAX_PER_DAY` | int | `5` | - | - | `5` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MIN_ANCHOR_COS` | float | `0.92` | - | - | `0.92` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MIN_INTERVAL_S` | float | `600.0` | - | - | `600.0` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MIN_MARGIN` | float | `0.1` | - | - | `0.1` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MIN_SCORE` | float | `0.85` | - | `docs/research/speaker-resnet34-optimisation-2026-10.md` | `0.85` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_MIN_SPEECH_S` | float | `2.0` | - | - | `2.0` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_PIN_WINDOW_S` | float | `900.0` | - | - | `900.0` | yes | NO | `services/zoe-data/speaker_adaptation.py` |
| `ZOE_SPEAKER_ADAPT_RESNET34_ANCHOR_FLOOR` | float | `0.0` | - | - | `dynamic` | yes | yes | `services/zoe-data/speaker_models.py` |
| `ZOE_SPEAKER_ADAPT_RESNET34_MIN_SCORE` | float | `0.0` | - | `docs/knowledge/speaker-resnet34-switch.md` | `dynamic` | yes | yes | `services/zoe-data/speaker_models.py` |
| `ZOE_SPEAKER_ID_THRESHOLD` | float | `0.82` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0.82'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_SPEAKER_MODEL` | str | `'resemblyzer'` | - | `docs/knowledge/speaker-resnet34-switch.md` | `''`, `'resemblyzer'` | yes | yes | `scripts/setup/zoe_enroll_flow.py`<br>`scripts/setup/zoe_voice_daemon.py`<br>`services/zoe-data/speaker_models.py`<br>`services/zoe-data/voice_session.py` |
| `ZOE_SPEAKER_RESNET34_THRESHOLD_LONG` | float | `0.0` | - | - | `dynamic` | yes | yes | `services/zoe-data/speaker_models.py` |
| `ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT` | float | `0.0` | - | `docs/knowledge/speaker-resnet34-switch.md` | `dynamic` | yes | yes | `services/zoe-data/speaker_models.py` |
| `ZOE_SPEAKER_RESNET34_TIER` | str | `'far1'` | - | `docs/knowledge/speaker-resnet34-switch.md` | `'far1'` | yes | yes | `scripts/setup/zoe_speaker_resnet34.py`<br>`services/zoe-data/speaker_models.py` |
| `ZOE_SPECULATIVE_MAX_HOLD_MS` | int | `5000` | - | `docs/architecture/b1-speculative-turn-start.md` | `5000` | yes | NO | `services/zoe-data/voice_speculation.py` |
| `ZOE_SPECULATIVE_TAIL_MS` | int | `320` | - | `docs/architecture/b1-speculative-turn-start.md` | `320` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_SPECULATIVE_TURN` | bool | `False` | - | `docs/architecture/beat-the-bar-2026-program.md` | `'false'`, `False` | yes | NO | `scripts/setup/zoe_voice_daemon.py`<br>`services/zoe-data/voice_speculation.py` |
| `ZOE_STICKY_SESSION` | bool | `True` | - | `docs/knowledge/identity-and-session-continuity.md` | `True` | yes | NO | `services/zoe-data/session_continuity.py` |
| `ZOE_STICKY_SESSION_MINUTES` | float | `20.0` | - | `docs/knowledge/identity-and-session-continuity.md` | `20.0` | yes | NO | `services/zoe-data/session_continuity.py` |
| `ZOE_STRIP_NARRATION` | str | `''` | - | `docs/knowledge/conversation-quality-classes.md` | `-` | no | NO | `services/zoe-data/narration_filter.py` |
| `ZOE_STRUCTURAL_CLAIMS` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/structural-floors.md` | `-` | no | NO | `services/zoe-data/structural_claims.py` |
| `ZOE_STRUCTURAL_READER` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/structural-floors.md` | `'shadow'` | yes | NO | `services/zoe-data/structural_reader.py` |
| `ZOE_STRUCTURAL_VERIFIER` | str | `'shadow'` | `off`, `shadow`, `enforce` | `docs/knowledge/structural-floors.md` | `'shadow'` | no | NO | `services/zoe-data/structural_verifier.py` |
| `ZOE_STRUGGLE_DETECT` | str | `'shadow'` | `off`, `shadow`, `on` | `docs/architecture/struggle-detector.md` | `-` | no | NO | `services/zoe-data/struggle_detector.py` |
| `ZOE_STRUGGLE_PROPOSALS` | str | `'shadow'` | `off`, `shadow`, `on` | `docs/knowledge/night-window.md` | `-` | no | NO | `services/zoe-data/struggle_proposals.py` |
| `ZOE_STRUGGLE_PROPOSAL_DAYS` | float | `7.0` | - | `docs/architecture/struggle-detector.md` | `7.0` | yes | NO | `services/zoe-data/struggle_proposals.py` |
| `ZOE_STRUGGLE_PROPOSAL_MIN_EPISODES` | int | `3` | - | `docs/architecture/struggle-detector.md` | `3` | yes | NO | `services/zoe-data/struggle_proposals.py` |
| `ZOE_STRUGGLE_QUEUE_MAX` | int | `256` | - | `docs/architecture/struggle-detector.md` | `256` | yes | NO | `services/zoe-data/struggle_detector.py` |
| `ZOE_STRUGGLE_RETENTION_DAYS` | int | `90` | - | `docs/architecture/struggle-detector.md` | `90` | yes | NO | `services/zoe-data/struggle_detector.py` |
| `ZOE_STRUGGLE_WINDOW_S` | float | `0.0` | - | `docs/architecture/struggle-detector.md` | `dynamic` | yes | NO | `services/zoe-data/struggle_detector.py` |
| `ZOE_STT_BACKEND` | str | `'moonshine'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'moonshine'` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_STT_PREWARM_ON_WAKE` | bool | `True` | - | - | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_STT_SNAP` | str | `'shadow'` | `off`, `shadow`, `on` | `docs/knowledge/voice-pipeline.md` | `'shadow'` | no | NO | `services/zoe-data/stt_snap.py` |
| `ZOE_STT_STREAM_FINISH_TIMEOUT_MS` | int | `2500` | - | `docs/knowledge/prefill-under-speech-2026-10-10.md` | `2500` | yes | NO | `services/zoe-data/voice_stt_stream.py` |
| `ZOE_STT_STREAM_UNDER_SPEECH` | bool | `False` | - | `docs/knowledge/little-gemma-deep-dive-2026-10-10.md` | `False` | yes | NO | `services/zoe-data/voice_stt_stream.py` |
| `ZOE_STT_STREAM_UPLOAD` | bool | `False` | - | `docs/knowledge/prefill-under-speech-2026-10-10.md` | `'false'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_SUBPROCESS_QUEUE_WAIT_S` | float | `30.0` | - | - | `30.0` | yes | NO | `services/zoe-data/async_subprocess.py` |
| `ZOE_SYNTHETIC_USER_ALLOWLIST` | str | `''` | - | `docs/knowledge/user-model-ab.md` | `''` | no | NO | `services/zoe-data/user_filters.py` |
| `ZOE_TASK_TIMEOUT_S` | float | `0.0` | - | - | `-`, `dynamic` | yes | NO | `services/zoe-data/background_runner.py` |
| `ZOE_TAVILY_DEPTH` | str | `'basic'` | - | - | `'basic'` | yes | NO | `services/zoe-data/web_search_provider.py` |
| `ZOE_TELEGRAM_BOT_TOKEN` | str | `''` | - | `docs/knowledge/app-connection-handoff.md` | `''` | no | NO | `services/zoe-data/auth_handoff.py` |
| `ZOE_TELEGRAM_BOT_USERNAME` | str | `''` | - | - | `''` | no | NO | `services/zoe-data/telegram_link.py` |
| `ZOE_TELEGRAM_FFMPEG` | str | `''` | - | `docs/knowledge/telegram-voice-notes.md` | `''` | no | yes | `services/zoe-data/routers/telegram_media.py` |
| `ZOE_TELEGRAM_FFMPEG_TIMEOUT_S` | int | `20` | - | `docs/knowledge/telegram-voice-notes.md` | `'20'` | no | yes | `services/zoe-data/routers/telegram_media.py` |
| `ZOE_TELEGRAM_LINK_SECRET` | str | `''` | - | - | `dynamic` | no | NO | `services/zoe-data/telegram_link.py` |
| `ZOE_TELEGRAM_MEDIA` | bool | `False` | - | `docs/knowledge/telegram-voice-notes.md` | `'off'` | no | yes | `services/zoe-data/routers/telegram_media.py` |
| `ZOE_TELEGRAM_STT_CAPTURE_DIR` | str | `''` | - | `docs/knowledge/telegram-voice-notes.md` | `''` | no | yes | `services/zoe-data/routers/telegram_media.py` |
| `ZOE_TELEGRAM_VOICE_MAX_S` | int | `60` | - | `docs/knowledge/telegram-voice-notes.md` | `'60'` | no | yes | `services/zoe-data/routers/telegram_media.py` |
| `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED` | str | `''` | - | `docs/knowledge/relationship-memory-flag-enable.md` | `''` | no | NO | `services/zoe-data/person_extractor.py` |
| `ZOE_TIMEZONE` | str | `'Australia/Perth'` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''`, `'Australia/Perth'` | no | NO | `services/zoe-data/calendar_ical.py`<br>`services/zoe-data/identity_facts.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/memory_digest.py`<br>`services/zoe-data/multica_autopilot_sync.py`<br>`services/zoe-data/proactive/arrival.py`<br>`services/zoe-data/proactive/engine.py`<br>`services/zoe-data/proactive/triggers/emotional_followup.py`<br>`services/zoe-data/proactive/triggers/evening_windown.py`<br>`services/zoe-data/proactive/triggers/evolution_weekly_digest.py`<br>`services/zoe-data/proactive/triggers/people_birthday.py`<br>`services/zoe-data/proactive/triggers/people_health.py`<br>`services/zoe-data/proactive/triggers/reminder_scan.py`<br>`services/zoe-data/routers/weather.py`<br>`services/zoe-data/time_utils.py`<br>`services/zoe-data/voice_greeting.py` |
| `ZOE_TOUCH_PANEL_OFFLINE_CACHE` | bool | `False` | - | `docs/knowledge/ui-deep-review-2026-10-04.md` | `'0'` | no | NO | `services/zoe-auth/touch_panel/quick_auth.py` |
| `ZOE_TOUCH_PROBE_DEVICE_TOKEN` | str | `''` | - | - | `''` | no | NO | `scripts/maintenance/pi_touch_hybrid_production_probe.py` |
| `ZOE_TOUCH_PROBE_PANEL_ID` | str | `'zoe-touch-pi'` | - | - | `'zoe-touch-pi'` | no | NO | `scripts/maintenance/pi_touch_hybrid_production_probe.py` |
| `ZOE_TRIVIA_HEDGE` | str | `''` | - | `docs/knowledge/conversation-quality-classes.md` | `-` | no | NO | `services/zoe-data/zoe_flue_client.py` |
| `ZOE_TRUSTED_PROXY_NETS` | str | `'127.0.0.0/8,::1/128,172.16.0.0/12'` | - | `docs/architecture/panel-remote-access.md` | `'127.0.0.0/8,::1/128,172.16.0.0/12'` | no | NO | `services/zoe-data/panel_principal.py` |
| `ZOE_TTS_KEEP_TAIL_MS` | int | `130` | - | - | `130` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_TTS_LEAD_GUARD_MS` | int | `20` | - | - | `20` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_TTS_MODE` | str | `'hybrid'` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'hybrid'` | yes | yes | `services/zoe-data/main.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `ZOE_TTS_TRIM_SILENCE` | bool | `True` | - | - | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_UNAUTHENTICATED_ROLE` | str | `'guest'` | - | `docs/architecture/panel-anywhere.md` | `'guest'` | no | yes | `services/zoe-data/auth.py` |
| `ZOE_UPDATE_CHECK_ENABLED` | bool | `True` | - | - | `'true'` | no | NO | `services/zoe-data/system_updates.py` |
| `ZOE_URL` | str | `'https://zoe.local'` | - | `docs/knowledge/mac-virtual-panel.md` | `'http://127.0.0.1:8000'`, `'https://zoe.local'`, `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py`<br>`scripts/setup/zoe_enroll_flow.py`<br>`scripts/setup/zoe_face_id.py`<br>`scripts/setup/zoe_voice_daemon.py` |
| `ZOE_USER_MODEL_BLOCK` | str | `''` | - | `docs/knowledge/user-model-ab.md` | `-` | no | yes | `services/zoe-data/user_portrait.py` |
| `ZOE_USE_CORE_BRAIN` | bool | `True` | - | `docs/knowledge/persona-layer.md` | `'true'` | no | NO | `services/zoe-data/brain_dispatch.py` |
| `ZOE_USE_OMNIGENT_EXECUTOR` | bool | `False` | - | `docs/research/agent-sessions-off-box-2026-10-04.md` | `'0'` | no | NO | `services/zoe-data/omnigent_issue_executor.py` |
| `ZOE_USE_PI_EXECUTOR` | bool | `False` | - | - | `'false'` | no | NO | `services/zoe-data/pi_executor.py` |
| `ZOE_VAD_CLEAN_FALL_CHUNKS` | int | `2` | - | `docs/knowledge/voice-pipeline.md` | `2` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VAD_CLEAN_MIN_SPEECH_MS` | int | `480` | - | `docs/knowledge/voice-pipeline.md` | `480` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VAD_CLEAN_TAIL_MS` | int | `0` | - | `docs/knowledge/voice-pipeline.md` | `0` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VAD_HESITATION_TAIL_MS` | int | `0` | - | `docs/knowledge/voice-pipeline.md` | `0` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VAD_SPEECH_THRESHOLD` | float | `0.5` | - | `docs/knowledge/voice-pipeline.md` | `'0.5'` | no | NO | `services/zoe-data/voice_vad.py` |
| `ZOE_VAD_TAIL_DEEP_PROB` | float | `0.1` | - | `docs/knowledge/voice-pipeline.md` | `'0.10'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VAD_TAIL_MS` | int | `0` | - | `docs/knowledge/voice-pipeline.md` | `0` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VERIFY_ON_CHALLENGE` | str | `''` | - | `docs/knowledge/conversation-quality-classes.md` | `-` | no | NO | `services/zoe-data/verify_on_challenge.py` |
| `ZOE_VOICE_ALERT_NON_PASS_RUNS` | int | `3` | - | `docs/knowledge/voice-pipeline.md` | `'3'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_BARGE_IN` | bool | `False` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `'0'` | no | NO | `services/zoe-data/routers/voice_livekit.py` |
| `ZOE_VOICE_BASELINE` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/voice_gate_check.py`<br>`scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_CHAT_TIMEOUT_S` | int | `20` | - | `docs/knowledge/voice-pipeline.md` | `'20'` | no | NO | `services/zoe-data/routers/voice_tts.py`<br>`services/zoe-data/zoe_agent.py` |
| `ZOE_VOICE_CONTINUATION_HOLD` | bool | `True` | - | `docs/knowledge/voice-pipeline.md` | `'on'` | no | NO | `services/zoe-data/voice_utterance_shape.py` |
| `ZOE_VOICE_FAST_FIRST_AUDIO` | bool | `True` | - | - | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_AFTER_S` | float | `1.6` | - | - | `'1.6'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_ENABLED` | bool | `False` | - | - | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_FILLER_EXPECT_MIN_S` | float | `1.0` | - | `services/zoe-data/AGENTS.md` | `1.0` | yes | NO | `services/zoe-data/voice_spacer.py` |
| `ZOE_VOICE_FILLER_PHRASES` | str | `''` | - | `docs/knowledge/conversation-quality-classes.md` | `''` | no | NO | `services/zoe-data/voice_spacer.py` |
| `ZOE_VOICE_GATE_MAX_AGE_H` | int | `24` | - | `docs/knowledge/voice-pipeline.md` | `'24'` | no | NO | `scripts/maintenance/voice_gate_check.py` |
| `ZOE_VOICE_GATE_PATHS` | str | `''` | - | `docs/knowledge/voice-pipeline.md` | `''` | no | NO | `scripts/maintenance/voice_gate_check.py` |
| `ZOE_VOICE_GREETING_ENABLED` | bool | `False` | - | `docs/knowledge/voice-pipeline.md` | `'0'` | no | NO | `services/zoe-data/voice_greeting.py` |
| `ZOE_VOICE_GREETING_STATE_PATH` | str | `''` | - | `services/zoe-data/AGENTS.md` | `dynamic` | no | NO | `services/zoe-data/voice_greeting.py` |
| `ZOE_VOICE_IDENT` | str | `''` | - | `docs/knowledge/feature-audit-2026-09-25.md` | `''` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_LOG` | str | `''` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VOICE_MEMORY_PACKET_LAZY` | bool | `True` | - | `docs/knowledge/voice-pipeline.md` | `'true'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_PROBE_MIN_MEM_MB` | str | `''` | - | - | `-` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_SAMPLES` | int | `20` | - | - | `'20'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_TIMEOUT_S` | int | `900` | - | - | `'900'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_USER` | str | `'jason'` | - | - | `'jason'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_VAD_CHECK` | bool | `True` | - | `docs/knowledge/voice-pipeline.md` | `'1'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROBE_VAD_CLIPS` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_PROFILE` | str | `'zoe_au_natural_v1'` | - | - | `'zoe_au_natural_v1'` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_REPLAY_STT` | str | `'inprocess'` | - | `docs/knowledge/chroma-1-5-migration.md` | `'inprocess'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_RESULTS` | str | `''` | - | `docs/knowledge/voice-pipeline.md` | `dynamic` | no | NO | `scripts/maintenance/voice_gate_check.py`<br>`scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_SAMPLE_DIR` | str | `'/home/zoe/.zoe-voice-samples'` | - | `docs/research/arousal-detection-licence-scope-2026-10-04.md` | `'/home/zoe/.zoe-voice-samples'`, `dynamic` | no | NO | `scripts/maintenance/curate_voice_corpus.py`<br>`scripts/maintenance/stt_accuracy_report.py`<br>`scripts/maintenance/voice_regression_probe.py`<br>`services/zoe-data/routers/voice_tts.py`<br>`services/zoe-data/struggle_proposals.py` |
| `ZOE_VOICE_SAVE_AUDIO` | bool | `False` | - | `docs/knowledge/voice-pipeline.md` | `False` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_SESSION` | bool | `False` | - | `docs/knowledge/voice-verified-session.md` | `False` | yes | NO | `services/zoe-data/voice_session.py` |
| `ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER` | bool | `False` | - | `docs/knowledge/voice-verified-session.md` | `False` | yes | NO | `services/zoe-data/voice_session.py` |
| `ZOE_VOICE_SESSION_TTL_S` | int | `120` | - | `docs/knowledge/voice-verified-session.md` | `120` | yes | NO | `services/zoe-data/voice_session.py` |
| `ZOE_VOICE_STITCH_ENABLED` | bool | `False` | - | `docs/knowledge/voice-pipeline.md` | `'0'` | no | NO | `services/zoe-data/main.py`<br>`services/zoe-data/voice_stitch.py` |
| `ZOE_VOICE_STREAM` | bool | `True` | - | `docs/research/inference-speech-stack-2026-10-03.md` | `'1'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ZOE_VOICE_STT_LOG` | str | `''` | - | `docs/architecture/beat-the-bar-2026-program.md` | `-` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_STT_LOG_MAX_BYTES` | int | `5000000` | - | - | `5000000` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_TOOL_FILLER` | bool | `True` | - | `docs/architecture/samantha-evolution-plan.md` | `True` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_TREND` | str | `''` | - | - | `dynamic` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_TTS_MIN_SENTENCE_CADENCE_MS` | int | `0` | - | `docs/research/incomplete-turn-marker-2026-10-04.md` | `0` | yes | NO | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_VOICE_WARN_MS` | int | `1500` | - | - | `'1500'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_VOICE_WARN_RATIO` | float | `1.5` | - | - | `'1.5'` | no | NO | `scripts/maintenance/voice_regression_probe.py` |
| `ZOE_WAKE_ACK_PHRASE` | str | `''` | - | `docs/guides/OPERATOR_RUNBOOK.md` | `-` | yes | yes | `services/zoe-data/routers/voice_tts.py` |
| `ZOE_WEATHER_CACHE_TTL_S` | int | `600` | - | `docs/research/self-building-skills-2026-10-04.md` | `'600'` | no | NO | `services/zoe-data/routers/weather.py` |
| `ZOE_WEB_BROWSE_MAX_HTML` | int | `1500000` | - | - | `1500000` | yes | NO | `services/zoe-data/zoe_agent.py` |
| `ZOE_WEB_FALLBACK_PROVIDER` | str | `'auto'` | - | `docs/architecture/beat-the-bar-2026-program.md` | `'auto'` | no | NO | `services/zoe-data/research_evidence.py` |
| `ZOE_WEB_SEARCH_TOOL` | bool | `False` | - | `docs/knowledge/self-model.md` | `'0'` | no | yes | `services/zoe-data/research_evidence.py` |
| `ZOE_WORKTREE_PRUNE_INTERVAL_S` | float | `86400.0` | - | `docs/architecture/multica-retirement-gates.md` | `86400.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_WORKTREE_ROOT` | str | `''` | - | `docs/guides/MULTICA_HERMES_PR_LOOP.md` | `''` | no | NO | `services/zoe-data/worktree_bootstrap.py` |
| `ZOE_WRITE_CLAIM_GUARD` | str | `'enforce'` | `off`, `shadow`, `enforce` | `docs/knowledge/write-claim-guard.md` | `'enforce'` | no | NO | `services/zoe-data/write_claim_guard.py` |
| `ZOE_WS_IDLE_TIMEOUT_SECONDS` | float | `120.0` | - | `docs/knowledge/voice-pipeline.md` | `120.0` | yes | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_POLL_S` | float | `2.0` | - | - | `'2.0'` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_YTMUSIC_POTOKEN_URL` | str | `'http://localhost:4416'` | - | `docs/knowledge/music-ytdlp-js-runtime.md` | `'http://localhost:4416'` | no | NO | `services/zoe-data/music_service.py` |
| `ZOE_YTMUSIC_REFRESH_ENABLED` | bool | `False` | - | `docs/knowledge/log-review-docker-2026-10-04.md` | `'false'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_REFRESH_HOURS` | int | `12` | - | `services/zoe-data/AGENTS.md` | `'12'` | no | NO | `services/zoe-data/main.py` |
| `ZOE_YTMUSIC_SECRET_DIR` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `dynamic` | no | NO | `services/zoe-data/ytmusic_signin.py` |
| `ZOE_YTMUSIC_SESSION_TIMEOUT_S` | int | `300` | - | `services/zoe-data/AGENTS.md` | `'300'` | no | NO | `services/zoe-data/ytmusic_signin.py` |

## Lab flags (`labs/` — not prod)

39 flags; 37 not documented in `.env.example`.

| Flag | Type | Default | Allowed | Owner doc | Read default(s) | typed_env | .env.example | Readers |
|---|---|---|---|---|---|---|---|---|
| `ZOE_BITEMPORAL_SUPERSEDE` | str | `''` | - | `docs/architecture/b3-1-bitemporal-supersession.md` | `-` | no | NO | `labs/b3-1-supersession/bitemporal.py` |
| `ZOE_BRAIN_ALLOW_WRITES` | bool | `False` | - | `docs/knowledge/voice-pipeline.md` | `'false'` | no | NO | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` |
| `ZOE_BRAIN_API_KEY` | str | `'local-no-key'` | - | - | `'local-no-key'` | no | NO | `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts` |
| `ZOE_BRAIN_BASE_URL` | str | `'http://127.0.0.1:11434/v1'` | - | - | `'http://127.0.0.1:11434/v1'` | no | NO | `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts` |
| `ZOE_BRAIN_CONTEXT_WINDOW` | str | `''` | - | `docs/architecture/zoe-flue-integration.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/context-window.ts` |
| `ZOE_BRAIN_DB` | str | `''` | - | `docs/architecture/beat-the-bar-2026-program.md` | `dynamic` | no | NO | `labs/flue-zoe-brain-2x/src/db.ts` |
| `ZOE_BRAIN_ELIDE_STALE_BLOCKS` | str | `''` | - | `docs/knowledge/user-model-ab.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/context-blocks.ts` |
| `ZOE_BRAIN_MAX_TOOL_ITERS` | str | `''` | - | `docs/research/flue-and-agent-runtimes-2026-10-03.md` | `-` | no | NO | `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts` |
| `ZOE_BRAIN_OPEN` | str | `''` | - | - | `-` | no | NO | `labs/flue-zoe-brain-2x/src/auth.ts` |
| `ZOE_BRAIN_PROGRESSIVE_TOOLS` | bool | `True` | - | `docs/architecture/zoe-flue-integration.md` | `'true'` | no | NO | `labs/flue-zoe-brain-2x/src/tools/tool-groups.ts` |
| `ZOE_BRAIN_REPLY_RESERVE` | str | `''` | - | `docs/architecture/zoe-flue-integration.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/context-window.ts` |
| `ZOE_BRAIN_STICKY_DISCLOSURE` | bool | `True` | - | `docs/knowledge/voice-pipeline.md` | `'true'` | no | NO | `labs/flue-zoe-brain-2x/src/tools/tool-groups.ts` |
| `ZOE_BRAIN_STREAM` | str | `''` | - | - | `''` | no | NO | `labs/flue-zoe-brain-2x/src/streaming.ts` |
| `ZOE_BRAIN_STREAM_TIMEOUT_S` | str | `''` | - | - | `-` | no | NO | `labs/flue-zoe-brain-2x/src/streaming.ts` |
| `ZOE_BRAIN_TEMPERATURE` | str | `''` | - | `docs/research/inference-speech-stack-2026-10-03.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts` |
| `ZOE_BRAIN_TOKEN` | str | `''` | - | `docs/knowledge/self-model.md` | `''`, `-` | no | NO | `labs/flue-zoe-brain-2x/parity/flue_wire.py`<br>`labs/flue-zoe-brain-2x/src/auth.ts` |
| `ZOE_BRAIN_TOOL_TIMEOUT_MS` | str | `''` | - | - | `-` | no | NO | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` |
| `ZOE_BRAIN_URL` | str | `''` | - | - | `'http://127.0.0.1:3579'` | no | NO | `labs/flue-zoe-brain-2x/parity/flue_wire.py` |
| `ZOE_BRAIN_USER_ID` | str | `''` | - | `docs/knowledge/user-model-ab.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts`<br>`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` |
| `ZOE_DATA_URL` | str | `'http://127.0.0.1:8000'` | - | `docs/research/telegram-calls-voice-photos-2026-10-04.md` | `'http://127.0.0.1:8000'`, `dynamic` | no | NO | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`<br>`labs/flue-zoe-brain-2x/src/user-model.ts`<br>`labs/flue-zoe-telegram-2x/src/brain.ts` |
| `ZOE_ENV_FILE` | str | `'/home/zoe/assistant/services/zoe-data/.env'` | - | - | `dynamic` | no | NO | `labs/flue-executor/src/config.ts` |
| `ZOE_EXECUTOR_DISPATCH` | str | `'dry'` | - | `docs/architecture/multica-executor-migration.md` | `'dry'` | no | NO | `labs/flue-executor/src/config.ts` |
| `ZOE_EXECUTOR_MODE` | str | `'lab'` | - | `docs/architecture/multica-executor-migration.md` | `'lab'` | no | NO | `labs/flue-executor/src/config.ts` |
| `ZOE_EXECUTOR_RUNTIME_NAME` | str | `''` | - | - | `dynamic` | no | NO | `labs/flue-executor/src/config.ts` |
| `ZOE_FLUE_ABORT_GUARD` | str | `''` | - | `docs/knowledge/voice-pipeline.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/turn-guard.ts` |
| `ZOE_FLUE_EARLY_TEXT` | str | `''` | - | `docs/knowledge/panel-ttfa-breakdown-2026-09-28.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/early-text.ts` |
| `ZOE_HARNESS_DISPOSABLE_CHECKOUT` | str | `''` | - | - | `-` | no | NO | `labs/flue-harness-spike/src/github.ts` |
| `ZOE_INTERNAL_TOKEN` | str | `''` | - | `docs/knowledge/incident-runbook.md` | `''` | no | yes | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`<br>`labs/flue-zoe-brain-2x/src/user-model.ts`<br>`labs/flue-zoe-telegram-2x/src/brain.ts` |
| `ZOE_KOKORO_VOICES` | str | `''` | - | `docs/knowledge/voice-pipeline.md` | `'/home/zoe/models/voices-v1.0.bin'` | no | NO | `labs/kokoro-voice-blend/blend_zoe_voices.py` |
| `ZOE_LIVE_ROOT` | str | `''` | - | `docs/research/samantha-flags-ab-2026-10-06.md` | `-` | no | NO | `labs/flue-zoe-brain-2x/parity/gatelib.py` |
| `ZOE_MULTICA_KILL_SWITCH` | str | `''` | - | - | `'/nonexistent-lab-never-idles'`, `dynamic` | no | NO | `labs/flue-executor/src/config.ts` |
| `ZOE_PGU_SID` | str | `''` | - | - | `dynamic` | no | NO | `labs/flue-zoe-brain-2x/parity/tool_breadth_gate.py` |
| `ZOE_ROUTER_HEAD_LOG` | str | `''` | - | `docs/research/failure-fed-proposals-2026-10-04.md` | `dynamic` | no | NO | `labs/router-selftrain/mine_candidates.py` |
| `ZOE_ROUTER_SIDECAR_PORT` | int | `11436` | - | - | `'11436'` | no | NO | `labs/router-90-campaign/prod_path_eval.py` |
| `ZOE_TELEGRAM_DB` | str | `''` | - | - | `dynamic` | no | NO | `labs/flue-zoe-telegram-2x/src/db.ts` |
| `ZOE_TELEGRAM_TRIAL` | str | `''` | - | - | `-` | no | NO | `labs/flue-zoe-telegram-2x/src/brain.ts` |
| `ZOE_TIMEZONE` | str | `'Australia/Perth'` | - | `docs/knowledge/synthetic-users-and-proactive-recipients.md` | `''` | no | NO | `labs/flue-zoe-brain-2x/src/local-time.ts` |
| `ZOE_USER_MODEL_TTL_MS` | str | `''` | - | `docs/knowledge/user-model-ab.md` | `-` | no | NO | `labs/flue-zoe-brain-2x/src/user-model.ts` |
| `ZOE_WEB_SEARCH_TOOL` | bool | `False` | - | `docs/knowledge/self-model.md` | `'0'` | no | yes | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` |

## Non-`ZOE_` env names read by prod code

245 names. These gate live paths too but are not `ZOE_*` flags, so they are not in the registry; listed so they are visible. (`typed_env` = read through an accessor.)

| Name | Default(s) | typed_env | .env.example | Readers |
|---|---|---|---|---|
| `ACTIVATE_ALLOWED_ORIGINS` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `ACTIVATE_GUARD` | `'on'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `AMBIENT_CAPTURE_ENABLED` | `'false'` | no | yes | `scripts/setup/zoe_voice_daemon.py` |
| `AMBIENT_MIN_SPEECH_MS` | `'500'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `AMBIENT_SILENCE_PAD_MS` | `'800'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `AMBIENT_VAD_THRESHOLD` | `'0.4'` | no | yes | `scripts/setup/zoe_voice_daemon.py` |
| `API_SERVER_KEY` | `-` | no | NO | `services/zoe-data/chat_hermes_stream.py`<br>`services/zoe-data/hermes_http.py` |
| `AUDIO_OUTPUT_DEVICE` | `dynamic` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `BAKEOFF_BLACKOUTS` | `-` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_BUDDYINFO` | `'/proc/buddyinfo'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_CAP_MIN` | `'90'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_DEEP_UNIT` | `'/home/zoe/.config/systemd/user/llama-server-12b-deepbrain.service.disabled'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_DIR` | `'/home/zoe/.zoe/bakeoff-2026-10'` | no | NO | `scripts/perf/zmb/bakeoff.py`<br>`scripts/perf/zmb/ram_opt.py` |
| `BAKEOFF_DROP_CACHES` | `'1'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_HARNESS_LOCK` | `'/tmp/zoe-voice-harness.lock'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_HEALTH_WAIT_S` | `'180'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_HM_SHARED_EMBEDDER` | `'1'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_HS_QUIET_WAIT_S` | `'60'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_KEEP_PG` | `-` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_LEAN` | `'1'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_LIVE_PORT` | `'11434'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_LIVE_UNIT` | `'llama-server.service'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_LOCK` | `'/tmp/zoe-brain-window.lock'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_MEMINFO` | `'/proc/meminfo'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_MIN_AVAIL_MB` | `'1200'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_MIN_CONTIG_BLOCKS` | `'64'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_PANEL_HOST` | `'zoe-pi'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_QUIET_POLL_S` | `'60'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_QUIET_S` | `'600'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_QUIET_WAIT_MIN` | `'180'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_REFLECT_CTX` | `'32768'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_REFLECT_STOP_UNITS` | `''` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_SHIM_MODEL` | `'auto'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_SKIP_BRAIN_STOP` | `-` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_SMOKE_CELLS` | `'0'` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BAKEOFF_Z0N` | `-` | no | NO | `scripts/perf/zmb/bakeoff.py` |
| `BARGE_DUCK_DB` | `-` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `BARGE_DUCK_ENABLED` | `'false'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `BARGE_IN_ENABLED` | `'true'` | no | yes | `scripts/setup/zoe_voice_daemon.py` |
| `BARGE_SEED_NEXT_TURN` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CF_ACCESS_CLIENT_ID` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CF_ACCESS_CLIENT_SECRET` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CHROME_PATH` | `dynamic` | no | NO | `scripts/maintenance/panel_screenshots.py` |
| `CHUNK_SIZE` | `'1280'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CONV_BEEP` | `'first'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CONV_MAX_S` | `'300'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CONV_MAX_TURNS` | `'40'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CONV_SILENT_WINDOWS` | `'2'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `CONV_WINDOW_S` | `'12.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `DATABASE_PATH` | `'/app/data/zoe.db'`, `'/home/zoe/assistant/data/zoe.db'` | no | NO | `scripts/maintenance/migrate_lists_enhancements.py`<br>`scripts/maintenance/migrate_project_lists.py` |
| `DESKTOP_GATE_PIN` | `(required)` | no | NO | `scripts/maintenance/desktop_browser_verify.py` |
| `DEVICE_TOKEN` | `''`, `-`, `dynamic` | no | NO | `scripts/maintenance/zoe_latency_probe.py`<br>`scripts/setup/zoe_enroll_flow.py`<br>`scripts/setup/zoe_face_id.py`<br>`scripts/setup/zoe_voice_daemon.py` |
| `EGRESS_AUDIT_LOG` | `'/home/zoe/.zoe/bakeoff-2026-10/egress-audit.log'` | no | NO | `scripts/perf/zmb/egress_audit/sitecustomize.py` |
| `ENVIRONMENT` | `-` | no | NO | `services/zoe-auth/main.py` |
| `FACE_CAMERA_INDEX` | `'0'` | no | NO | `scripts/setup/zoe_enroll_flow.py`<br>`scripts/setup/zoe_face_id.py` |
| `FACE_CAPTURE_FRAMES` | `'4'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_CAPTURE_SPAN_S` | `'0.8'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_DET_THRESHOLD` | `'0.5'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_ID_ENABLED` | `'false'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_ID_SYNC_TTL_S` | `'3600'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_MIN_PX` | `'60'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FACE_MODEL_DIR` | `'~/.zoe-voice/models'` | no | NO | `scripts/setup/zoe_face_id.py` |
| `FASTEMBED_CACHE_PATH` | `(required)`, `-` | no | NO | `scripts/perf/zmb/embed_shim.py` |
| `FOLLOWUP_LOOKBACK_CHUNKS` | `'4'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `FOLLOW_UP_LISTEN_S` | `'5.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `FOLLOW_UP_MAX_TURNS` | `'5'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `FOLLOW_UP_VAD_THRESHOLD` | `'0.35'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `FORCE_FULL_CONTEXT` | `'false'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `GEMMA_SERVER_URL` | `''`, `'http://127.0.0.1:11434'`, `'http://127.0.0.1:11434/v1'`, `-`, `dynamic` | no | NO | `scripts/maintenance/music_discovery_batch.py`<br>`scripts/maintenance/zoe-struggle-proposals.py`<br>`scripts/night/jobs/night_digest.py`<br>`scripts/perf/measure_speed.py`<br>`services/zoe-core/bench/pi_brain_latency.py`<br>`services/zoe-data/gemma_endpoint.py`<br>`services/zoe-data/memory_digest.py`<br>`services/zoe-data/night_mind.py`<br>`services/zoe-data/omi_day_review.py`<br>`services/zoe-data/ui_compose.py`<br>`services/zoe-data/zoe_agent.py` |
| `GITHUB_OUTPUT` | `-` | no | NO | `scripts/maintenance/voice_gate_check.py` |
| `GREPTILE_API_KEY` | `-` | no | NO | `services/zoe-data/greptile_client.py` |
| `GREPTILE_ENV_FILE` | `dynamic` | no | NO | `services/zoe-data/greptile_client.py` |
| `GREPTILE_MCP_BIN` | `''` | no | NO | `services/zoe-data/executors/kanban_adapter.py` |
| `GREPTILE_MCP_URL` | `'https://api.greptile.com/mcp'` | no | NO | `services/zoe-data/greptile_client.py` |
| `HARDWARE_PLATFORM` | `'unknown'` | no | NO | `scripts/maintenance/migrate_lists_enhancements.py`<br>`scripts/maintenance/migrate_project_lists.py` |
| `HA_ACCESS_TOKEN` | `''` | no | yes | `services/homeassistant-mcp-bridge/main.py` |
| `HA_BASE_URL` | `'http://homeassistant.local:8123'`, `'http://homeassistant:8123'` | no | NO | `services/homeassistant-mcp-bridge/main.py`<br>`services/zoe-data/main.py` |
| `HA_BRIDGE_URL` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `HA_OIDC_CLIENT_ID` | `'home-assistant'` | no | yes | `services/zoe-auth/oidc/startup.py` |
| `HA_OIDC_CLIENT_SECRET` | `''` | no | yes | `services/zoe-auth/oidc/startup.py` |
| `HEALTH_BIND` | `dynamic` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `HEALTH_PORT` | `'7777'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `HERMES_API_KEY` | `dynamic` | no | yes | `services/zoe-data/chat_hermes_stream.py`<br>`services/zoe-data/hermes_http.py` |
| `HERMES_API_URL` | `'http://127.0.0.1:8642'`, `'http://127.0.0.1:8642/v1/chat/completions'` | no | NO | `services/zoe-data/chat_hermes_stream.py`<br>`services/zoe-data/proactive/triggers/openclaw_trigger.py`<br>`services/zoe-data/routers/system.py` |
| `HERMES_BACKGROUND_QUEUE_WAIT_S` | `1200.0` | yes | NO | `services/zoe-data/background_runner.py` |
| `HERMES_BACKGROUND_TIMEOUT_S` | `900.0` | yes | NO | `services/zoe-data/background_runner.py` |
| `HERMES_BIN` | `''` | no | NO | `services/zoe-data/hermes_http.py` |
| `HERMES_FAST_PATH` | `'true'` | no | NO | `services/zoe-data/main.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/chat.py` |
| `HERMES_HOME` | `'~/.hermes'` | no | NO | `services/zoe-data/hermes_model_profiles.py`<br>`services/zoe-data/kanban_phase_budget.py` |
| `HERMES_KANBAN_DB` | `dynamic` | no | NO | `scripts/maintenance/reset_engineering_boards.py` |
| `HERMES_MODEL` | `''`, `'hermes'`, `'hermes-agent'` | no | NO | `services/zoe-data/background_runner.py`<br>`services/zoe-data/chat_hermes_stream.py`<br>`services/zoe-data/proactive/triggers/openclaw_trigger.py`<br>`services/zoe-data/routers/system.py` |
| `HERMES_MULTICA_AGENT_ID` | `''` | no | NO | `services/zoe-data/multica_client.py` |
| `HOME` | `''`, `'/'`, `'/home/zoe'`, `'~'` | no | NO | `scripts/night/night_window.py`<br>`scripts/perf/manner_block_ab.py`<br>`scripts/perf/zmb/arms/mpa_palace.py`<br>`scripts/perf/zmb/bakeoff.py`<br>`scripts/perf/zmb/bakeoff_measure.py`<br>`scripts/perf/zmb/embed_shim.py` |
| `JETSON_AGENT_MODE` | `'false'` | no | NO | `services/zoe-data/main.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/chat.py`<br>`services/zoe-data/zoe_agent.py` |
| `KOKORO_PROBE_HEALTH_URL` | `'http://127.0.0.1:10201/health'` | no | NO | `scripts/perf/kokoro_first_chunk_probe.py` |
| `KOKORO_SIDECAR_PORT` | `'10201'` | no | NO | `scripts/setup/kokoro_sidecar.py` |
| `KOKORO_VOICE` | `'af_sky'` | no | NO | `scripts/setup/kokoro_sidecar.py`<br>`services/zoe-data/voice_settings.py` |
| `LITELLM_API_KEY` | `-` | no | yes | `scripts/utilities/comprehensive_integrated_test.py`<br>`scripts/utilities/comprehensive_system_optimization.py` |
| `LIVEKIT_API_KEY` | `''`, `-` | yes | yes | `services/zoe-data/routers/skybridge.py`<br>`services/zoe-data/routers/voice_livekit.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `LIVEKIT_API_SECRET` | `''`, `-` | yes | yes | `services/zoe-data/routers/skybridge.py`<br>`services/zoe-data/routers/voice_livekit.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `LIVEKIT_TURN_URL` | `''` | no | NO | `services/zoe-data/routers/voice_tts.py` |
| `LIVEKIT_URL` | `-` | yes | yes | `services/zoe-data/routers/skybridge.py`<br>`services/zoe-data/routers/voice_tts.py` |
| `LLAMA_SERVER_URL` | `'http://127.0.0.1:11434'`, `'http://127.0.0.1:11434/v1'` | no | NO | `scripts/perf/measure_speed.py`<br>`services/zoe-data/intent_classifier_llm.py` |
| `LOCAL_UPLOAD_BASE_URL` | `-` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `LOCAL_UPLOAD_DIR` | `-` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `LOG_LEVEL` | `'INFO'` | no | NO | `scripts/setup/touchscreen/zoe-kiosk-gate.py` |
| `MALLOC_PERTURB_` | `''`, `-` | no | NO | `scripts/perf/zmb/arms/mempalace_verbatim.py`<br>`services/zoe-data/memory_residue.py` |
| `MANNER_AB_NODE` | `'node'` | no | NO | `scripts/perf/manner_block_ab.py` |
| `MEMORY_AUTO_INGEST` | `'false'` | no | NO | `services/zoe-data/routers/chat.py` |
| `MEMORY_CONSOLIDATION_ENABLED` | `'true'` | no | NO | `services/zoe-data/routers/system.py` |
| `MEMORY_DIGEST_ENABLED` | `'true'` | no | NO | `services/zoe-data/routers/system.py` |
| `MEMORY_DIGEST_MODEL` | `''`, `'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf'` | no | NO | `scripts/maintenance/zoe-struggle-proposals.py`<br>`services/zoe-data/contact_backfill.py`<br>`services/zoe-data/latent_intent_detector.py`<br>`services/zoe-data/memory_authority.py`<br>`services/zoe-data/memory_digest.py`<br>`services/zoe-data/memory_retire.py`<br>`services/zoe-data/night_mind.py`<br>`services/zoe-data/omi_day_review.py`<br>`services/zoe-data/person_extractor_llm.py`<br>`services/zoe-data/structural_reader.py`<br>`services/zoe-data/structural_verifier.py`<br>`services/zoe-data/user_portrait.py` |
| `MEMPALACE_DATA_DIR` | `'~/.mempalace'`, `(required)`, `-`, `dynamic` | no | NO | `scripts/maintenance/memory_authority_backfill.py`<br>`scripts/perf/recall_gate_replay.py`<br>`services/zoe-data/live_store_guard.py`<br>`services/zoe-data/mcp_server.py`<br>`services/zoe-data/memory_service.py`<br>`services/zoe-data/zoe_agent.py` |
| `MEMPALACE_PATH` | `'/home/zoe/.mempalace'` | no | NO | `scripts/maintenance/check_emotional_thread.py` |
| `MIN_WAKE_INTERVAL_S` | `'3.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `MULTICA_API_PROXY_HEALTH_URL` | `'http://127.0.0.1/multica-api/health'` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `MULTICA_API_TOKEN` | `''`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`services/zoe-data/multica_autopilot_sync.py`<br>`services/zoe-data/multica_client.py` |
| `MULTICA_BASE_URL` | `''`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`services/zoe-data/multica_autopilot_sync.py`<br>`services/zoe-data/multica_client.py` |
| `MULTICA_DATABASE_URL` | `''`, `-` | no | NO | `scripts/maintenance/retire_hermes_openclaw_board.py`<br>`services/zoe-data/executors/executor_queue_backend.py`<br>`services/zoe-data/multica_board_runner.py` |
| `MULTICA_DB_NAME` | `'multica'` | no | NO | `scripts/maintenance/reset_engineering_boards.py`<br>`services/zoe-data/executors/executor_queue_backend.py` |
| `MULTICA_OIDC_CLIENT_ID` | `'multica'`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`services/zoe-auth/oidc/startup.py` |
| `MULTICA_OIDC_CLIENT_SECRET` | `''`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`services/zoe-auth/oidc/startup.py` |
| `MULTICA_OIDC_DISCOVERY_URL` | `'http://127.0.0.1/.well-known/openid-configuration'` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `MULTICA_PROXY_URL` | `'http://127.0.0.1/multica/'` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `MULTICA_REQUIRE_EMAIL` | `'false'` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `MULTICA_WEBHOOK_SECRET` | `''`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`scripts/maintenance/multica_reassign_open_to_hermes.py`<br>`scripts/maintenance/setup_multica_webhooks.py`<br>`services/zoe-data/multica_webhook_emitter.py`<br>`services/zoe-data/routers/system.py` |
| `MULTICA_WEBHOOK_TARGET_URL` | `'http://127.0.0.1:8000/api/agent/board/webhook'` | no | NO | `services/zoe-data/multica_webhook_emitter.py` |
| `MULTICA_WEB_URL` | `'http://127.0.0.1:3000'` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `MULTICA_WORKSPACE_ID` | `''`, `-` | no | NO | `scripts/maintenance/multica_health_report.py`<br>`services/zoe-data/multica_autopilot_sync.py`<br>`services/zoe-data/multica_client.py` |
| `MUSIC_ASSISTANT_TOKEN` | `''` | no | NO | `scripts/maintenance/ma_idle_reap.py`<br>`services/zoe-data/intent_router.py`<br>`services/zoe-data/music_oauth.py`<br>`services/zoe-data/music_service.py`<br>`services/zoe-data/routers/music.py` |
| `MUSIC_ASSISTANT_URL` | `'http://localhost:8095'` | no | NO | `scripts/maintenance/ma_idle_reap.py`<br>`services/zoe-data/intent_router.py`<br>`services/zoe-data/ma_ondemand.py`<br>`services/zoe-data/music_oauth.py`<br>`services/zoe-data/music_service.py`<br>`services/zoe-data/routers/music.py` |
| `N8N_API_KEY` | `''` | no | NO | `services/zoe-auth/sso/n8n.py` |
| `NIGHT_BATCH` | `'512'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_CACHE_RAM_MIB` | `'512'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_CAP_MIN` | `'65'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_CTX` | `'8192'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_DIR` | `dynamic` | no | NO | `scripts/night/night_window.py`<br>`scripts/perf/tool_use_bench.py`<br>`services/zoe-data/self_model.py` |
| `NIGHT_END_BY` | `'03:55'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_FIT_OFF` | `'1'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_JOB_RESERVE_MIB` | `'700'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_KV` | `'q8_0'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_LLAMA_BINARY` | `-`, `dynamic` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_MARGIN_MIB` | `'0'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_MIND_CMD` | `''` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_MIN_AVAIL_MB` | `'1200'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_MLOCK` | `-` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_NGL` | `'34'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_NO_UNIFIED` | `-` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_PORT` | `'11500'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_QUIET_WAIT_MIN` | `'30'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_REPORT_DIR` | `dynamic` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_UBATCH` | `'128'` | no | NO | `scripts/night/night_window.py` |
| `NIGHT_ZOE_DATA_DIR` | `dynamic` | no | NO | `scripts/night/jobs/night_digest.py`<br>`scripts/night/jobs/night_precheck.py` |
| `OMNIGENT_CONTAINER` | `'zoe-omnigent'` | no | NO | `scripts/maintenance/omnigent_claude_auth_mode.py` |
| `OMNIGENT_OIDC_CLIENT_ID` | `'omnigent'` | no | NO | `services/zoe-auth/oidc/startup.py` |
| `OMNIGENT_OIDC_CLIENT_SECRET` | `''` | no | NO | `services/zoe-auth/oidc/startup.py` |
| `OMNIGENT_SERVER` | `'http://127.0.0.1:6767'` | no | NO | `scripts/maintenance/reap_stale_omnigent_runners.py` |
| `OPENCLAW_AGENT_TIMEOUT_S` | `'900'` | no | yes | `services/zoe-data/openclaw_ws.py`<br>`services/zoe-data/zoe_acp_client.py` |
| `OPENCLAW_ALLOW_AUTO_UPDATE` | `''` | no | yes | `services/zoe-data/openclaw_maintenance.py` |
| `OPENCLAW_ALL_TOOLS_ENABLED` | `'true'` | no | NO | `services/zoe-data/routers/chat.py` |
| `OPENCLAW_BACKGROUND_VERSION_CHECK` | `'false'` | no | yes | `services/zoe-data/routers/system.py` |
| `OPENCLAW_BIN` | `'openclaw'` | no | NO | `services/zoe-data/openclaw_manager.py` |
| `OPENCLAW_DIR` | `dynamic` | no | NO | `services/zoe-data/openclaw_manager.py` |
| `OPENCLAW_GATEWAY_HEALTH_URL` | `'http://127.0.0.1:18789/health'` | no | NO | `services/zoe-data/openclaw_maintenance.py` |
| `OPENCLAW_GATEWAY_TOKEN` | `''` | no | yes | `services/zoe-data/openclaw_ws.py`<br>`services/zoe-data/zoe_acp_client.py` |
| `OPENCLAW_GATEWAY_URL` | `'http://127.0.0.1:18789'` | no | NO | `services/zoe-data/routers/chat.py`<br>`services/zoe-data/routers/openclaw.py` |
| `OPENCLAW_GUARDED_AUTO` | `'true'` | no | NO | `services/zoe-data/routers/chat.py` |
| `OPENCLAW_UPGRADE_ENABLED` | `'true'` | no | yes | `services/zoe-data/routers/system.py` |
| `OPENROUTER_API_KEY` | `-` | no | NO | `scripts/maintenance/zoe_cheap_pr_agent.py` |
| `OPENWEATHERMAP_API_KEY` | `''` | no | yes | `services/zoe-data/mcp_server.py`<br>`services/zoe-data/routers/weather.py` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `'http://localhost:6006/v1/traces'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `PANEL_ID` | `'zoe-touch-pi'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `PANEL_PLATFORM` | `-` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `PATH` | `''` | no | NO | `scripts/perf/manner_block_ab.py` |
| `PI_USER_FACTS_CACHE_TTL_S` | `'120.0'` | no | NO | `services/zoe-data/zoe_agent.py` |
| `PORTRAIT_MAX_INJECT_CHARS` | `'600'` | no | NO | `services/zoe-data/user_portrait.py` |
| `PORTRAIT_MIN_MEMORIES` | `'5'` | no | NO | `services/zoe-data/user_portrait.py` |
| `PORTRAIT_PROMPT_BUDGET_TOKENS` | `'5500'` | no | NO | `services/zoe-data/user_portrait.py` |
| `POSTGRES_APSCHEDULER_URL` | `''` | no | yes | `services/zoe-data/proactive/scheduler.py` |
| `POSTGRES_URL` | `''`, `'postgresql://zoe:CHANGE_ME@localhost:5432/zoe'`, `'postgresql://zoe:zoe@zoe-database:5432/zoe'`, `-`, `dynamic` | no | yes | `scripts/maintenance/provision_parity_test_user.py`<br>`scripts/maintenance/purge_orphaned_test_data.py`<br>`scripts/maintenance/retire_hermes_openclaw_board.py`<br>`scripts/maintenance/verify_executor_queue_backend.py`<br>`scripts/maintenance/voice_regression_probe.py`<br>`scripts/setup/migrate_auth_data.py`<br>`services/zoe-auth/models/database.py`<br>`services/zoe-data/alembic/env.py`<br>`services/zoe-data/database.py`<br>`services/zoe-data/db_pool.py`<br>`services/zoe-data/executors/executor_queue_backend.py`<br>`services/zoe-data/multica_board_runner.py` |
| `POST_PLAY_TAIL_S` | `'0.4'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `PREROLL_CHUNKS` | `'20'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `PYTEST_CURRENT_TEST` | `dynamic` | no | NO | `services/zoe-data/live_store_guard.py` |
| `PYTHONMALLOC` | `-` | no | NO | `scripts/perf/zmb/arms/mempalace_verbatim.py` |
| `RECORD_SILENCE_AMPLITUDE` | `'300'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `REDIS_HOST` | `'zoe-redis'` | no | NO | `services/zoe-auth/api/dependencies.py` |
| `REDIS_PORT` | `'6379'` | no | NO | `services/zoe-auth/api/dependencies.py` |
| `RESEND_API_KEY` | `dynamic` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `ROPT_FLOOR_MB` | `'1500'` | no | NO | `scripts/perf/zmb/ram_opt.py` |
| `SAMPLE_RATE` | `'16000'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SILENCE_TIMEOUT_S` | `'1.5'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SMTP_HOST` | `-` | no | NO | `scripts/maintenance/multica_health_report.py` |
| `SPEAKER_ADAPT_MIN_GAP_S` | `'60'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ADAPT_MIN_SCORE` | `'0.85'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ADAPT_MIN_SPEECH_S` | `'2.0'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ADAPT_RESNET34_MIN_SCORE` | `dynamic` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ADAPT_SEND` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ID_ENABLED` | `'false'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ID_SHADOW` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ID_SHADOW_LOG` | `dynamic` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_ID_SYNC_TTL_S` | `'3600'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `SPEAKER_RESNET34_MODEL_DIR` | `dynamic` | no | NO | `scripts/setup/zoe_speaker_resnet34.py` |
| `SPEAKER_SHADOW_BOOT` | `'300'` | no | NO | `scripts/voice/speaker_shadow_eval.py` |
| `SPEAKER_SHADOW_CORPUS` | `-` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_DIR` | `dynamic` | no | NO | `scripts/voice/speaker_shadow_embed.py`<br>`scripts/voice/speaker_shadow_eval.py` |
| `SPEAKER_SHADOW_FOLDS` | `'5'` | no | NO | `scripts/voice/speaker_shadow_eval.py` |
| `SPEAKER_SHADOW_HEALTH_EVERY_S` | `'60'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_HEALTH_URL` | `'http://127.0.0.1:7777/health'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_MAX_S` | `'30'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_MIN_S` | `'0.5'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_MODEL` | `'campplus_en_voxceleb'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_SEED` | `'20261005'` | no | NO | `scripts/voice/speaker_shadow_eval.py` |
| `SPEAKER_SHADOW_SESSION_GAP_S` | `'1800'` | no | NO | `scripts/voice/speaker_shadow_eval.py` |
| `SPEAKER_SHADOW_THREADS` | `dynamic` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SPEAKER_SHADOW_WARMUP` | `'3'` | no | NO | `scripts/voice/speaker_shadow_embed.py` |
| `SQLITE_PATH` | `'./data/zoe.db'` | no | NO | `scripts/setup/migrate_auth_data.py` |
| `TAVILY_API_KEY` | `''` | yes | yes | `services/zoe-data/web_search_provider.py` |
| `VAD_ENDPOINT_ENABLED` | `'false'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VAD_ENDPOINT_SILENCE_S` | `'0.8'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VAD_ENDPOINT_THRESHOLD` | `'0.35'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VERIFY_SSL` | `'true'` | no | NO | `scripts/setup/zoe_enroll_flow.py`<br>`scripts/setup/zoe_face_id.py`<br>`scripts/setup/zoe_voice_daemon.py` |
| `VOICE_API_MAX_RETRIES` | `'2'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VOICE_API_RETRY_BACKOFF_S` | `'0.35'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VOICE_IGNORE_TRANSCRIPTS` | `'yes,yeah,yep,yup,no,ok,okay,thank you,thanks,hi,hello,hmm,um'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `VOICE_ROUTE_MODE` | `'direct'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKEWORD_DEBUG` | `''` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKEWORD_FRAMEWORK` | `''` | no | NO | `scripts/setup/mac_panel/mac_backend.py` |
| `WAKE_BEEP_DURATION_MS` | `'120'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKE_BEEP_ENABLED` | `'true'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKE_BEEP_FREQ_HZ` | `'1046'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKE_BEEP_VOLUME` | `'0.22'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKE_CONFIRM_COUNT` | `'2'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WAKE_CONFIRM_WINDOW_S` | `'0.8'` | no | NO | `scripts/setup/zoe_voice_daemon.py` |
| `WHATSAPP_FLOW_ENABLED` | `'true'` | no | NO | `services/zoe-data/routers/chat.py` |
| `XDG_RUNTIME_DIR` | `dynamic` | no | NO | `scripts/maintenance/zoe_agents_sampler.py` |
| `ZMB_EMBED_MODEL` | `'auto'` | no | NO | `scripts/perf/zmb/embed_shim.py` |
| `ZMB_EMBED_MODEL_DIR` | `-` | no | NO | `scripts/perf/zmb/embed_shim.py` |
| `ZMB_HINDSIGHT_URL` | `'http://127.0.0.1:18888'` | no | NO | `scripts/perf/zmb/arms/hindsight.py` |
| `ZMB_HM_EMBEDDER_MODEL` | `''` | no | NO | `scripts/perf/zmb/arms/mempalace_verbatim.py` |
| `ZMB_HM_EMBEDDER_URL` | `''`, `'local MiniLM session'` | no | NO | `scripts/perf/zmb/arms/mempalace_verbatim.py`<br>`scripts/perf/zmb/mpa_window.py`<br>`scripts/perf/zmb/pilot/hm_ram_probe.py` |
| `ZMB_LEAN_STUBS` | `''` | no | NO | `scripts/perf/zmb/lean_imports/sitecustomize.py` |
| `ZMB_ORT_OPT` | `'basic'` | no | NO | `scripts/perf/zmb/embed_shim.py` |
| `ZMB_ORT_THREADS` | `'2'` | no | NO | `scripts/perf/zmb/embed_shim.py` |
| `ZMB_PROBE_NEEDLE` | `''` | no | NO | `scripts/perf/zmb/arms/mempalace_verbatim.py` |
| `ZMB_REAL_HOME` | `dynamic` | no | NO | `scripts/perf/zmb/arms/mpa_palace.py` |

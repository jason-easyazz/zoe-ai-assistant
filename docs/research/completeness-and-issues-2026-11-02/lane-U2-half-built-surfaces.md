# Half-built surfaces (lane 4 / U2)

Snapshot: origin/main (tree extracted to scratchpad/snap), static grep/AST; live box read only for `systemctl --user` and `apscheduler_jobs`. Scripts: scratchpad/u2/{routes,refs,fe,tables}.py.
Method limits: "unreferenced" = no literal match of the route's static prefix in dist html/js (excl. lib/, workbox/, _preview/), labs/flue-zoe-brain-2x+telegram-2x src, scripts/ (all), services/{omi-sync,panel-bluetooth,zoe-core,zoe-auth,...}, nginx. Routes called only by the native iPhone app or by in-process Python are NOT visible, and are marked as such. Reader detection for tables is `FROM/JOIN` text, so dynamic SQL is a false-negative risk.

## 1. Backend routes with no referrer
492 route decorators (routers/*.py + main.py; per-variable prefixes resolved). 159 (32%) have no referrer anywhere; 36 more are referenced only from scripts/ (ops), not the UI.

Per router (unref/total):
system.py 52/71 | music.py 13/32 | alarms 7/7 | contacts 7/7 | persona 7/7 | phone_app 7/7 | memories 6/24 | proactive 5/11 | main.py 5/15 | change_notices 4/4 | journal 4/9 | portrait 4/6 | struggles 4/8 | weather 4/8 | panel_auth 3/12 | chat 2/14 | enrollment 2/4 | music_setup 2/10 | omi_recordings 2/14 | omi_voices 2/14 | reminders 2/9 | ui_actions 2/12 | autoresearch 1/1 | skybridge 1/4 (+ 1 each: calendar, capability_matrix, dashboard, lists, panel_relay, people, smart_home_setup, stubs, transactions, voice_livekit, voice_speaker_adapt, voice_tts).
(ha_ui 2/2 looked dead but nginx `auth_request` uses `/api/ha-ui/gate`, services/zoe-ui/nginx.d/locations.inc:220: live.)

Routers whose ENTIRE surface is unreferenced (flag state from live .env):
- alarms.py (7 routes, :55-124), ZOE_ALARMS off. contacts.py (7, :55-120), ZOE_CONTACTS_API off. persona.py (7, :77-162), ZOE_PERSONA_LAYER off. phone_app.py (7, :66-128), ZOE_PHONE_APP off, consumer is the unwritten native iPhone app. All 404 when off: dark, not dead, but no front end exists for 3 of the 4.
- change_notices.py (4, :29-63): ZOE_CHANGE_NOTICES=on LIVE, run_loop started main.py:1262, but no page reads/writes preferences or status.
- autoresearch.py (1, :62 `/api/autoresearch/status`): no flag, no referrer.

system.py unreferenced clusters (52): pi-intent/* (8, :245-369) + pi_intent_lab router; hermes/model-profiles/* (6, :470-547); openclaw/preferences+upgrade (3, :561-627); engineering/tasks+guard (5, :1304-1387); board/* (4, :1676-1857); evolution/* (5, :1922-2425, duplicates /api/agent/evolution which the UI does call); tasks/* (3, :1163-1259); registry, squad, runtimes, costs, llm-stats, peers card (7); memory-loops, memory-reconcile, memories/consolidate+digest, run-digest (5); display/brightness (:2706), updates/history (:2452), modules/enabled (:126).
music.py unreferenced (13): queue move/remove/play-index/save (:646-678), playlists add/tracks (:693-700), favorite/unfavorite (:708-715), recommendations, dont-stop, ungroup, providers, jukebox/qr/{handle} (:775).

## 2. Front-end calls with no backend
327 distinct `/api|/ws` URL literals in dist; 51 match nothing (about 20 are test_*.js noise inside dist/). Real orphans (product code):
- memories.html:1768 `POST /api/collections`, :1515/1585/1813 `/api/collections/{id}/tiles` (only GET stubs exist, routers/stubs.py:28-36), memories.html:2044,2089 `/api/tiles/{id}`.
- js/widgets/core/project.js:111,134,497,528,558,576,684: 7 call sites on `/api/projects/{id}...`, `/items`, `/move`, `/navigate`, `/stages`. Backend is only the stub GET/POST `/api/projects` (stubs.py:16-25).
- touch/settings.html: `/api/trust-gate/allowlist|audit` (2813,2823), `/api/learnings` (2861,2876,2879), `/api/scheduler/jobs` (2885,2896), `/api/self-creation/{proposals,scan,widgets}` (2902-2929), `/api/channels` (2935-2961), `/api/proactive/relationship-check` (3012), `/api/chat/training-stats` (3028), `/api/training/{history,settings,export}` (3053,3112,3125): 14 URLs, 0 routes.
- `/api/openclaw/skills|plugins/*` (chat.html:5516-5829, touch/chat.html:4678-4909, touch/settings.html:2852): 9 URL shapes, 12+ call sites, no route (OpenClaw retired).
- voice.html:416 `/api/chat/confirm`; js/widgets/core/week-planner.js:291 `/api/calendar/week`; touch/js/ambient-widgets.js:270 `/api/events/upcoming`; touch/js/touch-widgets.js:659 `/api/media/now-playing`; js/journal-ui-enhancements.js:42 `/api/memories/people`.

## 3. Tables
111 tables created in alembic/versions (down-migration DROPs ignored).
- Zero writers in services/scripts/tools: `calendars` (0052; read at routers/calendar.py:104 but nothing inserts), `engineering_tasks` (0009; only a forget_reach.py:171 mention), `trust_allowlist` (0001; zero refs), `trust_audit` (0001; forget_reach.py:166 only). `panel_presence_events` is retired (0028 drops; scripts/maintenance/migrate_panel_tables.sh:112).
- Writers, zero readers: `alarm_events` (writes alarms.py:237, deletes only; flag-dark), `chat_feedback` (write conversation_feedback.py:112; forget_reach.py:117 marks it a forget GAP), `event_alarms` (write calendar_service.py:385; only a dynamic tuple read at :423, so possibly read, verify).
- Writer is DELETE/UPDATE-only, no INSERT (non-error, flag-dark or alembic-seeded): background_tasks, night_observations, night_runs, night_threads.

## 4. Brain tools to handlers
zoe-tools.ts registers 25 tools; dispatched intents: list_add, list_remove, list_show, weather, reminder_list/create, calendar_show/create, note_create/search, timer_create, journal_create/prompt/streak, people_create/search, music_play/control/volume/setup, set_volume, smart_home, memory_store, memory_retire. All are in `_DISPATCHABLE_INTENTS` (system.py:2828-2850) and have an execute_intent branch. Tools dispatching to nothing: 0.
In the allowlist but called by NO tool: daily_briefing, time_query, date_query, calculate (4; get_time is local). `memory_retire`/`memory_store` are allowlisted and called but not produced by detect_intent (by design).

## 5. intent_router intents without an executor
detect_intent emits 67 names; execute_intent (intent_router.py:3642) handles 56. The 14 not in it: build_widget, build_page, extend_capability, self_improve, ha_full_setup (delegation to OpenClaw text, intent_router.py:300-311; chat.py:453-471 `_OPENCLAW_DELEGATION_INTENTS` swallows the None as "not a failure"), panel_setup/status/list/confirm_code (handled in routers/chat.py:794-863: fine), journal_prompt/journal_streak, transaction_create/summary (handled by the expert path, :4810-4823/5797-5822: fine), people_introduce (voice_tts.py:4554: fine). Genuinely dead end: the 5 builder intents; only the Multica board route (`_MULTICA_BOARD_INTENTS`, chat.py:462) can service them.

## 6. MCP tools
mcp_server.py: 75 TOOLS entries, 76 handle_tool branches. No stub handler, none returns a constant (smallest are real calls: calendar_delete_event, browser_get_capabilities delegate to a broker). Issues: `deep_web_research` branch (:2022) has no TOOLS schema entry (unreachable from a client; still used by zoe_agent.py); `zoe_sync_knowledge` (:2073) -> agent_sync.py writes ~/.openclaw/workspace/ZOE_SELF.md and ~/.hermes/SOUL.md (agent_sync.py:8-9,25-26; both retired runtimes, 465 lines); `a2a_delegate` (:420) refuses hermes/openclaw by its own description; tool descriptions still say "Hermes-owned" browser (:771-814).

## 7. Scheduled work coded but not registered
- APScheduler in DB: music_discovery_weekly, music_history_observe, panel_provision_token_sweep, 8 multica_autopilot_*. Coded but absent: `ytmusic_cookie_refresh` (main.py:1396, ZOE_YTMUSIC_REFRESH_ENABLED off) and `router_selftrain_weekly` (main.py:1493, ZOE_ROUTER_SELFTRAIN off). Dead helper: `proactive/scheduler.py:149 register_job` has 0 callers.
- Triggers: 7 registered at main.py:1337-1348; triggers/reminders.py is a library (OK). create_task loops: all started; none orphaned.
- systemd: templates in scripts/setup/systemd (58 files) not installed in the user manager: zoe-offbox-backup(.timer), zoe-router-retrain(.timer), zoe-samantha-weekly(.timer), zoe-agents-sampler(.timer)+zoe-agents.slice, zoe-claude-bridge, zoe-omnigent-runner-reaper(.timer), omi-sync, zoe-bluetooth (Pi-side, expected). Installed with NO template in repo: zoe-dreaming.service/.timer (disabled), zoe-multica-daemon (disabled, template under scripts/setup/jetson), zoe-night-window (template scripts/night/systemd). Retired units still on disk: ~/.config/systemd/user/hermes-agent.service, openclaw-gateway.service (disabled/inactive). zoe-dreaming.timer disabled while docs describe the night window.

## 8. Fake success / constants
- stubs.py: `POST /api/projects` returns 200 `{"error": "...coming soon", "status": "not_implemented"}` (:22-25); `GET /api/projects|collections|collections/{id}/tiles|user/layout` return empty shells (:16-41). (The old POST /api/user/layout fake-ok was already removed, stubs.py:44-45.)
- voice_tts.py:7392 `/chatgpt-auth-status` constant `{"status":"not_connected"}`, no UI caller.
- system.py:107 `/platform` returns constant `"engine": "hermes"`; :126 `/modules/enabled` hard-coded all-True list (settings.html:1834 and list-common.js:18 read /platform).
- omi_recordings.py:283-293 retranscribe returns the real requeue count (OK, not fake). AST sweep of routed handlers with no await/calls found only the above plus pure constants (dashboard widgets/available, lists/types, bluetooth config).

## 9. Flags
802 prod flags in flag-inventory.json. Documented but unread: `ZOE_CREDENTIAL_BROKER` (docs/architecture/credential-vault.md:546, PLANS.md:62: zero code), `ZOE_COMPOSE_VOICE_BUDGET_S` (the scanner's `registry_unread`; registered flag_registry.py:196, no reader). `ZOE_INTERCOM_PTT` does not exist anywhere on origin/main (only `ZOE_INTERCOM`, intercom.py:44). A text scan finds 128 `ZOE_*` names present only in docs (docs/architecture/zoe-abilities.md: ZOE_ABILITY_*, ZOE_CHANNEL_*, ZOE_CONNECTED_ACCOUNTS_UI; panel-identity-plan/identity-presence-engine: ZOE_IDENTITY_*, ZOE_FUSION_*, ZOE_PRESENCE_*; communication-preferences: ZOE_COMM_PREFS, ZOE_CHANGE_NOTICE_*; research docs: ZOE_FAILURE_*, ZOE_SKILL_*, ZOE_TELEGRAM_PHOTOS...). Many are research proposals; the architecture-doc ones read as shipped.
Read but gate nothing: `zoe_acp_client.py:31 _ACP_DELIVERY_MODE` (never referenced; the module is the retired OpenClaw ACP client), `memory_digest.py:58 _ZOE_TIMEZONE` (assigned, never used; voice_greeting.py:26 has its own copy). 6 other "unread" flags (ZOE_WAKE_ACK_*, ZOE_PROCESSING_ACK_*, ZOE_ROUTER_STRUGGLE_FEED, ZOE_KIOSK_CREDENTIAL, ZOE_REPLAY_BASE_URL, ZOE_BACKUP_AGE_RECIPIENTS_FILE) are read through dict/shell paths: not dead.

## Ranked: 20 half-built surfaces (user-visible breakage first), smallest finish-or-delete step
1. Memories "collections/tiles" UI (memories.html:1515-2089) vs GET-only stubs: create/edit collection silently fails. Step: delete the collection/tile UI + stub routes (stubs.py:28-36), or build 3 routes.
2. Project widget + `/api/projects` (project.js 7 sites; lists.html:2131, dashboard.html:2056 + touch twins; stubs.py:16-25 returns 200 "coming soon"). Step: delete widget + stub, or make POST a 501.
3. touch/settings.html dead panels: trust-gate, learnings, scheduler, self-creation, channels, training, relationship-check (14 URLs, 0 routes; settings.html:2813-3125). Step: delete those sections (no backend, no tables: trust_allowlist/trust_audit also empty).
4. Chat UI OpenClaw skills/plugins browser (chat.html:5516-5829; touch/chat.html:4678-4909; settings.html:2852). Step: remove the panel; it 404s on every open.
5. OpenClaw/Hermes backend remnants (~2,000 lines + routes): openclaw_ws 201, zoe_acp_client 266, openclaw_maintenance 316, hermes_model_profiles 490, chat_hermes_stream 264, agent_sync 465; 95 "openclaw" hits in chat.py, 38 in system.py (/openclaw/*, /hermes/model-profiles/* 9 routes, 0 referrers); `/platform` "engine":"hermes". Step: delete in one PR (matches AGENTS.md "being fully retired").
6. Builder intents build_widget/build_page/extend_capability/self_improve/ha_full_setup (intent_router.py:300-311; chat.py:453-471) route to a retired runtime and mask the None as success. Step: answer "not available" or route to the board only; drop `_OPENCLAW_DELEGATION_INTENTS`.
7. Other dead UI calls: voice.html:416 /api/chat/confirm, week-planner /api/calendar/week, ambient-widgets /api/events/upcoming, touch-widgets /api/media/now-playing, journal-ui-enhancements /api/memories/people. Step: repoint to existing /api/calendar/events/today etc. or delete each caller (5 one-liners).
8. Change notices LIVE with no UI (change_notices.py:29-63, ZOE_CHANGE_NOTICES=on, loop main.py:1262). Step: add a Settings toggle on /preferences or document voice-only control.
9. Zero-writer tables: calendars (reader routers/calendar.py:104), engineering_tasks, trust_allowlist, trust_audit. Step: migration dropping the latter 3; add the INSERT or drop `calendars`.
10. Flag-dark routers with no front end: alarms (7), contacts (7), persona (7), phone_app (7). Step: pin owners in IDEAS/PLANS with a go/no-go date; delete any with no consumer (alarms/contacts/persona have no UI).
11. Accept/dismiss APIs with nobody to click: struggles proposals (struggles.py:79-126), proactive suggestions (proactive.py:169-199), welcome/trigger-morning. Step: surface in the day-review card or remove the accept path.
12. system.py admin cluster (52 unreferenced routes: pi-intent 8, engineering 5, board 4, evolution 5, tasks 3...). Step: delete pi-intent (8 + pi_intent_lab router) and the /api/system/evolution duplicates first.
13. music.py 13 unreferenced routes (queue edit, favorites, playlists add, recommendations, dont-stop, ungroup). Step: wire to music.html or drop; verify voice does not call them in-process.
14. Memory privacy/ops routes with no UI: `/api/memories/opt-out` GET/PUT (memories.py:1600-1612), proposals, backfill-contacts, pending-contacts, scrub-residue. Step: opt-out into Settings (owner-visible control) or delete.
15. Read-only helpers never fetched: journal on-this-day/streak/mood/prompts (journal.py:228-330), weather preferences/default-location (weather.py:583-629), portrait x4, transactions/summary/week, reminders/today+snooze, calendar/events/today, people/search, lists/types, dashboard/widgets/available. Step: delete the unused ones (~20 routes) or wire to the voice tiers.
16. `deep_web_research` (mcp_server.py:2022) lacks a TOOLS schema; zoe_sync_knowledge writes to ~/.openclaw and ~/.hermes. Step: add schema or delete branch; delete agent_sync tool.
17. Systemd templates not installed: zoe-offbox-backup (off-box backup timer), router-retrain, samantha-weekly, agents-sampler, claude-bridge, omnigent-runner-reaper, omi-sync; plus installed-without-template zoe-dreaming. Step: install the off-box backup timer or mark it blocked in PLANS; commit a dreaming template.
18. Unscheduled jobs: ytmusic_cookie_refresh + router_selftrain_weekly (flags off), proactive/scheduler.register_job (0 callers, scheduler.py:149). Step: delete register_job; leave the two flag-gated.
19. Dead/doc-only flags: ZOE_CREDENTIAL_BROKER, ZOE_COMPOSE_VOICE_BUDGET_S, _ACP_DELIVERY_MODE, memory_digest._ZOE_TIMEZONE, ~128 doc-only names. Step: delete the 4 code ones; a CI check that every ZOE_* in architecture docs resolves to a reader or is marked "planned".
20. Tables written but never read: alarm_events, chat_feedback (forget_reach.py:117 flags it as a GAP), event_alarms. Step: add the forget path for chat_feedback (privacy gap); add a reader or drop alarm_events.

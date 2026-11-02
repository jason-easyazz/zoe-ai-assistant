# Lane 2-J: data model, persistence, and what a restore recovers (second-round deep dive, 2026-10-19)

Scope: everything Zoe stores, where, who writes it, what deletes it, and what survives the NVMe dying.
Method: read-only live queries on the box (2026-10-19 00:10-00:40 AWST): `psql` SELECT and `\d` only, counts,
sizes and min/max timestamps, never row contents; SQLite opened `mode=ro` through Python (there is no `sqlite3`
CLI on the box); `du`, `ls`, `/proc/*/fd`. Code read from `origin/main` at `4b4ceac76` (the live checkout is at the
same commit). PR #2119 is merged (`6ebe3d8ef`). **PR #2110 is still OPEN** (head `730ef4a6`), so everything it
adds (`forget_reach.py`, `local_text_logs.py`, text-log retention) is **not live**; I read it from the PR head.
Labels: **[verified]** = I saw it live or in code; **[inferred]** = reasoned from what I saw.
Public repo: no household names or values below. Personal tables are described by counts, columns and dates only.

---

## 0. Answer first

1. **Postgres is small and mostly healthy, but it is typed like SQLite.** One database `zoe` (38 MB, 120 tables,
   Alembic head `0057`, one linear chain) plus `multica` (20 MB) and a leftover `multica_executor_lab`. There is **no
   `zoe_auth` database**: zoe-auth's 16 tables live in `zoe` and are owned by no Alembic migration. 109 of 122
   `created_at`/`updated_at` columns are `TEXT`, another 18 time columns are epoch `real`/`double`, and at least
   three text formats coexist. There is **one** JSONB column in the schema; every "metadata" column is `TEXT`, and
   one of them (`audit_logs.details`) holds Python reprs, not JSON. pgvector 0.8.6 is available in the image and
   **not installed**: embeddings are `BYTEA`. The app connects as the only role, `zoe`, a **superuser**, with
   `statement_timeout = 0` and `idle_in_transaction_session_timeout = 0`. [verified]
2. **45 of 120 tables have 0 rows**, including the whole 0052 calendar model and the whole 0053 contacts model;
   25 more have not been written in 30 days. **50 of 61 `users` rows are harness ids** (`test-*`, `parity*`).
   [verified]
3. **The biggest stores of household words are not in Postgres and not in the forget inventory:** the Flue brain's
   own SQLite (`labs/flue-zoe-brain-2x/data/zoe-brain.db`, 447 MB, live writer, 155,121 stream batches, append-only,
   no retention), the pre-Postgres `data/zoe.db` (454 MB, root-owned, content to 2026-03-02, mounted read-write into
   the zoe-auth container), the May SQLite `services/zoe-data/data/zoe.db` plus its `.pre-migration` copy (#2110
   labels it "metadata, out of scope": it holds 2,234 chat messages and 14 memory rows), five stale palace copies
   (21-30k rows each) and a July pre-wipe dump. None is a live writer except the Flue DB. [verified]
4. **A restore tonight recovers Postgres (`zoe` + `multica`, minus four biometric tables' data) and the palace from
   the same disk that just died: there is no off-box copy yet.** #2119's off-box backup is tracked code only:
   no `~/.config/zoe-backup`, no `restic`/`age` binary, no `zoe-offbox-backup` unit on the box. Even once installed,
   it would **not** carry Home Assistant's config (`homeassistant/.storage`, `secrets.yaml`), the zoe-data `data/`
   dir (the live VAPID key, the music discovery state), the Flue brain DB, 25 systemd drop-ins, `~/bin`, the crontab,
   or a models manifest (none exists; `~/models` is 80 GB). Checklist in section 6. [verified]
5. **The weekly restore check is a liveness check, not a restore test**: it restores into a scratch database on the
   **live** Postgres instance, counts `auth_users` and `chat_messages`, and counts Chroma rows through raw SQLite
   without ever loading the HNSW index. It runs Sunday 03:30, inside the night window. [verified]
6. **Multi-household:** no table carries a household or tenant key; "the household" is the string `'family'` in a
   `visibility` column (about 220 code references) plus singleton tables (settings, rooms, panels, capability
   matrix, field definitions). The cheapest correct path is **one database and one palace per household** (what
   the411's per-household backup design already assumes), not a `household_id` retrofit. [verified / inferred]

---

## 1. Postgres inventory

### 1.1 Instances, databases, roles [verified]

| item | value |
|---|---|
| server | PostgreSQL 17.11 (Debian, aarch64), container `zoe-database`, `127.0.0.1:5432` only, `pg_hba` scram-sha-256 |
| databases | `zoe` 38 MB, `multica` 20 MB (49 tables), `multica_executor_lab` 7.7 MB (not backed up; a lab), `postgres` |
| roles | **one**: `zoe`, superuser, can log in. zoe-data (`POSTGRES_URL=postgresql://zoe:***@localhost:5432/zoe`), zoe-auth and Multica all use it |
| extensions installed in `zoe` | `plpgsql` only. Available but not installed: `vector 0.8.6`, `pg_trgm 1.6`, `pg_stat_statements 1.11` |
| settings | `max_connections 100`, `shared_buffers 128MB`, `work_mem 4MB`, `statement_timeout 0`, `idle_in_transaction_session_timeout 0`, `TimeZone Etc/UTC` |
| connections at 00:30 | 16 client backends, all idle except my psql; 0 idle-in-transaction; longest transaction 0 s |
| stats since | postmaster start 2026-10-18 08:44 UTC: 3.14M commits, 378 rollbacks, 0 deadlocks, 0 temp files, 99.97% buffer hit |

zoe-auth's default DSN hard-codes the password `zoe` if `POSTGRES_URL` is unset (`services/zoe-auth/models/database.py:149`). [verified]

### 1.2 Every table in `zoe` (row count, size, newest timestamp, creator)

Newest = max of the first of `updated_at`/`created_at`/`ts`/... that exists (text columns compared as text; epoch
columns converted). "Mig" = Alembic file that creates it (`services/zoe-data/alembic/versions/`); `auth` = created
by zoe-auth / the 2026 auth migration script, no Alembic owner; `aps` = APScheduler's SQLAlchemy job store.

| table | rows | newest | mig | note |
|---|---:|---|---|---|
| chat_messages | 1,741 | 2026-10-15 | 0001 | 3.4 MB; no user column (owner via session; `metadata.user_id` duplicated on every row) |
| chat_sessions | 501 | 2026-10-15 | 0001 | 18 belong to harness users |
| chat_ag_ui_runs | 350 | 2026-10-05 | 0001 | 3.0 MB, reply event streams |
| ui_action_ledger / ui_actions | 3,543 / 2,092 | 2026-10-15 | 0001 | 33 `queued` never acked; payload can hold `image_base64` |
| llm_call_log | 5,842 | 2026-10-18 | 0001 | epoch `real` |
| audit_logs | 1,255 | 2026-10-15 | auth | `details` is Python repr text; 857 naive + 398 tz-aware timestamps |
| openclaw_run_state | 942 | 2026-10-05 | 0001 | dead since OpenClaw retirement |
| evolution_proposals | 718 | 2026-10 (epoch) | 0001/0027 | **716 pending, 2 approved**: an unread queue that only grows |
| ui_panel_sessions | 73 | 2026-10-18 | 0001 | only 1 row is a registered panel; 64 ephemeral `panel_*` ids, no expiry |
| reply_sources | 398 | 2026-10-18 | 0044 | |
| omi_recordings | 273 | 2026-10-16 | 0045 | `audio_path` points at `~/.zoe/omi-recordings` (72 MB) |
| music_play_history | 512 | **2026-07-26** | 0019 + runtime DDL | `zoe_user_id`, not `user_id` |
| notifications | 247 | 2026-10-16 | 0001 | 205 are `omi_sync` |
| people | 32 (18 live, 14 soft-deleted) | 2026-10-04 | 0001/0006/0043 | 5 owners incl. harness users |
| users | 61 | 2026-10-05 | 0001 | **37 `test-*`, 13 `parity*`** |
| auth_users | 5 | 2026-09-29 | auth | only 1 has a matching `users.id` |
| list_items / lists | 116 / 4 | 2026-10-12 / 2026-07-12 | 0001/0011 | one shopping list (`family`), it holds all 116 items |
| events | 25 | 2026-10-14 | 0001/0052 | **0 have a `calendar_id`** |
| reminders | 9 | 2026-10-04 | 0001 | one `due_date` holds an 8-letter word, not a date |
| proactive_scheduled / pending / candidates / deliveries / ledger_lines | 47 / 4 / 5 / 14 / 18 | to 2026-10-18 | 0012/0030-0041 | |
| night_observations / threads / runs | 42 / 26 / 4 | 2026-10 | 0040 | |
| exact_turns | 75 | 2026-10-14 | 0039 | |
| memory_consolidation_state | 409 | 2026-10-15 | 0014 + runtime DDL | |
| commitments / open_loops | 5 / 12 | 2026-10-18 / 10-14 | 0044 / 0001 | |
| omi_day_reviews / candidates | 4 / 14 | 2026-10-17 | 0054 | |
| struggle_events | 4 | 2026-10-14 | 0050 | |
| speaker_profiles / face_profiles | 2 / 3 | 2026-10-14 / 07-19 | 0023/0048/0051, 0024 | biometric `BYTEA` |
| panel_auth_challenges | 112 | 2026-10-15 | 0005 | |
| device_tokens | 9 | 2026-09-29 | 0005 | **all 9 `expires_at IS NULL`**, 3 not revoked |
| panels / rooms / room_devices / panel_user_bindings | 4 / 1 / 1 / 1 | July | 0001/0021/0026 | |
| apscheduler_jobs | 11 | n/a | aps | |
| user_portraits / user_model_cards | 3 / 3 | 10-11 / 10-17 | 0001 / 0034 | |
| person_relationships | 3 | 2026-07-14 | 0007/0015/0037/0043 | `authority`, `origin` NULL on all 3 |
| oauth_states / oidc_clients / oidc_signing_keys | 20 / 3 / 1 | June / May | auth | 20 OAuth states from June never expired |
| permissions / passcodes / passcode_history / auth_sessions | 37 / 1 / 1 / 1 | 2025-12 to 2026-10 | auth | |
| system_preferences / app_settings / user_preferences / display_preferences / weather_preferences | 4 / 1 / 1 / 1 / 3 | Apr-Jul | 0001/0018/0022 | |
| others with rows | agent_cost_events 27, auth_handoffs 3, enrollment_offers 1, omi_device_health 1, pending_suggestions 35, person_activities 4, restraint_classes 11, role_capability_matrix 3, people_field_definitions 10, ui_layouts 33, update_history 7, voice_announcements 27, notes 2, alembic_version 1 | | | |

**0 rows (45) [verified]:** ambient_memory, api_keys, background_tasks, calendars, chat_feedback, contact_fields,
contact_merge_reviews, contact_sources, dashboard_layouts, event_alarms, event_attendees, guest_codes,
household_persona, journal_entries, learned_voiceprint_segments, learned_voiceprints, member_modes,
**memory_forgotten** (the forget ledger has never held a row), oauth_device_codes, omi_devices,
omi_segment_embeddings, omi_segments, omi_voice_clusters, omi_voice_settings, openclaw_approvals,
panel_provision_codes, password_history, people_field_values, person_bucket_list, person_contact_links,
person_gift_ideas, person_important_dates, proactive_responses, push_subscriptions, rate_limits,
restraint_mutes, roles, service_accounts, sessions, source_contacts, struggle_proposal_events,
struggle_proposals, transactions, trust_allowlist, trust_audit.

**Not written in 30 days, non-empty (25) [verified]:** agent_cost_events (06-19), app_settings (07-16),
display_preferences, face_profiles, panels, rooms, room_devices (07-19), lists (07-12, items are live),
music_listening_events (08-09), music_play_history (07-26), notes (07-07), oauth_states (06-27), oidc_clients,
oidc_signing_keys, panel_user_bindings (06-17), passcodes, passcode_history (04-18), people_field_definitions,
role_capability_matrix (05-16), permissions (2025-12-16), person_relationships (07-14), system_preferences
(07-15), update_history (05-10), user_preferences (07-05), weather_preferences (04-19). Both music history tables
stopped writing in July/August. [verified]

**Churn note [verified + inferred]:** since the 10-18 08:44 restart, `chat_messages` saw 7,092 deletes,
`chat_sessions` 4,309, `reply_sources` 3,041, `openclaw_run_state` 1,475, `chat_ag_ui_runs` 1,383 and `events` 810,
against 52 inserts. The 02:35 dump's verify counted 8,833 chat messages; live is 1,741. A
`~/.zoe/backups/zoe-before-guest-purge.sql.gz` written at 06:40 that morning says this was a guest/harness purge
[inferred]. So the 7 daily + 4 weekly dumps still hold the purged rows for up to ~28 days, and the pre-purge file
holds them with no expiry (section 5).

### 1.3 Flags

- **No primary key:** none. Every table has a PK. [verified]
- **User-keyed tables with no FK to `users` (66 of 72 user-keyed):** every 0039+ table (exact_turns, night_*,
  omi_*, struggle_*, reply_sources, commitments, proactive_candidates/deliveries/ledger_lines, contact_*, restraint_*,
  user_model_cards, voice_announcements, enrollment_offers), plus audit_logs, auth_*, ambient_memory, llm_call_log,
  memory_forgotten, music_*, ui_actions/ui_action_ledger, ui_panel_sessions, panel_user_bindings (FK to panels only),
  learned_voiceprints (FK to people only). Only 0001-era tables and speaker/face profiles have the FK. Consequence:
  `DELETE FROM users` cascades nothing; every forget path is hand-written per table (which is why #2110 needs an
  inventory test). [verified]
- **Personal data without a member key:** `chat_messages` (owner only through `chat_sessions`), `list_items` (via
  list), `event_attendees` (via event), the router shadow and STT logs (by design, hash-only). [verified]
- **Hot-path indexes:** adequate for today's sizes. `chat_messages` has `(session_id)` only; the per-voice-turn
  history read is `WHERE session_id = ? ORDER BY created_at DESC LIMIT ?` (`routers/voice_tts.py:210-226`), which
  wants `(session_id, created_at)` once sessions grow [inferred]. `people` has `(user_id, lower(name)) WHERE
  deleted = 0` (0043). `voice_announcements` has only `(expires_at)` and took **172,506 sequential scans in ~15 h**
  (about 3 per second, 27 rows): it is polled; harmless at 27 rows, a cost if it grows. `auth_users` 21,460 seq
  scans, `proactive_scheduled` 11,982, `background_tasks` 7,429 (0 rows!) are the same polling pattern. [verified]
- **Text timestamps:** 109 of 122 `created_at`/`updated_at` are `TEXT`; formats seen: `2026-10-15 23:20:16.59+00`
  (`NOW()::text`), `2026-10-18T15:18:24Z` (Python), `2025-12-16T12:32:08.009394` (naive), plus epoch
  `real`/`double` in 18 columns (`llm_call_log.ts`, `exact_turns.said_at`, `struggle_*`, `night_*`,
  `evolution_proposals.*_at`, `auth_handoffs.*`). Text ordering across `T` and space formats is wrong within a day
  (`T` > space). [verified; ordering consequence inferred]

---

## 2. Duplicate truths

| real-world thing | where it lives | write master today | readers | what breaks on divergence |
|---|---|---|---|---|
| **A member** | `users` (61; FK target; `role` member/admin), `auth_users` (5; zoe-auth; `role` = adult/child authority), `speaker_profiles.display_name`, `face_profiles.display_name`, palace wing (`~/.mempalace/config.json`), Flue session ids | zoe-auth for identity and age; `users` rows are created lazily on first chat (`routers/chat.py:1150`, `:3219`, `intent_router.py:3142`, `pending_suggestions.py:94`, seed `database.py:89-92`) | adult/child gates read `auth_users.role` (`manner_block.py:120`, `enrollment_state.py:246`); FKs and most routers read `users` | two role columns; 4 of 5 auth users have no `users` row, so anything joining `users` misses them; harness ids sit beside real members. [verified] |
| **A person (contact)** | `people` (email/phone/birthday columns), 0053 contacts model (`contact_fields`, `source_contacts`, `contact_sources`, `person_contact_links`: **all 0 rows**), `person_*` side tables (mostly 0), `person_relationships` (3), Chroma drawers `entity_id` (197 drawers), `omi_segments.tagged_person_id`, `learned_voiceprints.people_id` | **`people`, written by 13 modules** (`contacts_store`, `correction_apply`, `intent_router`, `mcp_server`, `memory_forget_cascade`, `named_relations`, `pending_suggestions`, `person_extractor`, `person_health`, `person_merge`, `routers/people.py`, `routers/voice_tts.py`, `skybridge_service`) | recall, people UI, day review | the contacts model was built (0053) and never populated, so a phone number lives in `people.phone` today and would live in `contact_fields` tomorrow; no single writer means dedup rules are per writer. [verified] |
| **A calendar entry** | `events` (25), `calendars` (0052: **0 rows, no writer at all**), `event_alarms` (0), `reminders` (9), `proactive_scheduled` (reminder firing), `notifications` (`reminder`, `reminder_created`) | `events`: `calendar_service`, `routers/calendar.py`, `mcp_server`, `skybridge_service`, `pending_suggestions`, `memory_forget_cascade`; `reminders`: `reminder_service`, `routers/reminders.py`, `mcp_server`, `pending_suggestions` | panel, brief, proactive engine | 0052 added `calendar_id`; no event has one, so the calendar model is decorative. A reminder exists three times (row, scheduled firing, notification) with no FK between them. [verified] |
| **A list** | `lists`/`list_items`; unique `(user_id, lower(name)) WHERE deleted = 0` | `list_service`, `routers/lists.py`, `mcp_server`, `intent_router`, `skybridge_service`, `pending_suggestions` (6 writers for items) | panel, voice | live data has ONE shopping list (good). The index is per user, not per household, so two members can each own a "shopping" list with `visibility='family'` again; the fridge rule lives in code, not in the schema. [verified; recurrence inferred] |
| **A memory** | Chroma `mempalace_drawers` (307 rows) + `mempalace_audit` (19,831 rows, before/after text since 2026-07-04, no retention found), nightly JSON exports (`~/.zoe/memory-exports`, 14 kept), pre-compaction tars (`~/.zoe/palace-backups`, 4 x ~100 MB, no rotation), derived Postgres rows (`user_portraits`, `user_model_cards`, `open_loops`, `commitments`, `exact_turns`, `night_*`), Flue session store | `MemoryService.ingest` plus at least 39 write paths (first dive) | for-prompt, recall | derived rows can outlive the drawer they were built from (the #2110 gap list shows which). There is **no Postgres mirror** of drawers; the legacy SQLite `memory_items` (14 rows) is dead. [verified] |
| **A panel / room** | `panels` (4; `room_id` text, no FK, set on 1; free-text `location`), `rooms` (1; `ha_area_id`), Home Assistant areas (`homeassistant/.storage`), `room_devices` (1), `device_tokens` (9, FK panels), `panel_user_bindings` (1), `ui_panel_sessions` (73, no FK; 72 not a registered panel), MA player (`panels.default_player`) | `routers/panel_*`, `routers/rooms.py`, HA | voice routing, PA, UI | room identity in three places (location text, rooms row, HA area); panel sessions accumulate forever for ids that are not panels. [verified] |
| **A session** | `chat_sessions` (501), Flue `flue_conversation_streams` (5,557) in its own SQLite, `ui_panel_sessions` (73), `auth_sessions` (1), `sessions` (0, legacy), `panel_auth_challenges` (112), LiveKit room per session (ephemeral, #2117) | chat router; Flue sidecar; panel auth | the brain replays the Flue stream, the UI reads chat_messages | the brain's memory of a conversation (Flue) and the UI's (chat_messages) are separate copies with separate retention and separate forget reach (Flue: none). [verified] |
| **Music history** | `music_play_history` (512, to 07-26, runtime DDL), `music_listening_events` (21, to 08-09), legacy SQLite `music_events` (213), MA's own `library.db` | `music_history.py`, `database.log_music_event` (`database.py:26-61`) | taste seed, discovery | two Postgres tables for one fact, both stopped. [verified] |
| **Settings** | `system_preferences`, `app_settings`, `user_preferences` (`prefs` text JSON), `display_preferences`, `weather_preferences`, `household_persona` (0), `services/zoe-data/data/music_prefs.json`, env flags | various | various | no single settings table; a JSON file in the checkout's data dir is not backed up. [verified] |

---

## 3. SQLite and files

### 3.1 SQLite databases [verified]

| file | size | modified | tables / rows | writer | live? | forget? |
|---|---|---|---|---|---|---|
| `labs/flue-zoe-brain-2x/data/zoe-brain.db` (+4 MB WAL) | **447 MB** | 2026-10-18 23:48 | `flue_conversation_stream_batches` 155,121 (370 MB), `flue_agent_submissions` 14,658, `flue_conversation_streams` 5,557, fold checkpoints 640 | Flue 2.x sidecar, PID 1599 holds it open (`src/db.ts:43`) | **yes** | **no** (open-problems 2026-10-09 and 10-14 note it; not in `forget_reach.py`, which only scans zoe-data code) |
| `data/zoe.db` (repo root, **root:root 0644**) | **454 MB** | 2026-05-18 | pre-Postgres app DB: `performance_metrics` 2,239,892 rows (238 MB + 200 MB of indexes), `snapshots` 42,067, `chat_messages` 1,979, `chat_sessions` 936, `lists` 16 / `list_items` 158, `events` 12, `user_feedback` 122, `people` 3, `intent_metrics` 842, `timers` 27; content dated 2025-11 to 2026-03-02 | none; no process holds it | no | **no**, not in the inventory; and `data/` is bind-mounted **read-write** into `zoe-auth` at `/app/data` |
| `services/zoe-data/data/zoe.db` + `zoe.db.pre-migration-20260516` (identical) | 4.5 MB each | 2026-05-24 | `chat_messages` 2,234 (to 2026-05-15), `chat_sessions` 704, `people` 7, `memory_items` 14, `list_items` 101, `reminders` 13, `ui_actions` 475, `openclaw_run_state` 1,041 | `DB_PATH` default (`database.py:21`), "not used at runtime" | no (the `-shm` mtime of 00:10 today is my own read-only open) | **mislabelled**: #2110 `forget_reach.py:201` calls it `sqlite_dev_db`, `METADATA`, out of scope |
| `~/.mempalace/chroma.sqlite3` | 51 MB | live | section 4 | zoe-data in-process Chroma (PID 609135) | yes | yes (palace) |
| `homeassistant/home-assistant_v2.db` | 1.6 MB | live | HA recorder | Home Assistant | yes | n/a |
| `~/.zoe/music-assistant/{library,auth,cache}.db` | 0.6 / 0.1 / 0.2 MB | 2026-10-05 | MA store | MA container (bind mount) | yes | n/a |
| `data/touch_cache/*.db` | tiny | to 2026-10-05 | touch panel cache | `services/zoe-auth/touch_panel/cache.py` | yes, small | n/a |
| `data/memory.db`, `data/training.db`, `data/auth.db`, `data/music_auth.db`, `services/zoe-auth/auth.db`, `data/chroma/chroma.sqlite3` | < 1.2 MB | 2025-12 to 2026-07 | legacy | none | no | no |
| stale palace copies: `~/.mempalace.pre-b08-20260928-082034` (29,715 rows), `~/.zoe/chroma-migration-rehearsal/*/{src,dst}` (4 copies, 21,754 to 29,715 rows, 598 MB), `~/.zoe/bakeoff-2026-10/**/palace` (6 small) | | 09-27 to 10-06 | full palace copies incl. audit text | none | no | **no**; the off-box script excludes the rehearsal and bake-off dirs, but nothing deletes them |

### 3.2 JSON / JSONL / logs that hold user content [verified]

| file | size | retention today | #2110 (open) |
|---|---|---|---|
| `services/zoe-data/data/router_head_shadow.jsonl` | 3.8 MB, **10,893 records, all with raw `utt_text`** | none | strips text after 30 days (`ZOE_TEXT_LOG_RETENTION_DAYS`), drops a record naming a forgotten entity |
| `~/training/data/intent-misses.jsonl` | 23 KB | none | 30-day drop, forget reach |
| `~/.zoe-voice/voice_stt.jsonl` (+ `.1` 4.8 MB) | 6.3 MB | size rotation only | still a **gap** (`forget_reach.py:186`) |
| `~/.zoe/data/pi-intent-shadow.jsonl` | 6.7 MB | none | gap (`pi_intent_evidence`) |
| `~/.zoe-logs/zoe-data.*` | 45 MB stderr + 10 MB x4 app log + gz | size 50 MB x4 | gap (`app_logs`) |
| `~/.zoe/memory-exports/*.json.gz` | 14 x 1.6 MB | `--keep 14` (zoe-memory-export 02:40) | redacted by ledger |
| `~/.zoe/palace-backups/*` | 4 JSON (2.5 MB) + **4 tars ~100 MB each** (10-04 to 10-11) | none | tars "not rewritten"; deleted only with `redact_backups.py --delete-pre-compact-tars` |
| `~/.zoe/engineering_pipeline_runs.jsonl.backup-20260719T161516` | **1.5 GB** | none | out of scope (engineering) |
| `~/training/data/production-feedback.jsonl` | gone | | #2110 stops it at the source |

#2110's text-log retention covers exactly two files (router shadow, intent misses) plus ad-hoc backup **directories**
under `~/.zoe/backups/` whose names start with known prefixes (`local_text_logs.py:332-347` at the PR head). It does
**not** cover the STT log, the Pi evidence log, app logs, the Flue DB, or loose files in `~/.zoe/backups/`
(`zoe-before-guest-purge.sql.gz`, `postgres-all-20261017-1514.sql.gz` (9 MB, a full `pg_dumpall`), two HA config
tarballs, one root-owned, a Hermes env `.bak`). [verified]

---

## 4. Chroma (MemPalace)

[verified unless marked]

- **Client:** `chromadb.PersistentClient` in-process inside zoe-data (`memory_service.py:113-142`), chromadb 1.5.9
  on the py3.12 venv. One palace dir `~/.mempalace` (95 MB): `chroma.sqlite3` (51 MB) + two HNSW segment dirs.
- **Collections:** `mempalace_drawers` 307 rows, 384-d; `mempalace_audit` 19,831 rows, 384-d. The audit trail is 65x
  the size of the memories it audits and holds `before`/`after` text for every ingest (12,026), edit (6,606) and
  archive (861) since 2026-07-04; I found no age-based retention for it (grep of `memory_service.py` for
  prune/retention on the audit collection). Actors: consolidation 6,736, zoe_agent 4,280, chat_regex 3,035.
- **Embedder:** ONNX `all-MiniLM-L6-v2` (`_drawers_embedding_function`, `memory_service.py:182-200`), cached in
  `~/.cache/chroma/onnx_models` (167 MB; not backed up, re-downloadable). Metadata says `embedding_model_version` on
  every drawer.
- **Distance space:** the live drawers collection is **`l2`**; the create path uses **`cosine`** for a brand-new palace
  (`memory_service.py:229-231`, documented at `:216-217`), while the ranker inverts **L2** distance
  (`memory_service.py:3782`). Compaction preserves the space (`_rebuild_drawers`, `:480-490`). So a disaster restore
  that rebuilds from the JSON export into an empty palace would come back cosine and re-rank recall differently
  [inferred]. A tarball restore keeps l2.
- **HNSW health (now):** `memory_index_health.report` (read-only, run against the live palace): drawers 371 elements
  ever added / 307 live = **1.21**, audit 26,169 / 19,831 = **1.32**; advice threshold 3.0 (`memory_index_health.py:37`);
  no compaction advised. 589 drawer ops and 505 audit ops sit unpersisted in the write-ahead log (persist every 1,000
  ops). The 10-04 incident (1,591 elements for 258 rows, owner query returned 0) is documented at
  `memory_index_health.py:1-8`; four pre-compaction tars from 10-04..10-11 remain in `~/.zoe/palace-backups`.
- **"Docs live in SQLite, index rebuildable":** true as far as it goes. Documents are in `embedding_metadata`
  (`chroma:document`, 20,138 rows) and the full-text table (20,138); vectors are **not** in SQLite (only in the HNSW
  binaries and the unpurged WAL tail), so a rebuild re-embeds every row with MiniLM. That needs the ONNX model and,
  for 20k audit rows, minutes of CPU on the Jetson [inferred]. Nothing tests it: the weekly verify counts
  `embeddings` rows through raw SQLite (`zoe-backup-verify.sh`) and never opens the palace with chromadb.
- **Backup consistency:** the nightly tar takes `chroma.sqlite3` through the SQLite backup API but tars the HNSW
  binaries raw, after it, from a running process (`~/scripts/maintenance/mempalace-nightly-backup.sh`). If a persist
  lands between the two, the index on disk is newer than the `max_seq_id` row in the snapshot [inferred, low
  probability; the rebuild covers it]. The integrity check opens with `immutable=1`, which ignores the WAL.
- **Live script drift:** the unit runs `/home/zoe/scripts/maintenance/mempalace-nightly-backup.sh` (untracked), which
  lacks the tracked copy's two safety fixes (refuse a palace without its DB; write to `.partial` then rename).

---

## 5. Retention and lifecycle

| store | what deletes old data | evidence |
|---|---|---|
| Postgres dumps `~/.zoe-backups/postgres` | 7 daily + 4 weekly (Sunday) per DB; oldest weekly today 2026-09-27 (22 days) | `postgres-nightly-backup.sh:12-13`; files 0664 inside a 0700 dir |
| MemPalace tar `~/.zoe-backups/mempalace` | 7 newest `.tar.gz`; the three **April** snapshot dirs (`20260418-*`, `20260419-*`) are never rotated | live script; `ls` |
| memory JSON exports | 14 newest | `zoe-memory-export.service` `--keep 14` |
| palace pre-compaction tars | nothing automatic | `redact_backups.py:11-18` |
| `~/.zoe-backups` hand-made | nothing: `pre-wipe-20260704` (19 MB full SQL + a 4,167-row palace), `zoe_core` (May SQLite gz), `openclaw`, Hermes kanban DB, a member-named 2026-07-05 memory export (load-bearing for `restore_memories_from_export.py`) | `ls` |
| `~/.zoe/backups` loose files | nothing (prune only handles prefixed dirs) | `local_text_logs.py:343-346` (PR) |
| chat_messages / sessions / ag_ui runs | nothing by age; the user's delete (`routers/chat.py:3253-3254`), harness cleanup (`scripts/perf/samantha_bar.py:1790-1795`), `purge_orphaned_test_data.py:200` | |
| ui_panel_sessions, oauth_states, device_tokens, panel_auth_challenges, audit_logs, llm_call_log, notifications, evolution_proposals, ui_actions | nothing found; oauth states from June and 9 tokens with no expiry are still there | counts above |
| voice_announcements | `expires_at` column; rows are kept (27, oldest 07-19) | |
| omi audio | claim-time retention (`audio_expires_at`) | 0045, #2096 |
| Flue brain DB | nothing; no delete API in `@flue/runtime` | open-problems 2026-10-14 |
| Chroma audit collection | nothing by age; erased per member on `delete_user` | `memory_service.py` |
| HNSW tombstones | compaction when ratio > 3 (weekly trigger) | `memory_index_health.py` |
| text logs | **none live**; 30 days once #2110 lands | PR #2110 |

**Forget cascade after #2110 (PR head, generated table in `docs/knowledge/forget-reach.md`):** 102 stores: 32 reached,
31 out of scope, 39 known gaps.

- *Reached by both paths:* palace drawers + audit (physical erase incl. FTS, WAL, orphan HNSW dirs), `exact_turns`,
  `night_*`, `struggle_*`, `restraint_*`, `commitments`, `omi_*` incl. audio files, `contact_*`,
  `learned_voiceprint*`, router shadow + intent-miss logs, JSON exports (entity, by ledger redaction).
- *Entity-only (member erase misses):* `user_portraits`, `user_model_cards`, `open_loops`, `proactive_candidates`,
  `people` (soft delete), `person_relationships` (closed, kept), `event_attendees`, `events` (legacy attendees only),
  `chat_sessions`/`chat_messages` (redacted in place).
- *Neither path:* `chat_feedback`, `background_tasks`, `pending_suggestions`, notes, journal, lists/items,
  reminders, transactions, `people_field_values`, `person_*` side tables, `chat_ag_ui_runs`, `ambient_memory`,
  notifications, the proactive message tables, `voice_announcements`, `ui_actions`, OpenClaw tables, STT log, Pi
  evidence log, app logs, the voice corpus.
- *Not in the inventory at all (my diff of the live table list against it):* `audit_logs` (user_id + free-text
  details), `music_play_history` (keyed `zoe_user_id`, so the `user_id` regex misses it), `users`, `auth_*`,
  `evolution_proposals`, `household_persona`, `struggle_proposals`, `panels`, `device_tokens`, `rooms`, the settings
  tables; and every non-zoe-data store: the Flue DB, `data/zoe.db`, the stale palace copies, `~/.zoe-backups`
  hand-made dumps, `~/.zoe/backups` loose files. Cause: `discover_user_tables` parses Alembic `CREATE TABLE` text
  and a `user_id` column only (`forget_reach.py`, `_CREATE`/`_USERID_COL` regexes), so tables created by zoe-auth or
  keyed differently are invisible. [verified]
- *Tombstones:* `memory_forgotten` (hashed ledger, shield `PERMANENT` by default) has **0 rows** live: either no
  entity forget has run on this box since 0038, or the harness cleans up after itself [inferred].
- *PII scrubber:* every palace write goes through `MemoryService.ingest` (scrub + dedup + audit row), per the
  restore tool's own contract (`restore_memories_from_export.py:22-25`); raw collection writers are fenced by
  `live_store_guard.GuardedCollection` (`memory_service.py:232-235`). Postgres writers have no scrubber. [verified]

---

## 6. Restore completeness

### 6.1 What exists today [verified]

- `zoe-backup.timer` 02:30 (+600 s jitter): `postgres-nightly-backup.sh` (zoe + multica, `pg_dump -Fc`, biometric
  table **data** excluded for 4 tables, `:43`) then the untracked MemPalace script. Last run 2026-10-18 02:35:
  zoe 8.8 MB, multica 148 KB, palace 11 MB. All on the same NVMe.
- `zoe-memory-export.timer` 02:40: `export_memory_store.py --keep 14` to `~/.zoe/memory-exports` (0700, gzipped JSON
  of the palace sqlite), then `check_memory_tombstones.py`.
- `zoe-backup-verify.timer` **Sunday 03:30** (live, untracked; the tracked template moves it to 05:45,
  `scripts/setup/systemd/zoe-backup-verify.timer:6`): restores the newest dumps with `createdb` + `pg_restore` into
  `zoe_verify_*` **on the live `zoe-database` instance**, checks `SELECT COUNT(*)` of `auth_users` and
  `chat_messages` (zoe) and `issue` (multica), extracts the newest palace tar and counts `embeddings` via sqlite.
  Last run 10-18: "all checks passed" (5 / 8,833; 105 issues; 20,168 embeddings). It does not compare to live,
  check the Alembic head, load the HNSW index, run a query, or test anything off the disk.
- #2119 off-box: **not installed**. `~/.config/zoe-backup` absent, `restic`/`age` not on PATH, no
  `zoe-offbox-backup` unit. Its tiers once installed (`scripts/maintenance/zoe_offbox.py:94-190`):
  household = newest zoe + multica dumps, `pg_dumpall --globals-only`, newest palace tar, `~/.zoe` minus
  `*backup*`/`*cache*`/`venvs`/`bakeoff-*`/rehearsal/logs/`omi-recordings`/`env`/`secrets`, `~/.zoe-voice-samples`
  (225 MB); secrets = checkout `.env*`, `services|labs|modules/*/.env*`, `ssl/`, `config/` (incl. the cloudflared
  credentials and `config/vapid_private.pem`), `~/.zoe/env`, `~/.zoe/secrets` (absent today); artefacts = router
  gen1 checkpoint + `~/models/functiongemma-router`. Monthly drill with sha256 vs manifest. 35-day household bound.

### 6.2 If the NVMe died tonight

**Today:** nothing is recoverable from backup, because every backup is on the dead disk. What survives off the box:
the git repo (code, Alembic, router corpus `labs/functiongemma-finetune/data/`, router and restraint heads under
`services/zoe-data/models/*.npz|joblib`, which are tracked), HA config files that happen to be tracked (13 YAML /
blueprint files), the Pi (panel daemon, kiosk token in its Chromium profile), the pendant, and the cloud accounts.
Everything else is lost. [verified]

**After #2119 is installed, restore order** [inferred from the runbook, `docs/knowledge/offbox-backup.md` section 4]:
1. New box, OS, JetPack, Docker, the repo at a known commit, `install_tools.sh`; rebuild the zoe-data venv.
2. With the age identity (owner's vault, off the box): restore the **secrets** tier (`.env*`, ssl, config, cloudflared
   creds), then **household**.
3. Start `zoe-database` empty, `pg_dumpall --globals-only` roles, then `pg_restore` zoe and multica; `alembic
   current` must read `0057`.
4. Untar the palace to `~/.mempalace` (keeps `l2`); open it once with chromadb and run one recall query.
5. Restore `~/.zoe` (MA store, memory exports, Pi evidence, night-window state, kokoro cache excluded) and
   `~/.zoe-voice-samples`.
6. Re-download models (no manifest: by name from `docs/CANONICAL.md:24`), restore the artefacts tier for the router
   GGUF.
7. Reinstall systemd units from templates and **re-create drop-ins by hand**; reinstall the crontab.
8. **Re-issue every forget newer than the snapshot** (the ledger comes back with the data it protects).
9. Re-pair HA from scratch (see below); the panels keep working because `device_tokens` hashes come back.

### 6.3 What would be LOST even with #2119 installed (checklist)

- [ ] **Home Assistant config**: `homeassistant/.storage` (integrations, auth, device and area registry),
      `secrets.yaml`, custom components, recorder DB. Not tracked (beyond 13 YAML files), not in any tier;
      the only copies are two ad-hoc tarballs in `~/.zoe/backups`, which the off-box job excludes (`*backup*`).
- [ ] **`services/zoe-data/data/`**: `vapid_keys.json` (the key push.py actually reads today, since `~/.zoe/secrets`
      does not exist; 0 push subscriptions, so the cost today is nil), `music_discovery/` (40 MB), `music_prefs.json`,
      the router shadow log (the self-train corpus beyond `chat_messages`). Not in any tier.
- [ ] **Flue brain session store** (447 MB): conversation continuity the brain replays. Not in any tier (arguably
      correct for forget, but say so).
- [ ] **Models manifest**: none exists; 80 GB in `~/models`; exact quant/MTP file names are in CANONICAL but no
      hashes; Moonshine/Kokoro weights in `~/.cache/huggingface` (2.9 GB). Re-download depends on upstream.
- [ ] **systemd drop-ins** (25 `.conf` in 9 `.d` dirs: MemorySwapMax, MemoryLow, env lines) and the live unit
      files that differ from templates; **crontab** (`*/5 zoe-watchdog.sh`); **`~/bin`** (31 scripts incl.
      `zoe-tools-mcp.py`).
- [ ] **Pendant audio** `~/.zoe/omi-recordings` (72 MB, excluded by design); the 273 `omi_recordings` rows come back
      pointing at missing files.
- [ ] **Learned voiceprints / segment embeddings / voice clusters** (excluded by design; 0 rows today).
- [ ] **`multica_executor_lab`** database (not dumped; lab).
- [ ] **The Pi**: no backup of `/home/pi/.zoe-voice`, shairport/nqptp config, kiosk profile; re-provision by hand.
- [ ] **Docker named volumes** other than the database (`assistant_multica-uploads`, model volumes); multica
      uploads are not in the dump.
- [ ] **ONNX MiniLM cache** (re-downloadable, needed before the palace can embed offline).
- [ ] Recovered but stale or risky: the weekly dumps keep purged guest rows for ~28 days; device tokens come back
      without expiry; harness users come back.
- Recovered: Postgres zoe (incl. zoe-auth tables, speaker/face profiles, device tokens, panels, rooms), Multica
  DB, palace, memory exports, MA store (`~/.zoe/music-assistant`), voice corpus, `.env*`, ssl, cloudflared creds,
  router GGUF and checkpoint; router corpus and heads from git.

**Is restore ever tested?** Not off the box, not by loading the palace, and not on a separate instance. The #2119
monthly drill (once installed) restores each tier, compares sha256 with what was sent and reads 1/12 of the repo;
it still does not `pg_restore` the dump or open the palace. [verified]

---

## 7. Schema hygiene

- **Placeholders:** 106 non-test, non-Alembic files in zoe-data match `?`-placeholder SQL shapes (`= ?`, `VALUES (?`,
  `IN (?`), up from the first dive's 50 (my regex is broader; treat as an upper bound) [verified count, scope
  inferred]. 31 of 53 routers run raw SQL. Only 4 files still call `execute_fetchall`. The shim
  (`db_pool.py:457-514`) rewrites `?` to `$N`, `datetime('now', '±N unit')` to `(CURRENT_TIMESTAMP ± INTERVAL)::text`
  and bare `NOW()` to `NOW()::text` (`:90-95`), i.e. it exists to keep timestamps as text. Every `commit()` is a
  no-op (`:351-352`), so callers that think they hold a transaction do not, unless they use `conn.transaction()`
  [verified].
- **Runtime DDL outside Alembic:** `music_history.py:80` (`CREATE TABLE IF NOT EXISTS music_play_history`) and
  `memory_idle_consolidation.py:161` (`memory_consolidation_state`, guarded by an existence check because of a
  least-privilege-roles concern that does not apply: there is only a superuser). zoe-auth's 16 tables have no
  migration at all. Scripts with DDL (`scripts/maintenance/migrate_lists_enhancements.py`,
  `migrate_project_lists.py`, `scripts/utilities/*`) are legacy SQLite. [verified]
- **Alembic:** 56 files, one head `0057`, live `alembic_version = 0057`, linear `down_revision` chain; `0049` is
  skipped (gap only). 0010 drops 0009's table; 0028 drops a table 0001 created. [verified]
- **Naming:** mostly snake_case plural; outliers `users.id` text slugs (`family-admin`), `panels.panel_id` vs
  `rooms.id`, `zoe_user_id` vs `user_id` vs `owner_user_id`, ints for booleans (`deleted`, `is_active`, `writable`
  as `integer`/`smallint`). [verified]
- **JSON-in-text:** 25 text columns hold JSON (`metadata` x6, `data` x5, `evidence` x3, `payload` x2,
  `card_json` x2, `prefs`, `settings`, `ui_context`, `record_json`, `matrix_json`, `options_json`, `value_json`,
  `tokens`, `details`). Key samples: `chat_messages.metadata` = `{user_id}` on all 1,741 rows (a duplicated owner);
  `events.metadata` = reminders/get_ready_time/travel_time/prep_items/attendees/description (attendees here AND in
  `event_attendees`); `ui_actions.payload` = 19 keys incl. `card`, `cards`, `image_base64`; `ui_panel_sessions.ui_context`
  = page/title/activeElement/hasModal/timestamp/smoke; `audit_logs.details` is not valid JSON (single-quoted
  reprs). [verified]
- **Enum drift:** 20 CHECK constraints, all on 0053-0055 tables and `chat_messages.role`; none on `ui_actions.status`
  (success/skipped/failed/queued), `omi_recordings.status`, `evolution_proposals.status`, `notifications.type` (6
  values incl. `reminder` and `reminder_created`), `lists.list_type`, `visibility` (`personal`/`family`), or
  `reminders.due_date` (7 ISO dates, 1 null, **1 natural-language word**). [verified]

---

## 8. Access patterns

- **Pool vs worker:** one uvicorn worker (`main:app`, no `--workers`), asyncpg pool `min_size=2, max_size=10,
  command_timeout=30` (`db_pool.py:180-184`); live gauge `zoe_db_pool_size 5, in_use 0`. Acquire is bounded
  (`ZOE_DB_ACQUIRE_TIMEOUT_S`, `PoolExhaustedError`, `db_pool.py:41-73`), `/health` fails only on exhaustion
  (`:397-424`). Ten connections for one event loop is plenty; the historic failures were leaks (`async for db in
  get_db()` returned early, #953) and loop blocking, not pool size. [verified]
- **Long transactions:** none at sample time; no server-side guard (`idle_in_transaction_session_timeout 0`,
  `statement_timeout 0`). [verified]
- **Compat layer cost:** each call parses the SQL character by character in Python (`_adapt_params`) and then calls
  `fetch` or `execute` by prefix sniffing (`:328-341`); a `RETURNING` anywhere in the text routes to `fetch`.
  Prepared statements are cached by asyncpg on the rewritten text, so the cost is CPU on the event loop per call,
  small but on every query [inferred].
- **Hot path shape:** `/api/memories/for-prompt` (`routers/memories.py:895`) runs about ten awaited steps in
  sequence: `load_for_prompt`, `search`, a second-hop search (1 s timeout), `load_recent_for_prompt`, restraint,
  recall gate, `exact_words.lookup`, `night_mind.prompt_block`, a restraint filter, `personalisation_hop.build`, the
  reply ledger. Not N+1, but serial; Chroma calls are in-process. The voice history read is one indexed query per
  turn (`voice_tts.py:210-226`). [verified shape; latency not measured here: no `pg_stat_statements`, no
  for-prompt histogram on `/metrics`]
- **History:** the 3.9-day for-prompt outage was an event-loop fork deadlock, fixed in #947; after it the endpoint
  ran 18-66 ms (`docs/PLANS.md:151`). The 2026-07-03 pool drain (#953) is the other recorded failure. [verified]
- **Polling:** `voice_announcements` ~3 seq scans/s, `auth_users` ~0.4/s, `proactive_scheduled` ~0.2/s,
  `background_tasks` ~0.14/s on an empty table. [verified]

---

## 9. Multi-household readiness

- **No household or tenant key anywhere** (only `calendars.household_shared`, a 0-row table). 72 of 120 tables are
  keyed by a member (`user_id` and variants). The household is implicit: `visibility = 'family'` on lists, events,
  people, notes, journal, reminders, transactions (about 220 code references to `'family'`/`family-admin`), the seed
  user `family-admin` (`database.py:89-92`), the single palace with one wing per member. [verified]
- **One-house singletons:** `app_settings`, `system_preferences`, `display_preferences`, `weather_preferences`
  (per user but a city), `rooms`, `room_devices`, `panels`, `device_tokens`, `panel_provision_codes`,
  `role_capability_matrix`, `people_field_definitions`, `household_persona`, `permissions`, `roles`,
  `oidc_clients`, `oidc_signing_keys`, `apscheduler_jobs`, `evolution_proposals`, `update_history`,
  `trust_allowlist`; plus the shopping-list convention, HA (one instance), MA (one store), the Flue sidecar, the
  voice corpus. [verified]
- **`users.id` is a global text slug**, so two households would collide on ids like `family-admin` in a shared
  database. [verified]
- **What a `household_id` migration would touch** [inferred]: every member-keyed table (72) plus the ~20 singletons
  plus every unique index that is per user (lists, people, exact_turns PK, omi recording key), every raw-SQL file
  (106) to add the predicate, the palace (Chroma 1.x has `tenants`/`databases` tables, unused, a natural seam), the
  Flue store, every file log, and the backup/forget inventories. That is effectively a rewrite of the data layer.
- **Recommendation:** keep **one Zoe per household with its own database and palace** (already true; the411's
  backup is per-household by `<hid>` credential, `offbox-backup.md` section 1). If a shared the411 tier ever holds
  household data, use **database-per-household** (or schema-per-household with `search_path`) and Chroma tenants,
  never a retrofitted column. The only multi-tenant data the411 needs is the relay and backup credentials.

---

## 10. Ranked recommendations

| # | recommendation | evidence | size | risk to live data |
|---|---|---|---|---|
| 1 | **Make the off-box backup real and complete.** Run #2119's owner steps; then add HA (`homeassistant/` minus logs and recorder DB) to secrets, `services/zoe-data/data/` (minus the shadow log) to household, `~/.config/systemd/user/*.d`, `~/bin`, `crontab -l` to secrets; write a `models.lock` (path, size, sha256, source URL) and ship it; decide explicitly whether the Flue DB is shipped (forget says no). | 6.1-6.3: nothing off-box today; HA `.storage` and the live VAPID key in no tier | M (owner steps + ~5 sources) | none (read-only on the box) |
| 2 | **Turn the restore check into a restore test.** Restore into a throwaway `postgres:17` container (not the live instance), assert `alembic current == head`, per-table row counts within a band of live, open the palace with chromadb and run one owner-scoped recall; move it out of 01:50-04:55; adopt the tracked scripts (the live MemPalace script lacks the `.partial` and missing-DB guards). | 6.1, 4 | S | none; removes a restore load from the live instance |
| 3 | **Land #2110, then widen the inventory to what it cannot see.** Discover tables from the live catalog (or from `pg_dump --schema-only` in CI), not Alembic text; match `zoe_user_id`/`owner_user_id`; add `audit_logs`, `music_play_history`, `users`, `evolution_proposals`; add `OTHER` entries for the Flue DB, `data/zoe.db`, stale palace copies, `~/.zoe-backups` and loose `~/.zoe/backups` files; fix the `sqlite_dev_db` mislabel; extend `prune_adhoc_backups` to loose files. | 5: 10,893 raw utterances live today; inventory misses ~37 live tables and every non-zoe-data store | S | none |
| 4 | **Delete the dead stores that hold household words** (after one owner decision on exports): `data/zoe.db` (454 MB, root-owned, rw in zoe-auth), `services/zoe-data/data/zoe.db` + `.pre-migration`, `~/.mempalace.pre-b08-*`, `~/.zoe/chroma-migration-rehearsal` (598 MB), `~/.zoe-backups/pre-wipe-20260704-*`, the April palace dirs, `zoe_core`, `zoe-before-guest-purge.sql.gz`, `postgres-all-20261017*`, the 1.5 GB pipeline-runs backup, palace pre-compaction tars except the newest; drop the zoe-auth `data/` bind mount or make it read-only. | 3.1, 5 | S | irreversible; the 2026-07-05 member export is load-bearing for a past restore, keep it until the owner confirms |
| 5 | **Give the Flue sidecar store a lifecycle.** Session-epoch rotation in the Flue client, an offline compaction that drops streams older than N days (brain stopped, inside the night window), and a forget hook; size alarm. | 3.1: 447 MB, 155k batches, append-only, forget gap | M | medium: brain continuity; lab-prove first |
| 6 | **Stop harnesses writing the live database, then purge them.** 50 of 61 `users` are `test-*`/`parity*`; 18 chat sessions; 5 owners in `people`; churn of 7k deletes in a day. Point `samantha_bar`/day-sim at a scratch database (same container, own DB) and run `purge_orphaned_test_data.py` once. | 1.2 | S | low with a dry run; FKs cascade from `users` only for 0001-era tables |
| 7 | **Least privilege and server-side guards.** Create `zoe_app` (DML on `zoe` only) and `zoe_migrator` (DDL), keep `zoe` for the owner; set `idle_in_transaction_session_timeout = 60s` and a role-level `statement_timeout` matching the client's 30 s `command_timeout`, so a cancelled client query also stops on the server; install `pg_stat_statements` for a query latency history. | 1.1: superuser app, no timeouts, no query stats | S | low-medium: any runtime DDL (`music_history.py:80`, `memory_idle_consolidation.py:161`) must move to Alembic first |
| 8 | **Type the time columns, starting where it is read.** Alembic migrations to `timestamptz` with `USING` parse on `chat_messages`, `events`, `reminders`, `audit_logs`, `ui_panel_sessions`, then delete the `NOW()::text` rewrite; validate `reminders.due_date`. Add CHECKs for the status enums. | 1.3, 7: 109/122 text, 3 formats, epoch floats, a word in a date column | M (per table S) | medium: every raw-SQL comparison against those columns must change in the same PR; do one table per PR behind tests |
| 9 | **One write master per domain, generated tool schemas** (first dive's L item), ordered by writer count: `people` (13 modules), `list_items` (6), `events` (6), `reminders` (4). Decide the 0052 calendar and 0053 contacts models: adopt (backfill `calendar_id`, move phone/email into `contact_fields`) or drop; 45 empty tables are schema debt. | 2 | L | medium; behaviour-preserving refactor, replay-gate the voice path |
| 10 | **Retire the compat shim gradually.** CI lint that fails new `?`-placeholder SQL; codemod router by router to native `$N` + explicit `async with conn.transaction()` (today `commit()` is a no-op); remove `execute_fetchall` (4 callers). Also add `(session_id, created_at)` on `chat_messages` and expire `ui_panel_sessions`/`oauth_states`, give old `device_tokens` an expiry. | 7, 8 | M-L | low per file |

Also worth a line on the ledger: the audit collection's unbounded before/after text (19.8k rows since July; consider
a time bound matching the 35-day backup rule), the `cosine`-on-create vs `l2`-live mismatch for a rebuild-from-export
restore, and 716 never-reviewed `evolution_proposals`.

---

## 11. Not verified / limits

- No latency measurements (no `pg_stat_statements`, no for-prompt histogram); access-pattern costs are inferred.
- The 10-18 deletes are attributed to a guest purge from a backup file's name and timing, not from a log.
- I did not open the Multica schema beyond counts, the HA recorder, or the Pi.
- #2110 was read at its PR head `730ef4a6`; it may change before merge.
- The `?`-placeholder count is a regex over file text and over-counts strings that are not SQL.

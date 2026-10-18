---
type: Reference
title: Forget reach - where per-member data lives and what forgetting does about each store
description: The inventory behind "forgotten means forever" (architecture deep dive A6, 2026-10-18) - every Postgres table, file log, palace store and backup that can hold a member's words, voice or face, whether the member erase and the entity forget reach it, the known gaps, the 35-day backup rule, and the retention of the local utterance-text logs.
tags: [privacy, forget, retention, backups, inventory, router-shadow-log]
timestamp: 2026-10-18T11:30:00+08:00
---

# Forget reach

The owner rule (2026-10-05/06): **forgotten means forever**. Before 2026-10-18 it was true for the palace and Postgres and false for
the box: the router shadow log kept ~10.8k raw utterances beside its hashes, a retired training job kept a copy of conversations,
backups kept everything. This page is the inventory and the policy. The inventory is **code** - `services/zoe-data/forget_reach.py` -
and `services/zoe-data/tests/test_forget_reach_inventory.py` (`ci_safe`) fails when it drifts:

- a new Postgres table with a `user_id` column (or one that references such a table) has no entry;
- a new `*.jsonl` / `*.sqlite` / `*.db` sink or Chroma collection is covered by no entry's markers;
- a "reached" store names an eraser that does not exist or that the forget path never calls;
- a known gap is missing from [open-problems.md](open-problems.md) (`forget-reach gaps:`), or the ledger lists one that has been closed;
- the backup scripts no longer match the retention below, or this page is stale (`python3 services/zoe-data/forget_reach.py --write docs/knowledge/forget-reach.md`).

## The two forget paths

| path | trigger | entry point | reaches |
|---|---|---|---|
| **member** | admin `POST /api/users/{id}/forget` (right to be forgotten) | `MemoryService.delete_user` | the palace + audit trail (physically erased), exact words, night mind, struggle rows, restraint, reply sources, commitments, pendant recordings/voices/devices, day reviews, the contact model and learned voiceprints, and (since A6) the text layer of the local utterance logs |
| **entity** | "forget Dana" | `intent_router.memory_forget_entity` -> `memory_forget_cascade.cascade_forget` (+ `forget_redact.on_forget`) | the same stores by name, plus the derived rows (portrait, card, loops, candidates, people + edges, attendees), the verbatim transcript (redacted in place), the contact model, and the local text logs (cascade step `text_logs`) |

A store can be reached by one path and not the other; that asymmetry is what `status: gap` records.

## Local utterance-text logs (A6)

`services/zoe-data/local_text_logs.py` owns the two JSONL logs that keep words:

| log | file | what expiry does | forget |
|---|---|---|---|
| router shadow log | `services/zoe-data/data/router_head_shadow.jsonl` (+ `.N` segments) | **strips** `utt_text`; the hash and routing features stay | entity: the record naming the entity is dropped whole; member: the text layer of every record is cleared |
| intent-miss log | `~/training/data/intent-misses.jsonl` | **drops** the record | same |

- **Retention:** `ZOE_TEXT_LOG_RETENTION_DAYS`, default **30** (`keep` = no limit, `0` = keep none). The hourly `zoe-log-rotate` job applies it
  (`scripts/maintenance/rotate_service_logs.py`); `scripts/maintenance/redact_router_shadow.py` does the same once, with a backup and
  `--dry-run`, and `--all` for the whole corpus.
- **Why text is kept at all (router self-training):** the miner (`labs/router-selftrain`) reads `utt_text` directly, but it can also recover
  the words by re-hashing `chat_messages.content` and joining on the 12-hex hash (BOOTSTRAP). Stripping an old record therefore only loses
  the turns that never reached `chat_messages` (~40% in the first measured run). The loop is weekly and dark, so a 30-day window covers it; mine the
  backlog before the first sweep if you want the old text (`ZOE_TEXT_LOG_RETENTION_DAYS=keep` pauses the sweep).
- **No user id:** the logs carry none by design (a hash cannot be attributed), so a member erase clears the text layer household-wide.
  Hash-only rows hold no words and stay.
- `~/training/data/production-feedback.jsonl` (message + reply verbatim, no retention) is **no longer written** unless
  `ZOE_TRAINING_FEEDBACK_LOG=1`.

## Backup policy (decided 2026-10-18)

Forget cannot rewrite a tarball or a Postgres dump, so the rule is: **backups age out, and nothing is restored without re-applying the forget.**

- Automated backups are bounded at **35 days**: Postgres 7 daily + 4 weekly (`postgres-nightly-backup.sh`; a test pins `daily + 7*weekly <= 35`),
  MemPalace tarball 7 daily. Biometric table data (`learned_voiceprints`, `omi_segment_embeddings`, `omi_voice_clusters`, segments) is excluded from
  dumps; the enrolled `speaker_profiles` / `face_profiles` embeddings are in them for up to 35 days ([biometric policy](biometric-retention-policy.md)).
- The plaintext JSON exports are redacted by the forget ledger, nightly and on demand (`scripts/maintenance/redact_backups.py`, `forget_redact.py`).
- A one-off backup an owner script makes (`~/.zoe/backups/router-shadow-<ts>/` holds raw text, `migrate-hermes-env-<ts>/` holds secrets; mode 0700) is **deleted
  within 14 days, enforced**: the hourly `zoe-log-rotate` job runs `local_text_logs.prune_adhoc_backups`, and the job exits 1 if it cannot (fail closed). Each
  script also prints the path and the `rm -r` line. Hand-made dumps in `~/.zoe-backups` older than that are a ledgered cleanup, not a policy.
- After a restore from a backup older than a forget, **re-issue the forget**: the ledger table (`memory_forgotten`) is restored with the data it protects, but a
  restore from before the forget has neither.
- Off-box backups do not exist yet (deep dive finding 5); when they do, they inherit this rule and the 35-day bound.

## Adding a store

Add a `Store` to `forget_reach.py` (`TABLES` for a Postgres table, `OTHER` for anything else): `reached` with the eraser(s), `out_of_scope` with a one-line
reason, or `gap` with the reason AND the key in the `forget-reach gaps:` line of [open-problems.md](open-problems.md). A table that holds **words** cannot be
`out_of_scope`. Then regenerate the table below.

## The inventory

Generated from `forget_reach.py`; do not edit by hand.

<!-- BEGIN GENERATED: forget_reach.py -->

102 stores: 32 reached, 31 out of scope, 39 known gaps (`GAPS` in `services/zoe-data/forget_reach.py`).

| store | kind | holds | status | erased by | note |
|---|---|---|---|---|---|
| `exact_turns` | pg_table | words | reached | member: `exact_words.delete_user` / entity: `exact_words.erase_entity` | - |
| `night_observations` | pg_table | words | reached | member: `night_mind.delete_user` / entity: `night_mind.erase_entity` | - |
| `night_threads` | pg_table | words | reached | member: `night_mind.delete_user` / entity: `night_mind.erase_entity` | - |
| `night_runs` | pg_table | metadata | reached | member: `night_mind.delete_user` | - |
| `struggle_events` | pg_table | derived | reached | member: `struggle_detector.delete_user` / entity: `struggle_detector.erase_entity` | - |
| `struggle_proposal_events` | pg_table | derived | reached | member: `struggle_detector.delete_user` / entity: `struggle_detector.erase_entity` | - |
| `restraint_mutes` | pg_table | derived | reached | member: `restraint.erase_user` / entity: `restraint.erase_entity` | - |
| `restraint_classes` | pg_table | derived | reached | member: `restraint.erase_user` / entity: `restraint.erase_entity` | - |
| `reply_sources` | pg_table | derived | reached | member: `reply_ledger.forget_user` | - |
| `commitments` | pg_table | words | reached | member: `commitments.forget_user` / entity: `commitments.erase_entity` | - |
| `omi_recordings` | pg_table | words | reached | member: `omi_recordings.forget_user` / entity: `omi_recordings.erase_entity` | - |
| `omi_segments` | pg_table | words | reached | member: `omi_recordings.forget_user` / entity: `omi_recordings.erase_entity` | - |
| `omi_segment_embeddings` | pg_table | biometric | reached | member: `omi_recordings.forget_user` | - |
| `omi_voice_clusters` | pg_table | biometric | reached | member: `omi_recordings.forget_user` | - |
| `omi_voice_settings` | pg_table | config | reached | member: `omi_recordings.forget_user` | - |
| `omi_devices` | pg_table | metadata | reached | member: `omi_recordings.forget_user` | - |
| `omi_device_health` | pg_table | metadata | reached | member: `omi_recordings.forget_user` | - |
| `omi_day_reviews` | pg_table | words | reached | member: `omi_day_review.forget_user` / entity: `omi_day_review.erase_entity` | - |
| `omi_day_candidates` | pg_table | words | reached | member: `omi_day_review.forget_user` / entity: `omi_day_review.erase_entity` | - |
| `contact_fields` | pg_table | words | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_entity` | - |
| `contact_sources` | pg_table | metadata | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_entity` | - |
| `source_contacts` | pg_table | words | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_entity` | - |
| `contact_merge_reviews` | pg_table | derived | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_entity` | - |
| `person_contact_links` | pg_table | metadata | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_entity` | - |
| `learned_voiceprints` | pg_table | biometric | reached | member: `contacts_store.forget_user` / entity: `contacts_store.forget_voiceprints` | - |
| `learned_voiceprint_segments` | pg_table | biometric | reached | member: `omi_recordings.forget_user` / entity: `contacts_store.forget_voiceprints` | - |
| `user_portraits` | pg_table | derived | gap | entity: `memory_forget_cascade._run` | Entity forget deletes a portrait that names the entity; delete_user does not remove it. |
| `user_model_cards` | pg_table | derived | gap | entity: `memory_forget_cascade._run` | Entity forget deletes a card that names the entity; delete_user does not remove it. |
| `open_loops` | pg_table | words | gap | entity: `memory_forget_cascade._run` | Entity forget resolves a loop that names the entity; delete_user does not touch the rows. |
| `proactive_candidates` | pg_table | derived | gap | entity: `memory_forget_cascade._run` | Entity forget deletes candidates naming the entity; delete_user does not. |
| `people` | pg_table | words | gap | entity: `memory_forget_cascade._run` | Entity forget soft-deletes the person row (kept with deleted=1); delete_user does not touch people. |
| `person_relationships` | pg_table | derived | gap | entity: `memory_forget_cascade._run` | Entity forget closes the edges (valid_to); delete_user does not. |
| `event_attendees` | pg_table | words | gap | entity: `memory_forget_cascade._run` | Entity forget anonymises the attendee row; delete_user does not. |
| `events` | pg_table | words | gap | entity: `memory_forget_cascade._run` | Only legacy metadata.attendees are scrubbed by the entity forget; titles/notes stay and delete_user skips events. |
| `memory_forgotten` | pg_table | metadata | out_of_scope | - | The forget LEDGER itself: salted hashes, no names. It must survive a forget to keep the forget enforced. |
| `chat_sessions` | pg_table | words | gap | entity: `forget_redact.on_forget` | Entity forget redacts titles in place (forget_redact.on_forget); delete_user does not delete sessions. |
| `chat_messages` | pg_table | words | gap | entity: `forget_redact.on_forget` | Entity forget redacts the span in place (forget_redact.on_forget); delete_user does not delete messages. |
| `chat_feedback` | pg_table | words | gap | - | Holds a corrected_response (words); neither path reaches it. |
| `background_tasks` | pg_table | words | gap | - | Holds the task text and its result; neither path reaches it. |
| `pending_suggestions` | pg_table | words | gap | - | Offer text naming people can outlive the forget (only person offers are withdrawn by name). |
| `notes` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. Holds free text. |
| `journal_entries` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. Holds free text. |
| `lists` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. List items name people and places. |
| `list_items` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. The items themselves; the household shopping list etc. |
| `people_field_values` | pg_table | words | gap | - | Per-person custom field values (routers/people.py); the entity cascade soft-deletes the person row only. |
| `chat_ag_ui_runs` | pg_table | words | gap | - | Persisted AG-UI event streams of a chat run (the reply text); neither path reaches them. |
| `reminders` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. |
| `transactions` | pg_table | words | gap | - | Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user. Descriptions can name people. |
| `person_activities` | pg_table | words | gap | - | Per-person activity text; not reached by the entity cascade (only the people row is). |
| `person_bucket_list` | pg_table | words | gap | - | Per-person text; not reached by the entity cascade. |
| `person_gift_ideas` | pg_table | words | gap | - | Per-person text; not reached by the entity cascade. |
| `person_important_dates` | pg_table | metadata | gap | - | Dates and labels for a person; not reached by the entity cascade. |
| `calendars` | pg_table | metadata | out_of_scope | - | Calendar containers: a name and a colour, no event content (events are listed separately). |
| `event_alarms` | pg_table | metadata | out_of_scope | - | No words and no biometrics: ids, flags, times or counters only. |
| `ambient_memory` | pg_table | words | gap | - | Ambient captured memory text; neither path reaches it. |
| `notifications` | pg_table | words | gap | - | Delivered notification titles/messages; neither path reaches them. |
| `music_listening_events` | pg_table | metadata | out_of_scope | - | Listening history: titles/artists, no household words; the music journal has its own controls. |
| `proactive_deliveries` | pg_table | derived | gap | - | Delivery ledger rows keyed by candidate; text is in the candidate, not here. |
| `proactive_ledger_lines` | pg_table | words | gap | - | Holds the spoken line (words); neither path reaches it. |
| `proactive_pending` | pg_table | words | gap | - | Holds the message text; neither path reaches it. |
| `proactive_scheduled` | pg_table | words | gap | - | Holds the message text; neither path reaches it. |
| `proactive_responses` | pg_table | metadata | out_of_scope | - | No words and no biometrics: ids, flags, times or counters only. |
| `voice_announcements` | pg_table | words | gap | - | Holds the announcement message; short-lived (expires_at) but not erased on forget. |
| `speaker_profiles` | pg_table | biometric | out_of_scope | - | Governed by docs/knowledge/biometric-retention-policy.md: embeddings only, kept until the member un-enrols (their own delete); MemoryService.delete_user does not remove it. Present in Postgres dumps (age out in <= 35 days). |
| `face_profiles` | pg_table | biometric | out_of_scope | - | Governed by docs/knowledge/biometric-retention-policy.md: embeddings only, kept until the member un-enrols (their own delete); MemoryService.delete_user does not remove it. Present in Postgres dumps (age out in <= 35 days). |
| `enrollment_offers` | pg_table | metadata | out_of_scope | - | No words and no biometrics: ids, flags, times or counters only. |
| `user_preferences` | pg_table | config | out_of_scope | - | Preference flags; no household words. |
| `weather_preferences` | pg_table | config | out_of_scope | - | A city and units. |
| `dashboard_layouts` | pg_table | config | out_of_scope | - | UI layout JSON. |
| `ui_layouts` | pg_table | config | out_of_scope | - | UI layout trees. |
| `member_modes` | pg_table | config | out_of_scope | - | A mode flag per member. |
| `memory_consolidation_state` | pg_table | metadata | out_of_scope | - | A session id and a timestamp. |
| `panel_user_bindings` | pg_table | metadata | out_of_scope | - | No words and no biometrics: ids, flags, times or counters only. |
| `panel_auth_challenges` | pg_table | metadata | out_of_scope | - | Short-lived PIN hashes. |
| `ui_panel_sessions` | pg_table | metadata | out_of_scope | - | Which page a panel is on; ui_context can carry transient state, expires with the session. |
| `push_subscriptions` | pg_table | metadata | out_of_scope | - | A browser push endpoint and keys. |
| `auth_handoffs` | pg_table | metadata | out_of_scope | - | Short-lived hand-off tokens. |
| `trust_audit` | pg_table | metadata | out_of_scope | - | Tool-call audit: tool name, allowed flag, reason. |
| `agent_cost_events` | pg_table | metadata | out_of_scope | - | Token counts and cost per call. |
| `llm_call_log` | pg_table | metadata | out_of_scope | - | Latency and token counts per call. |
| `ui_actions` | pg_table | words | gap | - | Action payloads (e.g. a typed list item) are kept; neither path reaches them. |
| `ui_action_ledger` | pg_table | metadata | out_of_scope | - | Event ledger for UI actions: type and ids. |
| `engineering_tasks` | pg_table | metadata | out_of_scope | - | The household's engineering pipeline (tickets about the code, owner-authored), not member memory. |
| `openclaw_approvals` | pg_table | words | gap | - | Dead since OpenClaw was retired but may hold old request text; goes with the table drop in the OpenClaw removal PR. |
| `openclaw_run_state` | pg_table | words | gap | - | Dead since OpenClaw was retired but may hold old request/response text; goes with the table drop in the OpenClaw removal PR. |
| `palace_sqlite` | palace | words | reached | member: `memory_service.delete_user` / entity: `memory_residue.scrub_sqlite` | MemoryService.delete_user hard-deletes the rows, purges the audit trail and runs the physical erase (free pages, FTS5, WAL, orphan HNSW dirs); entity forget sweeps + memory_residue.scrub_sqlite. |
| `chroma_collections` | chroma | words | reached | member: `memory_service._delete_ids` / entity: `memory_residue.scrub_sqlite` | The palace's Chroma collections (drawers/audit) are the same store: rows deleted by id, then physically erased and verified. |
| `router_shadow_log` | file | words | reached | member: `local_text_logs.erase_all_text` / entity: `local_text_logs.erase_entity` | Utterance text ages out after ZOE_TEXT_LOG_RETENTION_DAYS (default 30), a record naming a forgotten entity is dropped (cascade step text_logs), a member erase clears the text layer. Hash-only rows hold no words. |
| `intent_miss_log` | file | words | reached | member: `local_text_logs.erase_all_text` / entity: `local_text_logs.erase_entity` | Same module and rules as the router shadow log (records are dropped, not stripped). |
| `production_feedback_log` | file | words | out_of_scope | - | Stopped at the source: no longer written unless ZOE_TRAINING_FEEDBACK_LOG=1 (default off): it held the message and Zoe's reply verbatim with no retention. Nothing reads it; an opt-in copy would be outside the cascade, so do not opt in. |
| `voice_stt_log` | file | words | gap | - | ZOE_VOICE_STT_LOG (~/.zoe-voice/voice_stt.jsonl): the Moonshine transcript of each utterance, no user id, no retention, outside the cascade. Rotated by size only. |
| `voice_sample_corpus` | corpus | biometric | gap | - | ~/.zoe-voice-samples: the owner's permanent regression corpus (real WAVs of whoever spoke at the panel, ZOE_VOICE_SAVE_AUDIO). Owner-designated and never erased by forget; a member's clips cannot be located by member. |
| `omi_audio_files` | file | biometric | reached | member: `omi_recordings.forget_user` / entity: `omi_recordings.erase_entity` | Pendant audio on disk goes with the recording (omi_recordings.forget_user removes rows and files). |
| `device_audit_logs` | file | metadata | out_of_scope | - | Audit trails of device/bluetooth/model-profile changes: ids and actions, no household words. |
| `pi_intent_evidence` | file | words | gap | - | ~/.zoe/data/pi-*.jsonl: intent-miss evidence and shadow labels (text with names/emails masked). Flag-gated (ZOE_PI_INTENT_*), outside the cascade, no retention. |
| `engineering_pipeline_runs` | file | metadata | out_of_scope | - | Engineering pipeline run records (tickets/PRs), not member memory. |
| `sqlite_dev_db` | file | metadata | out_of_scope | - | database.py's zoe.db is the legacy SQLite fallback for dev; production is Postgres. |
| `app_logs` | file | words | gap | - | ~/.zoe-logs/zoe-data.*: the service log. Message text is logged at INFO in a few places (e.g. intent_miss: <text>); rotated by size (50 MB x4), not by age, outside the cascade. |
| `backup_postgres` | backup | words | out_of_scope | - | Nightly dumps: 7 daily + 4 weekly, so a row is gone from every dump within BACKUP_MAX_AGE_DAYS (35). Forget cannot rewrite a dump; biometric table data is excluded from them. Restore => re-issue the forget. |
| `backup_palace_tar` | backup | words | out_of_scope | - | Nightly MemPalace tarball, 7 kept (<= 7 days). Not rewritten; the JSON exports beside it are redacted by the ledger (redact_backups.py). |
| `backup_json_exports` | backup | words | reached | entity: `forget_redact.redact_export` | Palace JSON exports and compaction backups are redacted by the forget ledger (forget_redact.redact_export, scripts/maintenance/redact_backups.py). |
| `backup_adhoc` | backup | words | out_of_scope | - | One-off backups an owner script makes (~/.zoe/backups/<tool>-<ts>/, mode 0700; the router-shadow one holds raw text, the env one holds secrets): ENFORCED deletion after ADHOC_BACKUP_MAX_AGE_DAYS (14) by the hourly sweep (local_text_logs.prune_adhoc_backups, fails the job if it cannot). Older hand-made dumps in ~/.zoe-backups are a ledgered cleanup. |

<!-- END GENERATED -->

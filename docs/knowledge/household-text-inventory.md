---
type: Reference
title: Household-text inventory - every place household words can be written, and what forget and delete-my-data do there
description: The ROADMAP A5-A7 acceptance record. A generated table of every store that can hold something a household member said or wrote (Postgres tables, the palace, local logs, pendant transcripts, audio, exports, backups, operator outputs, the off-box stage, the retired agent stores), each with its location as a pattern, the kind of text, whether a per-subject forget and the member erase reach it, retention and owner; plus the write-site detector that fails CI on an undeclared sink, and what it cannot see.
tags: [privacy, forget, retention, inventory, household-text, backups]
timestamp: 2026-11-14T19:30:00+08:00
---

# Household-text inventory

The owner rule is **forgotten means forever**. [Forget reach](forget-reach.md) is the per-member inventory (every table with a member
column, every sink the service names) and the policy. This page answers the wider question: **where can a household member's words
end up?** The registry is code, `services/zoe-data/household_text.py`, and `services/zoe-data/tests/test_household_text_inventory.py`
(`ci_safe`) holds the code to it.

- **One source of reach.** For every store `forget_reach.py` already holds, the two reach columns are READ from it (never restated),
  so the two pages cannot disagree. `household_text.EXTRA` adds what `forget_reach` does not hold: stores with no member key that are
  not a `*.jsonl` sink (opt-in capture directories, operator tool outputs, the off-box stage and snapshots, the retired agents' homes).
- **forget** = the per-subject forget ("forget Dana": `memory_forget_entity` + `memory_forget_cascade`, durable retry included).
  **delete-my-data** = the member erase (admin `POST /api/users/{id}/forget` -> `MemoryService.delete_user`).
- **Status** is `reached` (an eraser on a forget path), `gap` (a known hole, ledgered in [open-problems.md](open-problems.md):
  `forget-reach gaps:` for `forget_reach` stores, `household-text gaps:` for `EXTRA`), or `out_of_scope` with the reason.

## The detector (what fails CI)

1. **File writes.** Every Python file under `services/zoe-data/` and `scripts/` (tests, `archive/` dirs excluded) is parsed. A call
   that writes a file (`open(.., "w"|"a"|"x")`, `wave.open(.., "wb")`, `.write_text`, `.write_bytes`, `json.dump`) whose innermost
   enclosing function handles a text-like value (an identifier, argument, attribute, keyword argument, or a short space-free string such
   as a record key or file name, with a token such as `text`, `utt`,
   `transcript`, `message`, `reply`, `prompt`, `turn`, `fact`, `hyp`, `stt`) is a WRITE SITE `relpath::function`. Each one must be in
   `WRITE_SITES` (mapped to a store here) or `NOT_HOUSEHOLD` (reviewed: holds no household words, with the reason). A new one fails
   the test until someone decides; an allowlist entry that matches nothing any more fails too.
2. **Tables.** A `CREATE TABLE` (migration or runtime) with a text-like column (`content`, `body`, `title`, `summary`, `message`,
   `transcript`, `label`, `payload`, ...) must be in `forget_reach.TABLES` or `NO_TEXT_TABLES` with a reason. This reaches tables with
   no member column, which the forget-reach discovery cannot see.
3. The `forget_reach` sink scan (`*.jsonl` / `*.db` / Chroma collections) and member-table discovery still run, in their own test.

**What the detector cannot see** (stated, not hidden):

- Code that is not Python under those two roots: the Flue brain sidecar and the Telegram bot (TypeScript, `labs/`), the panel UI's
  browser storage, shell scripts (`*.sh`), and docker containers (Home Assistant, Music Assistant, llama-server's in-memory prompt cache).
- A writer whose function never names a text-like identifier (a payload built elsewhere and passed in as `rec` or `data`), a write
  through a library (`sqlite3`, `shutil.copy` of a text file, a logger handler), and a write in a `lambda`. Known instances: `scripts/train/generate_gemma_training_data.py` writes real conversations from the legacy SQLite DB as a payload named `ex`, and the router retrain writes its `.texts.jsonl` through a suffix constant in another module; both stores are registered by hand (`operator_tool_outputs`, `router_retrain_texts`). Granularity is the
  function: a reviewed function that later starts writing words to a second file is not re-flagged.
- `labs/` code (lab-only by contract), `tests/`, and `archive/` directories.
- What a store holds at runtime: the registry says what CAN be there, not what is. Live counts are never read by this test.

## Adding a store

Add a `TextStore` to `FROM_FORGET_REACH` (when `forget_reach.py` holds it) or `EXTRA`, map its writer in `WRITE_SITES`, ledger a
`gap` in open-problems, then regenerate: `python3 services/zoe-data/household_text.py --write docs/knowledge/household-text-inventory.md`.

## The inventory

Generated from `household_text.py`; do not edit by hand.

<!-- BEGIN GENERATED: household_text.py -->

88 stores: a per-subject forget reaches 44, delete-my-data reaches 40, both 33, neither 37.

| store | location (pattern) | text | forget reaches | delete-my-data reaches | status | retention | owner |
|---|---|---|---|---|---|---|---|
| `exact_turns` | `pg:exact_turns` | verbatim turns (exact words) | `exact_words.erase_entity` | `exact_words.delete_user` | reached | no age limit: kept until deleted | exact_words.py |
| `night_observations` | `pg:night_observations` | night-mind observations about a member's day | `night_mind.erase_entity` | `night_mind.delete_user` | reached | no age limit: kept until deleted | night_store.py |
| `night_threads` | `pg:night_threads` | night-mind threads (summaries of what was said) | `night_mind.erase_entity` | `night_mind.delete_user` | reached | no age limit: kept until deleted | night_store.py |
| `now_cards` | `pg:now_cards` | the member's 'now' card text | `now_card.erase_entity` | `now_card.forget_user` | reached | no age limit: kept until deleted | now_card.py |
| `struggle_events` | `pg:struggle_events` | a failed turn's classes and, for the owner, its words | `struggle_detector.erase_entity` | `struggle_detector.delete_user` | reached | no age limit: kept until deleted | struggle_detector.py |
| `struggle_proposal_events` | `pg:struggle_proposal_events` | proposals derived from failed turns | `struggle_detector.erase_entity` | `struggle_detector.delete_user` | reached | no age limit: kept until deleted | struggle_proposals.py |
| `turn_outcomes` | `pg:turn_outcomes` | failure capture of a turn (route, outcome, words for review) | `struggle_detector.erase_entity` | `struggle_detector.delete_user` | reached | no age limit: kept until deleted | turn_outcomes.py |
| `restraint_mutes` | `pg:restraint_mutes` | topics the member asked Zoe not to raise | `restraint.erase_entity` | `restraint.erase_user` | reached | no age limit: kept until deleted | restraint.py |
| `restraint_classes` | `pg:restraint_classes` | sensitivity classes of what was said | `restraint.erase_entity` | `restraint.erase_user` | reached | no age limit: kept until deleted | restraint.py |
| `reply_sources` | `pg:reply_sources` | which memory a reply drew on (ids + snippets) | - | `reply_ledger.forget_user` | reached | no age limit: kept until deleted | reply_ledger.py |
| `commitments` | `pg:commitments` | promises made in conversation | `commitments.erase_entity` | `commitments.forget_user` | reached | no age limit: kept until deleted | commitments.py |
| `omi_recordings` | `pg:omi_recordings` | pendant recording rows and their transcript | `omi_recordings.erase_entity` | `omi_recordings.forget_user` | reached | no age limit: kept until deleted | omi_recordings.py |
| `omi_segments` | `pg:omi_segments` | pendant transcript segments (who said what) | `omi_recordings.erase_entity` | `omi_recordings.forget_user` | reached | no age limit: kept until deleted | omi_voices.py |
| `omi_check_items` | `pg:omi_check_items` | the morning transcript check's queued lines | `omi_recordings.erase_entity` | `omi_recordings.forget_user` | reached | no age limit: kept until deleted | transcript_check.py |
| `omi_day_reviews` | `pg:omi_day_reviews` | the pendant day review (meetings, lists, reminders) | `omi_day_review.erase_entity` | `omi_day_review.forget_user` | reached | no age limit: kept until deleted | omi_day_review.py |
| `omi_day_candidates` | `pg:omi_day_candidates` | day-review candidates (words lifted from the day) | `omi_day_review.erase_entity` | `omi_day_review.forget_user` | reached | no age limit: kept until deleted | omi_day_review.py |
| `conversation_notes` | `pg:conversation_notes` | short notes about past conversations | `conversation_notes.erase_entity` | `conversation_notes.forget_user` | reached | no age limit: kept until deleted | conversation_notes.py |
| `contact_fields` | `pg:contact_fields` | contact details (names, addresses, notes) | `contacts_store.forget_entity` | `contacts_store.forget_user` | reached | no age limit: kept until deleted | contacts_store.py |
| `source_contacts` | `pg:source_contacts` | synced contact cards | `contacts_store.forget_entity` | `contacts_store.forget_user` | reached | no age limit: kept until deleted | contacts_store.py |
| `contact_merge_reviews` | `pg:contact_merge_reviews` | contact merge proposals | `contacts_store.forget_entity` | `contacts_store.forget_user` | reached | no age limit: kept until deleted | contacts_store.py |
| `named_guest_turns` | `pg:named_guest_turns` | what a named guest said | `contacts_store.forget_voiceprints` | `contacts_store.forget_user` | reached | no age limit: kept until deleted | named_guest_memory.py |
| `user_portraits` | `pg:user_portraits` | the member portrait (derived prose) | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | user_portrait.py |
| `user_model_cards` | `pg:user_model_cards` | the user-model card (derived prose) | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | user_model_card.py |
| `open_loops` | `pg:open_loops` | unfinished things the member mentioned | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | memory_digest.py, omi_review_apply.py |
| `proactive_candidates` | `pg:proactive_candidates` | things Zoe might raise (derived text) | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | commitments.py, proactive/selector.py |
| `people` | `pg:people` | people the household talks about (names, notes) | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | contacts_store.py, pending_suggestions.py |
| `person_relationships` | `pg:person_relationships` | who is related to whom | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | people_graph.py |
| `event_attendees` | `pg:event_attendees` | attendee names and emails | `memory_forget_cascade._run` | `calendar_forget.forget_user` | gap | no age limit: kept until deleted | calendar_service.py |
| `events` | `pg:events` | calendar event titles, places, descriptions | `memory_forget_cascade._run` | `calendar_forget.forget_user` | gap | no age limit: kept until deleted | skybridge_service.py, pending_suggestions.py |
| `chat_sessions` | `pg:chat_sessions` | chat titles | `forget_redact.on_forget` | - | gap | no age limit: kept until deleted | routers/chat.py, proactive/session_utils.py |
| `chat_messages` | `pg:chat_messages` | the verbatim chat transcript (every channel) | `forget_redact.on_forget` | - | gap | no age limit: kept until deleted | routers/chat.py, proactive/session_utils.py |
| `chat_feedback` | `pg:chat_feedback` | a corrected response | - | - | gap | no age limit: kept until deleted | conversation_feedback.py |
| `background_tasks` | `pg:background_tasks` | a background task's text and result | - | - | gap | no age limit: kept until deleted | background_runner.py (read side; no live writer in zoe-data) |
| `pending_suggestions` | `pg:pending_suggestions` | offer text (may name people) | `memory_forget_cascade._run` | - | gap | no age limit: kept until deleted | pending_suggestions.py, anticipation.py |
| `notes` | `pg:notes` | the member's notes | - | - | gap | no age limit: kept until deleted | intent_router.py, pending_suggestions.py |
| `journal_entries` | `pg:journal_entries` | the member's journal | - | - | gap | no age limit: kept until deleted | intent_router.py, mcp_server.py |
| `lists` | `pg:lists` | list names | - | - | gap | no age limit: kept until deleted | list_service.py |
| `list_items` | `pg:list_items` | list items (shopping and other lists) | - | - | gap | no age limit: kept until deleted | list_service.py |
| `people_field_values` | `pg:people_field_values` | custom fields about a person | - | - | gap | no age limit: kept until deleted | routers/people.py |
| `chat_ag_ui_runs` | `pg:chat_ag_ui_runs` | AG-UI event streams of a chat run (the reply text) | - | - | gap | no age limit: kept until deleted | routers/chat.py |
| `reminders` | `pg:reminders` | reminder text | - | - | gap | no age limit: kept until deleted | reminder_service.py |
| `transactions` | `pg:transactions` | transaction descriptions | - | - | gap | no age limit: kept until deleted | routers/transactions.py, mcp_server.py |
| `person_activities` | `pg:person_activities` | a person's activities | - | - | gap | no age limit: kept until deleted | person_extractor.py, routers/people.py |
| `person_bucket_list` | `pg:person_bucket_list` | a person's bucket list | - | - | gap | no age limit: kept until deleted | person_extractor.py, routers/people.py |
| `person_gift_ideas` | `pg:person_gift_ideas` | gift ideas for a person | - | - | gap | no age limit: kept until deleted | person_extractor.py, routers/people.py |
| `calendars` | `pg:calendars` | calendar names and descriptions | - | `calendar_forget.forget_user` | reached | no age limit: kept until deleted | skybridge_service.py, calendar_forget.py |
| `ambient_memory` | `pg:ambient_memory` | ambient captured text | - | - | gap | no age limit: kept until deleted | routers/voice_tts.py |
| `notifications` | `pg:notifications` | notification text | - | `notifications_store.forget_user` | reached | no age limit: kept until deleted | notifications_store.py (distress_handoff.py, omi_recordings.py, ...) |
| `proactive_deliveries` | `pg:proactive_deliveries` | delivery ledger (keys; text is in the candidate) | - | - | gap | no age limit: kept until deleted | proactive/ledger.py |
| `proactive_outcome_edges` | `pg:proactive_outcome_edges` | outcome edges (opaque ids) | - | `outcome_edges.erase_user` | reached | no age limit: kept until deleted | proactive/outcome_edges.py |
| `proactive_ledger_lines` | `pg:proactive_ledger_lines` | the spoken proactive line | - | - | gap | no age limit: kept until deleted | proactive/lines.py |
| `proactive_pending` | `pg:proactive_pending` | a queued proactive message | - | - | gap | no age limit: kept until deleted | proactive/session_utils.py |
| `proactive_scheduled` | `pg:proactive_scheduled` | a scheduled proactive message | - | - | gap | no age limit: kept until deleted | proactive/triggers/reminders.py |
| `voice_announcements` | `pg:voice_announcements` | an announcement message | - | - | gap | until expires_at (short-lived), then swept | voice_announce.py |
| `member_register` | `pg:member_register` | topics the member likes | - | `member_register.forget_user` | reached | no age limit: kept until deleted | member_register.py |
| `alarms` | `pg:alarms` | alarm labels | - | `alarms.forget_user` | reached | no age limit: kept until deleted | alarms.py |
| `ui_actions` | `pg:ui_actions` | UI action payloads (e.g. a typed list item) | - | - | gap | no age limit: kept until deleted | ui_orchestrator.py |
| `openclaw_approvals` | `pg:openclaw_approvals` | old OpenClaw request text (retired, no writer) | - | - | gap | no age limit: kept until deleted | none (retired; read by routers/chat.py) |
| `openclaw_run_state` | `pg:openclaw_run_state` | old OpenClaw request/response text (retired) | - | - | gap | no age limit: kept until deleted | routers/chat.py (retired lane) |
| `palace_sqlite` | `~/.mempalace/**/chroma.sqlite3` | the memory palace: every remembered fact | `memory_residue.scrub_sqlite` | `memory_service.delete_user` | reached | no age limit: kept until deleted | memory_service.py |
| `chroma_collections` | `chroma:mempalace_drawers, chroma:mempalace_audit` | the palace's collections (same store) | `memory_residue.scrub_sqlite` | `memory_service._delete_ids` | reached | no age limit: kept until deleted | memory_service.py |
| `router_shadow_log` | `services/zoe-data/data/router_head_shadow.jsonl(.N)` | utterance text beside its hash | `local_text_logs.erase_entity` | `local_text_logs.erase_all_text` | reached | text stripped after ZOE_TEXT_LOG_RETENTION_DAYS (30) | semantic_router.py (local_text_logs.py erases) |
| `intent_miss_log` | `~/training/data/intent-misses.jsonl` | utterances the intent router missed (crudely masked) | `local_text_logs.erase_entity` | `local_text_logs.erase_all_text` | reached | dropped after ZOE_TEXT_LOG_RETENTION_DAYS (30) | intent_router.py (local_text_logs.py erases) |
| `recall_gate_trace` | `~/.zoe/recall-gate-trace/trace.jsonl` | the owner's opt-in trace: turns + row text | `local_text_logs.erase_entity` | `local_text_logs.erase_all_text` | reached | 7 days | recall_gate_trace.py |
| `production_feedback_log` | `~/training/data/production-feedback.jsonl` | message + reply verbatim (opt-in only) | - | - | out_of_scope | none (not written unless ZOE_TRAINING_FEEDBACK_LOG=1) | zoe_agent.py |
| `voice_stt_log` | `$ZOE_VOICE_STT_LOG (~/.zoe-voice/voice_stt.jsonl, .1)` | the Moonshine transcript of every utterance | - | - | gap | size rotation only (ZOE_VOICE_STT_LOG_MAX_BYTES, one .1 segment) | routers/voice_tts.py |
| `flue_session_log` | `labs/flue-zoe-brain-2x/data/zoe-brain.db` | the Flue brain's own session event log | - | - | gap | no age limit: kept until deleted | the Flue brain sidecar (TypeScript, outside this scan) |
| `flue_session_store` | `labs/flue-zoe-brain-2x/data/zoe-brain.db` | the Flue brain's session store (replies, STT text) | - | - | out_of_scope | no age limit: kept until deleted | the Flue brain sidecar (TypeScript, outside this scan) |
| `omi_repass_work` | `~/.zoe/omi-repass/work/<id>.{manifest.json,out.jsonl}` | one night run's recognised words | - | `omi_recordings.forget_user` | reached | minutes (deleted when the run applies) | omi_repass.py, scripts/night/jobs/omi_repass_worker.py |
| `omi_repass_hyps` | `~/.zoe/omi-repass/hyps/hyps-<night>.jsonl` | every engine's hypothesis per chunk | `omi_recordings.erase_entity` | `omi_recordings.forget_user` | reached | 7 days | omi_repass_eval.py |
| `router_retrain_texts` | `$ZOE_ROUTER_RETRAIN_DIR/work/<stamp>/*.texts.jsonl` | failed turns' words for one retrain run | `struggle_detector.erase_entity` | `struggle_detector.delete_user` | reached | one run | scripts/maintenance/router_struggle_retrain.py |
| `self_review_records` | `~/.zoe/self-review/<night>.self-review.jsonl + .md` | the owner's flagged turns verbatim | `self_review.erase_entity` | `struggle_detector.delete_user` | reached | no age limit: kept until deleted | self_review.py |
| `night_extract_facts` | `~/.zoe/night-extract/<night>.night-extract.jsonl` | facts proposed from the member's day | `night_extract.erase_entity` | `struggle_detector.delete_user` | reached | no age limit: kept until deleted | night_extract.py |
| `self_improver_candidates` | `~/.zoe/self-improver/candidate-cells.jsonl` | synthetic asks in the shape of a failure | `self_review.erase_entity` | - | reached | no age limit: kept until deleted | self_improver (night) |
| `app_logs` | `~/.zoe-logs/zoe-data.*` | pre-2026-11-03 lines may hold utterances (now len + hash only) | - | - | gap | size rotation (50 MB x4) | the service logger |
| `pi_intent_evidence` | `~/.zoe/data/ (pi-*.jsonl)` | intent-miss evidence and shadow labels (masked text) | - | - | gap | no age limit: kept until deleted | pi_intent_evidence.py, pi_intent_shadow.py |
| `backup_postgres` | `~/.zoe-backups/postgres/*.sql.gz` | every Postgres row above, as a dump | - | - | gap | 7 daily + 4 weekly (<= 35 days) | scripts/maintenance/postgres-nightly-backup.sh |
| `backup_palace_tar` | `~/.zoe-backups/mempalace/*.tar.gz` | the palace, as a tarball | - | - | gap | 7 daily | scripts/maintenance/mempalace-nightly-backup.sh |
| `backup_json_exports` | `~/.zoe/memory-exports/*.json, palace-backups/` | palace JSON exports | `forget_redact.redact_export` | - | reached | redacted by the forget ledger | scripts/maintenance/export_memory_store.py |
| `backup_adhoc` | `~/.zoe/backups/<tool>-<ts>/` | one-off backups owner scripts make (raw text) | - | - | out_of_scope | 14 days, enforced | owner scripts (redact_router_shadow.py, ...) |
| `voice_sample_corpus` | `~/.zoe-voice-samples/*.wav` | recorded panel speech (audio of words) | - | - | gap | owner corpus: kept; guest clips 30 days | routers/voice_tts.py (ZOE_VOICE_SAVE_AUDIO), voice_clips.py |
| `omi_audio_files` | `~/.zoe/omi-recordings/<user>/*` | pendant audio (audio of words) | `omi_recordings.erase_entity` | `omi_recordings.forget_user` | reached | claim-time retention | omi_recordings.py |
| `telegram_stt_capture` | `$ZOE_TELEGRAM_STT_CAPTURE_DIR/tg-<stamp>-<pid>.{wav,txt}` | a Telegram voice note's audio and its transcript (opt-in; unset = nothing written) | - | - | gap | none | routers/telegram_media.py |
| `operator_tool_outputs` | `scratch dirs, ~/.zoe/voice-gate/, ~/.zoe/struggle-eval/, --out paths` | reports, labelling sheets and replays the owner's tools write (may quote transcripts and replies) | - | - | gap | none automatic; the owner's own files | scripts/perf/*, scripts/maintenance/* operator tools, scripts/train/generate_gemma_training_data.py |
| `offbox_stage` | `~/.cache/zoe-offbox/stage/<tier>/<hmac>.age` | age ciphertext of the household + secrets tiers (no plaintext; names are keyed HMACs) | via its sources (the next backup drops what they dropped) | via its sources | reached | mirrors the on-box sources: a file deleted there is deleted here on the next backup | scripts/maintenance/zoe_offbox.py |
| `offbox_snapshots` | `the backup target (restic repo: the411 rest-server or a local directory)` | snapshots of the stage (ciphertext) | - | - | gap | household tier <= 35 days (RETENTION in zoe_offbox.py) | scripts/maintenance/zoe_offbox.py |
| `evolution_proposals` | `pg:evolution_proposals, pg:evolution_proposal_history (+ the Multica issue a proposal opens)` | intent-gap proposals quote missed utterances verbatim (title, description, evidence examples) | - | - | gap | no age limit: kept until deleted | evolution_notice.py, evolution_queue.py |
| `retired_agent_stores` | `~/.openclaw/, ~/.hermes/` | the retired agents' session stores (OpenClaw, Hermes) | - | - | out_of_scope | deleted 2026-10-18 (owner-authorised) bar ~/.hermes/.env and an OpenClaw workspace stub | none (retired) |

<!-- END GENERATED -->

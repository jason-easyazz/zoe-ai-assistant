"""Where per-member data lives, and what the forget path does about each store (architecture deep dive A6, 2026-10-18).

"Forgotten means forever" was true for the palace and Postgres and false for the box: the router shadow log kept raw utterances, a
retired training copy kept conversations, backups kept everything. This module is the INVENTORY - data only, stdlib only, no runtime
caller - that ``tests/test_forget_reach_inventory.py`` holds the code to:

  * every Postgres table with a ``user_id`` column (and every table that references one) is a key in ``STORES``;
  * every non-table store the code can name (a JSONL log, the palace sqlite, a Chroma collection, a voice corpus) is covered by a
    ``markers`` entry, so a new ``*.jsonl`` sink fails the test until someone decides what forget does about it;
  * a ``reached`` store names the function that erases it and the call site that runs it (verified by AST, not by memory);
  * an ``out_of_scope`` store says why in one line; a ``gap`` store is a KNOWN hole, must appear in the "forget-reach gaps" entry of
    ``docs/knowledge/open-problems.md``, and goes away from ``GAPS`` the day it is closed (the test fails on a stale gap entry).

Two forget paths exist and a store can be reached by either or both:
  MEMBER  ``MemoryService.delete_user`` (admin ``/api/users/{id}/forget``): the right to be forgotten for a whole member.
  ENTITY  ``memory_forget_entity`` / ``memory_forget_cascade``: "forget Dana" - everything derived from one name.

BACKUP POLICY (decided 2026-10-18; ``BACKUP_MAX_AGE_DAYS``). Forget cannot rewrite a tarball or a Postgres dump, so the rule is:
backups AGE OUT, and nothing is restored without re-applying the forget. Automated backups are bounded at 35 days (Postgres:
7 daily + 4 weekly; MemPalace tar: 7 daily) while their nightly jobs succeed (they prune by count after a new backup, so a failing
job keeps its old ones: both are ledgered gaps until an age alarm exists); the plaintext JSON exports are redacted by the ledger (``redact_backups.py``); biometric
table DATA is excluded from dumps (``BIOMETRIC_TABLES`` in ``postgres-nightly-backup.sh``). A one-off backup an owner script makes
(``~/.zoe/backups/<tool>-<ts>/``) is deleted within ``ADHOC_BACKUP_MAX_AGE_DAYS``. After a restore from a backup older than a
forget, re-issue the forget (the ledger table is restored with the data it protects).
"""
from __future__ import annotations

import ast
import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

BACKUP_MAX_AGE_DAYS = 35
ADHOC_BACKUP_MAX_AGE_DAYS = 14

REACHED, OUT_OF_SCOPE, GAP = "reached", "out_of_scope", "gap"
WORDS, BIOMETRIC, DERIVED, METADATA, CONFIG = "words", "biometric", "derived", "metadata", "config"


@dataclass(frozen=True)
class Store:
    key: str                          # the table name, or a short id for a non-table store
    kind: str                         # pg_table | palace | chroma | file | corpus | backup
    holds: str                        # WORDS | BIOMETRIC | DERIVED | METADATA | CONFIG
    status: str                       # REACHED | OUT_OF_SCOPE | GAP
    why: str = ""                     # one line; required unless the store is reached by both paths
    member: Optional[str] = None      # "module.function" run by MemoryService.delete_user
    entity: Optional[str] = None      # "module.function" (or cascade step label) run by the entity forget
    markers: tuple[str, ...] = ()     # strings that identify a non-table store in code (file names, collection names, paths)


def _t(key, holds, status, why="", member=None, entity=None):
    return Store(key, "pg_table", holds, status, why, member, entity)


# -- Postgres tables ---------------------------------------------------------------------------------------------------------------
# member refs are called from MemoryService.delete_user (memory_service.py); entity refs from intent_router.memory_forget_entity or
# memory_forget_cascade.py. The omi_* family hangs off omi_recordings.forget_user / omi_devices.forget_user / omi_voices.forget_user.
_OMI_M = "omi_recordings.forget_user"
_CONTACTS_M = "contacts_store.forget_user"
_OWN = "Member-owned product data; the member edits and deletes it in the app. Not erased by delete_user."
_NOWORDS = "No words and no biometrics: ids, flags, times or counters only."

TABLES: tuple[Store, ...] = (
    # --- words / derived text the forget path reaches ---
    _t("exact_turns", WORDS, REACHED, member="exact_words.delete_user", entity="exact_words.erase_entity"),
    _t("night_observations", WORDS, REACHED, member="night_mind.delete_user", entity="night_mind.erase_entity"),
    _t("night_threads", WORDS, REACHED, member="night_mind.delete_user", entity="night_mind.erase_entity"),
    _t("night_runs", METADATA, REACHED, member="night_mind.delete_user"),
    _t("now_cards", DERIVED, REACHED, member="now_card.forget_user", entity="now_card.erase_entity"),
    _t("struggle_events", DERIVED, REACHED, member="struggle_detector.delete_user", entity="struggle_detector.erase_entity"),
    _t("struggle_proposal_events", DERIVED, REACHED, member="struggle_detector.delete_user", entity="struggle_detector.erase_entity"),   # via struggle_proposals.forget_user / forget_events
    _t("turn_outcomes", DERIVED, REACHED, member="struggle_detector.delete_user", entity="struggle_detector.erase_entity"),   # failure capture (#2141): _delete_user_locked + _erase_outcomes, whatever the flag says
    _t("restraint_mutes", DERIVED, REACHED, member="restraint.erase_user", entity="restraint.erase_entity"),
    _t("restraint_classes", DERIVED, REACHED, member="restraint.erase_user", entity="restraint.erase_entity"),
    _t("reply_sources", DERIVED, REACHED, member="reply_ledger.forget_user"),
    _t("commitments", WORDS, REACHED, member="commitments.forget_user", entity="commitments.erase_entity"),
    _t("omi_recordings", WORDS, REACHED, member=_OMI_M, entity="omi_recordings.erase_entity"),
    _t("omi_segments", WORDS, REACHED, member=_OMI_M, entity="omi_recordings.erase_entity"),
    _t("omi_segment_embeddings", BIOMETRIC, REACHED, member=_OMI_M),
    _t("omi_voice_clusters", BIOMETRIC, REACHED, member=_OMI_M),
    _t("omi_voice_settings", CONFIG, REACHED, member=_OMI_M),
    _t("omi_check_items", WORDS, REACHED, member=_OMI_M, entity="omi_recordings.erase_entity"),   # the morning transcript check's queue (0076): omi_voices.forget_user + drop_recording + redact
    _t("omi_check_asks", METADATA, REACHED, member=_OMI_M),            # the once-a-day ask lock (0076): a date + an outcome, no text
    _t("omi_channel_means", BIOMETRIC, REACHED, member=_OMI_M),        # the speaker step's per-channel mean offset (0075): omi_voices.forget_user
    _t("omi_wearer_samples", BIOMETRIC, REACHED, member=_OMI_M),       # the night re-pass's wearer-print windows (0075): omi_voices.forget_user + drop_recording
    _t("omi_devices", METADATA, REACHED, member=_OMI_M),
    _t("omi_device_health", METADATA, REACHED, member=_OMI_M),         # omi_health.forget_user, called from omi_recordings.forget_user
    _t("omi_day_reviews", WORDS, REACHED, member="omi_day_review.forget_user", entity="omi_day_review.erase_entity"),
    _t("omi_day_candidates", WORDS, REACHED, member="omi_day_review.forget_user", entity="omi_day_review.erase_entity"),
    _t("conversation_notes", WORDS, REACHED, member="conversation_notes.forget_user", entity="conversation_notes.erase_entity"),
    _t("contact_fields", WORDS, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_entity"),
    _t("contact_sources", METADATA, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_entity"),
    _t("source_contacts", WORDS, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_entity"),
    _t("contact_merge_reviews", DERIVED, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_entity"),
    _t("person_contact_links", METADATA, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_entity"),
    _t("learned_voiceprints", BIOMETRIC, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_voiceprints"),
    _t("learned_voiceprint_segments", BIOMETRIC, REACHED, member=_OMI_M, entity="contacts_store.forget_voiceprints"),
    _t("named_guest_turns", WORDS, REACHED, member=_CONTACTS_M, entity="contacts_store.forget_voiceprints"),   # via omi_voices.purge_people
    _t("panel_voice_clusters", BIOMETRIC, GAP,
       "Passive named guests (0073): unknown panel voices held for the household owner. Erased by expiry (14 days, hourly sweep), by "
       "naming (the voice becomes a guest's print), and by the owner's DELETE /api/voice/unknown-voices; a member erase does not "
       "reach them and they hold no name an entity forget could match. Never in the dumps (BIOMETRIC_TABLES)."),
    _t("panel_voice_samples", BIOMETRIC, GAP,
       "The embeddings behind panel_voice_clusters (never audio), erased with their voice. Never in the dumps (BIOMETRIC_TABLES)."),
    _t("learned_voiceprints_0071", METADATA, OUT_OF_SCOPE,
       "Not a store: the SQLite-only (lab / unit test) rebuild table of migration 0071, renamed to learned_voiceprints inside the "
       "same migration; it never exists on the Postgres box the dumps read."),
    # --- derived rows only the ENTITY path reaches (delete_user leaves them: a member-erase gap, ledgered) ---
    _t("user_portraits", DERIVED, GAP, "Entity forget deletes a portrait that names the entity; delete_user does not remove it.",
       entity="memory_forget_cascade._run"),
    _t("user_model_cards", DERIVED, GAP, "Entity forget deletes a card that names the entity; delete_user does not remove it.",
       entity="memory_forget_cascade._run"),
    _t("open_loops", WORDS, GAP, "Entity forget resolves a loop that names the entity; delete_user does not touch the rows.",
       entity="memory_forget_cascade._run"),
    _t("proactive_candidates", DERIVED, GAP, "Entity forget deletes candidates naming the entity; delete_user does not.",
       entity="memory_forget_cascade._run"),
    _t("people", WORDS, GAP, "Entity forget soft-deletes the person row (kept with deleted=1); delete_user does not touch people.",
       entity="memory_forget_cascade._run"),
    _t("person_relationships", DERIVED, GAP, "Entity forget closes the edges (valid_to); delete_user does not.",
       entity="memory_forget_cascade._run"),
    _t("event_attendees", WORDS, GAP, "Entity forget anonymises the attendee row; delete_user does not.", entity="memory_forget_cascade._run"),
    _t("events", WORDS, GAP, "Only legacy metadata.attendees are scrubbed by the entity forget; titles/notes stay and delete_user skips events.",
       entity="memory_forget_cascade._run"),
    _t("memory_forgotten", METADATA, OUT_OF_SCOPE,
       "The forget LEDGER itself: salted hashes, no names. It must survive a forget to keep the forget enforced."),
    # --- the verbatim transcript ---
    _t("chat_sessions", WORDS, GAP, "Entity forget redacts titles in place (forget_redact.on_forget); delete_user does not delete sessions.",
       entity="forget_redact.on_forget"),
    _t("chat_messages", WORDS, GAP, "Entity forget redacts the span in place (forget_redact.on_forget); delete_user does not delete messages.",
       entity="forget_redact.on_forget"),
    _t("chat_feedback", WORDS, GAP, "Holds a corrected_response (words); neither path reaches it."),
    _t("background_tasks", WORDS, GAP, "Holds the task text and its result; neither path reaches it."),
    _t("pending_suggestions", WORDS, GAP, "Offer text naming people can outlive the forget (person offers are withdrawn by name; "
       "anticipation offers naming the entity are deleted by the cascade); delete_user does not reach the table.",
       entity="memory_forget_cascade._run"),
    # --- member-owned product data: the member's own lists, notes, calendar, journal, money ---
    _t("notes", WORDS, GAP, _OWN + " Holds free text."),
    _t("journal_entries", WORDS, GAP, _OWN + " Holds free text."),
    _t("lists", WORDS, GAP, _OWN + " List items name people and places."),
    _t("list_items", WORDS, GAP, _OWN + " The items themselves; the household shopping list etc."),
    _t("people_field_values", WORDS, GAP, "Per-person custom field values (routers/people.py); the entity cascade soft-deletes the person row only."),
    _t("chat_ag_ui_runs", WORDS, GAP, "Persisted AG-UI event streams of a chat run (the reply text); neither path reaches them."),
    _t("reminders", WORDS, GAP, _OWN),
    _t("transactions", WORDS, GAP, _OWN + " Descriptions can name people."),
    _t("person_activities", WORDS, GAP, "Per-person activity text; not reached by the entity cascade (only the people row is)."),
    _t("person_bucket_list", WORDS, GAP, "Per-person text; not reached by the entity cascade."),
    _t("person_gift_ideas", WORDS, GAP, "Per-person text; not reached by the entity cascade."),
    _t("person_important_dates", METADATA, GAP, "Dates and labels for a person; not reached by the entity cascade."),
    _t("calendars", METADATA, OUT_OF_SCOPE, "Calendar containers: a name and a colour, no event content (events are listed separately)."),
    _t("event_alarms", METADATA, OUT_OF_SCOPE, _NOWORDS),
    _t("ambient_memory", WORDS, GAP, "Ambient captured memory text; neither path reaches it."),
    _t("notifications", WORDS, REACHED, member="notifications_store.forget_user"),   # every row of the member, any writer; no entity eraser
    _t("music_listening_events", METADATA, OUT_OF_SCOPE, "Listening history: titles/artists, no household words; the music journal has its own controls."),
    # --- proactive / voice delivery plumbing ---
    _t("proactive_deliveries", DERIVED, GAP, "Delivery ledger rows keyed by candidate; text is in the candidate, not here."),
    _t("proactive_outcome_edges", DERIVED, REACHED, member="outcome_edges.erase_user"),   # opaque item ids + outcomes only: no word for an entity forget to erase
    _t("proactive_ledger_lines", WORDS, GAP, "Holds the spoken line (words); neither path reaches it."),
    _t("proactive_pending", WORDS, GAP, "Holds the message text; neither path reaches it."),
    _t("proactive_scheduled", WORDS, GAP, "Holds the message text; neither path reaches it."),
    _t("proactive_responses", METADATA, OUT_OF_SCOPE, _NOWORDS),
    _t("voice_announcements", WORDS, GAP, "Holds the announcement message; short-lived (expires_at) but not erased on forget."),
    # --- identity / biometric enrolment ---
    _t("speaker_profiles", BIOMETRIC, OUT_OF_SCOPE, "Governed by docs/knowledge/biometric-retention-policy.md: embeddings only, kept until the member "
       "un-enrols (their own delete); MemoryService.delete_user does not remove it. Present in Postgres dumps (age out in <= 35 days)."),
    _t("face_profiles", BIOMETRIC, OUT_OF_SCOPE, "Governed by docs/knowledge/biometric-retention-policy.md: embeddings only, kept until the member "
       "un-enrols (their own delete); MemoryService.delete_user does not remove it. Present in Postgres dumps (age out in <= 35 days)."),
    _t("enrollment_offers", METADATA, OUT_OF_SCOPE, _NOWORDS),
    _t("member_register", WORDS, REACHED, member="member_register.forget_user"),   # liked topics are the member's words
    _t("alarms", WORDS, REACHED, member="alarms.forget_user"),                 # the label is the member's words
    _t("phone_devices", METADATA, OUT_OF_SCOPE, "A paired phone (phone_app.py): member id, a display name, the platform, a credential hash and "
       "public key - no household words. The member or an admin revokes it (phone_app.revoke_device), which deletes its push tokens."),
    # --- config, ids and infrastructure ---
    _t("user_preferences", CONFIG, OUT_OF_SCOPE, "Preference flags; no household words."),
    _t("weather_preferences", CONFIG, OUT_OF_SCOPE, "A city and units."),
    _t("dashboard_layouts", CONFIG, OUT_OF_SCOPE, "UI layout JSON."),
    _t("ui_layouts", CONFIG, OUT_OF_SCOPE, "UI layout trees."),
    _t("member_modes", CONFIG, OUT_OF_SCOPE, "A mode flag per member."),
    _t("memory_consolidation_state", METADATA, OUT_OF_SCOPE, "A session id and a timestamp."),
    _t("panel_user_bindings", METADATA, OUT_OF_SCOPE, _NOWORDS),
    _t("panel_auth_challenges", METADATA, OUT_OF_SCOPE, "Short-lived PIN hashes."),
    _t("ui_panel_sessions", METADATA, OUT_OF_SCOPE, "Which page a panel is on; ui_context can carry transient state, expires with the session."),
    _t("push_subscriptions", METADATA, OUT_OF_SCOPE, "A browser push endpoint and keys."),
    _t("auth_handoffs", METADATA, OUT_OF_SCOPE, "Short-lived hand-off tokens."),
    _t("trust_audit", METADATA, OUT_OF_SCOPE, "Never-built trust-gate audit log; never written, dropped by alembic 0065."),
    _t("agent_cost_events", METADATA, OUT_OF_SCOPE, "Token counts and cost per call."),
    _t("llm_call_log", METADATA, OUT_OF_SCOPE, "Latency and token counts per call."),
    _t("ui_actions", WORDS, GAP, "Action payloads (e.g. a typed list item) are kept; neither path reaches them."),
    _t("ui_action_ledger", METADATA, OUT_OF_SCOPE, "Event ledger for UI actions: type and ids."),
    _t("engineering_tasks", METADATA, OUT_OF_SCOPE, "The household's engineering pipeline (tickets about the code, owner-authored), not member memory."),
    _t("openclaw_approvals", WORDS, GAP, "Dead since OpenClaw was retired but may hold old request text; goes with the table drop in the OpenClaw removal PR."),
    _t("openclaw_run_state", WORDS, GAP, "Dead since OpenClaw was retired but may hold old request/response text; goes with the table drop in the OpenClaw removal PR."),
)

# -- everything that is not a Postgres table -----------------------------------------------------------------------------------------
OTHER: tuple[Store, ...] = (
    Store("palace_sqlite", "palace", WORDS, REACHED, "MemoryService.delete_user hard-deletes the rows, purges the audit trail and runs the "
          "physical erase (free pages, FTS5, WAL, orphan HNSW dirs); entity forget sweeps + memory_residue.scrub_sqlite.",
          member="memory_service.delete_user", entity="memory_residue.scrub_sqlite", markers=("chroma.sqlite3",)),
    Store("chroma_collections", "chroma", WORDS, REACHED, "The palace's Chroma collections (drawers/audit) are the same store: rows deleted by id, "
          "then physically erased and verified.", member="memory_service._delete_ids", entity="memory_residue.scrub_sqlite",
          markers=("mempalace_drawers", "mempalace_audit", "memory_service.py")),   # the last is the ONE module allowed to open a collection
    Store("router_shadow_log", "file", WORDS, REACHED, "Utterance text ages out after ZOE_TEXT_LOG_RETENTION_DAYS (default 30), a record naming a forgotten "
          "entity is dropped (cascade step text_logs), a member erase clears the text layer. Hash-only rows hold no words.",
          member="local_text_logs.erase_all_text", entity="local_text_logs.erase_entity", markers=("router_head_shadow.jsonl",)),
    Store("intent_miss_log", "file", WORDS, REACHED, "Same module and rules as the router shadow log (records are dropped, not stripped).",
          member="local_text_logs.erase_all_text", entity="local_text_logs.erase_entity", markers=("intent-misses.jsonl",)),
    Store("recall_gate_trace", "file", WORDS, REACHED, "~/.zoe/recall-gate-trace/: the owner's opt-in trace (ZOE_RECALL_GATE_TRACE, default off) - the "
          "owner's words + row text per gate decision, 0600, 7-day retention, never off-box. A member erase clears it household-wide (via "
          "local_text_logs.erase_all_text), an entity forget drops every line naming the entity (via local_text_logs.erase_entity, cascade step text_logs); both inside "
          "that module's erase barrier, which the trace's appends honour.",
          member="local_text_logs.erase_all_text", entity="local_text_logs.erase_entity", markers=("trace.jsonl",)),
    Store("production_feedback_log", "file", WORDS, OUT_OF_SCOPE, "Stopped at the source: no longer written unless ZOE_TRAINING_FEEDBACK_LOG=1 (default off): it held the message and "
          "Zoe's reply verbatim with no retention. Nothing reads it; an opt-in copy would be outside the cascade, so do not opt in.", markers=("production-feedback.jsonl",)),
    Store("voice_stt_log", "file", WORDS, GAP, "ZOE_VOICE_STT_LOG (~/.zoe-voice/voice_stt.jsonl): the Moonshine transcript of each utterance, "
          "no user id, no retention, outside the cascade. Rotated by size only.", markers=("voice_stt.jsonl",)),
    Store("flue_session_log", "file", WORDS, GAP, "The Flue brain sidecar's append-only event log (labs/flue-zoe-brain-2x/data/zoe-brain.db): its own session history, "
          "which keeps the words (off the record included). Zoe only READS it (mode=ro) for the guest-notes backfill, and the notes made from it are erased by the forget "
          "cascade (conversation_notes.erase_entity); the log itself is outside the cascade.", markers=("zoe-brain.db",)),
    Store("voice_sample_corpus", "corpus", BIOMETRIC, GAP, "~/.zoe-voice-samples: the owner's permanent regression corpus (real WAVs of whoever spoke at the "
          "panel, ZOE_VOICE_SAVE_AUDIO). Owner-designated and never erased by forget; a member's clips cannot be located by member. A named guest's clips go with the guest, and guest / unknown clips expire after 30 days (voice_clips.py).",
          markers=(".zoe-voice-samples", "ZOE_VOICE_SAVE_AUDIO")),
    Store("flue_session_store", "db", WORDS, OUT_OF_SCOPE, "labs/flue-zoe-brain-2x/data/zoe-brain.db: the Flue brain's own session store (Zoe's replies and the panel turns' STT text). The pendant "
          "panel-anchor step (omi_panel_anchor.py) only READS it, read-only, and returns text to the caller in memory: nothing is copied out of it. Its retention and erasure are the brain's own.",
          markers=("zoe-brain.db",)),
    Store("omi_repass_work", "file", WORDS, REACHED, "The pendant night re-pass's per-run work files (~/.zoe/omi-repass/work/<id>.manifest.json + <id>.out.jsonl, 0600) hold the audio PATHS and the "
          "recognised WORDS of the recordings in one night run; the run deletes them in a finally block as soon as the results are applied (minutes). A member erase, "
          "an entity erase or a recording delete deletes EVERY work file whatever its age (they name no member, so none can be proven clean; a run that loses one retries), "
          "and a mid-run erase is caught at the write (user_erase_gate.writing) and the recording tombstoned. state.json (measured speeds, Zoe's own voice reference) and "
          "shadow.jsonl (counts only: no word, no name) hold nothing of a member.",
          member="omi_recordings.forget_user", markers=(".out.jsonl", ".manifest.json", "shadow.jsonl")),
    Store("omi_repass_hyps", "file", WORDS, REACHED, "The night re-pass's per-chunk hypotheses (~/.zoe/omi-repass/hyps/hyps-<night>.jsonl, 0600): every engine's text for each chunk, the ROVER pick and the "
          "vote counts, kept 7 days (the audio's own retention; pruned each run) for the contender ledger, the morning transcript check and owner-correction scoring. A member erase rewrites the files "
          "without that member's lines; an entity forget replaces the name with [forgotten] in every hypothesis (omi_repass_eval.forget_user / erase_entity, called from omi_recordings.forget_user / "
          "erase_entity). The contender ledger (ledger.jsonl) holds counts only.",
          member="omi_recordings.forget_user", entity="omi_recordings.erase_entity", markers=("hyps-", "ledger.jsonl")),
    Store("router_retrain_texts", "file", WORDS, REACHED, "The struggle-fed router retrain run's work dir (~/.zoe/router_retrain/work/<stamp>/*.texts.jsonl) "
          "holds the failed turns' words for one run; forget tombstones the examples and deletes any leftover text files "
          "(router_retrain_ledger.forget, via struggle_proposals.forget_user / forget_events). The ledger itself holds hashes only.",
          member="struggle_detector.delete_user", entity="struggle_detector.erase_entity", markers=(".texts.jsonl",)),
    Store("self_review_records", "file", WORDS, REACHED, "~/.zoe/self-review/: the nightly self-review (self_review.py, ZOE_SELF_REVIEW). "
          "<night>.self-review.jsonl + <night>.md hold the OWNER's flagged turns verbatim (0600, never off-box; other members are ids and "
          "classes only); the summary is counts, ids and utterance hashes. An entity forget drops the member's records naming it (cascade "
          "step self_review); a member erase drops all of the member's records and summary events; load_reviews re-checks the ledger.",
          member="struggle_detector.delete_user", entity="self_review.erase_entity",
          markers=(".self-review.jsonl", ".self-review-summary.json")),
    Store("night_extract_facts", "file", WORDS, REACHED, "~/.zoe/night-extract/: the SHADOW night pre-pass (night_extract.py, ZOE_NIGHT_EXTRACT). "
          "<night>.night-extract.jsonl holds the facts the GLiNEREL extractor PROPOSED from the member's day (person names, ages, places, pet and kin "
          "words; 0600, never off-box, never a turn's text) and <night>.night-extract-summary.json counts only. An entity forget drops the member's facts "
          "naming it (cascade step night_extract); a member erase (struggle_detector.delete_user calls night_extract.delete_user) drops all of the member's facts and summary line; load_facts re-checks the ledger.",
          member="struggle_detector.delete_user", entity="night_extract.erase_entity",
          markers=(".night-extract.jsonl", ".night-extract-summary.json")),
    Store("self_improver_candidates", "file", DERIVED, REACHED, "~/.zoe/self-improver/candidate-cells.jsonl: synthetic asks the 12B "
          "invented in the SHAPE of a manner failure; a guard refuses any that shares a content word with the household's turn, a deny term, "
          "a name-like word, a number or an address, and no record names its turn or member. The entity forget sweeps it too (any ask naming "
          "the entity is dropped, self_review.erase_entity); with no member on a line, a member erase has nothing to locate.",
          entity="self_review.erase_entity", markers=("candidate-cells.jsonl",)),
    Store("warm_speaker_memo", "file", METADATA, REACHED, "~/.zoe/warm-speaker.json: panel id -> member id + time, the wake KV warm's likely speaker "
          "(a cache-shaping guess, never an identity, no words). Capped at 12 h; delete_user and voice-profile delete / consent revoke remove the member.",
          member="warm_speaker.forget_user", markers=("warm-speaker.json",)),
    Store("omi_audio_files", "file", BIOMETRIC, REACHED, "Pendant audio on disk goes with the recording (omi_recordings.forget_user removes rows and files).",
          member=_OMI_M, entity="omi_recordings.erase_entity", markers=("omi-audit.jsonl",)),
    Store("device_audit_logs", "file", METADATA, OUT_OF_SCOPE, "Audit trails of device/bluetooth/model-profile changes: ids and actions, no household words.",
          markers=("bluetooth-audit.jsonl", "model-profile-audit.jsonl")),
    Store("pi_intent_evidence", "file", WORDS, GAP, "~/.zoe/data/pi-*.jsonl: intent-miss evidence and shadow labels (text with names/emails masked). Flag-gated "
          "(ZOE_PI_INTENT_*), outside the cascade, no retention.",
          markers=("pi-intent-miss-evidence.jsonl", "pi-hybrid-production-evidence.jsonl", "pi-hybrid-production-labels.jsonl",
                   "pi-intent-shadow.jsonl", "pi-intent-shadow-labels.jsonl")),
    Store("engineering_pipeline_runs", "file", METADATA, OUT_OF_SCOPE, "Engineering pipeline run records (tickets/PRs), not member memory.",
          markers=("engineering_pipeline_runs.jsonl", "pipeline_runs.jsonl")),
    Store("sqlite_dev_db", "file", METADATA, OUT_OF_SCOPE, "database.py's zoe.db is the legacy SQLite fallback for dev; production is Postgres.",
          markers=("zoe.db",)),
    Store("app_logs", "file", WORDS, GAP, "~/.zoe-logs/zoe-data.*: the service log. Utterances are logged as len + hash only since 2026-11-03 (intent_miss: len=N utt=<hash>); "
          "lines written before then hold text until the size rotation drops them; "
          "rotated by size (50 MB x4), not by age, outside the cascade.", markers=("intent_miss:",)),
    Store("backup_postgres", "backup", WORDS, GAP, "Nightly dumps: 7 daily + 4 weekly, so a row is gone from every dump within "
          "BACKUP_MAX_AGE_DAYS (35) - WHILE the nightly job succeeds: it prunes by count after a new dump, so a job that keeps failing "
          "keeps its old dumps (an age alarm is the fix; a time prune must never delete the last good copy). Forget cannot rewrite a dump; "
          "biometric table data is excluded from them. Restore => re-issue the forget.",
          markers=("postgres-nightly-backup.sh",)),
    Store("backup_palace_tar", "backup", WORDS, GAP, "Nightly MemPalace tarball, 7 kept (<= 7 days WHILE the nightly job succeeds: it prunes by "
          "count after a new tarball, so a failing job keeps its old ones). Not rewritten; the JSON exports beside it are redacted "
          "by the ledger (redact_backups.py).", markers=("mempalace-nightly-backup.sh",)),
    Store("backup_json_exports", "backup", WORDS, REACHED, "Palace JSON exports and compaction backups are redacted by the forget ledger "
          "(forget_redact.redact_export, scripts/maintenance/redact_backups.py).", entity="forget_redact.redact_export",
          markers=("memory-exports", "palace-backups")),
    Store("backup_adhoc", "backup", WORDS, OUT_OF_SCOPE, "One-off backups an owner script makes (~/.zoe/backups/<tool>-<ts>/, mode 0700; the router-shadow one holds raw text, "
          "the env one holds secrets): ENFORCED deletion after ADHOC_BACKUP_MAX_AGE_DAYS (14) by the hourly sweep "
          "(local_text_logs.prune_adhoc_backups, fails the job if it cannot). Older hand-made dumps in ~/.zoe-backups are a ledgered cleanup.",
          markers=("migrate-hermes-env-", "router-shadow-", "dead-units-")),
)

STORES: tuple[Store, ...] = TABLES + OTHER
BY_KEY = {s.key: s for s in STORES}
GAPS = tuple(s.key for s in STORES if s.status == GAP)


# -- discovery ------------------------------------------------------------------------------------------------------------------------
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)\s*\(", re.I)
_ALTER_USER = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?user_id\b", re.I)
_REF = re.compile(r"REFERENCES\s+([A-Za-z_]\w*)", re.I)
_USERID_COL = re.compile(r'^\s*"?user_id"?\s', re.I | re.M)


def _bodies(text: str) -> Iterable[tuple[str, str]]:
    for m in _CREATE.finditer(text):
        i, depth, j = m.end(), 1, m.end()
        while j < len(text) and depth:
            depth += (text[j] == "(") - (text[j] == ")")
            j += 1
        yield m.group(1), text[i:j - 1]


def discover_user_tables(sources: Iterable[Path]) -> dict[str, str]:
    """``{table: why}`` for every table with a ``user_id`` column (``direct``) or that REFERENCES such a table (``child of X``),
    read from the CREATE TABLE / ALTER TABLE ADD COLUMN text of the given files."""
    direct: set[str] = set()
    refs: dict[str, set[str]] = {}
    for src in sources:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
        for name, body in _bodies(text):
            if _USERID_COL.search(body):
                direct.add(name)
            refs.setdefault(name, set()).update(_REF.findall(body))
        direct.update(m.group(1) for m in _ALTER_USER.finditer(text))
    found = {t: "user_id" for t in direct}
    changed = True
    while changed:
        changed = False
        for t, targets in refs.items():
            if t in found:
                continue
            parent = next((p for p in sorted(targets) if p in found), None)
            if parent:
                found[t] = f"references {parent}"
                changed = True
    return found


@functools.lru_cache(maxsize=16)
def _defined_function_names(source: str) -> frozenset[str]:
    """Every (async) function / method name defined anywhere in ``source`` (memoised by content: the inventory check asks about one
    4.6k-line module once per store)."""
    return frozenset(n.name for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def function_defined(py_file: Path, qualified: str) -> bool:
    """Is ``module.func`` (or ``module.Class.method`` / ``module._private_func``) defined in ``py_file``? Pure AST: no import."""
    name = qualified.split(".")[-1]
    return name in _defined_function_names(Path(py_file).read_text(encoding="utf-8"))


def uncovered_tables(discovered: Iterable[str]) -> list[str]:
    """Discovered per-member tables with no ``STORES`` entry: the list that must be empty."""
    return sorted(set(discovered) - {s.key for s in TABLES})


def stale_tables(discovered: Iterable[str]) -> list[str]:
    """Registry table entries that no migration creates any more (a rename or a drop): remove them."""
    return sorted({s.key for s in TABLES} - set(discovered))


# -- the generated table in docs/knowledge/forget-reach.md ----------------------------------------------------------------------------
BEGIN, END = "<!-- BEGIN GENERATED: forget_reach.py -->", "<!-- END GENERATED -->"


def render_markdown() -> str:
    def row(s: Store) -> str:
        by = " / ".join(x for x in (f"member: `{s.member}`" if s.member else "", f"entity: `{s.entity}`" if s.entity else "") if x) or "-"
        return f"| `{s.key}` | {s.kind} | {s.holds} | {s.status} | {by} | {s.why.replace('|', '/') or '-'} |"

    head = "| store | kind | holds | status | erased by | note |\n|---|---|---|---|---|---|"
    counts = {k: sum(1 for s in STORES if s.status == k) for k in (REACHED, OUT_OF_SCOPE, GAP)}
    summary = (f"{len(STORES)} stores: {counts[REACHED]} reached, {counts[OUT_OF_SCOPE]} out of scope, {counts[GAP]} known gaps "
               f"(`GAPS` in `services/zoe-data/forget_reach.py`).")
    return "\n".join([BEGIN, "", summary, "", head, *(row(s) for s in STORES), "", END])


def main(argv: Optional[list[str]] = None) -> int:
    """``python3 forget_reach.py --write docs/knowledge/forget-reach.md`` regenerates the table block in that page."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--write":
        path = Path(args[1])
        text = path.read_text(encoding="utf-8")
        a, b = text.index(BEGIN), text.index(END) + len(END)
        path.write_text(text[:a] + render_markdown() + text[b:], encoding="utf-8")
        return 0
    print(render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

---
type: Reference
title: Memory writer map (GENERATED)
description: Every code path in services/zoe-data that writes the memory drawers, derived from the AST - module, function, kind - with a role line per module. Replaces the hand-kept count in the 10-05 fidelity audit.
tags: [memory, writers, generated, audit]
---

# Memory writer map

**STATUS: GENERATED - do not hand-edit.** Regenerate with `python3 tools/audit/memory_writer_map.py`;
role lines live in [`memory-writer-notes.json`](memory-writer-notes.json) (one per writer module - a new writer
module fails `tests/unit/test_memory_writer_map.py` until it has one).

**72 write sites in 28 modules** (the 2026-10-05 fidelity audit counted 31 by hand; the 2026-10-18
deep dive found at least 39). A *write site* is one (module, enclosing function) that calls a `MemoryService`
write method or upserts a drawers collection directly.

Kinds: `archive_by_entity` 1, `archive_duplicate` 1, `compact_index` 1, `delete_user` 2, `erase_rows` 5, `flag_challenged` 1, `forget_last` 1, `ingest` 25, `raw_delete` 3, `raw_update` 6, `raw_upsert` 7, `record_candidate` 3, `relink_entity` 4, `retire_with_quote` 1, `review` 23, `scrub_residue` 1, `supersede_by` 4, `sweep_soft_archive` 1, `tick_access` 1.

## What counts

- `MemoryService` write methods: `ingest`, `record_candidate`, `supersede_by`, `restore_superseded`, `archive_duplicate`,
  `archive_by_entity`, `sweep_soft_archive`, `forget_last`, `retire_with_quote`, `erase_rows`, `relink_entity`, `scrub_residue`,
  `compact_index`, `edit_outranked`, `tick_access`, `tick_consolidation` (any receiver) and `review`, `delete_user` (on a recognised
  `MemoryService` receiver).
- `raw_upsert` / `raw_update` / `raw_delete` / `raw_add`: a direct write on a drawers collection handle. In `memory_service.py` these are the storage
  layer's own writes (the funnel). Anywhere else they **bypass the funnel's guards** (authority, tombstones, audit):
  `memory_digest.py`, `zoe_agent.py`.

**Not covered** (own tables, not memory-drawer writers): the people graph (`people`, `person_relationships`,
`person_activities`), exact-words, the night-mind and struggle stores, the forgotten-ledger, the Omi tables, and
the Flue/TS callers (`services/zoe-core/extensions/memory.ts`), which reach the drawers only through
`routers/memories.py` (listed below).

## Writers

| Module | Role | Write sites (function: kinds) |
|---|---|---|
| `contacts_conversation.py` | Conversational contacts (people_* intents): archives a contact's memory rows when the contact is renamed (review archive). | `refresh_person_mirror`: review |
| `correction_apply.py` | A spoken correction ("X is their dog", a day-first date) rewrites the stored fact (review edit) and ingests the corrected one; ZOE_CORRECTION_APPLY. | `apply_date_correction`: review<br>`apply_pet_correction`: ingest, review<br>`apply_row_correction`: review |
| `expert_dispatch.py` | Tier-1.5 domain experts: stores a voice_fact, retires the fact it replaces (review archive) and supersedes on a stated change. | `_ingest_or_supersede`: ingest, review, supersede_by |
| `hindsight_retain_candidates.py` | Hindsight evaluation path: creates a PENDING row for later admission (ingest). Not on the live turn path. | `create_hindsight_retain_candidate`: ingest |
| `hold_the_fact.py` | Hold the fact: restores the owner's stated value after a model pushback (review edit); ZOE_HOLD_THE_FACT. | `_apply_update`: review |
| `intent_router.py` | Intent-first router: the 'remember that' / 'forget that' / 'forget the last thing' fast paths (ingest, forget_last, review reject/archive). | `execute_intent`: erase_rows, forget_last, ingest, review<br>`execute_intent._erase_named`: erase_rows |
| `mcp_server.py` | MCP tools for Hermes/Pi skills: memory_add (ingest, brain_tool/mcp source) and the forget/edit tools (review). | `_execute_tool`: ingest, review |
| `memory_digest.py` | The nightly digest and dreaming passes: ingests digest facts/candidates, edits/archives/merges rows (review, archive_duplicate, sweep_soft_archive) and runs raw upserts for neighbour metadata and synthesis rows. | `_deep_sleep_pass`: raw_upsert<br>`_emotional_memory_pass`: ingest<br>`_merge_near_duplicates`: archive_duplicate<br>`_rem_reinforce_pass`: raw_upsert, tick_access<br>`_resolve_contradictions`: review<br>`_resolve_pending_person_links`: relink_entity<br>`_synthesis_pass`: ingest, raw_upsert<br>`run_memory_digest`: ingest, review<br>`run_music_taste_digest`: ingest<br>`run_turn_digest`: ingest, review<br>`run_weekly_consolidation`: sweep_soft_archive |
| `memory_disputes.py` | A held-back write becomes one question to the person: accepting approves the candidate, declining rejects it (review). | `expire_stale`: review<br>`resolve`: review |
| `memory_extractor.py` | The per-turn extractor (regex + LLM, chat_regex/voice_regex): ingests new facts and edits a same-attribute row on a stated change (review edit). | `extract_and_ingest`: ingest, review |
| `memory_retire.py` | Quote-backed retirement: a one-word change of state ("I gave up the cello") retires the right fact with the owner's words attached (retire_with_quote). | `decide`: retire_with_quote |
| `memory_service.py` | The STORAGE LAYER (funnel): its own raw drawers writes - _write_row, restore, compaction. Every other writer should reach the store through MemoryService, not around it. | `MemoryService._append_audit_sync`: raw_upsert<br>`MemoryService._delete_audit_for_rows_sync`: raw_delete<br>`MemoryService._delete_audit_for_user_sync`: raw_delete<br>`MemoryService._delete_ids`: raw_delete<br>`MemoryService._relink_entity_sync`: raw_update<br>`MemoryService._restore_superseded_sync`: raw_update, raw_upsert<br>`MemoryService._retire_with_quote_sync`: raw_update<br>`MemoryService._supersede_by_sync`: raw_update<br>`MemoryService._tick_access_sync`: raw_update<br>`MemoryService._tick_consolidation_sync`: raw_update<br>`MemoryService._write_row`: raw_upsert |
| `memory_space.py` | The panel's Memories space: per-item forget (review reject, with the exact-words twin). | `forget_row`: erase_rows, review<br>`retire_twins`: review |
| `memory_supersede.py` | Implicit supersession: a stated change of state retires the fact it replaces (supersede_by); ZOE_MEMORY_IMPLICIT_SUPERSEDE. | `nightly_conflict_pass`: supersede_by<br>`retire_by_claims`: supersede_by<br>`supersede_for_turn`: supersede_by |
| `mempalace_baseline.py` | Benchmark fixtures: ingests synthetic rows to score the store. Bench/lab use; never real members. | `run_mempalace_baseline`: ingest |
| `named_relations.py` | Named people after a relationship noun are kept, never summarised to a count (ingest, review edit). | `_apply_one`: relink_entity<br>`_ingest_user_fact`: ingest, review |
| `omi_review_apply.py` | Approve a pendant day-review candidate: the ONLY module that writes what the Omi day review proposed (ingest). | `unwrite`: erase_rows<br>`write_owner_fact`: ingest<br>`write_person_fact`: ingest |
| `person_extractor.py` | Person-fact extraction from conversation: ingests person rows and queues candidates (record_candidate), edits via review under the memory-authority wall. | `_edge_may_change`: record_candidate<br>`_hold_edge_belief`: record_candidate<br>`_hold_fact_belief`: record_candidate<br>`_ingest_to_mempalace`: ingest, relink_entity, review<br>`_reconcile_same_kind_entity_row`: relink_entity, review |
| `provenance_answers.py` | "Why did you say that?", the one-sentence fix, "forget that" and off-the-record: rejects the rows a forget or correction names (review reject). | `_forget_turn`: review<br>`fix`: review<br>`forget_it`: erase_rows, review |
| `routers/journal.py` | Journal API: a journal entry is mirrored into memory (ingest). | `_store_journal_memory`: ingest |
| `routers/memories.py` | The memories API: the explicit add/edit/review endpoints, plus the admin forget and the internal forget-synthetic teardown (ingest, review, delete_user). | `create_memory_proposal`: ingest<br>`forget_synthetic_user`: delete_user<br>`forget_user`: delete_user<br>`memory_compact_index_endpoint`: compact_index<br>`memory_scrub_residue_endpoint`: scrub_residue<br>`review_memory`: review |
| `routers/notes.py` | Notes API: a note is mirrored into memory (ingest). | `_store_note_memory`: ingest |
| `routers/people.py` | People API: ingests person facts and archives a person's rows when the person is removed or merged (ingest, archive_by_entity). | `_archive_person_mempalace`: archive_by_entity<br>`_store_person_memory`: ingest |
| `routers/user_profile.py` | User profile API: profile facts are mirrored into memory (ingest). | `analyze_profile`: ingest |
| `skybridge_service.py` | Skybridge card actions: a fact the member confirms on a card is stored (ingest, source skybridge_action). | `_store_skybridge_memory_fact`: ingest |
| `trait_challenge.py` | The owner hedges or denies a trait Zoe holds about them: parks the matching approved row as pending (flag_challenged); text and provenance untouched, an approve restores it. | `apply_challenge`: flag_challenged |
| `zoe_agent.py` | The agent loop's memory tool (ingest, brain_tool/zoe_agent source) and a one-time startup migration of legacy palace rows (a RAW upsert that bypasses the funnel). | `_background_memory_save`: ingest<br>`_mempalace_add`: ingest<br>`migrate_mempalace_legacy_records`: raw_upsert |
| `zoe_memory_prompt_packet_measure.py` | Measurement helper: ingests fixtures to time the prompt packet. Bench use; never real members. | `seed_prompt_packet_measure_memories`: ingest |

"""Every call of a mutating ``MemoryService`` method in the service, and WHO is acting at each (``tests/test_memory_one_writer.py``).

``member``: a household member asked for this change (spoken, typed, tapped, or a tool the brain called for them).
``automatic``: no member asked for THIS write: a per-turn extractor, a night pass, a mirror of a change another area's door
already decided (a note, a journal entry, a contact), an eraser finishing an earlier forget, a seeder.
``passes_on``: the service calling itself for the caller it is already serving.
"""
MUTATING = ("ingest", "delete_user", "sweep_soft_archive", "review", "supersede_by", "retire_with_quote", "set_curated_standing", "archive_duplicate", "flag_challenged", "record_candidate", "erase_rows", "relink_entity", "archive_by_entity")

#: (module, function, method) -> (who is acting, how many calls)
SITES = {
    ('contacts_conversation.py', 'refresh_person_mirror', 'review'): ('automatic', 1),
    ('correction_apply.py', 'apply_date_correction', 'review'): ('member', 1),
    ('correction_apply.py', 'apply_pet_correction', 'ingest'): ('member', 1),
    ('correction_apply.py', 'apply_pet_correction', 'review'): ('member', 1),
    ('correction_apply.py', 'apply_row_correction', 'review'): ('member', 1),
    ('expert_dispatch.py', '_ingest_or_supersede', 'ingest'): ('member', 1),
    ('expert_dispatch.py', '_ingest_or_supersede', 'review'): ('member', 1),
    ('expert_dispatch.py', '_ingest_or_supersede', 'supersede_by'): ('member', 1),
    ('forget_topic.py', '_forget', 'erase_rows'): ('member', 1),
    ('forget_topic.py', '_forget', 'review'): ('member', 1),
    ('hindsight_retain_candidates.py', 'create_hindsight_retain_candidate', 'ingest'): ('automatic', 1),
    ('hold_the_fact.py', '_apply_update', 'review'): ('member', 1),
    ('intent_router.py', '_execute_forget_last', 'erase_rows'): ('member', 1),
    ('intent_router.py', '_execute_forget_last._reject', 'review'): ('member', 1),
    ('intent_router.py', 'execute_intent', 'ingest'): ('member', 1),
    ('intent_router.py', 'execute_intent', 'review'): ('member', 1),
    ('intent_router.py', 'execute_intent._erase_named', 'erase_rows'): ('member', 1),
    ('mcp_server.py', '_execute_tool', 'ingest'): ('member', 1),
    ('mcp_server.py', '_execute_tool', 'review'): ('member', 2),
    ('memory_curation.py', '_add_locked', 'ingest'): ('member', 1),
    ('memory_curation.py', '_edit_locked', 'review'): ('member', 1),
    ('memory_curation.py', '_forget_locked', 'erase_rows'): ('member', 1),
    ('memory_curation.py', '_forget_locked', 'review'): ('member', 1),
    ('memory_curation.py', 'decide_held', 'review'): ('member', 1),
    ('memory_curation.py', 'set_my_optin._restamp', 'set_curated_standing'): ('member', 1),
    ('memory_digest.py', '_emotional_memory_pass', 'ingest'): ('automatic', 1),
    ('memory_digest.py', '_merge_near_duplicates', 'archive_duplicate'): ('automatic', 1),
    ('memory_digest.py', '_resolve_contradictions', 'review'): ('automatic', 1),
    ('memory_digest.py', '_resolve_pending_person_links', 'relink_entity'): ('automatic', 1),
    ('memory_digest.py', '_synthesis_pass', 'erase_rows'): ('automatic', 1),
    ('memory_digest.py', '_synthesis_pass', 'ingest'): ('automatic', 1),
    ('memory_digest.py', 'run_memory_digest', 'ingest'): ('automatic', 2),
    ('memory_digest.py', 'run_memory_digest', 'review'): ('automatic', 2),
    ('memory_digest.py', 'run_music_taste_digest', 'ingest'): ('automatic', 1),
    ('memory_digest.py', 'run_turn_digest', 'ingest'): ('automatic', 1),
    ('memory_digest.py', 'run_turn_digest', 'review'): ('automatic', 1),
    ('memory_digest.py', 'run_weekly_consolidation', 'sweep_soft_archive'): ('automatic', 1),
    ('memory_disputes.py', 'expire_stale', 'review'): ('automatic', 1),
    ('memory_disputes.py', 'resolve', 'review'): ('member', 1),
    ('memory_extractor.py', 'extract_and_ingest', 'ingest'): ('automatic', 2),
    ('memory_extractor.py', 'extract_and_ingest', 'review'): ('automatic', 1),
    ('memory_retire.py', 'decide', 'retire_with_quote'): ('automatic', 1),
    ('memory_service.py', '_undo_night_write', 'erase_rows'): ('passes_on', 1),
    ('memory_service.py', 'archive_by_entity', 'review'): ('passes_on', 1),
    ('memory_service.py', 'sweep_soft_archive', 'review'): ('passes_on', 1),
    ('memory_space.py', 'forget_row', 'erase_rows'): ('member', 1),
    ('memory_space.py', 'forget_row', 'review'): ('member', 2),
    ('memory_space.py', 'guardian_forget', 'erase_rows'): ('member', 1),
    ('memory_space.py', 'guardian_forget', 'review'): ('member', 1),
    ('memory_space.py', 'retire_twins', 'review'): ('member', 1),
    ('memory_supersede.py', 'nightly_conflict_pass', 'supersede_by'): ('automatic', 1),
    ('memory_supersede.py', 'retire_by_claims', 'supersede_by'): ('automatic', 1),
    ('memory_supersede.py', 'supersede_for_turn', 'supersede_by'): ('automatic', 1),
    ('mempalace_baseline.py', 'run_mempalace_baseline', 'ingest'): ('automatic', 1),
    ('named_relations.py', '_apply_one', 'relink_entity'): ('automatic', 1),
    ('named_relations.py', '_ingest_user_fact', 'ingest'): ('automatic', 1),
    ('named_relations.py', '_ingest_user_fact', 'review'): ('automatic', 1),
    ('omi_review_apply.py', 'unwrite', 'erase_rows'): ('automatic', 1),
    ('omi_review_apply.py', 'write_owner_fact', 'ingest'): ('member', 1),
    ('omi_review_apply.py', 'write_person_fact', 'ingest'): ('member', 1),
    ('person_extractor.py', '_edge_may_change', 'record_candidate'): ('automatic', 1),
    ('person_extractor.py', '_hold_edge_belief', 'record_candidate'): ('automatic', 1),
    ('person_extractor.py', '_hold_fact_belief', 'record_candidate'): ('automatic', 1),
    ('person_extractor.py', '_ingest_own_fact', 'ingest'): ('automatic', 1),
    ('person_extractor.py', '_ingest_to_mempalace', 'ingest'): ('automatic', 1),
    ('person_extractor.py', '_ingest_to_mempalace', 'relink_entity'): ('automatic', 1),
    ('person_extractor.py', '_ingest_to_mempalace', 'review'): ('automatic', 1),
    ('person_extractor.py', '_reconcile_same_kind_entity_row', 'relink_entity'): ('automatic', 1),
    ('person_extractor.py', '_reconcile_same_kind_entity_row', 'review'): ('automatic', 2),
    ('provenance_answers.py', '_forget_turn', 'review'): ('member', 1),
    ('provenance_answers.py', 'fix', 'review'): ('member', 1),
    ('provenance_answers.py', 'forget_it', 'erase_rows'): ('member', 2),
    ('provenance_answers.py', 'forget_it', 'review'): ('member', 2),
    ('routers/journal.py', '_store_journal_memory', 'ingest'): ('automatic', 1),
    ('routers/memories.py', 'create_memory_proposal', 'ingest'): ('member', 1),
    ('routers/memories.py', 'forget_synthetic_user', 'delete_user'): ('automatic', 1),
    ('routers/memories.py', 'forget_user', 'delete_user'): ('member', 1),
    ('routers/memories.py', 'review_memory', 'review'): ('member', 1),
    ('routers/notes.py', '_store_note_memory', 'ingest'): ('automatic', 1),
    ('routers/people.py', '_archive_person_mempalace', 'archive_by_entity'): ('automatic', 1),
    ('routers/people.py', '_store_person_memory', 'ingest'): ('automatic', 1),
    ('routers/user_profile.py', 'analyze_profile', 'ingest'): ('automatic', 1),
    ('skybridge_service.py', '_store_skybridge_memory_fact', 'ingest'): ('member', 1),
    ('trait_challenge.py', 'apply_challenge', 'flag_challenged'): ('member', 1),
    ('turn_forget.py', 'finish_parked', 'erase_rows'): ('automatic', 1),
    ('value_correction.py', 'apply', 'review'): ('member', 1),
    ('zoe_agent.py', '_background_memory_save', 'ingest'): ('automatic', 1),
    ('zoe_agent.py', '_mempalace_add', 'ingest'): ('member', 1),
    ('zoe_memory_prompt_packet_measure.py', 'seed_prompt_packet_measure_memories', 'ingest'): ('automatic', 1),
}

#: the same method names on something that is NOT the memory service (another store's own eraser or review)
NOT_THE_SERVICE = {
    ('evolution_inbox.py', '_failed_turn_cards', 'review'),   # turn_outcomes
    ('exact_words.py', 'delete_user', 'delete_user'),   # get_backend()
    ('memory_service.py', 'delete_user', 'delete_user'),   # struggle_detector
    ('night_mind.py', 'delete_user', 'delete_user'),   # night_store.get_backend()
    ('routers/struggles.py', 'failed_turns', 'review'),   # turn_outcomes
    ('struggle_detector.py', '_delete_user_locked', 'delete_user'),   # turn_outcomes.get_backend()
}

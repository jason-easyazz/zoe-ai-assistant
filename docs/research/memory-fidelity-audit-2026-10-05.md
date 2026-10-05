---
type: research
title: Memory fidelity audit — "Samantha doesn't have dementia, neither can Zoe" (2026-10-05)
date: 2026-10-05
status: research-only; nothing built. Read-only audit of the write paths, the authority model, the read paths and the live palace, a field comparison, and a ranked plan to make an inferred write unable to overrule a user-stated fact.
description: Deep audit of Zoe's memory against the owner's 2026-10-05 directive. Every code path that creates, supersedes, archives, merges or rewrites a memory or person/relationship row (file:line, inputs, what it may overwrite, provenance, user-told, history, tests); the authority matrix (14 retire-capable paths, 0 check provenance, 8 act on model-written or model-judged text); read-path risks (HNSW, filtered search, sticky sessions, the live recall flag, isolation, temporal validity, forgetting); read-only live-palace numbers (shapes only); the field (mem0, Letta/MemGPT, Zep/Graphiti, ChatGPT, Anthropic, Nomi, LoCoMo/LongMemEval) with sources; and a ranked P1-P3 plan with a test and a negative control for every item.
---

# Memory fidelity audit (2026-10-05)

The owner's standing directive, verbatim, given 2026-10-05: *"The memory needs to be flawless, it
needs to be as good as anything else out there, Samantha doesn't have dementia, neither can Zoe."*

Trigger incident (identity half fixed by PR #1866,
`docs/knowledge/identity-and-session-continuity.md` on that branch and
`services/zoe-data/tests/test_identity_facts.py`; at the time of writing #1866 is open and **not** in this
worktree's base, so every line number below is `main` @ `08538155`): on 2026-08-11 the 03:00 nightly digest
read a day transcript containing a panel speech-to-text fragment that named a third person, the
extractor asserted "User's name is X", and the contradiction pass called `MemoryService.review(edit)`,
which **superseded the owner's genuine row and carried its `source`/`session_id` forward**, so the wrong
row looked like a regex/Telegram write. The owner's generalisation: this must be impossible for **any
fact**, not just identity.

How this was produced: code read at the cited lines; the palace read through
`sqlite3 file:~/.mempalace/chroma.sqlite3?mode=ro` (journal mode `delete`, so a read-only open is safe
while zoe-data runs) and Postgres through `docker exec zoe-database psql -Atc` (selects only); three
behaviours reproduced against the **real** `MemoryService` / `memory_supersede` over the test suite's
in-memory collection (scratch scripts, not committed, nothing written to the live palace); the field half
by a web-research sub-agent (vendor numbers are self-reported; OpenAI's own pages returned HTTP 403, so
ChatGPT is [secondary] throughout). **No household name, address or utterance is quoted** — shapes and
counts only. `[unverified]` marks anything not read at source.

## 0. TL;DR

1. **There is no authority model.** `MemoryService` has no notion of who asserted a row. `source` /
   `added_by` are free strings written by the caller (`memory_service.py:1861-1863`), `confidence` is the
   writer's own number, and `review(edit)` **copies the edited row's `source`, `session_id`,
   `user_turn_id` and `source_excerpt` onto the replacement** (`memory_service.py:1596-1599`) and only
   adds `reviewed_by=<actor>` (`:1649`). Provenance is laundered by design: an LLM edit of a
   voice-dictated row produces a row that says `source=voice_fact`. Reproduced (§2.3, S1).
2. **14 code paths can retire or replace an existing row; 0 of them look at the existing row's
   provenance.** Eight act on model-written or model-judged text (turn digest, nightly contradiction judge, nightly
   reconcile, idle consolidation, weekly merge, weekly contradiction judge, the live nightly
   implicit-conflict pass, the LLM person extractor); four more are authority-blind by construction
   (decay archive, relationship-edge supersede, write-time implicit supersede, MCP agent edit-as-user);
   two are user-authored. The incident is the general case, not a one-off; #1866 closed one attribute
   (the user's name) with a **deny-list** of eleven source strings, so any source string it does not
   list still passes.
3. **The "explicit teach" class contains model-authored text.** `brain_tool` (the Flue brain's
   `remember_fact`, `intent_router.py:3653-3705`) is in `EXPLICIT_TEACH_SOURCES`
   (`memory_tombstones.py:40`) next to `voice_fact` and `review_ui`, so the model's own paraphrase is
   exempt from the tombstone guard and from #1866's identity block.
4. **Live palace (owner `jason`, 2026-10-05):** 103 rows, 82 approved, 17 superseded, 4 archived.
   Only **4 of the 82 approved rows (5%)** are unambiguously the user's own words (deterministic regex or
   dictated); **43 (52%)** are LLM-only (idle consolidation 27, nightly digest 10, voice turn digest 4,
   turn digest 2); 31 (38%) come from the person extractors, whose regex and LLM halves share one
   `source` string and cannot be told apart; 3 are UI-authored; 1 is a model-written `brain_tool` row.
   The evidence quote (`source_excerpt`) is present on **3 of 82**. The incident signature (approved,
   `source=chat_regex`, `reviewed_by=digest`) is **0** today because the polluted row was superseded by the
   operator audit tool — whose replacement row is itself stamped `source=chat_regex` (the laundering,
   visible in the live data).
5. **The audit trail contradicts the summary.** 6,605 `edit` audit rows exist; **6,585 are no-op
   in-place rewrites** by the weekly consolidation (`supersedes_id` equal to the row's own id,
   `reviewed_by=consolidation` stamped on rows nobody reviewed), on a fixture-shaped legacy user id;
   only **20** are real supersessions in 93 days across all users. The weekly "merged N near-duplicates"
   figure is mostly fiction.
6. **Forgetting is not durable.** "Forget everything about X" archives matching rows and writes an
   **in-process 300 s tombstone** (`memory_tombstones.py:33`, `intent_router.py:3541`); the nightly digest
   re-reads the day's `chat_messages` (which still contain X) and can resurrect it after five minutes.
   Derived stores (user portrait, user-model card, open loops, the `people` row, proactive candidates) are
   not touched.
7. **Plan:** P1 = one authority choke point in `MemoryService` (allow-list of provenance classes,
   fail-closed, shadow-then-enforce), digest facts anchored to a verbatim user span, unverified-speaker
   self-facts become candidates, and a fidelity regression pack with negative controls. P2 = temporal
   validity always on, durable forgetting with cascade, a nightly fidelity report. P3 = the churn / decay /
   stale-upsert / guest-bucket fixes. §6.

## 1. Write-path map

Legend. **Inputs** says whose words the writer reads. **Overwrites** is the strongest thing it may do to
an *existing* row. **Told** is whether the user hears about it. **Hist** is whether the old row survives.
"Audit" is the `mempalace_audit` collection (`memory_service.py:2539`), one row per
ingest/approve/reject/archive/edit, written best-effort; hard deletes write none (§3.7). Flags are the
**live** values from `services/zoe-data/.env` (the unit's `EnvironmentFile=`, read 2026-10-05).

### 1.0 The storage layer every writer funnels through

| Piece | What it does | Where |
|---|---|---|
| `ingest` | Opt-out wall for `MEMORY_OPT_OUT_SOURCES` only; PII scrub; forget-tombstone drop unless the source is explicit-teach; idempotency; durable dedup (a row that is not `pending` is never rewritten); upsert; audit | `memory_service.py:1126-1271` (opt-out `:1159`, tombstone `:1180`, dedup `:1244`) |
| `review(approve/reject/archive)` | Flips `status`, stamps `reviewed_by=actor` | `:1512`, `:1556-1580` |
| `review(edit)` | New row first, then the old row `superseded` (+`superseded_by_id`; `invalid_at` only if `ZOE_MEMORY_IMPLICIT_SUPERSEDE`); **the new row inherits the old `source` / `session_id` / `user_turn_id` / `source_excerpt` unless the caller passes a new excerpt**; `supersedes_id`, `reviewed_by` | `:1581-1688` (carry-forward `:1596-1599`, `:1648-1649`) |
| `supersede_by` | Metadata-only retire of an existing pair (`status=superseded`, `invalid_at`, `valid_from`) | `:1690-1740` |
| `_build_metadata` | `source`, `added_by` (= source), `confidence`, `valid_from` (only when the implicit-supersede flag is on) | `:1825-1918` |
| Authority / provenance class | **Does not exist.** No field records user-stated vs model-inferred, speaker verification, or asserted-by | — |

A caller can also bypass `MemoryService` and call `col.upsert` on a leased collection: the REM pass, the
deep-sleep pass, the synthesis back-link and the legacy migration do (`memory_digest.py:1569`, `:1573`,
`:1668-1673`, `:1799`; `zoe_agent.py:4560`). The "sole read/write surface" docstring
(`memory_service.py:1109`) is true for ingest and review, not for these.

### 1.1 Per-turn writers (run after every chat/voice turn)

The fan-out is `routers/chat.py:998-1033` (chat) and `routers/voice_tts.py:2958-2985` (voice): four
background writers run on the **user's turn only** (the assistant reply is passed but deliberately never
mined — contract at `memory_extractor.py:678`, pinned by `tests/test_memory_extractor_purity.py`). On the
voice lane the user id falls back through `identified_user_id or _bound_user or _panel_recent_user or
_panel_default_user` (`routers/voice_tts.py:3201-3208`): **a turn the speaker-ID did not accept is still
attributed to the panel's bound user, with no marker on the memory row**.

| # | Writer | Trigger | Inputs | May overwrite | Provenance recorded | Told | Hist | Tests |
|---|---|---|---|---|---|---|---|---|
| W1 | **Regex extractor** `memory_extractor.extract_and_ingest` (`:790`), `source=chat_regex` / `voice_regex` (and `voice_fact` via `expert_dispatch.py:576`) | every turn | the user turn (+ prior user turn for anaphora/corrections); templates incl. `my name is` → `User's name is` @0.92 (`:80`) | **yes** — `reconcile_for_ingest` UPDATE → `review(edit)` (`:931-949`); a same-attribute candidate with similarity ≥ 0.45 and a different value supersedes (`memory_quality.py:384`) | source, confidence 0.72-0.95, session_id, user_turn_id, `source_excerpt` (whole utterance) | no (silent) | yes (superseded) | `test_memory_extractor_*`, `test_cross_writer_supersede.py` |
| W2 | **Turn digest** `memory_digest.run_turn_digest` (`:459`), `source=turn_digest` / `voice_turn_digest` | every turn ≥ 4 words | the user turn → Gemma paraphrase → third-person fact (@0.82, `approved`) | **yes** — same reconcile UPDATE → `review(edit, actor="turn_digest")` (`:636-662`); plus W11 | source (**inherited from the old row on an edit**), excerpt = the user's whole turn | no | yes | `test_memory_implicit_supersede.py`, `test_continuity_affect.py` |
| W3 | **Person extractor, regex** `person_extractor.process_text` (`:1079`), `source=conversation` / `voice` | every turn | the user turn | **yes** — entity-keyed same-kind supersede (`:404-466`, review at `:439`), text-reconcile UPDATE (`:529-584`); **graph edges**: `_write_relationship` closes the pair's current edge when `rel_type` differs (`:792-955`; `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1`) | memory row: source + excerpt. **Edge: no source / asserted-by / evidence column** (`person_relationships`: id, user_id, person_a/b, rel_*, notes, created/updated, valid_from/to, superseded_by) | no | memory yes; edge yes (`valid_to`) | `test_person_extractor*.py`, `test_entity_keyed_supersede.py`, `test_temporal_relationships.py` |
| W4 | **Person extractor, LLM** `person_extractor_llm.process_text_llm` (`:185`) → `apply_person_fact` (`person_extractor.py:996`) | every turn ≥ 4 words | the user turn → Gemma JSON; anchor/role validators drop unanchored roles | **yes** — same `_ingest_to_mempalace` reconcile/supersede as W3, **under the same `source` string as W3**, so regex and model writes are indistinguishable | as W3 | no | yes | `test_person_extractor_llm_*.py`, `test_roles_not_guessed.py` |
| W5 | **Latent-intent suggestions** `latent_intent_detector.detect_and_store` | every turn | the user turn | **no** — writes `pending_suggestions` offers, never memory; a person offer becomes a contact only after the user says yes (`pending_suggestions.py:697`) | offer row | yes (asked) | n/a | `test_pending_*` |

**W5 is the pattern the directive wants** — inference adds a reviewable candidate, the user decides — and
it is applied to contacts but to no memory row.

### 1.2 Background / batch writers

| # | Writer | Trigger (live?) | Inputs | May overwrite | Provenance | Told | Hist | Tests |
|---|---|---|---|---|---|---|---|---|
| W6 | **Nightly digest, facts** `run_memory_digest` (`memory_digest.py:717`), `source=digest` | 03:00 nightly (`scripts/maintenance/daily_consolidation.py`, `routers/system.py:834`) | **all `role='user'` chat turns of the last window joined by newline, `LIMIT 200` (`:986`), then the FIRST 3,000 characters (`:1037`)**; per-turn speaker, time and session are discarded (`_load_todays_messages` `:969`) | **yes** — (a) contradiction pass: top-3 semantic neighbours, Gemma judge, `review(edit, actor="digest")` (`:794-826`) — the incident path; (b) reconcile UPDATE (`:840-860`) | `source=digest`; **no excerpt, session or turn id on a fresh row; on an edit the old row's source/excerpt ride along** | no | yes | `test_memory_digest_*` (SQL / window / verdict only; no authority test) |
| W7 | **Emotional pass** `_emotional_memory_pass` (`:894`), `source=digest`, `emotional_moment` @0.9 | after W6, same transcript | same day transcript | add-only (approved) | source only | no | n/a | `test_memory_emotional_recall.py` |
| W8 | **Idle consolidation** `memory_idle_consolidation.consolidate_session` (`:281`), `source=idle_consolidation`, **`ZOE_IDLE_CONSOLIDATION_ENABLED=1`** | ~3 min idle-session sweep | **`"{role}: {content}"` for every row — assistant turns included** (`:313`) fed to the extractor whose prompt says "user turns only" (`memory_digest.py:278-290`) | **yes** — `expert_dispatch._ingest_or_supersede` (`:391`) → reconcile UPDATE → ingest the new row then **archive** the old (`:474`) | source, session_id, stable turn id; no excerpt; **no anchor validator** (W6 has the relationship anchor, W8 has none) | no | archived (not `superseded`, no `superseded_by_id`) | `test_memory_idle_consolidation.py` (pins that the extractor sees the whole conversation) |
| W9 | **Weekly consolidation, merge** `_merge_near_duplicates` (`:1146`), `actor=consolidation` | weekly (Sunday) | approved rows only; keeper = highest `confidence` (`:1154-1159`); lexical containment ≥ 0.85 (`:1169`) | **yes** — `review(edit, edits=keeper.text)` on the weaker row (`:1174`): the weaker row's text is replaced by the keeper's, whoever wrote it; counts `merged += 1` even when the edit hashed to the same id (no-op) | `reviewed_by=consolidation` stamped regardless | no | **a no-op rewrite in 6,585 of 6,605 audit edits** | none pins it (only `test_memory_digest_idle_verdict.py` reads its summary) |
| W10 | **Weekly consolidation, contradictions** `_resolve_contradictions` (`:1193`) | weekly | newest 200 approved rows; pairs with lexical overlap ≥ 0.25; Gemma judge (`:1222`) | **yes** — the *older* row is replaced by the newer's text (`:1225-1232`); "newer" is `added_at`, not authority | `reviewed_by=consolidation` | no | yes | `test_memory_opt_out_endpoints.py` (opt-out only) |
| W11 | **Implicit supersede, write time** `memory_supersede.supersede_for_turn` (`:254`), called from W2 | `ZOE_MEMORY_IMPLICIT_SUPERSEDE=1` **live**; needs a change cue ("no longer", "used to", "moved to" …) in the *user's* words | the LLM-written fact + the user's cue | **yes** — `supersede_by(old, new)` for ≤ 3 older approved rows whose *topic* matches (`:288`) | `ACTOR=implicit_supersede`, `invalid_at` / `valid_from` | no | yes | `test_memory_implicit_supersede.py` |
| W12 | **Implicit supersede, nightly** `nightly_conflict_pass` (`:337`), dreaming Phase 1.7 (`memory_digest.py:2110-2118`), same flag, cap 10/user/night | nightly | **stored rows only — no user words at all**: any newer approved row carrying a cue word, or a different "home" value, retires the older same-topic row (`conflict_pairs` `:308-334`) | **yes — reproduced (§2.3 S4)** | `ACTOR`, note | no | yes | `test_memory_implicit_supersede.py` (pins cue behaviour, not authority) |
| W13 | **REM reinforce** `_rem_reinforce_pass` (`:1485`) | nightly | today's rows | metadata only (`related_ids`, `concept_tags`, `consolidation_count`), but via a **raw `col.upsert` of a metadata dict read before two awaits** (`:1569`, `:1573`) outside the per-user lock: a forget/supersede that lands in between is overwritten with the stale `status` | none | no | n/a | none |
| W14 | **Deep sleep** `_deep_sleep_pass` (`:1629`) | weekly | pending rows; score = 0.30·writer confidence + 0.24·access + … (`:1583`) | **promotes `pending`→`approved` on popularity** (score ≥ 0.8 and ≥ 3 distinct queries, `:1663`) **and archives pending rows older than 14 days** (`:1668`), raw upsert | `consolidation_count` only | no | n/a | none |
| W15 | **Synthesis** `_synthesis_pass` (`:1721`), `source=synthesis` | weekly | clusters of ≥ 5 approved rows by concept tag → Gemma one-sentence "insight" | add-only, **approved** @0.85 (`:1782`) | `related_ids` link | no | n/a | `test_memory_digest_synthesis_budget.py` |
| W16 | **Decay archive** `MemoryService.sweep_soft_archive` (`:1438`), `actor=consolidation`, called by the weekly pass (`memory_digest.py:1283`) | weekly | every approved row: score = confidence·e^(−ln2/70·age_days) + 0.1·ln(1+access) | **archives** rows with score < 0.02 and age ≥ 30 d — with no recall a 0.7-confidence row crosses at ~359 days, a 0.92 one at ~387 | `reviewed_by`, note | no | archived | `test_memory_service_metadata.py` (partial) |
| W17 | **Link resolver** `_resolve_pending_person_links` (`:116`), `ZOE_MEMORY_LINK_RESOLVER_ENABLED=1` | dreaming | `person_pending` rows by slug | relinks `entity_id` on a unique name match (`relink_entity` `memory_service.py:2484`); no text change | none | no | metadata overwrite | `test_memory_linkage_hygiene.py` |
| W18 | **Music digest** `run_music_taste_digest` (`:2201`), `source=music_digest` | nightly | listening events (not speech) | add-only @0.85 (`:2335`) | none | no | n/a | `test_music_*` |
| W19 | **Open loops** `_extract_open_loops` (`:1889`) | nightly | the day transcript (same as W6) | writes the `open_loops` table, not the palace | table row | via the brief | table | `test_memory_open_loops_extract.py` |

### 1.3 Explicit / user-driven and agent writers

| # | Writer | Trigger | Inputs | May overwrite | Provenance | Told | Hist | Tests |
|---|---|---|---|---|---|---|---|---|
| W20 | **Voice teach** `expert_dispatch.store_fact` (`:487`) → `_ingest_or_supersede` (`:638`), `source=voice_fact` @0.85 | "remember …", "note that …" | the user's utterance | **yes** — reconcile UPDATE archives the old row (`:474`) | source, excerpt via W1 | yes ("Got it — I'll remember …") | archived | `test_forget_tombstones.py`, `test_honest_confirm_entity_forget.py` |
| W21 | **Brain tool** `intent_router` `memory_store` (`:3653-3705`) ← Flue `remember_fact` / `remember_emotional_moment` (`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts:962-1027`), `source=brain_tool` @0.85 / 0.8 | the model chooses to call the tool | **model-chosen text** (the 4B brain's paraphrase) | add-only (no reconcile) — but `brain_tool` sits in `EXPLICIT_TEACH_SOURCES`, i.e. exempt from the forget-tombstone drop and from #1866's identity guard | source, idempotency key; no excerpt | the brain says "Got it" | n/a | `test_intent_dispatch.py` |
| W22 | **MCP memory tools** `mcp_server.py:3105-3240`: `memory_add` (`source="mcp"`), `memory_review(edit/approve/reject)`, `memory_forget` | any MCP-connected agent | agent-chosen text | **yes** — `review(edit)` with `actor=user_id` (`:3202`): the audit says the *user* edited it | actor = the user id | no | yes | none found |
| W23 | **Legacy brain tools** `zoe_agent._mempalace_add` (`zoe_agent.py:1359`; callers `:3147`, `:3170`), `source=zoe_agent` @0.7 | `mempalace_add` / `memory_update` tool calls on the legacy path | model `summary` | add-only | source | "Memory stored." | n/a | `test_zoe_agent_*` |
| W24 | **Review UI** `routers/memories.py:225` (`approve/reject/edit`, actor = session user), proposals `:165` | user in the Memories page | the user | yes | actor | yes | yes | `test_memory_opt_out_endpoints.py` |
| W25 | **Spoken forget** `memory_forget_last` (`intent_router.py:3522`, ≤ 10 min window, `memory_service.py:1404`) and `memory_forget_entity` (`:3541`): archive every row whose text contains the name as a whole word; add a 300 s in-process tombstone | "forget that" / "forget everything about X" | the user | archive (soft) | `reviewed_by=<user>`, note | yes ("I've forgotten N things") | archived rows remain in Chroma | `test_forget_tombstones.py`, `test_honest_confirm_entity_forget.py` |
| W26 | **Correction apply** `correction_apply.maybe_apply` (`:490`), **`ZOE_CORRECTION_APPLY` unset = OFF live** | date/pet corrections ("that date is wrong", "X is their dog") | the user's correction + their prior messages | edits rows holding the raw digits / a child-claim (`:241`, `:455`) + one ingest (`:470`) | `source` **carried from the old row** (the row says `chat_regex` though the user corrected it) | yes ("Fixed: …") | yes | `test_correction_apply.py` |
| W27 | **Contacts / people UI** `routers/people.py:144` (`person_created` / `person_updated` mirror rows @0.85), `:570` archive on delete; `contacts_conversation.py:794` archive on rename | the user's contact edits | the user | archives mirrors | source | yes | archived | `test_contacts_conversation.py` |
| W28 | **Notes / journal / profile** `routers/notes.py:63` (`note_*` @0.75), `routers/journal.py:103` (`journal_*` @0.7), `routers/user_profile.py:82` (`profile-analysis` @0.8 — a computed mood/topic count blob) | the user saves a note/entry; profile analysis | the user's text / a computed summary | add-only | source | yes (UI) | n/a | `test_notes_*` |
| W29 | **Skybridge actions** `skybridge_service._store_skybridge_memory_fact` (`:3410-3440`, `source=skybridge_action` @0.86) | spoken people actions | the user's utterance | add-only | `source_excerpt` = person name | yes | n/a | `test_skybridge_*` |
| W30 | **Hindsight retain** `hindsight_retain_candidates.create_hindsight_retain_candidate` (`:261`) | `HINDSIGHT_ENABLED` default false, **not in .env = dark** | admitted events | writes `pending` | admission id | no | n/a | `test_hindsight_memory.py` |
| W31 | **People-graph direct writers**: `routers/people.py` (CRUD, merge via `person_merge.py`), `person_extractor._create_partial_person` (`:279`, flag-gated birthday stubs), `pending_suggestions._execute_action` (`:515`, `context='suggested'`) | UI / confirmed offer | the user | rows soft-deleted (`deleted=1`), edges `valid_to` | **`people`: no source / asserted-by column; `person_activities` has `source` + `mem_id`; `person_relationships`: none** | yes (UI) | yes | `test_person_merge.py`, `test_relationship_features_integration.py` |

Observations that are not in the table:

* The only automatic guards on *what may be stored* are `memory_quality.is_storable_fact` (shape) and
  `user_relationship_claim_unsupported` / `named_role_claim_unsupported` (relationship and role anchors).
  W2 and the LLM half of W4 run the anchor checks against the single source turn; **W6 runs it against an
  empty string, which drops every user-anchored relationship rather than validating anything**
  (`memory_digest.py:780`); W7, W8 and W15 have no anchor check on any other claim.
* `MEMORY_OPT_OUT_SOURCES` (`user_prefs.py:39-48`) does not list `voice_regex`, `voice_turn_digest`,
  `voice` or `idle_consolidation`, so a memory-opted-out user is still mined on the voice lane and by idle
  consolidation (the owner's palace holds 8 + 4 + 27 such approved rows). Same deny-list class as the
  identity guard.
* `ingest` does not reject guest ids; the read side does (`memory_service.py:905` definition; checks at
  `:1277`, `:1306`, `:1326`). The palace holds **8 approved rows under `user_id=guest`** (voice teach / voice
  digest / voice regex): written, never readable.
* Three of the five accounts have no memory at all: the palace has rows for `jason`, `family-admin` (a
  legacy / startup-probe id, `main.py:808`, not an account), `guest` and one stray `u`. The other three
  accounts (`andrew`, `asya`, `teneeka`) have **no rows**, and Postgres shows only `jason` with chat turns in
  the last 60 days (226 user turns). The memory system has been exercised by one person.

## 2. Authority analysis

### 2.1 The rule being tested

> user-stated > user-confirmed > inferred. Inference may only **add** a reviewable candidate. It may not
> supersede, archive, merge into, or contradict a user-stated or user-confirmed row.

"User-stated" = the row's text is the user's own words: dictated, typed, or extracted deterministically
(regex) from their turn. "User-confirmed" = the user approved it (review UI, "yes" to an offer).
"Inferred" = a model produced the text, or no turn anchors it.

### 2.2 The matrix — can this writer override a user-stated row today?

"Check" means: does the code compare the *existing* row's provenance before it retires it? For every row
below the answer is **no** — `classify_against_existing` receives `(id, text)` pairs only
(`memory_quality.py:479`), and `review` / `supersede_by` never read `source`.

| Writer | Fact kinds | Overrides user-stated today? | Basis (file:line) | How established |
|---|---|---|---|---|
| W2 turn digest (UPDATE) | any attribute-shaped fact | **YES (model)** | `memory_digest.py:636-662` → `memory_service.py:1581` | reproduced S1 + S2 |
| W6 nightly contradiction pass | any | **YES (model) — the incident** | `memory_digest.py:794-826` | reproduced S1 (same call); incident record |
| W6 nightly reconcile UPDATE | any | **YES (model)** | `memory_digest.py:840-860` | S2 |
| W8 idle consolidation | any | **YES (model, and fed assistant turns)** | `memory_idle_consolidation.py:313,351` → `expert_dispatch.py:391-480` | code + S2 |
| W9 weekly merge | any with ≥ 0.85 overlap | **YES (confidence-ranked, authority-blind)** | `memory_digest.py:1154-1181` | code; S3 shows the rewrite machinery |
| W10 weekly contradiction judge | any | **YES (model; newer wins by timestamp)** | `memory_digest.py:1222-1232` | code |
| W12 nightly implicit-conflict pass (**live**) | home slot; any row with a cue word | **YES (model; no user words involved)** | `memory_supersede.py:308-359` | **reproduced S4** |
| W4 LLM person extractor | person facts, relationship text | **YES (model)** | `person_extractor.py:529-584`, `:404-466` | code (same `source` as W3) |
| W3 regex person extractor — graph edges | relationship type between two people | **YES (unverified speaker; no edge provenance)** | `person_extractor.py:792-955` | code; schema |
| W16 decay archive | any unrecalled row | **YES (not inference — time)** | `memory_service.py:1438-1500` | code; arithmetic in §1.2 |
| W11 write-time implicit supersede | topic-matched older rows | **YES (needs a user cue; the topic match is on the model's text)** | `memory_supersede.py:254-306` | code |
| W22 MCP `memory_review` | any | **YES (an agent acts as the user; the audit cannot tell)** | `mcp_server.py:3202` | code |
| W1 regex extractor | attribute-shaped | no — same authority (newer user words win); **wrong-row risk** at similarity ≥ 0.45 | `memory_extractor.py:931-949` | code |
| W20 voice teach | attribute-shaped | no — user-authored; same wrong-row risk | `expert_dispatch.py:638` | code |
| W14 deep sleep | pending → approved | **escalates inferred to approved on popularity** (does not override) | `memory_digest.py:1663` | code |
| W21 brain tool | any | adds; **can add a contradicting row that outranks the user's** (0.85 vs 0.72) and is exempt from guards | `intent_router.py:3653`, `memory_tombstones.py:40` | code |
| W13 REM | metadata | can **resurrect** a row superseded or forgotten mid-pass (stale upsert) | `memory_digest.py:1485-1575` | code |
| W24, W25, W26, W27 | user commands | no (user authority) | — | — |
| W5, W15, W18, W19, W23, W28-W30 | add-only or dark | no | — | — |

**Count.** Fourteen retire-or-replace paths exist: W1, W2, W3-edge, W4, W6a, W6b, W8, W9, W10, W11, W12,
W16, W20, W22. **None checks the existing row's provenance.** Eight can override a user-stated fact on the
strength of model-written or model-judged text (W2, W6a, W6b, W8, W9, W10, W12, W4); four are authority-blind by construction (W16, W3-edge,
W11, W22); two are user-authored (W1, W20). PR #1866 blocks exactly one attribute (the user's *name*) for
eleven named source strings (`identity_facts.AUTOMATIC_SOURCES`, on that branch).

### 2.3 Reproduced against the real code

Scratch scripts, the test suite's `_InMemoryCollection` (`tests/test_memory_opt_out_endpoints.py:98`), the
real `MemoryService`, `memory_quality` and `memory_supersede`; no live data touched.

* **S1 — provenance laundering.** A `voice_fact` row (a dictated appointment, session `sess-A`, with an
  excerpt) is edited by `review(edit, actor="digest", note="digest contradiction …")`. Result: old row
  `superseded`; new row `source=voice_fact`, `added_by=voice_fact`, `reviewed_by=digest`,
  `session_id=sess-A`, `user_turn_id` and `source_excerpt` carried, `status=approved`, `confidence=0.7`.
* **S2 — the reconciler decides UPDATE with no provenance input.**
  `classify_against_existing("User's dentist appointment is on Monday.", [(id, "… on Friday.")])` returns
  `("update", id)`. A model paraphrase and a dictated sentence are the same thing to it.
* **S3 — the weekly merge's no-op rewrite.** Two identical-text rows with different `entity_id`s;
  `review(edit, edits=first.text, actor="consolidation")` on the second returns the **same id**,
  `supersedes_id` equal to its own id, `status=approved`, `reviewed_by=consolidation`. That is the signature
  on 138 live rows (§4).
* **S4 — the live nightly conflict pass.** Three pairs, each returned by `conflict_pairs`: a newer
  `idle_consolidation` "User lives in <other city>" vs an older `voice_fact` "User lives in <city>" →
  `home`; a newer `digest` "User no longer runs half-marathons" vs an older `voice_fact` "training for a
  half-marathon" → `no longer`; a newer `turn_digest` "User **used to love** <city>" vs "User **lives in**
  <city>" → `used to` (a reminiscence retires a current fact).

### 2.4 Violations, listed

| V | Violation | Where |
|---|---|---|
| V1 | `review(edit)` carries the old row's `source`, `session_id`, `user_turn_id`, `source_excerpt` onto model-written text (provenance laundering; hides which writer created the live text) | `memory_service.py:1596-1599` |
| V2 | The shared reconciler's UPDATE has no provenance input and a 0.45 similarity bar | `memory_quality.py:384`, `:776-855` |
| V3 | Nightly contradiction judge replaces the older row with the newer one's text, whoever wrote either | `memory_digest.py:794-826` |
| V4 | The nightly transcript discards speaker, time and session; unverified panel speech enters as the bound user's turn | `memory_digest.py:969-1004`; `routers/voice_tts.py:3201-3208` |
| V5 | Idle consolidation feeds assistant turns to a "user turns only" extractor and archives on UPDATE; no anchor check | `memory_idle_consolidation.py:313`; `memory_digest.py:278` |
| V6 | Weekly merge / contradiction passes are confidence- or recency-ranked, authority-blind, and stamp `reviewed_by` on no-op rewrites | `memory_digest.py:1146-1238` |
| V7 | The live nightly implicit-conflict pass acts on stored LLM text with no user words; `used to` matches reminiscence | `memory_supersede.py:66-110`, `:308-359` |
| V8 | `brain_tool` (model text) is in the explicit-teach class: tombstone-exempt and identity-guard-exempt; #1866's guard is a deny-list of 11 strings | `memory_tombstones.py:40`; #1866 `identity_facts.AUTOMATIC_SOURCES` |
| V9 | The person extractors' regex and LLM halves share `source=conversation` / `voice` | `person_extractor.py:996`, `person_extractor_llm.py:185` |
| V10 | `people` and `person_relationships` carry no provenance; an edge written from an unverified fragment can close a user-confirmed edge | schema; `person_extractor.py:792` |
| V11 | Deep sleep promotes inferred pending rows to approved on access popularity | `memory_digest.py:1663` |
| V12 | Decay archive ignores authority: a user-stated, never-recalled row is silently archived at about a year | `memory_service.py:1438-1500` |
| V13 | MCP edit / forget run as the user id; an agent edit is indistinguishable from the owner's | `mcp_server.py:3202`, `:3230` |
| V14 | REM / deep-sleep / synthesis raw upserts write stale metadata outside the per-user lock | `memory_digest.py:1569-1573`, `:1668-1673`, `:1799` |
| V15 | Ranking uses the writer-reported `confidence` and access popularity, not authority: a 0.85 model row outranks a 0.72 regex row of the same fact, and each recall bumps `access_count`, so wrong-but-retrieved rows get hotter | `memory_service.py:2267-2268` |
| V16 | The opt-out wall omits `voice_regex`, `voice_turn_digest`, `voice`, `idle_consolidation` | `user_prefs.py:39-48` |

## 3. Read-path risks

**3.1 Recall reach (HNSW tombstones, #1827).** chroma 1.x never compacts a persistent HNSW index; every
deleted or churned row stays as a tombstone, so an owner-filtered query can come back full of the wrong
rows. Measured 2026-10-04: 1,591 elements for 258 live rows, an owner-filtered sentence query returned 0
(`memory_index_health.py:1-12`). Compaction is built (`memory_service.py:474-612`) and **live**
(`ZOE_MEMORY_INDEX_COMPACT=1`); the drawers index directory was rebuilt 2026-10-05 05:03 (mtime), so reach
is healthy *today*. Residual risks: the ratio is only advised at 3× (`memory_index_health.py`), a rebuild is
operator-gated, and `ZOE_MEMORY_LINT_IN_DREAMING` is not on, so nothing nightly *proves* recall of the
rows that matter (the self-recall probe samples one row, `memory_recall_probe.py:1-30`).

**3.2 Filtered vs unfiltered search (#1813 / #1815).** `_semantic_search` now queries **unfiltered first**
with `n_results = max(limit·20, 200)` and applies visibility / status / expiry in Python, using the
owner-filtered query only as a supplement when fewer than `limit` rows survive (`memory_service.py:2184-2215`).
Sound at 268 rows. It scales as a stopgap, not a design: at N ≫ 200 rows the unfiltered top-200 is
dominated by whoever has the most rows, the supplement is the tombstone-prone query, and the code
documents that `col.count()` wedged the service on 2026-10-04 so size cannot be consulted. Test coverage for
"owner's row is returned when other users have 10× the rows" is a pack item (§6, F13).

**3.3 Session continuity (#1866).** The estate ask-box posted with no `session_id` and the route minted a
fresh `web_<8hex>` each time, so every message lost context (the "visitor" / "found nothing" symptoms).
`ZOE_STICKY_SESSION` (default on, 20 min) fixes the web route only; Telegram, voice (`voice-panel-*`) and
LiveKit keep their own ids. Memory impact: the per-turn extractors' anaphora anchor
(`memory_extractor.recall_prev_user_turn`, an in-process LRU, `:195-207`) falls back to `chat_messages` by
session id (`:822-829`); a fresh session per message meant pronoun and correction anchoring never fired on
web. #1866 is the right fix and is open.

**3.4 Cross-session recall — `ZOE_SEAM_RECALL_INJECT` is already ON.** The memory notes call it an
operator flip; `services/zoe-data/.env` has `ZOE_SEAM_RECALL_INJECT=true` (alongside
`ZOE_RECALL_PRESENT_STATE_SHAPES`, `ZOE_SEAM_OFFER_INJECT`, `ZOE_USER_MODEL_BLOCK`, `ZOE_RECALL_EVIDENCE`,
`ZOE_OWN_FACT_PRECEDENCE`, `ZOE_VERIFY_ON_CHALLENGE`). What it does: on recall-shaped turns
(personal "what's my…", present-state, event-time, evidence, event-shaped) `zoe_flue_client` injects the
`/api/memories/for-prompt` packet (≤ 12 bullets / 1,600 chars) into the outbound message after the
` zoe-uid:` line, logging `SEAM_RECALL` (`zoe_flue_client.py:551-570`). It is a floor under the brain's own
`recall_memory` tool (~97% invocation). Fidelity consequence: **whatever the store holds is now injected
as fact**; there is no provenance marker in the bullets, so the brain cannot say "you told me" vs "I
inferred". `recall_evidence.py:50-54` partially covers this for *edited* rows (`effective_writer` uses
`reviewed_by` when `supersedes_id` is set, so an edited row is not quoted as the user's words) — a read-time
patch over the write-time laundering (V1).

**3.5 Isolation / synthetic users / test pollution.** Read side: guests get nothing
(`is_guest_memory_user`, `memory_service.py:905`); visibility is user-or-`family` (`:890`). Harness
isolation: conftest pins the palace off the live one (`tests/test_live_store_isolation.py`), the bar /
day-sim use `demo_<tag>_<hex>` ids with asserted teardown (`scripts/perf/samantha_bar.py:869-875`), the
nightly passes drop synthetic ids (`user_filters.drop_synthetic_users`). **Residual pollution is
measurable (§4):** 76 distinct user ids in the audit, 60 synthetic-looking; 114 ingests under the owner's id
since 2026-09-20 (79 of them on 09-27), of which 93 are no longer in the palace — 38 carried session ids
`sess-hermes` / `sess-openclaw` (external agent harnesses, string absent from this repo) and 55 carried none; since
2026-08-15, 195 audit-recorded ingests under the owner's id are no longer in the palace, **107 of them
literally equal strings in the repo's tests/scripts**; the other 88 are indistinguishable from legitimate
deletes because hard deletes leave no trace (§3.7). A legacy id (`family-admin`) holds 155 rows that are 6
distinct texts repeated up to 47×.

**3.6 Temporal validity.** A row carries `added_at` / `added_ts` (capture time) and, **only when
`ZOE_MEMORY_IMPLICIT_SUPERSEDE` is on**, `valid_from` (= capture time, not event time) and, once
superseded, `invalid_at` (`memory_service.py:1882-1885`, `:1667`). The flag is on live; 12 of 82 approved
owner rows have `valid_from` (those written since it was enabled), 3 have `invalid_at`. Reads hide
`superseded`/`archived`/`rejected`/`pending`/`disputed` outright (`_BLOCKED_READ_STATUSES`,
`memory_service.py:829`), so there is **no "as of" query**: "where did I live last year?" cannot be
answered from a superseded row, and the brain is never told a fact *changed*. "I used to…" is handled by
the cue table (`memory_supersede.py:66-110`): it stores a `state_change` tombstone and retires same-topic
rows — right for "I used to live in <city>", wrong for "I used to love <city>" (S4). Events expire through
`expires_at` (none set on any live row). Edge validity is better: `person_relationships` has
`valid_from` / `valid_to` / `superseded_by` and `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1` (3 current edges,
0 superseded for the owner).

**3.7 Forgetting on request.**

| Surface | "forget X" does | Gap |
|---|---|---|
| Palace rows whose text contains X as a whole word | `review(archive)` (soft; text and embedding stay in Chroma and in the audit's `before`/`after` JSON) | paraphrases without the name survive; archived is not deleted |
| Late in-flight writers | 300 s in-process tombstone (`memory_tombstones.py:33`), dropped at `ingest` for non-explicit sources | **dies at 5 min and on restart**; `brain_tool` is exempt |
| The next nightly digest / idle consolidation / open-loops pass | **nothing** — they re-read `chat_messages`, which still hold the forgotten turns, and the tombstone has expired | **resurrection**; the 03:00 digest reads the previous 24 h+ (`ZOE_MEMORY_DIGEST_LOOKBACK_HOURS`) |
| User portrait, user-model card, open loops, proactive candidates | untouched | derived text persists until regenerated (portrait: weekly, Sunday) |
| `people` row, `person_relationships`, `person_activities` | untouched (only a pending *offer* is withdrawn, `intent_router.py:3620`) | the person survives "forget everything about X" |
| Hard deletion | `delete_user` (admin / forget-synthetic) deletes rows **and** the user's audit rows (`memory_service.py:1365-1385`); `_delete_ids` (`:2316`) writes **no audit** | no ledger of what was removed, by whom, when; cannot distinguish a deliberate delete from loss |

The one structural guarantee that does hold: opt-out is enforced at `ingest` for listed sources (with the
V16 gaps) and "forget synthetic" is guarded by shape + registered-account checks.

## 4. Live palace health (read-only snapshot, 2026-10-05; shapes and counts only)

Method: one pass over `chroma.sqlite3` (`mode=ro`) reading every `embedding_metadata` key for the
`mempalace_drawers` collection (268 rows) and the `mempalace_audit` collection (19,454 rows). The identity
audit has already repaired the polluted name row, so the incident's *signature* is reported as it stands.

**Population.** `jason` 103 rows; `family-admin` 155 (legacy / probe id, not an account); `guest` 8;
`u` 2. Audit: 11,988 ingest / 6,605 edit / 861 archive, 2026-07-04 → 2026-10-05.

**Owner (`jason`), all 103 rows.**

| Cut | Counts |
|---|---|
| status | approved 82 · superseded 17 · archived 4 |
| `source` (all statuses) | idle_consolidation 30 · conversation 32 · digest 10 · voice 9 · voice_fact 5 · chat_regex 5 · voice_turn_digest 4 · person_created 3 · turn_digest 3 · journal_created 1 · brain_tool 1 |
| `added_by` ≠ `source` | 0 (the field is a pure copy: `added_by` adds no information) |
| `reviewed_by` | none 84 · operator-cleanup 7 · consolidation 7 · voice 1 · digest 1 · turn_digest 1 · idle_consolidation 1 · identity_audit 1 |
| `memory_type` | fact 40 · person 44 · relationship 5 · emotional_moment 8 · event 2 · health 2 · profile 1 · journal 1 |

**Owner, 82 approved rows, by who authored the text.**

| Authority class (my mapping of `source`) | Rows | Share |
|---|---|---|
| User's own words, deterministic (`chat_regex` 2, `voice_fact` 2) | 4 | 5% |
| UI-authored by the user (`person_created` 2, `journal_created` 1) | 3 | 4% |
| Model-written text in the explicit class (`brain_tool`) | 1 | 1% |
| Person extractors, regex **or** LLM, indistinguishable (`conversation` 23, `voice` 8) | 31 | 38% |
| LLM-only (`idle_consolidation` 27, `digest` 10, `voice_turn_digest` 4, `turn_digest` 2) | 43 | 52% |

Other cuts on the 82: `source_excerpt` 3 · `session_id` 68 · `user_turn_id` 38 · `valid_from` 12 · entity-linked 34
(6 `person_pending`) · `expires_at` 0 · `access_count` > 0 for 81 (so the hotness term is nearly flat and
carries no information) · confidence bands 0.7 → 32, 0.8 → 42, 0.9 → 8 · age: median 74 d, max 91 d, 60 older
than 30 d.

**The incident's signature.**

* Approved rows with `source=chat_regex` and `reviewed_by=digest`: **0**. The one `reviewed_by=digest` row in
  the palace (`source=chat_regex`) is `superseded`: that is the polluted row, retired by the operator
  audit. Its successor is `source=chat_regex`, `reviewed_by=identity_audit`, `approved` — a row written by
  an operator tool that **reads as a chat-regex write** (V1, in the live data).
* Audit-wide, edits of a row originally written by an explicit-teach source (`voice_fact`, `brain_tool`,
  `review_ui`, `proposal`) by an automatic actor: **1** (weekly consolidation over a `voice_fact` row, 2026).
  Rows with a user-ish `source` but an automatic `reviewed_by` today: 1 (`voice` / `consolidation`).
* Superseded owner rows by the **successor's** `reviewed_by`: consolidation 6 · operator-cleanup 4 ·
  digest 1 · turn_digest 1 · idle_consolidation 1 · voice 1 · identity_audit 1 · successor missing 2.
  So **3 were superseded by an LLM actor's `review(edit)`** (digest, turn_digest, idle_consolidation) and
  6 by the weekly pass (lexical merge or LLM judge; the row cannot say which). Only 3 of 17 carry `invalid_at`.

**"User's <attribute> is…" assertions among the 82 approved.** 6 rows: `idle_consolidation` 4 · `chat_regex` 1 ·
`turn_digest` 1. Name assertions: **2** (`idle_consolidation` 1, `chat_regex` 1), same value. One attribute has
more than one approved row; **0 attributes have approved rows with differing values** (no live
contradiction today). 12 approved rows share 6 identical texts; 0 near-duplicate pairs (containment ≥ 0.85,
different text). Post-#1866 the `idle_consolidation` name row could not be written again, but it is stored.

**The legacy id.** `family-admin`: 144 approved + 11 pending; **138 rows carry `supersedes_id` equal to
their own id and `reviewed_by=consolidation`** (S3); 6 distinct texts, up to 47 copies each; `source` is
`note_created` / `journal_created` / `person_created` 47 each (fixture shape). Weekly consolidation "edits":
1,079 / 1,398 / 1,490 / 1,661 per week on 07-18..08-08, then 19-143 per week. Real supersessions across
the whole audit: **20**.

**Guest and strays.** 8 approved `guest` rows (voice_fact 2, voice_turn_digest 3, voice_regex 3) — written,
never read. 2 approved `u` rows (`turn_digest`, test shape).

**The ledger gap.** Of 114 audit-recorded ingests under the owner's id since 2026-09-20, 93 are no longer in
the palace with no archive/edit audit; since 2026-08-15, 195 (107 literal test strings, §3.5). Nothing records
who removed them.

**Pending queue.** 11 pending rows exist (all `family-admin`, `nightly_session_scrub`); for the owner the review
queue is empty — the pipeline writes `approved` directly, so "reviewable candidate" is not a concept the
store currently exercises for the owner.

**People graph (Postgres).** Owner: 22 `people` rows (17 live: 6 `personal`, 11 `suggested`; 5 deleted),
3 `person_relationships` edges, all current. No provenance column on either table; `person_activities` has
`source` (voice 2, conversation 1).

## 5. The field

Researched 2026-10-05 by a web-research sub-agent; I did **not** re-fetch every URL. Every vendor number is
self-reported and sensitive to judge, prompt and category choice. **[secondary]** = from press, a blog or a
search summary rather than the primary page; **[unverified]** = could not be confirmed. OpenAI's own pages
(openai.com, help.openai.com) returned HTTP 403, so everything about ChatGPT is [secondary].

### 5.1 What each system guarantees

| System | Provenance | Temporal validity | Contradiction: who wins | User authority / edit | Forgetting | Evaluation |
|---|---|---|---|---|---|---|
| **mem0** | Platform history endpoint returns `old_memory`, `new_memory`, event (ADD/UPDATE/DELETE), timestamps, user/agent/app ids and the triggering input; **no user-said vs model-inferred field** — [history API](https://docs.mem0.ai/api-reference/memory/history-memory) | A settable timestamp and expiry that "stops surfacing after a known date without deletion" — [update docs](https://docs.mem0.ai/core-concepts/memory-operations/update), [llms.txt](https://docs.mem0.ai/llms.txt); no bi-temporal edges in the OSS flat store [unverified beyond docs read] | Paper: an LLM picks ADD / UPDATE / DELETE / NOOP per fact — [arXiv 2504.19413](https://arxiv.org/html/2504.19413). **Current v3 (blog dated 2026-04-16, updated 2026-09-28) is ADD-only: "the new fact is stored alongside the old one" and retrieval ranking resolves the conflict** — [migration doc](https://docs.mem0.ai/migration/oss-v2-to-v3), [blog](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm). Mem0g marks conflicting graph relationships invalid rather than deleting them | Update overwrites; "immutable" memories must be deleted and re-added | Delete exists; whether it cascades to graph links is undocumented [unverified] | LoCoMo J: full-context 72.9 · Mem0g 68.4 · Mem0 66.9 · Zep 66.0 · LangMem 58.1 · OpenAI 52.9 (paper, adversarial category excluded). Self-reported v3: LoCoMo 92.5, LongMemEval 94.4, BEAM-1M 64.1 ("contradiction_resolution" sub-score 35.7 — the lowest in the set) — blog |
| **Letta / MemGPT** | None documented; recall memory keeps the raw message history — [guides](https://docs.letta.com/guides/agents/memory-blocks) | None documented | The agent's own judgement (`memory_replace`, `memory_rethink`); "last write wins" on a block [secondary on tool names] | Blocks can be `read_only` (the agent cannot edit, developers can) and shared; archival is "agent-immutable" (agents cannot easily modify or delete it) — [archival docs](https://docs.letta.com/guides/agents/archival-memory) | Developer-side SDK deletes | LoCoMo **74.0%** with plain files + search tools on GPT-4o-mini, Letta's own figure; disputes Mem0's 68.5% MemGPT baseline — [blog](https://www.letta.com/blog/benchmarking-ai-agent-memory/). Sleep-time agents (a second agent rewrites memory blocks between turns) are evaluated on maths, not memory fidelity — [blog](https://www.letta.com/blog/sleep-time-compute/) |
| **Zep / Graphiti** | **Strongest**: every entity and edge traces to the raw *episodes* that produced it — [README](https://github.com/getzep/graphiti) | **Bi-temporal**: event timeline (`t_valid`, `t_invalid`; code names `valid_at`, `invalid_at`) and transaction timeline (`created_at`, `expired_at`); old facts are "invalidated — not deleted" — [arXiv 2501.13956](https://arxiv.org/html/2501.13956) | An LLM compares new edges with related existing ones; on temporal overlap the old edge's end is set to the new fact's start; "consistently prioritizes new information" — **newest wins, no user-vs-inferred rule**. Open bug: invalidation candidate search scans the whole graph, so merely mentioning an entity can retire an unrelated true fact (one reporter: 41% of facts invalidated, 3 of 4 sampled wrong) — [issue #1728](https://github.com/getzep/graphiti/issues/1728), one reporter's audit | Invalidate rather than delete | `remove_episode` exists; cascade undocumented [unverified]; "forget" usually means invalidated-but-stored — [issue #864](https://github.com/getzep/graphiti/issues/864) | LongMemEval (own paper): gpt-4o 60.2 → 71.2% (+18.5%), gpt-4o-mini 55.4 → 63.8%; but `single-session-assistant` fell 17.7%. DMR 94.8 vs MemGPT 93.4 vs full-context 94.4 (Zep itself calls DMR too easy). LoCoMo dispute: Zep says 75.14 ±0.17 vs Mem0g 68.44; Mem0 says 58.44 ±0.20 once the adversarial category is handled as theirs — [Zep blog](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/), [zep-papers #5](https://github.com/getzep/zep-papers/issues/5). **Do not cite any LoCoMo figure as settled** |
| **ChatGPT** [secondary] | "Memory sources" lets the user see what personalisation used; depth unverified — [TechTimes 2026-06-05](https://www.techtimes.com/articles/317840/20260605/chatgpt-memory-dreaming-update-openai-rewrites-personalization-engine-limits-audit-trail.htm) | "Dreaming" (2026-06-04) rewrites memory in the background ("going to Singapore in July" → "went to Singapore in July 2026") — same source | Automatic and undocumented; no published rule that a user statement beats an inference | Saved memories viewable / editable / deletable; Temporary Chat reads and writes nothing; a legacy mode can be restored | **Deleting a chat does not delete memories derived from it**; deleted logs may be kept ≤ 30 days — [FAQ summary](https://help.openai.com/en/articles/8590148-memory-faq) | OpenAI-internal, unaudited: factual recall 41.5% (2024) → 67.9% (2025) → 82.8% (2026); accuracy over time 52.2 → 75.1% — same TechTimes piece. Failure reports: silent non-save when full, entries bleeding across chats ([OpenAI forum](https://community.openai.com/t/memory-context-corruption-contradiction-in-chatgpt-gpt-4-5/1144222)); a 2026-06 test reportedly found outdated personal details treated as reliable ([TechBuzz](https://www.techbuzz.ai/articles/chatgpt-s-memory-feature-silently-poisons-answers-with-bad-data), original unseen); memory poisoning by prompt injection persisted across chats (May 2024) — [Rehberger](https://embracethered.com/blog/posts/2024/chatgpt-hacking-memories/) |
| **Anthropic** | Memory tool: none — a client-side `/memories` file CRUD (view, create, str_replace, insert, delete, rename) the model drives; provenance, validity and contradiction are your handler's job — [docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool). Claude.ai: per-topic memory, no provenance documented | None | The model's choice | Claude.ai: view / edit / delete a topic in Settings or by asking; Incognito and memory-off chats excluded; per-project memory spaces — [help](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context) | Real deletion on the tool (you own the storage); in Claude.ai **deleting a conversation does not remove memory entries made from it** | No published memory-fidelity numbers found [unverified] |
| **Nomi** | None documented | None documented | "One does not supersede the other": the user's Shared Notes and the Nomi's Identity Core are "two perspectives" — [Nomi](https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/) | Mind Map entries are editable and can be **locked against automatic change**; the underlying memories "remain invisible to humans and cannot be edited or deleted" — [Nomipedia](https://wiki.nomi.ai/Are_Mind_Map_Entries_Memories%3F) | Weakest: a wrong underlying memory can only be out-voted by a note | None published |

### 5.2 Benchmarks, and what they do and do not test

* **LoCoMo** ([arXiv 2402.17753](https://arxiv.org/abs/2402.17753)): up to 35 sessions, ~300 turns / 9K tokens per
  conversation; QA categories single-hop, multi-hop, temporal, open-domain, adversarial (names as used by the
  Mem0 / Zep papers). An independent audit found 99 of 1,540 questions (6.4%) with wrong golden answers —
  a ceiling of **93.57%** — and a gpt-4o-mini judge that accepted 62.81% of deliberately wrong but topical
  answers ([github.com/dial481/locomo-audit](https://github.com/dial481/locomo-audit)). My inference, not the
  audit's: a self-reported 92.5 sits suspiciously close to that ceiling.
* **LongMemEval** ([arXiv 2410.10813](https://arxiv.org/abs/2410.10813)): 500 questions in chat histories;
  five abilities — information extraction, multi-session reasoning, **temporal reasoning, knowledge
  updates, abstention**; `_S` ≈ 115k tokens (~40 sessions), `_M` ≈ 500 sessions; GPT-4o judge
  ([repo](https://github.com/xiaowu0162/LongMemEval)). Commercial assistants lost ~30% accuracy over sustained
  interactions. This is the benchmark that tests the three things Zoe's incident is about (an update, a
  contradiction, a refusal to invent).
* **Neither benchmark has an authority axis** ("a model inference must not overwrite what the user said"),
  and neither injects an unverified-speaker fragment. The published scores therefore cannot certify the
  property the owner asked for. They are useful as category templates (§6 P1.4) and as an offline
  retrieval-only hit@k check on lab hardware; running the full `_S` set through a 4B brain with an 8k slot is not
  meaningful and is not proposed.

### 5.3 Attribution failures are a named class in 2026 research (sub-agent-sourced, not re-fetched)

* *MemIR* ([arXiv 2605.25869](https://arxiv.org/abs/2605.25869)) names **"provenance-role collapse"** — memory
  stored as unstructured text loses who supplied each claim — and proposes typed memory separating raw
  evidence, retrieval cues and truth-bearing claims.
* *Memory Provenance Laundering* ([arXiv 2607.29167](https://arxiv.org/abs/2607.29167)): LLM consolidation can
  rewrite an external observation as apparent user history; a gate matching action risk to memory
  authority blocks the evaluated attacks. **V1 + S1 are this, in our own code, without an attacker.**
* *MemGhost* ([The Hacker News](https://thehackernews.com/2026/07/new-memghost-attack-plants-persistent.html)):
  one email makes an agent write a false persistent memory (87.5% reported on one stack; the vendor disputes
  the methodology but says it is weighing "provenance, audit logs, and confirmation prompts"). *MemTrace*
  ([arXiv 2605.28732](https://arxiv.org/abs/2605.28732)) is a framework for locating where memory systems fail.
* mem0 issue [#4573](https://github.com/mem0ai/mem0/issues/4573) (one reporter's audit): 97.8% junk in 10,134
  entries, a hallucinated preference amplified into 808 duplicates because **recalled memories were
  re-extracted as new input**. Zoe's guard against the same loop is the extractor purity contract
  (`memory_extractor.py:678`) plus `recall_evidence`; idle consolidation (V5) is the one path that feeds
  assistant text back in.

### 5.4 Map to Zoe: have / partial / missing, and the piece worth borrowing

| Capability | Zoe today | Verdict | Piece to borrow (the idea, not the framework) |
|---|---|---|---|
| Provenance | `source`, `session_id`, `user_turn_id`, `source_excerpt` (3/82 rows), audit before/after; **laundered on edit; no authority class; none on people/edges** | **partial** | Graphiti: every derived fact points at the raw episode (`chat_messages` turn ids) it came from; MemIR: a typed *role* on every claim |
| Temporal validity | `valid_from`/`invalid_at` flag-live on rows; edges have `valid_from`/`valid_to`; `expires_at` unused | **partial** | Graphiti: two timelines (when it was true / when we learned it) and **invalidate, never delete**; add an `as_of` read |
| Contradiction handling | similarity reconciler + two LLM judges + a deterministic cue pass; newest/model wins | **partial, unsafe** | Letta `read_only` blocks + Nomi's lock: a user-stated row is **locked** against automatic change; mem0 v3 honesty: keep both, rank, and ask |
| User authority / edit | review UI, MCP, spoken forget, correction tier (dark) | **have** (+ gap) | Nomi's lock flag on a user-corrected entry; Letta's `read_only` |
| Forgetting | soft archive + 5-min tombstone; no cascade; no ledger | **partial** | Anthropic's real deletion; and the **negative lesson** from ChatGPT and Claude.ai: chat deletion does *not* delete derived memory — Zoe must cascade |
| Evaluation | Samantha bar S1-S22, day-sim, recall probe, index health; no longitudinal or authority axis | **partial** | LongMemEval's categories (knowledge-update, temporal, abstention, multi-session) as *scenario shapes*; an authority axis nobody publishes |
| Poisoning / attribution | extractor purity, anchor validators, tombstones, opt-out; **no speaker verification on rows** | **partial** | Provenance-laundering gate: *action risk ≤ memory authority* |

## 6. Ranked plan to "flawless"

Principles. (1) The rule is enforced **where the write happens** (`MemoryService`), so a new writer, a new
source string, or a future model cannot route around it. (2) **Allow-list, fail-closed**: an unknown
`source` is the lowest class. (3) Everything that changes live behaviour ships **shadow first**
(`would-block` counted and logged, nothing changed), then enforce. (4) Every item below names a test **and a
negative control** (the test must go red when the fix is removed — the standing rule from
`feedback_verify_your_instruments`). (5) Voice-path files (`routers/voice_tts.py`, `zoe_flue_client.py`) are
on the replay-gate list and merge serially; the bar runs only outside the 01:45-03:15 window.

### 6.0 The provenance classes (the vocabulary P1.1 enforces)

| Rank | Class | Which writers (today's `source` / actor) | Needs |
|---|---|---|---|
| 5 | `operator` | `operator-cleanup`, `identity_audit`, admin tools | named actor; every use audited |
| 4 | `user_confirmed` | review-UI approve/edit, "yes" to an offer, an answer to a clarifying question | actor is the account |
| 3 | `user_stated` | `voice_fact`, `review_ui`, `proposal`, `person_created/updated`, `note_*`, `journal_*`, `skybridge_action`, `conversation_correction`, and the deterministic extractors `chat_regex` / `voice_regex` **when the speaker is authenticated** (typed session, Telegram, or a speaker-ID score ≥ threshold) | verbatim span in the user's turn |
| 2 | `user_unverified` | the same voice writers when the turn was attributed only by panel binding | — never supersedes; self-assertions are `pending` |
| 1 | `model_from_turn` | `turn_digest`, `voice_turn_digest`, the LLM person extractor, `brain_tool`, `mcp`, `zoe_agent` | a `quote` that is a substring of one user turn and entails the fact; with an authenticated speaker this is promoted to `user_stated` (the user said it) |
| 0 | `model_from_transcript` | `digest`, `idle_consolidation`, `synthesis`, emotional pass, `music_digest`, anything unknown | none — **add-only** |

Rule: a write may supersede / archive / merge into a target only if `rank(writer) ≥ rank(target)`;
otherwise it is **demoted to a candidate** (`status=disputed`, `contradicts_id=<target>`; `disputed` is
already a blocked-read status, `memory_service.py:829`) and the contradiction is surfaced as a question
(§6 P1.1c). Recency breaks ties only inside a rank. `confidence` stops being a ranking input across ranks.

### P1 — before any other memory work

**P1.1 One authority choke point, with provenance classes.**

* **Files.** New `services/zoe-data/memory_authority.py` (class table above, `check(writer, target) →
  ALLOW | DEMOTE | DENY`, pure, no I/O); `memory_service.py` — `ingest` stamps `authority`,
  `asserted_by` (`user|assistant|third_party|system`), `speaker` (`authenticated|score:<x>|panel_bound`);
  `review(edit/archive)`, `supersede_by`, `sweep_soft_archive`, `archive_by_entity` and the `relink` call
  `check()` and obey it. Stop the carry-forward at `memory_service.py:1596-1599`: the edited row gets the
  **editor's** `source`, and `origin_id` / `origin_source` keep lineage. Move #1866's identity guard
  inside the same function (the account-name fact is `user_confirmed` by construction), replacing its
  deny-list of 11 strings. Add `authority` to the per-writer call sites only where they know better
  (e.g. `person_extractor` passes `regex|llm` so V9 disappears). Backfill: `scripts/maintenance/
  memory_authority_backfill.py --dry-run` maps existing `source` → class (the 82 owner rows: 4 / 3 / 1 / 31 /
  43), no text changes.
* **c. Dispute → question.** A demoted write stores a `disputed` candidate; the proactive selector
  (`proactive/selector.py`, already ranks open loops) gets a `Question` candidate "Earlier you told me X;
  I heard Y — which is right?". The answer is a `user_confirmed` write that supersedes or rejects. This is
  the W5 pattern (`pending_suggestions.py`) applied to memory.
* **Flag.** `ZOE_MEMORY_AUTHORITY=off|shadow|enforce` (default `shadow` on merge; flip to `enforce` after
  a clean week). Shadow logs `AUTHORITY_WOULD_BLOCK writer=… target=<class> kind=<attr>` and increments
  `memory_authority_block_total{writer,class}`. The class stamp itself is a pure addition.
* **Tests.** `tests/test_memory_authority.py` (`ci_safe`, real `MemoryService` over the in-memory
  collection used by `test_memory_opt_out_endpoints.py`): parametrised **writer × target class** across all
  14 retire-capable paths in §2.2 — the 8 model paths must demote, the 2 user paths must succeed, a
  `user_confirmed` target survives every automatic writer, an unknown source string is rank 0, an edit's
  `source` is the editor's. **Negative control:** `ZOE_MEMORY_AUTHORITY=off` makes the same test fail with the S1
  signature (old row superseded, new row `source=voice_fact`). Also replay S1-S4 as regression tests.
* **Expected effect.** The §2.2 matrix goes from 12 "YES" rows to 0; V1/V2/V3/V6/V8/V9/V11/V13 closed by one
  change. **Measured by** the shadow counter (expect > 0 on day one — the nightly passes will show what
  they would have done), the fidelity report (§P2.3) and the pack (P1.4).
* **Risk.** Over-blocking a legitimate correction: the user's own turn arrives as `user_stated`/`model_from_turn`
  with an authenticated speaker, so "I've moved to <city>" still supersedes — that path must be in the pack.

**P1.2 The digest anchored to the user's own words — for every fact.**

* **Files.** `memory_digest.py`: `_load_todays_messages` (`:969`) returns turns with `turn_id`, `created_at`,
  `speaker` (see P1.3); `_EXTRACTION_PROMPT` (`:278`) returns `{"fact","quote"}`; a new
  `memory_quality.fact_unanchored(fact, quote, turns)` generalises `user_relationship_claim_unsupported(fact,"")`
  (`memory_digest.py:780`, which today *drops every relationship* rather than checking one): the quote must be
  a normalised substring of one user turn, share ≥ k content tokens with the fact, and self-attributes
  (name, age, birthday, home, job, spouse) additionally need a first-person marker in the quote
  ("my", "I'm", "I live", "call me"). Facts that fail are dropped and counted. The stored row carries
  `source_excerpt=quote`, `user_turn_id`, `speaker`. The contradiction pass (`:794-826`) stops editing: a
  contradiction with a rank-≥-writer row is a `disputed` candidate. `memory_idle_consolidation.py:313`
  sends **user turns only** (the prompt already claims it) and runs the same anchor. Weekly merge
  (`:1146`) becomes archive-the-later-duplicate on *identical normalised text* — no `review(edit)`.
* **Flag.** Pure fix for the transcript (assistant turns out) and the merge; the anchor ships under
  `ZOE_DIGEST_ANCHOR=shadow|enforce` (same staging as P1.1).
* **Tests.** `tests/test_memory_digest_anchor.py`: the incident, generalised — a day transcript with an
  unverified fragment naming a third person plus one genuine statement, for **six attributes** (name, age,
  home, job, spouse, birthday): no approved row from the fragment, no change to the existing row. **Negative
  control:** disable the anchor → the nightly pass reproduces the incident (S1) for each attribute. Plus:
  assistant-turn text never reaches the extractor; the weekly merge performs 0 edits where text is identical.
* **Expected effect.** Closes V3/V4/V5/V6; removes the 6,585-row audit churn; 52% of owner rows (LLM-only)
  gain a quote and a turn id. **Measured by** `source_excerpt` coverage on approved rows (3/82 → ≥ 95%
  for rows written after the fix) and the pack.

**P1.3 STT fragments naming third persons never become user facts.**

* **Files.** `routers/voice_tts.py` (voice-path; replay-gated): record the speaker verdict on the
  persisted turn (`speaker_verified`, `speaker_score`, `speaker_source ∈ {claim_accepted, panel_bound,
  typed_session}`) into `chat_messages.metadata` and pass it through `_run_voice_memory_passes`
  (`:2940`) to `extract_and_ingest(metadata=…)` and `run_turn_digest`. `memory_extractor.py`: first-person
  self templates (`my name is`, `i live in`, `i work`, `i'm N years old`, `my <relative> is`, birthday —
  `_TEMPLATE_PATTERNS` `:62-110`) from `panel_bound`/unverified speech store `pending`, not `approved`;
  a name introduced in a fragment without a first-person anchor can only become a `person_pending` /
  contact **offer** (W5), never a `User's …` row. `memory_digest.py`: the nightly transcript includes
  unverified turns only for non-self facts. `person_extractor._write_relationship` (`:792`) gets the same
  gate and an `asserted_by`/`source` column on `person_relationships` (migration).
* **Flag.** `ZOE_MEMORY_SPEAKER_GATE=shadow|enforce`; the column and the verdict recording are additive.
* **Tests.** `tests/test_memory_speaker_gate.py` + a bar scenario (below, F8). **Negative control:**
  flag off → an unverified "my name is <other>" turn stores an approved `User's name is …` row (today's
  behaviour).
* **Expected effect.** Closes V4/V10 at the source; measured by the count of approved self-facts
  with `speaker=panel_bound` (target 0) and `fidelity.unverified_self_fact_total`.

**P1.4 The memory-fidelity regression pack.** Static half in `services/zoe-data/tests/` (joins CI by the
co-located `ci_safe` marker, per the marker-based CI rule), live half as new bar scenarios in
`scripts/perf/samantha_bar.py` (S23+, same demo-users-only guardrails, `backdate` already supported at
`:1477`) and a multi-day arc in `scripts/perf/samantha_day_sim.py`. Each scenario has a deterministic
scorer where possible and a negative control that must turn it red.

| ID | Scenario | Static (ci_safe) | Live (bar / day-sim) | Negative control |
|---|---|---|---|---|
| F1 | **Store → recall after N days** (1 / 7 / 30 / 90 / 400): a dictated fact is recalled verbatim; decay and compaction never lose it | fake clock through `sweep_soft_archive` and a compaction rebuild | S8 variant with `--backdate` | lower the decay floor → the 400-day row is archived (V12) |
| F2 | **Correction** wins, history kept ("actually it's Tuesday") | supersede + `as_of` read returns the old value | S2 (exists) + history question | drop `invalid_at` → `as_of` red |
| F3 | **Negation** ("I don't live in X any more", "my sister isn't called Y") never stores the positive | extractor + digest on negated shapes | S3-style decline | remove the cue → positive row appears |
| F4 | **"I used to…"**: "I used to live in A" retires "lives in A"; "I used to love A" does not (S4) | cue table + `conflict_pairs` | day-sim arc | revert the cue fix → reminiscence retires a current fact |
| F5 | **Isolation**: B never recalls A; guest nothing; owner rows survive 10× rows from others | search with 3 users, 10× volume, post-compaction | S6 (exists) | remove the owner `where` → bleed |
| F6 | **No hallucinated facts**: every approved row has an anchor in a user turn; asking about something never said is declined | pack-level invariant over a generated palace | S3 + a nightly audit query | insert an unanchored LLM row → invariant red |
| F7 | **Identity**: name / home from the account, a conflicting row cannot change the answer | by reference to #1866's tests | S-id | (#1866's own) |
| F8 | **Third-party STT fragment** (the incident), six attributes | `test_memory_digest_anchor.py` | S23: scripted panel fragment then the nightly pass is run on a throw-away user | anchor / speaker gate off → incident reproduced |
| F9 | **Authority matrix** writer × target class (all 14 paths) | `test_memory_authority.py` | — | `ZOE_MEMORY_AUTHORITY=off` |
| F10 | **Forget persists**: forget X, wait 6 min, run digest + idle + open loops over a transcript containing X | fake clock | S24 | revert to the 300 s in-process tombstone → X resurrected (today's behaviour) |
| F11 | **Forget cascades**: portrait, card, open loops, proactive candidates, `people` row | fixture DB | S25 | skip the cascade → mention survives |
| F12 | **Provenance honesty**: an edited row's `source` is the editor's; the reply distinguishes "you told me" from "I picked up" | metadata assertion | judged scenario (fixed rubric, temp 0) | restore the carry-forward → S1 signature |
| F13 | **Scale**: owner recall when other users hold 10× rows, after delete churn | in-memory + real chroma on a copy | — | filtered-only query → owner rows missing |
| F14 | **No churn**: weekly merge on identical texts performs 0 edits; audit edit count == real changes | S3 as a test | — | restore the merge → 138 self-edits |
| F15 | **Restart durability**: tombstones / ledger survive a process restart | persist + reload | — | in-process dict → lost |
| F16 | **Only the user promotes**: no `pending → approved` without a user actor | deep-sleep pass on a pending fixture | — | restore popularity promotion → red |
| F17 | **Opt-out parity** across chat, voice and idle lanes | iterate every source in the authority table | — | drop `voice_regex` from the list → red |

Benchmarks: F2 / F4 / F6 / F1 are the LongMemEval shapes (knowledge-update, temporal, abstention,
multi-session). Report them as **pass counts per category**, not as a comparable score.

### P2 — next

**P2.1 Temporal validity, always on.** `valid_from` unconditional (today only when the implicit-supersede flag
is on, `memory_service.py:1882`); distinguish `learned_at` (= `added_ts`) from `valid_from` (when it became
true, when the user says so: "since 2019", "until March"); `invalid_at` always written on supersede;
`MemoryService.search(as_of=…)` and a `history` read that includes `superseded` rows for "where did I
live before?" / "what did I think last year?"; tombstone rows (`state_change`) become first-class
history; fix the `used to` cue so it requires a state-change reading (`memory_supersede.py:66-110`; S4
shows the false positive); give events an `expires_at` at write time. *Test:* F2/F4. *Measure:* rows with
`valid_from` 12/82 → 100%; the temporal bar category. *Flag:* the stamp is a pure addition; the `as_of` read
is additive.

**P2.2 Forgetting that stays forgotten.** A Postgres `memory_forgotten` ledger (user, entity key,
`forgotten_at`, `scope`, actor) replacing the 300 s in-process tombstone (`memory_tombstones.py`):
consulted by `ingest` for **every** source except an explicit re-teach by a verified speaker, and by the
digest's transcript loader (`_load_todays_messages`) so turns containing a forgotten entity are skipped,
not re-mined; a cascade that archives/regenerates the portrait, card, open loops, proactive candidates and
soft-deletes the `people` row + edges (with a spoken "I've also removed them from your contacts and your
summary — say 'keep the contact' to undo"); a **hard-delete path** for "forget it for good" that removes the
Chroma row, the audit `before/after` text, and writes a text-free deletion record (`id`, class, time,
actor) — closing the ledger gap (`_delete_ids` writes nothing today); `brain_tool` loses its tombstone
exemption. *Tests:* F10, F11, F15. *Measure:* resurrection count (target 0), ledger-vs-palace
reconciliation in the report.

**P2.3 A nightly fidelity report.** `scripts/maintenance/memory_fidelity_report.py` + `GET
/api/memories/maintenance/fidelity` (beside `index-health`), run at the end of the dreaming cycle
(`memory_digest.py:2172`), read-only, JSON to `~/.zoe-logs/` and one Telegram line **only on anomaly**.
Fields: rows by status × authority class; approved-by-LLM-only share; `source_excerpt` / `user_turn_id` /
`valid_from` coverage; shadow `AUTHORITY_WOULD_BLOCK` counts by writer; contradictions by attribute
(same-attribute approved rows with different values — 0 today); duplicates and no-op edits; pending-queue
age; audit-vs-palace reconciliation (rows present in audit but absent with no deletion record); synthetic /
fixture-literal rows under real ids (§3.5); guest-bucket rows; unverified-speaker self-facts; recall
probe of 5 `user_stated` rows (extends `memory_recall_probe.py`, today one row); `IDENTITY_CONFLICT`
counts from #1866; opt-out parity. Reuses `memory_lint.py` (report-only, today off: set
`ZOE_MEMORY_LINT_IN_DREAMING`) and `memory_index_health.py`. *Test:* a golden report over a seeded palace;
negative control: seed an authority violation → the report flags it.

### P3 — hygiene fixes (each small, mostly pure)

1. **Weekly merge no-ops** (`memory_digest.py:1146-1181`): skip when `new_id == mem_id` / texts identical; stop
   stamping `reviewed_by` on no-ops. Kills ~6.6k audit rows and the fictitious "merged N" figure. Pure fix.
2. **Decay archive**: exempt `user_stated` and `user_confirmed` rows (or require explicit unaccessed-for-N-years
   plus a question) — a never-repeated "my dog is called …" must not be archived at ~1 year (V12).
3. **Raw upserts** (REM / deep sleep / synthesis, `memory_digest.py:1569-1573`, `:1668-1673`, `:1799`): use
   `col.update` under the per-user lock with a fresh read, never a stale full-dict upsert (V14).
4. **Opt-out list → allow-list**: derive it from the authority table so `voice_regex`,
   `voice_turn_digest`, `voice`, `idle_consolidation` are covered (V16).
5. **Guest writes**: reject `is_guest_memory_user` ids at `ingest` (8 write-only rows today).
6. **Ranking**: order by authority rank, then relevance × recency; drop writer `confidence` and access
   popularity as cross-class ranking inputs (V15, `memory_service.py:2267-2268`).
7. **Recall packet provenance**: tag each bullet "you told me" / "I picked this up" so the brain can hedge
   (feeds S3 and F12); `recall_evidence.py` already computes the effective writer.
8. **People-graph provenance**: `asserted_by`, `source_session`, `source_excerpt` on `people` and
   `person_relationships`; edge supersede obeys the same rank rule (V10); `_resolve_person_uuid` stops using
   `LIKE '%name%'` (`person_extractor.py:264`, a substring match that can attach a fact to "Samantha" for "Sam").
9. **Test-pollution guard**: refuse `ingest` from non-owner callers whose session id matches a harness
   pattern into a real user id, and quarantine `mcp` / `zoe_agent` writes to `model_from_*` (§3.5: 114 ingests
   under the owner's id since 09-20, 93 gone with no trace).
10. **Config smell**: `MEMORY_DIGEST_MODEL=gpt-4o-mini` in `.env` while the code defaults to the Gemma GGUF —
    the local llama-server ignores the id, but a model name that disagrees with the machine will mislead the
    next debugger. Align or remove.

### Sequencing

PR A (additive, safe): class table + stamps + shadow `check()` + the merge no-op fix + the pack skeleton
(F9, F14, F6) + `memory_fidelity_report.py`. PR B: enforce flip + digest anchor + user-only idle transcript
+ the `used to` fix. PR C (voice-path, serial, replay-gated): speaker verdict + gate. PR D: forgetting
ledger + cascade + temporal reads. Each ≤ 30 files; the bar baseline is re-recorded after B.

## 7. Decisions for Jason

1. **Strict authority, or ask?** Adopt "a model can never overwrite what you said; a contradiction becomes a
   question Zoe asks you (spoken at the next natural moment or queued in the panel)". The alternative is
   quiet last-write-wins with a visible history. The first is what the directive reads like; it costs an
   occasional "earlier you told me … which is right?".
2. **What does "forget" mean?** Today: archive + a five-minute shield. Proposed: a permanent "forgotten"
   ledger that also stops the nightly digest re-reading those turns, cascades to your summary, contacts and
   open loops, and a spoken "forget it for good" that really deletes (text-free record kept). Do you want
   that default, or opt-in per request?
3. **People who aren't you at the panel.** Speech the panel cannot attribute to you by voice would no longer
   create facts *about you* (it can still offer to add a name as a contact). Visitors and family speaking near
   the panel stop leaking in; the price is that a quiet "my name is…" from you with a poor voice match waits
   in the review queue instead of being saved. Acceptable?
4. **How much history do you want kept visible?** With temporal validity on, Zoe can answer "where did I live
   before?" from superseded rows. That is also more retained personal history. Keep it queryable by default,
   or only on request, or expire superseded text after N days?

## 8. Sources

**Our code (worktree base `08538155`, line numbers as cited inline).** `services/zoe-data/`: `memory_service.py`,
`memory_digest.py`, `memory_extractor.py`, `memory_quality.py`, `memory_supersede.py`, `memory_tombstones.py`,
`memory_idle_consolidation.py`, `correction_apply.py`, `person_extractor.py`, `person_extractor_llm.py`,
`expert_dispatch.py`, `intent_router.py`, `mcp_server.py`, `zoe_agent.py`, `recall_evidence.py`,
`user_prefs.py`, `user_filters.py`, `pending_suggestions.py`, `routers/{chat,voice_tts,memories,people,notes,journal,user_profile}.py`,
`zoe_flue_client.py`, `skybridge_service.py`, `hindsight_retain_candidates.py`; `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`;
`scripts/perf/samantha_bar.py`, `scripts/perf/samantha_day_sim.py`. PR #1866
(`fix/identity-and-session-continuity`): `docs/knowledge/identity-and-session-continuity.md`,
`identity_facts.py`.

**Live state read (read-only, 2026-10-05).** `~/.mempalace/chroma.sqlite3` (`mode=ro`); Postgres
`auth_users`, `users`, `people`, `person_relationships`, `person_activities`, `chat_messages`,
`chat_sessions`; `services/zoe-data/.env` flag names and values (memory flags only).

**Field.** Papers: LoCoMo https://arxiv.org/abs/2402.17753 · LongMemEval https://arxiv.org/abs/2410.10813 ·
Mem0 https://arxiv.org/abs/2504.19413 · Zep https://arxiv.org/abs/2501.13956 · MemGPT https://arxiv.org/abs/2310.08560 ·
MemIR https://arxiv.org/abs/2605.25869 · Memory Provenance Laundering https://arxiv.org/abs/2607.29167 ·
MemTrace https://arxiv.org/abs/2605.28732. Vendor docs and blogs: mem0 https://docs.mem0.ai/migration/oss-v2-to-v3,
https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm, https://docs.mem0.ai/api-reference/memory/history-memory,
https://docs.mem0.ai/core-concepts/memory-operations/update · Letta https://docs.letta.com/guides/agents/archival-memory,
https://docs.letta.com/guides/agents/memory-blocks, https://www.letta.com/blog/benchmarking-ai-agent-memory/,
https://www.letta.com/blog/sleep-time-compute/ · Graphiti https://github.com/getzep/graphiti,
https://github.com/getzep/graphiti/issues/1728, https://github.com/getzep/graphiti/issues/864 ·
Zep/Mem0 dispute https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/,
https://github.com/getzep/zep-papers/issues/5 · mem0 issue https://github.com/mem0ai/mem0/issues/4573 ·
ChatGPT [secondary] https://help.openai.com/en/articles/8590148-memory-faq,
https://www.techtimes.com/articles/317840/20260605/chatgpt-memory-dreaming-update-openai-rewrites-personalization-engine-limits-audit-trail.htm,
https://embracethered.com/blog/posts/2024/chatgpt-hacking-memories/ · Anthropic
https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool,
https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context ·
Nomi https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/,
https://wiki.nomi.ai/Are_Mind_Map_Entries_Memories%3F · LoCoMo audit https://github.com/dial481/locomo-audit ·
MemGhost https://thehackernews.com/2026/07/new-memghost-attack-plants-persistent.html.

**Not done, deliberately.** No code changed; no live service, `.env` or flag touched; no model loaded; no
turn sent to the live API. The three reproductions ran on an in-memory collection. Palace numbers are a point
snapshot (10:00 local, 2026-10-05) and will move with the next nightly pass.

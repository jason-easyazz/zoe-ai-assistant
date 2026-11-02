# Person-centred memory: contacts, profiles and the assistant that remembers people

> Status: DESIGN (2026-11-02), no code. Owner ask: "memories and contacts - originally we pulled inspiration from a full
> open-source CRM for contacts, but I feel that has been lost. Memories are being created but not really assigned or
> curated, set up properly, easy to read ... what do you know about me gives odd responses ... where do I go to see
> everything about one contact ... birthday reminders, gift ideas ... think Samantha, think what an assistant would do."
>
> PUBLIC repo: this page holds shapes, counts and file references only. No household names, facts or memory text.
> Measured read-only against the live box on 2026-11-02 (Postgres `people*` tables, the MemPalace chroma file opened
> `mode=ro`, the chat transcript table, the code on `main`).

## 0. Answer first

- **The CRM was built, then starved.** May 2026 shipped a Monica-inspired people CRM (circle, health score, dates, gifts,
  bucket list, activities, 4-tab detail, birthday + keep-in-touch triggers). The plumbing is all there. The *data* is not:
  of 32 `people` rows, **0 have a birthday, email, phone, notes, preferences or how-we-met**; the date, gift and bucket
  tables hold **0 rows**; there are **3 relationship edges** and **4 activity rows**. The birthday trigger reads
  `person_important_dates` (0 rows), so it can never fire. The keep-in-touch trigger needs `health_score < 0.3` and every
  row sits at the default.
- **Memories are written but mostly not attached.** 325 drawer rows (146 approved). Only **50 of 146 approved (34%)** carry
  a person link; **96 (66%) have no subject at all**; 13 of the linked point at a name-only stub with no `people` row.
  Only **39 of 146 (27%)** carry a provenance label (`user_stated` 17, `inferred` 22); 107 have none.
- **"What do you know about me" is already a deterministic tier** (`provenance_answers`), which is good, but its output
  is a count-and-newest-item summary, and "what do you know about <contact>" is *deliberately* not handled by it (returns
  to the brain). In the transcript table, 11 "about me" turns: 7 got the tier's template, 4 went to the brain (2
  denial-shaped); 13 "about / tell me about <someone>" turns: **5 denial-shaped**, mean reply 155 characters.
- **No page answers "everything about this person".** The people pages show circle, a timeline and gifts/dates tabs
  that are empty; the Memories pages are a notes/collections scrapbook plus a "what Zoe knows about me" space that has no
  per-person view.
- **The fix is not a new system.** Build a per-person *profile read-model* over what exists (people graph + satellite
  tables + contact card + memory authority rows), make every write resolve its subject, add a curation loop that routes
  doubt to the owner, and feed dates and follow-ups into the existing pull queue. Adopt Monica's *shapes*, not its code.

## 1. Current state (measured)

### 1.1 Data (counts, 2026-11-02)

| Store | Measure |
|---|---|
| `people` | 32 rows (14 soft-deleted, 18 live) across 6 members. `relationship` 31/32, `circle` 32/32 (all default-assigned), `health_score` 32/32 (all default). `last_contacted_at` 3. `contact_count > 0`: 0. `email` / `phone` / `birthday` / `notes` / `preferences` / `how_we_met` / `first_met_date` / `introduced_by`: **0 each**. `is_partial`: 0. |
| `person_important_dates` | 0 |
| `person_gift_ideas` / `person_bucket_list` | 0 / 0 |
| `person_activities` | 4 (3 `fact`, 1 `meeting`; sources voice 2, conversation 2) |
| `person_relationships` | 3 edges, all current, none closed |
| contact card (`contact_fields`, `source_contacts`, `person_contact_links`) | 0 / 0 / 0 (built, no connector connected) |
| `pending_suggestions` | 36: `person_create` 29 (24 resolved, 5 open), `note_create` 6 open, `reminder_create` 1 open |
| `open_loops` | 17 (12 resolved, 5 open); none linked to a person |
| `memory_forgotten` | 0 |

Mismatch worth noting: the `people` rows have almost no structured attributes, yet 47 approved memory rows are typed
`person`. The facts are being captured as *prose memories*, not as profile fields, and are not reachable from the person.

### 1.2 Memory store (MemPalace / chroma, `mempalace_drawers`)

- 325 rows (+ 19,854 audit rows). Status: **146 approved, 162 archived, 16 superseded, 1 pending**. Archive is dominated
  by the weekly identical-duplicate sweep (142 rows), so dedupe of *exact* repeats works; 3 exact-text duplicate groups
  (6 rows) survive among approved. Near-duplicates (paraphrase) are not measured by anything today.
- Approved rows by type: fact 49, person 47, emotional moment 11, relationship 8, insight 8, event 7, health 6, profile 4,
  habit 2, journal 2. By wing: 138 of 146 belong to the owner's wing, 6 to the family-admin wing, 2 to another.
  The store is overwhelmingly "about the owner"; person facts are a minority and thinly spread (**30 distinct entities**
  across 50 linked rows).
- Writers (approved rows by `source`): idle consolidation 40, conversation 26, digest 16, operator restore 16, voice
  turn digest 12, voice 10, synthesis 8, turn digest 5, person created 4, chat regex 2.
- Provenance: 39/146 labelled (see section 0). 18 approved rows were reviewed by a person. Only 2 approved rows carry a
  validity end (`invalid_at` / `expired_at`): **nothing ages.** All timestamps cluster in July (78) and October (47).
- Supersession and disputes exist (`memory_authority`, `memory_disputes`, `b3-1-bitemporal-supersession`), 157 rows carry
  a `supersedes_id`; there is no *person-keyed single-value* contradiction check (employer, city, partner).

### 1.3 Write paths

| Path | Attaches a person? |
|---|---|
| `person_extractor.process_text` (regex) + `person_extractor_llm` (Gemma), every chat and voice turn | Yes: upserts `people` stubs, `person_activities`, dates, gifts, edges, mirrors a fact with `entity_id`. Birthday capture for a not-yet-a-contact name is behind `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` (code default OFF, not pinned in any env file). Edge changes hold as `pending` unless `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED`. |
| `memory_extractor` (pronoun-anchor, "my X's name is Y") | Sometimes: a name slug as `entity_id`; pronoun-anchoring has produced misattributions before (3 archived rows say so). |
| Idle consolidation, turn/voice digest, synthesis | **No subject resolution**: the biggest source of unlinked rows. |
| Omi day review (approve-before-write) | Proposals; writes only on approval. |
| Explicit "remember X" (`ask_to_remember`, `hold_the_fact`) | Owner-stated, no person link unless the extractor also fired. |
| Structural reader / claims | Shadow. |

No path ages a fact, proposes a merge of near-duplicates, or asks "is this still true?".

### 1.4 Read paths (what actually runs)

| Utterance | Path |
|---|---|
| "what do you know about me" (+ variants) | `fast_tiers` -> `provenance_answers`: deterministic, owner-scoped, grouped counts + newest item per group (voice: ~3 sentences; chat: longer list). Private classes counted, never read. A guest is refused. |
| "what do you know about <another member>" | Refused by design (`OTHER_MEMBER_REPLY`). |
| "what do you know about <contact>" / "tell me about <contact>" | **Falls through to the brain.** The named-person floor (`person_recall_floor`, `memory_gate`) forces the for-prompt packet and the 1-hop relational block, so the brain *can* answer, but it summarises whatever the packet holds; there is no profile, no ordering, no "tell me more", and with an empty graph it answers thinly or denies. |
| "who is X" for an unknown name | Forced recall pass, then an honest "no". |

### 1.5 UI

- Desktop `people.html` (~1,970 lines): card grid, 4-tab detail (Timeline / About / Dates and gifts / Together).
  Touch `touch/people.html` (~1,300 lines): same, with health dots. Both are driven by the empty tables above.
- `memories.html` / `touch/memories.html`: a notes-and-collections scrapbook, **not** the memory store. The estate
  "Memories" space (`touch-memories-space.js`, `GET /api/memories/mine`) shows the signed-in member's own rows grouped by
  topic with per-item forget; it has no per-person view and no confirm queue.
- `GET /api/memories/review` + `POST /api/memories/{id}/review` exist (approve/reject) but no page leads the owner there.

### 1.6 Proactive

- `PeopleBirthdayTrigger`: 7-day lookahead at 08:00, **excludes day-of**, reads only `person_important_dates`
  (ignores `people.birthday`). With 0 rows it has never had anything to say.
- `PeopleHealthTrigger`: 21 days no contact and `health_score < 0.3`; the score is default for every row and
  `last_contacted_at` is rarely written, so it cannot trigger.
- No gift suggestion, no anniversary lead-up, no "ask how X went", no owner-set cadence. `open_loops` carries a
  `follow_up_after` and feeds `EmotionalFollowUpTrigger` (off by default) but is not tied to a person.
- The channel is right: the selector's nightly `proactive_candidates` + the **pull** tier (`proactive/pull.py`, orb dot,
  "anything for me?") with restraint, welcome tap and `ZOE_PROACTIVE_SPOKEN=0`.

### 1.7 The original inspiration

`docs/guides/QUICK_START_PEOPLE.md` credits **Monica** (open-source personal CRM). The 2026-05-18 implementation
(`c3101c860`) copied its *skeleton*: circles, health decay, activities, important dates, gift ideas, bucket list,
timeline, a relationships graph. What Monica offers that Zoe lost or never built: reminders that actually fire (birthdays
with a lead time, "stay in touch" frequency per contact), gift state machine (idea / offered / received), a life-events
timeline, "how you met", a contact's own food/pet/work facts, and a **single contact page where all of it lives**.
What Zoe has that Monica lacks: voice capture, provenance, forget-forever, temporal relationships, privacy walls.

## 2. Field scan (the pieces worth borrowing; from knowledge, not re-fetched)

| Product | Take | Do not take | Licence note |
|---|---|---|---|
| **Monica** | Contact page as the unit; reminders with per-contact lead times and a "stay in touch" frequency; gift lifecycle idea -> offered -> received; life events; "how you met"; relationship types with inverses | The PHP/Laravel stack, multi-tenant accounts | **AGPL-3.0**: network copyleft. Borrow *schema shapes only*, no code. Zoe already has the inverse-role schema. |
| **Clay / Dex** (closed SaaS) | Keep-in-touch cadence set per person or group, and the nudge surfaced as "overdue by N days"; "last interaction" auto-derived from calendar and messages; a one-line "what to ask about" per person | Social-profile scraping, network enrichment (violates local/private) | Closed; ideas only. |
| **Twenty** | Custom fields on a person; an activity timeline per record | A generic object-builder (we have a fixed domain) | **AGPL-3.0** core (plus a commercial enterprise folder): ideas only. Zoe's `people_field_definitions` already covers custom fields. |
| **Apple / Google Contacts** | RFC 6350 / 9553 shapes: BDAY, ANNIVERSARY, RELATED, NICKNAME, NOTE | Their cloud as source of truth | Standards; already in `contacts-data-model.md`. |
| **Assistant products** (Replika, Kindroid, Nomi, ChatGPT memory; see `docs/research/person-likeness-2026-10-09.md` section 7) | A memory view the user can open and delete from; one unprompted item a day with a feedback tap; pause words | Silent memory writes; first-person loneliness lines | Already the restraint policy. |

Adopt-don't-rebuild verdict: **no code reuse is warranted.** Everything adoptable is a data shape Zoe already has or a UX
pattern; the work is wiring and curation, not a new CRM.

## 3. Design

### 3.1 Principles

1. **No parallel system.** Facts stay where they live: structured, dated, enumerable things in the people graph and
   satellite tables; free-form facts as memory authority rows linked by `entity_id = people.id`. A *profile* is a
   read-model composed from both, never a copy.
2. **One home per datum.** A date lives only in `person_important_dates`; a relationship only on `person_relationships`;
   contact numbers only in `contact_fields`; a like or a life-now fact only as a memory row. The legacy `people.birthday /
   notes / preferences / email / phone` columns (0 rows filled) are read-through for one release, then removed.
3. **Authority ladder, never inverted** (owner rules): user-stated > confirmed > inferred; inference never supersedes;
   every write has provenance; **inferred person facts are approve-before-write** (they land `pending`, are not served,
   and wait in the confirm queue); forgotten means forever (existing cascade + tombstones; the Person page "forget"
   calls the same path).
4. **Zero-LLM, bounded reads on the hot path.** A profile is one `people` read + satellite reads + one entity-scoped
   memory read; composition is deterministic. LLM work stays in the night window.
5. **Dates and doubt go to the owner through the existing queue.** Pull, not push; no new spoken proactivity.

### 3.2 The person profile model

A profile is `{person, facets}`. Every fact line carries `{text or value, authority (user_stated | confirmed | inferred),
confidence, source (turn id pointer / contact card / Omi review / owner edit), captured_at, valid_from, review_after}`.

| Facet | Backed by | Notes |
|---|---|---|
| About | `people` (name, nickname, relationship, circle, pronouns) + `contact_fields` | Relationship is a derived label from the edge when present. |
| Important dates | `person_important_dates` | birthday, anniversary, other; `reminder_days_before` per row; synced vs member-said kept as separate rows (C2 in contacts model). |
| Likes / dislikes | memory rows, `facet=likes` (new metadata key beside `concept_tags`) | Includes allergies and diet as a *sensitive* sub-class (counted, spoken only when asked). |
| Gift ideas | `person_gift_ideas` (idea / offered / received) | An idea cites the like or remark it came from (`mem_id`). |
| Life now | memory rows, `facet=life_now`, with `review_after` | Job, project, trip, health event: expires into a "still true?" check. |
| How we know them | `people.how_we_met`, `first_met_date`, `introduced_by_person_id` + edges | Currently all empty; filled by a one-time gentle ask, never a form. |
| Last contact / cadence | `last_contacted_at`, new `cadence_days` | Written when the owner says they spoke, from an Omi-reviewed meeting, or a logged call. |
| Relationships | `person_relationships` (temporal) | Unchanged. |
| Open threads | `open_loops` gains `person_id` | "Ask how X's interview went." |

The new metadata is small: `facet`, `subject_basis` (`exact_name | pronoun_anchor | model | owner_confirmed`),
`review_after`, plus `people.cadence_days` and `open_loops.person_id`. One additive Alembic migration; chroma metadata keys
need none.

### 3.3 Assignment and curation pipeline

**At write time** (`MemoryService.ingest` choke point, so every writer benefits):
1. Resolve the subject with `people_graph.resolve_person_id` (the existing invariant: unique -> attach; ambiguous -> hold
   as a pending candidate and ask; unknown -> `person_pending` stub as today). No subject = the speaking owner.
2. Classify `facet` deterministically (lexicon/regex, same style as `provenance_answers.group_of`); unclassified stays
   unfaceted rather than guessed.
3. `subject_basis` records how the link was made. A `pronoun_anchor` or `model` link on a minor's or sensitive row never
   auto-attaches; it goes to the confirm queue.
4. Inferred authority -> `status=pending` (existing). User-stated -> approved immediately.

**Night window** (existing idle/dreaming pass; heavy work off the hot path):
- **Backfill**: for the 96 unlinked approved rows, whole-name unique match only -> auto-attach (reversible, audited);
  anything else -> queue. No fuzzy auto-attach (the 2026-07 coreference artifacts are the reason).
- **Merge duplicates**: same entity + facet + normalised text -> archive the later (as the weekly sweep does today);
  paraphrase near-duplicates (embedding threshold) -> *propose* merge, never silent. Invalidate, never delete.
- **Age**: `life_now` and mood-class facts get `review_after` (default 90 days / 14 days). On expiry: "still true?" in the
  queue; stable facts (birthday, allergy, relationship) never age.
- **Contradictions**: a person-keyed single-value check (employer, home city, partner, diet) reuses `memory_lint` +
  `memory_disputes` to raise ONE question ("earlier you said A, now B?"), answered by the owner; the older row is
  retired only on their answer.
- **Confirm queue** = `pending_suggestions` / `/api/memories/review`, one list, grouped by person, capped (batch of
  5 per week as one card) so it never becomes homework.

**Kids**: a child's profile holds only guardian-stated facts; no inferred facts, no health or mood facets, no gift
inference, no night-window model passes over it; birthday (date only) still reminds. A child member's own rows remain
theirs alone (existing walls). The enrol/ask rules for kids stay as they are.

### 3.4 "What do you know about me / <person>"

Both become deterministic tiers in `fast_tiers.resolve` (same place as `provenance_answers`, same replay-gate rule).

- **About <person>**: resolve the name (ambiguity -> `ask_when_ambiguous`'s one question). Spoken, about 3 sentences,
  fixed order: who they are to you (relationship + how you know them) -> one life-now item -> the next date -> "Want more,
  or want the gift ideas?". "Tell me more" pages through facets one at a time. Chat/Telegram: the grouped card with
  authority chips ("you said", "you confirmed", "I think"). Sparse profile: say what is missing and offer to fill one gap
  (a question, not a form). Never the brain's paraphrase; every line is a stored row.
- **About me**: keep the existing tier and walls; re-shape the output into the same ordered sections (Basics, People
  who matter, Routines, Likes, Coming up) plus "N things I want to check with you". Route *all* about-me shapes to the
  tier so none reach the brain (4 of 11 recently did).
- **Privacy at the panel**: a voice-verified member hears their own contacts; guest and unverified speaker hear nothing
  (existing replies). Private classes (health, money, feelings, relationships in trouble, allergies) are counted, never
  read, unless pulled by name and only by a verified owner. Another household member's *account* is not a contact: still
  refused. Household-visible people (`visibility=family`, the fridge rule): name, relationship and dates are visible to any
  verified adult member; facts and notes stay personal.

### 3.5 The Person page

Extend the existing `people.html` / `touch/people.html` detail into the profile (no new app): header (name, relationship,
next date, last contact), the facets of 3.2, a **"to check"** strip (that person's confirm-queue items, one tap yes/no),
a timeline, and a **Forget** button. Every fact row: text, authority chip, source/date, edit, delete.
- Phone and desktop: full editing. Panel: large targets, no keyboard; tap yes/no/forget, and any typed edit hands off to
  the phone via the existing QR handoff. Voice edits use the one-sentence fix ("that's wrong, it's X") already built.
- Reuses `/api/people/{id}/...` routes; adds `GET /api/people/{id}/profile` (the composed read-model) and a per-person
  filter on `/api/memories/review`.

### 3.6 The proactive assistant

All of it as *candidates into the existing selector/pull queue*; nothing new is spoken unprompted
(`ZOE_PROACTIVE_SPOKEN=0` stands). Orb dot, "anything for me?", restraint, welcome tap, quiet hours all apply.

| Candidate | Source | Rule |
|---|---|---|
| Date lead-up | `person_important_dates` | Default 7 days and day-of (inner/family: 14, 3, day-of); per-person override. Fix the trigger: include day-of, read both date sources. |
| Gift idea | stored likes + `person_gift_ideas` | Only from stored, sourced likes ("you mentioned ... in March"); logs what was given before so it never repeats; never browses or buys on its own. |
| Keep in touch | `cadence_days` + `last_contacted_at` | **Opt-in per person or circle**, set by the owner ("remind me to call mum every two weeks"); no auto-nagging. |
| Follow-up | `open_loops.person_id` + `follow_up_after` | "Ask how X's appointment went", once, then resolved. |
| Fill a gap | a profile with no birthday | At most one gentle ask per week, respects a decline permanently. |

Retire by removing: `PeopleHealthTrigger` (health score is decay arithmetic over data that is never written) is replaced
by the cadence candidate; the birthday trigger becomes the date-lead candidate generator.

## 4. Phases

| # | Phase | Size | What it delivers | Notes |
|---|---|---|---|---|
| 1 | **Assign + read**: write-time subject resolution, facet tag, night backfill (unique-name only), `person_profile` composer, deterministic "about <person>" and re-shaped "about me" | M-L (~600-800 lines, 2 PRs) | The two questions the owner asked now return organised, sourced answers; links 66% unlinked rows safely | Voice path: replay-gate, serial landing, outside the night window. Most felt value. |
| 2 | **Dates + nudges**: fix birthday lead-up (day-of, both sources), enable birthday capture, date-lead + gift candidates into the pull queue | M (~400) | Birthday reminders actually fire; gift ideas from real likes | Needs dates captured: phase 1 fills some; the gentle "when is X's birthday?" ask fills the rest. |
| 3 | **Person page**: profile view, to-check strip, per-fact edit/forget, phone hand-off | M-L (~700 UI) | "Where do I go for everything about X" | Desktop + touch; UI browser-verify gates. |
| 4 | **Curation loop**: aging (`review_after`), near-duplicate proposals, person-keyed contradictions, weekly confirm card | M (~500) | Memory stays correct and readable instead of accreting | Night window only; Greptile-tier review (load-bearing). |
| 5 | **Keep-in-touch + follow-ups**: `cadence_days`, `open_loops.person_id`, cadence candidates | S-M (~300) | The assistant part: "you haven't spoken to X in a while", "ask about Y" | Retire `PeopleHealthTrigger`. |
| 6 | **Card enrichment**: contact import/sync fills about/dates/notes | L (connector work) | Real birthdays/phones in bulk | Already designed in `contacts-data-model.md`; gated on the connectors work and owner C1-C4. |

Acceptance: extend the Samantha bar harness with person cells (said-vs-did on "about <person>", date lead-up fires on the
right day, a wrong inferred fact is one-step correctable, a forgotten person never returns, guest hears nothing).

## 5. Risks

- **Misattribution** (pronoun anchors, shared names): mitigated by unique-whole-name-only auto-attach, `subject_basis`,
  ambiguity -> ask, and reversibility. The 2026-07 coreference artifacts are the standing warning.
- **Stale facts erode trust**: aging + "still true?" + contradiction questions; stable facts never age.
- **Homework fatigue**: the confirm queue is capped and batched; a decline is permanent; nothing blocks use.
- **Panel privacy**: the wall display is a shared room; spoken answers are verified-voice-only and private classes are
  counted, not read; pinned by negative-control tests as `provenance_answers` already is.
- **Hot-path latency / voice regression**: composition is deterministic and bounded; any `fast_tiers` change is
  replay-gated against the real-voice corpus and lands serially.
- **Sparse data**: a profile of a barely-known person must say so and ask one question, not pad.
- **AGPL contagion**: avoided by borrowing shapes only.
- **Empty-to-dense cliff**: phase 1 before phase 3; a gorgeous page over empty tables is the failure already shipped once.

## 6. Owner decisions (each with a recommendation)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Inferred person facts: approve-before-write, or serve labelled "I think"? | **Approve-before-write** (your standing rule). Batch into one weekly card. User-stated facts write immediately. 22 approved rows today are `inferred`; leave them, label them, offer to confirm in bulk. |
| D2 | Turn on birthday capture from conversation (`ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED`)? | **Yes**, after the phase-2 trigger fix, so a captured date has somewhere to go. |
| D3 | Reminder lead times | 7 days + day-of; inner/family 14, 3 and day-of; per-person override by voice. |
| D4 | Keep-in-touch cadence | **Opt-in only**, owner-set; no automatic cadence for anyone. |
| D5 | Gift ideas | Only from stored, sourced likes and past-gift log; no web browsing or buying by default. |
| D6 | Who hears what at the panel | Verified member: own contacts; guest/unverified: nothing; private classes counted not read; family-visible people: name, relationship and dates household-wide, facts personal-only. |
| D7 | Children's profiles | Guardian-stated facts only, no inference, no health/mood facets, date reminders yes. |
| D8 | Reuse Monica / Twenty code | **No** (AGPL-3.0, wrong stack). Shapes only. |
| D9 | Retire the legacy `people.birthday / notes / preferences` columns | Yes: read-through one release, then drop (all 0 rows). |
| D10 | Person page: new page or extend? | Extend `people.html` / `touch/people.html`. |
| D11 | Name-only stubs (`person_pending`) | Ask ("is that the X you mentioned?") before promoting to a `people` row; never auto-create on a model's say-so. |
| D12 | Phase order | 1 -> 2 -> 3 -> 4 -> 5; phase 6 follows the connectors work. If only one thing lands this week: phase 1 plus the birthday fix. |

## 7. References

- [contacts-data-model.md](contacts-data-model.md) (card, provenance, merge review, forget) and
  [ADR-relationship-memory](../adr/ADR-relationship-memory.md) (people graph invariants).
- [zoe-memory-samantha-buildplan.md](zoe-memory-samantha-buildplan.md), `services/zoe-data/memory_authority.py`,
  `provenance_answers.py`, `memory_space.py`, `person_recall_floor.py`, `proactive/pull.py`.
- [person-likeness-2026-10-09.md](../research/person-likeness-2026-10-09.md) (restraint and salience policy).
- [people_crm.md](../guides/people_crm.md) (the May 2026 build).

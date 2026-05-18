# People CRM — Developer Guide

## Overview: Two-Layer Design

Every person fact lives in two places simultaneously:

| Layer | Purpose | Module |
|---|---|---|
| **PostgreSQL** | Structured data, UI rendering, queries | `routers/people.py` |
| **MemPalace** | Semantic recall in every conversation | `memory_service.py` |

When you write a fact about a person, `person_extractor.process_text()` fans it out to both layers, using the DB UUID as `entity_id` so the two layers are permanently linked.

---

## Circle Model

| Circle | Half-life (health decay) | Typical members |
|---|---|---|
| `inner` | 14 days | Partner, closest family, best friends |
| `friends` | 30 days | Regular friends |
| `family` | 21 days | Parents, siblings, children |
| `work` | 45 days | Colleagues, clients |
| `acquaintance` | 60 days | Anyone else |
| `public` | 90 days | Public figures, loose connections |

The circle is set on create/update (`PersonCreate.circle`) and auto-inferred from `relationship` text by the Alembic 0006 migration.

---

## How Facts Are Extracted from Conversations

Every chat and voice turn passes through `_persist_memory_candidates` / `_run_voice_memory_passes`, which now calls `person_extractor.process_text()` in parallel.

### Recognised patterns

| Example utterance | PostgreSQL target | MemPalace text |
|---|---|---|
| "Sarah loves jazz" | `person_activities` type=fact | "Sarah loves jazz" |
| "Sarah's birthday is 15 March" | `person_important_dates` + activity | "Sarah's birthday is 15 March" |
| "Sarah works at Acme" | `person_activities` type=fact | "Sarah works at Acme" |
| "met Sarah for coffee" | `person_activities` type=meeting | "Met Sarah" |
| "getting Sarah a headphone" | `person_gift_ideas` status=idea | "Gift idea for Sarah: headphone" |
| "gave Sarah a book for her birthday" | `person_gift_ideas` status=given | "Gave Sarah a book" |
| "want to travel with Sarah" | `person_bucket_list` | "Want to travel with Sarah" |

### Entity ID strategy

- Person found in `people` table → `entity_id = DB UUID`, `entity_type = "person"`
- Person not found → `entity_id = "slug:sarah"`, `entity_type = "person_pending"`

---

## How to Introduce Someone to Zoe

Say or type any of:
- `"Zoe, meet Alice"`
- `"This is Bob"`
- `"Introduce you to Carol"`
- `"Say hi to Dave"`

Zoe will:
1. Create or find the person in the `people` table
2. Navigate the touch panel to `/touch/people.html?person=<id>&intro=1`
3. Reply: *"Hi Alice, I'm Zoe. So nice to meet you! What do you do for work, or what are you passionate about?"*
4. Store any follow-up answers as `person_activities` and MemPalace facts

---

## How Relationship Health Scoring Works

`person_health.calc_health_score()` returns a float in `[0.0, 1.0]`.

```
score = 0.6 × recency + 0.3 × frequency + birthday_boost
```

- **Recency** = `exp(−days_since_contact / half_life)` — decays exponentially
- **Frequency** = `log(1 + contact_count) / log(1 + 50)` — capped at 1.0
- **Birthday boost** = +0.3 if birthday is within 14 days

`recalc_and_save()` is called automatically after every write to `person_activities`, `person_important_dates`, `people` UPDATE.

### Health dot colours (UI)

| Score | Level | Colour |
|---|---|---|
| ≥ 0.6 | good | Green |
| 0.35–0.59 | fair | Amber |
| < 0.35 | low | Red |

---

## How Proactive Nudges Fire

Two triggers run daily in the proactive slow-loop:

### `PeopleHealthTrigger` (9am)
Fires for Inner Circle / friends where:
- `health_score < 0.3` AND
- `last_contacted_at < NOW() − 21 days`

Message: *"You haven't spoken to Sarah in 3 weeks."*

### `PeopleBirthdayTrigger` (8am)
Fires for contacts whose birthday is **1–7 days away** (not day-of, for lead time).

Message: *"Sarah's birthday is in 5 days."*

---

## Chat / Voice Commands Reference

| What you say | Intent | What Zoe does |
|---|---|---|
| "Zoe, meet Alice" | `people_introduce` | Creates contact, opens panel, asks intro questions |
| "Add contact John" | `people_create` | Creates person via API |
| "Sarah loves hiking" | *(passive, via memory path)* | Extracts fact, writes to DB + MemPalace |
| "Sarah's birthday is March 15" | *(passive)* | Writes `person_important_dates` |
| "Getting Sarah a headphone" | *(passive)* | Writes `person_gift_ideas` status=idea |
| "Met Tom for coffee" | *(passive)* | Writes `person_activities` type=meeting |

---

## API Reference

### Core CRUD

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/people/` | List people (supports `?circle=`, `?search=`, `?limit=`, `?offset=`) |
| `POST` | `/api/people/` | Create person (body: `PersonCreate` incl. `circle`) |
| `GET` | `/api/people/fields` | List field definitions (must come before `/{person_id}`) |
| `POST` | `/api/people/fields` | Create field definition |
| `PUT` | `/api/people/fields/{field_key}` | Update field definition |
| `GET` | `/api/people/{id}` | Get person by ID |
| `PUT` | `/api/people/{id}` | Update person (incl. `circle`) |
| `DELETE` | `/api/people/{id}` | Soft-delete + archive MemPalace facts |
| `PUT` | `/api/people/{id}/mark-read` | Reset `notification_count` to 0 |

### Sub-resources

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/people/{id}/activities` | List activity timeline |
| `POST` | `/api/people/{id}/activities` | Log an activity (`activity_type`, `description`, `source`) |
| `GET` | `/api/people/{id}/important-dates` | List dates |
| `POST` | `/api/people/{id}/important-dates` | Add date (`label`, `date_type`, `month`, `day`, `year`) |
| `GET` | `/api/people/{id}/gift-ideas` | List gift ideas |
| `POST` | `/api/people/{id}/gift-ideas` | Add gift idea |
| `PUT` | `/api/people/{id}/gift-ideas/{gift_id}` | Update gift status (`idea` → `purchased` → `given`) |
| `GET` | `/api/people/{id}/bucket-list` | List bucket list |
| `POST` | `/api/people/{id}/bucket-list` | Add item |
| `PUT` | `/api/people/{id}/bucket-list/{item_id}/done` | Mark done |

---

## Developer Notes

### entity_id consistency
- `people.py` writes `entity_id = person.id` (DB UUID)
- `memory_extractor.py` (legacy) writes `entity_id = "name_slug"` — acceptable for supplemental entries
- `person_extractor.py` (new) always resolves to DB UUID when possible → authoritative entity-scoped entries

### mem_id backlink
All CRM sub-resource tables (`person_activities`, `person_important_dates`, `person_gift_ideas`, `person_bucket_list`) have a `mem_id TEXT` column. This is populated with the MemPalace ID returned by `_ingest_to_mempalace()`, enabling bidirectional lookup.

### Symmetric delete
`DELETE /api/people/{id}` calls `memory_service.archive_by_entity(entity_id=person_id, user_id=user_id)` in the background via `asyncio.ensure_future`. This archives (not deletes) all MemPalace facts for the person, preserving auditability.

### Route order fix
`GET /api/people/fields` **must** be declared before `GET /api/people/{person_id}` in FastAPI registration order. The Alembic 0006 migration and `people.py` rewrite both enforce this.

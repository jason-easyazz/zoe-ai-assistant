---
type: record
title: The superseded-value registry (ZOE_MEMORY_SUPERSEDED_REGISTRY)
description: A durable, word-free record of the values a member corrected away (alembic 0080, memory_value_registry.py) so a late idle pass cannot re-file them; flag off|shadow|on, default off, shadow-only today; what it stores, the said-at rule, every forget path that erases it, and the log lines.
tags: [memory, corrections, forget, flag, migration, privacy]
timestamp: 2026-11-14T00:00:00Z
---

# The superseded-value registry (`ZOE_MEMORY_SUPERSEDED_REGISTRY`)

Build step 5 of the memory redesign (structured claims + deterministic validation). Code: `services/zoe-data/memory_value_registry.py`;
table `memory_value_registry` (alembic `0080`); tests `services/zoe-data/tests/test_memory_value_registry.py` (ci_safe).

## Why

`value_correction` remembers a correction in process (`_REMEMBERED`: 6 h, 16 per member, lost on restart) so that an idle pass mining
an EARLIER transcript does not file the corrected-away value again. This is the durable version, with no words in it. It is shadow
only: the in-process registry keeps deciding until a later step flips it.

## What a row holds

| column | holds |
|---|---|
| `subject_ref` | `self` or a `people.id`. Never a name or a label: a subject that does not resolve to ONE person is not recorded |
| `attribute` | closed vocabulary, number-valued only: `event_date origin_date birthday age amount measure count time_of_day schedule` (CHECKed) |
| `role` | `superseded` (corrected away) or `asserted` (corrected to) |
| `value_norm` | canonical JSON of numbers and enum codes in the value shapes of `memory_claims` (PR #2477): `{"v":8.0}` (age), `{"cur":"AUD","v":"13500"}` (amount: a decimal numeral string), `{"v":3}` (count), `{"d":19,"m":12}`; `normalise` refuses anything else |
| `said_at`, `expires_at`, `created_at` | epoch seconds; rows expire 7 days after `said_at` (swept on the next record) |
| `by_claim`, `source_turn_id`, `row_ids` | ids only: the claim, the turn's content id (`people_graph.content_turn_id`), the memory rows involved |

The read: `is_superseded(user, subject_ref, attribute, value, said_at)`. Only entries said AFTER `said_at` count. The newest assertion
of a value cancels an older `superseded` of it (A -> B -> A keeps A). For `age` and `birthday` (one value per person) a newer
assertion of ANOTHER value also makes this one stale.

## What shadow records today

`value_correction.apply` records beside `_remember` only what it can normalise without words: an AGE of a plain name that resolves
to exactly one live person of the member by an EXACT folded name match (no prefix or token match, no relation, no label, no lazy
possessive). Everything else is counted (`COUNTS`), not recorded. The idle pass compares its own verdict with the registry's for
every mined fact and logs one counts-only line.

## Forget reach

Erased in every flag mode (rows written while the flag was on must still go after it is turned off):

| path | eraser | call site |
|---|---|---|
| member erase | `forget_user` (fail closed) | `MemoryService.delete_user` |
| "forget <name>" | `erase_entity` (by person id, live or soft-deleted) | forget cascade step `value_registry` (durable retry) |
| "forget what I told you about <topic>" | `erase_topic` (tied rows, or a person a topic word names) | `forget_topic.forget` |
| "forget that" | `erase_for_turn` (the turn's content id, tied rows) | `intent_router` `memory_forget_last` |
| any physical row erase | `erase_for_rows` (fail closed, before the rows go) | `MemoryService.erase_rows` |

A record racing a forget loses: it writes under the member's erase gate and re-checks after its commit that the person still exists.
The inventory entry is in `forget_reach.py`; the topic / turn / rows call sites are pinned by `test_memory_value_registry.py`.

## Log lines (counts and fixed codes only: no user id, subject or value)

- `MEMORY_SUPERSEDED_RECORDED attribute=<a> superseded=<n> asserted=<0|1> new=<n>` / `MEMORY_SUPERSEDED_REFUSED attribute=<a>`
- `MEMORY_SUPERSEDED_SHADOW lane=idle verdict=agree|old_only|registry_only old=<0|1> registry=<0|1> entries=<n>`
- `MEMORY_SUPERSEDED_ERASED path=member|entity|topic|turn|rows rows=<n>`

`old_only` is expected for every correction shape v1 does not record (dates, amounts, labels, relations).

## Owner steps

None to land it: the flag is off and nothing reads the table. To shadow it: `ZOE_MEMORY_SUPERSEDED_REGISTRY=shadow` in the zoe-data
environment after `alembic upgrade head` has created the table. `on` is reserved and currently behaves as `shadow`.

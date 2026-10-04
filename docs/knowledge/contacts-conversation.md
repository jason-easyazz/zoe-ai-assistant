---
type: reference
title: Contacts conversation classes (2026-10-04)
description: The four ways the contacts intents failed to behave like a human assistant (non-name searches, relation phrases saved as names, one-at-a-time offers, robotic and duplicated lookups), where each lives in the live path, what is a pure fix versus flag-dark, and how to turn it on and check it.
tags: [contacts, people, intents, telegram, voice, flags, zoe-data]
timestamp: 2026-10-04T15:00:00Z
---

# Contacts conversation classes

Observed 2026-10-04 on the Telegram lane. The owner: "it doesn't work like a human assistant."
Companion to [contacts-people-memory.md](contacts-people-memory.md) (the contacts-from-known-people loop).
Helpers: `services/zoe-data/contacts_conversation.py` (stdlib only). Tests:
`services/zoe-data/tests/test_contacts_conversation.py`, `tests/unit/test_contacts_dedupe.py`.

## The live path

Telegram or panel text -> `POST /api/chat` -> `fast_tiers.resolve` (Tier-0 reads, then the two-stage
router and the intent gate, `fast_tiers.py:310`) -> `intent_router.detect_and_extract_intent`
(`intent_router.py`, off-panel offer replies first, then `detect_intent`) -> `execute_intent` ->
the direct handlers `_execute_people_create_direct` / `_execute_people_search_direct`. The Flue
brain reaches the SAME handlers through the `people` tool -> `POST /api/system/intent-dispatch`
(`routers/system.py`, `_DISPATCHABLE_INTENTS`), and voice reaches them through the same intents. So
every fix lives in the handler, not only in the regex that happens to catch the phrase.
Pending contact offers live in `pending_suggestions` rows (`person_create`); the brain voices them
from two prompt surfaces (`zoe_flue_client._pending_offer_block`, `routers/memories._fold_pending_contact_offers`)
and the reply is bound back to the rows by `intent_router._match_pending_offer_reply`.

## The classes

| # | Symptom | Cause | Fix | Flag |
|---|---|---|---|---|
| 1 | "Who is on my contacts" -> `No contacts found for "on my contacts"` | the brain's `people` tool (and the "who is" regex for "my X") hands the trailing words to the handler as a name | `classify_contacts_query` in the handler: scaffold words ("in/on/my/the/contacts") -> list-all; a clause or time phrase -> asks for a name; "my brother" -> relationship search. New list-all phrasings route to the empty-query `people_search`. | none (bug fix) |
| 1b | the list itself is a bare `Found: - ...` | formatter | "You have 7 contacts. Friends: A and B. Brother: C. And 2 more." (cap 12) | `ZOE_CONTACTS_CONVERSATIONAL` |
| 2 | "Save a contact for my brother Kyle" -> `Added My Brother Kyle as your friend` | the name regex kept the relation phrase; the brain tool defaults the relationship to `friend` | `split_relation_from_name` in BOTH the regex lane and `_execute_people_create_direct`; the phrase wins over an empty/generic relationship; a bare "my boss" asks for the name | none (bug fix) |
| 2b | reply wording | | "Added Kyle, your brother." (old: "Added Kyle as your brother to your contacts.") | `ZOE_CONTACTS_CONVERSATIONAL` |
| 3 | "add A? add B?" one at a time; "add his whole family" landed on the shopping list | the prompt surfaces ask per offer and a bare yes binds only the OLDEST offer | one enumerated question; `batch_reply_scope` makes yes / "all of them" / "add his whole family" accept the surfaced set, "just X" accept one and drop the rest, "no" drop the set. Execution fans out per row through `execute_suggestion`, failures are named. | `ZOE_CONTACT_OFFER_BATCH` (needs `ZOE_PERSON_SUGGEST_ENABLED`, `ZOE_SEAM_OFFER_INJECT`) |
| 3b | "add his whole family" -> shopping list item | implicit `list_add` swallowed a people phrase | the matcher defers people references (`_PEOPLE_REF_ADD_RE`) to the brain / offer matcher | none (bug fix) |
| 4a | lookup is a list, not a sentence | formatter | "Caitlin Farrell is your friend. You've told me Caitlin is allergic to ..." (top 2 facts: memory rows linked by `entity_id`, then the contact's notes and birthday; 2 s time box, failures just shorten the answer) | `ZOE_CONTACTS_CONVERSATIONAL` |
| 4b | the same person twice (`Caitlin` and `Caitlin Farrell`) | nothing merged a first-name-only record into the fuller one | lookup collapses them; a save of the stub says "you already have ..." and writes nothing; a save of the fuller name upgrades an UNAMBIGUOUS stub in place; several fuller records sharing a first name are asked about, never guessed | `ZOE_CONTACTS_CONVERSATIONAL` |

Same person means: same first name, one side first-name-only, relations equal or one side blank.
Two surnames are two people.

## Existing duplicates

`python3 scripts/maintenance/contacts_dedupe.py --dry-run` prints the likely-duplicate groups from the
live table (via the `zoe-database` container; `--json-file` for offline). It never writes. The output
names household contacts: keep it on the box.

## Turning it on

All three flags default OFF and are read per call. With them OFF, replies are byte-identical to before
except for the bug fixes in the table above. Enable `ZOE_CONTACTS_CONVERSATIONAL` first (reply shape only,
no new state), then `ZOE_CONTACT_OFFER_BATCH`. Samantha bar S13 and S14 pass with the flags off; S15 needs
`ZOE_CONTACTS_CONVERSATIONAL`; S16 SKIPs until the offer flags are on (see [samantha-bar.md](samantha-bar.md)).

## Safety rules added after review (PR #1854)

* **Relation lookups** ("who is my son") match `people.relationship` only, whole value, any spelling of
  the relation (mother = mum = mom). Never the name field, never a substring (grandson, sister-in-law).
  The reply echoes the user's own word.
* **A yes/no binds an offer set only when that set was ASKED.** "Surfaced" only means injected into a prompt.
  The prompt builders record the question and the offer ids (`contacts_conversation.record_asked`,
  in-process); the matcher requires the user's previous assistant message to END with that question
  (`asked_in_message`; a question asked after ours means the yes was for that one). Nothing recorded, nothing
  in history, or a restart: no match, the brain gets the reply. The accepted set is always a subset of the asked set.
* "yes, not X" accepts the rest and drops X; "no X" is left to the brain.
* **A first-name-only contact is renamed in place only when nothing can be lost**: specific equal relationship
  (NULL and the brain's default `friend` match any same-first-name person), no phone/email/notes/birthday,
  no linked memories. Otherwise Zoe asks "I already have a Dan saved. Is Dan Murphy the same person?"
  and acts on a yes/no only if that question ends her previous message (`people_same_person_reply`).
  A rename clears `is_partial` and refreshes the memory mirror. The offer-accept path (`execute_suggestion`,
  including batch) uses the same decision (`decide_same_person`).
* A stub next to two or more fuller people is never folded into one of them (lookup, save, dedupe report).
* "how many contacts do I have" answers with the count; a missing relationship is never printed as `(None)`.

## Known limits

* Offers are individual rows: "Rodrigo, Jessika and the three kids" enumerates the kids by name if each has
  a row; there is no household-group offer.
* "no, not Jessika" (a named refusal after a no) is left to the brain, not guessed.
* Facts on the lookup come from entity-linked memory rows; a fact stored unlinked (`person_pending`) is
  not shown until the idle link resolver (`ZOE_MEMORY_LINK_RESOLVER_ENABLED`) relinks it.
* The person relationship graph (`person_relationships`) is not read for the sentence yet.

## Contact-create commands in the fast tiers (PR #1863)

* `expert_dispatch._plan` files a regex-recognised `people_create` as kind `direct`, never `expert` (the
  memory-fact expert answered "Got it, I'll remember save a contact for…" and wrote no row). Chat defers it
  to its own intent lane; telegram/livekit execute it with the regex slots, as the acting user (no
  `family-admin` -> `guest` alias).
* Relation-first phrasings detect as `people_create` ("save my brother Percival as a contact", "add my brother
  Percival", lowercase too). Without a contact cue the name is at most two words and not a day/time word or a
  shopping/household noun (`_NOT_NAME_TIME`, `_NOT_NAME_ITEM`); it is a guard, not a lexicon.
* livekit (`ZOE_LIVEKIT_FAST_TIERS`, default off) cannot bind a follow-up "yes" (it neither persists the reply nor
  runs the intent lane's matchers), so `binds_followups=False` in its channel profile makes a `direct` create
  state the same-person outcome instead of queueing a question ("I already have a Dan saved, so I haven't added
  Dan Murphy. Ask me in chat to add …").
* Voice: a capitalised transcript "Add my brother Percival." now reaches `people_create` in `voice_tts`'s own
  confirm path; the prompt names the relation ("Add Percival, your brother, to your contacts?").

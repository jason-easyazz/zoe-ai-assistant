---
type: spec
title: QA round 7 memory fixes - a forget that holds in the same conversation, corrections that stick, the right person, a grandson is not a pet
description: Acceptance spec + root causes for the memory failures of QA round 7 (2026-11-09). "Forget the thing about X" erases the rows and the brain stops repeating it in the same chat; "actually the exam is on friday not thursday", "kais 21 not 22" and "ashas swimming is on wednesday not thursday" change the stored fact under the right name; Zoe's own facts about the speaker are the speaker's; grandchildren, nieces and nephews are never filed as pets; "dont forget to add X" makes no memory.
tags: [qa, memory, forget, corrections, child-vs-pet, spec]
timestamp: 2026-11-09T00:00:00Z
---

# QA round 7 memory fixes

## Acceptance — true when done

Synthetic names: Asha is a five-year-old who hates mushrooms, Kai is a boyfriend, Max is a four-year-old grandson, Priya is the speaker.
Each line is checkable by a typed QA chat.

1. After "forget the thing about asha and mushrooms" (also "the bit about my brother", "that thing about the shops", "all the stuff I
   said about my mum's heart") every stored copy of that fact is gone, and the very next turns in the same conversation - "what does
   asha hate eating", "what do you know about me" - do not bring it back. Asha's other facts (her age) stay. "forget about my day"
   and "dont forget the bit about the bins" are never a forget.
2. After "actualy my exam is on friday not thursday" (a misspelt "actually", "on" before the day), "when is my exam" says Friday, now
   and in a later conversation. After "also kais 21 not 22", Kai is 21 and "how old is kai" says 21; no person called "Kais" appears.
   After "actually ashas swimming is on wednesday not thursday" the stored fact says "Asha's swimming is on Wednesday", never
   "User's swimming".
3. "How old is my daughter" is never answered with the speaker's own age: what the speaker said about themself ("its priya ... night
   shift nurse", "im 36") is filed as the user's, naming the speaker, not as a contact called Priya beside the people they talk about.
4. "grandson max is 4 and our dog is rusty" never produces "User's pet is named Max" or "User's pet Max is at kindy". The row reads
   "User's grandson ...". The same for granddaughter, grandchild, niece, nephew. Rusty is still a pet.
5. "dont forget to add renew my rego" (or "... put milk on the list", "... remind me ...", "... schedule ...") writes no memory row.
   "dont forget that mum is allergic to nuts" still does.
6. Recall denials where the packet holds the answer (Dev's job, the cat, Ian, Max's age, Kai's age): covered by #2344 (below).

## Root causes

| item | cause | fix |
|---|---|---|
| 1a. the forget never ran | `forget_topic.parse` required a quantifier ("everything", "what I told you"). "forget the thing about X" matched nothing, so no intent ran, the brain said "noted" / "set that aside" (not a write claim the guard can catch) and the rows stayed in the packet. | The quantifier also accepts "the/that/those + thing/bit/part/stuff/story/details/info" (noun required, so a bare "forget about my day" is still a dismissal). |
| 1b. the brain repeated it anyway | The Flue sidecar keeps its own per-session history of the turns it answered: the owner's original sentence ("asha hates mushrooms") and the brain's replies are in it. #2355 redacted the quote in the forget confirmation, but nothing can erase the sidecar's copy of the earlier turns. | `forget_session.py`: a forget (topic, last-turn, entity) marks the member; `zoe_flue_client._endpoint` addresses the sidecar with `<session>~f<epoch>` from then on, so the next turn starts a clean sidecar history. The packet is read fresh every turn, and fast-path exchanges still ride `flue_fastpath_context`. |
| 2a. exam stayed Thursday | `value_correction` knew "actually" but not "actualy"; and "on friday" as the new value would have rewritten the row to "on on Friday". | Misspelt cues; a value's leading preposition is not part of the value. |
| 2b. "Kais: 21" | "kais 21 not 22" (no "is", no apostrophe) was no known shape, so nothing was corrected, and the person extractor read "kais" as a name. | New bare shape "<name> <N> not <M>" (both numbers, no stop-word subject); the compact "Name: N" row is an age row; a person write whose name is the correction's subject plus a stray s uses the subject. |
| 2c. Wednesday lost its subject | The digest guard `fact_subject.attribute` skips "User's ..." facts, and the digest dropped the possessor of "ashas swimming". | `fact_subject._possessive_owner`: a "User's <noun> ..." fact from a turn that says "<name>('s) <noun>" with no first person is re-attached to that name. `value_correction` reads "<name>s <noun> is X not Y" and only rewrites a row naming that owner. |
| 3. "Asha is 36" | The person extractor stored the speaker's own facts as a person called Priya ("Priya: night shift nurse") in the same People list as Asha: the packet could not tell the speaker from her people, and the 4B mixed the two ages. (The exact 36 row could not be read: QA reads are API-only; this is the attribution defect the packet showed.) | `person_extractor.apply_person_fact`: a name that is the speaker's own (`own_name`) is filed as "User ..." / "User (Priya): ...", with no person node. |
| 4. Max as a pet | `kin_pet_guard`'s child cues (`child_cue_words`) had no grandson/granddaughter/grandchild/niece/nephew, so "grandson max" never marked Max a child and the digest's "pet" typing stood. | The cues are in `lexicons_data/en.json`; the repair writes the user's own noun ("User's grandson Max"). `person_age.CHILD_WORDS` (the voice child gate) gains niece/nephew/grandchildren/grandkid. |
| 5. "Important note: to add ..." | `memory_extractor`'s "don't forget ..." template saved any capture. | A capture that opens on add / put / remind / set / schedule / book / create / write down is a list, calendar or reminder ask: no row. |
| 6. recall denials | Question turns got no packet unless a recall-shaped grammar matched (30 of 33 denials carried no packet). | Already fixed by open #2344 (`memory_gate.is_question_turn` + `packet_touches_question`): checked on its branch, the Dev / Kai / Max / brother questions all fetch and touch the packet. Not duplicated here. One gap found on that branch: `_touch_tokens` adds a plural's stem only above four letters, so "whats my cats name" / "the dogs name" never touch "cat" / "dog" (see open-problems). |

## Not in this change

A same-session forget after a zoe-data restart addresses the sidecar's old history again (the alias is process-local). "Forget the thing about Zara" erases memory rows about Zara, not the contact or its relationship edge (that is "forget everything about Zara"). The "I don'm" garble is #2344's. Safety variants, weather compound questions and guest-scope write claims are other branches.

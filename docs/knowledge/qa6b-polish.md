---
type: spec
title: QA round 6b polish - own name is not a contact, named places, corrections that stick, simple questions answered, forget that stays forgotten
description: Acceptance spec + root causes for the non-safety failures of QA round 6b (2026-11-09). Zoe never offers to add the speaker as a contact, answers the weather for the place named (one or two), applies spoken corrections (a day, an age with its half, a value given "for <name>") without inventing a field, answers a cooking or local question directly in the household's units, and does not hand a forgotten item back in the same conversation.
tags: [qa, contacts, weather, corrections, memory, forget, brain, spec]
timestamp: 2026-11-09T00:00:00Z
---

# QA round 6b polish

## Acceptance — true when done

Synthetic names: Walter is an older man chatting with Zoe, Margaret is his wife, Wren is a toddler, Anika has a board pitch. Each line
is checkable by a typed QA chat.

1. When Walter says "its walter" or "my name is Walter", Zoe never asks "Would you like me to add Walter as a contact?" - not once,
   not four times in a row, and not later in a new conversation. An offer for Walter that was already waiting disappears by itself.
   Margaret, a neighbour, anyone else is still offered normally.
2. "How's the weather in Bunbury today, we're driving down Saturday" gives Bunbury's weather, not the home town's. The same for
   Albany and Mandurah. "Weather in Fremantle today? and Mandurah" answers both places. A question that names no place still gets the
   home town.
3. "Actually the pitch is Wednesday not Thursday" - afterwards Zoe says Wednesday, in this conversation and the next.
4. "Wren is 3 and a half, not 3" - afterwards Zoe says three and a half.
5. "It's 76 for Margaret, not 78" - Margaret is 76 afterwards, and Zoe invents nothing new (no "your blood pressure is 78").
6. A food temperature comes back in Celsius for an Australian house (pork 63C, not 145 Fahrenheit), and "what temp do I keep curry
   at in the fridge" is answered with a temperature (5C or below), never a time.
7. "What's the best oven temp for roast potatoes" and "where can I get a decent pie in Northam" are answered straight away. Zoe does
   not ask for a location, a budget or a timeframe. A real shopping hunt ("find me the cheapest flight to Bali") still gets those
   questions.
8. After "forget what I said about the shops meltdown" and "Done", asking "what happened at the shops" in the same conversation does
   not bring the meltdown back. **(not yet:)** a second, differently-worded copy of the same memory (b15, "chest tightness" vs
   "chest feels tight") is only caught when it holds every word of the topic; see open-problems.md.

## Root causes

| item | cause | fix |
|---|---|---|
| A. offers to add the speaker | The detectors (`latent_intent_detector` LLM + regex) name whoever the turn mentions, and the user introduces themself in the same words. The only self-check lived in the backfill and compared the name with the account id/display name, so a handle or demo account never matched; the detectors and `store_suggestions` had no self-check. Rows already stored were re-offered forever and "no" never reached them. | `own_name.py`: the speaker's own names (account/preferred name, id tokens, names they introduced themself with, kept in `user_preferences.introduced_names`). `store_suggestions` refuses a `person_create` for them; `surface_pending_contacts_for_prompt` closes one already stored; `list_pending_contacts` hides it; `detect_and_store` notes the introduction first. |
| B. weather ignores a place | #2351's `weather_place` regex was anchored to the END of the message, so any clause after the place ("today, we're driving down Saturday", "this weekend, Jonas and I are going", "today? and Mandurah") matched nothing and the home town won. A second place was never read. | `weather_speech.weather_places`: the place is the words after in/for/at up to a stop word or punctuation; "and <place>" / "what about <place>" adds a second. Both planners (`expert_dispatch._plan`, `detect_intent`) carry `location` + `also_locations`; `execute_intent` answers each. |
| C1. Wednesday | `value_correction` read only a name or "my <relation>" as the subject; "the pitch" matched nothing. | Subjects may be "the/my/our <noun>"; the row is found by the noun. |
| C2. 3 and a half | Values were whole numbers only. | Half-ages ("3 and a half", "three and a half", "3.5") parse and render in the row's own spelling. |
| C3. Margaret 76 not 78 | The "its 76 for margaret, not 78" shape was not read, so nothing was corrected, and the turn digest then filed the stray number under an invented attribute ("User's blood pressure is 78"). | The "its N for <name>, not M" shape is read; on a correction turn the digest drops a fact that holds the corrected value but names a field the turn never used (`invented_on_correction_turn`). |
| D1. Fahrenheit, time for temperature | The household-units line says Celsius but the 4B recalls the US figure; nothing gave it the figures. | `KITCHEN_DOCTRINE` (code-held, in `doctrines.ts`): Celsius figures for cooking and storage, and "a question asking for a temperature gets a temperature". |
| D2. research brief on a simple question | "what's the best ..." and "where can I get ..." are research frames; the brief (location, budget, timeframe) was asked even when the answer was a cooking temperature or a place already named. | `research_evidence.is_direct_question`: how-to / cooking questions and a nearby-place lookup with a place and no buying word go to the brain. |
| E. forget, same session | The forget confirmation QUOTES the erased row ("Done - I forgot: \"...\""). It is saved as a fast-path exchange, and `flue_fastpath_context` re-sends fast-path exchanges to the brain on the next turns of the same conversation - handing the item straight back. Cross-session never saw it (the block is same-session). | `flue_fastpath_context.redact_forget_quote`: the brain sees "Done - I forgot that." |

## Not in this change

Safety items (poisoning, emergency numbers, red-flag symptoms, blood sugar), recall denial and stubs, the "I don'm" garble, false
"didn't manage to save", child-vs-pet and AI honesty belong to other branches.

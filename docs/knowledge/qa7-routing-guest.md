---
type: spec
title: QA round 7 routing and guest fixes - a routine is not the clock, a place gets its weather, goodnight is a goodbye, a stranger is told the truth
description: Acceptance spec + root causes for the routing and guest failures of QA round 7 (2026-11-09). A question about a person's routine or a named event is the person's own life (never the clock, never today's agenda); a weather question naming a place, and "and in Albany" after a weather answer, get the weather; "dont forget to add X" is the add command; a decision question is not a complaint; "gnite" is a goodbye; a feeling's turn never gets a question three turns running or a stock "I'm here to listen"; a stranger asking to be remembered or forgotten is told honestly.
tags: [qa, routing, weather, lists, goodnight, guests, warmth, spec]
timestamp: 2026-11-09T00:00:00Z
---

# QA round 7 routing and guest fixes

## Acceptance — true when done

Synthetic names: Greg is an older man, Max his grandson at kindy, Mia a student with an exam, Quinn a stranger at the panel. Each line
is checkable by a typed QA chat and by `tests/test_qa7_routing_guest.py`.

1. "Which day is Max at kindy" and "when is Mum at work" never get the time of day. "What exam do I have and when" never gets "You've got
   nothing on today": with no day named it is a lookup of Mia's own plans (memory beside the calendar). "What time is it" is still the
   clock, and "what exam do I have tomorrow" still reads tomorrow's calendar.
2. "Temp in Albany" and "weather in Broome today" answer with data for that place. After a weather answer, "and in Albany", "and Broome" or
   "what about Albany?" is the same question for the new place (same day word). Without an earlier weather answer, "and Broome" is not
   touched, and "and tomorrow", "and you", "and in the kitchen" are never read as places.
3. "Weather in Bunbury and how hot for a pork roast" and "...and the temp for roast potatoes" are answered whole by the brain, not the
   weather half only. "Weather in Perth and will it rain tomorrow" or "...and Mandurah" are still the weather expert's.
4. "Dont forget to add screws" and "dont forget to add print my study notes" ask which list (shopping or personal); Zoe never says she
   cannot add things and never offers a reminder instead. "Dont forget to add milk" goes straight on the shopping list.
5. "Should I get a new ute or fix the old one" is answered as a decision question, never logged as feedback ("I've logged that as
   feedback"). A real complaint ("fix the lights", "you got that wrong") is still a report.
6. "gnite", "g'night", "nite nite", "gn", "nini", "nighty" get "Night. Sleep well." - never "do you want me to do something with the light".
   "Good night" keeps its ordinary path.
7. A person who is overwhelmed is never asked a question on three replies running: when the last reply ended on a question and the person
   asked nothing, this reply ends on a statement (a "yeah" straight after a feeling counts as part of that moment). A reply that is only a
   stock "I'm here to listen to whatever is on your mind" says one true thing instead ("That sounds like a lot to carry..."); with other
   words in the reply the stock line is dropped. A reply that is only a question is kept whole.
8. Answered with a few unrelated words (a colour, an animal, an appliance) Zoe says she missed it and asks again; she does not remark on them.
9. A stranger who says "hi, I'm Quinn, remember me" is told the truth: nothing is kept about guests, and if someone in the house asks Zoe
   to learn the voice she will know them next time. She never says "I'll remember". "Forget me" is answered "I don't remember guests, so
   there's nothing of yours to forget." "Do you know who's talking" is answered "I don't know you yet." A signed-in member is never given
   these lines, and a recognised named guest (flag on) keeps its own flow.
10. The spliced-contraction garble ("I don'm", "I don'd", "I don'll") is PR #2344's `contraction_repair`; checked against this round's
    transcripts, all 5 of the 5 garbled replies come out right (`I don'm sorry` -> `I'm sorry`). Not duplicated here.

## Root causes

| item | cause | fix |
|---|---|---|
| b9 "which day is max at kindy" -> clock | The head claims `time` on the words "which day"; `memory_gate.EVENT_TIME_QUESTION_PATTERNS` knew a possessive plan ("when is jonah's test") but not a named person at a regular place, so the time expert's unconditional "personal question -> brain" guard did not fire. | New rows `their_routine` (when / which day / what time + is/does + a name + at/in/go + kindy, school, work, swimming ...) and `named_event` ("what <event> do I have"). |
| c14 "what exam do i have and when" -> "nothing on today" | The calendar planner's show-cue ("do I have") read the calendar with an empty qualifier = today. | `expert_dispatch._plan`: an event-time question with no day named returns None (the brain; the recall floor now claims both new kinds with no flag, so the stored rows are in front of it). |
| c7 / g8 weather stubs | "temp in albany": the stage-2 router picked `people` (a town reads like a name) and the brain answered with a promise and no tool call. "and broome": no weather word at all, the head said low confidence, and the fast-path answer is not in the brain's history. | `weather_followup` + `fast_tiers._weather_place_tier`: a short weather question naming a place, or a bare place follow-up within 10 minutes of a weather answer in the same conversation, goes straight to the weather expert before the router. |
| a17 / b17 second half dropped | The weather expert answers its half and says nothing about the other. | `weather_speech.has_other_question`: a second non-weather question (cooking, food words) makes the weather plan return None, so the brain answers both. |
| b4 / c4 planning talk | The natural-language shopping pattern read "dont forget to add screws" as the ITEM "to add screws"; the head (bare words: smart_home, notes) vetoed it; the brain improvised three different answers. | `intent_router`: "dont forget to / remember to / make sure to add|put X" is the add command with the lead-in stripped; no list named + a task, a verb-led item or hardware -> ask which list. `fast_tiers.intent_gate` allows that explicit shape (the add verb is not a guess). |
| b15 "should i get a new ute or fix the old one" -> feedback | `_USER_ISSUE_RE` matched `fix the` anywhere. | A decision question (should/shall I|we ... or ...) is never a user-issue report. |
| c13 "gnite" -> light | No class in the head, no farewell word in `clean_goodbye`; the brain guessed a device. | `clean_goodbye.is_goodnight_short` (and the farewell vocabulary) + a code reply in `fast_tiers._qa7_conversation_tier`. |
| g4 question on 3 of 3 turns | The cross-turn rule lived only in the prompt; the 4B ignored it. `reply_polish` already tracks "last reply ended on a question" per conversation but capped only generic closers. | `reply_polish.feeling_turn_policy`: on a feeling's turn (and the two short turns after one) with the last reply ended on a question and nothing asked, ANY trailing question is dropped. New emotional cues: "feel horrible", "snapped at my", "dont know how people do it". |
| g1 "I'm here to listen" | Stock line the doctrine bans; the 4B says it anyway. | `reply_polish.is_stock_line`: dropped when anything else is said; replaced by one true statement when it is the whole reply. A sentence with a second clause is never a stock line. |
| g12 "purple monkey dishwasher" | A nonsense answer was commented on; no deterministic test of "makes sense as an answer" exists. | One sentence in `WARMTH_DOCTRINE` ("Unrelated words (a colour, an animal) are not an answer"). Prompt-only: the residual risk is stated below. |
| g18 remember / forget a stranger | The memory expert read "remember me" as a fact to store ("I'll remember hi. you're quinn"); "forget me" fell to the stock line. | `guest_asks`: three whole-utterance shapes, only for an unidentified speaker who is not a recognised named guest, answered in code with what is true. |

## Not in this change

- Safety variants (swallowed objects, "tight feeling in my chest", blurred eyes) are `fix/qa7-safety-variants`; recall and correction
  failures are `fix/qa7-memory`.
- g12 is prompt-only: a deterministic "this does not answer my question" test needs a model; if it recurs, the cheap lever is a
  one-turn clarify state in the seam.
- A compound weather question goes to the brain, which can still answer with a promise instead of a tool call (the narration filter that
  replaces an unkept promise is a separate, default-off flag).
- Voice path: `fast_tiers.py`, `reply_polish.py`, `clean_goodbye.py`, `expert_dispatch.py` and the doctrine are on the voice path;
  replay-gated by the voice lander before deploy (`voice-pending` line on the PR). Nothing was probed or armed here.

## Verification

`services/zoe-data/tests/test_qa7_routing_guest.py` (ci_safe; every fix has a neighbour that must not change), the unchanged
`test_reply_polish.py`, `test_clean_goodbye.py`, `test_emotional_turn_guard.py`, `test_intent_router_gate.py`, and
`labs/flue-zoe-brain-2x/test/{warmth_doctrine,harness_overlay,persona_layer}.test.ts` (golden hash re-pinned).

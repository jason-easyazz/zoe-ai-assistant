---
type: Reference
title: Clause-first voice prompt (2026-10-10) - REJECT as written
description: Paired A/B of a static "open with a short complete clause" instruction (ZOE_VOICE_CLAUSE_FIRST_PROMPT, default OFF) against the mechanical ZOE_FIRST_SOUND_CLAUSE cut, from the Little Gemma paper section 4.3. The prompt shortens the first unit but buys no first-audio time and makes the 4B brain skip tool calls and answer confidently without them.
tags: [voice, latency, first-sound, prompt, clause, little-gemma, flue, measurement, quality]
timestamp: 2026-10-10T10:30:00+08:00
---

# Clause-first voice prompt (2026-10-10)

Idea (Little Gemma paper section 4.3, first audio 1.21 to 0.82 s on their E2B): make the model its own clause splitter, so the reply opens with a short complete speakable clause. Built as `ZOE_VOICE_CLAUSE_FIRST_PROMPT` (default OFF) in `labs/flue-zoe-brain-2x/src/agents/zoe.ts`: one static paragraph, `CLAUSE_FIRST_DOCTRINE`, placed straight after `VOICE_DELIVERY_DOCTRINE`. Read once at module load, so the system prompt stays byte-identical turn to turn (prefix cache safe). Flag OFF is byte-identical to before (persona_layer golden still passes). Related: [first-sound-latency-2026-10-09.md](first-sound-latency-2026-10-09.md).

## Verdict: REJECT as written (do not enable); keep `ZOE_FIRST_SOUND_CLAUSE` as the lever
Do not stack. The prompt adds no first-audio gain over the mechanical cut and it damages tool use.

## Method
`scripts/perf/measure_first_sound.py` (now with `--drain --keep-text --offset --shapes`), live brain, 10 memory / 10 tool / 10 chat prompts, 4 arms per prompt, order rotated, fresh session per turn, warmup discarded. The prompt arm is a parallel-port sidecar built from this branch (:3580, flag ON, isolated DB); the other arms use the live sidecar (:3579, identical code, flag absent). Times are seconds from the clip POST, medians. Caveats: other agents loaded the box and restarted the brain once during chunk 2 (a guard recorded it), temperature 0.7, so the spread is wide (IQR 2-6 s) and single-digit-second outliers hit every arm. Tool-call counts and reply text are the robust signal here, not sub-0.3 s deltas.

| median | memory off / prompt / clause / both | tool | chat |
|---|---|---|---|
| first audible s | 5.25 / 3.81 / 3.17 / 2.55 | 2.65 / 5.64 / 1.81 / 2.19 | 2.81 / 2.24 / 2.02 / 2.48 |
| first unit words | 12 / 8.5 / 12 / 8 | 11 / 8.5 / 11.5 / 7.5 | 15 / 9.5 / 9 / 8.5 |
| tool turns (of 10) | 3 / 3 / 2 / 0 | **9 / 4 / 8 / 3** | 0 |
| reply words | 16.5 / 14.5 / 13 / 10.5 | 11.5 / 19 / 11.5 / 16.5 | 29.5 / 31 / 28 / 27.5 |

Paired change vs off (all 30 prompts): prompt +0.13 s (faster on 11/30), CLAUSE -0.51 s (19/30), both -0.02 s (16/30). The prompt does what it says to the text (first unit 12 to 9 words, 70 to 50 characters) but the first sound does not move: the live loop already cuts at a sentence or 60-character clause, and the wait is the brain and Kokoro, not the unit length. The mechanical CLAUSE cut helped without touching what the model says.

## Quality: the prompt makes the model skip tools and answer anyway
On tool-shaped prompts (calendar, reminders, lists) the model called a tool on 9/10 turns with the flag off and 4/10 with the prompt (3/10 prompt+CLAUSE; CLAUSE alone 8/10). The opening clause becomes a narrated promise ("I need to check your reminders first.") and the model then answers from nowhere. Examples from the run: reminders asked, no tool called, reply "None of your reminders are coming up soon" where the flag-off turn called `list_reminders` and found some; "I don't have access to your schedule right now" for a calendar question; "you don't have anything scheduled for the weekend" with no calendar read. That is the paper's own warning (style pressure makes a small model confidently wrong), reproduced on Zoe's brain and in the worst place: said-vs-did. A first wording ("start with four to eight words") also made replies curt (family question lost the parent names); the shipped wording adds "keep the same detail, warmth and length", which fixed length (reply words are level) but not tool skipping.

Not done: the `fact` / `care` quality shapes (ground-truth regexes and six emotional prompts are in the script, `--shapes fact,care`) and `voice_regression_probe.py` against the flag-on sidecar were not run - the coordinator closed the lock for a 12B night-brain window and the result above already fails the bar. Samantha bar / day-sim cannot test this prompt without repointing live zoe-data at a second sidecar (`ZOE_FLUE_BRAIN_URL`), a live-flag change not made here.

## Negative control
The structural tests were broken once to go red (removing "never guess, skip a tool" fails `test_the_doctrine_is_a_static_constant...`). The harness itself sees a difference (tool turns 9 to 4, first unit 11 to 8.5 words), and the CLAUSE-only arm moves only timing, not tool use, so the effect is the prompt. A flag-off build of the same sidecar on :3580 (no-op control, tool shape, n=10 paired with the live sidecar) gives tool turns 8/10 vs 10/10, first unit 13.5 vs 10.5 words, first audible 2.92 vs 2.56 s: the prompt effect (tools 4/10, first unit 8.5 words) is gone when only the flag is off, and the residual shows the run-to-run noise floor (about 0.4 s, 2 tool turns). Treat the prompt-arm tool-skip count (7/20 across both prompt arms vs 17/20 off+clause) as strong but n=10 per cell.

## If someone retries
Candidate fixes to measure, not assumed: tell the model to call the tool BEFORE any opening ("tools first, then open with the result"); apply only to chat-class turns (the router already knows the class); or a fine-tune for pause placement as the paper does. Re-measure with `--shapes memory,tool,chat,fact,care --drain --keep-text` and read tool counts first.

## Operating
Default OFF; nothing enabled. To try: set `ZOE_VOICE_CLAUSE_FIRST_PROMPT=1` in `labs/flue-zoe-brain-2x/.env` and restart `flue-zoe-brain-2x` (rollback: unset and restart). Merging this branch changes `labs/flue-zoe-brain-2x/`, which deploy.yml rebuilds and restarts on merge, so the voice-path replay gate applies. Tests: `labs/flue-zoe-brain-2x/test/clause_first_prompt.test.ts`, `tests/unit/test_clause_first_prompt_flag.py`, `tests/unit/test_measure_first_sound.py`.

---
type: Reference
title: Converse, do not mirror (ZOE_MIRROR_GUARD + the manner block's new text) - why the 4B says "It sounds like", what changed, how it is measured
description: Diagnosis of the reflective-listening tic from the prompts the 4B actually gets, the new manner-block text (substance first, one clause of care, at most one question), the deterministic mirror guard (off|shadow|on, default shadow), and the converse A/B (mirror rate + conversation score, dev / held-out) that must print KEEP before the flags go on.
tags: [samantha, person-likeness, manner-block, mirror-guard, ab-test, flue, zoe-data, flag-dark]
timestamp: 2026-10-12T00:00:00Z
---

# Converse, do not mirror

**Status 2026-10-12: built; the prompt text is a PROVISIONAL candidate; NOT yet measured on the live 4B** (the harness refuses to run while a
voice landing is in flight, and the landing queue held the box all evening - see "Measured"). Do not flip `ZOE_MANNER_BLOCK` until
`scripts/perf/converse_ab.py --phase report ... --ship V0` prints `VERDICT ... KEEP` on the held-out half. The guard (`ZOE_MIRROR_GUARD`) is safe
in its default `shadow`.

## The failure (owner, 2026-10-12)

"We really need to teach you how to talk to people ... it feels more natural rather than you just regurgitating what I'm doing." In one
evening 6 of 14 conversational replies opened "It sounds like ..." / "That sounds like ..."; most ended on a generic question; a note that a
word was mispronounced was answered with an invented apology ("I'm sorry I said something that upset you"); a meta-complaint got
"I hear what you're saying, and I really appreciate you pointing that out ..."; "what can you tell me about it?" after a factual answer got the
previous answer back verbatim. Even the owner started handing the formula back to her.

## Diagnosis - what the prompt tells the 4B to be

The live system prompt is ~2.7k tokens (`FLUE_CONTEXT_BUDGET system=2721` in the app log). What it says about HOW to converse, in full:

| Source | The words | Effect |
|---|---|---|
| `soul.ts` `ACKNOWLEDGE` | "When someone shares something personal or emotional, **acknowledge it first** - before the task. When someone seems off, notice it." | the opening move is fixed as an acknowledgement |
| `soul.ts` `HELP_IS` | "Sometimes it means **listening** ... noticing what's actually being said underneath" | listening = restating |
| `zoe.ts` `VOICE_DELIVERY_DOCTRINE` | "If the message carries emotional weight, **acknowledge that first**, in a few words." | the same, in the delivery rules |
| `zoe.ts` `EMOTIONAL_CAPTURE_/RECALL_DOCTRINE` | "give your **warm** human reply", "keep responding **warmly**", "then reply **warmly**" | "warm" three more times, never "useful" |
| continuity block (`_CONTINUITY_*_ASK`) | "briefly and warmly **ask how that is going** before you answer" / "gently **check in**" | ends the reply on a check-in question |
| the manner block, as shipped in #1977 (flag-dark) | "**first say back what you heard** in a few words of your own, then stop or ask ONE question" | literally the mirror instruction (it came from the clinical reflective-listening record, `person-likeness-2026-10-09.md` WRM1) |

Nowhere does the prompt say what a reply is FOR: no "give something", no "have a take", no "do not restate", no "feedback about you is
feedback". A 4B instruction-tuned model given "warm" + "acknowledge first" + "listen" has exactly one empathy move, restating the feeling
("It sounds like you're ..."), and it reaches for it every time. The research record's "reflect before you fix" is right for a counsellor
and wrong for a companion that is supposed to talk back; MITI itself warns that models over-produce reflections.
The feedback-about-Zoe cases fail for the same reason: with no rule, "acknowledge" becomes a stock apology plus an invented cause.
The repeated answer is a separate defect (the follow-up turn is recall-shaped; the memory PR owns it); the new text adds "never repeat your
last answer; if they ask for more, add something new" and the A/B scores it.

## What changed (zoe-data side only; no sidecar build, no TS)

1. **The manner block's text** (`lexicons_data/{en,es}.json` key `"manner"`, still flag-dark `ZOE_MANNER_BLOCK=on`, adults only, <= 160
   tokens): from "first say back what you heard" to a procedure of counted behaviours - start with something of your own (a fact, a real opinion,
   a related thought, a next step); care is one short clause, not a paraphrase; at most one question and only if the answer changes what you say
   next, otherwise end on a statement; criticism of you is feedback about you (name the exact thing, say what you will do); never repeat your last
   answer; never flatter. It comes after the soul in the system prompt, so it overrides "acknowledge first" by being the later, more specific rule.
   The old plan-flaw / goodnight lines are gone from it: the flaw is found by code (`risk_cues.py`) and the goodbye is cleaned by code (`clean_goodbye.py`).
2. **The mirror guard** (`mirror_guard.py`, `ZOE_MIRROR_GUARD=off|shadow|on`, default `shadow`). A stream filter after the narration strip, before
   the clean-goodbye filter. If the reply's FIRST sentence opens with a mirror formula (after a filler like "oh" / "wow") and SOMETHING OF SUBSTANCE
   follows - a sentence that is neither another mirror nor a generic closer - that sentence is dropped. Never trimmed: a mirror with nothing after
   it, a mirror followed only by a generic question ("How are you holding up?"; trimming would leave something worse), a positive reaction
   ("sounds like a great plan"), a mirror that is not the first sentence, a very long sentence. A mirror that carries its own substance after a
   colon / dash / contrast word ("I hear you: say it slower.") keeps the substance. **No second brain call**: regenerating with a nudge costs a full
   time-to-first-token again (0.9 s warm, 6.6 s cold in the 2026-10-12 voice log), unacceptable on voice; the trim is free. Early release: a prefix that cannot
   grow into a formula is released on its first delta (no first-sound cost); one that might is held for a word or two. `shadow` is byte-identical and
   logs `MIRROR_GUARD mode=shadow lang=en opener=1 would_trim=1` (labels only, never reply text); `off` wraps nothing.
3. **The words are data** (`"mirror"` in `lexicons_data/{en,es}.json`: fillers, openers, contrast, intensifiers, positive_exempt, generic_closers).
   A language with no entry gets NO guard (inert; never guessed from English). Spanish was written by the author, not reviewed by a native speaker.

Not touched: the soul / voice doctrine (sidecar TS; changing them is a build + deploy + voice-gate event), the continuity block, the recall packet.

## Measured

`scripts/perf/converse_ab.py` (sibling of `manner_block_ab.py`: its rig, locks, tombstones, fingerprints, plan digests; its own ledger). Invented
corpus, six kinds (personal disclosure, open statement, feedback about Zoe, complaint about how she talks, "tell me more" follow-up, factual
control), DEV half and frozen HELD-OUT half (`SPLIT_DIGEST`, written before any run). Deterministic scorers only (a 4B judge is not validated): mirror-open
rate, generic closer, question count, substance (novel content words), length, boilerplate, invented premise, topical (feedback), repeat / new
information (follow-up); each sub-metric is reported beside the composite. POST-GUARD figures apply the real trim to the saved raw replies.
KEEP: the shipped arm's mirror-open rate separates DOWN from `none`, its conversation pass rate separates UP, no kind separates DOWN, the controls
are answered as often (Newcombe-Wilson 95 %).

**Result: pending.** On 2026-10-12 the harness could not run: `preflight` refuses while a landing runs, and a queue of voice-PR landings
(10+ PRs) held the box from 19:00; two hours of waiting produced no gap. A detached waiter retries the DEV run
(`~/.cache/zoe/converse_ab/dev.log`, rows in `~/.cache/zoe/converse_ab/conv-1.jsonl`). Resume:

```
cd <this worktree>; ln -s /home/zoe/assistant/labs/flue-zoe-brain-2x/node_modules labs/flue-zoe-brain-2x/node_modules   # + cp -r the deployed dist
ZOE_PERF=1 flock -s -w 3600 /tmp/zoe-brain-window.lock flock -w 3600 /tmp/zoe-voice-harness.lock \
  /home/zoe/.zoe/venvs/zoe-data-py312/bin/python scripts/perf/converse_ab.py --phase dev --arms none,LEGACY,V0,C2,C3 --run-id conv-1
python3 scripts/perf/converse_ab.py --phase report --split dev --arms none,LEGACY,V0,C2,C3 --run-id conv-1       # choose on DEV only
# if not V0: move the chosen CANDIDATES text into lexicons_data/en.json (+ es.json), update the sha256 goldens in test_manner_block.py
... --phase heldout --arms none,LEGACY,V0 --run-id conv-1        # the verdict, once
python3 scripts/perf/converse_ab.py --phase report --split heldout --arms none,LEGACY,V0 --ship V0 --run-id conv-1
```

(The lock order is window-first on purpose: the landing scripts take the brain-window lock and THEN the harness lock, so harness-first deadlocks
against a landing - observed 2026-10-12.) Never 02:30-04:30.

## Owner flag lines (only after a printed KEEP on the held-out half)

```
ZOE_MANNER_BLOCK=on      # adults only; the new text
ZOE_MIRROR_GUARD=on      # the deterministic backstop (default shadow already logs what it would do)
```
Review `MIRROR_GUARD ... would_trim=` counts in `~/.zoe-logs/zoe-data.app.log` for a day of `shadow` first: that is the live mirror rate of the
current prompt.

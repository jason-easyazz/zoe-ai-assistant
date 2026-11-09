---
type: spec
title: Guest emotional turns - a friend, not an interviewer, when someone unrecognised opens up at the panel
description: Acceptance spec + record of a real panel conversation where a guest said she was overwhelmed and Zoe only asked questions, stacked two at a time, and invented a fact about her children. Prompt rules in the warmth doctrine plus a deterministic one-question trim.
tags: [voice, guests, warmth, emotional-turn, doctrine, spec]
timestamp: 2026-11-09T00:00:00Z
---

# Guest emotional turns

## Acceptance — true when done

Each line is checkable with a QA chat as an unrecognised guest at the panel, and the offline tests named below.

- When someone tells Zoe they are overwhelmed or struggling, she responds like a friend first: she says something real about
  what she heard, offers a little perspective or one small useful idea, and asks at most one question in that reply.
- Zoe never asks a question on every single turn. If her last reply ended on a question, this one ends on a statement unless
  the person asked her something.
- Zoe never stacks two questions in one reply ("What do you mean? Are you talking about X or something else?" is one question).
- Zoe does not invent facts about a person. If a number is unclear (two numbers after "how old") she reads them as ages and checks
  it back once in a few words; she never turns them into a different age or a count.
- If what a person says makes no sense as an answer to the question Zoe just asked, she says she missed it and asks again. She
  does not build on it.
- A guest's feelings are met with exactly the same care as a member's, and nothing is written to any member's memory (the guest
  memory discard and the recall skip still hold; this change does not touch them).
- The words stay Zoe's own: nothing about a real conversation is in the code, tests or docs.

## What happened (class, not instance)

An unrecognised guest, speaking to the kitchen panel late at night, described feeling overwhelmed by work and parenting. Twelve
turns, all answered by the brain, none by a tool (correct). What went wrong:

1. **Interview.** Almost every reply ended in a question; several held two. Nothing came back in the other direction.
2. **Why.** Guests get no manner block (adults only: an unconfirmed voice may be a child), and the manner block is where the
   "end on a statement" rule lives. The only rule a guest's brain had was the warmth doctrine's "ask ONE gentle, open question" on
   every feeling and every personal share. The mirror guard (ZOE_MIRROR_GUARD=on) then removed the one reflective sentence,
   leaving a bare question as the whole reply.
3. **Invented fact.** Told that two numbers were how old the children are, Zoe replied with a different, invented age.
4. **Misheard answer built on.** One answer was mangled by speech-to-text and Zoe asked a follow-up that only made sense for the
   wrong words.
5. **The emotional-turn guard did not help.** It only drops unrequested weather / offers / asides; it never looked at question
   count. (No `EMOTIONAL_TURN_GUARD` log line appears for the window, so the flag was not in shadow.)

Guest scope behaved correctly: public scope, recall skipped for the guest, `GUEST_MEMORY_DISCARD` on every turn, nothing written
to a member.

## What changed

- `labs/flue-zoe-brain-2x/src/doctrines.ts` `WARMTH_DOCTRINE`: three short paragraphs (not an interview; only what they said;
  answers that do not fit). Reaches every member and guest. Golden prompt hash re-pinned.
- `services/zoe-data/emotional_turn_guard.py`: a second question in the same reply is dropped on an emotional turn (kind
  `extra_question`), in the same flag and modes as before (`off` / `shadow` / `on`). The stream form equals the pure form for
  every way of cutting a reply.

## Not changed, and why

- Speech-to-text (Moonshine) is a fixed rock. A long turn lost words that the pendant caught; the doctrine now makes Zoe ask again
  instead of building on a garbled answer.
- The mirror guard still drops a reflective opener before a specific question; with real content now in the reply it works as
  designed.
- A deterministic cross-turn "no question two turns running" breaker was left out: it needs per-session state on the voice path.

## Verification

`services/zoe-data/tests/test_emotional_turn_guard.py`, `labs/flue-zoe-brain-2x/test/warmth_doctrine.test.ts`,
`persona_layer.test.ts`, `harness_overlay.test.ts`. Voice path: replay-gated by the voice lander before deploy.

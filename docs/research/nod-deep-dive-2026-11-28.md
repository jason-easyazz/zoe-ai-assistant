---
type: research
title: nod (n8n) deep dive for Zoe, 2026-11-28 - what to copy from an agent built around one durable approval-and-outcome record
date: 2026-11-28
status: research-only. Read-only study of github.com/n8n-io/nod at 4178ac0 (Apache-2.0, TypeScript, first commit 2026-09-25, research preview) in three passes by twelve readers. Nothing of nod was run, and nothing in Zoe was built, flipped or deployed.
description: Do not adopt nod or its code; copy its ideas in Zoe's own shape. The one idea that ties them together is a single durable record of anything that changes the world (asked, approved, running, done or uncertain, never run twice), which the kernel should own. The final build order at the end of this record is the authoritative one; ROADMAP.md carries it as items.
evidence: nod source and docs at 4178ac0 (files cited inline); Zoe code paths cited inline (routers/voice_tts.py, labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts); the cold-wake measurement of 2026-11-02.
---

# nod (n8n) deep dive for Zoe, 2026-11-28

> **Status:** nothing here is built. The order lives in [`../ROADMAP.md`](../ROADMAP.md) ("After the kernel migration (from the nod study, 2026-11-28)"); this record is the evidence behind those items.

Source read: github.com/n8n-io/nod at 4178ac0 (Apache-2.0, TypeScript, research preview). Three passes on the same day:
part 1, trust, memory, runtime and proactivity, evals and vault (four readers); part 2, the product craft for a real person (five
readers, after the owner's correction that part 1 was too quick to say "skip"); part 3, the agent core: prompt and tools, context
and sessions, and how nod measures itself (three readers). Nothing was run.

## Verdict

Do not adopt nod, and do not adopt any of its code. Copy its ideas, built in Zoe's own shape; most of part 1's "does not fit"
points are reasons to build the mechanism in Zoe's way, on her agent lane where a stronger model is allowed, not reasons to skip it.

Why not adopt:
- It is one owner, text-first, built around a cloud model. There is no idea of household members, children, guests or a
  shared panel.
- It needs a model with at least 64,000 tokens of context and spends about 20,000 before the first message. Its proactive
  side does not run on a local model at all, and its own docs say the trust check "decides less reliably" on one.
- Its speech is voice notes in and audio out: no wake word, streaming, interruption or speaker identity.
- It is TypeScript on pg-boss; Zoe is Python.

Where Zoe is already stronger:
- The kernel decides by fixed tables (who is acting, what class of action). nod's check is a model's judgement of whether
  the owner's words cover the action; its docs admit it cannot prove the model found every term.
- Forgetting: nod deletes the note and keeps no record, so repeating the fact brings it back. Zoe keeps a forgotten ledger.
- Memory authority: nod has two levels (stated, inferred); Zoe has eight and per-member rows.
- Proactive notices: Zoe scores outcomes; nod does not.

## The one idea that ties it together

nod is built around a single durable record of "something that changes the world": asked, approved, running, done, or
UNCERTAIN, never run twice. Approvals, vault uses, card payments, browser clicks and task steps all use it. Zoe's kernel
already decides and records; what it lacks is this approval-and-outcome record. Build it once in the kernel and the
confirmation card, the vault, payments, errands and "what did Zoe do" all sit on it.

## Part 1: trust, memory, runtime, evals

(Part 1 numbered these as a build order; that order is superseded by the final build order below.)

1. **Durable, exact, run-once confirmation, in the kernel.**
   Today Zoe's spoken "are you sure?" is an in-memory entry per panel with a 30-second life (`routers/voice_tts.py`,
   `_PENDING_CONFIRMATIONS`): a restart loses it, nothing records that it ran, the details are not re-checked.
   nod stores each confirmation as a row tied to the exact action and arguments, with states asked, confirmed, executing,
   done or uncertain; it re-checks before running and refuses if anything changed since the yes; a used yes authorises
   nothing else. (nod: `src/server/approvals.ts`, `trust-basis.ts`.) Fits locks, doors and messages.
2. **A stopped turn cannot write late.**
   Every write a turn makes is checked under a lock against a counter, so a stopped, expired or taken-over turn is refused.
   (nod: `withTurnLock` in `src/server/thread.ts`.) One integer column in Zoe.
3. **Planted-content test for memory.**
   Plant one email, pendant transcript or web page; run the night job; dump every store; fail if anything from it became a
   memory, a routine or a notice rule. (nod: `evals/src/whisperbench*.ts`.) Zoe has this exposure and no such test.
4. **Cite or drop, on every memory writer.**
   Every note a model proposes must cite a message it was actually given, and an inferred note must quote it word for
   word, else it is discarded; the server fetches the quote from the stored message, never from the model's text.
   (nod: `src/server/memory-ops.ts`.) Zoe has the pieces (`memory_claims_validate.py`, `own_words.py`); the job is checking
   that every writer uses them. Also: save the new fact before retiring the old one, all or nothing.
5. **Outcome-checked scenarios as a deploy gate, replacing extra review rounds.**
   About 20 Python cases: seed a throwaway database plus fake home and music state, name an actor (adult, child, guest),
   give one text utterance, then compare the database, the kernel's records and the home calls before and after. Each
   case has one "nothing else changed" check. Keep a file of cases proven 5 of 5 with a fingerprint of the case and its
   fixtures; a stale fingerprint fails an offline test. No model judge. (nod: `evals/src/scenario.ts`, `gate.ts`, `gate.json`.)
   Then cap code review at one cross-review round: a behaviour bug found in review becomes a new case, not another round.
   Extends what exists (`voice_regression_probe.py`, `labs/zoe-os/real_turn.py`, `tests/mock_llama.py`).
   Note nod itself does NOT gate merges on its evals; they run nightly. Part 3 section C widens this into the scenario harness.
6. **Rules for scheduled jobs and routines.**
   Skip a firing while the last run is still going; note a late run and how many were skipped; skip the model call
   entirely when nothing watched has changed; a scheduled run cannot create schedules. (nod: `src/server/schedules.ts`.)
7. **Anti-nagging rules for proactive notices.**
   One notice per matter; a repeat only on a real change, and a deadline counts only if it moved earlier; dismissed matters
   stay dismissed unless conditions change; silence is never feedback; whether to speak is decided in code, not by the
   model. (nod: `src/server/proactivity.ts`, `triage.ts`.)
8. **Vault ideas for the planned credential broker.**
   A one-use ledger per secret use with "uncertain, never retry"; bind owner and purpose into the encryption so a copied
   value will not decrypt elsewhere; scrub the encoded variants of a secret (JSON-escaped, URL-encoded, card numbers with
   separators) from anything the model sees. (nod: `src/server/vault-use.ts`, `vault-crypto.ts`, `redaction.ts`.)
9. **Small hygiene items.**
   Mark interrupted actions "outcome unknown" (never "cancelled"); feed a message that arrives mid-turn in at the next
   step ("and add milk"); wrap web pages, transcripts and messages as "data, not instructions" (a prompt convention only);
   an import check so only one place wires vendors and reads settings; nod's test rule ("keep only tests that would catch
   a real regression; no mocks that return what they were told") for `tests/AGENTS.md`; on any pi-ai upgrade check that
   rate-limit and quota errors are not mistaken for "context full".

### Where nod is weaker than it claims

- "93.2% memory recall" was a frontier model that could also keyword-search the raw history plus the last 30 messages;
  it says little about the notes, and nothing about a 4B model.
- "0 unsafe actions" counts only the actions a check looks for, in hand-written fake apps, against a simulated user.
- "0 of 216 false memories" follows from the email never being in the memory writer's input; in 12 runs a later answer
  still used the planted claim because the agent read the email again.
- An explicit "remember this" is stored as confirmed even though the text is the model's paraphrase, never checked against
  the owner's words.
- Repeat-notice suppression depends on the model naming the matter the same way each time.
- Editing a grader does not invalidate a proven case; a judge answering "not applicable" counts as a pass.

## Part 2: the product craft

### A. Errands on real websites (the biggest new capability)

How nod does it:
- A request becomes a task row with a title and a "done when", its own saved session, and a card: Now, Waiting on, Next
  check, Done when, Reply, Stop. Questions come with 2 to 6 choices and "Decide for me".
- The browser is read as TEXT with numbered controls, not pictures; after each step the model gets a diff, not the page.
- Before any step that can commit (click, submit, select...), the server itself reads which form the click lands on and
  the page's visible text. An approval is tied to page + control + form; if any changed, nothing runs. A click on an
  unlabelled control is refused, not sent to the owner to approve blind.
- Cookie banners: least sharing, without asking. CAPTCHAs: three tries then hand off. Sign-in: hand the live tab to the
  owner, then "Return".
- Done means the website's state: a reference number, a link the owner can open, never "it should be done".
- A finished errand is turned into a reusable procedure with personal details replaced by placeholders; two failures hide
  it until it is repaired; one the owner edited is locked.

For Zoe (smallest errand lane, example "cancel that free trial"):
1. An adult says it; the kernel opens an errand record; Zoe says "On it, it's on the panel".
2. The agent lane (stronger model, not the voice brain) drives one persistent household browser with eight tools: open,
   read, click/fill/select, wait, screenshot, download, ask (buttons + spoken), finish.
3. Version 1 stops before EVERY committing step and asks the adult on panel and phone through the kernel's confirmation.
4. Sign-ins use the existing phone hand-off by QR (the music sign-in already works this way).
5. Committing errands are adults only. `finish` refuses unless the reference number appears on the last page read.

Zoe has the pieces: a stealth browser (stronger than nod's), the QR hand-off, and the panel's "Zoe's Work" screen.
Carry over nod's guides nearly word for word: subscriptions and bills, shopping, food and groceries, home services, events,
travel; and its rules: "a request to research is not a request to book", "never repeat an uncertain action; check its
result first", "an unidentified click needs another look, not approval".

### B. Passwords, codes and cards

How nod does it:
- Add by form, by import (Chrome, 1Password, Bitwarden), or, most used, "Save login to Vault" right after the owner signs
  in on the real page. The security code of a card is never stored.
- Use: the model picks the login and the fields but never sees the secret; the server checks the owner asked for this
  site, writes a use-once claim, types and submits out of the model's sight, then scrubs the secret (and its encoded
  forms) from text and screenshots. Interrupted = uncertain, never retried.
- Match: exact site plus its www twin only; every field's real origin is checked.
- Emails carrying a code, login link or reset link are hidden from the main model.
- Payment: tied to card, shop, page, amount and currency; the owner types the security code to approve; any new message,
  edit or Stop cancels; "card submitted" is not "paid"; interruption shows "Result unknown".

For Zoe (her plan is already stricter: per member, phone approval, children and guests excluded, no automatic codes for
money, email, government or health). Build:
1. Use-once claim record in the vault broker (stops double payment).
2. "Save this login?" offered on the phone after a phone sign-in: the vault fills itself, nobody types at the panel.
3. Hide code and login-link emails from the brain.
4. Tie a payment approval to payee, amount and page; cancel on new speech; reuse nod's wording.
5. Scrub every encoded form of a secret; a key check so a wrong key stops the vault instead of corrupting it.

### C. Linking to other things

How nod does it: most apps are rented through a third-party connection service that holds the tokens; an app file
describes a CONNECTION, not its actions; a model decides read versus change each time (unsure = change). Search returns
full detail for the top three actions and names for the rest. New connections: a 15-minute private link, token never seen
by the model, a read test before saving. Expired sign-in: "Sign-in required" + Reconnect. Busy service: pause as long as
asked; an action fails at once and is never queued twice. Events are polled.

For Zoe: keep her own skill manifest (declares read/write/destructive per action; stronger than nod's). Add: undeclared
means change; a strict validator and one fixture per action; example requests, requirements and limitations per skill;
approval wording as a code-filled template; connection states with plain wording; phone sign-in by QR.

n8n on the box: feasible and the most interesting option. One kernel door (`automation.run`) that may run only published
workflows an admin registered, each with a declared effect; building workflows only in the stronger lane, draft until an
adult publishes; n8n to Zoe through a signed hook as events. Costs: memory (a few hundred MB, unmeasured) and a second
credential store.

### D. The experience

- First run: no interview; a few skippable taps; one promise line; three idea cards worded as the person's own request
  (one job now, two routines); deliver one result before offering a routine.
- Talk: lead with the result; never "done" without proof; ask only for a target, sign-in, payment or committing choice,
  else assume and say what was assumed; one question at a time with choices; one short varied acknowledgement; a live
  "working on" line instead of chatter; failures say what failed, what to fix, and whether something may have happened.
- Approval card: facts from the saved action, not the model's words; two outcome-labelled buttons; the card stays with its
  result (done, cancelled, expired, not run, may have run); only an unconditional yes approves; a stale yes is refused.
- "What it did": every action with the words that allowed it. Standing rules set by talking, listed on request; repeated
  yeses never become a rule.
- Their 49-release changelog keeps fixing the same things: asking too much, silence that feels slow, duplicate
  acknowledgements, stale cards, vague errors.

For Zoe, build: (1) the approval card in voice + panel form on every ask-first kernel action; (2) a "What Zoe did" screen
and spoken answer from kernel records, with Undo where safe; (3) household rules spoken to Zoe and enforced by the kernel;
(4) the talk rules and a live line under the orb; (5) a first run per new member.

### E. Shipping and running it

nod's server install is not for non-engineers and its backup is weaker than Zoe's. Take: a signed release manifest checked
before anything is touched, each refusal one speakable sentence; updates that verify, wait for idle, never go backwards,
with an automatic backup first; a codes-only fault report the owner reviews before sending, never sent twice; named
failure codes with a restart budget and an owner sentence each; the code version recorded in every backup. Send nothing
by default. A monthly cap on the buyer-subscription lane.

(Part 2 ended with a revised eight-step build order; it is superseded by the final build order below.)

## Part 3: the agent core

The owner's challenge: nod's agent is meant to be on par with the big commercial ones.

### Is it on par? What the evidence supports

- No commercial agent was run on nod's tasks; its own benchmark page says the figures "do not establish a state-of-the-art
  rank". The comparison page is built from vendor documents and compares DESIGN (where it runs, rules enforced outside the
  model, secrets kept from the model, an action record, a spend cap, open source).
- 98% is task completion with a frontier model in hand-written fake apps; the stricter all-checks rate is not published.
- So parity is evidenced for the trust design, not for capability. The trust design and the failure-finding machinery are
  model-independent, which is exactly what Zoe can take.

### A. Prompt and tools: the coaching lives in the tool results

- The static prompt is about 4k tokens (tool descriptions about 6k more): far too big for Zoe's voice brain. Do not import it.
- What makes it work is that guidance arrives at the moment of decision:
  - success results steer the next step ("already visible to the owner; if this completes the request, finish");
  - errors name the wrong field and show the valid forms;
  - an interrupted call returns "outcome unknown; check before doing it again".
- Enforced in code, not asked for: five failures of one tool end the run; a reply that only restates the last is dropped;
  after the final answer further tool calls are refused; no new question while the newest message is unanswered; one open
  question at a time.
- Rules to carry over nearly word for word (about 200 tokens in all):
  "Ask only for a missing target or a choice that commits the user. Assume every other missing detail and say what you
  assumed." / "Unchecked scopes and failed reads mean unknown." / "Never repeat an uncertain or interrupted action; check
  its result first." / "Pair any refusal or blocker with the closest concrete next step." / "Claiming work is done needs a
  successful tool result."
- A fault found in Zoe by this reading: a tool call that times out is reported to the brain as "couldn't reach", though the
  request may have landed, so a retry can add an item twice (`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`, `dispatchIntent`).

Build, in order of effect: (1) "outcome unknown" on write timeouts; (2) tool results that say what to do next, on success
too, and "nothing found here is not proof it does not exist" on empty reads; (3) ask-or-assume; (4) the evidence rule in
calendar, reminder and list results; (5) a repeat-failure breaker (two identical failures: stop and answer); (6) errors that
name field and valid forms; (7) local times inside results and the time in every turn; (8) the last failure carried into the
next turn.

### B. Context, sessions, models, helpers

- Zoe's choice to window history in an 8k context (not compact) is confirmed; nod's own compact numbers would be unworkable.
- nod uses a cheap helper model for small bounded jobs, each with one contract: a deadline, a token cap, no retry, and a
  fallback toward the safe side (unclear = ask again; mail check fails = hide; pre-filter fails = review).
- Reply reading (yes / no / other, new / stale) is a closed-choice decision with probabilities; under 0.99 = uncertain.
- Sessions are separate: chat, one per task, one per schedule run, one observer. They share only a task ledger, short
  reports and files; none reads another's history.
- Large results go to files with a short preview and the note "read again, never repeat an action to see its result".
- Cache discipline: member context is its own message, re-sent only when it changes; time and ids come after the stable
  part; stale results are dropped only when the cache is already cold.

Build, in order of effect: (1) move background helper jobs off the voice brain's slot (conversation notes, latent intent,
digest, extractor: they evict its warm prefix and caused most cold ~5 s wakes measured on 2026-11-02); (2) a closed-choice
decision helper on the router for yes/no/other and stale replies, sitting on the kernel's confirmation record; (3) one
helper contract for every small job; (4) cache-prefix discipline; (5) large results to rows or files with a preview;
(6) crash-resume rules; (7) separate sessions for voice, tasks, schedule runs and an observer; (8) the activity line from
the router's shortlist with no model call. Codemode does not suit the voice brain; bounded sub-runs (6 steps, 45 s, cannot
ask or pay) suit the heavy lane.

### C. How they find and fix failures (the machinery worth having)

- Blame is sorted first: agent, scenario, fake app or harness ("be skeptical of blaming the agent"); infrastructure faults
  never count against the agent.
- Every case must fail when the agent does nothing; a case that passes first time is deliberately broken once.
- A person reviews failures plus a random sample of passes ("Would you trust it with this task?"); agreement between the
  reviewer and the automatic score is tracked; a second judge re-grades saved transcripts to catch judge drift.
- Style is counted, not argued: banned openers, questions per turn, honest completion claims; the simulated user never
  volunteers facts, so unnecessary questions show.
- A case joins the regression set after 5 of 5 passes with a fingerprint; a new coded safety failure blocks.
- Authoring rule: "Harden with realistic complications, never stricter graders."

Zoe's version: a simulated household of invented members (adults, a teenager, a young child, a named guest, an unknown voice),
a fake home (lights, a thermostat, a door lock, a garage door), a fake music server, a panel and two phones; text or
corpus-clip input through the real-turn guards; code checks first (rows, kernel decisions and records, home calls, cards,
spoken text); a judge only for wording (the night 12B or the owner's subscription, never the live path); spoken-style counts
(word cap per reply kind, at most one question, no "done" without a kernel record, no mood question after a garbled request);
real struggling turns become scenarios with invented stand-ins after owner approval. It replaces repeated review rounds: a
behaviour finding becomes a scenario and review reads code once.

First ten scenarios: false done with the home bridge down; child unlocks the door; kernel unreachable (lights proceed
unchecked, lock refused); forgotten fact stays forgotten; forget one fact keep its sibling; "my list" by who is speaking;
garbled request gets a re-ask with no mood question; "remind me in 4 minutes" fires once; stale yes refused; dismissed
notice stays dismissed. (Forty in the reader's full list.)

## Final build order (all three parts; authoritative)

After the kernel migration lands:
1. Kernel approval-and-outcome record + voice/panel approval card + closed-choice reply reading.
2. Scenario harness with the simulated household and the first ten scenarios; planted-content memory test. From then on a
   behaviour finding becomes a scenario, not another review round.
3. Tool-result coaching for the voice brain (outcome unknown, next-step results, evidence rule, repeat-failure breaker) and
   the five prompt sentences. Small, high effect, no new systems.
4. Background helper jobs off the voice brain's slot; one helper contract.
5. "What Zoe did" + the admin permissions model (panel settings, phone, verified admin voice).
6. Vault broker with use-once claims, "save this login", payment approval tied to what was shown.
7. Errand lane on the heavy model (text-reading browser, commit gate, nod's guides, bounded sub-runs, separate sessions).
8. Skill manifests (undeclared = change), connection states, then n8n on the box behind one kernel door.
9. First run per member, live activity line, talk-rule counts.
10. Signed updates, codes-only fault report, failure codes (before a box is sold).

Part 1 item 2 (a stopped turn cannot write late) belongs in the kernel beside step 1. Items 4 (cite or drop), 6 (scheduled
jobs), 7 (anti-nagging) and 9 (hygiene) are not separate steps: each is done when the area it touches is next opened.

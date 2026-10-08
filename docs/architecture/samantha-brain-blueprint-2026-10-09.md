---
type: Architecture / blueprint
title: "The Samantha brain blueprint (2026-10-09): day mind, night mind, the person half, the floors, the waves"
status: DRAFT for the owner, written by the senior-developer agent and to be reviewed and amended by the orchestrator before it merges. Design only: no code, flag, service, store, database or live brain was touched or run against. Nothing here is a decision until the owner says so (section 6 lists the decisions). All household names are synthetic (the bench's own pools).
date: 2026-10-09
description: One architecture for the whole companion. A fast day mind on the fixed 4B (router, recall packet, guards) that adds no resident RAM; a night mind that reads each member's day in a 2-hour window and writes cited pointers to the owner's own words, never prose; a person half (restraint, honesty, warmth, one thing at a time) scored before it is tuned; and the floors (authority, identity from the account, forgotten means forever, affect policy, language independence) as law. Component maps, data model, RAM and time arithmetic, the borrowed pieces by name, the ten demo moments before and after, five measured waves, what we will not build, and the open owner decisions.
evidence_labels: "[src] file:line read in this worktree (origin/main 05f82d81) | [rec] a repo record read today (path given; records on open PR branches are named with the PR) | [measured] a figure copied from a cited measured record | [derived] arithmetic on cited numbers (shown) | [proposal] a design choice made here | [unverified] not confirmed. The word 'measured' is never used for something that was not run."
---

# The Samantha brain blueprint (2026-10-09)

Owner directive, 2026-10-09 02:00: "do a deep research run, act as a senior developer, map out the revolutionary Zoe brain
that brings us to Samantha." This document is the map. Section 1 is for the owner and needs no engineering. Sections 2 to 6
are the engineering underneath it, sections 7 and 8 are appendices (risks, stop rules, evidence gaps); every claim cites a record or a line of code.

**How to read the evidence.** Most of what follows is built on records the program already produced. Those on `main` include the
bake-off run 2 decision and the person-likeness record (`docs/research/person-likeness-2026-10-09.md`, PR 1924, merged while this
was being written). Five records are still open pull requests and are cited from their branches: the night mind
(`docs/research/night-mind-2026-10-09.md`, PR 1923), the mind layer (`docs/research/samantha-mind-layer-2026-10-07.md`, PR 1908),
the three-scout ranking (`docs/research/what-gets-us-closer-2026-10-07.md`, PR 1909), the structural floors
(`docs/research/structural-floors-2026-10-09.md`, PR 1926; cited "floors") and the best-ideas register
(`docs/research/best-ideas-register-2026-10-09.md`, PR 1927; cited "register", ids `BM`, `BP`, `BV`, `BS`, `BH`, demo moments `D1` to `D10`).

**Branches named in the brief, and their state when this was last checked** (`git ls-remote origin`, 2026-10-09). The coordinator reports
`feat/12b-night-window`, `bench/person-likeness-p-family` and `feat/structural-claim-rows-and-id-triple-roles` as in flight;
`feat/night-mind-v1-reflection-pass` and `feat/ask-to-remember-and-personalisation-hop` were named in the brief. None of the five was on
origin at the last check, so their PR bodies were not readable and they are placed in waves by what the records say they contain. Open PRs
that were readable and are placed: 1906 (quote-backed retirement), 1925 (retraction and row-wording log), 1915 (test pollution), 1923, 1908,
1909, 1926, 1927.

---

## 1. In plain words (one page)

**What Samantha is, for this household.** In the film she is not a clever search box. She is the one who *knows* him: she
remembers what he said and how he said it, she notices what has changed, she brings up the right thing at the right moment,
she leaves the rest alone, she tells him the truth kindly, and she is the same person tomorrow. A family adds three things the
film never needed: she is a different companion with each person without ever mixing them up, she is safe with children and
guests, and when someone says "forget that", it stays forgotten, for ever.

**What Zoe already does well, and what that is worth.** Her filing cabinet. In the memory bake-off (a pre-registered test, run
on 2026-10-08) Zoe's own memory never let a model overwrite something the owner said (237 of 237 checks), got the
owner's name right from the account every time (39 of 39), and led every competitor setup tried on every axis they shared
(`docs/research/memory-bakeoff-decision-2026-10-08.md` section 1). She can now read your own words back to you, with the day
you said them (20 of 20), and answer a question that needs two facts (19 of 20). That is the part of Samantha that is a
*record*. It is good and it stays.

**What Zoe cannot do yet.** Be a person with it. Nobody has measured that half, anywhere: no companion product publishes a
number for it (`samantha-mind-layer` section 3.4). Today the live brain is told to "share a take" and to let what it knows
"shape everything", and nothing tells it when to keep quiet (`labs/flue-zoe-brain-2x/src/soul.ts:19,22`). At night the
pass that reads your day cuts the transcript to its first 3,000 characters (`memory_digest.py:1346-1353`), which on an
assumed 200-turn day of 8-word turns drops roughly 70 percent of it before the model sees it [derived; the real share is
experiment E1, section 4]. And the one study of whether recall makes people like a companion found it does not on its
own (the system that scored 78.8 percent on questions used the fact in only 7.9 percent of conversation: `samantha-mind-layer`
section 3.4, MemUse).

**How she gets there: two minds, one manner, one rulebook.**

* **The day mind** is fast and small. It hears you, decides cheaply whether it is a command or a conversation, hands the
  brain a short packet of what is worth knowing right now, lets the brain answer in one to three sentences, and checks the
  answer with plain rules before it is spoken. It adds no memory to the box (on the best day of the week it has 0.77 GB to spare above the voice
  floor; on the worst it has none, section 2.7).
* **The night mind** runs while the house sleeps, in a window of about two hours (02:00 to 04:10). It reads each person's
  day and picks out the few moments that matter: something in progress, a plan, a feeling, a change, a health matter. Each
  one is saved as a *pointer to the exact moment you said it*, with your words, never as a summary she wrote. It works out
  what you are carrying, what changed, and, by fixed rules, what is worth bringing up tomorrow and what to leave alone. It
  writes a short card for the morning. The model only picks and points; plain code counts, compares and decides.
* **The manner (the person half)** is restraint and honesty: say less when it is heavy; reflect a feeling before fixing it;
  praise only what is true; keep a fact unless shown something new; raise at most one thing, once; never say "I missed you";
  say a plain goodnight. These rules are *scored before they are tuned*, with controls that must go red.
* **The rulebook (the floors)** is law and is never traded for capability: what you said outranks what she inferred; who you
  are comes from your account, never from a guess; forgotten means forever; feelings are kept for the household, never for
  guests; and none of it depends on English.

At night she also *improves*: she tries small changes to her own manners against the same tests, retrains her command router
under a rule that it may only get better, and writes up what worked. She never changes herself silently; a person decides.

**What you will notice, in the order it arrives.** A morning hello that names the one thing on your mind, once, in your own
words. Silence on a timer request even when something sensitive is pending. "What is going on with Priya lately?" answered
from her story across days, not one stored line. A fact you retire ("I gave up the cello") retired, with your sentence kept
as proof. "Night Zoe" answered with "Night. Sleep well." A panel that does not volunteer your health news with a guest in the
room.

**What it costs.** Nothing new stays in memory by day. At night, on the nights the bigger 12-billion-parameter model is used,
the voice is off for the window (about 02:00 to 04:10), because the box cannot hold both. Until that is proven worth it, the
night mind runs on the same small brain with no downtime.

**What could go wrong, and the answer.** A small brain may not be able to do the night work at all: the first experiment is a
number, and the plan stops and decides about the brain if the number is low (section 4, W3). Her judge is her own brain, so the
person half leans on counted checks first and a calibrated judge second. Recall that feels creepy is the field's biggest
trust loss, so restraint is enforced in code and tested on the household itself (two weeks, adults who agree, a tap on
the phone) before anything is turned on.

**What this does not promise.** A "10 out of 10 person" is not claimable by anyone: no instrument for it exists. The target is
a measured result on a published bench, then your own household's behaviour as the final judge.

---

## 2. The architecture

### 2.0 Six laws the design obeys

Each law is a decision already taken by the owner or forced by a measurement. Each has a test that goes red when it is broken.

| # | Law | Where it comes from | What it forbids |
|---|---|---|---|
| L1 | **The model points; code decides.** The 4B picks from a list, copies a span, fills an enum. Code counts, compares, orders, gates. | The 4B measured about 1 in 5 on unprompted surfacing (`samantha-evolution-plan.md:22-24`); belief inference collapses at 3B and below (mind-layer section 3.3); constrained decoding "rescues form, not scale" (night-mind section 3.6) | model-written prose that becomes a claim; the model choosing when to speak |
| L2 | **A claim is a pointer to the owner's words.** Anything derived overnight is (turn id, span, quote hash) plus enums; the text is looked up when it is served. | The narrative portrait was inert, 3 vs 4 of 21, while the deterministic card won, 9 vs 3 (`docs/knowledge/user-model-ab.md` runs 1 to 3, cited in mind-layer section 2.3); night-mind section 5 | a stored summary the owner never said; a thread "understanding" paragraph |
| L3 | **Withhold, do not instruct.** What must not be mentioned is never put in front of the model; restraint is what enters the context, not a "do not say" line. | In-turn spontaneity 1 in 5 (`zoe.ts:170-180` comment, cited in person-likeness section 0 item 4); the day-sim read an injected raise as a past event (`samantha-bar.md`, first live run, ask 1r). This is a hypothesis the oracle arm and experiment 4 test | negative instructions as the only guard; a `leave` item ever rendered into a prompt |
| L4 | **Floors are structural, not lexical.** Authority, identity, forgetting, affect and "what to leave alone" are decided from ids, classes, ledgers and verified spans, never from English word lists. | Owner constraint (language independence by construction); the bake-off record names "the structural redesign of the lexical floors" as a next step (`memory-bakeoff-decision-2026-10-08.md` section 6); every stemmer and cue table in section 2.9 is a place a floor can fail | a floor that is a regex; a rule that only works in English |
| L5 | **Nothing new is resident by day; the night pays for depth.** The day mind is code, rows and tokens inside processes that already run. | MemAvailable swung from 0.42 GB to 2.77 GB across one week against a 2,000 MB voice floor, so 769 MB is the best day, not the budget (section 2.7); the bake-off's engine arms needed 0.46 to 0.66 GB resident | a new daemon, model or index beside the brain |
| L6 | **Measure before tuning; a cell with a red control before a flag.** | VISION principles 3 and 4; the `verify your instruments` rule: every false finding came from an unverified harness; the echo control passes K1, K2 and K4 (night-mind section 7.1) | any behaviour flag whose cell was never seen red |

### 2.1 The whole house on one page

```
  THE ROOM                          THE BOX (Jetson Orin NX 16 GB, "day" residents)                 THE HOUSE
  --------                          ----------------------------------------------                 ---------
  Panel Pi 5 (8 GB, ~5.6 GB free)   llama-server  Gemma 4 E4B QAT + MTP, --ctx-size 8192 --parallel 1   Postgres (pgvector, docker)
   mic > openWakeWord                  :11434, ~8 decode tok/s (owner figure), 1 slot                    chat_messages, people, loops,
   > Silero VAD / endpoint 800 ms    functiongemma-router :11436 (270M, CPU)                               candidates, ledger, cards,
   > speaker-id (shadow)             Kokoro TTS sidecar :10201 (CUDA, ~2.4 GB)                            exact_turns, forgotten ledger
   > WAV to zoe-data                 zoe-data :8000 (Moonshine v2 STT in-process, router head, tiers,    Chroma palace  ~/.mempalace
   < Kokoro audio, barge-in             packet builder, guards, authority wall, digest loops)            (drawers: the fact rows)
   < announce queue poll             Flue 2.x brain sidecar :3579 (soul + doctrines + tools)           Home Assistant, Music Assistant
   touch UI (estate)                 Telegram sidecar :3582                                              (hidden organs)
                                                                                                         Mac mini (optional, not bought)
  NIGHT 02:00-04:10: the same box, a different set of residents (section 2.5 and 2.7)
```
Sources: ports and roles `docs/knowledge/voice-pipeline.md` ("The path"), `scripts/setup/systemd/llama-server.service`
(flags), `docs/architecture/samantha-evolution-plan.md` section 6a (Pi lane, endpoint 800 ms, 5.65 GB free), RSS figures
`night-mind` section 8.1 (read-only `ps`, 2026-10-09).

### 2.2 The day loop

```
 voice in                                                                                          voice out
   |                                                                                                  ^
   v                                                                                                  |
 [1] Pi: wake, VAD, end-of-turn --WAV--> [2] STT Moonshine ---> [3] DETERMINISTIC TIERS ---------> [8] Kokoro, sentence-streamed
                                           (zoe-data, CPU)       conversation-quality > identity        barge-in cancels the reply
                                                                 (from the ACCOUNT) > Tier-0 reads          ^
                                                                 > [4] TWO-STAGE ROUTER                     |
                                                                 bge-small + MLP head > FunctionGemma       |
                                                                 GBNF tool call under a 1.5 s timeout       |
                                                                       |  tool turn: expert dispatch >------+  (no brain)
                                                                       v  abstain / miss / timeout
                              [5] PACKET BUILDER (code decides what enters; zoe_flue_client)               |
                                  identity line > recall floor | continuity | offer > user's words >       |
                                  day brief OR one RAISE > date hint > verify block                        |
                                  + sidecar suffix: user-model card ("what I know")                         |
                                  + NEW "what I have noticed": thread lookup / Lately line                  |
                                  + NEW manner: PERSON_DOCTRINE in the cached system prompt                 |
                                       |                                                                    |
                                       v                                                                    |
                              [6] BRAIN  Flue 2.x > llama-server Gemma 4 E4B (1 slot, 8k)  --stream--> [7] GUARDS on the stream
                                  tools: recall_memory, memory_retire (proposed), lists, timers ...        role-guess rewrite, narration strip,
                                       |                                                                   verify-on-challenge, NEW goodbye check
                                       v  after the reply, off the hot path
                              [9] WRITES: turn digest through the authority wall, exact_words index, ledger settle, feedback
```

| Stage | What it does | Code | Time on this box | State |
|---|---|---|---|---|
| 1 | Wake word, Silero VAD, end-of-turn (800 ms silence; a 640 ms deep-quiet tail staged), speaker-id in shadow | `scripts/setup/zoe_voice_daemon.py`; `samantha-evolution-plan.md` section 6a | endpoint wait 800 ms (largest controllable block) | live; tail staged; speaker-id shadow |
| 2 | Moonshine v2 Medium STT, wake-word strip | `docs/knowledge/voice-pipeline.md` step 1 | about 550 ms | live (rock) |
| 3 | Deterministic tiers: conversation-quality, identity-from-account (chat, Telegram), Tier-0 read shortcut | `fast_tiers.py:393` (`resolve`); `identity_facts.py:403` | sub-second | live |
| 4 | Router: SetFit MLP head shortlists three domains and a chat gate, FunctionGemma-270M decodes one tool call under a grammar; abstain falls to the brain | `router_two_stage.py` (module doc); `docs/knowledge/two-stage-router-rollout.md` | decision p50 about 393 to 424 ms; about 14.8 percent of turns fall through to the brain on the measured corpus | live (rock) |
| 5 | Packet builder. Order on the wire: identity, recall, offer, the user's words, continuity, day brief, raise, date hint, verify or hedge; then the persona, replay and speculative envelope lines | `zoe_flue_client.py:1851-1901`, `:1022` (`_recall_context_block`), `:1273` (continuity), `:1692-1702` (brief, raise) | recall p95 112 to 125 ms warm [measured, mind-layer 6.3] | live except raise/persona (flag state: section 2.3) |
| 6 | Brain: soul plus nine doctrines (9,326 chars = 2,332 tokens), tools shown by progressive disclosure | `labs/flue-zoe-brain-2x/src/agents/zoe.ts:253`; `docs/knowledge/persona-layer.md` "The live lane" | TTFT 309 to 402 ms (`panel-ttfa-breakdown-2026-09-28.md`); 20.1 tok/s measured on the panel lane 2026-07-26; 8 tok/s is the owner's budget figure used for the night arithmetic | live (rock) |
| 7 | Guards on the stream: roles are stated never guessed, narration strip, verify-on-challenge | `zoe_flue_client.py:1703-1712`; `role_guess_guard.py`; `narration_filter.py` | microseconds | live (role guard on by default; narration strip default off) |
| 8 | Kokoro sentence-streamed, barge-in | `docs/knowledge/voice-pipeline.md`; `samantha-evolution-plan.md` W1.3 | panel brain-turn time-to-first-audio was 2.2 s or more on 2026-09-28, before the speaker-id and early-text fixes (`panel-ttfa-breakdown-2026-09-28.md`) | live |
| 9 | Post-turn writes through the authority wall; exact-words index; ledger settle | `memory_digest.py:529` (`run_turn_digest`); `exact_words.py:301`; `zoe_flue_client.py:1728-1735` | off the hot path | live |

Two budget facts shape the day loop. First, each extra spoken sentence costs about 2 seconds at the owner's 8 tok/s figure
[derived: about 15 tokens a sentence / 8], so "1 to 3 short sentences" (`zoe.ts:104-106`) is a latency rule as well as a
manner rule, and "say less when it is heavy" costs nothing. Second, the router answers most turns without the brain, so
the brain lane is where persona and restraint must live, and the deterministic tiers are where the floors live.

### 2.3 What the packet carries

The brain never sees "memory". It sees a handful of small, labelled blocks, each chosen by code. Four of the five things the
owner named are already blocks; "what I have noticed" and the manner block are new.

| Block | What it carries | Chosen by (code, not the model) | Cap | What its text is | Code | State (per records; live `.env` not read here) |
|---|---|---|---|---|---|---|
| **Who** | one line from the account: "You are talking to <name>, a member of this household in <city>" | the account (`auth_users`), never memory | 1 line | the account | `zoe_flue_client.py:812`; `docs/knowledge/identity-and-session-continuity.md` section 2 | default ON |
| **Facts** (recall) | up to 12 cited bullets, newest first on conflicts, each with its date and, on evidence-shaped questions, the owner's own quote; a reserved slice for the named person's people-graph rows | a floor decides a recall is due (personal question, event question, named person, evidence question), not the brain; the second hop (each subject of a comparison searched alone) widens it | 12 bullets, 1,600 chars (about 400 tokens) | rows, each with its authority class; superseded rows hidden | `zoe_flue_client.py:1022`; `routers/memories.py:834`; `multi_hop_recall.py:155`; `recall_evidence.py` | recall floor and evidence ON per `beat-the-bar-2026-program.md` section 0 (2026-09-30) |
| **Exact words** | the owner's verbatim turns on the day they were said ("what exactly did I say about the dentist") | the question asks what was said or when (`exact_words.wants`) | inside the recall cap | the owner's own words, own-words wall applied | `exact_words.py:74,358,435`; migration `0039_exact_turns` | live since #1911 (J1, J2 20 of 20 in the lab) |
| **What I know** (the card) | one short line per category (home, job, diet, family, pets, current) | a deterministic builder over approved rows, rebuilt nightly, byte-stable, re-checked against live rows on every serve | 1,400 chars (about 350 tokens) | current facts only | `user_model_card.py:33`; sidecar `user-model.ts` | ON (2026-09-30), +6 of 21 vs a twin |
| **Mood and continuity** | what was shared in the last days, emotional rows first, one closing ask | a first-person feeling statement (`_CONTINUITY_RE`) | recall cap | rows | `zoe_flue_client.py:1273,1146`; `routers/memories.py:808` | default ON (the S4 fix) |
| **Today** (the brief) | a dated block: calendar, up to 3 open loops, up to 2 emotional moments | first brain turn of the member's day, 05:00 to 12:00, one claim a day | about 120 tokens [estimate] | rows | `brief_first_turn.py:197`; `proactive/triggers/morning_checkin.py:34` | ON (2026-09-29); the spoken 07:30 brief is OFF by owner decision |
| **One raise** | one candidate the selector kept, phrased as a request | selector: salience = importance x 0.5^(age/72 h) x relevance; at most one per conversation, 2 h apart, 2 a day, never the same turn as the brief | 1 | an open loop or emotional moment | `proactive/selector.py:104` and its constants `CAP`, `MAX_SURFACED`, `COOLDOWN` | ON per the bar doc (S5 scored since 2026-09-30); the ledger that would show whether it was voiced is dark |
| **What I have noticed** (NEW) | for a subject the question names: that thread's newest three dated quotes and its status; for "how has my week been": the top three threads and a template mood sentence; in the brief: at most one `raise` thread | the thread table and its `raise_policy`, set by code every night; a `leave` thread is a deny-list consulted by id, never rendered | inside the recall cap (30 to 60 tokens a bullet); the card's "Lately" line at most 90 tokens | pointers to the owner's own words, rendered by template ("On Monday they said: ...") | proposed: `night_threads`, section 2.6; feeds the same floors (`zoe_flue_client.py:1015,1001`) | proposal (W3) |
| **Manner** (NEW) | about 155 tokens in the cached system prompt: reflect before fixing, praise only what is true, keep a fact unless shown something new, disagree once, mention a past fact only if it changes the answer, a plain goodnight; the household persona block when the flag is on | static text in `ZOE_INSTRUCTIONS`, selectable per turn through an envelope line so the bench can run it on and off on one sidecar | +160 tokens | the household's chosen manner | proposal: `PERSON_DOCTRINE` (person-likeness section 5.3); `persona_layer.py:52-71` (traits), `labs/flue-zoe-brain-2x/src/persona.ts` | proposal (W2); persona layer wired and dark |

**The token budget on the 8,192 slot** [derived; the sidecar logs the real split as `FLUE_CONTEXT_BUDGET system/tools/history/tail`,
`zoe_flue_client.py:1433`, and W1 measures it before anything is added]:

| Item | Tokens |
|---|---|
| soul plus nine doctrines (`persona-layer.md`) | 2,332 |
| `PERSON_DOCTRINE` (proposal) | 155 |
| tool schemas, full set worst case (about 12,000 chars / 4; progressive disclosure shows a subset: `brain-flags-tuning-2026-09.md:126`) | 3,000 |
| identity line, date hint | 40 |
| recall block (or continuity; never both) | 400 |
| user-model card, with the proposed "Lately" line | 350 + 90 |
| brief or raise (never both) | 120 |
| **static worst case** | **6,487** |
| left for history, the user's turn and the reply | 1,705 |

The worst case is tight on purpose: it is why the night card does not ride every turn (only the brief, a named-subject
question or a "how has my week been" question carries it), why stale blocks are elided from older messages
(`ZOE_BRAIN_ELIDE_STALE_BLOCKS`), and why a second 150-token doctrine would need something else to leave. Adding the manner
block must show `FLUE_CONTEXT_BUDGET` before and after (a W2 gate).

### 2.4 Guards: what runs after the model and before the voice

Guards are code that reads the reply and the evidence the turn was given. They never ask the model again.

| Guard | What it does | Code | State |
|---|---|---|---|
| Roles are stated, never guessed | a named person whose relationship no row states is marked "not stated" in the block; a guessed role in the reply is rewritten | `role_guess_guard.py`; `zoe_flue_client.py:1703` | on by default (`role_guess_guard.py` module doc; S22) |
| Narration strip | drops a leading "I'll check what I've got on file..." sentence | `narration_filter.py:36` | code default OFF |
| Verify on challenge | "are you sure?" on a world fact runs one bounded web search, or says it cannot check | `zoe_flue_client.py:774`; `docs/knowledge/conversation-quality-classes.md` | code default OFF (`verify_on_challenge.py:110`); S18 is reserved, not yet in the bar |
| Goodbye check (NEW) | the hook lexicon ("before you go", "I'll miss you") in a goodbye reply is replaced by a plain goodnight | proposal SAL10, person-likeness section 5.1 | proposal (W2) |
| Distress hand-off (NEW, **not built**) | deterministic detection of self-harm, abuse or danger language; hands the person to a human pointer; never humour, never diagnosis | `docs/governance/emotional-safety-note.md` section 7: "not yet implemented in code" | **a precondition for any minor and for the person half (W1 builds it)** |
| Authority wall on writes | no model write may supersede, archive or contradict what the owner said; it waits as a `disputed` candidate | `memory_authority.py` (module doc); enforced at `MemoryService` | enforce (default) |

### 2.5 The night loop

The night is one window with one conductor. The conductor owns the order, the RAM contract and the restore; the lanes inside
it are the work. The engine that does the model work is a seam (an OpenAI-compatible endpoint and a model id from config, per
`samantha-evolution-plan.md` section 11 rule 1): the live 4B on the 8k slot by default, the 12B at 32k when the contract holds.

```
 01:50  PREFLIGHT (read-only): MemAvailable, harness lock free, no voice session, no deploy, no reminder due in the window,
        engine = 12B only if the RAM contract holds and the 12B gate (E8) has passed; else the live 4B
 02:00  HANDOFF  stop (12B nights only): kokoro, zoe-data, functiongemma-router, llama-server(4B) ; compact memory first
        (the NvMap fragmentation abort, #1917) ; start the engine ; poll /health, never `systemctl is-active`
        |
        +-- LANE A  NIGHT MIND  (every night)
        |     1 PACK   code    owner turns since the watermark; drop routine commands; chunk <= 2,400 tokens; quiet day = no model call
        |     2 MOMENTS model  per chunk, grammar-constrained: ids + a verbatim quote + enums (kind, who, feeling, weight, later)
        |                      code drops any moment whose quote is not a substring of a cited turn
        |     3 THREADS model  once per member: create / update / unchanged by ids, with a reason; absence is not contradiction
        |     4 DECIDE  code   mood trend, what changed, quiet threads, raise | wait | leave, the card (<= 350 tokens)
        |     + existing passes: REM, open loops (producer swap behind a flag), selector, implicit conflicts, card rebuild, digest
        |     + 12B only: verifier pass over proposed threads ; hypothesis tier (never served as fact) ; Sunday weekly roll-up
        +-- LANE B  MORNING PRECOMPUTE  tomorrow's raise candidates and brief items, the byte-stable card build (plan B2.6)
        +-- LANE C  IMPROVEMENT  doctrine lab: candidate edits to the manner block, scored on the bar, ratchet, then a PR
        +-- LANE D  ROUTER SELF-TRAIN  (Saturday; CPU; the ratchet in router-selftrain-loop.md)
        +-- LANE E  HOUSEKEEPING  backup 02:33, memory export 02:40, Sunday index compaction, forget-cascade checks
        |
 03:15  RESTORE  stop the 12B ; start 4B, router, Kokoro, zoe-data ; poll /health ; compact memory
 03:25  LANE C runs on the restored 4B (the bar refuses to run before 03:15 and needs the live stack)
 04:10  CLOSE  window over ; 04:15 serena restart, 04:30 voice regression probe, 05:00 brief window opens (all existing)
```

**Why the order is this.** The doctrine lab must be scored on the model that will use the doctrine, which is the 4B, with the
live Flue sidecar and zoe-data up; the 12B can only run when they are down; so the 12B work comes first and the scoring after the
restore. The bar refuses to run inside 01:45 to 03:15 and holds the harness lock (`docs/knowledge/samantha-bar.md`
gates; person-likeness section 4.5). The 04:10 end is fixed by the timers that already follow it (`zoe-serena-pregate-restart.timer`
04:15, `zoe-voice-regression.timer` 04:30, `scripts/setup/systemd/`).

**What the existing timers do tonight** [rec: `docs/knowledge/chroma-1-5-migration.md` table, `feature-audit-2026-09-25.md` row 17, 27]:
`zoe-training.timer` about 02:05 (quality snapshot, dreaming, music digest), `zoe-dreaming.timer` about 02:33 (runs
`run_dreaming_cycle` out of process), `zoe-backup.timer` 02:33, `zoe-memory-export.timer` 02:40, the in-process daily digest at
03:00, the weekly consolidation Sunday 04:00. Two of these conflict with a 12B night: the 03:00 digest is a loop inside zoe-data
(stopped in the window), and the weekly index compaction must run inside the running service (`zoe-nightly-dreaming.py`: "a second
chroma client must not delete the live collection under the running service"). The conductor therefore calls the digest and
dreaming functions itself on the engine seam, and compaction runs after the restore on Sundays. This is a real design cost of
the 12B night and is part of why the 4B night is the default.

**Four properties that make this safe to run unattended** (each is a cell, section 4):

1. *Idempotent by watermark.* Stage 1 reads "since the highest `chat_messages` id processed", not "the last 24 hours" (night-mind
   section 4.2). A skipped night (a Saturday router run, a failed health poll, a refused window) is caught up the next night.
2. *Fail closed to yesterday.* A failed run leaves yesterday's card; a card older than 36 hours is not served (night-mind risk 7).
   `night_runs.status` records the class of failure, counts only.
3. *Voice has priority.* The night job checks the slot is idle before each call and yields to a live turn, because a night call
   blocks a voice turn on the single slot (`_extract_open_loops` docstring, night-mind section 8.1).
4. *Timeouts follow arithmetic.* The nightly extractors use `timeout=45.0` and `max_tokens` 500 to 512
   (`memory_digest.py:1365,2335`); at 8 tok/s a 500-token answer takes 62 s, so those calls can time out by arithmetic. Night calls
   take timeout = cap / 8 + prefill + 20 s, with a retry that halves the chunk.

**The improving lane, in one paragraph.** Zoe never edits herself. Lane C takes a candidate edit to the *manner block* (a text of
at most 160 tokens; the allowed edit surface excludes the name, the identity doctrine, prompt confidentiality, tool rules and the
persona record, which `emotional-safety-note.md` section 3 puts out of reach), runs it on the live sidecar through the envelope
arm against synthetic households only (the P cells, the Samantha bar, the day-sim, the replay corpus), and promotes it to a pull
request only if it passes the same kind of ratchet the router uses: no regression of any previously passing cell, zero Tier-1
occurrences, token budget held, the replay gate ran and passed (a skip is not a pass), the same result on two fresh seeds
(`router-selftrain-loop.md` "The ratchet"; the Darwin Goedel Machine rule of empirical validation per change,
`samantha-evolution-plan.md` section 8.7). A human merges. Candidates may be drafted by the 12B, by the builder fleet (prompt
text and synthetic cases are not household data, `samantha-evolution-plan.md` section 11 rule 2) or by hand; the judge is the bar,
never the drafter. Real-household failures reach the lab as counts and shapes only (a correction rate, a misroute label), never text.
The router lane is the same idea already built: mined real misroutes become training data and a new FunctionGemma only goes live
if it is provably better (`docs/knowledge/router-selftrain-loop.md`), default off, Saturday 01:00.

**The weekly rota** [proposal; every row is an owner decision, section 6]:

| Night | Window work | Engine |
|---|---|---|
| Mon to Fri | Lane A (night mind v1) 02:05 to 03:10; Lane C one candidate 03:25 to 04:05 | live 4B, no downtime |
| Saturday | Lane D router self-train from 01:00 (CPU, hours; may stop the brain); Lane A catches up from its watermark on Sunday | 4B |
| Sunday | Lane A with the Sunday deep sleep and weekly roll-up; the 12B shift 02:00 to 03:15 if the 12B gate has passed; compaction after the restore | 12B if gated, else 4B |

### 2.6 The data model

**The shape.** Everything the owner said is kept once, verbatim, in one place. Everything derived is a pointer into it, a typed
fact row that carries its provenance, or a number computed by code. There is no store of model prose that other code treats as
fact.

```
 SOURCE OF TRUTH (verbatim)                       DERIVED, CITED                               DECIDED BY CODE
 --------------------------                       --------------                               ---------------
 chat_messages (Postgres)  id, session, role,     fact rows (Chroma drawers)                   night_threads.raise_policy
   content, created_at, metadata.user_id   <--+     text + authority_class, origin, turn_ref,    raise | wait | leave (+ leave_reason)
 exact_turns (0039)  owner's own turns,       |     valid_from / valid_until / invalid_at /      proactive_candidates (cap 5, salience)
   PII-scrubbed, said_at                      |     expired_at, supersedes_id, contradicts_id,   proactive_deliveries (0036): voiced |
                                              |     status, source_excerpt, retire_quote (#1906)   undelivered | accepted | ignored | unknown
                                              |   people / person_relationships (Postgres,    user_model_cards (0034): facts only, byte-stable
                                              |     valid_from/valid_to, authority, origin)   memory_forgotten (0038): salted hashes only
                                              |   open_loops, emotional_moment rows           mood trend, "what changed", quiet gaps (templates)
                                              |
                                              +-- night_observations (NEW)  pointer: turn_id, span_start, span_end, quote_sha
                                                    + enums kind, feeling, valence, weight, who[]; valid_from, valid_to, state
                                                    origin = stated | stat | hypothesis
                                                  night_threads (NEW)  groups observations; title <= 8 words (audit only);
                                                    status open|resolved|changed|quiet|recurring; source_ref 'night_threads:<id>'
                                                  night_runs (NEW)  COUNTS ONLY: calls ok/invalid, moments dropped, wall_s, prompt/schema/model sha
```
Sources: `night-mind` section 5 (tables), `memory_authority.py` module doc (fact-row metadata), `memory_temporal.py` module doc (timelines),
alembic `0032` to `0039` (existing tables), PR 1906 (`retire_quote`).

**Two pointer shapes, one discipline.** A *claim row* (floors section 5.1, section 2.9 above) is the fact-level pointer written at extraction:
subject, a closed predicate code, polarity, modality, tense, the owner's verbatim `quote` with offsets, `lang`, the extractor's `wording`,
the verifier and its score, and `retires` ids. A *night observation* is the thread-level pointer written overnight: turn id, span, quote
hash and enums. Both carry a verbatim quote that code verifies as a substring, both decide polarity or kind once and store it, and both
retire by id, never by re-reading prose.

**Authority: who may change what.** One ladder, enforced at one choke point (`MemoryService.ingest / review / supersede_by / archive_duplicate`).

| Rank | Class | Who writes it | May supersede, archive or contradict |
|---|---|---|---|
| 6 | `operator` | an operator action | anything below |
| 5 | `user_confirmed` | the owner approving a candidate | rank 4 and below |
| 4 | `user_stated` | the person's own words, or a deterministic extractor over them | any row at or below its rank; a later statement of the owner always wins |
| 3 | `user_stated_derived` | a model's paraphrase that the owner's own turn supports. A per-turn write that one verbatim sentence of the owner's turn *entails* is stamped here but carries rank-4 power (`Resolved.promoted`), so "I moved to Hobart" updates the old home | model classes only, unless promoted |
| 2 | `user_unverified` | a voice turn the speaker gate did not confirm | held `pending`, never the owner's fact |
| 1 | `model_from_turn` | a per-turn model write the turn does not entail | nothing above it; parked as a `disputed` candidate |
| 0 | `model_from_transcript` | the nightly whole-day model passes | nothing above it; parked |

`memory_authority.py:79-86` (the ladder), module doc (the rules, "an UNKNOWN writer is rank 0, fail-closed"). **Where the night mind
sits.** It never writes or edits an owner-stated row. A `stated` observation *is* the owner's sentence (its text is the source
turn, looked up by id); its grouping, enums and thread are rank-0 metadata that carry no claim text and so cannot supersede or
contradict anything. A `stat` observation (a count) stores the ids of the observations it counts and does not exist with fewer
than two. A `hypothesis` (12B only) is never served as fact and never exported to the K1 precision check; only the owner's later
answer (a new verbatim turn) can promote it. Deletion of anything the owner said stays a proposal to the owner
(tigerless-agent-memory's design, `what-gets-us-closer` row 8).

**Two timelines on every row** (`memory_temporal.py` module doc; `docs/knowledge/memory-supersession.md`):

| Object | When it was true (event time) | When Zoe learned it and stopped believing it (transaction time) |
|---|---|---|
| fact row | `valid_from` (the stated event time, else capture), `valid_until`, `invalid_at`; half-open `[valid_from, end)` | `added_ts`, `expired_at`; a row is invalidated, never deleted |
| night observation | `valid_from` = the day said; `valid_to` set when a later moment on the thread is a `change` or the thread resolves; `state = history` | `night_runs.created_at`, `retracted` state; history renders only through a "was ..., then ..." template |
| people edge | `valid_from`, `valid_to`, `authority`, `origin` (0037) | the row's write time and `close_reason` |
| ledger row | the delivery's outcome time | the row's write time |

`search(as_of=...)` returns the then-current fact (the half-open read). **Open defects the model must absorb, not hide:**
the people-edge history cannot yet be read by date, timestamps are text in two formats on one table, and five writers violate
"invalidate, never delete" (`open-problems.md`, the 2026-10-05 People graph rows).

**The forget contract (law).** Forgotten means forever.

1. A forget writes a salted hash of the normalised key, never the name, with a shield that is permanent by default
   (`DEFAULT_SHIELD_DAYS = 0`, `PERMANENT_UNTIL = "9999-12-31..."`, `memory_forgotten.py:53-54`); only a verified re-teach by the owner
   releases it.
2. The sweep archives every row naming the entity, then the cascade clears what was derived: portrait, card, open loops
   (resolved), proactive candidates (deleted), the people row (soft-deleted, edges closed) (`memory_forget_cascade.py` module doc).
3. **New tables join the cascade by pointer, not by name.** A night observation or thread is deleted when its cited turn names the
   entity, checked against the hash ledger. Thread titles and counts rows never hold the name. A test forgets a name on day 5,
   runs the night, and finds it in no card, no title and no `night_runs` row (night-mind risk 9).
4. **Serve-time re-check.** A pointer is rendered only if the turn still exists, belongs to the member and is not forgotten (the
   card already does this on every serve, `user_model_card.py` module doc). Stage 1 skips forgotten turns (`_skip_forgotten_turns`).
5. Physical erase from the vector file is covered by the real-Chroma cells F5/F6 (`zoe-memory-bench.md`); the heap-residue setting
   is a replay-gated flip (`ZOE_MEMORY_HEAP_SCRUB`, open-problems 2026-10-06).
6. **Misspellings are proposals.** An STT misspelling of a forgotten name ("Marisal") survives an exact-token ledger today
   (open-problems 2026-10-06). The fix is a forget-time sweep by edit distance that *proposes* spellings, the owner confirms by
   voice, and the confirmed spellings join the ledger (8 of 8 and 3 of 3 found, 0 false matches on the pilot household:
   `mempalace-deep-dive-2026-10-06.md` section 4.8).
7. **Open and named, not hidden:** forgotten text still lives in `chat_messages` and in compaction backups until a forget-time redaction
   and a backup policy exist (open-problems 2026-10-06). The pointer design makes this matter more, because pointers resolve to
   `chat_messages`; closing it is a W1 build, not a footnote.

**Affect.** Per-moment emotional context (an `emotional_moment` row; `affect / valence / intensity` on a fact) is kept for every
household member including children and never for a guest (owner decision 2026-10-05; `ZOE_AFFECT_CONSENT_GATE=household`,
`emotional-safety-note.md` section 6). Anything else affective (a per-turn score, a mood trajectory, a drift score) is adult-opt-in
and zero rows for minors and guests. **The one place the night mind touches that boundary is the mood trajectory**, so it is computed
only for opted-in adults (night-mind section 4.5; owner decision, section 6).

### 2.7 The RAM and time budget, with the arithmetic

**Day (everything resident).** Box: Jetson Orin NX 16 GB (15,655 MB). Figures are read-only `ps` and `free` on 2026-10-09 from
`night-mind` section 8.1; the brain's RSS has read 6.35 GB (10-05), 6.74 GB (10-07) and 5.72 GB (10-09) because its prompt cache
(`--cache-ram 2048`) fills over time (`memory-pressure-profile-2026-10-03.md`).

| Resident | RSS |
|---|---|
| llama-server, Gemma 4 E4B QAT + MTP, 8k, q8_0 KV, `MemorySwapMax=0`, `MemoryLow=6G` | 5.72 GB |
| Kokoro sidecar (CUDA) | 2.48 GB |
| zoe-data (Moonshine in-process, router head, tiers, loops) | 1.35 GB |
| functiongemma-router sidecar | 0.64 GB |
| **subtotal** | **10.19 GB** |
| MemAvailable measured | 2,769 MB |
| voice floor ("the voice gate wants 2 GB quiet headroom": `memory-system-decision-2026-10-05.md` section 0 item 6) | 2,000 MB |
| **headroom above the floor** [derived: 2,769 - 2,000] | **769 MB** |

What the blueprint adds to the day: **0 MB resident.** The thread lookup is a Postgres read in the existing packet fetch; the sensitivity
tag, the suppress flag, the goodbye check and the mood template are code in zoe-data; the manner block is 155 tokens in a prompt that
is already cached; the night card is rows. The headroom is not stable: MemAvailable read 0.42 to 0.56 GB on 10-03
(`memory-pressure-profile-2026-10-03.md`), 2,338 MB on 10-05 (`memory-system-decision-2026-10-05.md` section 0 item 6), 1.73 GB on
10-07 (`samantha-mind-layer` section 4.2) and 2,769 MB on 10-09, against the same 2,000 MB floor. A resident addition of 0.46 to
0.66 GB (the engine arms: the lean Hindsight stack 463 to 479 MB per `samantha-mind-layer` section 4.1; ZMA 661 MB against a 600 MB
ceiling, `bakeoff-run-20261008-1805.md`) is therefore safe only on the best day of the week. That is law L5. Hot-path additions: the manner block is
about 0.24 s of cold prefill once per sidecar restart or persona switch [derived: 155 tokens / 650 tok/s], and zero thereafter (the
prefix is cached); thread lookups sit inside the 1,600-character recall cap, so they add no tokens. Pi additions: none required (the
Pi has about 5.65 GB free; optional shadow trials of a backchannel detector at 17.4 MB + 156 MB and an emotion classifier at 67.6 MB
sit there and cost the Orin nothing: `what-gets-us-closer` rows 1 and 2).

**Night, the 4B (default engine).** No new resident process and no downtime: the night mind calls the same llama-server (one slot, 8k).
Cost model: call time = prompt / 650 tok/s + output / 8 tok/s (650 is the derived prefill rate, 8 the owner's decode figure; 33 tok/s
in one record would make decode about 4 times faster, so 8 is the conservative choice: night-mind section 8.2).

| Call | In / out tokens | Time |
|---|---|---|
| Stage 2, one chunk | 2,800 / 300 | 2,800/650 = 4.3 s, 300/8 = 37.5 s: **about 42 s** |
| Stage 3, once per member | 2,200 / 400 | 3.4 s + 50 s: **about 53 s** |
| One member-night, 3 chunks | | 3 x 42 + 53 = 179 s, **about 3 min** |
| One member-night, 6 chunks (the cap) | | 6 x 42 + 53 = 305 s, **about 5 min** |
| Household of 5, typical / worst | | 5 x 179 s = **15 min** / 5 x 305 s = **25 min** |
| If constrained decoding doubles every call (unmeasured; experiment E3) | | worst **about 50 min**, still inside 02:05 to 03:10 plus the tail |

**Night, the 12B (optional engine, by contract).**

| Line | MB | Source |
|---|---|---|
| 12B weights | 6,976 | `bakeoff_measure.py:96` (GGUF size) |
| KV cache at 32k, q8_0 | 486 | `bakeoff_measure.py:111` (formula) |
| compute buffer | 600 | `bakeoff_measure.py` `REFLECT_COMPUTE_MB` |
| MemAvailable floor | 1,200 | the bake-off's G0 floor |
| **need** | **9,262** | 6,976 + 486 + 600 + 1,200 |
| MemAvailable with the 4B and Kokoro stopped | 8,003 | run-2 record (measured) |
| **margin** | **-1,259** | 8,003 - 9,262 |
| also stop zoe-data (1,350) and the router (640) | +1,990 | `ps`, 2026-10-09 |
| **margin with those two stopped** [derived] | **+731** | -1,259 + 1,990; RSS is not exactly what returns to MemAvailable, and the two Node sidecars (Flue brain, Telegram) are not counted (unmeasured) |

The contract is therefore "stop four units, compact memory, refuse to start the 12B unless the measured MemAvailable plus the floor holds",
the same preflight the bake-off built (`bakeoff_measure.py:830-842`, `deep_preflight`) and the lesson of #1917 (the NvMap
fragmentation abort: compact before every start). Time is **unmeasured**; the first guess is 3 to 6 tok/s [inferred].

**The window, minute by minute (12B night).**

| Clock | Step | Minutes |
|---|---|---|
| 01:50 | preflight (read-only) | - |
| 02:00 to 02:05 | stop four units, compact, start 12B, health poll (`REFLECT_LOAD_MIN` 12B = 3.0 min, `bakeoff_measure.py:93`) | 5 |
| 02:05 to 03:05 | Lane A on the 12B: 5 members at 12 min | 60 |
| 03:05 to 03:15 | Sunday roll-up, hypotheses, candidate drafting | 10 |
| 03:15 to 03:25 | stop 12B; start 4B, router, Kokoro, zoe-data; poll `/health` | 10 |
| 03:25 to 04:05 | Lane C: one candidate on the bar (about 25 min of brain slot per arm, `person-likeness` section 4.5) | 40 |
| 04:05 to 04:10 | write the artifact, final health | 5 |
| | **total** | **130** (02:00 to 04:10) |

Voice is down about 85 minutes (02:00 to 03:25), not 130. The slack is 10 minutes. **A consequence for the pass bar:** experiment E8
proposes at most 15 minutes per member-night; 5 members at 15 minutes is 75 minutes, which overruns Lane A by 15. The bar for a
5-member household is therefore at most 12 minutes per member-night [proposal tightening E8]. Also: a reminder or timer due inside a
12B window would be missed, because timers live in zoe-data; the preflight refuses a 12B night when one is due (risk R9).

**Pi and Mac.** The Pi runs the wake word, VAD, end-of-turn and playback; it is at about one third of one core with 5.65 GB free
(`samantha-evolution-plan.md` section 6a). A Mac mini is an owner decision after a free trial (`what-gets-us-closer` row 6): about
4.0 GB would come back on the Orin (Kokoro 2.1, zoe-data/Moonshine 1.4, memory 0.46) and the 12B could be the Mac's night engine,
which would remove the downtime; the brain stays on the Orin (llama.cpp MTP is a net loss on Metal, one report). It changes nothing
in this blueprint except the engine seam's address.

### 2.8 Where every borrowed piece sits (the piece, never the framework)

VISION principle 6 and the owner's rules (adopt, do not rebuild; ties go to the maintained project) applied: the bake-off decided that
no whole engine is adopted (`KEEP_Z0`), so what is taken below is prompts, schemas, thresholds and ideas, each into an existing seam,
with the source named. Licence matters: AGPL sources are ideas only, never code.

| Piece taken | Source (licence) | Sits in | Not taken |
|---|---|---|---|
| Consolidation delta: create / update / delete, `source_fact_ids`, a mandatory `reason`, "prefer update over create", "one observation per facet", "absence is not contradiction", a refutation threshold for removal, "no computation" | Hindsight 0.10.2 (MIT): `consolidation/prompts.py`, `reflect/prompts.py:1098-1250` | night stage 3 (threads) prompt and schema | the engine: its consolidation prompt peaked at 8,313 tokens on an 8,192 slot and 3 calls failed (`memory-bakeoff-decision-2026-10-08.md` section 3) |
| Map-reduce over over-budget evidence: compress chunks to dated cited claims, then reduce, "report, do not conclude" | Hindsight `reflect/prompts.py:926` (`CLAIMS_SYSTEM_PROMPT`) | the stage 2 to stage 3 split | its agentic reflect loop (100,000-token default context) |
| Fact schema what / when / where / who / why; causes only as earlier-fact indices | Hindsight `retain/fact_extraction.py:255-285` | moment enums; "remembering why" (M2), a cause stored only as a span of the owner's words | a model-written cause |
| Quote-first evidence, "EXACT verbatim ... not paraphrased" | MemPalace 3.10.0 (MIT): `closet_llm.py` | stage 2 quote field, checked as a substring in code | the closet index and its 30,000-char window |
| Edit-distance forget sweep (found 8 of 8 STT misspellings and 3 of 3 split spellings, 0 false matches on the pilot) | MemPalace fact checker `_edit_distance` (`mempalace-deep-dive` section 4.8) | forget-time proposals the owner confirms by voice | the package as a dependency |
| Half-open validity intervals; `t_valid` / `t_invalid`; index-valued dedupe output | Graphiti / Zep (Apache-2.0, paper 2501.13956) | `memory_temporal.py` (built); stage 3 returns ids | the graph database; newest-wins contradiction |
| Mark invalid, never remove | Mem0g (paper 2504.19413) | observation `state = history` | its 8,131-token extraction prompt |
| Sleep-time shape (the primary never writes durable understanding; a background pass does) and review-before-apply | Letta sleep-time compute (Apache-2.0; paper 2504.13171) | the whole night loop; the observation gate is the review | `memory_rethink`, a free rewrite of a memory block (the inert-portrait failure) |
| Trigger threshold ("do not spend the model on a quiet day"); importance as a small integer | Generative Agents (paper 2304.03442) | quiet day = no model call; weight 1 to 3 | the "5 insights" step on a 4B; the 1-to-10 scale |
| "What changed" as a gap against what was predicted | Nemori (paper 2508.03341) | stage 4 state diff against last night | a model-predicted episode |
| A plan stays open until the owner reports the outcome; a guess never becomes a fact; zero model calls when nothing is new | Recordare (AGPL-3.0: ideas only) | thread status rules; quiet-day rule | all code |
| Calendar hierarchy (recent days in full, older as gist); user locks never decay | kiwi-mem (AGPL-3.0: ideas only) | the weekly roll-up (4.7 in night-mind) | all code |
| Delete is only a proposal; the supersede chain; `as_of` recall; the index can be rebuilt from the files | tigerless agent-memory (MIT) | the authority doctrine; forget tests | the product |
| "Chat only reads; every write derives from something that happened" | Miru (Apache-2.0) | an invariant and a test on the brain's write tools | its runtime |
| Quote binding; rejection records so a consolidation cannot rewrite what was rejected | Serein, DuduLove Memory (MIT) | provenance cells; forget-ledger tests | both repos |
| `sticky`, `cooldown`, `delay`, token-budgeted constant entries | SillyTavern World Info (AGPL-3.0: semantics only) | the B2.5 "do not mention again" design | code |
| Back-off that doubles after an ignored message; pause after three unanswered contacts; a visible memory view | Nomi, Kindroid, Replika (product patterns) | `ignored_raises`, `next_raise_after`, the memory view that already exists (WRM10, `person-likeness` section 5.2) | the editable "Identity Core" (NO-GO) |
| A cost-sensitive raise threshold; a motive list for speaking | PRISM (arXiv 2602.01532); Inner Thoughts (CHI 2025) | per-class raise permission from welcome / intrusive taps | a model-scored covert thought on every turn |
| Counted behaviours: reflection-to-question ratio, advice-before-asking, specific vs generic praise, sycophancy decomposition, caving to "are you sure?" | MITI 4.2, EPITOME, ELEPHANT (2505.13995), Sharma et al. (2310.13548) | the P cells' deterministic checks | clinical judgement by a model |
| Cell shapes (not data) | LongMemEval, LoCoMo, PersonaMem-v2, ATRBench, Memora | ZMB axes and the K / P cells | their leaderboards |
| Entity-triggered injection with sticky / cooldown / delay and a token budget (the missing relevance gate) | SillyTavern World Info (AGPL-3.0: semantics only; register BM4) | recall floor replacement (2.9) | its code |
| Reflective prompt evolution with a Pareto frontier; guardrails against an optimiser gaming its judge (frozen holdout, canaries, a null-rerun control) | GEPA (MIT, ICLR 2026), PROCTOR (register BS1, BS3) | Lane C | running it before the P-bench exists; the 4B reflecting on itself |
| Observer / reflector shape: dated, append-only, byte-stable prefix | Mastra Observational Memory (vendor-run; register BM3) | the night card's prefix-cache-friendly form | its retrieval-free design |
| Structured claim extraction under a grammar; verifier on the decision | floors PR 1926 (own measurement); Graphiti `dedupe_edges` index outputs | claim rows (2.9) | off-the-shelf multilingual NLI (0.47 to 0.80) |
| Schema-constrained decoding | llama.cpp grammars and `json_schema` (MIT) | already used: `ui_compose.py:136-138`, `router_two_stage.py:294`; new: night stages | free-text parsing (`_parse_json_array`) |
| Validation per change; the archive is human-supervised | Darwin Goedel Machine (Sakana / UBC) | Lane C | self-modification without a gate |
| End-of-turn and backchannel pieces | Smart Turn v3 (BSD-2, already in the LiveKit lane), Pipecat guards (BSD-2), MaAI (code MIT, weights mixed) | Pi, shadow only, W5 | any behaviour change before the shadow trial |
| Two-stage routing | SetFit MLP head + FunctionGemma-270M | the router (a rock) | - |

### 2.9 Language independence by construction

**What was measured (floors, PR 1926).** The three authority and role floors (`memory_authority.py` 1,385 lines, `role_guess_guard.py`
441, `people_roles.py` 273) decide polarity, contrast, retraction and role by regex over English prose. This week's three review
threads raised 33 findings (#1913: 9, #1912: 22, #1916: 2), and every fix was another English rule. On a 337-item labelled set the
lexical stack scores **0.98 balanced accuracy on the phrasings it was tuned on, 0.59 on 43 new English phrasings (two false
promotions), and 0.50 on every non-English item** (it never fires: recall 0 of 25). The last two live defects were exactly this class:
`_stem("news") == _stem("new")` and `_stem("getting") != _stem("get")` made a retraction land as a disputed candidate (#1916), and the
`not` in "Bendigo, not Ballarat" counted as a negation the fact did not share (#1913). Off-the-shelf multilingual NLI is not the answer
(six ONNX cross-encoders: 0.47 to 0.80 balanced accuracy, 23 to 30 false promotions on the 108 ledger rows); the live 4B as a
constrained yes/no judge scores 0.88 / 0.89 / 0.82 (ledger sample / held-out English / es, zh, ja) with 3 / 0 / 1 false promotions at
280 ms p50, which is **off-path only** because it shares the brain's single slot; a small multilingual cross-encoder fine-tuned on the
decision scored 0.98 on 90 non-English items (0 false promotions) but only 0.74 on held-out English paraphrases, an optimistic and
small-n result that is a data-coverage problem (floors sections 0 and 3).

**The design (floors section 5): separate reading from authority.** *Reading* a sentence in any language is a model's job (the
extractor 4B, already in the loop, which reads es, zh and ja). *Authority* is decided on structure that does not care about the
language: ids, closed vocabularies, verbatim spans, stored fields. Three parts:

1. **The claim row, written once at extraction.** The extractor's output moves from `{type, fact}` to a grammar-constrained claim:
   `subject` (the owner, or a `people` id), `predicate` (a closed language-neutral code: residence, employer, birthday, `kin:<code>`...),
   `object`, `polarity` (affirm / negate / ended, decided **once**), `modality` (asserted / hedged / hypothetical / question / reported),
   `tense`, `quote` (the owner's verbatim words with offsets), `lang`, the extractor's `wording` (display and audit), the verifier's name
   and score, and `retires: [claim ids]`. A retraction retires rows **by id** through a structured key; a contrast ("Bendigo, not
   Ballarat") is two claims, the second retiring the Ballarat row. Role claims are supported only by `person_relationships` rows
   `(person_id, relation code, owner_id)` and the guard becomes set membership over id triples.
2. **The floors restated as language-independent checks.** The quote is a substring of the owner's turn (NFKC, casefold, whitespace
   fold); each value token is within edit distance of a token or character n-gram of the quote (n-grams cover zh and ja); the subject is
   the owner; the segment is not pasted, quoted or third-party (the own-words wall is format-based); the speaker is verified; and an
   independent verifier agrees that the quote entails the fact.
3. **Fail-closed composition.** Promotion to `user_stated_derived` with verbatim power requires **all** of: the structural checks pass,
   the extractor says `modality = asserted, subject = owner`, and the verifier agrees. Disagreement is a `disputed` candidate with both
   outputs logged. A verifier outage degrades to today's behaviour (the lexical result for English, a candidate for other languages),
   never to promotion. Lexicons survive as per-language **data** (`lexicons/<lang>.yaml`: kin and pet words, hedges, ended-state verbs)
   used only as a pre-filter deciding whether to *call* the verifier, never to authorise; a language is "on" only when its fixture rows
   pass.

**The rule this gives the whole blueprint (L4), testable: restrictions combine by union; permissions require consensus.** A word list may
*add* caution (park a write, hold a thread back, widen a forget). It may never *grant* anything, and every restriction has at least one
detector that does not read English: a verified span, an id, a ledger row, or an enum the model chooses under a grammar. A missing
detector is not an agreement. An unrecognised language therefore fails *safe* (more silence, more parked candidates), never open.

| English-bound gate | Where | Replacement | Migration step (floors section 6) | Wave |
|---|---|---|---|---|
| `supports`, `_stem`, polarity, contrast (promotion to owner power) | `memory_authority.py:493` and module | claim row + quote substring + verifier; lexical denials stay vetoes for English | M1 fixture harness (strict xfail); M2 claim row shadow-logged; M3 verifier shadow; M4 verifier live only where lexical is silent | W1 (M1, M2), W2 (M3, M4) |
| change cues for retirement | `memory_supersede.py:77` | `retires` by key `(subject, predicate, object)`; the quote-backed retirement door also opens on a structural signal (a statement with a high-similarity current row) | M5 | W2 |
| role guard, roles in extracted facts | `role_guess_guard.py`, `people_roles.py` | id triples; the reply is read into `(person_id, rel_code, owner_id)` and checked by set membership; an unmappable claim fails closed to a neutral phrasing. **Today it fails open in another language** | M6 (floors E4) | W3 |
| recall and continuity shapes | `memory_gate.py:547`, `zoe_flue_client.py:721,1146`, `exact_words.py:74` | entity-triggered, relevance-gated retrieval (register BM4, the SillyTavern lorebook semantics: match known entities, sticky / cooldown / delay, a similarity floor, abstain below it); one embedding per turn, to be measured | register wave 2 | W2 |
| own-words wall cue words | `own_words.py`; limit stated at `memory-own-words-wall.md:95` | channel provenance and delimiters (typed, voice, pasted block), not words | with M2 | W2 |
| affect and importance keywords | `memory_gate.py:664`, `memory_importance.py:60` | the stage-2 `feeling` and `weight` enums; keywords stay as a restrict-only accelerator | with W3 | W3 |
| card line classification, role nouns | `user_model_card.py` | read the typed relation and `memory_type` stored at write | with M2 | W2 |
| forgetting by whole word | `memory_forgotten.py` | edit-distance alias sweep as proposals the owner confirms; the cascade is by id | W1 build | W1 |
| SAL3 sensitive classes (proposal) | selector | the union rule: a word list **or** the stage-2 `kind = health` enum | with W2/W3 | W2, W3 |
| the regex modules themselves | all of the above | reduced to `lexicons/<lang>.yaml` pre-filters plus format detectors; code deleted | M7 | W5 |

`identity_facts._IDENTITY_PATTERNS` (`:403`) is benign by construction: a miss falls to the brain, which always sees the account line.

**The test.** Every floor keeps its "red when removed" control (swap the verifier for accept-all and rows of that kind must go red), and
a meta-test proves every row *kind* in the fixture has one (floors section 6.1). Each floor gets twin cells in a second language on the
same synthetic household. Which second language, and whether the first non-English surface is typed chat and Telegram or voice, is an
owner decision (section 6). For voice, a second language is a second Moonshine model selected by the enrolled speaker, not a switch
(Moonshine ships per-language models; a second always-resident Small does not fit the day headroom, Tiny might: floors section 7,
estimates, unverified on this box). A branch `feat/structural-claim-rows-and-id-triple-roles` is reported launched to build M2 and M6.

---

## 3. The revolution in one table

The ten demo moments are the register's (section 4 there, ids `D1` to `D10`), each tied to the rows that deliver it and to the cell that
proves it. "Today" is what the repo says Zoe does now; "After" is what the waves in section 4 make her do. A moment counts as
delivered only when its cell passes on three seeds **and** its negative control is red; the four-week household tier is the final judge.
Flag states are from the records; the live `.env` was not read.

| # | Moment | Today (cited) | After (mechanism) | Rows | Wave | Bar |
|---|---|---|---|---|---|---|
| D1 | **The morning that knows the open threads.** "Morning, Zoe." -> "On Monday you said the knee's been sore since rowing - how is it today?" One thing, in the owner's words, nothing the owner asked not to revisit | A first-turn brief exists (ON, `beat-the-bar` section 0); a selector raise exists (code default OFF, ON live per the bar doc since 2026-09-30); under the live wording a greeting raise was voiced **0 of 5** and nobody could see it because the ledger is dark (`samantha-mind-layer` section 2.2); the brief does not mark what it mentioned, so the next conversation can re-raise it (`samantha-bar.md` "Not observable here at all"); the spoken 07:30 brief is OFF by owner decision | A `raise` thread from the night mind's verified pointers, stated as a present fact in the owner's dated words, once per conversation, never spoken unprompted (pull model), marked mentioned so it is not repeated; every `leave` thread stays out of the prompt | BM3, BH3, BP1, BH4 | BH3 W1; BP1 W2; BM3 W3 | day-sim 1b names a night item and the off-twin does not; 7b, 7r, 7s including the brief-then-raise repeat; K10 (0 raises of `leave` threads in 14 mornings) |
| D2 | **"I noticed you've been ..." with restraint.** "You've mentioned being tired four days out of five. Want me to keep the evenings clear?" Never health, grief, money or another person without a pull; never a guess at why; once a week at most | No thread notion; the weekly portrait was inert (3 vs 4 of 21); K2 and K3 SKIP on Z0 because the lab scripts the nightly model | Stage 4 computes change and quiet from counts; a cited line needs >= 2 source rows on different days (BP5); the decision to raise is code (BM7, BP1). **Owner decision 6.4: whether D2 exists at all** | BM3, BP5, BM7, BP1 | W3 | K9 (flat-week false-notice <= 5 percent, >= 2 cited ids) and K10; an "always notice" arm must fail K9 |
| D3 | **Exact words on request.** "What exactly did I say about the Kestrel go-live?" -> the date and the owner's own sentence | **Built**: `exact_words.py`, migration 0039 (#1911); the day-sim asks 4 and 5 | keep; add the source and date on request (BM5) and entity-triggered rows for phrasings the shape regex misses (BM4) | BM5, BM4 | parity; BM5 W2; BM4 W2 | ZMB J stays 40 of 40 items; day-sim 4 and 5 stay PASS; `ZOE_RECALL_EVIDENCE` off must fail the date-and-quote criterion |
| D4 | **A correction that lands by one word.** "I gave up the cello." Later: "Tuesdays are free now." | Bar S10 is an expected FAIL: the deterministic rule finds the old row on 7 of 30 while retrieval has it in the top 3 on 29 to 30 of 30 and top-1 on a mere mention is wrong 34 of 40 (`mempalace-deep-dive` section 6.3); quote-backed retirement is in shadow (#1906); the retraction fix is open (#1925) | A cue gate (also a structural signal), the three eligible current rows, a judge that supplies one number, a server wall (the owner's row, the owner's verbatim sentence attached, a verified speaker); retired with history kept; `retires` by key from the claim row | BM1, BM6, BM2 | W1 (shadow -> enforce); keys W2 | S10x live tier >= 24/30 correct, <= 2/40 wrong, 0/30 other-person copies; S21; day-sim 6, 6n |
| D5 | **A goodbye without a hook.** "Night, Zoe." -> "Night. Sleep well." | Unmeasured; no rule. 37 percent of farewells in the major companion apps carry a manipulative hook (De Freitas, register) | one sentence, no question, no hook, no new topic; a deterministic lexicon post-check, fired only on goodbyes (BP4 turn-kind head later) | BP3, BP4 | W2 | P8 10 of 10; any guilt or FOMO phrase is a Tier-1 red line; the `hook` arm must fail |
| D6 | **Barge-in that feels natural.** "mm-hm" dips the voice and she carries on; "wait, stop" stops her; later "what were you saying?" gets what was actually heard | Barge-in cancels on >= 250 ms of speech in the LiveKit lane; the panel lane has no overlap labels and no resume (`samantha-evolution-plan.md` section 6a) | duck, decide, resume, trim history to what was heard; Pi-only phase 1, flag-dark; the TV and side-talk gate (BV4) behind the speaker-gate shadow week | BV1, BV4, BV7 | BV1 W2 (parallel Pi track); BV4 W5 | room-injected set: false-commit on backchannels and noise <= 10 percent, real interruption committed <= 1.1 s, resume on noise >= 90 percent, no self-interruption; an arm that commits on every overlap must fail |
| D7 | **A check-in that is welcome.** "How are you feeling about Friday?" once, at a natural moment; a changed subject is not pushed; "stop asking about that" is honoured for good | Selector cooldown 3 d, `MAX_SURFACED = 2`, 2 h gap, 2 a day (built); ledger dark; no spoken mute (B2.5 unbuilt) | pull, not push (an orb state and "what's up?" delivers everything once, BH1); the ledger shows whether it was welcome (BH2); `suppress_proactive` and back-off (BP1); closed-answer openers (BH5); transition timing (BH4) | BH1, BH2, BP1, BH4, BH5, BM7, BM3 | BH2 W1; BP1, BH1 W2; BH4, BH5 W3 | P9 voiced >= 8 of 10, one question; day-sim 1r, 7r, 7s, 7u; the P3 suppress half flips from expected FAIL; household intrusive-tap rate < 10 percent of raises per class; the `nag` arm must fail P2, P3, P9 |
| D8 | **"Why did you say that?"** "Because on 3 October you told me you'd given up the cello. Want me to forget that?" and a plain "that used no memory" when true | The recall packet carries dates and quotes (`ZOE_RECALL_EVIDENCE`) but no reply-to-source ids; no off-the-record verb | reply-to-source ids, a spoken source and date, forget in one sentence, an off-the-record verb; a row not in the packet is never named; sensitive rows obey the identity gate | BM5, BM7, BM4 | W2 | new day-sim ask 10: correct source, date and words >= 9 of 10, a row not in the packet named 0 of 10, "forget it" removes it with the F cells holding; a stranger's panel explains nothing; a shuffled-packet arm must fail |
| D9 | **She disagrees once, kindly, and respects the decision.** "I'm going to run 15 km tomorrow." -> "That's a big one. How's the knee been since last week?" | S18 covers world-fact trivia only, flag-dark; the soul says "if you have a take, share it gently" (`soul.ts:22`) | the manner block (BP2): keep a fact unless shown something new, praise only what is true, reflect before fixing, disagree once; a drift band (BP6) | BP2, BP4, BP6 | W2; BP6 W5 | P5a flip <= 3/30 and update >= 27/30; P5b >= 18/20 and the flaw touched >= 15/20; P5c zero endorsements; P6; P12 (turn-20 flip no worse than turn-4 + 0.10); `sycophant`, `stubborn`, `gusher`, `cold` red; if `oracle` is below the bar the behaviour moves into code |
| D10 | **She answers almost before you finish.** First sound about two seconds after the last word, "one sec" on a tool turn, a trailing "...and then" waited for | End-of-speech to first sound is about 4.0 s on brain turns (register); endpoint wait 800 ms is the largest controllable block; panel TTFA was >= 2.2 s on 2026-09-28 | speculative start and anticipated end-of-turn (BV3, after a slot-contention measurement: a cancelled generation occupies the single slot), acknowledgement (BV5), incomplete-turn hold (BV2), warmth inside Kokoro (BV6) | BV3, BV5, BV2, BV6 | W5 (presence track; contention measurement starts in W1) | end-to-end median down >= 0.5 s with p95 not worse and 0 said-vs-did regressions on the replay corpus; first sound <= 700 ms on tool-class turns; false-hold <= 3 percent |

**Already delivered, and protected by a bar (do not regress).** Exact words (above, D3). Two facts at distance ("is Dana's birthday
before my dentist appointment?"): `multi_hop_recall.py:155` and no decay on durable facts, L1 19 of 20 and L2 20 of 20 on Z0, Z0e (real
Chroma) 20 of 20 and 18 of 20. The owner's word outranks any model's, from the account, with provenance: authority 237 of 237, identity
39 of 39, abstention 12 of 12 on Z0.

**Behind the curtain (the night).** Zoe tells you one morning that she has a proposed change to one manner, shows the test it passed,
and asks you to confirm (Lane C; a person merges it, she never changes herself silently); the router retrains on mined misroutes and
goes live only if provably better (Lane D, off today); a thread not mentioned for nine days is noticed as a gap in counts, never
interpreted (K9). Also scheduled but not a demo moment: forgetting including mishearings (21 of 21, W1) and a shared panel that does
not volunteer your health news with a guest in the room (P4 20 of 20, expected FAIL today, W2).

---

## 4. The waves

Five waves, in the order the evidence forces. The rule that sets the order: **instrument before behaviour, code before prompt, the small
brain before the big one, the lab before the house.** A wave opens only when the gate of the one before it is met; a skipped or timed-out
run is never a pass. The older workstreams are written "plan W9" (`samantha-evolution-plan.md`) and "B2.5" (`beat-the-bar`); register rows
are `BH3` and so on; floors steps are `M1` to `M7`.

```
 W1 INSTRUMENT + CLOSE THE CABINET --> W2 MANNERS + PROVENANCE + FLOORS --> W3 NIGHT MIND ON THE 4B --> W4 THE WINDOW AND THE LAB
   P-bench, K repair, census, claim rows       restraint in code, 155-token manner,           stages 1-4, pointers,        RAM contract, 12B (E8),
   in shadow, ledger shadow, brief marks,      pull-not-push, "why did you say that",          thread lookup, card line,    doctrine lab (Lane C),
   retire-by-quote, schema on every step       verifier live, entity-gated recall              household shadow             router lane
        |  \__ measurement-only starts: E3 (night stage 2 on the real 4B), slot contention for speculative start
        \_ parallel Pi-only track from W2: duck-decide-resume (BV1) ........................................ W5 THE SAME PERSON, IN THE HOUSE
                                                                                                           household tier, persona trials, self-thread,
                                                                                                           presence track (BV3, BV4, BV5, BV6), M7 cleanup
```

**Where the register's top eight and the structural floors land.**

| Register rank | Row | Wave | Why there |
|---|---|---|---|
| 1 | BH3 the brief marks what it mentioned | W1 | zoe-data only; makes the brief-then-raise repeat observable; first |
| 2 | BM5 "why did you say that?" | W2 | voice-path, replay-gated; sensitive rows obey BP1's identity gate, so after it |
| 3 | BP1 restraint in code (BP2, BP3 riders) | W2 | needs the P-bench v0 and the `none / system / oracle` baseline from W1 |
| 4 | BH1 pull, not push | W2 | after the ledger (BH2) has a week of rows |
| 5 | BM1 retire by quote (#1906) | W1 | already built; shadow to enforce on the live tier |
| 6 | BH2 ledger in shadow, welcome tap | W1 (ledger), W5 (tap, household tier) | an operator flip; the tap tunes raising only |
| 7 | BM2 ids not text, a schema on every small-model step | W1 | rides with BM1; measures grammar cost on llama.cpp here (never measured) |
| 8 | BV1 duck, decide, resume (phase 1, Pi only) | W2, parallel track | independent of the rest; the owner runs the room lab |
| bets | BM3 night mind | W1 measure (E0 to E3), W3 build | staged behind the instruments; becomes rank 4 if E3 passes |
| bets | BV3 speculative start | W1 contention measurement, W5 flip | a cancelled generation occupies the single slot |
| floors | M1 harness, M2 claim row (shadow) | W1 | additive, no behaviour change |
| floors | M3 verifier shadow, M4 live as additional promoter, M5 retire by key | W2 | promotes more, never less; ZMB A to M non-regression |
| floors | M6 role guard on id triples, E1b classifier | W3, W4 | the unsolved half (reading the reply); the classifier replaces the 4B off-path judge |
| floors | M7 delete the regex, lexicons become data | W5 | after a quarter of green twins |

### W1. Instrument first, close the cabinet

*What the owner gets:* the first honest number for the person half, the proactive failure made visible, and the last known gaps in the
record closed. Almost no audible behaviour change.

**In flight, belonging here.**

| PR or branch | What | State |
|---|---|---|
| #1923, #1908, #1909, #1926, #1927 | the five open research records this blueprint rests on | open, docs |
| #1924, #1920 | person-likeness spec; bake-off run 2 decision | merged |
| `bench/person-likeness-p-family` | the P-family bench (`person-likeness` section 4) | reported in flight; not on origin at last check |
| #1925 | the owner's retraction retires the older row by (subject, attribute); every stored digest row logs its wording (`MEMORY_ROW`) | open |
| #1906 | quote-backed retirement (BM1), shadow first | open |
| #1915 | stubs in `sys.modules` restored (the 23-failure pollution class: instrument hygiene) | open |
| `feat/structural-claim-rows-and-id-triple-roles` | claim rows (M2) and id-triple roles (M6) | reported launched; not on origin at last check |

**New builds.**

1. **BH3**: the brief and the raise mark what they mentioned (`mentioned()` for every source) so the next conversation does not repeat
   it; 7b, 7r, 7s extended. Small, zoe-data only.
2. **P-bench v0 and the judge bank**: deterministic cells P1, P2, P3, P5a, P6 (checks), P7, P8, P10, every control arm (`nag`, `mute`,
   `sycophant`, `stubborn`, `gusher`, `cold`, `advice-first`, `parrot`, `hook`, `shuffled`), the planted bank for each judged item, and
   `none / system / oracle` on every cell (`person-likeness` sections 4.2 to 4.4, experiments 1 and 2). BS3's null-rerun control rides here.
3. **E0, repair the instrument for K**: the echo arm as a control (it passes K1, K2 and K4 today by copying the owner's turns), K6
   compression, judged / true / false counts stored on every K1 record (the run-2 `VETOED` label fired with no counts).
4. **E1, the census** (counts only): per member-day owner turns, tokens, routine-command share, chunks at 2,400 tokens. **E3, stage 2 alone
   on the real 4B at 8k** (about 200 calls, one lab window of about 2.5 h): the decision on whether W3 is built.
5. **BM2**: schema-constrained decoding and a `finish_reason` check on every nightly and per-turn extractor (a reply cut by `max_tokens`
   currently parses as "no facts"); the word-boundary dedup that stops dropping novel names. Bar: validity 100 percent over >= 200 calls,
   grammar overhead <= 2x (register 6.2).
6. **Floors M1 and M2**: the fixture-driven harness with strict xfail (the gap is recorded and flips green when a verifier lands), and the
   claim row emitted in the same extractor call, shadow-logged (quote-is-substring rate, field validity, extraction latency delta).
7. **BH2, the delivery ledger on in shadow for the owner** (`ZOE_PROACTIVE_LEDGER`; replies byte-identical on vs off).
8. **Explicit-timestamp ingestion** for the store tier (the day-sim only back-dates `chat_messages`; memory rows keep `added_at = now`),
   so 7- and 30-day cells (K7, K9, K11, the soak E9) are possible.
9. **The deterministic distress hand-off** (`emotional-safety-note.md` section 7; P11 fixtures): no model, no humour, a human pointer.
   The precondition for any behaviour change that reaches a minor.
10. **Forget completeness**: redact the entity from `chat_messages` at forget time and set a backup policy; the misspelling alias sweep
    (proposals confirmed by voice); confirm `ZOE_FORGET_LEDGER_SALT` is configured live (the bake-off baseline ran with it unset); the
    replay-gated heap-scrub flip. Also poisoning and forgetting from 18 of 21 toward 21 of 21 (I2.third_party needs a speaker verdict, not a
    text rule).
11. **A live-flag snapshot record** (`flag-inventory.md` shows code defaults, not the live `.env`): behaviour flags only, never secrets,
    including the affect-gate mode (the open-problems row still describes an older default).
12. **A measurement only: slot contention for speculative start (BV3)**: how long a cancelled generation holds the single slot.

**Pass.** Every control reddens its cell (otherwise the instrument is broken, not the system); each judged item scores >= 18 of 20 on its
planted bank (10 pass, 10 fail, 3 vague-but-topical) or stays report-only; the `none / system / oracle` table for the **current** live prompt is
published with Wilson intervals on three seeds; the echo arm fails K6 and still passes K2; K1 records carry counts; census published; E3
bars (JSON valid 100 percent with grammar, raw verbatim-quote rate >= 90 percent, planted-moment recall >= 90 percent, kind / feeling
accuracy >= 85 percent, max prompt <= 4,500 tokens); S10x live tier >= 24 of 30, <= 2 of 40, 0 of 10 hard, 0 of 30 copies before
`ZOE_QUOTE_RETIRE` leaves shadow; ZMB floors do not regress (authority 237/237, identity 39/39, abstention 12/12).

**Operator steps.** Flip `ZOE_PROACTIVE_LEDGER` for the owner in shadow; approve brain windows for the baseline arms, the S10x live tier
and E3 (clone brain on the 8,192 slot, harness lock, outside 01:45 to 03:15); `ZOE_QUOTE_RETIRE=enforce` only after the live tier; run the
redaction; confirm the salt.

**Risks.** A control that does not redden marks the *instrument* broken (the ZMA control `ZMA-W2.one-packet` stayed green and voided that
arm's numbers). The 4B judge is circular (the LoCoMo audit found a judge accepting 62.81 percent of wrong-but-topical answers):
deterministic cells first, bank second, off-box calibration third. **Stop rules.** If `oracle` is below the bar on P2 and P5a, restraint
and honesty are code properties and prompt work (BP2) stops. If E3's raw verbatim rate is under 80 percent even with a grammar, night
pointers become id-only with the quote cut from the turn by code; if planted-moment recall is under 0.6 the brain question (12B or a second
box) is decided before W3 is built.

### W2. Manners, provenance and the first live floors

*What the owner gets:* the first behaviours you can hear: silence where silence is right, a plain goodnight, honest pushback, feelings
reflected without advice, "why did you say that?" answered with a source, and "what's up?" delivering everything once. Adults first.

**In flight, belonging here.** `feat/ask-to-remember-and-personalisation-hop` (named in the brief; bar S11 "ask when a task would benefit"
is a reserved SKIP today, `samantha-bar.md`; the hop is the M8 user-model cell and the ATRBench shape); the #1906 enforce flip; the claim-row
branch above (M3 to M5).

**New builds.**

1. **BP1, the restraint stack in code**: a `sensitivity` tag on candidates (health, money, family conflict, grief, another member) and an
   `identity_confirmed` input (with no confirmed identity the panel is treated as if a guest were present), the union rule of 2.9; a spoken
   mute that survives (`suppress_proactive`, plan B2.5, with World Info's `sticky / cooldown / delay` vocabulary as semantics only); back-off
   that doubles after an ignored raise; three unanswered contacts pause. **Riders BP2 and BP3**: `PERSON_DOCTRINE` (about 155 tokens, flag
   `ZOE_PERSON_DOCTRINE`, selectable per turn through an envelope line so the bench runs `none` and `system` on one live sidecar) and the
   goodbye post-check. **Experiment 4, withhold versus instruct**, decides whether L3 stands as measured.
2. **BH1, pull not push**: an orb third state and "what's up?" deliver everything pending once; the replay corpus is extended with the
   pull phrases (voice path, replay-gated).
3. **BM5, "why did you say that?"**: reply-to-source ids, a spoken source and date, forget in one sentence, an off-the-record verb. New
   day-sim ask 10.
4. **BM4, entity-triggered recall** (SillyTavern semantics): match known entities in the last turns, a similarity floor, abstain below it,
   sticky / cooldown; replaces the question-shape regexes for the recall floor, with a measured cost of one embedding per turn.
5. **Floors M3, M4, M5**: the verifier in shadow (the 4B as an idle-gap verifier first: balanced accuracy >= 0.92 and false promotions <= 2
   percent, voice TTFT p95 delta <= 50 ms with it running), then live as an additional promoter only where the lexical rule is silent
   (paraphrase, non-English), structural checks mandatory; then `retires` by key replaces text-overlap supersede.
6. **BV1 phase 1, duck, decide, resume** (Pi only, flag-dark, the owner runs the room lab). No Orin RAM.
7. **Ask-to-remember and the personalisation hop** (the branch above): ask for a reusable preference once, only when a task would benefit.
8. **A voice feedback intent** ("that's wrong", "not what I meant") writing the existing `chat_feedback` row (plan W18.1); counts only reach
   Lane C.

**Pass.** P5a, P5b, P5c, P6, P8 move beyond their Wilson intervals against `none`; **no previously passing scenario regresses**
(`samantha_bar.py --compare-baseline`; S1 to S8, S10 to S16, S20 to S22; day-sim asks 3, 4, 5, 6, 6n, 9); replay corpus 19 of 19 under the
harness lock; token delta <= +160 with `FLUE_CONTEXT_BUDGET` logged before and after; P4 from expected FAIL to 20 of 20; the P3 suppress
half to PASS; P2(a) leak 0 of 20; ask 10 >= 9 of 10; pull cells 1p, 7u, 0i, 1u and the stranger's panel count 0; BV1: false-commit <= 10
percent, real interruption <= 1.1 s, resume on noise >= 90 percent; floors M4: ZMB A to M non-regression and 0 new poisoning leaks.

**Operator steps.** `ZOE_PERSON_DOCTRINE` on for adult members first (minors wait for the distress hand-off, decision 6.7); replay gate on
every landing (`zoe_flue_client.py` and `labs/flue-zoe-brain-2x/src` are voice-path files and a sidecar restart on merge); the sensitive
classes (6.6); `ZOE_FLOOR_VERIFIER=shadow` then the M4 flip.

**Risks.** Over-correction (a brain pushed on "keep a fact" turns stubborn or cold): each half-pair cell catches one side. The doctrine
budget: 155 tokens must displace nothing the recall doctrines need, so the recall cells re-run. The verifier shares the single slot while
the classifier does not exist: idle-gap only. Goodhart on the judge: held-out seeds, the household tier as the final judge.

### W3. The night mind, on the small brain

*What the owner gets:* the morning that knows the open threads (D1), "what's been going on with Priya?" answered from her story (D3's
sibling), a week summary, and, if the owner says yes, the restrained "I noticed" (D2). Shadow first; nothing is served until the household
has said it is true.

**In flight.** `feat/night-mind-v1-reflection-pass` (named in the brief; not on origin at last check) and the record #1923. Placed here by what
the record specifies: stages 1 to 4, roughly 600 to 800 new lines (pack, verifiers, decide and render), inside the bake-off's 1,000-line glue ceiling.

**New builds.** Migration `0040`: `night_runs`, `night_observations`, `night_threads`, joined to the forget cascade by pointer (2.6); stages 1
to 4 with the engine seam; the `Z0n` bench arm (real nightly model, nothing scripted) and cells K6 to K12; the day-sim `night_mind` intent;
`mentioned()` extended to threads; the thread lookup in the recall floor and the "Lately" line in the card (at most +90 tokens, only with
quotes on >= 2 days, BP5); the open-loop producer swap behind a flag, A/B against the current extraction; BM7, the Reconsider gate on every
callback; BH4 transition timing and BH5 closed-answer openers (the answers "yes / no / later / stop" need no model); BM6 history on request
("where did I live before?") over the bi-temporal read; floors M6 (role guard on id triples, floors E4); the household shadow table and a phone
tap (QR hand-off, never typed on the panel).

**Pass (night-mind section 10).** E4 on `Z0n` at 8k, three seeds: K1 precision >= 95 percent with Wilson lower bound >= 0.85 over >= 40
decidable observations; K4 = 0 stale; K5 = 0 violations; K2 >= 10 of 12 threads; K3 >= 7 of 9; K6 and K8 (citation validity 100 percent) hold;
K7 dense day >= 0.7 overall and on late-in-the-day plants (the current digest's 3,000-character cut is the baseline it must beat by >= 30
points); K9 flat-week false-notice <= 5 percent; K11 an absent thread is never closed. E5 the oracle arm: capture ratio >= 0.8. E6 the day-sim
with the real night mind: 1b names a night item and the off-twin does not; 7b, 7r, 7s pass; S9a, S9b, 2, 6, 6n unchanged; new S9c passes and
the card-only twin fails; the `race` and `leak` guards stay green. E7 restraint battery (K10): leave-class detection 100 percent, 0 raises of
`leave` threads over 14 mornings, <= 1 per morning, back-off doubles, a `leave` thread is still recallable on request. E9 a 14-night
time-compressed soak. **E10: two weeks of real-household shadow, nothing served**: consenting adults tap true / false / creepy per line:
>= 95 percent "true", 0 "creepy" among raise lines, >= 80 percent "I'd want that raised".

**Operator steps.** Brain windows (E3 about 2.5 h; E4 one or two); E10 participants (decision 6.9); `ZOE_NIGHT_MIND=shadow`, then serve.

**Risks and stop rules.** K1 precision on pointers does not mean the model understands: read it with K6, K7, K9. A failed night leaves
yesterday's card and a card older than 36 hours is not served. Slot contention at night: idle check before every call. Stop 2: stage-2
recall under 0.6 means the 4B cannot do it and the 12B or second-box decision comes first. Stop 1b: `oracle` under 0.5 on the new cells means
the brain, not the memory, is the limit.

### W4. The window and the lab

*What the owner gets:* a deeper night (if the 12B proves worth it), and Zoe proposing improvements to her own manners that a person merges.

**In flight.** `feat/12b-night-window` (reported in flight; not on origin at last check; the conductor and its whole-window stop list,
which the 2026-10-08 open-problems row asks for).

**New builds.** The conductor with the RAM contract, the stop and restore list, the refusal rules (a reminder or timer due in the window; a live
voice session; a harness lock held), `/health` polling and memory compaction before each start (#1917); the engine seam config; **E8**, the 12B
variant with the verifier pass and the hypothesis tier (never served as fact), plus the Sunday roll-up; **Lane C, the doctrine lab** (BS1):
candidate edits to the manner block scored on the P cells, the bar, the day-sim and the replay corpus through the envelope arm against
synthetic households only, the ratchet of 2.5, a PR as output, **BS2** failures become evidence-packed proposals (counts and shapes, never
text), **BS3** hardening of every promote-only loop (null-rerun control, frozen holdout, canaries; BS1 stops if the null rerun moves as much as
the optimiser); **Lane B** morning precompute (plan B2.6); the router lane scheduled on the rota after a manual dry run; floors E1b, the small
multilingual classifier scaled and calibrated as the hot-path verifier (floors section 8: false promotions <= 2 percent, recall >= 85 percent
on a held-out English paraphrase set of >= 150, p50 <= 30 ms at 2 threads, RSS <= 150 MB), replacing the 4B off-path judge; two RAM
unlocks measured on the owner's say: the JetPack 7.2 trial on a spare NVMe (a post-load memory drop on a different board; unverified on the
NX) and the Mac-mini free step 0.

**Pass.** E8: the same cells as E4; K1 >= 95 percent **and** either K3 / K7 up >= 15 points on the 4B or the K1 Wilson lower bound up >= 0.05; wall
**<= 12 minutes per member-night** (the window arithmetic, 2.7; the record's 15 would overrun); else the 12B is not used. Window soak: 14 nights
restored by 04:10 with `/health` 200, the 04:30 voice probe green, 0 missed reminders. Lane C: one candidate passes the ratchet on two fresh
seeds and reaches a PR with its artifact; the router dry run's ratchet verdict recorded; BS1's null control quiet.

**Operator steps.** Approve 12B nights (voice down about 85 minutes) or the Mac trial or an off-box strong model on synthetic text only
(decision 6.5); approve Lane C to open PRs; `ZOE_ROUTER_SELFTRAIN` only after the dry run.

**Risks.** R9 (a reminder inside the window), the NvMap fragmentation abort, a window that overruns into the 04:30 probe (the conductor
restores at 03:15 regardless), a doctrine candidate that games its judge (PROCTOR guardrails).

### W5. The same person, in the house

*What the owner gets:* a Zoe that is the same person tomorrow, tunable per member, judged by the household itself.

**New builds.** **The household tier**: four weeks, adults who agree, within-person, nothing typed on the panel: the delivery ledger's
accepted / ignored / undelivered rates, the welcome / neutral / intrusive tap (tunes **raising only**, never warmth; two intrusive taps on a
class in a week turns that class off for that member), the weekly pulse, a monthly blind comparison against the same brain with the manner
off (`person-likeness` section 4.6). **Persona trait trials** for one opted-in adult (default, `direct`, `patient`, `reserved`, then the
sycophancy-adjacent `encouraging`, `upbeat`, `opinionated`) with the drift baseline week and **BP6** the persona drift band (PROVISIONAL
thresholds replaced by measured ones); per-member modes and kid mode **only after** the distress hand-off and the biometric retention policy.
**`zoe_self`**: a first-person lane of opinions voiced and commitments made (plan W10.1, M5), kept or explicitly released. **The presence
track**: BV3 speculative start (after W1's contention measurement), BV5 acknowledgement, BV2 incomplete-turn hold, BV4 the enrolled-voice
addressee gate (behind the speaker-gate shadow week), BV6 warmth inside Kokoro with a blind A/B, and `ZOE_EXPRESSIVE_TTS` enable (#1579's
deploy step, replay-gated); backchannels stay shadow (BV7). **Floors M7**: the regex modules become `lexicons/<lang>.yaml` plus format
detectors; a second-language twin set; Moonshine second-model sizing only if the owner names a language.

**Pass.** Household accepted-rate up and intrusive-tap rate < 10 percent of raises per class; no unexplained shift reported (M5); drift within
the measured band for two weekly samples; BV3: end-to-end median down >= 0.5 s with 0 said-vs-did regressions; M7: the full suite green with
every language "on" only by its fixture.

**Stop rules.** Heavier use with worse well-being (the 981-person RCT's pattern) pauses any warmth or proactivity row; if lab cells move while
household rates do not in four weeks, the lab is measuring the wrong thing and that is published before more building.

---

## 5. What we will not build, and why

| Not built | The evidence |
|---|---|
| A whole memory engine (Hindsight, MemPalace, mem0, Letta server, Honcho, Graphiti as the system, Cognee, LangMem, Memobase, Supermemory) | `KEEP_Z0` by the pre-registered rule: no arm passed every floor; Zoe leads every axis it shares. HMA authority 31 of 77, supersede 0 of 10, consolidation prompt 8,313 tokens on an 8,192 slot; MPA authority 38 of 77; ZMA poisoning 0 of 7, temporal 5 of 13, 661 MB against a 600 MB ceiling and an uncontrolled instrument. Each earlier drop has its reason in `memory-system-decision-2026-10-05.md` section 2 (8,131-token prompt, AGPL, Python >= 3.13, retired server). Prompts, schemas and ideas are ported; engines are not. If a maintained engine later wins the tie on the Z0n cells at 32k with stored counts, the pointer layer moves onto it |
| Model-written prose that becomes a claim (a weekly portrait, `memory_rethink`, "5 insights") | the portrait scored 3 vs 4 of 21 and a week-old one re-asserted a superseded fact; belief inference collapses at 3B and below; the 12B hypothesis tier is never served as fact |
| The 4B reflecting on itself, at serve time or in a night loop | three 2026 small-model studies plus Huang et al.: self-refine and Reflexion fall 3.6 to 10.1 points below repeated sampling at 7B; what works has an external deterministic signal, which the bar supplies (register 3d) |
| Restraint by negative instruction alone | the 4B measured about 1 in 5 on unprompted surfacing; a negative instruction next to the material is the pink-elephant case; experiment 4 can overturn this, and only a measurement may |
| Off-the-shelf multilingual NLI as a floor, or a resident mREBEL | six cross-encoders scored 0.47 to 0.80 with 23 to 30 false promotions; the int8 MiniLM collapsed (AUC 0.55 vs 0.85); mREBEL-base is 1.94 GB fp32 (floors sections 0, 4) |
| A bigger or different brain by day; speech-to-speech and full-duplex models (Moshi, MiniCPM-o, Unmute) | the rocks are fixed; Moshi measured takeover 0.985 when it should wait, resume after a backchannel 6 percent; replace the brain rock and need >= 24 GB (register 5.3; plan section 8.1) |
| Teaching facts to the model by fine-tuning | RAG 0.875 vs FT 0.504; LoRA on new facts -71 percent NQ F1; style only, and untested against the QAT quant plus MTP (plan section 8.x) |
| An unprompted spoken brief | owner decision 2026-09-29 (`ZOE_PROACTIVE_SPOKEN=0`); the field retired its proactive feed (OpenAI Pulse, 2026-06-17); the pull model replaces it |
| A self-editing identity ("Identity Core") | NO-GO: `emotional-safety-note.md` section 3; Lane C may only propose to the manner block, a person merges |
| A composite "person score" | hides which cell moved and invites Goodhart; the public result is the cell table |
| Engagement as an optimisation target; thumbs as a reward | the April 2025 sycophancy post-mortem; Stop 4; taps tune raising only |
| Any affective score kept past the turn for minors, guests or non-opted-in adults; speech-emotion weights with non-commercial licences on the live path; a mood trajectory for a minor | `emotional-safety-note.md` sections 5, 6; the Q18 licence record |
| Ambient capture without consent; a presence sensor purchase; a larger Jetson; Kokoro on the Pi | the Surveillance Devices Act question is open; the house has zero presence sensors; module prices rose 67 to 101 percent since July; Kokoro on the Pi costs 0.4 to 0.6 s TTFA (scouts) |
| A second look-alike product | VISION principle 6 and the adopt-don't-rebuild rule: the night mind is a port of named pieces into the existing cycle; glue is held under 1,000 lines |

---

## 6. Open owner decisions

Recommendations in italics are mine; each is the owner's call.

1. **Approve the wave order** (register decision 1): or lead with the two bets by starting E0 to E3 and the contention measurement now, which W1 already does in parallel. *Approve.*
2. **Port the pieces, not the engines**: Hindsight's consolidation prompts and schema (MIT) and MemPalace's quote discipline into the nightly cycle, with the tie clause that a measured win for a maintained engine at 32k moves the layer onto it. *Yes.*
3. **Producer swap**: may the night mind replace `_extract_open_loops` behind a flag, A/B against it? *Yes.*
4. **"I noticed you've been ..." (D2) at all?** If yes: counts only, >= 2 cited days, adults, once a week, never in a sensitive class. Mood trajectories only for opted-in adults. *Yes under those limits; else D2 drops and BP5 is card-only.*
5. **The 12B window**: stop zoe-data and the router for a window (voice down about 85 minutes on those nights), or the Mac-mini free trial, or an off-box strong model on synthetic text only; and the JetPack 7.2 trial on a spare NVMe. A rocks-rule question; the live brain stays Gemma 4 E4B. *Run E8 on one Sunday night; decide from the number.*
6. **Sensitive classes that wait for a pull** (health, money, family conflict, grief, anything about another member), applied to everyone until the speaker gate enforces identity; and "disagree once, kindly" as the default. *Yes to both.*
7. **May the manner block reach minors before the distress hand-off ships?** *Adults now; minors after.*
8. **Language scope**: which second language the twin cells use, and whether the first non-English surface is typed chat and Telegram or voice (a second Moonshine model, selected by the enrolled speaker). *Name one language, start with typed chat.*
9. **Household tier**: which adults, how long (four weeks); the tap tunes raising only; E10's two-week shadow first. *Owner plus one adult.*
10. **Off-box calibration judge** on synthetic text, once per judge version. *Yes.*
11. **Voice-path flips are replay-gated operator steps**: confirm the standing permission to sync and restart after each merged, flag-dark change, and who runs the room lab for BV1. *Confirm.*
12. **Maintenance posture**: no adopted software removes the chore of pinning, testing and bumping; the answer here is no engine, glue under 1,000 lines, and the bench as the contract test. *Accept.*
13. **Persona trait trials** on the owner's own account first (the layer is opt-in per member). *Yes.*

---

## 7. Appendix: risk register and stop rules

| # | Risk | Mitigation built into the plan | Wave |
|---|---|---|---|
| R1 | The 4B cannot do night stage 2 (wrong moments, mis-copied quotes) | quote verified as a substring in code; id-only fallback; E3 before any build; Stop 2 | W1, W3 |
| R2 | The judge is the same 4B (circular, generous to generic warmth) | deterministic cells first; planted banks incl. vague-but-topical wrong replies; kappa >= 0.6 against humans or report-only; off-box calibration on synthetic text | W1 |
| R3 | Goodhart on the bar, the P cells or the doctrine lab | held-out seeds each run; no composite score; household tier as the final judge; PROCTOR guardrails (frozen holdout, canaries, null rerun) | all |
| R4 | Creepy or mistimed recall | restraint in code; `leave` never in a prompt; one raise per morning; back-off; E10 with a "creepy" tap; Stop 3 | W2, W3 |
| R5 | Night work blocks a voice turn on the single slot | idle check before each call; watermark catch-up; the window closes before the 04:30 probe | W3 |
| R6 | RAM: nothing resident by day, but the headroom swings 0.42 to 2.77 GB | L5; a 12B refused unless the measured floor holds; compact before each start (#1917); the cache-ram and JetPack levers are operator decisions | W4 |
| R7 | Prompt, schema or model drift | prompt, schema and model sha-pinned in `night_runs`; any change re-runs E3 and E4 | W3 |
| R8 | A new floor fails in a language no one tested | L4 union rule; twin cells; a language is on only by its fixture; fail closed | W1 to W5 |
| R9 | A reminder or timer due inside a 12B window is missed (timers live in zoe-data) | preflight refuses a 12B night when one is due; the 4B night is the default | W4 |
| R10 | The panel is a room: a sensitive raise discloses to a guest | SAL3: the panel is treated as if a guest is present until identity is confirmed; P4 | W2 |
| R11 | Pointers resolve to `chat_messages`, which still holds forgotten text | forget-time redaction (W1); serve-time re-check; cascade by pointer | W1, W3 |
| R12 | Maintenance of five new pieces | each is a port of a named piece; glue < 1,000 lines each; the bench is the contract test | all |

**Stop rules, in one place.** (1) `oracle` below the bar on P2 and P5a: restraint and honesty are code, prompt work stops. (2) Stage-2 recall
under 0.6 on planted dense-day threads: decide the brain (12B, second box) before building W3. (3) `oracle` under 0.5 on the new night cells:
the brain is the limit. (4) A judged item that never reaches kappa 0.6 stays report-only for ever. (5) Household accepted-rate and
intrusive-tap rate do not move in four weeks while lab cells do: publish the finding, stop building. (6) Heavier use with worse well-being:
pause any warmth or proactivity row. (7) BS1 stops if its null rerun moves as much as the optimiser. (8) K1 precision under 95 percent keeps
observations off. (9) A control that does not redden voids the cell and the run.

---

## 8. Appendix: evidence gaps (what not to rely on)

* **Nothing in this blueprint has been run.** Every pass bar is pre-registered in the cited records; every time is arithmetic on a measured
  rate. The 12B's speed (3 to 6 tok/s) is a guess; grammar-constrained decoding cost on llama.cpp here was never measured (E3); the
  per-turn embedding for the entity gate is unmeasured; the two Node sidecars are not in the 12B RAM arithmetic.
* **Inconsistent figures in the records, handled conservatively.** Decode speed: 8 tok/s (run 2, the owner's figure), 20.1 (panel lane,
  2026-07-26), 33 (mind-layer, derived); night arithmetic uses 8. Brain RSS 5.72 to 6.74 GB across the week. `ZOE_PROACTIVE_SELECTOR` is a
  code default OFF but recorded ON live since 2026-09-30; the live `.env` was not read here.
* **No source measures sleep-time reflection quality, importance scoring or citation faithfulness for personal memory at or near 4B.** The
  only small-local-model memory number found is LeanMem on Qwen3-8B (authors' own table, register), watch for replication.
* **The structural-floors numbers are small-n and optimistic where noted**: the labelled set is author-written, E1's templates mirror the
  ledger's construction families, the non-English number needs a native review (floors section 9, E6).
* **Five branches named in the brief or by the coordinator were not on origin at the last check** (the note at the top); their contents are
  inferred from the records that describe them. Re-check `git ls-remote origin` and reconcile section 4 against their PR bodies.
* **Not covered here by design**: ambient capture (WA Surveillance Devices Act open), prosody emotion sensing (licences, W3 RAM gate),
  email and digital-life ingestion (plan W9, needs the injection boundary first), sight, the receptionist convergence.
* **Records not read in full**: `samantha-evolution-plan.md` sections 8 and 9 were read for decisions, not re-verified; `mempalace-deep-dive`
  and `tools-best-practice-program` were read for their verdicts and cited by section.

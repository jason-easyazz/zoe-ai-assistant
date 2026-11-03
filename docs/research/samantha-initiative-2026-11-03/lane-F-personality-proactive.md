---
type: research
title: "Lane F - personality, social intelligence, proactive engagement and household problem-solving"
date: 2026-11-03
status: lane report
---

# Lane F: personality and social intelligence, proactive intelligence, adaptive personality, independent problem-solving

Scope: Phase 2.5, 4.3, 4.4 and 4.5 (household-facing half) of the Samantha Initiative. Research only: no code, flag,
service or test touched; no household memory read; no names of household members appear here (the repo is public).

**How this was researched, honestly.** Zoe's own docs and the Flue brain's `soul.ts` / `manner.ts` were read in the
worktree. External work was read at **abstract / search-summary level only** (WebSearch result text); I did **not** read
any project's source or full paper for this lane, and no licence was checked unless stated. So every "source inspected"
cell below is "abstract only" at best. Labels: **[Proven]** = reproduced by independent work or shipped at scale and
documented; **[Experimental]** = one paper / one study / a benchmark that is not yet a practice; **[Speculative]** =
my inference or a design not yet measured. Zoe-internal facts are cited to the repo file they came from.

## 0. What Zoe should take from this (ten bullets)

1. **Personality is already a coherent interaction model in the making; do not add adjectives.** Zoe's live pieces are a
   fixed soul (`labs/flue-zoe-brain-2x/src/soul.ts`), a counted-behaviours manner block (<=160 tokens, `manner_block.py`),
   a per-member persona envelope (`persona.ts`), a user-model card, and deterministic guards (hold-the-fact, ask-when-ambiguous,
   clean goodbye). Each is a *behaviour with a test*, not a trait word. The research supports exactly that direction:
   fidelity to a described persona decays over a long dialogue [Experimental, arXiv 2512.12775; 2402.10962], and
   trait-adjective prompts are the weakest anchor. **Keep building behaviours, measure them, do not randomise traits.**
2. **Recognisable identity = invariants + a small adaptation surface.** Fix what never varies (honesty, no hooks, no flattery,
   one question at a time, hold a stated fact). Let only *register* vary per person (length, formality, pace, warmth
   expression, humour allowance), and make every variation a stored, inspectable, owner-editable setting derived from
   stated or observed preference, never a sampled one. [Speculative as a whole; each piece is Proven in Zoe's own bench.]
3. **Measure drift with a behavioural probe set, not a vibe.** Drift work (Li et al., 2024) measures it by quantitative
   tests on the persona over rounds without human annotation [Proven method, small models]; adapt it as a *fixed
   probe battery* of ~40 scripted turns (including the sycophancy and goodbye cells Zoe already has), run per release in
   the replay lane, scored by code where possible (regex/state checks) and a judge only for style. Alarm on regression
   against a stored baseline, same as the voice-regression harness.
4. **Sycophancy and companion over-attachment are the two failure modes that matter for a household companion.** The
   MIT/OpenAI four-week randomized trial (981 people) found heavier daily use tracked with more loneliness, emotional
   dependence and less socialising [Experimental; self-report, one population, no non-chatbot control]. Zoe's
   existing stance (clean goodbye, no hooks, no "I'll be here", keep-in-touch opt-in only) is the right design response.
   **Do not add engagement mechanics.** Treat "uses Zoe less because life is going well" as a success state.
5. **Proactivity: the evidence agrees with the owner.** The classic mixed-initiative result is that agents fail by
   guessing goals badly, ignoring the cost of acting, and poor timing (Horvitz, CHI 1999) [Proven, 25 years of
   follow-on]; smart-speaker field work found "opportune moments" depend on busyness, mood, home routine and who is
   present (Cha et al., CHI 2020) [Proven-ish: one 40-person week]. A pull surface (orb dot, "what's up") sidesteps
   most of it. Keep pull as the default and make any push *earn* its slot with a decision table (section 3).
6. **A dot is free, a card is cheap, speech is expensive.** Rank channels by interruption cost; a proactive item must
   clear a higher bar for each step up, and *silence is the default winner*. Never speak unprompted (owner decision on the
   spoken morning brief, `ZOE_PROACTIVE_SPOKEN=0`).
7. **Humour: allow it narrowly.** Voice-assistant humour studies (CUI 2023, ~30 participants) found humour repaired
   communication breakdowns but participants still preferred the non-humorous version, and the funniest (aggressive)
   persona was less likable [Experimental, small]. Rule: Zoe never uses humour as a repair for her own error, never
   after a negative-affect turn, never with a child unless mirrored, and only wry/light when the person set the tone first.
8. **Adaptation per person is best evidenced for older adults, thin for children.** An older-adult study found informal
   conversational style preferred (especially by low-agreeableness users) and likened to relatives rather than
   professionals [Experimental, WoZ]. For children the evidence found is thin; Zoe's rule (guardian-stated facts only,
   no persona for minors until the crisis path ships, `MINORS_GET_PERSONA` False) should stand. Design for *explicit,
   stated* style preference first, observation second.
9. **The problem-solving loop is mostly a policy wrapper around machinery Zoe already has.** ReAct / Reflexion-style loops
   are established patterns [Proven as patterns; weak on small models]. The household-grade difference is not the
   loop; it is the **authority table** (what may be done without asking), the **budget** (steps, time, cost), and
   **a human-escalation path that is a card, not a failure**. See section 4.
10. **Smallest first move is measurement, not features.** Build the persona probe battery and the proactive "would this
    have been welcome" ledger (log-only shadow of the policy in section 3) before changing any prompt or turning on any new
    surface. Both are flag-off, voice-path-free, and make every later lane's claim falsifiable.

## 1. Projects and papers (the field scan)

Columns: purpose / architecture in one line; source inspected; maturity; strengths and weaknesses; compute and licence;
verdict for Zoe. "Abstract only" = I read a search-result summary, not the paper or code.

### 1a. Persistent personality, persona drift, character consistency

| Item | Purpose / architecture | Source inspected | Maturity | Strengths / weaknesses | Compute, licence | Verdict |
|---|---|---|---|---|---|---|
| [Measuring and Controlling Instruction (In)Stability in Language Model Dialogs (Li et al.; first titled Persona Drift)](https://arxiv.org/abs/2402.10962) | Defines persona drift, a quantitative benchmark needing no human annotation; reports drift within ~8 rounds on a 70B chat model; proposes split-softmax attention re-weighting | Abstract only | [Experimental] 2024, widely cited | + gives a *method* for drift; - mitigation needs model access (attention), not a prompt change on a served llama.cpp | Offline eval cheap; paper licence arXiv | **Take the measurement idea, skip the mitigation.** |
| [Persistent Personas? Role-Playing, Instruction Following and Safety in Extended Interactions (2512.12775)](https://arxiv.org/abs/2512.12775) | 100+ round persona dialogues with dialogue-conditioned benchmarks; fidelity falls over the dialogue, esp. goal-oriented; persona answers converge to baseline as fidelity fades | Abstract only | [Experimental] Dec 2025 | + directly the "does the voice survive a long session" question; - role-play personas, not a companion with memory | Eval only | **Use as the protocol shape for the Zoe probe battery.** |
| [Stable Personas: Dual-assessment of temporal stability (2601.22812)](https://arxiv.org/abs/2601.22812) | Seven LLMs; self-report stays stable but *observer* ratings show expression declining in long conversations | Abstract only | [Experimental] Jan 2026 | + warns that self-report ("I am warm") is a bad instrument; - simulation of humans, not a companion | Eval only | **Take: measure behaviour by observer, never by asking the model.** |
| [Persona Vectors (Chen et al., Anthropic Fellows)](https://arxiv.org/abs/2507.21509) ([repo](https://github.com/safety-research/persona_vectors)) | Finds activation directions for traits (evil, sycophancy, hallucination, also humour, optimism); monitor and steer | Search summary only | [Experimental] 2025; code released | + a real *monitor* for sycophancy shift; - tested on Qwen2.5-7B and Llama-3.1-8B, needs activations; Zoe's brain is Gemma 4 on llama.cpp (a rock) | GPU hooks; code licence Apache-2.0 (verified) | **Do not adopt. Park (idea: activation-level monitor).** |
| [Constitutional AI (Bai et al.)](https://arxiv.org/abs/2212.08073) | Principles-based self-critique to shape behaviour | Not re-read (known) | [Proven] as a training method | + the "constitution" idea; - training-time, not available for a fixed rock | n/a | **Take the idea only:** Zoe's soul + doctrines already are a runtime constitution; keep them short and testable. |
| [Generative Agents (Park et al., 2023)](https://arxiv.org/abs/2304.03442) | Memory stream + reflection + planning for believable agents | Not re-read (known); cited in later work as the base for social-sim benchmarks | [Proven] influential; [Experimental] as a product pattern | + reflection and planning are the template for Zoe's night mind; - Zoe's version is stricter (pointers, no model prose) which is correct | Heavy on API calls in the original | **Already adopted in spirit** (`night_mind.py`). Keep the pointer-only discipline. |
| Character.AI / Replika / Pi (publicly documented) | Companion products with persistent persona and chat memory | Product behaviour only; no source exists | [Proven] at consumer scale | + shows demand for continuity; - engagement-driven design; MIT/OpenAI trial cites Replika dependence patterns (users perceiving the bot as having needs) | Cloud | **Use as the anti-pattern list**: streaks, guilt hooks, needs the user must tend. |
| [Longitudinal RCT of chatbot psychosocial effects (MIT Media Lab + OpenAI)](https://arxiv.org/abs/2503.17473) | 4 weeks, 981 participants, text vs voice, open vs personal vs non-personal topics | Abstract and coverage only | [Experimental], preprint | + only controlled evidence on dependence; - self-report; no non-chatbot control | n/a | **Design constraint** (bullet 4). |

### 1b. Social intelligence, theory of mind, emotional intelligence

| Item | Purpose / architecture | Source inspected | Maturity | Strengths / weaknesses | Compute, licence | Verdict |
|---|---|---|---|---|---|---|
| [SOTOPIA (ICLR 2024)](https://arxiv.org/abs/2310.11667) | Interactive role-play social scenarios with goals, multi-dimension scoring (SOTOPIA-EVAL); larger models score higher, hard subset defeats all | Abstract only | [Proven] as a benchmark, [Experimental] as predictor of real use | + goal + relationship scoring; - multi-agent sim, LLM-judged, generic personas | Judge calls (cost) | **Borrow its dimensions** (goal, relationship, believability, knowledge, secret-keeping) as probe labels; do not run it. |
| [Lifelong-SOTOPIA (2506.12666)](https://arxiv.org/abs/2506.12666) | Social intelligence across a *sequence* of interactions | Abstract only | [Experimental] 2025 | + matches "same people, many days"; - sim, not real | Judge calls | **Borrow shape:** score consistency across sessions. |
| [EQ-Bench 3](https://eqbench.com/eqbench3.html) (successor of [EQ-Bench, 2312.06281](https://arxiv.org/abs/2312.06281)) | LLM-judged hard emotional-intelligence role-plays on eight dimensions (empathy, insight, social dexterity, emotional reasoning...) | Site summary only | [Experimental]; judge identity is described inconsistently on the site (two pages disagree) | + public, cheap run (~$10-15 on a frontier judge per the site); - judged by a larger model, English text, one-shot, not a 4B voice companion | Needs a judge; leaderboard accepts open-weight models only | **Use as a one-off sanity read of the 4B, not a gate.** Never optimise to it. |
| Theory-of-mind benchmarks (FANToM, Hi-ToM, OpenToM) | False-belief and who-knows-what tests | Not searched this pass; named from prior knowledge, **links not verified** | [Experimental] | + the only direct tests of "she doesn't know that yet" | Eval | **Defer;** Zoe's real ToM need is narrow: who is in the room and what each may hear (identity + privacy classes), which is code, not inference. |

### 1c. Proactive assistance, interruptibility, mixed initiative, calm technology

| Item | Purpose / architecture | Source inspected | Maturity | Strengths / weaknesses | Compute, licence | Verdict |
|---|---|---|---|---|---|---|
| [Horvitz, Principles of Mixed-Initiative UIs (CHI 1999)](https://erichorvitz.com/uiact.htm) | Twelve principles: expected-utility of acting, dialogue to resolve uncertainty, graceful degradation, undo, learning over time | Abstract / summaries | [Proven] foundational | + names the failure set (bad goal guesses, ignoring cost, bad timing); - 1999, desktop scheduling | None | **Adopt as the policy skeleton** (section 3): act only when utility beats interruption cost; otherwise ask or wait. |
| [Cha et al., smart-speaker opportune moments (IMWUT 2020)](https://doi.org/10.1145/3411810) | One-week field study, 40 participants, what makes a moment right for a speaker to speak | Search summary | [Experimental] strong design | + home, not lab; busyness, mood, routine, mobility, social presence all matter; - sample, one week | None | **Direct input to the signal list** (section 3.3). |
| [Eliciting spoken interruptions (2106.02077)](https://arxiv.org/abs/2106.02077) | How people interrupt each other during complex tasks | Search summary | [Experimental] 2021 | + urgency shortens the wait; people use cues from the interrupted task | None | **Take:** an interruption must carry its urgency in its first words. |
| [Human-first pacing (NeurIPS 2026 listing)](https://neurips.cc/virtual/2026/162434) | Estimate what the user would do if the agent waited, speak first only after a calibrated chance for the human to act | Listing summary; work in progress | [Speculative] | + exactly the "would they have done it anyway" test; - unreviewed | None | **Borrow the question** as a shadow-metric. |
| [Generative-agent simulation of 10 user studies (2505.09938)](https://arxiv.org/abs/2505.09938) | Simulated users reproduce a smart-speaker timing study; works better for focus-heavy activities | Search summary | [Experimental] | + a cheap pre-test of timing policies; - simulated people are not Zoe's household | LLM calls | **Maybe** as a policy pre-filter (experiment E5); never as evidence of welcome. |
| Weiser and Brown, calm technology (1996) | Technology that moves between periphery and centre of attention | **Not retrieved this pass; no verified link** | [Proven] as a design principle, not a measurement | + the orb dot is literally a periphery signal | None | **Adopt as principle;** cite from the paper itself before quoting. |
| Static vs adaptive proactive speech agents (CUI-era study, listing only) | Whether context-adaptive interruptions are rated better | Search summary | [Experimental] | Timing hypothesis was *rejected* (no rating difference), i.e. adaptive timing is not a free win | None | **Warns against over-investing in timing models.** |

### 1d. Humour, adaptive communication style, problem-solving loops

| Item | Purpose / architecture | Source inspected | Maturity | Strengths / weaknesses | Compute, licence | Verdict |
|---|---|---|---|---|---|---|
| [Humor as repair with voice assistants (CUI 2023)](https://vbn.aau.dk/ws/files/756816271/Humor_CUI2023_postprint.pdf) | 30 participants, four humour personas vs neutral, after breakdowns | Search summary | [Experimental] small | + repair worked, but neutral preferred; aggressive rated funniest yet less likable | None | **Restrict humour** (bullet 7). |
| [Timely humour in AAC (2410.16634)](https://arxiv.org/html/2410.16634) | AI-suggested humorous comments, timing as core variable | Search summary | [Experimental] | + humour is a timing problem, not a content problem | LLM | **Context only.** |
| ["Like My Aunt Dorothy" (2309.11681)](https://arxiv.org/abs/2309.11681) | Formal vs informal VA style with middle-aged and older adults (WoZ) | Search summary | [Experimental] | + older adults prefer informal and liken it to relatives; style preference varies by agreeableness | None | **Informs per-person register defaults;** always overridable. |
| Age-adaptive conversational systems (pilot) | Detect age from language and adapt | Search summary | [Experimental] pilot | - inferring age from text is exactly the inference Zoe's rules forbid for minors | n/a | **Do not infer age.** Use stored, guardian/owner-stated mode. |
| [ReAct (2210.03629)](https://arxiv.org/abs/2210.03629), [Reflexion (2303.11366)](https://arxiv.org/abs/2303.11366) | Reason-act loops; verbal self-reflection stored for the next attempt | Not re-read (known patterns) | [Proven] as patterns, brittle on small models | + the loop vocabulary; - self-evaluation on a 4B is unreliable | Extra tokens per step | **Use the shape with code-checked success tests,** not model self-grading. |

## 2. Identity and adaptation design

### 2.1 What Zoe has (read, not assumed)

- **Soul** (`soul.ts`): "warm, curious, genuinely present", natural voice, contractions, never "Great!/Of course!", acknowledge
  the personal first, help is sometimes listening. SOURCE OF TRUTH, sha-pinned, mirrors the dormant core lane's `SOUL.md`.
- **Persona layer** (`docs/knowledge/persona-layer.md`): household persona + per-member mode (flag `ZOE_PERSONA_LAYER`),
  an envelope line replaces the soul's first five paragraphs; minors get none; guests get household tone.
- **Manner block** (`manner_block.py`, `manner.ts`): <=160 tokens of *counted behaviours* - reflect before advising, name one
  specific true thing, one question at a time, no hooks, disagree once kindly, no flattery.
- **Person-half guards** (`person-half-guards.md`): hold the fact, ask when ambiguous, clean goodbye - deterministic code
  because the 4B brain would not do them from a prompt (bench cells P5a, P7, P8, P12), with an oracle arm proving it.
- **Gaps (completeness record section 1, item 4):** fast-tier and fallback replies read as a machine; about one in ten
  empty transcripts gets a canned line. These are *identity breaks*: the same house, two voices.

### 2.2 The design: personality as an interaction model

Four stored layers; a reply is a function of them, none is a free-text adjective list.

| Layer | What it is | Source of truth | Changes how |
|---|---|---|---|
| L0 Invariants | The behaviours that never vary (honesty, hold the fact, no flattery, no hooks, one question, clean goodbye, privacy classes) | soul + doctrines + guards (code) | Only by a reviewed PR; tested |
| L1 Voice | One speaking style for *every* tier (brain, fast tier, fallback, error): sentence length, contractions, how a re-ask sounds | One drafted phrase set, data not code, shared by all tiers | Reviewed edit; versioned |
| L2 Register per person | Length (terse / normal / fuller), pace, formality, warmth expression, humour allowance (off / light), reading level for a child mode | A small per-member row; **set by the member's words** ("keep it short") or the owner; observation can *propose*, never silently set | Member utterance (immediate), proposal card (weekly), owner edit |
| L3 Relationship state | Per pair (Zoe, member): topics they like to discuss, things to leave alone (deny-list), what was raised and ignored (the night mind's back-off), standing preferences | Memory authority rows + night-mind pointers, user-stated > confirmed > inferred | Existing memory authority rules |

**Why this yields a recognisable identity:** L0 and L1 are identical across members and tiers, so the *same* Zoe is heard
everywhere; L2 and L3 vary only through stored, explainable state, so the variation is *consistent* (same person gets
the same register tomorrow) rather than random. [Speculative as a combined design; each layer is Proven inside Zoe.]

**What "emerge" means concretely.** Do not add "witty, playful" to the soul. Instead, make the observable behaviours the
specification: e.g. "when the member has been terse for three turns, the next reply is one sentence" is an L2 rule with a
test; "references yesterday's open thread once and only if the member is not mid-task" is an L3 rule with a test.
A reader would call the result "personality" because the same pattern of choices recurs.

### 2.3 Adaptation by situation (not trait)

| Situation signal | Source | Effect (all reversible, all logged) |
|---|---|---|
| Time of night / just woke | local-time, panel state | Shorter, quieter; no new topics |
| Mid-task (timer running, cooking mode, music query) | active session state | Answer only what was asked |
| Distress words or low-arousal voice (corroborating only; see the tone-of-voice record) | text cues + relative prosody | Reflect before advising; no humour; no proactive raise |
| Guest present / voice unverified | identity layer | Household tone; no member memory; nothing proactive spoken |
| Child mode (stored) | owner/guardian-set mode | Plain words, no inference, no health/mood facets; guardian-stated facts only |
| Elder preference (stated) | member row | Pace and register as the member chose; informal default is an *option*, not an assumption |

### 2.4 Humour policy (small, explicit)

Allowed: light, observational, *following* the member's own joke or tone, in a low-stakes turn, at most one per
conversation. Forbidden: as repair for Zoe's own failure; after distress; about a person absent; with an unverified or
guest speaker; with a child unless mirroring. Evidence base: [Experimental, small studies]; treat as a default to be
tuned by explicit "less of that" feedback, which becomes an L2 `humour: off` row.

### 2.5 One voice for every tier (the concrete fix for the completeness item)

Draft once, as data: the re-ask ("Sorry, say that again?"), the confirmation, the failure ("That didn't go through, want
me to try again?"), the empty-transcript response, the goodbye. Every tier (fast-tier, fallback, error path, brain) reads
the same set. Test: a diff test that no user-facing string in a fast-tier module is absent from the phrase set. This is
voice path; it is replay-gated (AGENTS.md mandatory) before landing. [Proven approach, small effort; dependency of every other idea here.]

### 2.6 Measuring consistency and drift

| Measure | How | Automated? | Baseline and alarm |
|---|---|---|---|
| Invariant pass rate | Scripted probes for each L0 behaviour (flattery bait, "are you sure?" pushback, farewell, hook bait, ambiguous name) at turn 1, 20, 60 of a long synthetic session | Yes (regex / state checks, as the existing person bench) | Any drop against stored baseline = regression |
| Cross-tier voice parity | Same intents through brain and fast tier; compare against the phrase set (exact-match for data phrases) | Yes | 100% for data phrases |
| Style stability | Reply length, contraction rate, opener distribution, question-per-reply, hook rate, per member and per session position | Yes (counts) | Band from baseline, alarm on shift >2 sigma |
| Persona fidelity over turns | Observer-judge rating of N sampled replies against the soul, early vs late in session (the Stable Personas lesson: judge from outside, do not ask the model) | Judge call, offline, weekly | Track delta late-minus-early |
| Cross-session consistency | Same member, same prompt, different days: same register | Yes (counts) | Equal within band |
| Sycophancy | Existing P5a cells; add a push-back-without-evidence set | Yes | 0 flips on held facts |
| Dependence guardrails | Share of sessions ending on a hook; closers that invite more; proactive items per member per week | Yes (counts) | Zero hooks; weekly count under cap |

Never use a persona-vector style activation monitor here (rock stays unchanged, requires activations) [Speculative to revisit].

## 3. The proactive engagement policy

### 3.1 Principles (from the owner's stance and the literature)

1. Pull by default. The orb dot and "what's up" are the primary surface. Owner rejected the spoken morning brief.
2. Utility must beat interruption cost (Horvitz); when unsure, ask or wait, never act on a guess about goals.
3. Silence wins ties. A missed nudge is cheap; a mistimed one erodes trust and teaches the household to ignore the dot.
4. Every proactive item carries its source, can be dismissed in one gesture, and dismissal is remembered (back-off doubles; two ignored = leave).
5. Privacy classes are checked *before* composing, not after: a private class is counted, never read aloud, never visible to a guest.
6. No push without a stated reason the member would recognise ("because you said the dentist is Friday").

### 3.2 Channel ladder

| Rung | What | Interruption cost | Example |
|---|---|---|---|
| 0 Silence | Nothing | None | Default |
| 1 Dot | Orb indicator; nothing shown until the member pulls | Near zero | "There is something for you" |
| 2 Card | Visible on the panel/app when the member is present and engaged with the screen; one tap to dismiss | Low | Weekly approve-before-write batch; change notice |
| 3 Spoken, on pull | Spoken only after the member asks "what's up" | Chosen by the member | The morning rundown, when asked |
| 4 Spoken, unprompted | **Not permitted** by default. Reserved for safety (alarm, timer the member set, an explicit reminder the member asked for at a time) | High | A reminder the member requested |

### 3.3 Decision table

Order of evaluation: privacy and guest check, then do-not-disturb states, then rung selection. First matching row wins.

| # | Condition | Decision | Why |
|---|---|---|---|
| 1 | Speaker unverified or guest present | Silence (rung 0). Nothing member-specific anywhere | D6 (guest hears nothing) |
| 2 | Item touches a deny-list class (health, grief, money trouble, a quarrel, "don't bring that up", child's mood) | Silence; only if the member raises it | Night-mind leave list; D7 |
| 3 | Member asked for it at a time (reminder, timer, alarm) | Spoken at that time (rung 4, the one allowed) | It is a pull in time-shifted form |
| 4 | Member is mid-task, in a call, or speaking with others, or it is a rest window | Hold; re-evaluate when the state clears; expires | Opportune-moment work |
| 5 | Item is a change notice about something the member owns (booking moved, a thing they track changed) | Dot, then card if the panel is awake and the member is near | Useful, factual, low cost |
| 6 | Night-mind `raise` thread (at most one per member per morning) | Dot only; surfaces when the member opens "what's up" or the first conversation; mark raised so it does not repeat | Existing pull-only design |
| 7 | Inferred person facts awaiting approval (D1) | One weekly card; never spoken | Approve-before-write |
| 8 | Upcoming date for a family-visible person (birthday, anniversary), lead times D3 | Dot at lead time, card on the day; no speech | Date reminders are welcome by decision D3 |
| 9 | Keep-in-touch / "you haven't spoken to X" | Silence unless the member set a cadence for that person (D4 opt-in) | Opt-in only |
| 10 | Open loop the member created (promised to do X, asked Zoe to remember) and it is now due | Card, then dot if unseen; one follow-up only | Closes loops the member opened |
| 11 | Routine-derived suggestion (weather before the usual outing) | Only if the member enabled routine hints; dot only | Highest false-positive risk |
| 12 | Two earlier items from this class ignored or dismissed | Suppress the class for this member for a doubling interval | Notification fatigue back-off |
| 13 | More than the weekly cap of unsolicited items (start: 3 cards, 7 dots a week per member) | Hold lowest-value items | Fatigue cap |
| 14 | Everything else | Silence | Silence wins |

### 3.4 Signals and their failure modes

| Signal | Use | Failure mode | Guard |
|---|---|---|---|
| Night-mind threads (pointer-shaped, cited) | What to raise, what is quiet | Raises something that already resolved; raises a sensitive thing | Re-check state at surfacing time; deny-list is never written into a prompt |
| Calendar | Lead-time notices | Stale or duplicate events; wrong person's calendar | Source shown on the card; per-member scope |
| Routines (observed times, devices) | Hints | Inferring a routine from two days; the house has no presence sensors, so "home/away" is a guess | Require N occurrences; never claim presence; routine hints off by default |
| Open loops (member's own promises) | Follow-up | Nagging; loop already done off-device | One follow-up, "mark done" on the card |
| Voice tone/arousal | Corroborate only (tone record) | Misread frustration, 28-35% on natural speech per the tone record's own cited range | Never trigger a push; only soften a reply |
| Time of day | Rest windows | Different sleepers | Per-member quiet hours set by the member |

### 3.5 Failure modes of the policy itself

- **Fatigue and ignoring the dot** (the measured way proactive systems die). Mitigation: weekly cap, doubling back-off, a "was this useful" tap logged.
- **Stale surfacing**: item composed at 02:00, shown at 08:00 after the situation changed. Mitigation: re-validate at show time; expiry on every item.
- **Disguised engagement**: cards that exist to bring the person back. Mitigation: the dependence counters in 2.6; no streaks, ever.
- **Cross-person leakage** on a shared panel. Mitigation: rows 1-2 first; privacy class counted not read.
- **Over-trust in a timing model**: the static-vs-adaptive study found no timing-rating gain; do not build a learned timing model before the log shows a need.

## 4. The household-grade problem-solving loop

### 4.1 Loop

`understand -> missing info? -> research -> plan -> authorised tools -> evaluate -> learn -> escalate`

| Step | Zoe-grade rule |
|---|---|
| Understand | Restate the goal in one line; resolve ambiguity by the existing ask-when-ambiguous path; never guess a person, a place or a time |
| Missing info | Prefer *looking* (memory, calendar, lists, devices the member owns) over asking; ask only what only the member knows; one question at a time |
| Research | Local first; web only for facts the member asked to be looked up, with sources shown (existing web-search policy, `docs/knowledge`, not changed here) |
| Plan | A plan is a short list the member can see ("I'll check the calendar, then draft the message"); plans over 4 steps are summarised first |
| Authorised tools | Authority table below; unknown tool = ask |
| Evaluate | **Code-checked success tests where possible** (the event exists in the calendar; the list now contains the item); model self-grading is advisory only on a 4B (Reflexion lesson) |
| Learn | A correction becomes a stored preference via the memory authority (user-stated > confirmed > inferred); an inferred lesson goes to the approve-before-write card |
| Escalate | To a person (the member, or the owner for system/safety matters) as a card with: what I tried, what is blocked, the one decision needed |

### 4.2 Authority table

| Action class | Allowed without asking | Needs a yes | Never |
|---|---|---|---|
| Read the member's own data in scope | Yes | | Another member's private classes |
| Write a member-stated fact | Yes | | |
| Write an inferred fact | | Yes (D1 approve-before-write) | Silent write |
| Create/modify a list item, reminder, timer the member asked for | Yes | | |
| Change a calendar entry with other attendees, send a message to a person, spend money, buy, post anywhere, change a household device outside the room's normal use | | Yes, every time | Silently |
| Anything touching a child's profile | | Guardian | Inference |
| Delete/forget | | Yes + existing forget path | Soft-forget that can resurface (forgotten means forever) |
| Self-modify (code, prompts, flags) | | Not this loop | Always (owner review path only) |

### 4.3 Budgets and stop rules

- Max 6 tool steps and 90 seconds per request on the voice path; longer work is handed to a card ("I'll work on it, I'll leave a note").
- Same tool with same arguments twice = stop and escalate (the loop-detector exists conceptually in the harness; confirm before relying on it).
- A confidence floor: if two plans differ materially and nothing in memory decides, ask the member, do not pick.
- Every attempt is logged with reason, tool, result (names only in the log if the repo is ever mirrored; it is a public repo, so keep fixtures synthetic).

### 4.4 Failure modes

Wrong-person action (mitigate: identity before write); partial completion reported as success (mitigate: success tests are code, not narration; `set_timer` fail-closed pattern generalised); runaway loops (budget); sycophantic "yes it's done" (hold-the-fact style guards and the evaluate step); privacy leaks via search queries (queries composed from the request only, no memory text sent out); escalation fatigue (cap escalations; batch per day unless urgent).

## 5. Experiments (smallest first)

All offline or flag-off or shadow; none touches production behaviour; any later voice-path landing is replay-gated and run under the voice harness lock. Effort is engineer-days.

| # | Hypothesis | Implementation | Resources | Evaluation | Success / failure | Effort |
|---|---|---|---|---|---|---|
| E1 | Persona drift is measurable on Zoe with code-only probes | A synthetic long-session script (60 turns) with invariant probes at turn 1/20/60; counts of hooks, flattery, held-fact flips, farewell cleanliness; run through the replay lane against the live brain contract | The existing person bench (`scripts/perf/samantha_person.py`), the test lane lock, synthetic users only | Pass rate per probe per position | Success = we get a stable baseline and detect a deliberately injected regression (negative control). Failure = noise exceeds any plausible drift | 2-3 |
| E2 | One voice for all tiers removes the robotic seams | Extract the re-ask / confirm / failure / empty-transcript / goodbye phrases into one data file read by fast-tier, fallback and error paths; diff test that tier strings are in the set | Fast-tier modules; replay gate on Jason's corpus (flag-off by default) | Said-vs-did does not regress; a blind A/B of 20 old vs new lines by the owner | Success = no regression and owner prefers new in most pairs. Failure = latency regression or no preference | 2 |
| E3 | The proactive policy would have been welcome | Log-only shadow: run the section 3.3 table on real signals (night-mind raise, dates, open loops) and record the decision and rung it *would* take; no output to the member | Existing proactive selector and ledger; flag-dark | After two weeks: counts per rung per row; owner reviews 30 sampled "would have shown" items and tags welcome / neutral / unwelcome | Success = >=80% welcome-or-neutral on rung 2+ items and zero deny-list leaks. Failure = any deny-list item selected | 3 |
| E4 | A per-member register row changes replies consistently | Add `register` (length, humour allowance) as a stored row set only by the member's explicit words ("keep it short"); apply as one manner-block line | Persona layer plumbing; flag-dark | Reply-length band per member before/after; cross-session variance | Success = length shifts as stated and stays within band across days; failure = bleed to another member (negative control: two members alternating) | 3 |
| E5 | A simulated user can pre-filter timing policies | Use a generative-agent simulation (2505.09938 style) with synthetic household routines to compare cap/back-off settings before live trial | A local or API model; synthetic data only | Rank order of policies vs the E3 log once available | Success = sim ranking agrees with E3 on the top policy. Failure = disagree (then drop the sim) | 3 (after E3) |
| E6 | EQ-style quality of the 4B can be read once, cheaply | Run the 4B on EQ-Bench 3 scenarios as a one-off read; compare with persona-block on vs off | Public benchmark; a judge (cost ~ $10-15 per the site; frontier credits policy applies) | Elo-style delta only, not a target | Informational only; no gate; a drop with the persona on is the finding | 1 |
| E7 | The authority-table loop completes everyday tasks safely | Pick 5 synthetic everyday tasks (reschedule a reminder, build a list from a recipe, find a free slot, draft a message to a contact); run the loop with the table and success tests in a lab harness | Lab (Flue lab), synthetic household, no real contacts | Completion rate; unauthorised-action count (must be 0); escalation card quality | Success = 0 unauthorised actions, >=70% completion on the 5; failure = any silent write of an inferred fact | 5 |
| E8 | A dependence monitor stays flat | Weekly counts: hooks, closers inviting more, unsolicited items per member, session-length distribution | Existing logs | Trend, per member | Success = counters flat or falling; any upward trend triggers owner review | 1 |

Order: E1, E2, E3, E8 first (measurement and seam repair); E4 and E6 next; E7 after E3's log exists; E5 only if E3 shows a need.

## 6. Metrics and what is speculative

### 6.1 Metrics

- Invariant pass rate (per probe, per turn position) and its baseline; target: equal to baseline, never below.
- Cross-tier phrase parity: 100% for data phrases.
- Hook rate: 0. Flattery rate: 0 on bait set. Held-fact flips: 0 on owner-stated rows.
- Reply style bands (length, contractions, questions per reply) per member, stable across days.
- Proactive: items per member per week by rung (cap table), dismissal rate, "useful" tap rate, ignored-twice suppressions, deny-list leaks (must be 0).
- Problem-solving: completion rate, unauthorised actions (0), escalation rate, mean steps, wrong-person actions (0).
- Dependence: share of sessions ending in a hook (0), unsolicited items trend, session length trend.

### 6.2 Evidence quality, plainly

- [Proven]: mixed-initiative failure set (Horvitz 1999); that fidelity to a prompted persona degrades over long dialogues (two independent papers, model-dependent magnitude); Zoe's own bench result that a prompt cannot fix hold/ask/goodbye on the 4B (oracle arm).
- [Experimental]: humour effects (small studies), older-adult style preference (WoZ), opportune-moment findings (one 40-person week), the MIT/OpenAI dependence trial (self-report, preprint at time of reading), EQ-Bench 3 as a quality read (judge identity inconsistently reported on its own site), persona-vector monitoring (7-8B models only).
- [Speculative]: the four-layer identity model as a whole, the section 3.3 thresholds (caps of 3 cards / 7 dots; N occurrences for routines), the 6-step 90-second budget, the 80% welcome bar, and all of E5. These are starting values to be replaced by the E3 log.
- **Not verified in this pass:** Weiser and Brown (1996) text and link; theory-of-mind benchmark links; Character.AI and Replika internals (no public source exists; I used product-level and trial-level evidence only); licences of every external item (unchecked unless stated). The 2026 Journal of Consumer Research companion-loneliness result seen second-hand was **not** relied on.
- Nothing here changes a rock (Gemma 4 E4B+MTP, Moonshine, Kokoro); every item works around them.

### 6.3 Open questions for the owner

1. Is a visible "was this useful" tap acceptable on a card, or is that itself friction?
2. May the member-stated register row (E4) also be set by the owner on a member's behalf, or only by the member?
3. Are routine hints ever wanted, given the house has no presence sensors (so "home" is inferred)? Default here: no.

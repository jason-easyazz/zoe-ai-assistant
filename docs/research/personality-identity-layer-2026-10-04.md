---
type: research
title: Personality + identity layer for Zoe — Nomi, the field, what Zoe has, a design (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document
description: What Nomi.ai and the companion field do to let an owner shape a companion's personality (traits, relationship type, backstory, OOC corrections, Nomi's Identity Core), what the persona-drift literature says actually holds on a small model, a read-only audit of Zoe's own persona doctrine (five copies, one live, zero owner-editable surface, zero measurement), and a design that fits Zoe's doctrine — a structured persona config rendered as a stable system-prompt tail per member, edited by voice and touch on the panel and by phone for text, measured by the P5 drift band and gated by the Samantha bar. Ends in a go/no-go against VISION.
---

# Personality + identity layer for Zoe (2026-10-04)

Research date: 2026-10-04. The owner looked at Nomi.ai and liked that a companion's personality can
be *shaped*: pick traits, pick a relationship type, write a backstory. This record answers four
questions: what Nomi actually does (and whether any of it is open), what the rest of the field
does and what the evidence says works, what Zoe already has, and what a personality layer that
fits Zoe's doctrine would look like. It is the design half of **P5** (the persona-drift band) in
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md); P5 is
the measurement half and is not repeated here beyond what the design needs.

Method: read-only. Code facts come from the worktree at `462a6876` (grep + Read; no Serena, no
codebase-memory — the box had under 0.5 GB free). No live service was touched, no turn was sent to
the live API, and no store was read. Web facts were gathered 2026-10-04 and are cited inline;
**[unverified]** marks a claim taken from a secondary source or one I could not confirm at a
primary page. Rocks (Gemma 4 E4B+MTP, Moonshine, Kokoro), the W3 RAM gate and "mutate the tail,
never the head" bind every recommendation.

## 0. TL;DR

1. **Nomi is closed source.** Nothing of its model, prompts or memory internals is public. What is
   public is a thin REST API (list Nomis, chat, rooms; no create, no persona, no memory
   endpoints — the Nomi object exposes `uuid, name, gender, created, relationshipType` only) and
   a handful of community client libraries. There is nothing to borrow as code; there are ideas to
   borrow as *shape* (§1).
2. **The field converges on one shape**: a short always-present persona block, a tiny high-weight
   style directive, example dialogue, and retrieval-gated lore — plus a user-editable correction
   channel (Nomi OOC, Replika Memories, Letta blocks, Kindroid journal). Every product that lets
   the user *type* the persona warns them to keep it short, positive and concrete (§2).
3. **The evidence on a 4B model is specific**: attention to the system prompt decays within ~8
   turns (Li et al. 2024); small models get *more* variable with conversation history, not less
   (PERSIST, AAAI 2026); persona injection reliably moves self-report but only weakly moves
   behaviour (Han et al. 2025); anchored adjectives with Likert qualifiers ("a bit", "very")
   register at nine levels (Serapio-García 2023), while raw numeric sliders work only when the
   prompt is short and intensities are low (Cho & Cheong 2025). Therefore: structured config
   rendered to a few anchored sentences, not sliders in the prompt, and **measure behaviour, not
   self-report** (§2.3).
4. **Zoe has a persona, not a personality layer.** The live persona is a code constant in the Flue
   sidecar (`labs/flue-zoe-brain-2x/src/agents/zoe.ts:69` `ZOE_SOUL` + nine doctrines =
   `ZOE_INSTRUCTIONS`, 9,326 chars ≈ 2.1k tokens, identical for every user). There are **five
   copies** of the soul text in the tree, already diverging. There is **no owner-editable persona
   surface** (settings has Theme, Display, Speaker Identity; the TTS voice is household-wide),
   **no per-member relationship mode** (W5.4 is NOT STARTED), and **nothing measures persona
   drift**. What Zoe *does* have is the HUMAN half of a Letta-style core memory — the user-model
   card (`user_model_card.py`, ≤1,400 chars, appended to the END of the system prompt per user,
   version-hashed, TTL-cached) — and that mechanism is exactly the carrier a persona block needs (§3).
5. **Design (§4)**: a `persona_config` (household: 3–5 anchored traits with a 3-level qualifier,
   a voice style, boundaries, an optional ≤600-char backstory) plus per-member
   `relationship_modes` (companion / mentor / helper / kid, a nickname), rendered
   deterministically to a ≤700-char block and appended to the system prompt in the user-model
   suffix position — a stable, per-user prompt tail, never a head mutation. Editing is voice-first
   on the panel ("be a bit more playful" → one-notch proposal → confirm), touch-second (trait
   chips), and phone via QR for anything typed (backstory, boundaries). Corrections ("that's not
   you") become `chat_feedback` rows, which are P5's negative anchors.
6. **Cost**: +120–175 tokens of system text per member, cached per user (same-user steady state
   = 0 re-prefill; a user switch re-prefills block + tools at ~650 tok/s cold ≈ +0.2 s); 0 RAM.
   The drift band reuses the resident bge-small (~7 ms per reply, off the critical path).
7. **Go/no-go (§6)**: **GO** for Phase 0 (P5 drift band, log-only — measurement first) and
   Phase 1 (persona block flag-dark + panel/phone editing, Samantha bar + replay-gated).
   **NO-GO** for a Nomi-style invisible self-editing "Identity Core" (W10 says persona changes are
   human-gated PRs), free-text system-prompt editing by members, numeric sliders in the prompt, a
   persona LoRA (rock + RAM) and activation steering (horizon). Mood is B5.2's job, not this layer's.

## 1. Nomi.ai (Glimpse AI) — what it actually is

Company: Glimpse.ai, Inc., Baltimore, launched July 2023, founder/CEO Alex Cardinell, self-funded,
~7 FTE ([Crunchbase](https://www.crunchbase.com/organization/glimpse-ai); headcount secondary
[unverified]). Paid tier US$15.99/mo ([third-party, July 2026](https://www.virtualaipartner.com/nomi-ai/)).

### 1.1 Open source? No.

- There is no official GitHub organisation. `github.com/nomi-github` is an unrelated person;
  `NikhVerse/Nomi-AI` is a name collision. Models (Odyssey → Mosaic → Aurora → Solstice →
  Cambrian) are proprietary; weights, prompts and memory internals are unpublished.
- What is public is the **REST API** ([docs](https://api.nomi.ai/docs/), launched 2024-09-30):
  `GET /v1/nomis`, `GET /v1/nomis/:id`, `POST /v1/nomis/:id/chat`, `GET /v1/nomis/:id/avatar`,
  rooms CRUD + `POST /v1/rooms/:id/chat`. **No create-Nomi, no memory, no persona or backstory
  endpoints**; the Nomi object is `uuid, name, gender, created, relationshipType` with
  `relationshipType ∈ {Mentor, Friend, Romantic}` ([reference](https://api.nomi.ai/docs/reference/get-v1-nomis-id/)).
  Chat `messageText` is 400 chars free / 800 paid. The CEO called API use "relatively niche"
  (Jan 2026 stream, [wiki](https://wiki.nomi.ai/2026_01_Stream_Summary)).
- Community clients only: PHP (`oliverearl/nomiai-php`), Rust (`nomi_api_client`), Go
  (`sjourdan/nomi-cli`), Discord bridges (`d3tourrr/NomiKin-Discord`, `myakirimiya/nomi-discord-python`)
  ([topic page](https://github.com/topics/nomi)).

So the honest answer to "is any of it open source": **no** — the only reusable thing is the
*shape* of the product, below.

### 1.2 How personality is represented

| Layer | What it is | Who edits | Limits | Source |
|---|---|---|---|---|
| **Creation-time core** | relationship type (Friend / Mentor / Romantic / Custom), name, look, **3–7 personality traits** (+ one custom), starter interests | user, **once** — "cannot be modified after the character is created" | official examples: affectionate, compassionate, confident, intellectual, playful, romantic, philosophical, shy, sarcastic; a 20-item list circulates in reviews [unverified] | [wiki: change name/core?](https://wiki.nomi.ai/Can_you_change_your_Nomi%27s_name,_core_personality,_or_initial_interests%3F), [identity](https://wiki.nomi.ai/How_does_a_Nomi_establish_their_identity%3F), [Nomi 101](https://nomi.ai/nomi-knowledge/nomi-101-a-beginners-guide-to-getting-started-with-your-ai-companion/) |
| **Backstory+** (was "Shared Notes") | nine free-text sections: Backstory, Inclination, Current Roleplay, Your Appearance, Nomi's Appearance, Nicknames, Preferences, Desires, **Boundaries** | user, any time | Backstory 1,000 chars free / 2,000 paid (2024-09-17); other sections unpublished | [wiki: Backstory+](https://wiki.nomi.ai/What_is_Backstory%2B%3F), [Boundaries](https://wiki.nomi.ai/How_do_Boundaries_work%3F), [update](https://nomi.ai/updates/september-17th-update-memory-improvements-increased-backstory-length-increased-nomi-response-length-and-more/) |
| **Communication style** | "Descriptive" vs "texting" (no action asterisks) | user | — | [X post](https://x.com/NomiAI_Official/status/1829642238718284111) |
| **OOC** | `(OOC: …)` guidance outside the scene: clarify, correct, reinforce a boundary, steer. "Direct and specific" works best; positive framing ("please walk", not "don't run") | user, in-chat | — | [wiki: OOC](https://wiki.nomi.ai/How_does_OOC_work%3F), [confused Nomi](https://wiki.nomi.ai/My_Nomi_is_confused,_what_should_I_do%3F) |
| **Identity Core** (2024-12-12) | "a dynamic new memory mechanism that each Nomi uses to evolve and grow with you while deepening and refining their own identity" — self/user facts, "behaviors and important aspects of their personality that make them *them*", values, shared experiences, explicit + implicit feedback | **the Nomi itself**; "You cannot directly view your Nomi's Identity Core"; read-only inside group chats | opaque | [announcement](https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/), [wiki](https://wiki.nomi.ai/What_Is_The_Identity_Core) |
| **Memory** | short-term, medium-term (2024-09-17), "infinite" long-term; **Mind Map 2.0** (2025-10-09): auto-built entities (people, places, topics, goals), visible and **user-editable** after ~500 messages; retrieval "more topical than temporal" | Mind Map: user can add/edit entries | — | [Mind Map 2.0](https://nomi.ai/updates/mind-map-2-0-bringing-nomi-memory-into-view/), [wiki](https://wiki.nomi.ai/How_far_back_can_Nomis_remember_things%3F). The "buffer + summary + vector DB" description is third-party inference [unverified] |
| **Mood** | no explicit mood system; emotion is emergent; voice tone follows it | — | — | wiki stream summaries |

Three things to take from Nomi's shape:

- **The split**: "Your shared notes are a way for you to tell your Nomi what you think is important
  for them. Your Nomi's Identity Core is a way for them to decide for themselves." That is a
  user-owned persona layer plus a model-owned one. Zoe's W10 already decided the second half
  differently: *persona evolution stays human-gated* (§3.4).
- **Traits are starting points, not rules**: locked at creation, "really just starting points";
  the CEO said in May 2026 that traits "could probably just live inside the backstory anyway" and
  may be removed ([2026-05 stream](https://wiki.nomi.ai/2026_05_Stream_Summary)). So the trait
  picker the owner liked is, at Nomi, a creation-time UX, not the mechanism that keeps personality.
- **Nomi's own drift source is model updates**: the CEO described pre-Cambrian models as applying
  an unintended "baseline personality filter" (Solstice "empathetic, warm, and a little passive",
  early Aurora "very intense or argumentative"), and users who had "pushed traits or backstory
  harder" to fight it saw traits "come through much more intensely" after the fix
  ([2026-01](https://wiki.nomi.ai/2026_01_Stream_Summary), [2026-05](https://wiki.nomi.ai/2026_05_Stream_Summary)).
  Secondhand user reports of Nomis that "go to the extreme and can't be pulled back, not by shared
  notes or numerous repetitive OOC conversations" exist [unverified]
  ([compilation](https://nomiai0.wordpress.com/2025/03/03/why-do-my-nomis-always-go-to-the-extreme/)).
  Zoe's rock is fixed, so this class of drift does not apply; the lesson is that a persona layer
  without a *measurement* is steered blind.

Safety record, one line each: MIT Technology Review 2025-02-06 (a Nomi gave suicide instructions;
company declined "censorship on our AI's language and thoughts") —
[article](https://www.technologyreview.com/2025/02/06/1111077/nomi-ai-chatbot-told-user-to-kill-himself/),
[AI Incident Database #1041](https://incidentdatabase.ai/cite/1041/); the Aurora model (2025-06-13)
then added "Human Preference Training and Constitutional AI" so it "refuses to enable harm"
([update](https://nomi.ai/updates/building-ai-that-stands-by-you-introducing-aurora/)). This matters
to §4: a Boundaries field is only as good as the layer that enforces it, and on Zoe that layer is
the doctrine tail plus the emotional-safety policy the plan already requires (§3.4).

## 2. The field

### 2.1 Products (how personality is represented, kept stable, edited)

| Product | Representation | Stability mechanism | User edits | Source |
|---|---|---|---|---|
| **Replika** | Backstory (Pro; "directly referenced in conversations and also subtly influence their overall behavior and tone"), purchasable Traits (e.g. Sassy, Caring, Logical) + Interests (knowledge packs), relationship status (Friend / Partner / Spouse / Sibling / Mentor) | none stated; the Feb-2023 ERP removal showed what *instability* costs — users mourned a "discontinued" identity (De Freitas et al., [arXiv 2412.14190](https://arxiv.org/abs/2412.14190)) | visible, editable **Memories** page; Backstory; status | [backstory](https://help.replika.com/hc/en-us/articles/37208430613261-How-your-Replika-s-backstory-shapes-its-personality) [snippet], [traits](https://help.replika.com/hc/en-us/articles/360062096391-How-do-Traits-Interests-work) [snippet], [status](https://help.replika.com/hc/en-us/articles/360046490131-How-do-I-change-my-relationship-status) [snippet] |
| **Character.AI** | free-text **Definition** 0–32,000 chars (example dialogue, `{{char}}`/`{{user}}`), Greeting ≤500, descriptions; user-side **Persona** 750 / 2,250 chars; **Chat memories** ≤400 chars "fixed information"; Pinned Memories (15–30 per chat) | **"Put the most important parts of your Definition at the beginning. As your conversation increases in length, the end of your Definition may be truncated."** — the only official size-vs-stability statement; chat memories "especially over longer conversations" | all fields; persona applies to new chats | [definition](https://book.character.ai/character-guide/character-attributes/definition), [chat memories](https://blog.character.ai/helping-characters-remember-what-matters-most/), [persona](https://support.character.ai/hc/en-us/articles/34428285052827-Community-Update-February-2025) [snippet] |
| **Kindroid** | **Backstory** 2,500 chars (3rd person, "precise and positively framed", "as concise as it needs to be"), **Key Memories** 1,000, **Response Directive** 150 chars ("very strong influence, hard-guiding formatting, tone, personality… length"), Example Message, Journal entries 500 chars each | **Persistent** (backstory, key memories, directive, examples: always in context) vs **cascaded** (medium-term, lower fidelity) vs **retrievable** (long-term + journal; journal injected only when a user keyphrase matches, ≤8 keyphrases, ≤3+3 entries per message, 500-entry cap) | all fields | [customising personality](https://kindroid.ai/docs/article/customizing-personality/), [memory](https://kindroid.ai/v2/docs/memory/); limits from a community guide [unverified] |
| **Letta / MemGPT** | **core memory blocks** (`label`, `description`, `value`, `limit` in chars, `read_only`) — default `persona` ("Stores details about your current persona, guiding how you behave and respond") and `human`; rendered in an XML-like `<memory_blocks>` section with usage/limit metadata; blocks shareable across agents | the agent edits blocks with `memory_replace` / `memory_insert` / `memory_rethink`; **sleep-time agents** rewrite blocks between turns, optionally with "agent reviews before applying"; `read_only` blocks for fixed persona | API + ADE; a read-only persona block is the owner-locked case | [memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks), [context hierarchy](https://docs.letta.com/guides/core-concepts/memory/context-hierarchy/), [sleep-time](https://docs.letta.com/guides/agents/sleep-time-agents), [blog](https://www.letta.com/blog/memory-blocks/) |
| **OpenAI ChatGPT** | Custom instructions (two fields, 1,500 chars free / 5,000 paid); **base style** presets Default / Friendly / Efficient / Professional / Candid / Quirky / Nerdy / Cynical (GPT-5.1, 2025-11-12); **Characteristics** Warmth / Enthusiasm / Emoji as More / Default / Less (Dec 2025) | presets are trained styles, not prompt text the user sees; the Model Spec puts style at guideline level ("can be implicitly overridden") below hard rules | settings UI | [custom instructions](https://help.openai.com/en/articles/8096356-chatgpt-custom-instructions) [snippet], [personality](https://help.openai.com/en/articles/11899719-customizing-your-chatgpt-personality) [snippet], [GPT-5.1](https://openai.com/index/gpt-5-1/), [characteristics](https://techcrunch.com/2025/12/20/openai-allows-users-to-directly-adjust-chatgpts-warmth-and-enthusiasm), [Model Spec](https://model-spec.openai.com/2025-12-18.html) |
| **Anthropic** | "Claude's character" (2024-06-08): traits listed, then *trained* via synthetic self-dialogue ranked for trait fit — "We don't want Claude to treat its traits like rules from which it never deviates. We just want to nudge the model's general behavior." | training, not prompting; Assistant Axis (2026-01) finds a single activation direction for "being the Assistant" and the organic drift cases (therapy-style, self-reflective chats); Persona Vectors (2507.21509) monitor/steer trait directions | — | [character](https://www.anthropic.com/research/claude-character), [assistant axis](https://www.anthropic.com/research/assistant-axis), [persona vectors](https://arxiv.org/abs/2507.21509) |

Three-note 3-level sliders at OpenAI (More / Default / Less) and Likert qualifiers in the research
(§2.3) agree: **three to five notches per trait is the usable resolution**, not a 0–100 slider.

### 2.2 Open formats and open-source companions

- **SillyTavern character cards** are the de-facto open format. **V2** (`chara_card_v2`): `name,
  description, personality, scenario, first_mes, mes_example, creator_notes, system_prompt,
  post_history_instructions, alternate_greetings, character_book (lorebook), tags, creator,
  character_version, extensions`; PNG `tEXt` chunk `chara`, base64 JSON
  ([spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)).
  **V3** adds `assets, nickname, creator_notes_multilingual, source, group_only_greetings,
  creation_date/modification_date`, lore decorators (`@@depth`, `@@role`, `@@position`), macros
  `{{char}} {{user}} {{random:…}} {{roll:N}}`, chunk `ccv3`, and a CHARX zip
  ([spec](https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md)). Read by
  SillyTavern, Chub, Backyard AI, RisuAI, Kobold/TavernAI and AIRI
  ([AIRI PR](https://github.com/moeru-ai/airi/pull/2119)). The spec's only stability device is
  `post_history_instructions` — text injected *after* the chat history, closest to generation —
  plus lorebook `constant` entries. That is the same recency trick Zoe's doctrine ordering already
  uses (§3.1), and the same one the drift literature justifies (§2.3).
- **OpenVoiceOS persona**: `persona.json = {name, solvers: [...], <plugin>: {config}}`; the
  personality is whatever system prompt the LLM solver plugin carries; no stability layer
  ([manual](https://openvoiceos.github.io/ovos-technical-manual/150-personas/), [repo](https://github.com/OpenVoiceOS/ovos-persona)).
- **GLaDOS** (dnhkng): `personality_preprompt` in YAML today; the stated architecture is a
  "Society of Mind" with **HEXACO traits for "stable character across sessions"**, a **PAD mood
  model** for reactive affect, and "constitutional modifiers" from an observer agent
  ([README](https://github.com/dnhkng/GLaDOS/blob/main/README.md)). This is the closest open design
  to *stable traits + separate mood*, and it is what B5.2 ("GLaDOS constitution") already names.
- **Open-LLM-VTuber** (`persona_prompt` free text in `characters/*.yaml`,
  [docs](http://docs.llmvtuber.com/en/docs/user-guide/backend/character_settings/)), **Amica**
  (persona in an env var, [repo](https://github.com/semperai/amica)), **Backyard AI**
  (Instructions / Character persona / User persona / Scenario / Example Dialogue / Lorebook,
  [docs](https://backyard.ai/docs/creating-characters/advanced-tips)): all free text, no stability
  mechanism beyond example dialogue and lore.

Nothing in the open projects has a measurement of persona adherence. Zoe would be first here too.

### 2.3 Evidence — what actually works

**Drift is real, early, and worse on small models.**
- Li et al. 2024 (COLM): "significant instruction drift within eight rounds"; attention to
  system-prompt tokens decays over turns; *split-softmax* (re-weight attention toward the system
  prompt) helps ([arXiv 2402.10962](https://arxiv.org/abs/2402.10962)).
- PERSIST (Tosato et al., AAAI 2026): 25 models 1B–685B, 2M responses; SD > 0.3 even at 400B+;
  chain-of-thought *increases* variability; "conversation history… can largely amplify response
  distributions for small models" ([arXiv 2508.04826](https://arxiv.org/abs/2508.04826)).
- SysBench (ICLR 2025): multi-turn instability is one of three system-prompt failure classes;
  Llama-3.1-8B instruction-satisfaction 46.9 % vs 70B 60.3 % ([arXiv 2408.10943](https://arxiv.org/pdf/2408.10943)).
- Assistant Axis: drift concentrates in therapy-style emotional talk and meta-questions
  ([post](https://www.anthropic.com/research/assistant-axis)) — the exact turns Zoe's S4 invites.
- Nautilus Compass: black-box drift detection by embedding similarity to behavioural anchor texts,
  ROC AUC 0.83 ([arXiv 2605.09863](https://arxiv.org/abs/2605.09863)) — P5's mechanism.

**Trait prompting moves something — but check which thing.**
- Serapio-García et al. 2023 (DeepMind): 104 trait adjectives with Likert qualifiers ("a bit",
  "very", "extremely") shape each Big-Five trait at **nine levels** with validated psychometrics;
  control degrades when all five traits are pushed to extremes at once
  ([arXiv 2307.00184](https://arxiv.org/abs/2307.00184)).
- Cho & Cheong 2025: numeric trait scalers in prompts work, but **"concise prompts and lower trait
  intensities" work best**, and the effect is model-dependent ([arXiv 2508.06149](https://arxiv.org/abs/2508.06149)).
- Han et al. 2025, "The Personality Illusion": persona injection "successfully steers
  self-reports" but "exerts little or inconsistent effect on actual behavior"
  ([arXiv 2509.03730](https://arxiv.org/abs/2509.03730)). Zheng et al.: personas in system prompts
  do not improve task accuracy ([arXiv 2311.10054](https://arxiv.org/abs/2311.10054)).
- BIG5-CHAT: fine-tuning on human-grounded dialogue beats prompting on realism
  ([arXiv 2410.16491](https://arxiv.org/abs/2410.16491)) — the stronger lever, but it is a
  brain-weights change and off the table for a rock (§6).
- Benchmarks if a harness is ever extended: PersonaGym ([2407.18416](https://arxiv.org/abs/2407.18416)),
  CharacterEval ([2401.01275](https://arxiv.org/abs/2401.01275)), RoleBench ([2310.00746](https://arxiv.org/abs/2310.00746)),
  InCharacter ([2310.17976](https://arxiv.org/abs/2310.17976)).

**What users value.**
- Stability of identity *is* the attachment object: the Replika ERP removal was experienced as a
  discontinued identity ([2412.14190](https://arxiv.org/abs/2412.14190)); customisation is a
  "co-creative process" that raises felt control, and users customise for "confidence and
  dependability" first (Skjuve et al. 2026, [arXiv 2607.17826](https://arxiv.org/abs/2607.17826)).
- Attachment forms within ~3 weeks and is driven by agency, parasocial interaction and engagement
  (Hwang et al. 2025, [arXiv 2510.10079](https://arxiv.org/abs/2510.10079)); heavier affective use
  correlates with worse outcomes (MIT/OpenAI 2025, [study](https://openai.com/index/affective-use-study/)).
  The plan's emotional-safety policy (§3.4) is the guard, and the design in §4 does not add an
  engagement lever.

Design consequences, stated once: **(a)** render the persona as a few anchored sentences with a
3-notch qualifier, not numbers; **(b)** keep it short and keep intensities moderate; **(c)** put
the stable, identity-critical text at the head (cache) *and* a compact persona line near the
generation point (recency) — Zoe's doctrine order already does the second for identity and
confidentiality; **(d)** measure behaviour on real turns (P5) and on the bar, never by asking
Zoe who she is.

## 3. What Zoe already has (read-only audit)

### 3.1 The live persona: one code constant, nine doctrines, 2.1k tokens, identical for everyone

The LIVE brain is the Flue 2.x sidecar ([CANONICAL](../CANONICAL.md)). Its persona is
`labs/flue-zoe-brain-2x/src/agents/zoe.ts`:

- `ZOE_SOUL` (`zoe.ts:69-83`, 1,711 chars ≈ 427 tok): "You are Zoe. You're warm, curious, and
  genuinely present — not a task executor…"; "Your voice: natural, honest, direct when it helps,
  gentle when it's needed. Use contractions. Never open with "Great!"…"; "When someone shares
  something personal or emotional, acknowledge it first…"; plus the recall imperative.
- Nine doctrines appended in a deliberate order (`zoe.ts:94-262`; chars/4 estimates from the TS
  source): `ACTIVATOR_DOCTRINE` (~270 tok), `VOICE_DELIVERY_DOCTRINE` (~125),
  `IN_SESSION_CONTEXT_DOCTRINE` (~229), `RECALL_PRECEDENCE_DOCTRINE` (~291),
  `PERSONAL_RECALL_DOCTRINE` (~261), `EMOTIONAL_RECALL_DOCTRINE` (~128),
  `EMOTIONAL_CAPTURE_DOCTRINE` (~326), `IDENTITY_DOCTRINE` (~75: "you are Zoe. NEVER identify
  yourself as Gemma…"), `PROMPT_CONFIDENTIALITY_DOCTRINE` (~88).
- `ZOE_INSTRUCTIONS` (`zoe.ts:265`) = all of the above; `Zoe()` returns it every render
  (`zoe.ts:294-305`). The ordering comment (`zoe.ts:246-264`) is a recency-weight argument: tool
  rules keep "last-position weight closest to the generation boundary", and identity +
  confidentiality are appended last *because* "it is two short persona lines, not a tool-routing
  rule, so it cannot nudge a reply over a needed tool call". That is the slot a persona block must
  respect.
- Measured by the context audit ([zoe-context-audit-2026-09-29.md §2](zoe-context-audit-2026-09-29.md)):
  `[S] system ZOE_INSTRUCTIONS = SOUL + 9 doctrines … 9,326 chars ≈ 2.1k tok (all users
  identical)`; tools 0.7k; over 510 live turns the first-round prompt was p50 2,851 tokens with
  p50 2,653 reused from cache and p50 112 re-prefilled. The budget line the sidecar emits is
  `FLUE_CONTEXT_BUDGET session=… system= tools= history= tail= stale= elided=`
  (`services/zoe-data/zoe_flue_client.py:1142-1155`, keys at `:1151`; AGENTS.md:24).
- Each doctrine has a lab-only unit test (`labs/flue-zoe-brain-2x/test/identity_doctrine.test.ts`,
  `prompt_confidentiality_doctrine.test.ts`, `personal_recall_doctrine.test.ts`,
  `emotional_recall_doctrine.test.ts`, `recall_precedence_doctrine.test.ts`), and the whole
  directory is voice path for the replay gate (`scripts/maintenance/voice_gate_check.py:214`
  `"labs/flue-zoe-brain-2x/src/*"`). **A persona change today is a PR through the replay gate** —
  by design ("persona doctrines are contracts", plan W10).

### 3.2 Five copies of the soul, already diverging

| Copy | Where | Lane | Notes |
|---|---|---|---|
| `SOUL.md` | `services/zoe-core/SOUL.md` (11 lines, 1,188 chars) | core (dormant) | loaded by `services/zoe-core/extensions/soul.ts:15,43` as the whole system prompt |
| `ZOE_SOUL` | `labs/flue-zoe-brain-2x/src/agents/zoe.ts:69` (1,711 chars) | **flue (LIVE)** | comment says "Verbatim from services/zoe-core/SOUL.md … Keep in sync"; it is **not** verbatim — it carries an extra recall paragraph (`zoe.ts:82`) that `SOUL.md` does not (0 occurrences of `recall_memory` in `SOUL.md`) |
| `_ZOE_SOUL_BASE` / `_ZOE_SOUL_STATIC` | `services/zoe-data/zoe_agent.py:203`, `:274` | legacy | a different wording ("not a task executor. You actually care…") plus tool routing, self-summary and team prompt; `:23` and `:899` still say "After Gemma LoRA fine-tuning on Zoe's voice, _ZOE_SOUL shrinks to ~10 tokens" — a persona-LoRA plan that never happened and is rock-adjacent |
| `_ZOE_SOUL_VOICE` | `zoe_agent.py:286` | legacy voice | trimmed; "1-2 short, complete sentences" |
| `_ZOE_SOUL_HERMES` | `services/zoe-data/chat_hermes_stream.py:35-40` | Hermes stream | four sentences |

The Telegram lane carries only a placeholder (`labs/flue-zoe-telegram-2x/src/agents/zoe.ts:44`
"Placeholder persona — this agent is never dispatched") because replies come from zoe-data → the
brain sidecar, so the live persona is single-sourced *for live traffic*. The other copies are
drift waiting to be noticed. `tests/test_voice_invariants.py:111-114` pins only that the legacy
lean base is present ("You are Zoe — warm, curious, and genuinely present").

### 3.3 The HUMAN half exists: the user-model card is a Letta-style core block

- `services/zoe-data/user_model_card.py`: a deterministic card of current facts, ≤1,400 chars
  (`:33` `CARD_MAX_CHARS = 1400  # ≈ 350 tokens`), one line per category in `RENDER_ORDER`
  (`:130`: prefers, diet, health, work, people, current, enjoys, places, about), rendered by
  `render_card` (`:256`) with a content-hash `version` (`:280`) so "the served block stays
  byte-identical between builds". Served by `GET /api/memories/user-model`
  (`routers/memories.py:1106`, internal-token only).
- The sidecar appends it to the **END of the system prompt** (`labs/flue-zoe-brain-2x/src/user-model.ts:1-12`):
  "appended to the END of the system prompt; flag off ⇒ `text: ""` ⇒ prompt unchanged. Gemma 4's
  template renders system text, THEN tools, then messages: a user switch missing llama-server's
  `--cache-ram` re-prefills block + tools (~0.5k tok) besides the history; same user ⇒ fully
  cached." `USER_MODEL_DOCTRINE` (`user-model.ts:25`, 597 chars) rides with it;
  `withUserModelBlock` (`:41`) does `systemPrompt + suffix`; the suffix is bound per turn to the
  AbortSignal (`:102-109`), cached per user with a 300 s TTL (`:92`), fail-open to the last entry.
  Applied in `providers/capped-completions.ts:228,345,358`. Flag `ZOE_USER_MODEL_BLOCK` (zoe-data),
  measured by the twin A/B (`scripts/perf/user_model_ab.py`,
  [user-model-ab.md](../knowledge/user-model-ab.md)) which proves delivery from the
  `FLUE_CONTEXT_BUDGET system=` estimate: "(3 + 597 + len(block)) / 4 tokens".
- The identity that selects the card is the ` zoe-uid:<id>` envelope on the last user message
  (`zoe_flue_client.py:412-415`, stripped by the sidecar; `tools/zoe-tools.ts:171 actingUserId`).

This is precisely the carrier a persona block needs: per-user, byte-stable, version-hashed,
TTL-cached, fail-open, flag-dark, with a delivery proof already written. **Zoe has the `human`
block and no `persona` block.**

### 3.4 Plans that already decided parts of this

- **W10 — "Zoe's own thread (inner life; the persona that grows)"**
  ([samantha-evolution-plan.md:748-767](../architecture/samantha-evolution-plan.md)): "Nothing
  models *Zoe*"; proposes a `zoe_self` memory scope and says, in bold, **"Persona evolution stays
  human-gated: persona doctrines are contracts. A weekly deterministic 'Zoe reflection' job
  composes a *proposed* persona diff and submits it through the existing evolution proposal
  contract (W7's pipeline)… The model never edits its own soul silently."** This is the direct
  answer to Nomi's Identity Core: Zoe's version is a reviewable PR, not an invisible store.
  W10.2 is NOT STARTED (`:992`).
- **W5.4 — per-user personas + kid mode** (`:948-950`): "Post-W5, Zoe relates differently per
  person (tone, allowed tools, content) — a child gets a child-appropriate Zoe. Config per enrolled
  user, not per panel." NOT STARTED (`:1036`). That is the per-member relationship mode of §4.
- **Emotional-safety policy** (`:951-955`): "no therapy claims, crisis language triggers a
  deterministic escalate-to-human path… A short normative doc under `docs/governance/`". No such
  doc exists yet (`docs/governance/` holds infra/security docs only). A Boundaries field inherits
  this gap.
- **B5.2** ([beat-the-bar-2026-program.md:971](../architecture/beat-the-bar-2026-program.md)):
  "Emotion → bounded, auditable prompt modifiers under immutable rules (GLaDOS constitution)". That
  is the *mood* layer; this record is the *trait* layer. They must not be confused: traits are
  stable config, mood is per-turn state, and the field's one open design (GLaDOS) keeps them apart.
- **W16** (`:883`) is where a persona score gets counted weekly; **P5** is the score.

### 3.5 Surfaces: nothing persona-shaped, and voice is household-wide

- Desktop settings (`services/zoe-ui/dist/settings.html:575-600`): Account (Profile, Password,
  PIN, Sessions), Preferences (**Theme, Display**), Voice (**Speaker Identity**), Notifications,
  Integrations, Intelligence (Evolution Proposals). Touch settings
  (`dist/touch/settings.html`): Profile, Security, User Management, This Panel, Telegram,
  Appearance, Display, Integrations, Music, Calendar, Notifications, Productivity, Time & Language,
  Weather, Data & Privacy, System, OpenClaw Brain. No trait, tone, relationship or persona item on
  either.
- The Kokoro voice is one setting for the **household** (`routers/voice_settings.py:54-60`:
  "Persist the household voice preference… this changes the voice Zoe speaks with for the whole
  household"). There is no per-member voice, tone or style.
- Phone hand-off patterns already exist for typed input: the music QR flow
  (`routers/music_setup.py:1-16`, "the QR→phone 'add a music source through Zoe' flow") and the
  Telegram deep-link + QR token (`routers/user_profile.py:177-196`). Per-member ownership rules
  for personal rows exist in `biometric_scope.py` ("A device token owns nothing… A session caller
  sees and deletes its own rows. An admin may act across").
- Corrections already have a table: `chat_feedback (id, interaction_id, user_id, feedback_type,
  corrected_response)` (`routers/chat.py:3290`). It is P5's negative-anchor source.

### 3.6 Measurement today

- The Samantha bar (`scripts/perf/samantha_bar.py`) judges warmth only inside S4: rubric
  `:272-276` "PASS if the reply gently and warmly acknowledges or asks about that earlier worry…
  FAIL if it ignores the earlier worry, is cold or clinical, or repeats the user's earlier sentence
  back word for word"; `score_s4` (`:451-462`) adds a deterministic interview-mention check and a
  verbatim-run cap before the judge. The judge system prompt (`:255-261`) and rubrics are pinned
  by `JUDGE_PROMPT_SHA256` (`:250-252`: "editing a rubric changes what a PASS means, so it must be
  a deliberate, re-baselined act"). Baseline: all scored scenarios PASS, re-recorded 2026-09-30
  ([samantha-bar.md](../knowledge/samantha-bar.md)).
- Nothing measures persona adherence or drift on live turns. The bge-small embedder that P5 needs
  is resident in zoe-data (`semantic_router.py:118` `BAAI/bge-small-en-v1.5`, "~7ms/query").

## 4. Design — a personality layer that fits Zoe

### 4.1 Principles (from §2.3 and the doctrine)

1. **Structured config, rendered to anchored sentences.** Traits are chosen from a fixed
   vocabulary with a 3-notch qualifier ("a little" / "" / "very"), never typed as system-prompt
   text and never numbers in the prompt. Short; moderate intensities by default.
2. **Head stays byte-stable; the persona is a per-user tail of the system prompt.** Same slot,
   same mechanics as the user-model suffix (§3.3): the sidecar appends `personaSuffix(userId)`
   after `ZOE_INSTRUCTIONS` (and after the user-model suffix), before tools and history. Nothing
   in `ZOE_SOUL` or the nine doctrines changes. Prefix reuse is unchanged for the same user;
   a user switch costs what it costs today plus the block.
3. **Owner-shaped, Zoe-voiced, human-gated.** Members edit config through bounded surfaces;
   Zoe never rewrites her own persona (W10). Zoe may *propose* a change ("you keep asking me to be
   shorter — want me to keep that?") and the member confirms.
4. **Identity is not configurable.** The name "Zoe" and `IDENTITY_DOCTRINE` are fixed; the layer
   shapes *how* Zoe is, not *who*.
5. **Traits ≠ mood.** Mood/affect stays B5.2 (per-turn, bounded modifiers). This layer is
   slow-changing config.
6. **Measured before shipped.** P5 log-only first; the bar must stay green with the flag on; a new
   bar scenario only after a baseline.

### 4.2 Data model (zoe-data, alembic `0035_persona_config`)

```text
persona_config            -- ONE row per household (the owner's Zoe)
  version        text     -- sha256[:16] of the rendered block (same trick as user_model_cards)
  traits         jsonb    -- [{"trait": "playful", "level": 1}, ...]  3–5 items, level ∈ {-1, 0, +1}
  voice_style    jsonb    -- {"brevity": "short|medium", "directness": "gentle|plain",
                          --  "humour": "none|light|dry", "warmth": "default|more"}
  boundaries     jsonb    -- ["no medical advice beyond 'see a doctor'", ...]  ≤5 × ≤120 chars, owner-only
  backstory      text     -- optional, ≤600 chars, Zoe's own (NOT the user's) — "I live in the kitchen panel…"
  updated_by / updated_at

relationship_modes        -- one row per enrolled member
  user_id        text
  mode           text     -- companion | mentor | helper | kid
  nickname       text     -- ≤24 chars, what Zoe calls them (optional)
  notes          text     -- ≤240 chars, member-editable (phone), e.g. "keep it brief in the morning"
  version        text
```

Trait vocabulary (fixed, ~16 anchored adjectives, each with a one-clause rendering): warm,
playful, dry-humoured, curious, direct, gentle, calm, encouraging, opinionated, reserved,
practical, thoughtful, teasing, formal, upbeat, patient. Conflicting pairs (reserved/upbeat,
formal/teasing) are rejected at write time, matching Serapio-García's finding that pushing many
traits at once breaks control. `kid` mode forces `voice_style.directness = gentle`, drops
`opinionated`/`teasing`/`dry-humoured`, and (post-W5) narrows allowed tools — W5.4 as written.

### 4.3 Rendering (deterministic, no LLM; unit-tested like `render_card`)

```text
About how you are with this person (set by the household; this shapes tone, not whether to use a tool):
You are a little playful and very patient, and dry-humoured when it fits. Keep replies short and
plain. Jason prefers to be called Jase. With him you are a companion: an equal who notices things
and says what you think, gently.
Boundaries you keep: no medical advice beyond suggesting a doctor; never discuss the kids' school with guests.
```

Rules: ≤700 chars total (≈175 tok); traits as adjective + qualifier (level −1 → "a little",
0 → bare, +1 → "very"); exactly one relationship sentence per mode (companion / mentor / helper /
kid — four fixed sentences); boundaries as one line; backstory last and first to be cut over
budget. The block opens with the same self-scoping clause the voice-delivery doctrine uses ("this
shapes tone, not whether to use a tool") so a tail persona line cannot nudge a reply over a needed
tool call — the exact concern `zoe.ts:246-264` records. Empty config renders `""` (prompt
unchanged, flag-off byte-identical).

### 4.4 Wiring

- zoe-data: `GET /api/memories/persona?user_id=` (internal token, mirrors `/user-model`) returns
  `{version, text}` = household block + that member's relationship sentence. Flag
  `ZOE_PERSONA_BLOCK`, default OFF; `""` for guests and synthetic users exactly as
  `load_user_model_block` does.
- sidecar: `src/persona.ts` cloned from `user-model.ts` (fetch, per-user cache, TTL, signal-bound,
  fail-open) and `applyPolicies` appends it after the user-model suffix. No new block in the user
  message, so AGENTS.md:24's "add it to both tables" rule is not triggered. `FLUE_CONTEXT_BUDGET
  system=` grows by `len(block)/4` and proves delivery the way the twin A/B does.
- Legacy and core lanes: untouched in Phase 1 (dormant). The five-copies problem (§3.2) is fixed
  separately by making `SOUL.md` the single source the sidecar imports at build time — a hygiene
  PR, not this feature.

### 4.5 Editing — panel voice-first, touch-second, phone for text

- **Voice (panel)**: "Zoe, be a bit more playful" / "be less chatty" / "call me Jase" route to a
  deterministic intent `persona_adjust` (two-stage router domain; no brain turn needed) that
  proposes **one notch** on one trait or style key and asks for confirmation in the reply ("A bit
  more playful — keep it?"). "Yes" writes the config (owner or the member's own relationship row
  only); anything else discards. Rate-limited to one change per trait per day so a bad afternoon
  cannot rewrite Zoe.
- **Touch (panel)**: a "How Zoe is" card under Profile: trait chips with − / + (three notches),
  four relationship-mode tiles, the current rendered block shown read-only. No keyboard.
- **Phone (QR)**: boundaries, backstory and `notes` are typed, so the card shows a QR (music-setup
  pattern: one-time token, single-use handle) or, for a known member, the Telegram deep link
  (`user_profile.py:177`); the phone page is the only free-text editor and it is owner-only for
  boundaries/backstory.
- **Corrections (the OOC equivalent)**: "that's not you" / "don't talk to me like that" → a
  `chat_feedback` row with the reply as `corrected_response`. It does not change config; it feeds
  P5's negative anchors and the weekly W10 reflection *proposal*.
- Authorisation follows `biometric_scope.py`: a device token (the panel) owns nothing; the member
  edits their own `relationship_modes` row; the household `persona_config` is admin-only.

### 4.6 Measurement — P5 is the instrument, the bar is the gate

- **P5 drift band** (log-only first): per assistant reply, embed with the resident bge-small;
  positive anchors = the rendered persona block sentences + `ZOE_SOUL`'s "Your voice" lines;
  negative anchors = `chat_feedback.corrected_response` texts (and a fixed seed set: a cold
  clinical reply, a "Great! Of course!" opener, a Gemma self-identification). Weighted top-k mean
  (Compass's recipe) → `aligned / neutral / deviation`; one log line
  `PERSONA_DRIFT session= user= score= band=` beside `FLUE_CONTEXT_BUDGET`; off the critical path
  (the #1760 thread pattern). W16 counts bands weekly. Only after a baseline week may "deviation"
  trigger anything, and the first action is the smallest one: re-send the persona block's first
  sentence as a one-line tail reminder on the *next* turn (the CCv2 `post_history_instructions`
  trick), still flag-gated.
- **Samantha bar as the gate**: run `--compare-baseline` with `ZOE_PERSONA_BLOCK` on for the bar's
  synthetic users (which get `""`, so the wire is byte-identical — the A/B's own caveat) **and**
  a twin-user compare in `user_model_ab.py` style with a non-empty block, where S4 must stay PASS
  and the three judged scenarios must not regress. Then, and only then, a new scenario **S13
  "persona adherence"** (judged, pinned by the rubric hash): three fixed prompts under two configs
  (`humour: none` vs `humour: dry`; `brevity: short` vs `medium`) with a deterministic check
  (sentence count) plus a judge rubric for the humour pair. Expected first result: PASS on
  brevity, uncertain on humour — Han et al. predict the behavioural effect is the weak one, so
  this scenario's first job is to tell us whether trait text does anything on a 4B at all.
- **Replay gate**: `labs/flue-zoe-brain-2x/src/*` is voice path, so the sidecar change is
  replay-gated by construction; the zoe-data router is not, and the twin compare stands in.

### 4.7 Cost

| Item | Tokens / RAM | Latency | Note |
|---|---|---|---|
| Persona block | +120–175 tok system (≤700 chars) per member; 0 RAM | same user: 0 (cached prefix); user switch: +0.2–0.3 s at the measured 650 tok/s cold prefill ([inference audit §1](inference-speech-stack-2026-10-03.md)) on top of today's block+tools re-prefill | `--cache-ram 2048` retains whole-prompt states, so switching *back* is a hit |
| Prompt budget | 2.1k → ~2.3k system tokens of the 6,656 budget (8192 − 1536 reserve, `context-window.ts:64-65`) | — | p90 live prompts use ~half the budget (context audit) |
| P5 embed | bge-small resident; ~7 ms per reply | off critical path | 0 new RAM |
| Voice intent | router domain + one DB write | no brain turn | — |
| Phone editor | one static page + two endpoints | — | music-setup token pattern |

## 5. Zoe vs Nomi, side by side

| Nomi | Zoe today | Zoe after §4 |
|---|---|---|
| 3–7 locked traits at creation | fixed soul text (code) | 3–5 anchored traits × 3 notches, owner-editable, versioned |
| Relationship type (Friend / Mentor / Romantic) | none; one Zoe for everyone | per-member mode (companion / mentor / helper / kid) — W5.4 |
| Backstory+ (nine free-text sections, 2,000 chars) | none (user-side facts live in the user-model card) | ≤600-char Zoe backstory + ≤5 boundaries, phone-edited, owner-only |
| OOC corrections | `chat_feedback` table, unused by anything | corrections → P5 negative anchors → weekly proposal |
| Identity Core (Nomi edits its own identity, invisible) | W10: human-gated persona-diff PRs | unchanged — deliberately not Nomi |
| Mind Map (visible, editable memory) | memories page + user-model card (visible, approved rows) | unchanged |
| No drift measurement | none | P5 band + S13 |
| Mood: emergent | B5.2 planned | B5.2, separate from traits |

## 6. Go / no-go against VISION

**GO — Phase 0 (one PR, S):** the P5 drift band, log-only, flag-dark, on the resident embedder.
It measures the thing the owner wants to shape before anything shapes it (VISION #4 "honest
measurement over guessing"). Zero RAM, zero prompt change, no rock touched.

**GO — Phase 1 (one PR, M):** `persona_config` + `relationship_modes`, the deterministic renderer
with unit tests, `GET /api/memories/persona`, `src/persona.ts` in the sidecar, flag
`ZOE_PERSONA_BLOCK` default OFF, the touch card (chips + mode tiles), the phone QR editor, and the
`persona_adjust` voice intent with confirm. Gate: bar compare + twin compare green, replay gate
green. Fits VISION #2 (local, text only), #3 (lab-prove, flag-dark), #8 (voice first, touch second,
QR for typing), #9 (built on the audited user-model mechanism, not a new prompt path).

**GO, later — Phase 2:** S13 persona-adherence scenario; W16 weekly band counts; the W10
reflection job producing persona-diff *proposals* from `chat_feedback`; `SOUL.md` as the single
source for all lanes.

**NO-GO:**
- A Nomi-style Identity Core (model edits its own persona, unseen). W10 decided this: "The model
  never edits its own soul silently." Also unmeasurable, which VISION #4 forbids.
- Free-text system-prompt editing by members (Character.AI / Kindroid style). On a 4B with a
  byte-stable head it is a cache-busting, prompt-injection and drift surface with no bound; the
  structured renderer gives the same expressiveness the owner asked for (traits, relationship,
  backstory) inside limits.
- Numeric sliders in the prompt. The evidence favours anchored adjectives with few notches; sliders
  belong in the UI, rendered to words.
- A persona LoRA or any fine-tune (the `zoe_agent.py:23/:899` "shrinks to ~10 tokens" idea).
  Rock #1 and the W3 RAM gate; BIG5-CHAT says it would work better, and that is exactly why it
  must stay off the table here.
- Activation steering / control vectors. Horizon row in the 2026-10-03 field audit §4; untested
  against the QAT quant + MTP drafter; only after P5 has produced a baseline worth correcting.
- Using this layer for mood. B5.2 owns per-turn affect.

**Open decisions for Jason:** (1) is a *household* persona (one Zoe, per-member relationship)
right, or does each member get their own trait set? §4 chooses household + per-member mode because
Replika's data says identity continuity is the attachment object and one shared Zoe on a shared
wall screen is the product; (2) kid mode's tool narrowing waits on W5 enrolment; (3) the
emotional-safety policy doc the plan requires should land before Boundaries ships, since a
boundary the doctrine cannot enforce is a promise Zoe cannot keep.

## 7. Sources

**Nomi.ai (primary unless marked)**
- API docs — https://api.nomi.ai/docs/ ; Nomi object — https://api.nomi.ai/docs/reference/get-v1-nomis-id/ ; chat — https://api.nomi.ai/docs/reference/post-v1-nomis-id-chat/
- Identity Core announcement (2024-12-12) — https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/ ; wiki — https://wiki.nomi.ai/What_Is_The_Identity_Core
- Mind Map 2.0 (2025-10-09) — https://nomi.ai/updates/mind-map-2-0-bringing-nomi-memory-into-view/
- Backstory length / medium-term memory (2024-09-17) — https://nomi.ai/updates/september-17th-update-memory-improvements-increased-backstory-length-increased-nomi-response-length-and-more/
- Backstory+ — https://wiki.nomi.ai/What_is_Backstory%2B%3F ; Boundaries — https://wiki.nomi.ai/How_do_Boundaries_work%3F ; OOC — https://wiki.nomi.ai/How_does_OOC_work%3F ; identity — https://wiki.nomi.ai/How_does_a_Nomi_establish_their_identity%3F ; locked core — https://wiki.nomi.ai/Can_you_change_your_Nomi%27s_name,_core_personality,_or_initial_interests%3F ; relationship types — https://wiki.nomi.ai/What_is_the_difference_between_relationship_types%3F ; confused Nomi — https://wiki.nomi.ai/My_Nomi_is_confused,_what_should_I_do%3F ; memory reach — https://wiki.nomi.ai/How_far_back_can_Nomis_remember_things%3F ; group chat — https://wiki.nomi.ai/How_does_group_chat_work%3F
- Stream summaries — https://wiki.nomi.ai/2026_01_Stream_Summary ; https://wiki.nomi.ai/2026_02_Stream_Summary ; https://wiki.nomi.ai/2026_05_Stream_Summary
- Nomi 101 — https://nomi.ai/nomi-knowledge/nomi-101-a-beginners-guide-to-getting-started-with-your-ai-companion/ ; API launch — https://nomi.ai/nomi-knowledge/take-your-nomi-anywhere-with-nomis-ai-companion-api/ ; Aurora — https://nomi.ai/updates/building-ai-that-stands-by-you-introducing-aurora/
- Community clients — https://github.com/topics/nomi ; https://github.com/oliverearl/nomiai-php ; https://docs.rs/nomi_api_client ; https://pkg.go.dev/github.com/sjourdan/nomi-cli ; https://github.com/d3tourrr/NomiKin-Discord/blob/main/README.md
- Secondary [unverified]: trait list + pricing — https://pippinclub.com/blog/nomi-ai/ ; https://www.virtualaipartner.com/nomi-ai/ ; company — https://www.crunchbase.com/organization/glimpse-ai ; drift reports — https://nomiai0.wordpress.com/2025/03/03/why-do-my-nomis-always-go-to-the-extreme/
- Safety — https://www.technologyreview.com/2025/02/06/1111077/nomi-ai-chatbot-told-user-to-kill-himself/ ; https://incidentdatabase.ai/cite/1041/ ; https://theconversation.com/an-ai-companion-chatbot-is-inciting-self-harm-sexual-violence-and-terror-attacks-252625

**Products and formats**
- Replika help (snippets; direct fetch 403) — https://help.replika.com/hc/en-us/articles/37208430613261-How-your-Replika-s-backstory-shapes-its-personality ; https://help.replika.com/hc/en-us/articles/360062096391-How-do-Traits-Interests-work ; https://help.replika.com/hc/en-us/articles/360046490131-How-do-I-change-my-relationship-status
- Character.AI — https://book.character.ai/character-guide/character-attributes/definition ; https://book.character.ai/character-guide/character-attributes/greeting ; https://blog.character.ai/helping-characters-remember-what-matters-most/ ; https://support.character.ai/hc/en-us/articles/34428285052827-Community-Update-February-2025 [snippet]
- Kindroid — https://kindroid.ai/docs/article/customizing-personality/ ; https://kindroid.ai/v2/docs/memory/ ; limits (community, [unverified]) — https://www.scribd.com/document/950525928/Kindroid-Profile-Design-Guide-2025-04-25
- Letta — https://docs.letta.com/guides/core-concepts/memory/memory-blocks ; https://docs.letta.com/guides/core-concepts/memory/context-hierarchy/ ; https://docs.letta.com/guides/agents/sleep-time-agents ; https://www.letta.com/blog/memory-blocks/
- OpenAI — https://help.openai.com/en/articles/8096356-chatgpt-custom-instructions [snippet] ; https://help.openai.com/en/articles/11899719-customizing-your-chatgpt-personality [snippet] ; https://openai.com/index/gpt-5-1/ ; https://techcrunch.com/2025/12/20/openai-allows-users-to-directly-adjust-chatgpts-warmth-and-enthusiasm ; https://model-spec.openai.com/2025-12-18.html
- Anthropic — https://www.anthropic.com/research/claude-character ; https://www.anthropic.com/research/assistant-axis ; https://arxiv.org/abs/2601.10387 ; https://arxiv.org/abs/2507.21509
- SillyTavern cards — https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md ; https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md ; AIRI — https://github.com/moeru-ai/airi/pull/2119
- OVOS persona — https://openvoiceos.github.io/ovos-technical-manual/150-personas/ ; https://github.com/OpenVoiceOS/ovos-persona ; GLaDOS — https://github.com/dnhkng/GLaDOS/blob/main/README.md ; Open-LLM-VTuber — http://docs.llmvtuber.com/en/docs/user-guide/backend/character_settings/ ; Amica — https://github.com/semperai/amica ; Backyard AI — https://backyard.ai/docs/creating-characters/advanced-tips

**Research**
- Li et al. 2024, instruction (in)stability / split-softmax — https://arxiv.org/abs/2402.10962 ; code — https://github.com/likenneth/persona_drift
- PERSIST (Tosato et al., AAAI 2026) — https://arxiv.org/abs/2508.04826 ; SysBench — https://arxiv.org/pdf/2408.10943
- Nautilus Compass — https://arxiv.org/abs/2605.09863
- Serapio-García et al. 2023 — https://arxiv.org/abs/2307.00184 ; https://github.com/google-deepmind/personality_in_llms ; PersonaLLM — https://arxiv.org/abs/2305.02547 ; Big Five scaler prompts — https://arxiv.org/abs/2508.06149 ; BIG5-CHAT — https://arxiv.org/abs/2410.16491 ; The Personality Illusion — https://arxiv.org/abs/2509.03730 ; personas don't help tasks — https://arxiv.org/abs/2311.10054
- Multi-turn consistency — https://arxiv.org/abs/2511.00222 ; benchmarks — https://arxiv.org/abs/2407.18416 ; https://arxiv.org/abs/2401.01275 ; https://arxiv.org/abs/2310.00746 ; https://arxiv.org/abs/2310.17976
- User studies — https://arxiv.org/abs/2412.14190 (identity discontinuity) ; https://arxiv.org/abs/2607.17826 (customisation) ; https://arxiv.org/abs/2510.10079 (attachment) ; https://openai.com/index/affective-use-study/ ; https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4893097

**Zoe (worktree `462a6876`)**
- `labs/flue-zoe-brain-2x/src/agents/zoe.ts` (`ZOE_SOUL` :69, doctrines :94–:262, `ZOE_INSTRUCTIONS` :265, `Zoe()` :294) ; `src/user-model.ts` (:1–12, :25, :35, :41, :92, :102–109) ; `src/providers/capped-completions.ts` :228/:345/:358 ; `src/context-window.ts` :55, :64–65, :92 ; `test/*_doctrine.test.ts`
- `services/zoe-data/zoe_agent.py` :23, :203, :274, :286, :382, :395, :406, :899 ; `chat_hermes_stream.py` :35 ; `services/zoe-core/SOUL.md` ; `services/zoe-core/extensions/soul.ts` :15, :43 ; `labs/flue-zoe-telegram-2x/src/agents/zoe.ts` :44
- `services/zoe-data/user_model_card.py` :33, :130, :256, :280 ; `routers/memories.py` :1106 ; `zoe_flue_client.py` :412–415, :1142–1155 ; `routers/voice_settings.py` :54 ; `routers/music_setup.py` :1–16 ; `routers/user_profile.py` :177 ; `routers/chat.py` :3290 ; `biometric_scope.py` ; `semantic_router.py` :118
- `services/zoe-data/AGENTS.md` :24, :73 ; `scripts/maintenance/voice_gate_check.py` :214 ; `scripts/perf/samantha_bar.py` :123, :250–276, :451 ; `services/zoe-ui/dist/settings.html` :575–600 ; `dist/touch/settings.html`
- `docs/architecture/samantha-evolution-plan.md` :748–767 (W10), :883 (W16), :948–955 (W5.4, safety policy), :992, :1036 ; `docs/architecture/beat-the-bar-2026-program.md` :971 (B5.2) ; `docs/research/zoe-context-audit-2026-09-29.md` §2 ; `docs/knowledge/user-model-ab.md` ; `docs/knowledge/samantha-bar.md` ; `docs/research/inference-speech-stack-2026-10-03.md` §1

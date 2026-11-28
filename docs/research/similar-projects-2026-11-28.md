---
type: research
title: "Similar projects: what Zoe takes from them (2026-11-28)"
date: 2026-11-28
status: research-only. Six read-only survey lanes (local voice assistants; real-time voice frameworks; open-source personal agents; memory systems; generated interfaces and commercial home hubs; browser agents and agent safety). Third-party code was read, never built or run. Nothing here is built, flipped or deployed. The order of work lives in ROADMAP.md, not here.
description: What comparable open projects and commercial hubs do that a local, multi-member household voice assistant on one small box should take, what to leave, the faults in Zoe that the comparison exposed, and one ranked list of fifteen first steps mapped onto the nod study's final build order.
evidence: six survey reports (fleet survey 2026-11-28); the nod deep dive's final build order (2026-11-28); samantha-frontier-2026-11-11.md for the repo's own measured figures.
---

# Similar projects: what Zoe takes from them (2026-11-28)

## Summary

- **Already ahead.** Zoe's kernel, authority ranks for memory, the durable forget ledger, two timelines on every memory row, quote-backed retirement, the spoken mute and the pull-not-push default are ahead of every open project read. None of those projects is family-grade, and two say outright that separating their users is not a security boundary.
- **Most valuable find 1: the wake turn.** Home Assistant's new voice stack does four things Zoe does not: a second-stage wake check, a warm and gain-normalised end-of-speech detector, silence on a false wake, and a cut aligned to the end of the wake phrase.
- **Most valuable find 2: ordering memory by when things were said.** Graphiti retires a fact by when it became true, not when it arrived. Zoe orders by arrival, so a late re-pass can retire a newer fact.
- **Most valuable find 3: one foreground-priority brain slot.** Leon's scheduler lets a voice turn hold the model through its tool calls, and background work waits. In Zoe a card composer and other helpers compete with the voice brain on equal terms.
- **Most of the rest:** turn-level speech biasing, held requests with slot-filled fields for errands, and cards that bind to live data.

## 1. Local voice assistants

**Projects read**
- Home Assistant Assist pipeline (Apache-2.0, very active).
- OHF "Voice Support" app (first release 2026-11-13, a Labs preview; the most important find).
- Voice PE firmware and ESPHome microWakeWord (active).
- linux-voice-assistant, which replaces wyoming-satellite (active).
- openWakeWord (last commit 2025-12-30, slowing).
- OVOS dinkum listener (active).
- Willow (low activity).
- speech-to-phrase (moved into OHF apps).
- Rhasspy 3, Neon and Mycroft are dormant and were not read.

**Mechanisms worth taking**
- **Second-stage wake check.** The satellite sends 1.5 s of audio from before the wake. A small Kaldi decoder scores it against four grammars: the wake phrase, background speech, the phrase minus its last word, and a list of confusable phrases. It rejects when the prefix, the last word or a confusable phrase scores better than the wake phrase. The device can then run an eager first stage: its cutoff drops from 0.85 to 0.40, and it shows a light instead of a chime. Zoe today builds openWakeWord with no `vad_threshold` and no verifier, and confirms with N hits in a window. First step: add a VAD-gate arm to the existing wake replay tool. openWakeWord's own verifier is a logistic regression over its shared embeddings that costs almost nothing, so train it on the household's own false wakes, on the box.
- **Endpointing.** The end-of-speech detector is warmed on the wake-word audio. Started cold it scored a command about 0.4; warm, about 0.8. Its input only, not the recogniser's, is gained to −30 dBFS, judged from the wake word's loudest 32 ms. A command spoken across the room had arrived 23 dB quieter. Zoe resets its Silero stream at the wake and never feeds it the pre-roll. First step: an arm in the offline turn-ending evaluation that feeds the pre-roll and applies gain to the detector's input only. The audio sent to the speech model stays untouched, which respects Zoe's finding that blind sample trims regress results.
- **Silent false wakes.** The Voice PE ignores "no text recognised" without a sound. Zoe plays a retry chime on every empty first turn. First step: one branch on the wake turn — empty transcript plus low VAD means no sound, log it as a false wake and keep the clip.
- **Aligned cut.** HA drops exactly the wake audio, at the verifier's alignment. The speech-model binding Zoe pins exposes word timestamps, so a re-decode from the aligned start is possible when the first transcript looks broken.
- **Duplicate wakes across rooms.** HA lets the first satellite win within 2 s. Willow picks the loudest. Loudest-wins is the later choice for multiple rooms.

**Measured numbers** (from the vendor's own corpus of four devices and six speakers, so vendor-reported)
- Adding a Parakeet second opinion raised verified real wakes from 69% to 91%, and for a child from 25% to 88%.
- It also raised near-miss accepts from 10.1% to 21.9%, and overheard-speech accepts from 0.19% to 0.29%.
- First words cut off fell from 30% to 23%.
- openWakeWord's README targets fewer than 0.5 false accepts per hour on a dinner-party corpus.

**Weaker than its reputation**
- HA still clips first words on 23% of its own corpus.
- Voice Support verifies only three English wake words, and its aarch64 build was untested.
- linux-voice-assistant's default streams only after the chime, so a command said in the same breath as the wake word is lost.

## 2. Real-time voice frameworks

**Projects read**
- Pipecat (BSD-2, commit 2026-11-27).
- LiveKit Agents (Apache-2.0; its turn-detector weights use a separate licence, and its adaptive-interruption model is a billed cloud call).
- Kyutai Unmute (last commit 2026-09).
- Moonshine 0.1.3 itself.
- Smart Turn (still v3.2).
- TEN turn detection (built on a 7B model, too big for the box).
- OpenAI Realtime `semantic_vad` (docs only).
- ServiceNow EVA (bot-to-bot testing).

**Mechanisms worth taking**
- **Contextual biasing in the speech model.** Moonshine builds a trie of key terms. Before each argmax it adds a logit bonus of `boost*(1+ln(depth))` to tokens that continue a term, and the list can change between decodes. Zoe applies a keyterm list once, at model load. The two lanes disagree on whether that list is empty or holds twenty static terms; that is **unresolved**. First step: an arm in the speech-accuracy A/B that compares "this turn's context list" (room devices, the speaker's list names, household names) with the static list, at the default boost of 2.0.
- **Curating key terms.** LiveKit confirms a term only by a clean spell-out, a confident use by the assistant, or an explicit correction; "recurrence alone never confirms". Zoe's night lane already emits speech-term proposals. First step: give those proposals a pending or confirmed state.
- **Empty-turn policy (Pipecat).** An empty turn that interrupted the assistant gets one re-ask that repeats its question. One that arrived while idle gets silence, and there are never two in a row.
- **Learned pauses (LiveKit).** The minimum end-of-turn delay is an exponential moving average of each speaker's own pauses. An immediate re-speak counts as a false close. First step: compute it per person offline from the panel's logs.
- **Latency attribution (Pipecat).** Per-stage contributions must sum exactly to the time from speech end to first sound, each tagged with its owner. Zoe's timing line has no residual field.
- **Flush and word-confirmed barge-in (Unmute).** At the endpoint, push zeros so the streaming recogniser finalises at once. Commit a barge-in only when a real word appears.

**Measured numbers.** Moonshine's own sweep says a boost above 2.0 harms both key terms and other words, and long lists hurt. EVA reports named-entity transcription as the dominant failure, and pass@3 well above pass^3 everywhere (vendor-reported). The repo's records put speech end to first sound at about 2.7 to 3.0 s median, on different samples.

**Weaker than its reputation.** Moonshine's per-word confidence is a constant 1.0 in the source, so no re-ask can be built on it. LiveKit's best turn features are either cloud-only or under a separate model licence.

## 3. Open-source personal agents

**Projects read**
- Hermes Agent (MIT, very active).
- OpenClaw (MIT, active daily).
- Leon (MIT, active; its "pulse" loop is now real code).
- Home Assistant's AI Task, `evaluate` and LLM helpers (Apache-2.0).
- Goose (Apache-2.0, read briefly).
- Agent Zero, Letta (slowing), Khoj (AGPL), Suna, Jan, AnythingLLM, Open Interpreter, OpenHands and the small "family assistant" projects: skimmed or skipped.

**Mechanisms worth taking**
- **Escalating decline (Leon).** A declined suggestion puts that intent and target on a 1-day, then 7-day, then 30-day cooldown. It also suppresses similar items and stores a preference. Zoe halves the score of one dismissed item and has no class-level escalation. First step: key suppression on member, class and subject, with the ladder.
- **Foreground-priority slot (Leon).** A foreground turn holds the one local model slot through its tools, and a request's timeout starts only once it gets the slot. Zoe uses a plain semaphore.
- **Standing intents (OpenClaw).** "When someone mentions X, remind me Y", matched by keyword with no model call. Defaults are a 24-hour cooldown, 3 fires and a 90-day expiry; cancelling is explicit. Zoe has nothing like it. First step: a trigger table matched in SQL before the brain prompt, plus a presence trigger.
- **Typed decisions (HA `evaluate`, OpenClaw decision role).** Returns a yes-probability, a choice or a score. First step: one `decide()` helper that constrains the model to one answer token and reads its probabilities.
- **What not to remember (Hermes).** The after-turn review bans negative claims about tools, environment failures and failed attempts written up as procedure. First step: add those clauses to the self-review and struggle-proposal prompts.
- **Wake gate and silent marker for scheduled jobs (Hermes).** A deterministic pre-check can stop the model from running at all, and missed runs collapse into one "missed ×N" row.
- **Small-model tool hygiene (HA).** Short ids, a preference for the speaking device's area, and repair of empty or string-encoded tool arguments.

**Weaker than its reputation.** OpenClaw says its multi-user ownership is "not a security boundary". Hermes separates people by profile only. Leon trusts a model to classify the owner's reaction. Hermes's review defaults to "be active", which invites churn.

## 4. Memory systems

**Projects read**
- Graphiti (Apache-2.0, active).
- mem0 v3 (Apache-2.0, active).
- MemOS and its MemReader models (Apache-2.0).
- LangMem (MIT).
- Letta (moved to letta-code).
- The ChatGPT and Claude memory help pages, plus about a dozen papers.
- Hindsight was not re-opened: the October bake-off verdict kept Zoe's own memory.

**Mechanisms worth taking**
- **Ordering by valid time (Graphiti).** A late-arriving older fact is retired by the newer one, not the other way round. The contradiction prompt now forbids inferring a change from recency alone, and treats activity on a project as no change of role. A deterministic pass sets future-tense facts to start when they were said. Zoe's conflict pairing sorts by arrival time. First step: a bench cell where a statement said earlier but ingested later must not retire the current fact; then sort by the time it was said.
- **Debounced extraction (LangMem) and a "defer" outcome (MemReader).** Extraction runs once after a conversation goes quiet. An incomplete fragment is held for later rather than dropped. First step: count deferrable fragments in Zoe's empty extractor passes.
- **Per-item memory controls (ChatGPT, Claude).** "Sources", "Why this memory?" and "Don't mention this again" (mute without delete). Zoe has the backends for all three actions but no screen for them.

**Measured numbers**
- Zoe's own records (2026-11-11): the idle extractor kept zero facts over 25 passes, and memory was used in 2 of 32 member replies.
- Hindsight's 91.4% on LongMemEval needs a large cloud model. With a 20B open model it scores 83.6%.
- An independent re-run by a rival vendor got mem0 at 57.5%, then 73.8%, against a claimed 93.4% (unverified).
- MERIT: agents used a correctly retrieved updated value only 55% of the time.
- HaluMem: the update step is the weakest stage in every system.
- No neutral leaderboard exists, and nothing published is near a 4B model with an 8k context, so Zoe's own bench stays the instrument.

**Weaker than its reputation.** mem0 is now add-only, so a correction never retires the old fact, and it extracts from assistant turns too. MemOS keeps its "dream" signals in memory, where they are lost on restart, and cross-checks overwrites only in batches of five or more.

## 5. Generated interfaces and commercial home hubs

**Projects read**
- Google A2UI (Apache-2.0, v1.0, active).
- Vercel json-render (Apache-2.0, active).
- Tambo (MIT).
- MCP-UI / MCP Apps (Apache-2.0).
- Thesys OpenUI, CopilotKit, the OpenAI Apps SDK, Vercel AI SDK 6 and assistant-ui (docs only).
- Alexa+, Gemini for Home, Apple HomeView, Samsung and Josh.ai (docs and press).

**Mechanisms worth taking**
- **Tool-declared UI (MCP Apps).** The tool, not a model, declares its card, and the host fills it from the result. This is the missing middle tier for Zoe: no model call, always correct. First step: an optional card tree with bindings in one skill manifest.
- **Live data binding (A2UI, Tambo).** Each card has a surface id. Props point into a data model, and an update message changes the data without resending the layout. Tambo puts what is on screen into the next turn's context, so "add eggs to that" changes the card already showing. Zoe's cards have static props and no surface id.
- **A way to decline.** Allow a `None` root in the grammar, behind a cheap pre-check for structure, so chit-chat gets no card.
- **Bounded primitives (json-render).** Compact Table and Chart shapes, plus Countdown, Timeline, Fields, Callout and a "what I did" row with an undo phrase.
- **What it did (HA).** The conversation API returns lists of succeeded and failed targets.
- **Two-factor private view (Apple HomeView).** Personal information shows only when a face is recognised and that person's phone is present and recently unlocked.

**Measured numbers.** A2UI Express claims 55–70% fewer tokens and names a Gemma 4 model as its target. Its optimiser was actually run on a cloud model, with pass rates moving between 83% and 95%. OpenUI claims 52–67% fewer tokens, on a GPT tokenizer. Both are vendor figures.

**Weaker than its reputation**
- A2UI's countdown example is static: the agent has to push every tick.
- Reviewers report Gemini for Home often shows nothing useful and takes 7–10 s for light commands (press, unverified).
- Alexa+ shows full-screen ads, and its spoken "by the way" prompts are long disliked.
- HA's success lists are reported empty even when the action ran.

## 6. Browser agents and agent safety

**Projects read**
- browser-use (MIT, active; V4 drives raw CDP).
- Stagehand (MIT, active).
- CaMeL (Apache-2.0, research code, dead since 2025-06).
- Playwright MCP (Apache-2.0).
- Microsoft FIDES (blog only; repository not reachable, code unverified).
- Skyvern (AGPL, docs only).
- Published safety material from Anthropic, OpenAI, Google, Meta and AWS.

**Mechanisms worth taking**
- **Hold the request, not the click.** browser-use's guard intercepts requests at the request stage on its own CDP connection. Holding every state-changing request and form submit until the kernel's approval lands makes the commit point deterministic.
- **Slot-filled sensitive fields (CaMeL, Stagehand).** The model sees only placeholders. Values come only from the adult's words or kernel rows, so page text cannot set what gets committed.
- **Read-write origins fixed per errand (Chrome).**
- **An accessibility-tree reader with one-line action outcomes (browser-use),** but without its auto-accepted dialogs.
- **A route cache that self-repairs (Stagehand),** where a repaired route needs one supervised success before reuse.
- **A pass/fail judge that keeps at most five lessons with no personal values.**
- **A permission grid as data,** enumerated exhaustively, with property tests and a plain-language impact diff for every change.

Zoe's browser broker defines action classes and a confirmation flag that nothing sets, and it has no click or type API.

**Measured numbers**
- Attacks on Claude in Chrome succeeded 23.6% of the time, then 11.2%, then 1.4% with safeguards (vendor). The vendor still calls 1.4% "meaningful risk".
- Adaptive attackers beat 12 published defences at over 90%.
- CaMeL solves 77% of tasks, against 84% with no defence.
- A reasoning-blind auto-mode classifier misses 17% of real overeager actions, with 0.4% false alarms (vendor).
- On Online-Mind2Web most agents finish about 30% of real tasks.

**Weaker than its reputation.** browser-use auto-accepts "Confirm payment?" dialogs. Its own guard is "not a sandbox". The ChatGPT agent's confirmation step is trained behaviour, not a rule.

## Faults and gaps in Zoe that the comparison exposed

- Conflicting memories are ordered by arrival time, not by when they were said (read from code).
- Every near-silence false wake plays a retry chime (read from code).
- The wake turn's end-of-speech detector starts cold and never sees the pre-roll (read from code).
- Speech-model keyterm biasing is static, set once at load; whether the list is empty or holds twenty terms is unresolved (read from code).
- Composing a card posts to the voice brain's own server (read from code).
- The brain slot is a plain semaphore with no foreground priority (read from code).
- Dismissed notices are halved one by one, with no class-level escalation (read from code).
- The wake model runs with no VAD gate and no verifier, and noise suppression is absent on the panel computer (read from code).
- The idle extractor kept zero facts over 25 passes, and memory was used in 2 of 32 member replies (measured in the repo's own records).
- Speech-end-to-first-sound time is not attributable stage by stage; the timing line has no residual (measured in the repo's own records).
- Card props are static, with no surface id, binding or update (read from code).
- No prospective memory ("next time X, tell them Y") exists (read from code).
- Small classifiers each parse their own JSON, and there is no typed decision helper (read from code).
- The browser broker's confirmation flag is never set, and there is no click or type API (read from code).

## Fifteen things to do, ranked

1. **Silent false wakes.** First step: a wake-turn branch — empty transcript plus low VAD means no sound, log it and keep the clip. Build-order item: new item.
2. **Supersede by when said.** First step: a bench cell for a late-ingested older statement, then sort by the time it was said. Build-order item: new item.
3. **Foreground-priority brain slot, with card composing off it.** First step: a two-class queue where timeouts start at grant, and a measurement of composer prefix eviction. Build-order item: item 4.
4. **Warm, gain-normalised wake-turn endpointer.** First step: an offline evaluation arm that feeds the pre-roll and gains only the detector's input. Build-order item: new item.
5. **Per-turn keyterm biasing, curated at night.** First step: a context-list vs static-list A/B in the speech-accuracy report. Build-order item: new item.
6. **Wake VAD gate plus a verifier trained on the box's own false wakes.** First step: a VAD-gate arm in the wake replay tool, with a near-silence negative class. Build-order item: new item.
7. **One re-ask for an empty or fragment turn that interrupted Zoe, silence for an idle one.** First step: a re-ask phrase plus a consecutive counter. Build-order item: item 3.
8. **"What I did" rows with success and failed targets and an undo phrase.** First step: a success/failed contract on every action turn. Build-order item: item 5.
9. **Escalating, class-level decline suppression.** First step: key on member, class and subject, with a 1/7/30-day ladder. Build-order item: item 9, with a scenario in item 2.
10. **Hold the committing request, and fill sensitive fields from slots.** First step: a request-stage hook in the browser executor, tested against a local page. Build-order item: item 7.
11. **Memory metrics and privacy probes.** First step: a served-and-used count per reply, changed-fact cells, and cross-member, child and guest leak probes. Build-order item: item 2.
12. **Templated card tier with a `None` root.** First step: a card tree with bindings in one skill manifest, plus a no-card test for chit-chat. Build-order item: item 8.
13. **One typed decide() helper.** First step: constrain the model to one answer token and read its probabilities; move the reaction and urgency classifiers onto it. Build-order item: item 4.
14. **Permission grid as data, with exhaustive tests and an impact diff.** First step: enumerate the cells and add a snapshot test. Build-order item: item 5.
15. **Memory-writing safety.** First step: Hermes's "do not capture" clauses in the self-review prompts; promote a memory only when recalled by two or more distinct queries; refuse lossy rewrites. Build-order item: new item.

## Do not take

- **Parakeet as a second wake opinion:** doubles near-miss accepts and costs about 380 MB.
- **Voice Support as a dependency:** three English wake words, aarch64 untested.
- **LiveKit turn detector and adaptive interruption:** separate licence, cloud billed.
- **TEN turn detection:** a 7B model, too big.
- **Ultravox, Moshi and full duplex:** swaps a fixed model.
- **Moonshine word confidence:** a constant 1.0.
- **A2UI Express and OpenUI formats:** savings measured on cloud tokenizers, lenient parsing.
- **Vega charts:** too heavy for a small model.
- **mem0's add-only extraction:** corrections never retire the old fact.
- **MemOS dream:** beta, lost on restart.
- **Hindsight:** already decided against.
- **The CaMeL interpreter wholesale:** dead code and a task cost; take the slot idea only.
- **Skyvern and Khoj:** AGPL.
- **Goose tool emulation:** desktop-only value.
- **Auto-accepting dialogs:** silent payments.
- **Any classifier as a guarantee:** adaptive attacks beat them.
- **Hermes's "be active" review default:** churn.
- **Spoken unprompted suggestions, and ads:** the most-disliked hub behaviours.
- **Loudest-wins across rooms:** later, once there are several rooms.

## Sources

**Local voice assistants**
- https://github.com/OHF-Voice/app-voice-support
- https://www.simon42.com/home-assistant-voice-support-app/
- https://github.com/home-assistant/core
- https://github.com/esphome/home-assistant-voice-pe
- https://github.com/esphome/esphome
- https://github.com/dscripka/openWakeWord
- https://github.com/OHF-Voice/linux-voice-assistant
- https://github.com/OpenVoiceOS/ovos-dinkum-listener
- https://github.com/rhasspy/wyoming-satellite
- https://github.com/toverainc/willow
- https://github.com/OHF-Voice/speech-to-phrase
- https://github.com/kahrendt/microWakeWord
- https://www.home-assistant.io/voice_control/about_wake_word

**Real-time voice frameworks**
- https://github.com/pipecat-ai/pipecat
- https://github.com/livekit/agents
- https://github.com/kyutai-labs/unmute
- https://github.com/pipecat-ai/smart-turn
- https://github.com/TEN-framework/ten-turn-detection
- https://github.com/moonshine-ai/moonshine
- https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/
- https://livekit.com/blog/adaptive-interruption-handling
- https://developers.openai.com/api/docs/guides/realtime-vad
- https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/
- https://huggingface.co/blog/ServiceNow-AI/eva
- https://pypi.org/project/calibrate-agent/
- https://arxiv.org/abs/2511.04133
- https://dev.to/sagarkava/namo-turn-detection-v1-semantic-turn-detection-for-ai-voice-agents-4deg

**Open-source personal agents**
- https://github.com/NousResearch/hermes-agent
- https://hermes-agent.nousresearch.com/
- https://runtimewire.com/article/nous-research-hermes-agent-v023-unified-gateway-browser-exec
- https://hermesatlas.com/reports/state-of-hermes-november-2026
- https://releasebot.io/updates/nousresearch
- https://github.com/openclaw/openclaw
- https://docs.openclaw.ai/releases/2026.9.4
- https://docs.openclaw.ai/releases/2026.10.10.md
- https://dev.to/instantclaw/openclaw-v2026429-update-smarter-messaging-sharper-memory-and-rock-solid-reliability-2n41
- https://www.skool.com/build-and-automate-2188/openclaw-2026412-ships-active-memory-plugin
- https://github.com/leon-ai/leon
- https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant
- https://github.com/skye-harris/hass_local_openai_llm
- https://community.home-assistant.io/t/so-you-want-to-ai-in-home-assistant/1021777
- https://github.com/aaif-goose/goose
- https://github.com/agent0ai/agent-zero
- https://newreleases.io/project/github/agent0ai/agent-zero/release/v2.15
- https://github.com/Jayphen/homeclaw
- https://github.com/MarlBurroW/hivekeep
- https://community.home-assistant.io/t/hestia-a-local-llm-house-brain-that-treats-home-assistant-as-its-hands-no-cloud-agpl/1016481
- https://htekdev.github.io/home-os-site/

**Memory systems**
- https://github.com/getzep/graphiti
- https://github.com/mem0ai/mem0
- https://github.com/MemTensor/MemOS
- https://github.com/langchain-ai/langmem
- https://github.com/letta-ai/letta
- https://huggingface.co/MemTensor/MemReader-4B/blob/main/README.md
- https://arxiv.org/abs/2604.07877
- https://anthropic.com/research/components-of-agent-memory
- https://arxiv.org/abs/2609.05441
- https://arxiv.org/abs/2605.30771
- https://arxiv.org/abs/2611.11929
- https://arxiv.org/html/2512.12818v1
- https://dev.to/gaurav_dadhich/the-state-of-ai-memory-in-2026-claimed-vs-observed-1n94
- https://arxiv.org/html/2507.05257v3
- https://arxiv.org/pdf/2603.07670
- https://arxiv.org/pdf/2511.03506
- https://arxiv.org/abs/2611.10439
- https://www.alphaxiv.org/abs/2609.12320
- https://arxiv.org/html/2606.23217
- https://proceedings.mlr.press/v306/wei26ac.html
- https://arxiv.org/html/2601.05504
- https://help.openai.com/en/articles/8590148-memory-faq
- https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context.md
- https://docs.letta.com/guides/agents/architectures/sleeptime/index.md

**Generated interfaces and commercial home hubs**
- https://a2ui.org/specification/v1.0-a2ui/
- https://github.com/google/A2UI
- https://github.com/vercel-labs/json-render
- https://github.com/tambo-ai/tambo
- https://github.com/MCP-UI-Org/mcp-ui
- https://raw.githubusercontent.com/thesysdev/openui/main/README.md
- https://www.copilotkit.ai/blog/the-developer-s-guide-to-generative-ui-in-2026
- https://developers.openai.com/apps-sdk/reference
- https://app.unpkg.com/ai@6.0.77/files/docs/04-ai-sdk-ui/04-generative-user-interfaces.mdx
- https://www.assistant-ui.com/docs/guides/tool-ui
- https://developers.home-assistant.io/docs/intent_conversation_api
- https://dinki.github.io/View-Assist/docs/intro
- https://www.aboutamazon.com/news/devices/new-alexa-top-features
- https://www.techspot.com/news/109829-echo-show-users-amazon-smart-displays-starting-feel.html
- https://www.tomsguide.com/ai/alexa-just-spoiled-my-kids-birthday-heres-what-happened
- https://smarthomecompared.com/displays/amazon-echo-show-21
- https://www.techradar.com/ai-platforms-assistants/its-almost-worthless-google-home-users-slam-painfully-slow-gemini-assistant-but-its-getting-a-global-rollout-no-matter-how-much-you-dislike-it
- https://support.google.com/googlehome/answer/16686363
- https://www.popsci.com/gear/apple-homeview-smart-display-speaker-siri-ai-home-accessories-hub-review/
- https://www.macworld.com/article/3259547/apples-completely-personal-homeview-smart-home-hub-is-built-for-sharing.html
- https://www.sammobile.com/2026/05/11/samsung-bespoke-ai-refrigerator-family-hub-update-usa-gemini
- https://www.cepro.com/cep/joshgpt-brings-advanced-ai-to-control-market
- https://www.techradar.com/ai-platforms-assistants/amazon-is-pushing-alexa-upgrades-on-prime-members-and-users-arent-happy-about-it

**Browser agents and agent safety**
- https://github.com/browser-use/browser-use
- https://github.com/browserbase/stagehand
- https://github.com/google-research/camel-prompt-injection
- https://arxiv.org/abs/2503.18813
- https://github.com/microsoft/playwright-mcp
- https://devblogs.microsoft.com/agent-framework/fides/
- https://arxiv.org/pdf/2505.23643v2
- https://anthropic.com/research/prompt-injection-defenses
- https://www.anthropic.com/news/claude-for-chrome
- https://www.anthropic.com/engineering/claude-code-auto-mode
- https://deploymentsafety.openai.com/chatgpt-agent/user-confirmations
- https://openai.com/index/hardening-atlas-against-prompt-injection
- https://openai.com/index/prompt-injections/
- https://blog.google/security/architecting-security-for-agentic/
- https://ai.meta.com/blog/practical-ai-agent-security/
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-natural-language.html
- https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/
- https://skyvern.com/docs/developers/features/code-caching
- https://arxiv.org/abs/2504.01382
- https://arxiv.org/abs/2510.09023
- https://brave.com/blog/comet-prompt-injection/
- https://brave.com/blog/unseeable-prompt-injections/

# Scout: companion / persona / voice-loop projects (2026-10-07)

Research date 2026-10-07. Slice: projects that make an assistant act like a person. Delta only: this
does not repeat `docs/research/companion-field-vs-samantha-2026-10-03.md`, `flue-and-agent-runtimes-2026-10-03.md`,
`personality-identity-layer-2026-10-04.md` or `arousal-detection-licence-scope-2026-10-04.md`. Anything those
already settled gets one line here.

Method. Stars, licence (SPDX), last push, release tags and archived flags come from the GitHub API (`gh api`) on
2026-10-07, so they are first-hand. READMEs and docs were read through WebFetch, which summarises with a small
model. Where a fetched summary looked wrong (it gave Pipecat release years as 2024) I took the date from the API
instead. **[unverified]** marks anything I could not confirm first-hand. Nothing was installed, downloaded or run on the Jetson or the Pi.

Owner rules applied: ties go to the maintained project; no Zoe-grown look-alikes. So where a maintained artefact
exists I name it. Where only an idea exists (Callhome, NC licence) I say so, and the verdict is idea-only.

Hard constraints respected: rocks are Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro, Flue 2.x + pi, openWakeWord.
The W3 RAM gate binds every Jetson-side item below.

## 0. Where the gap actually is (read against Zoe's own tracker)

The tracker already owns most of the Samantha gap list: B1.3 (Unmute-style interrupt policy), B1.4 (Smart Turn
plus a rule-based "mm-hm"), B2.x (pull-not-push proactivity), B5.2 (emotion tag per sentence), B5.4 (Pocket TTS),
B5.5 (Gemma audio-in on flagged turns), W11.2 (backchannels, NOT STARTED), W4 (hears mood, blocked on W3 and a
licence problem). Grep of `docs/` for the projects below returned **zero hits** for: MaAI, SoulX-Duplug,
pipecat-backchannel, Callhome, Headlong, X-Talk, Gander, AIRI-as-runtime, go-emotions, `jetson-voice-assistant`,
Open Souls. So everything ranked here is new to the repo.

Three things are NEW and change a tracked item:

1. **A learned backchannel detector and predictor exists, open, CPU-sized (MaAI).** B1.4/W11.2 plan a *rule* ("incomplete score
   above 0.7 for 0.7 s, 2.5 s cooldown"). The same group's `bc_det` model answers the harder barge-in question the rule cannot:
   "was that short utterance a backchannel, not a turn?" That is the false-cancel Zoe's barge-in still has.
2. **A permissive, local, 67 MB text-affect classifier removes the need for Gemma to emit emotion tokens.** AIRI's own
   roadmap says its `<|EMOTE_X|>` tokens waste LLM tokens and proposes a classical sentiment model instead. SillyTavern has
   shipped exactly that for years. B5.2 currently says "Gemma emits one expression tag per sentence (OLV convention)"; this
   is a cheaper path for the *display* side, and it is the text-valence half W4 needs anyway.
3. **There is public, working evidence that Gemma 4 hears audio directly through llama.cpp on a Jetson.** `dwain-barnes/jetson-voice-assistant`
   (Orin Nano Super 8 GB) runs Gemma 4 E2B GGUF with an F16 audio/vision mmproj (0.92 GiB) under llama-server, no STT stage. The
   repo pins llama.cpp commit `9f0d017`. This bears on the "input_audio closed upstream" blocker recorded for B5.5. One repo with 0 stars, so it is
   a recipe to read, not a dependency.

## 1. Ranked top 5

| Rank | Project | Verdict | One-line reason |
|---|---|---|---|
| 1 | **MaAI** (Kyoto Univ.) `bc_det` + `vap_bc` | ADOPT-TRIAL (shadow mode, replay corpus) | Only open, CPU-sized learned model for backchannel detection and timing; closes the "mm-hm cancelled my reply" and W11.2 gaps; replaces a hand rule with a trained one |
| 2 | **go-emotions ONNX classifier** (`Cohee/distilbert-base-uncased-go-emotions-onnx`, MIT) via SillyTavern's expression pattern | ADOPT-TRIAL (model) + BORROW (pattern) | 67.6 MB int8, CPU, zero Gemma tokens: drives panel expression/orb colour and is the text-valence half of W4 |
| 3 | **jetson-voice-assistant** recipe (Gemma 4 audio-in on llama.cpp mtmd, Orin) | BORROW (lab only, W3-gated) | Local path to emotional prosody *in* with no SER model at all; also shows heard-text truncation on interrupt |
| 4 | **Pipecat** family: `pipecat-backchannel`, user-idle handling, Smart Turn v3.2 | BORROW (guard set into B1.4) | Maintained, BSD-2, validates the 2.5 s cooldown and adds two guards B1.4 lacks |
| 5 | **SillyTavern** World Info timed effects + Character Card V3 spec | BORROW (spec and semantics, not code: AGPL) | Sticky/cooldown/delay and inclusion groups are the maintained answer to "persona lore without drift or spam"; V3 is the portable persona format |

Ties went to maintained projects: MaAI (push 2026-10-01, 0.2.0 shipped 2026-04, new models Aug 2026) beats the
Zoe-rule route; Pipecat (push today, v1.12.0 2026-09-26) beats any in-house turn-state machine.

### 1.1 MaAI (rank 1)

- What: Real-time Voice Activity Projection library from Kyoto University (Koji Inoue's group). Models: turn-taking (VAP), VAD
  with cross-channel attention, **backchannel timing prediction** (`bc`, ~0.5 s ahead), **backchannel detection** (`bc_det`, "is the
  utterance being produced right now a backchannel"), head-nodding. English, Chinese, Japanese.
  https://github.com/MaAI-Kyoto/MaAI (read 2026-10-07).
- Maintained: yes. 154 stars, 12 contributors, MIT code, last push 2026-10-01. Release notes: 0.2.0 on 2026-04-17,
  single-channel VAP 2026-08-30, VAD 2026-08-09, backchannel model 2025-11-19.
- Licence: code MIT. **Weights are mixed.** `vap_bc_en` and the `*_kyoto` turn-taking models are MIT; `vap_en`, `vad_en`, `vap_mc_en` are CC-BY-NC-ND-4.0;
  **`bc_det_en` carries no licence tag on Hugging Face**. The Mimi encoder (`continuous-mimi-onnx`) is CC-BY-4.0. The licence of `bc_det_en` must be
  asked of the authors before anything beyond a shadow trial.
- Runs beside the rocks: yes in principle. "Operates efficiently, even exclusively on CPU." Sizes from the HF tree: `bc_det_en` head 17.4 MB plus the
  shared Mimi encoder (ONNX int8 156 MB); `vap_bc_en` has a 23 MB non-Mimi 10 Hz variant and a 403 MB Mimi variant. The Pi 5 has about 5.6 GB free
  and sits outside the W3 gate. **No latency or RTF numbers are published**; I could not find any. Treat CPU feasibility as unmeasured.
- Gives Zoe: (a) barge-in quality: `bc_det` threshold 0.45 (their imbalance-tuned operating point, not 0.5) to decide "keep talking" vs "cancel" when the user
  says "yeah"/"mm" over Zoe; (b) W11.2: a trained "now is a good moment for 'mm-hm'" signal instead of a pause-length rule; (c) a second opinion on end-of-turn.
- Caveats: trained on human-human dyadic corpora, taking two channels (user + system). Zoe's system channel is Kokoro playback, which the Pi daemon has
  exactly, so the input is available, but the Jabra echo path is the unknown. Needs the usual replay-corpus negative control (feed it Zoe's own playback as "user" and it must not fire).
- Cost: S-M. A shadow-mode probe on the Pi that logs `p_bc_det` and `p_bc` against the existing replay corpus. No live behaviour change. Flag-dark like the rest.
- Verdict: **ADOPT-TRIAL**.

### 1.2 go-emotions ONNX text classifier (rank 2)

- What: a 28-label GoEmotions classifier. SillyTavern's default expression engine is `Cohee/distilbert-base-uncased-go-emotions-onnx` running server-side
  (docs: https://docs.sillytavern.app/extensions/expression-images/, read 2026-10-07), with a 6-label alternative, and an LLM-classify fallback.
  Labels map to sprites by filename, with a configurable fallback and random choice among several sprites per label.
- Licence: MIT (HF metadata, checked). SillyTavern itself is AGPL-3.0 (34k stars, push 2026-10-02): **use the model and the pattern, not the code**.
- Size: Cohee distilbert int8 ONNX **67.6 MB** (268 MB fp32). Larger alternative `SamLowe/roberta-base-go_emotions-onnx` int8 125 MB (MIT). SamLowe's card reports
  F1 about 0.447 at a fixed 0.5 threshold (accuracy 0.475, precision 0.58, recall 0.40), and "about 2x as fast" int8 vs fp32 on CPU at batch 1. **Treat 28-way labels as
  colour, not truth.** Use top label above a margin, collapse to 6 to 8 groups, and never write the label to memory.
- Evidence it is the right split: AIRI's roadmap (docs/content/en/docs/chronicles/version-v0.0.1) lists "emotion detection: currently wasting extra tokens to
  process emotion tokens, could consider trying sentiment for traditional NLP emotion detection". Hume likewise keeps expression measures out of the LLM's token stream.
- Runs beside the rocks: yes, CPU, tens of ms for a short sentence [unverified on the Pi]. Zero Jetson RAM if it runs on the Pi or in zoe-data (CPU).
- Gives Zoe: panel expression/orb state per reply sentence with no brain tokens; the text-valence half of the W4 fusion (arousal from audio, valence from text, per the
  2026-10-03 note); an input to B5.2 and W11.1 delivery profiles. Does not give prosody out: Kokoro stays.
- Cost: S. One ONNX session, a label-to-orb map, replay-gated.
- Verdict: **ADOPT-TRIAL** (model), **BORROW** (the pattern).

### 1.3 jetson-voice-assistant (rank 3)

- What: https://github.com/dwain-barnes/jetson-voice-assistant (read 2026-10-07). Fully local on a Jetson Orin Nano Super 8 GB. Gemma 4 E2B Q2 GGUF on llama.cpp
  with an F16 audio+vision mmproj; Pocket TTS served through a patched `llama-tts-server`; "there is no speech-to-text in this chain". Reported: TTFT 0.37 to 0.56 s,
  about 1.5 s to first sound; budget 2.24 GiB model + 0.92 GiB mmproj + 0.15 GiB TTS, about 5.65 GiB total. Pins llama.cpp `9f0d017` plus a patch; documents that NvMap does not
  reclaim page cache (same trap Zoe already logged) and that `-ngl 0` does not keep the mtmd audio projector off the GPU.
- Maintained? Created 2026-08-31, last push 2026-09-01, 0 stars, MIT. A single-author snapshot. **Not a dependency.**
- Gives Zoe: (a) a working audio-in recipe to read before B5.5 (Gemma hears "how", not just "what", which is the only local route to emotional prosody in without a SER model
  and its licence problem); (b) it truncates the stored assistant turn to what was actually spoken on interrupt, matching B1.7; (c) a reminder that audio context ages out and the model then remembers the
  question rather than the fact (their README line ~139).
- Fit: E4B's mmproj is larger and the W3 gate stands. Lab only after B0.1/B5.1. I did not verify that the pinned commit matches Zoe's llama.cpp build.
- Verdict: **BORROW** (read the recipe, correct the "closed upstream" note in B5.5 once the pinned-commit claim is reproduced in a lab).

### 1.4 Pipecat family (rank 4)

- Pipecat: https://github.com/pipecat-ai/pipecat. 16.2k stars, BSD-2-Clause, push 2026-10-07, v1.12.0 on 2026-09-26. Per the release notes (read through WebFetch, dates corrected against the API): v1.12 added an
  `interruptible` frame property and a non-LLM proactive classifier worker; v1.11 distinguishes interrupted vs idle user turns; v1.9 added per-stage latency breakdown; v1.10 prosody-preserving TTS continuation.
- `maisterr/pipecat-backchannel`: https://github.com/maisterr/pipecat-backchannel. 23 stars, BSD-2, push 2026-08-10. Triggers backchannels on **clause-complete pauses** using a second VAD and turn detector tuned the opposite way from end-of-turn;
  guards `fire_probability=0.8`, `cooldown_s=2.5`, `min_speech_before_eligible_s=0.7`; "a bot that reacts to each eligible pause sounds mechanical"; roadmap lists prosody matching.
- Smart Turn v3.x (BSD-2, 1.6k stars): already in the tracker (B1.4).
- Gives Zoe: independent confirmation of B1.4's 2.5 s cooldown, plus two guards B1.4 lacks (skip about 20 % of eligible pauses; ignore the first 0.7 s of a turn). Cost: about 2x inference while the user speaks (second VAD and detector). Zoe does not need to adopt Pipecat the framework.
- Verdict: **BORROW**. Fold the guards into B1.4; use MaAI to replace the rule once it proves out.

### 1.5 SillyTavern World Info and Card V3 (rank 5)

- What: SillyTavern is the de-facto persona runtime: https://github.com/SillyTavern/SillyTavern. 34.2k stars, AGPL-3.0, push 2026-10-02. The previous persona note covered the card format; the delta here is the **lorebook semantics**
  (https://docs.sillytavern.app/usage/core-concepts/worldinfo/, read 2026-10-07):
  - Triggers: keyword/regex, constant, vector match; selective logic AND ANY / AND ALL / NOT ANY / NOT ALL; recursion with three per-entry modes.
  - **Timed effects**: `sticky` (stays active N messages after trigger), `cooldown` (cannot re-trigger for N messages), `delay` (not before N messages exist).
  - **Inclusion groups** (only one entry of a group fires; weighted or priority); token budget; eight insertion positions including chat depth.
- Why it matters to Zoe: persona and "things Zoe knows about the household" currently compete for the same prompt budget with no spam control. Sticky/cooldown/delay is the missing vocabulary for B2.5
  ("don't mention this again") and for the persona-lore split the 2026-10-04 design deferred.
- Not worth taking: group chats (these are multiple *characters*, not multiple household *users*); expression sprites (see rank 2).
- Cost: S, as a spec. Zoe's own implementation is a few lines, but the semantics are a maintained, community-proven reference. Verdict: **BORROW**.

## 2. Full scan table (slice: companion / persona / voice loop)

Columns: stars, licence, last push (all from GitHub API 2026-10-07) / maintained / runs beside our rocks / what it adds / adoption cost / verdict.

### 2.1 Full-duplex and conversational speech stacks

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| Kyutai Moshi | 11.2k, Apache-2.0, 2026-09-09 | Full-duplex speech-text model. Needs a GPU; replaces the brain rock. Tracked in the 10-03 note's horizon table. | SKIP |
| Kyutai Unmute | 1.5k, MIT, 2026-09-09 | README states "GPU with CUDA and at least 16 GB VRAM" (STT 2.5 GB, TTS 5.3 GB, LLM 6.1 GB); TTS latency ~750 ms single L40S. B1.3 already borrows the interrupt policy. | SKIP (BORROW policy, already tracked) |
| Kyutai MoshiRAG | 189, Apache-2.0 (weights CC-BY-4.0), 2026-09-09 | Async retrieval while the model keeps talking; needs 24 GB+ VRAM; "sensitive to retrieval delays over 3 seconds". The design (acknowledge, retrieve, inject) is the pattern; the model is out of reach. | SKIP |
| Kyutai Pocket TTS | 9.8k, MIT, 2026-10-06 | 100M params, ~6x realtime on an M4 CPU, ~200 ms first chunk, voice cloning, no emotion control. Already B5.4. Not a Kokoro replacement (rock). | SKIP (tracked) |
| Sesame CSM | 14.7k, Apache-2.0, last push **2025-05-27** | Open 1B model needs CUDA and a Llama backbone; no new open release found; context-conditioned prosody is the one capability nobody else open-sources. Horizon, tracked as B1.5. | SKIP (watch) |
| Pipecat | see 1.4 | | BORROW |
| LiveKit Agents | 14.6k, Apache-2.0, 2026-10-06 (agents 1.8.5) | Adaptive interruption is Cloud-only (confirmed 2026-10-07 via docs search). **New**: Turn Detector v1 is an *audio* end-of-turn model (parallel semantic and acoustic branches), v1 reports 9.9 % false-cutoff at 300 ms vs 12.9 % Deepgram Flux; `v1-mini` is quantised and pruned for CPU; released 2026-06-17 under the non-OSI "LiveKit Model License" (terms not read), 14 languages; plugin `livekit-plugins-turn-detector` 1.8.5 is marked deprecated in favour of `livekit.agents.inference.TurnDetector`. Candidate for a replay-harness bake-off against Smart Turn v3.2 only, subject to the licence read. | BORROW (benchmark candidate) |
| Vocode | 3.8k, MIT, **last push 2024-11-15** | Stale. | SKIP |
| RealtimeSTT / RealtimeTTS | 10.2k / 4.0k, MIT, 2026-09 | Libraries overlapping Moonshine and Kokoro roles. Nothing Zoe lacks. | SKIP |
| HF speech-to-speech | 13.4k, Apache-2.0, 2026-10-06 | Modular VAD/STT/LLM/TTS with Smart Turn v3.2 validation and speculative processing; Kokoro and Pocket supported; desktop-class hardware. Already B1.1's reference. | SKIP (tracked) |
| SoulX-Duplug | 322, Apache-2.0, 2026-07-17 | Plug-and-play streaming semantic VAD (states idle / nonidle / speak / blank), 0.6B, bundles Paraformer/SenseVoice ASR, no latency numbers or comparison to Silero/Smart Turn in the README. Too big for the Pi, duplicates Moonshine. | SKIP (watch) |
| X-Talk | 246, repo says Apache-2.0 (API reports NOASSERTION), 2026-10-03 | Pure-Python full-duplex cascaded framework (VAD, ASR, LLM, TTS) with interruption and "paralinguistic" capture; how the paralinguistics are obtained is not stated. Hardware not documented. [unverified] | SKIP (watch) |
| Gander / Omni-Interaction-Agent | 462, Apache-2.0, 2026-09-17 | MiniCPM-o 4.5 based, **three NVIDIA GPUs minimum**. | SKIP |
| Willow (HeyWillow) | 3.1k, Apache-2.0, 2026-10-01 | ESP32-S3 client, self-hosted server. Zoe's panel does this already. | SKIP |
| OpenVoiceOS ovos-core | 295, Apache-2.0, 2026-10-04 | Alive (6.3k commits). Persona/solver plugins, speaker verifier, converse, runs on Pi 3B+. It is a competing assistant, not a part. Speaker-verifier plugin already cited (B4.2). | SKIP |
| Neon (NeonCore) | 213, licence "other", 2026-09-26 | Mycroft derivative with multi-user support; same verdict as OVOS. | SKIP |
| Rhasspy / Wyoming | OHF-Voice/wyoming 401, MIT, 2026-09-29; linux-voice-assistant 644, Apache-2.0 | Satellite protocol for Home Assistant. Zoe's panel daemon is its own protocol; nothing in the Samantha gap list needs Wyoming. | SKIP |
| Home Assistant Assist | 91.3k, Apache-2.0, 2026-10-07 | 2026.10 (beta notes via search): MA announcements can speak a message; fan-mode by voice. No speaker ID, no companion features. | SKIP |

### 2.2 Persona and character continuity

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| SillyTavern | 34.2k, AGPL-3.0, 2026-10-02 | See rank 5 and rank 2. | BORROW |
| Open Souls Soul Engine | 322, MIT, **2026-02-08, marked "Legacy release"** | Working memory, cognitive steps, mental processes (a state machine of behavioural modes such as "introduction" or "frustrated"). No successor named; cloud-LLM oriented; about 3 years old per its own warnings. The concept of explicit conversation modes is sound but the code is retired. | SKIP |
| Letta | 25.1k, Apache-2.0, 2026-09-10 | Already tracked (persona/human blocks, sleep-time, skill learning). | SKIP (tracked) |
| Open-LLM-VTuber | 14.0k, MIT (API shows NOASSERTION because of Live2D sample-model terms), push 2026-05-15, latest release 1.2.1 on 2025-08-26 | Idle-time proactive speaking, emotion-mapped Live2D expressions, voice interruption without headphones. README says long-term memory removed and v2 is a planned rewrite. Maintenance mode. B5.2 already borrows its tag convention. | SKIP |
| AIRI (moeru-ai) | 50.1k, MIT, 2026-10-07, v0.12.0-beta.5 on 2026-08-29, ~204 contributors | Very alive. Browser/desktop/mobile "stage" apps, Live2D and VRM, local Kokoro TTS option, Discord/Telegram/Minecraft integrations, DuckDB-WASM memory ("Memory Alaya" WIP). No proactive speech documented. Its `<|EMOTE_X|>` and `<|DELAY:n|>` stream-token parser is the design Zoe's B5.2 copies, and its own roadmap calls the token cost wasteful. If the owner ever wants a face on a non-panel screen, AIRI's renderer packages are the maintained route. Not tried on a Pi 5 browser. | BORROW (renderer, only if a face is wanted) |
| Chub / Backyard / Kobold | not scanned first-hand | Card V3 read by these (per 10-04 note). | n/a |
| Miru | 173, Apache-2.0, 2026-09-23 | Markdown memory (people, projects, topics) + journal + commitment list, `soul.md`, an attention system that opens a conversation when you appear stuck. Cloud-API oriented consumer desktop pet. Ideas already in Zoe (open loops, user-model card). | SKIP |
| Aion (AionsHome) | 875, MIT, 2026-09-26 | Three-layer memory (unresolved items / topic / recent), "Sentinel" screenshot monitor that can trigger a proactive message, needs Gemini and SiliconFlow keys. Not local. | SKIP |
| super-agent-party / Soul-of-Waifu | 2.7k AGPL-3.0 / 1.4k GPL-3.0 | Copyleft consumer apps. | SKIP |
| awesome-ai-companion list | 887, CC0, 2026-10-06 | Useful index. Entries worth a glance later: Headlong (agency microharness, 1.2k Apache-2.0), jiwen (drifting emotional axes triggering behaviour thresholds, MIT, 162), Ombre-Brain (valence/arousal-tagged memory with forgetting curves, MIT, 1.4k). None read first-hand beyond the list summary. | WATCH |

### 2.3 Proactive and initiative frameworks

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| Callhome | 147, **PolyForm Noncommercial 1.0.0**, 2026-10-01 | Companion-initiated voice-call stack. Mechanisms worth knowing: escalation dialling (silence for hours leads to one check-in call, at most once a day, never at night or in do-not-disturb); **soft hangup** (a soft goodbye, then the line lingers 15 to 20 s; speaking cancels the hangup, silence closes); voicemail for missed calls; SenseVoice emotion tags plus acoustic features ranked against **each speaker's own baseline** (to avoid mic and tone false positives). Licence blocks code reuse. It is a Zoe-grown look-alike risk if we rebuild it, so the owner rule means: idea only, adopt nothing. The soft-hangup window maps to Zoe's 5 s follow-up listening window; the per-speaker-baseline note is the same idea as AS-norm in P3. | SKIP (idea only) |
| Pipecat proactive workers, `user_idle` | see 1.4 | Screen-analysis classifier, idle vs interrupted turns. | BORROW (see rank 4) |
| refixai/proactivity-sdk | 12, Apache-2.0, 2026-09-10 | "Self-adjusting cadence". Too small to trust. [unverified] | SKIP |
| Nomi / LangChain ambient | covered 10-03 | | tracked |
| Headlong | 1.2k, Apache-2.0, 2026-10-07 | Bash-based recursive agent harness; competes with Flue. | SKIP |

### 2.4 Local emotion and affect

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| go-emotions ONNX | see rank 2 | | ADOPT-TRIAL |
| MaAI | see rank 1 | Detects backchannels, not emotion. | ADOPT-TRIAL |
| emotion2vec+, SenseVoice, Wav2Small | settled 10-04 | Arousal model still has to be made; no permissive sub-300 MB arousal model exists. Nothing found today changes that. HF `audio-classification` search shows only wav2vec2-XLSR/HuBERT categorical models, most Apache-2.0 but 300M+ params (e.g. `speechbrain/emotion-recognition-wav2vec2-IEMOCAP`), and the audEERING dimensional model remains CC-BY-NC-SA. | tracked |
| CoCoEmo (ICML 2026) | 14, MIT, 2026-07-01 | Activation steering of the speech-LM inside CosyVoice2 (0.5B) and IndexTTS2; README says nothing about Kokoro-class models, so it is inapplicable to the Kokoro rock. | SKIP |
| EmoRES-TTS (Meta) | 33, licence "other", 2026-10-01 | Residual emotion steering for emotional TTS; research code, same inapplicability. | SKIP |

### 2.5 Theory of mind and user model

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| Honcho (plastic-labs) | 7.5k, AGPL-3.0, 2026-10-06 | Peer model keyed on `(observer, observed)` pairs, async "deriver" and "dreamer", peer cards. Needs Postgres+pgvector and an LLM provider (Gemini default). Already evaluated elsewhere in the repo (9 doc hits); AGPL plus cloud-by-default conflicts with local-only. | SKIP |
| OpenHands ToM-SWE | 119, **archived**, no licence | Coding-agent user model. | SKIP |
| ToM benchmarks (MMToM-QA etc.) | academic | Evaluation sets, not components. | SKIP |

### 2.6 "Her / Samantha" attempts on GitHub

| Project | Stats | Notes | Verdict |
|---|---|---|---|
| callbacked/os1 | 145, Apache-2.0, 2026-09-02 | Browser OS1 look-alike. Aesthetic demo, no memory or initiative. | SKIP |
| sighmon/os-one | 9 | Toy. | SKIP |
| lancekrogers/samantha | 3, Apache-2.0 | Voice agent for coding agents over Tailscale. | SKIP |
| goncaloneves/samantha | 5, MIT | Coding-tool voice with wake word. | SKIP |

I found no open Samantha attempt with memory, initiative and a measured bar. Zoe's own bar harness (`samantha_bar.py`) has no public peer.

## 3. Things I could not confirm

- MaAI CPU latency / real-time factor on Pi 5: not published; no probe run. The `bc_det_en` weights have **no licence tag**.
- Whether `jetson-voice-assistant`'s pinned llama.cpp commit reproduces on Zoe's build (E4B vs E2B, F16 vs BF16 mmproj).
- LiveKit Model License terms (page not read) and whether v1-mini's weights can be downloaded outside the SDK.
- X-Talk's paralinguistic method; SoulX-Duplug latency; VoiceMem (`xzf-thu/VoiceMem`, 2.4k stars, Apache-2.0, "universal memory for voice agents") and the NVIDIA `nemotron-voice-agent` blueprint (232 stars, NOASSERTION): metadata only, README not read.
- SillyTavern group-chat internals and Kindroid/Nomi-type closed systems: not first-hand.
- Moonshine's repo now also lists "intent recognition and text to speech" (MIT, 11.2k stars, push 2026-10-02); the README gave no speaker-ID or emotion output. The tracker already knows its Kokoro-graph TTS (B5.7 note).

## 4. Suggested next steps (smallest first)

1. Ask the MaAI authors for the `bc_det_en` weights licence. Write the shadow-mode probe: log `p_bc_det` and `p_bc` on the Pi against the replay corpus, with Zoe's own playback fed in as the "user" channel as the negative control. No behaviour change.
2. Drop the 67.6 MB go-emotions ONNX into a lab CPU probe: accuracy on the Jason corpus transcripts, map 28 to 6 or 8 groups, wire to orb colour behind a flag.
3. Add `fire_probability` and `min_speech_before_eligible_s` guards to B1.4's spec text; no new code needed until W11.2 starts.
4. When B0.1 frees RAM, reproduce the jetson-voice-assistant llama.cpp audio-in recipe with E4B in a lab and compare against B5.5's assumption.
5. Put SillyTavern's sticky / cooldown / delay vocabulary into the B2.5 design note.

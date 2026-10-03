# The companion / voice-assistant field vs the Samantha spec (2026-10-03)

Research date: 2026-10-03. Sources are cited inline and listed in §5. **[unverified]** marks a claim
taken from a secondary source or a summary, not a primary one. **Not scanned** marks a project the
brief named that this pass did not reach.

## 0. Scope: a delta, not a re-survey

Most of the field has already been mapped. This report does not repeat that work:

- **State review 2026-09-25 §7** ([state-of-zoe-review-2026-09-25.md](../knowledge/state-of-zoe-review-2026-09-25.md))
  has 16 ranked pieces to borrow, cited below as **SR#1–SR#16**.
- **Tracker §1/§2/§5** ([beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md))
  covers what Zoe already beats, a 15-row 2026 bar checklist, and an explicit NOT-doing list. Its
  program items are cited as **B1.1 … B10.x**.
- **Samantha plan** ([samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md)) holds
  the spec (§1), the workstreams **W1–W18** and the acceptance bar (§5).
- **Memory and context** were researched on 2026-09-29
  ([samantha-context-engineering-2026-09-29.md](samantha-context-engineering-2026-09-29.md)) and
  are out of scope here.

Rule for this document: **a piece that is already tracked gets one line and its ID. Only NEW pieces,
or new evidence that changes a tracked item's design or gate, get full treatment.** The rocks (Gemma 4
E4B+MTP, Moonshine v2 Medium, Kokoro) and the W3 RAM gate (operator decision 2026-07-22: the gate
stands, and an agent may not waive it) bind every recommendation. HA and Music Assistant stay hidden
organs.

Where Zoe stands on the spec today (live `main` as of 2026-09-30, per state review §10–§11 and the tracker §0):

- **Barge-in**: Silero VAD on the Pi daemon, anchored to playback start (800 ms grace, 3 of 6 chunks
  or 2 chunks ≥0.95). It **cancels** the reply. There is no pause/resume and no backchannel.
- **Latency**: ~4.0 s median from end of speech to first sound (BEFORE #1760/#1761, which cut ~0.4 s
  and ~0.3 s; not re-measured live since).
- **Speaks first**: the spoken 07:30 brief is **OFF by owner decision** (`ZOE_PROACTIVE_SPOKEN=0`). The
  brief is folded into the first turn of the day. A nightly selector raises at most 1 open loop per session.
- **Hears mood**: nothing from audio. W4 is blocked on W3.
- **Knows who**: resemblyzer in **shadow** on the Pi (the claim is discarded). Face-ID is behind a flag.
  The retention policy is open.
- **Presence**: panel activity only. The house has zero presence sensors.
- **Ambient**: off. B9 plans Omi, gated on consent (B9.0).
- **Self-evolution**: W7 has never closed. B8.1 is open.

## 1. Scoreboard

Columns: *best in field (mechanism, published numbers)* · *Zoe today* · *gap* · *what to take (tracked ID or NEW)*.

| Spec item | Best in field | Zoe today | Gap | Take |
|---|---|---|---|---|
| **Interruptibility** | **LiveKit Agents 1.5** "adaptive" interruption: an audio model that separates real barge-ins from backchannels, coughs and TV. 86 % precision / 100 % recall at 500 ms overlap, rejects 51 % of VAD false positives, ≤30 ms. **Cloud-only**: self-hosted agents fall back to VAD. Self-hostable defaults: `min_duration` 0.5 s, `false_interruption_timeout` 2.0 s, `resume_false_interruption` true ([LiveKit blog 2026-06-30](https://livekit.com/blog/turn-detection-and-interruption-handling); [tuning docs](https://docs.livekit.io/agents/logic/turns/tuning/)). **Pipecat 1.0** `MinWordsUserTurnStartStrategy`: the word minimum applies *only while the bot is speaking* ([docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies)). **Voice-Light** (2026-09): a **reversible duck**. On overlap, fade −15 dB over 450 ms, then pause; a floor-take score >0.82 commits, otherwise the *same* generation resumes. Speculative work carries generation IDs and never enters history until promoted ([arXiv 2609.20995](https://arxiv.org/html/2609.20995v1)). **Vapi** `stopSpeakingPlan.numWords=2`: one-word acknowledgements don't interrupt ([docs](https://docs.vapi.ai/customization/speech-configuration)). | Tuned VAD barge-in on the real Jabra echo. It cancels. | No resume after a false barge-in. Barge-in on VAD alone (no words). No record of what was actually heard. **P2** (duck → decide → resume, refining B1.2), B1.3 (MinWords while speaking: Pipecat confirms the "only while speaking" scoping), B1.7 (truncate to what was played; OpenAI Realtime's `conversation.item.truncate` deletes unheard text from context, [docs](https://developers.openai.com/api/docs/guides/realtime-vad.md)). The adaptive model itself is out of reach (cloud-only, §4). |
| **Latency** | **Kyutai Unmute**: streaming STT + semantic VAD + text-streaming TTS around any text LLM, 200–350 ms responses on GPU ([kyutai.org/unmute](https://kyutai.org/unmute/), [repo](https://github.com/kyutai-labs/unmute)). **LiveKit** `preemptive_generation`: the LLM starts before the turn is confirmed and cancels if speech resumes. On by default, `preemptive_tts` off, `max_speech_duration` 10 s, `max_retries` 3. **HA 2025** streaming TTS: ~5 s → ~0.5 s to first audio with Piper; not used under 60 chars ([HA blog 2025-09-11](https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/)). | ~4.0 s before the 09-28 fixes. Sentence-streamed TTS already. Speculative turn-start built but dark. | Endpoint tail (~0.85 s) and whole-clip STT (0.25 s per s of audio) dominate. | B1.1 (flip it, starting from LiveKit's defaults: speculative **LLM yes, TTS no**). Streaming STT stays RAM-blocked (§4). |
| **Speaks first (between conversations)** | **Nomi**: cadence of 1 h / 3 h / 1 d / 4 d; **interval doubles if ignored**; on the lowest setting, **one message, then wait for a reply**; quiet hours 22:00–08:00 ([Nomipedia](https://wiki.nomi.ai/When_Your_Nomi_Messages_You_First)). **LangChain ambient agents**: every background item is **Notify / Question / Review**, collected in an Agent Inbox ([blog, 2025-01](https://www.langchain.com/blog/introducing-ambient-agents)). **Open-LLM-VTuber**: a "raise hand" button invites the AI to speak [unverified detail]. | Spoken brief OFF (owner found it intrusive). Brief on first turn ON. Selector ≤1 per session. Playback-ACK'd "heard". | No *pull* path: Zoe can't signal "I have something" without speaking. No ignore-backoff yet (B2.2). | **NEW P1**: pull-not-push (indicator + voice inbox). B2.2 (doubling backoff, now with Nomi's "one unanswered, then wait" rule). |
| **Speaks first (inside a conversation)** | **Inner Thoughts** (CHI 2025): a covert thought stream; a trigger on a new message *or a pause* → retrieve → form a thought → rate intrinsic motivation 1–5 → speak only above a threshold. Preferred 82 % of the time over next-speaker prediction plus persona. In a field study, the most selective setting was rated **worst** by 7/12 ([arXiv 2501.00383](https://arxiv.org/html/2501.00383)). **Pipecat** `FilterIncompleteUserTurnStrategies`: the LLM's first token marks the turn ● complete / ◐ cut off / ○ thinking. Incomplete turns are held, then the bot re-engages after 5 s / 10 s ([docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/filter-incomplete-turns)). **LiveKit async tools** (1.6, 2026-06): `ctx.update()` acknowledges at once; the deferred result is spoken when both sides are idle ([blog](https://livekit.com/blog/async-tools-voice-agents)). | B1.6 plans a fixed 7 s silence marker (Unmute). | A timer does not choose *what* to say. **NEW P7**: incomplete-turn marker first, then a motivation-scored pause thought, replacing B1.6's fixed marker. B1.5 gains LiveKit's deferred-result rule. |
| **Hears mood** | **Hume EVI 3** (2025-05): streamed prosody measures condition both the reply and the TTS prosody ([docs](https://dev.hume.ai/docs/speech-to-speech-evi/overview)). **Gemini Live** `enable_affective_dialog` on native-audio models ([guide](https://ai.google.dev/gemini-api/docs/live-guide)). Both are cloud S2S. Hume injects the **top-3 expression labels as text** into the LLM's user message ([FAQ](https://dev.hume.ai/docs/speech-to-speech-evi/faq)). **Alexa's frustration detector** (lexical cue + tone model + combiner) triggers an apology and a clarifying question, first only for music errors [secondary]. Open, small: **Wav2Small**, 72 K params / 60–120 KB int8 ONNX / ~9 MB RAM / ~9 ms CPU, **arousal CCC 0.66 but valence only 0.37**; the licence is inconsistent (HF card CC-BY-NC-SA-4.0 vs paper CC BY-SA 4.0) ([arXiv 2408.13920](https://arxiv.org/html/2408.13920v1), [weights](https://huggingface.co/dkounadis/wav2small)). **emotion2vec+** base ~90 M / large ~300 M, 9 categorical classes; a community ONNX of base exists ([HF](https://huggingface.co/pankotaro/emotion2vec-plus-base-onnx)). | Text-inferred only. | No acoustic signal at all. W4 is blocked on the Jetson W3 gate. | **NEW P4**: arousal on the Pi, used first for frustration repair; valence stays text-derived (evidence below). |
| **Knows who** | **3D-Speaker CAM++ / ERes2NetV2** via sherpa-onnx: VoxCeleb1-O EER 0.65 % / 0.61 %; CAM++ is the cheaper one on ARM64 CPU ([3D-Speaker](https://github.com/modelscope/3D-Speaker)). **pyannote community-1** (pyannote.audio 4.0, 2025): WeSpeaker-ResNet embedding, AMI headset DER 18.8 → ~17.0 %, exclusive-speaker mode ([blog](https://www.pyannote.ai/blog/community-1)). **AS-norm**: normalise each score against the most similar impostor cohort ([impl](https://github.com/nidwbin/AS-Norm)). **Omi postmortem** (issue #12765, 2026-09): one noisy clip + raw cosine 0.45 + *different* models for enrolment and matching failed; only 19–23 % of users finished enrolment. Fix: one model, centroid of ≥5 separate clips, AS-norm/relative ranking ([issue](https://github.com/BasedHardware/omi/issues/12765)). **Omi PR #18483** (merged 2026-09-24): "Is this you?" cards from clean 5–10 s single-voice stretches; voiceprint = base + last 5 confirmations, against a **measured 71 % cross-session false-reject**; 20 h cooldown, 7-day back-off after 3 ignored ([PR](https://github.com/BasedHardware/omi/pull/18483)). **Apple HomePod** says "Okay <name>" before personal data; **Google** treats an unknown voice as a guest ([Apple](https://support.apple.com/en-gb/guide/homepod/apd1841a8f81/homepod), [Google](https://support.google.com/googlehome/answer/16687976)). | resemblyzer (2019 GE2E) in shadow. Two-cluster enrolment trap known. | Weaker embedder. Raw cosine threshold. No score normalisation. | B4.1 (CAM++ + margin rule). **NEW P3**: Omi's postmortem fix list + confirm-to-teach enrolment (below). |
| **Multi-user household** | **Weakest area across OSS.** HA Assist still has no native speaker ID. Community add-ons attach a `user_id` to the turn, and a design thread proposes **one Assist pipeline per speaker, exposing only that person's devices** ([HA community](https://community.home-assistant.io/t/speaker-recognition-in-voice-assistant/654276)). OVOS personas are voice-switchable solver chains, not per-user ([manual](https://openvoiceos.github.io/ovos-technical-manual/150-personas/)). No OSS kid mode with traction found. | Per-member memory, recipients, consent tables. Identity dark. | Per-user tool scopes / kid mode (W5.4) not started. | W5.4. The HA "pipeline per speaker" idea is the shape: a per-member tool allow-list keyed on the resolved identity. No new piece. |
| **Presence (gates speaks-first)** | **Echo Show**: ultrasound (≥32 kHz) out of its own speaker, heard by its own mics; drives "Adaptive Content" by proximity ([Amazon Science](https://www.amazon.science/blog/the-science-behind-ultrasonic-motion-sensing-for-echo)). **Bermuda** (HA, BLE trilateration via ESPHome proxies, **IRK** so iPhones' rotating addresses resolve) gives *who* is in which room ([repo](https://github.com/agittins/bermuda)). **ESPectre** (Wi-Fi CSI on an ESP32, motion score 0–100, ESPHome auto-discovery, no camera or mic) ([repo](https://github.com/francescopace/espectre)). **LD2450** mmWave: 3 targets with x/y, 3 zones, ESPHome-native ([ESPHome](https://esphome.io/components/sensor/ld2450/)); the 2026 practice is fusing it with an **LD2412** for still people [secondary]. **Alexa+ Visual ID** delivers a person-targeted reminder only when *that* person is seen ([Amazon](https://www.aboutamazon.com/news/devices/new-alexa-plus-amazon-devices)). Ultrasound misses people sitting still. | Panel touch/voice activity only. Zero sensors. | No device-free "anyone here" signal, and no "who is home" signal that isn't a biometric. | B4.5 (camera / HA device presence). **NEW P8**: presence fusion, PanaCast person-detect first, Bermuda IRK for *who*. |
| **Attributed ambient** | **Correction to plan §8.6: Limitless's "Consent Mode" never shipped** (Meta acquired and discontinued Limitless 2025-12-05) [per scan; secondary]. No shipped product records only consenting enrolled voices, so Zoe would be first. Closest building block: **OVOS `ww-verifier-plugin-speaker`**, which embeds after the wake word, matches the household, **silently drops unknown speakers**, and stores embeddings only ([repo](https://github.com/OpenVoiceOS/ovos-ww-verifier-plugin-speaker)). pyannote community-1's exclusive mode simplifies word-to-speaker alignment. | `ambient_memory` exists, off. B9 Omi plan with consent decisions pending. | Consent decisions (B9.0), multi-speaker discard (B9.3). | B9.0–B9.5 cover it; drop-before-store at one chokepoint (Omi's opt-out pattern). Diarization stays horizon (§4). |
| **Personality / consistency** | **Anthropic Assistant Axis** (2026-01-19): models drift off persona most in **therapy-style emotional talk**, meta-questions about their own nature, and requests for authorial voices. Coding keeps them stable. Activation capping cut harmful responses ~50 % without capability loss (Gemma 2 27B, Qwen 3 32B, Llama 3.3 70B) ([post](https://www.anthropic.com/research/assistant-axis)). **Nautilus Compass** (2026-05): black-box drift detection by embedding similarity to behavioural anchor texts, weighted top-k mean, ROC AUC 0.83 ([arXiv 2605.09863](https://arxiv.org/abs/2605.09863)). Drift is measurable within 8 rounds ([Li et al. 2024](https://github.com/likenneth/persona_drift)). | Persona doctrine. Samantha bar S4 (emotional thread) is a known flaky scenario. | Nothing *measures* persona drift. The emotional turns Zoe now invites (S4) are the measured high-drift case. | **NEW P5**: drift band on the bge-small embedder already resident. B5.2 (GLaDOS constitution) stays the shaping layer. |
| **Self-evolution** | **Letta skill learning** (2025-12-02): trajectory → reflection → SKILL.md. Terminal-Bench 2.0 +21.1 % relative from trajectories, **+36.8 % relative when failure feedback is included**, −15.7 % cost ([blog](https://www.letta.com/blog/skill-learning/)). **SICA**: archive of versions + scores; 17 → 53 % on a SWE-Bench Verified subset [unverified]. DGM was already cited (plan §8.7). | Gated PR harness + replay gate. The loop has never closed (W7). | No source of proposals. W18 (feedback) is orphaned. | **NEW P9**: failure-fed proposals (replay misses + `chat_feedback`) as W7's first input. |
| **Privacy** | **HA**: "prefer handling commands locally" (deterministic intents first, LLM on a miss); per-assistant exposed entities ([2024.12](https://www.home-assistant.io/blog/2024/12/04/release-202412/), [2025-09 AI post](https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/)). **Neon / OVOS**: fully on-device capable. | Fully local hot path; data-class doctrine + interim-remote ledger (plan §11); consent tables. | None structural. | Keep. Nothing in the field is stricter. |

### Evidence that changes a tracked design (not new items)

- **W4 shape: audio for arousal, text for valence.** The SER literature is consistent: acoustic
  features predict arousal and dominance well and valence poorly, and word embeddings predict valence
  better ([Springer 2025 survey of speech+text VAD fusion](https://link.springer.com/article/10.1007/s12243-025-01069-1);
  [Cambridge multitask study](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/BCBF69FBED76857F84090A2FB58B2498/S2048770320000141a.pdf/dimensional_speech_emotion_recognition_from_speech_features_and_word_embeddings_by_using_multitask_learning.pdf)).
  The Interspeech 2025 MSP-Podcast challenge's valence gains came from adding diverse *text*
  foundation models (baseline valence CCC 0.638, top-5 ensemble 0.734; [challenge paper](https://www.isca-archive.org/interspeech_2025/naini25_interspeech.pdf)).
  Wav2Small itself scores arousal CCC 0.66 but valence only 0.37, and MSP-Podcast clips are 2.75–11 s
  long; Zoe's commands are often shorter, where categorical SER is weak (best 8-class Macro-F1 ≈ 0.40).
  Consequence: W4's job is **arousal**, which a ~100 KB model does at CCC 0.66. The "flat 'I'm fine'"
  DoD case becomes *low arousal + neutral text*, so the fusion rule should key on arousal.
- **The decision to speak should not be the LLM's. New external evidence for the plan's §0 doctrine.**
  A small temporal-graph controller beat LLM triggers on AUC (the best of 9 architectures), lifted
  downstream F1 by 16.7 points on average across 14 backbones, and was 4–7× faster at the trigger
  stage (14 ms on a laptop, ~220 MiB BF16) ([arXiv 2605.30152, rev. 2026-09-28](https://arxiv.org/abs/2605.30152)).
  ProactiveBench's reward model agrees with humans at 91.8 % F1 ([arXiv 2410.12361](https://arxiv.org/html/2410.12361)).
  This supports B2.2's non-LLM scorer and its later reward model. It does **not** justify the
  220 MiB model on the Jetson; see P10.
- **B1.1 flip parameters.** LiveKit ships speculative *LLM* generation on by default but speculative
  *TTS* off. That is the low-risk first flip: the brain call is cheap to cancel, but audio that has
  started playing cannot be taken back.
- **HA validates Zoe's 60-char rule.** HA's streaming TTS skips replies under 60 characters, the same
  threshold as Zoe's `ZOE_EXPRESSIVE_MIN_CHARS` and `_FIRST_UNIT_CLAUSE_MIN`. That is convergence, not a gap.

## 2. Top 10 pieces to borrow (ranked by impact per unit of work)

RAM is Jetson RAM unless stated. The Pi 5 has ~5.6 GB free and sits outside the W3 gate (plan §6a).
Effort: S = days, M = 1–2 weeks. Every item ships behind a flag, default OFF, and voice-path items
are replay-gated.

| # | Piece | From | Spec item | Jetson RAM | Effort / risk | Rocks | Tracked? |
|---|---|---|---|---|---|---|---|
| **P1** | **Pull, not push.** When the nightly selector holds an item, the panel orb shows an "I have something" state; "what's up?" or a tap delivers it. Each item is classed **Notify / Question / Review**. Unanswered items follow Nomi's **one unanswered, then wait**, with doubling. This gives the owner's "no unprompted speech" decision a path for Zoe to *offer* without speaking. | LangChain ambient agents + Agent Inbox; Nomi; Open-LLM-VTuber "raise hand" | speaks first | 0 | S / low (orb state + selector field) | fits | **NEW** (extends B2.2, W13.1) |
| **P2** | **Duck → decide → resume** instead of hard cancel. On barge-in, fade Kokoro playback −15 dB over ~450 ms. If the speech stays a backchannel or noise (Zoe's existing 3-of-6 Silero rule, Vapi's `numWords≥2` once words exist), restore volume and continue. Otherwise cancel and **trim the stored assistant turn to what was played** (B1.7). | Voice-Light (arXiv 2609.20995); Vapi; OpenAI `truncate` | interruptibility | 0 (Pi playback gain) | S–M / medium (voice path) | fits | B1.2 + B1.7 (tracked); the **duck** mechanism is new |
| **P3** | **Rebuild the speaker gate on Omi's postmortem**: one VoxCeleb-trained embedder for enrolment *and* matching (CAM++ or WeSpeaker ResNet34 via `speakeronnx`/sherpa-onnx, ~7 M params), an enrolment centroid of ≥5 separate clips covering both of Zoe's known acoustic clusters, AS-norm or relative ranking plus B4.1's margin, thresholds re-tuned per model. Then **confirm-to-teach**: "Is this you?" on the panel from clean 5–10 s turns, voiceprint = base + last 5 confirmations, 20 h cooldown, back-off after 3 ignored. Apple's "Okay <name>" before anything personal. | Omi #12765 + PR #18483; 3D-Speaker; AS-norm; HomePod | knows who | 0 (Pi) | M / low (shadow first) | fits | B4.1 (tracked); postmortem list + continual enrolment are **NEW** |
| **P4** | **Arousal on the Pi, first used for frustration repair.** Wav2Small scores each finished WAV off the critical path (the #1760 thread pattern) and posts `arousal`. High arousal + a lexical cue ("no", "that's not what I said") → apologise and clarify (pairs with W1.5). The label reaches Gemma as text, Hume-style. Valence stays text-derived. One-week log-only window. **Licence must be settled first** (NC-SA on the HF card). | Wav2Small; Alexa frustration detector; Hume EVI | hears mood | **0** (Pi ~10 MB) | S–M / low | fits | **NEW**: re-scopes W4.1. **Needs Jason's decision**: W4's gate is recorded as W3, and this moves the model off the box that gate protects. |
| **P5** | **Persona-drift band**: embed each assistant reply with the **bge-small already resident** in zoe-data against positive anchors (persona doctrine lines) and negative anchors (replies Jason corrected). A weighted top-k mean gives aligned / neutral / deviation. Log it, count it in W16, and inject a one-line reminder only on deviation. Emotional turns (S4) are the measured high-drift case. | Nautilus Compass (AUC 0.83); Assistant Axis | personality | 0 (reuses bge-small; the paper's BGE-m3 is not needed) | S / low (log-only first) | fits | **NEW** |
| **P6** | **Flip B1.1 speculative turn-start** with LiveKit's guards: speculative **LLM on, TTS off**, skip over 10 s of speech, ≤3 resets per turn, reuse the llama.cpp prompt cache (no second context). | LiveKit `preemptive_generation` | latency | 0 | S / medium (voice path) | fits | B1.1 (tracked; the guards are new) |
| **P7** | **Incomplete-turn marker, then pause thoughts.** Rung 1: Gemma's first token marks ● / ◐ / ○; Zoe holds an incomplete turn and re-engages after 5 s / 10 s ("go ahead, I'm listening"). The marker is stripped before TTS. Rung 2: on a longer pause, one candidate thought from an open loop or recall, scored 1–5, spoken above a threshold tuned toward *active* (too-selective was rated worst). | Pipecat `FilterIncompleteUserTurnStrategies`; Inner Thoughts (CHI 2025) | speaks first (in conversation) | 0 (one token / one extra call, compute not RAM) | M / medium (changes brain output; replay gate) | fits | **NEW** (upgrades B1.6) |
| **P8** | **Presence fusion with no new sensor first**: person-detect on the existing **PanaCast** at ≤1 fps on the Pi (frames never stored) + touch/voice activity → "someone is here". **Bermuda** BLE with IRKs via ESPHome proxies → "Jason's phone is in this room" for W2 step 4 and B2.1. An LD2450/LD2412 or ESPectre is the later upgrade. Speak a person-targeted item only when *that* person is present (Alexa+ Visual ID rule). | Alexa occupancy / Visual ID; Bermuda; ESPHome mmWave | presence, knows who | 0 on Jetson (Bermuda runs in HA, which **conflicts with W3.3's HA reap**) | S–M + ~A$10/room for BLE proxies / low | fits (HA hidden organ) | B4.5 (tracked); the concrete fusion is **NEW** |
| **P9** | **Failure-fed evolution proposals**: W7's first proposals come from failure trajectories (replay said-vs-did misses, Samantha-bar FAILs, `chat_feedback` rows), each reflected into a doctrine or skill diff carrying its own acceptance test. | Letta skill learning (+36.8 % relative with failure feedback vs +21.1 % without) | self-evolution | 0 (fleet work, off-box) | M / low (human-gated) | fits | **NEW** (feeds W7 + W18) |
| **P10** | **Learned should-I-speak head** in the router-head pattern: a numpy logistic/MLP head on bge-small plus structured features, trained on `proactive_responses` accepted / ignored / undelivered. It replaces B2.2's hand rubric once ~200 labelled rows exist. Do **not** adopt the 220 MiB TGL model. | arXiv 2605.30152 (evidence); ProactiveBench (false-alarm focus) | speaks first | ~0 | M / low, data-gated | fits | **NEW** (B2.2 names "a later reward model") |

Already tracked, still worth doing, and nothing new to add: B1.3, B1.8, B2.6 (sleep-time precompute:
Letta's paper reports ~5× less test-time compute at equal accuracy and +13–18 % accuracy, with the
gain growing with query predictability, [arXiv 2504.13171](https://arxiv.org/pdf/2504.13171)),
B4.2 (OVOS's speaker-verifier wake plugin is a working reference), B5.2, B5.4, IDEAS "Streaming STT"
(Moonshine v2's sliding-window encoder plus Unmute's flush-at-endpoint is the mechanism; still RAM-gated).

### Smaller notes (verified, not ranked)

- **Dynamic endpointing** for the flag-dark adaptive tail (#1766 `ZOE_VAD_CLEAN_TAIL_MS`): LiveKit
  1.5 learns the tail as an EMA of the session's pauses (α 0.9, 0.5–3.0 s). LiveKit issue #7057
  (2026-08-31): backchannels during agent speech were learned as pauses, so one "uh-huh" moved the
  tail from 0.3 s to 1.18 s. Exclude pauses that overlap playback
  ([docs](https://docs.livekit.io/reference/agents/turn-handling-options/), [issue](https://github.com/livekit/agents/issues/7057)).
- **Echo cancellation: low priority.** WebRTC AEC3 is available in-process on the Pi through
  `livekit.rtc.AudioProcessingModule` (aarch64 wheel, livekit 1.1.20), but it fails silently beyond
  ~500 ms reference delay. The panel's mic is the Jabra Speak 750 (`zoe_voice_daemon.py` device
  selection), whose own AEC already covers its own speaker. AEC3 matters only if capture moves to
  the PanaCast.
- **Narrow "stop" arming**: OHF's linux-voice-assistant arms a dedicated *stop* model only during
  long announcements and ringing timers. That is a safer barge-in scope for the proactive lane if
  P1 ever speaks.
- **Gemini "proactive audio"**: the model declines to answer speech that isn't addressed to it.
  This is the bar for Zoe's 5 s follow-up window, where TV or side talk can start a turn. The local
  approximation is speaker-gating the follow-up window (P3 + B4.2), not a new model.

## 3. Where Zoe already does better than the field (specific)

1. **Measured voice path with a said-vs-did gate on the owner's own voice.** The per-stage TTFA
   breakdown ([panel-ttfa-breakdown-2026-09-28.md](../knowledge/panel-ttfa-breakdown-2026-09-28.md)),
   the replay probe with negative controls, and the VAD stage that caught a dead Silero file: no OSS
   project (HA, OVOS, GLaDOS, Willow, Leon) or commercial SDK publishes anything comparable.
   LiveKit and Pipecat publish component numbers, not end-to-end budgets for a deployed home.
2. **Barge-in tuned against real echo, self-hosted.** LiveKit's adaptive interruption model is
   cloud-only, so a self-hosted LiveKit agent falls back to plain VAD. Zoe's playback-anchored grace
   plus sustained or fast-path gating (#1765) is at least the best that is self-hostable. GLaDOS
   simply stops playback on any voice.
3. **Spoken proactivity with a delivery receipt.** The brief counts as heard only on the daemon's
   playback ACK (`voice_announcements.played_at`). HA's `start_conversation`, GLaDOS's autonomy lane
   and Open-LLM-VTuber's idle timer fire and forget. Zoe also has the owner-tested lesson the field
   lacks: clock-driven speech was turned off and replaced by context folded into the first natural
   turn.
4. **Deterministic "when", model "what".** The plan's §0 doctrine (the 4B brain measured ~1/5 on
   unprompted surfacing) now has external support from arXiv 2605.30152. Leon's "pulse" and
   GLaDOS's autonomy lane let the LLM decide; their bounds are not published.
5. **Local first, with a learned router.** A two-stage FunctionGemma-270M router with a confidence
   floor (0.70) sits ahead of the brain. HA's "prefer local" is a regex/intent matcher, and one 2026
   community thread reports it inflating latency [unverified].
6. **Household identity with consent and retention as gates.** HA Assist still has no native
   speaker ID after years of requests. Zoe has consent-gated voice and face profiles
   (migrations 0023/0024) and an explicit rule that each modality needs its own shadow week. That is
   ahead in design, though it is dark in operation. Omi shipped speaker-ID, then measured a 71 %
   cross-session false-reject rate in production. Zoe's shadow-week-before-enable rule exists to
   catch exactly that before users feel it.
7. **Self-evolution behind a real-voice replay gate.** DGM, SICA and Letta measure on benchmarks.
   Zoe's harness is gated on the live user's speech and a human merge.
8. **Memory and context.** The user-model card, write-time supersession, evidence quotes and the
   Samantha bar 8/8 (2026-09-30) are beyond what Leon 2.0 (markdown memory files), Khoj or OVOS
   ship (see the 2026-09-29 report).

## 4. Horizon: blocked by the rocks or by RAM

| Item | Best in field | Blocker | Watch trigger |
|---|---|---|---|
| Full-duplex speech-to-speech (talk and listen at once, natural overlap) | Moshi (~200 ms); MiniCPM-o 4.5 (9B, ~5 GB int4) ([learnopencv](https://learnopencv.com/minicpm-o-4-5-a-9b-model-that-can-see-hear-and-speak-at-the-same-time/)); 2026 research on tool calls in full duplex via a frontend/backend split, 92–97 % tool-call recall ([arXiv 2609.19334](https://arxiv.org/abs/2609.19334)) | Replaces the brain rock; RAM. Tracker §5 NOT-doing. | New hardware. The frontend/backend split is the same shape as B1.5 (acknowledge while tools run), which *is* doable now. |
| Native affective dialog (prosody in, prosody out) | Hume EVI 3, Gemini Live affective dialog | Cloud S2S; Gemma 4 audio input needs a BF16 mmproj, and llama-server `input_audio` was closed upstream (plan §6a, B5.5) | B0.1 RAM reclaim + an upstream llama.cpp audio path. P4 + W11 is the local approximation. |
| Adaptive interruption *model* (backchannel vs real barge-in from audio) | LiveKit 1.5 (86 % P / 100 % R, ≤30 ms) | Cloud-only, weights not public | Open weights. Meanwhile P2's duck-and-resume + B1.3 approximate it. |
| Streaming STT during recording | Unmute / Kyutai DSM STT | Needs ≥1.5 GB free (IDEAS.md entry; TTFA fix #2) | W3 DoD profile. |
| Persona steering in activations (control vectors / capping) | Persona Vectors ([arXiv 2507.21509](https://arxiv.org/abs/2507.21509)); Assistant Axis capping | Rock-adjacent: llama.cpp supports control vectors, but whether they compose with the QAT quant + MTP drafter is untested [unverified]; replay-gate risk | After P5 has produced a drift baseline worth correcting. |
| Diarization for ambient (who said what, overlapping) | pyannote community-1; Nemotron 3 Diarization (B4.1 note) | Torch-heavy on the Jetson; W6 consent first | B9.3 shadow week; run Pi- or dock-side if it ever ships. |
| Sight ("ask about what you see") | GLaDOS with FastVLM on an 8 GB SBC ([repo](https://github.com/dnhkng/GLaDOS)) | Gemma vision mmproj RAM on the Jetson; camera privacy | A Pi-side small VLM bake-off is possible without the W3 gate [unverified feasibility]. Tracker B7.4. |
| Endpoint anticipation (forecast the end of the turn up to 2.56 s ahead) | arXiv 2606.13450 (Interspeech 2026, with Unmute): −505 ms average for +28.4 % speculative compute | Model size and code unpublished; likely GPU [unverified]. Extra brain compute on a RAM-bound box | Code release + a CPU-sized model. B1.1 is the cheap version. |
| A faster speech→silence VAD | TEN VAD (claims Silero lags several hundred ms at speech offset; ~306 KB library) | No Linux-arm64 build listed; licence has extra conditions | An aarch64 build; then the probe's VAD stage decides. |
| Ultrasonic presence through the panel speaker | Echo Show (≥32 kHz) | Unknown whether the Jabra + Pi audio path can emit or capture near-ultrasound; needs ≥96 kHz for Amazon's band [unverified] | A one-hour probe on the Pi; zero-hardware if it works. |

## 5. Sources

Primary unless marked. Dates are publication dates where the source gives one.

**Turn-taking, latency, interruption**
- LiveKit, "Configuring turn detection and interruptions" (2026-06-30) — https://livekit.com/blog/turn-detection-and-interruption-handling
- LiveKit turn-taking tuning reference — https://docs.livekit.io/agents/logic/turns/tuning/ ; turns overview — https://docs.livekit.io/agents/logic/turns/
- LiveKit Python Agents 1.5.0 release — https://community.livekit.io/t/python-agents-1-5-0-released/619 (adaptive-model numbers via a search summary of LiveKit material [unverified])
- Pipecat user-turn strategies — https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies ; migration to 1.0 — https://docs.pipecat.ai/pipecat/migration/migration-1.0
- Smart Turn v3 (Daily) — https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/ ; releases — https://github.com/pipecat-ai/smart-turn/releases (no v4 found 2026-10-03)
- Kyutai Unmute — https://kyutai.org/unmute/ ; https://github.com/kyutai-labs/unmute ; DSM STT/TTS — https://github.com/kyutai-labs/delayed-streams-modeling/
- OpenAI Realtime VAD (semantic_vad eagerness 8/4/2 s max; truncate) — https://developers.openai.com/api/docs/guides/realtime-vad.md
- Gemini Live API guide (proactive audio, affective dialog) — https://ai.google.dev/gemini-api/docs/live-guide
- Home Assistant, "Building the AI-powered local smart home" (2025-09-11) — https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/
- Home Assistant Voice chapter 10 (2025-06-25) — https://www.home-assistant.io/blog/2025/06/25/voice-chapter-10/ ; 2024.12 release (prefer local) — https://www.home-assistant.io/blog/2024/12/04/release-202412/
- Voice PE (XMOS XU316 AEC, beamforming) — https://www.home-assistant.io/blog/2024/12/19/voice-preview-edition-the-era-of-open-voice/ ; field review numbers [unverified] — https://smarthomefieldguide.com/blog/home-assistant-voice-pe-review-2026/
- Hu et al., frontend-backend tool calls in full-duplex models (2026-09-16) — https://arxiv.org/abs/2609.19334

**Mood, identity, presence**
- Hume EVI overview — https://dev.hume.ai/docs/speech-to-speech-evi/overview ; EVI 3 latency figures via secondary [unverified] — https://www.techraisal.com/blog/hume-ai-empathic-voice-real-time-emotion-and-evi-3-for-conversational-ai_1756380371/
- Wav2Small (Kounadis-Bastian et al.) — https://arxiv.org/html/2408.13920v1 ; weights — https://huggingface.co/dkounadis/wav2small ; https://github.com/dkounadis/wav2small
- emotion2vec+ — https://huggingface.co/emotion2vec/emotion2vec_plus_base ; community ONNX — https://huggingface.co/pankotaro/emotion2vec-plus-base-onnx
- Interspeech 2025 SER challenge (MSP-Podcast 1.12) — https://www.isca-archive.org/interspeech_2025/naini25_interspeech.pdf
- Speech + text VAD fusion — https://link.springer.com/article/10.1007/s12243-025-01069-1 ; https://www.cambridge.org/core/services/aop-cambridge-core/content/view/BCBF69FBED76857F84090A2FB58B2498/S2048770320000141a.pdf/dimensional_speech_emotion_recognition_from_speech_features_and_word_embeddings_by_using_multitask_learning.pdf
- 3D-Speaker (CAM++, ERes2NetV2 EERs) — https://github.com/modelscope/3D-Speaker ; sherpa-onnx — https://github.com/k2-fsa/sherpa-onnx
- pyannote community-1 — https://www.pyannote.ai/blog/community-1 ; https://huggingface.co/pyannote/speaker-diarization-community-1
- AS-norm implementation — https://github.com/nidwbin/AS-Norm
- Omi speaker-ID postmortem (issue #12765) — https://github.com/BasedHardware/omi/issues/12765 ; continual enrolment (PR #18483, merged 2026-09-24) — https://github.com/BasedHardware/omi/pull/18483
- speakeronnx (ONNX embedder registry) — https://github.com/TigreGotico/speakeronnx ; OVOS speaker wake verifier — https://github.com/OpenVoiceOS/ovos-ww-verifier-plugin-speaker
- ERes2NetV2 short-utterance EER — https://arxiv.org/pdf/2406.02167 ; CAM++ — https://arxiv.org/pdf/2303.00332 ; TitaNet — https://arxiv.org/pdf/2110.04410
- Apple HomePod voice recognition — https://support.apple.com/en-gb/guide/homepod/apd1841a8f81/homepod ; Google Voice Match / guest — https://support.google.com/googlehome/answer/16687976
- Alexa+ devices (Omnisense, Visual ID) — https://www.aboutamazon.com/news/devices/new-alexa-plus-amazon-devices ; LD2412 + LD2450 fusion [secondary] — https://www.atomic14.com/esp32/sensors/hlk-ld2412/
- Hume EVI FAQ (top-3 expressions into the prompt) — https://dev.hume.ai/docs/speech-to-speech-evi/faq ; Alexa frustration detection [secondary] — https://onezero.medium.com/heres-how-amazon-alexa-will-recognize-when-you-re-frustrated-a9e31751daf7
- LAION Empathic-Insight-Voice-Small (synthetic-trained; real-world untested) — https://huggingface.co/laion/Empathic-Insight-Voice-Small
- SenseVoice via sherpa-onnx (emotion + event fields) — https://k2-fsa.github.io/sherpa/onnx/sense-voice/index.html
- Limitless discontinued after the Meta acquisition (2025-12-05): per scan, secondary [unverified primary]
- HA speaker-recognition discussion — https://community.home-assistant.io/t/speaker-recognition-in-voice-assistant/654276
- Amazon ultrasonic presence — https://www.amazon.science/blog/the-science-behind-ultrasonic-motion-sensing-for-echo ; Echo presence help — https://www.amazon.com/gp/help/customer/display.html?nodeId=GSR22RYDWS3KBUYW
- Bermuda — https://github.com/agittins/bermuda ; ESPHome + Bermuda guide (2025-12) — https://www.derekseaman.com/2025/12/home-assistant-track-whos-in-each-room-with-esphome-bermuda-ble.html
- ESPectre — https://github.com/francescopace/espectre
- ESPHome LD2450 — https://esphome.io/components/sensor/ld2450/ ; Apollo R PRO-1 — https://apolloautomation.com/products/r-pro-1

- Voice-Light (2026-09) — https://arxiv.org/html/2609.20995v1
- Vapi speech configuration — https://docs.vapi.ai/customization/speech-configuration ; Retell global settings — https://docs.retellai.com/build/conversation-flow/global-setting
- Pipecat incomplete-turn filter — https://docs.pipecat.ai/api-reference/server/utilities/turn-management/filter-incomplete-turns
- LiveKit async tools (2026-07-07) — https://livekit.com/blog/async-tools-voice-agents ; turn-handling options — https://docs.livekit.io/reference/agents/turn-handling-options/ ; dynamic-endpointing issue #7057 — https://github.com/livekit/agents/issues/7057
- LiveKit APM (WebRTC AEC3) — https://docs.livekit.io/reference/python/livekit/rtc/apm.html
- OHF linux-voice-assistant — https://github.com/OHF-Voice/linux-voice-assistant
- Moonshine v2 streaming (2026-02) — https://arxiv.org/html/2602.12241v1
- Endpoint Anticipation — https://arxiv.org/abs/2606.13450 ; TEN VAD — https://github.com/TEN-framework/ten-vad
- Home Assistant Voice chapter 11 (2025-10-22) — https://www.home-assistant.io/blog/2025/10/22/voice-chapter-11/

**Companions, proactivity, persona, self-evolution**
- Nomi proactive messaging — https://wiki.nomi.ai/When_Your_Nomi_Messages_You_First ; https://nomi.ai/nomi-knowledge/proactive-messaging-when-your-nomi-messages-you-first/
- Liu et al., Proactive Conversational Agents with Inner Thoughts (CHI 2025) — https://arxiv.org/html/2501.00383 ; https://dl.acm.org/doi/10.1145/3706598.3713760
- Liu, Zhang, Abdi, Galley et al., "Do Proactive Agents Need an LLM to Decide When to Act?" (2026-05-28, rev. 2026-09-28) — https://arxiv.org/abs/2605.30152
- ProactiveAgent / ProactiveBench — https://arxiv.org/html/2410.12361
- LangChain, Introducing ambient agents (2025-01) — https://www.langchain.com/blog/introducing-ambient-agents
- GLaDOS (dnhkng) — https://github.com/dnhkng/GLaDOS
- Leon, "Road to 2.0" (2026-03-19) — https://blog.getleon.ai/road-to-2-0/ (pulse bounds not published)
- OVOS persona — https://github.com/OpenVoiceOS/ovos-persona ; https://openvoiceos.github.io/ovos-technical-manual/150-personas/
- Willow (alive, small) — https://github.com/toverainc/willow/releases
- Anthropic, The Assistant Axis (2026-01-19) — https://www.anthropic.com/research/assistant-axis
- Persona Vectors (2025-07) — https://arxiv.org/abs/2507.21509
- Nautilus Compass (2026-05-11) — https://arxiv.org/abs/2605.09863
- Persona drift — https://github.com/likenneth/persona_drift
- Letta skill learning (2025-12-02) — https://www.letta.com/blog/skill-learning/
- Sleep-time compute (Letta/UC Berkeley, 2025-04) — https://arxiv.org/pdf/2504.13171
- Kindroid memory docs [partly unverified] — https://kindroid.ai/v2/docs/memory/
- Honcho silent dreamer failure (issue #608) — https://github.com/plastic-labs/honcho/issues/608
- HA 2026.10 release notes (beta) — https://rc.home-assistant.io/blog/2026/09/30/release-202610/

**Not scanned in this pass:** OpenHome, Character.AI voice, Rhasspy 3 beyond its Wyoming succession,
Mycroft successors other than OVOS/Neon. Sesame CSM-1B was checked only far enough to confirm it needs CUDA. SR#1–SR#16
already cover Sesame (B1.5) and the HF speech-to-speech speculative reopen (B1.1).

---
type: research
title: "Samantha Initiative, lane E - conversational intelligence, natural conversation and emotional intelligence (Phases 2.4, 4.1, 4.7)"
date: 2026-11-03
status: lane report
description: Owner ask - "I want to be able to speak naturally to Zoe, and not feel like I'm talking to a half baked robot." What the 2025-2026 field does for turn-taking, interruption, backchannels, duplex speech, expressive TTS, emotion and proactive timing; what fits the Orin and Pi beside the fixed rocks; what each rock-lift would honestly buy; ten smallest-useful experiments with success and failure bars.
---

# Lane E - conversation that does not feel like a half-baked robot (2026-11-03)

Research only. Nothing on the box was changed, played or restarted; no tests were run. Repo read at the worktree
(`origin/main` plus the round-4 bundle). The web was searched and fetched on 2026-11-03.

Labels. Claims about the field: **[Proven]** = read in the primary paper, repo or docs this session; **[Experimental]** = reported by the
authors or a vendor, or measured only on benchmarks unlike Zoe's room; **[Speculative]** = my extrapolation. Claims about Zoe:
**[verified]** = read in code or in a committed record this session; **[inferred]** = derived, reasoning given. A source I did not
open is marked **[not fetched]** and is never used to carry a recommendation. No utterance, name or per-person datum appears here;
only aggregate counts from the committed lane records.

Path shorthand: `D` = `scripts/setup/zoe_voice_daemon.py`, `VT` = `services/zoe-data/routers/voice_tts.py`, `FT` = `services/zoe-data/fast_tiers.py`.

## 0. What Zoe should take from this

1. **"Natural" is mostly turn-taking, and Zoe's weakest links are the two ends of the turn, not the middle.** The tail (speech end to
   first sound) is about 3.2 s median and 6.5 s p90 [inferred, lane 2-I], of which a fixed 640-800 ms is endpoint wait. The other end
   is the first word being a canned line. A 4B brain cannot be made smarter by this lane; the wait before and the manner after it can.
2. **Full-duplex models are not the answer, inside or outside the rocks.** On Full-Duplex-Bench, Moshi takes over during a pause almost
   every time (takeover rate 0.985 on synthetic pauses) and resumed after only 6% of backchannels in the v1.5 overlap test; Gemini Live
   was the most patient but answered a turn about 1.3 s late [Proven, benchmark tables below]. The best open models want 24-80 GB
   of GPU. Zoe has about 500 MB free in waking hours. Full duplex is a "not at all" on this box.
3. **The cheap, proven piece of the field is a small end-of-turn classifier, and it belongs on the Pi, not the Orin.** Smart Turn v3.2
   (Whisper-tiny encoder, about 8M parameters, 8 MB int8, BSD-2-Clause, 23 languages, 10-65 ms on CPU) reads the trailing 8 s of audio
   and says "finished / not finished" [Proven, vendor figures]. The Pi has about 5 GB of page cache free; the Orin does not. It replaces
   a fixed tail with a judged one and is the standard answer to both "cut me off" and "waits too long".
4. **Interruption handling is already built; it has never been heard.** Duck, decide, resume (`BARGE_DUCK_ENABLED`) is the same policy
   LiveKit now ships as "adaptive interruption" (false-barge classification, resume without regenerating). Zoe's version is flag-dark
   and untested at the panel. Turning it on in a lab is the highest value-per-effort item in this report.
5. **Continuity is thinner than it looks.** The voice path loads the last **3 chat rows** of history per turn (`VT:293`, `limit=3`)
   [verified]: about one exchange and a half. Recall injection is trigger-gated and almost never fires on real speech (#2225, lane U
   of the 11-02 bundle). A person's "that one", "the other one", "as I said" are resolved from this window. Fix the window and
   the injection before buying anything.
6. **Emotion: act on text and timing, not on a classifier, and never claim a feeling.** Alexa's documented behaviour (apologise and ask
   a clarifying question after "no, that's not what I said") is the shipped state of the art, and the repo already has its text half
   (the struggle detector). Acted-emotion models score 90-99% and fall to 28-35% on real speech [Proven, tone-of-voice record]. A
   per-speaker prosody *delta* is a corroborating vote, not a verdict.
7. **Silence is a feature with four separate policies, and Zoe has one.** Do not wake (FAR/FRR is still unmeasured), do not answer
   unaddressed speech in the follow-up window (`ZOE_FOLLOWUP_ADDRESSED` is off), do not volunteer (spoken proactive brief is off by
   owner decision), and do not fill a pause. Section 2.5 makes these four separate, measurable rules.
8. **Kokoro's limit is expressiveness, not quality, and most of what is missing can be faked with levers Zoe already controls.** The
   model takes phonemes, a length-indexed style vector and one scalar speed; there is no emotion input [Proven, source read]. Speed as a
   function of clause length, comma pauses, shorter units, and choosing words that carry the warmth are the levers. Nonverbal sounds
   (laughs, sighs) need a different TTS and therefore a rock-lift.
9. **A small "turn-act" classifier is the one genuinely new idea worth a lab slot** [Speculative]. DuplexCascade (2026) keeps ASR, LLM
   and TTS but lets a text LLM emit control tokens (wait, respond, backchannel) every 0.6 s. Zoe has a 270M FunctionGemma router
   already resident that can be trained to emit the same five acts on the streaming Moonshine partials. It would not touch the brain slot.
10. **What the household feels first is dead air and canned lines, so the order is: fix the two ends, finish the unfinished built
    things, then measure with said-vs-did plus a short blind "robot feel" panel.** None of items 1-9 swaps a rock.

## 1. The field (2025-2026) - table with verdicts

Columns: purpose and architecture | source inspected | maturity | fits Orin beside the brain? | licence | verdict. "Orin" means the Jetson that
holds Gemma 4 E4B, Kokoro (2.3 GB sidecar) and Moonshine, with about 0.5 GB free awake [verified, 11-02 bundle]. "Pi" means the
panel's Raspberry Pi 5 (8 GB, about 5 GB cache, throttle flags set) [verified, lane P].

### 1.1 End-to-end and full-duplex speech models

| Project | Purpose, architecture | Inspected | Maturity | Orin fit | Licence | Verdict |
|---|---|---|---|---|---|---|
| [Moshi / Kyutai](https://arxiv.org/abs/2410.00037) | Speech-text foundation model; user and system audio as parallel streams, inner-monologue text before audio tokens; theoretical latency 160 ms, 200 ms practical | Abstract read; FD-Bench tables read | Research-grade; weakest on pauses | No (7B-class, datacentre GPU) [Experimental, size from memory] | Code MIT (Python) / Apache (Rust); weights CC-BY-4.0 (verified) | Reference design only. Would require the owner to lift a rock. |
| [Kyutai Unmute + STT/TTS](https://github.com/kyutai-labs/delayed-streams-modeling) | Modular cascade: any text LLM + Kyutai streaming STT (1B en/fr, 500 ms delay, semantic VAD) + Kyutai TTS; delayed-streams modeling | Repo page read (announcement URL 404) | Released July 2025, production Rust server | STT-1B maybe; TTS no [Speculative] | Code MIT/Apache; STT-1B and TTS weights CC-BY-4.0 (verified) | The architecture to copy (cascade with a smart STT), not the models. Lifts STT and TTS rocks. |
| [NVIDIA PersonaPlex-7B](https://github.com/NVIDIA/PersonaPlex) | Moshi fine-tune with text persona and voice conditioning, Jan 2026 | Repo/card via search; not run | New | A 4-bit community port is 5.3 GB on Apple Silicon [secondary]; Jetson not found | Code MIT; weights NVIDIA Open Model License (verified) | Interesting for persona control; no tool path; skip. |
| NemotronLabs VoiceChat 11B | Open full-duplex with live tool calling, hybrid Mamba-Transformer; about 448 ms smooth turn-taking by NVIDIA's own run | Secondary coverage only [not fetched] | Aug 2026 | No; reported needs an 80 GB GPU | Reported research-only | Watch only. |
| [Hertz-dev](https://si.inc/hertz-dev) | 8.5B full-duplex audio base model, no instruction tuning; 120 ms real-world on an RTX 4090 | Search/card read | Base model, not an assistant | No | Apache-2.0 | Not applicable. |
| [Freeze-Omni](https://arxiv.org/abs/2411.00774) | Frozen LLM with speech in/out, chunk-wise state prediction to let the model interrupt or reject input | Abstract read; FD-Bench numbers | Research | No (Qwen2-7B + modules) | Custom Tencent licence terms (License.txt, verified) | Best open model on FD-Bench interruption quality, still 1.35 s to stop. Not applicable. |
| [LLaMA-Omni](https://arxiv.org/abs/2409.06666) / LLaMA-Omni2 | LLM-to-speech with streaming decoder; v1 226 ms; v2 0.5B-14B on Qwen2.5 | Abstract/snippets only | Research | v2 small sizes might fit [Speculative] | Not checked | Not applicable: replaces the brain. |
| GLM-4-Voice, Qwen2.5-Omni, Step-Audio, MiniCPM-o | Speech-in/speech-out LLMs, 7-9B class | Not fetched this session | Mature research | No | Mixed [not fetched] | Not assessed; all replace the brain and none runs beside it in 0.5 GB. |
| [Qwen3-Omni](https://arxiv.org/abs/2509.17765) | Thinker-Talker MoE (30B-A3B); 234 ms theoretical first-packet latency | Abstract and listing read | New | No (30B total) | Apache-2.0 [Proven, abstract] | Not applicable on this box. |
| [Sesame CSM-1B](https://huggingface.co/sesame/csm-1b) | Llama backbone + audio decoder emitting Mimi codes, conditioned on prior text AND audio of the conversation | Card via search | Open 1B; the demo model is separate | Orin GPU maybe; no RTF found | Apache-2.0 | The right idea (speech conditioned on conversation) and the wrong fit (a second TTS, slow). Lifts the TTS rock. |
| Gemini Live / OpenAI Realtime | Cloud native-audio; Gemini has `enable_affective_dialog` and preview `proactive_audio`; OpenAI has `semantic_vad` with `eagerness` and `interrupt_response` | Docs via search ([Gemini](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/configure-gemini-capabilities), [OpenAI](https://developers.openai.com/api/docs/guides/realtime-vad)) | GA / preview | Cloud | Commercial | Excluded by local/private. Useful as the feature list to meet. |

Benchmark facts that decide the full-duplex question [Proven, tables read]:

- [Full-Duplex-Bench v1](https://arxiv.org/abs/2503.04721): Moshi takes over during pauses 0.985 of the time (Gemini Live 0.255, Freeze-Omni 0.642),
  responds to an interruption in 0.257 s but with relevance 0.765 out of 5 (Gemini 3.38, Freeze-Omni 3.62); Gemini Live waits 1.30 s before taking a
  clean turn (Moshi 0.27 s). Fast models are rude; patient models are slow.
- [v1.5 overlap handling](https://arxiv.org/abs/2507.23159): GPT-4o Realtime stops in 0.23 s on a genuine interruption but also answers
  91% of "talking to someone else" and 93% of background speech; Gemini/Nova Sonic resist non-addressed speech but need 2.2 s to yield.
  Moshi resumed after 6% of backchannels. This is the household problem exactly: a room with a TV and other people.
- [Talking Turns](https://arxiv.org/abs/2503.01174) (ICLR 2025): audio foundation models are weak at recognising backchannels and
  interruptions and interrupt unpredictably, against a small supervised turn-taking model [Experimental, secondary summary read].
- [v2](https://arxiv.org/abs/2510.07838) extends this to multi-turn with an automated examiner [abstract only].

Conclusion: the field's honest position is that nobody has a model that is both fast and polite, and the polite one is the cascade
with a good turn detector. That is Zoe's shape already.

### 1.2 Turn-taking, endpointing, interruption

| Project | Purpose, architecture | Inspected | Maturity | Fit | Licence | Verdict |
|---|---|---|---|---|---|---|
| [Smart Turn v3.2](https://github.com/pipecat-ai/smart-turn) (Pipecat/Daily) | Audio-native end-of-turn: 8 s of 16 kHz PCM into Whisper-tiny + linear head; 23 languages; 8 MB int8 CPU / 32 MB fp32 GPU; 10 ms (fast CPU), 57 ms (2 vCPU Graviton), about 65 ms on a 1x cloud instance; v3.1 post: [Daily](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/) | Repo page read; no code run | v3.2, trained on cleaned data, "relatively simple" per its own README | **Pi yes** (CPU, 8 MB); Orin not needed | BSD-2-Clause | **Adopt in the lab.** Already ON in the dormant LiveKit lane (#1051); not in the Pi daemon. Third-party Portuguese test: 68.6% accuracy [Experimental], so measure on the household corpus. |
| [LiveKit turn-detector](https://docs.livekit.io/agents/logic/turns/turn-detector) | Text end-of-utterance model, Qwen2.5-0.5B distilled from a 7B teacher; multilingual 396 MB, 25-160 ms CPU; dynamic endpointing delay | Docs read | Shipping | Orin/Pi no (400 MB RAM for a gain Zoe's own Moonshine partials can approximate) | "LiveKit Model License" [secondary] | Copy the idea (semantic completeness from text), not the weights. |
| [LiveKit adaptive interruption](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/) | Audio model run after VAD on overlap chunks to separate true barge-ins from "uh-huh"; false interruption resumes playback where it left off without regenerating (v1.5.0) | Docs read | Shipping; inference on LiveKit Cloud, not open weights [secondary: community thread] | No (cloud) | Proprietary service | Copy the policy; Zoe's duck-decide-resume already encodes it. |
| [Voice Activity Projection](https://github.com/ErikEkstedt/VoiceActivityProjection) (Ekstedt and Skantze) | Self-supervised model predicting each speaker's next 2 s of voice activity, giving turn-shift, backchannel and hold probabilities; [real-time CPU demo](https://arxiv.org/abs/2401.04868) | Paper and repo page read; training data not released | Research, Interspeech/ICASSP line | CPU feasible (paper) | Repo MIT (verified) | Best academic answer to "when would a human take the floor". Two-channel input (it needs system audio too, which Zoe has). Lab only [Experimental]. |
| [Deepgram Flux](https://developers.deepgram.com/docs/flux/nova-3-migration) | STT with built-in turn events: StartOfTurn, EagerEndOfTurn, TurnResumed, EndOfTurn; about 260 ms p50 | Docs via search | GA Oct 2025 | Cloud | Commercial | Excluded (cloud STT). The eager/resume event model is exactly Zoe's B1.1 speculation. |
| [DuplexCascade](https://arxiv.org/abs/2603.09180) (2026) | VAD-free ASR→LLM→TTS in 0.6 s micro-turns; LLM emits `wait / respond / backchannel` control tokens; LoRA on 50k text dialogues | Abstract read; no numbers visible there | v1 preprint, Interspeech 2026 submission, unreviewed | Idea fits; its LLM does not share our slot | Not checked | Source of the turn-act classifier experiment (E8). |

### 1.3 Speaker identification (identity as part of conversation)

Keep WeSpeaker ResNet34 INT8: it is a near-tie with CAM++ and ERes2Net on VoxCeleb and CN-Celeb EER ([WeSpeaker](https://arxiv.org/abs/2210.17016)), and
the repo's own two-cluster corpus and thresholds are the right method [verified, lane N A2]. pyannote community-1 diarization is CC-BY-4.0 and
gated (matters for the Omi lane, not the panel); no verification-EER comparison was found [not fetched]. The product gap is enrolment
(one voice enrolled; 158 of 159 panel turns were `identified=None` before the strict flip) and "who said yes" (fixed in #2253), not model choice.

### 1.4 Expressive and streaming TTS

| Model | What it adds over Kokoro | Size / licence | Fit | Verdict |
|---|---|---|---|---|
| Kokoro-82M (live) | Fast, clean; **control surface is phonemes, one speed scalar (or a function of phoneme count), a style vector chosen by phoneme length, voice blends**; per-segment timestamps; chunk limit 510 phonemes; each chunk starts from fresh prosody [Proven: `kokoro/pipeline.py` v0.9.4 read] | 82M, Apache | Live | Stay. See section 2.6 for the levers. |
| [Chatterbox Turbo / Nano](https://github.com/resemble-ai/chatterbox) | Paralinguistic tags `[laugh] [cough] [chuckle]`, exaggeration control (original model), voice cloning; Nano targets CPU at about 3x real time on 8 cores | 350M / 110M, MIT; imperceptible watermark on output | Nano CPU possible; a second voice | Lifts "Kokoro is the ONLY voice". Lab-only reference. |
| [Orpheus](https://github.com/canopyai/Orpheus-TTS) | Eight emotion/nonverbal tags (`<laugh> <sigh> <gasp>` ...), about 200 ms streaming claim | Llama-3B backbone; repo says Apache-2.0, a secondary guide says Llama-licence attribution (conflict, unresolved) | Heavy for the Orin | Not on this box. |
| [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (Kyutai) | About 100M, about 200 ms to first chunk, roughly 6x real time on a laptop CPU, voice cloning, 7 languages | MIT | CPU, could live on the Pi | Interesting, but a second voice. Do not run until a rock is lifted. |
| Dia, Sesame CSM | Dialogue-style nonverbals; context-conditioned speech | Apache | GPU, large | Not applicable. |

### 1.5 Emotion from speech and text

| Approach | Evidence | Fit | Verdict |
|---|---|---|---|
| Per-speaker prosody deltas (F0, energy, rate, pauses) | Best-supported old result for annoyance in real human-machine dialogue (Ang et al. 2002); needs a baseline per speaker and per device; cannot do valence, sarcasm [Proven, [tone record](../tone-of-voice-deep-dive-2026-10-26.md) §3A] | Tens of MB, off the critical path | Phase 0, as a corroborating vote. |
| emotion2vec+ (base 90M, large 300M), SenseVoiceSmall (234M, 5 languages + emotion tags) | Weights under a custom "model-license" in the HF cards; SenseVoice labels happy/sad/angry/neutral [secondary] | CPU-plausible | Lab bake-off only after the RAM gate; licence must be read first. |
| Audio-native LLMs hearing tone | LISTEN benchmark: models follow the words, collapse to 16-23% on prosody-only clips [Proven via tone record]; the box's llama.cpp has the Gemma 4 audio encoder but no E4B mmproj is installed [verified] | Same model family as the brain (not a swap) | Later, flagged turns only. |
| Text cues (re-ask, correction, "no, that's not...") | Deterministic, shipped as the struggle detector in shadow | Free | Extend; this is where Alexa's shipped behaviour lives. |
| Cloud (Hume, Gemini affective dialog) | Hume's measurement API ended 2026-06-14; labels are "proxies" by its own science page | Cloud | Excluded. |

### 1.6 Proactivity, silence, continuity

| Source | Idea | Verdict |
|---|---|---|
| [Inner Thoughts](https://arxiv.org/abs/2501.00383) (CHI 2025) | Parallel covert thoughts; speak only when motivation to speak exceeds a threshold, from human heuristics (relevance, information gap, urgency, coherence, originality, balance, dynamics) | The right frame for any future proactive speech: score, then usually stay silent. Multi-party chat setting; [Experimental] for voice. |
| [Latency-masking study](https://arxiv.org/abs/2507.22352) (VR agents, 2025) | Natural conversational fillers improved perceived latency at 4.0 s and 6.5 s delays; non-verbal "processing" sounds and icons did not | [Experimental, small simulated-delay study]. Supports a spoken ack for slow tool turns (already `ZOE_FIRST_SOUND_TOOL_ACK`), and warns against beeps. |

## 2. The natural-conversation architecture inside the rocks

Principle: **the cascade stays; the floor-management layer around it is what changes.** Gemma stays the only thing that speaks
content, Moonshine the only transcriber, Kokoro the only voice. Everything below is a small piece on the Pi or a policy in zoe-data.
Where the Orin has no room (about 0.5 GB free awake, swap heavy [verified, 11-02 bundle]) the work goes to the Pi, which has
headroom but runs hot (79.6 C idle, throttle flags set [verified, lane P]); every Pi addition therefore carries a thermal check.

### 2.1 What exists today (so nothing is rebuilt)

| Need | Built | State | Evidence |
|---|---|---|---|
| Wake | openWakeWord ONNX, 3 hits at 0.45 within 1 s | Live; no measured FAR/FRR; 9% of wakes end in an empty transcript, 19% never become a turn | lane 2-I section 3 |
| End of turn | Silero tail 640 ms deep / 800 ms any-quiet; hesitation and clean-stop adaptive tails | Adaptive tails **off** (`ZOE_VAD_CLEAN_TAIL_MS`, `ZOE_VAD_HESITATION_TAIL_MS` unset) | `D:204-226`, `D:1053-1222` |
| Early start | B1.1 speculative turn: fire at the first short verdict, hold everything audible until commit, side effects deferred | Built, **off** | `voice_speculation.py`, `D:3388` |
| Streaming STT | Moonshine partials under speech | Live (`ZOE_STT_STREAM_UNDER_SPEECH=1`) | lane 2-I |
| First sound | Tool-ack line at dispatch; clause cut | Tool-ack on; clause cut off (pitch-reset caveat) | `voice_first_sound.py` |
| Interruption | Detector (3 of 6 chunks at 0.75) hard-stops; duck, decide, resume built | Hard stop live; duck **off, never run at the panel**; 8 barge-ins since 09-01, 4 with no follow-up (small n) | `D:1444-1900`, `voice-pipeline.md` Phase 1 |
| Follow-up | 5 s window after every reply, up to 999 turns | Live; addressedness check off | `D:651`, lane 2-I |
| Corrections / distress / feedback | Deterministic tiers before the brain | Live (flag-gated) | `FT:390-440, 768-820` |
| Self-correction | Struggle detector | Shadow only | `struggle_signals.py` |

The honest summary: **most of Phase 4.1 exists as code and is switched off or unverified.** The binding constraint is evidence, not
invention.

### 2.2 Turn-taking model: a judged end of turn, not a fixed tail

Target behaviour, in the order a person experiences it:

1. **Clean, complete utterance** ("turn off the kitchen light"): close early. Today's rule waits 640 ms of deep quiet even when the
   sentence is syntactically and prosodically final. Smart Turn on the last 8 s plus Moonshine's partial transcript gives a second
   opinion at about 300 ms of quiet; if both say complete, fire (with B1.1 the brain starts at once and commits on the verdict).
   Expected saving: 200-350 ms median on commands [Speculative; the corpus evidence from #1573 is -160 ms median for a 640 ms tail].
2. **Hesitation or an open clause** ("so I was wondering, um" / ends in "and", "because", "the"): wait longer, up to a cap (the
   daemon already clamps hesitation to 1.5 s), and do not fire. Text trailer rules are free (no model): a final conjunction, filler,
   or article lowers the end-of-turn probability. This is what LiveKit's text model learns, done with a lexicon (precision over recall,
   English only, in the style of `struggle_signals.py`).
3. **Trailing off** (tail reached, Smart Turn says "incomplete", no further speech for about 2.5 s): do **not** answer as if it were a
   complete request. Say one short prompt ("Mm?" or "Go on") from a cached clip and keep the mic open for one continuation window.
   This is the "unfinished thought" case; it is the single most human behaviour that costs nothing in compute.
4. **Late addition after an answer started** (user resumes within about 600 ms of endpoint): this is the speculative-reopen case
   (HF speech-to-speech `--speculative_reopen_ms`, Pipecat's speculation gate, both cited in `voice_speculation.py`). With B1.1 on,
   the audible hold means nothing was said yet; concatenate and re-run.

Placement: Smart Turn and the lexicon run in the Pi daemon, off the wake path, only when the VAD tail has passed 300 ms (so
the model runs a few times per turn, not per chunk). Silero's dropped 256 samples per chunk and the never-reset state (open since
10-05) should be fixed first; a turn detector on a skewed VAD is measuring noise. VAP-style prediction (probability the next 2 s
contain speech from the user) is the research successor; do not build it before the cheap version has numbers.

### 2.3 Interruption handling: three kinds, three behaviours

| Kind | Cue | Behaviour | Status |
|---|---|---|---|
| Backchannel ("mm-hmm", "yeah") | < 400 ms speech, no continuation | Ignore; keep speaking | Duck then resume (`BARGE_RESUME_SILENCE_MS` 400); untested |
| True interruption | >= 900 ms speech (`BARGE_COMMIT_SPEECH_MS`) | Stop, keep recording from before onset, seed the next turn with no beep | Built (`BARGE_SEED_NEXT_TURN`) |
| Other people / TV / cough | Short or non-addressed | Resume; never count as a turn | Cough fails the 3-of-6 rule today |

Add one thing not yet built: **tell the brain what the person actually heard.** The playout ledger already computes `heard_chunks` and
`heard_ms`; phase 3 of the design (A3 in the record) is to mark the unheard tail so the next reply can say "the short version is..."
instead of repeating, or drop the topic when the interruption was "thanks, got it". Without this, a barge-in resumes the same speech
and a commit loses the thread, which is exactly what makes interruption feel robotic.

The field agrees on the shape: LiveKit's v1.5 resumes after a false interruption without regenerating; FD-Bench v1.5 shows the
trade (fast yielders over-trigger on side talk, patient ones are slow). Zoe's design chooses patience for the first 900 ms and
acknowledges instantly with the duck, which is the right side of that trade for a kitchen.

### 2.4 References, unfinished thoughts, humour and ambiguity - via the brain, with the right context

These four are not model features; they are context features, and a 4B model does them well when given the right 600 tokens.

- **Window.** `_load_voice_history(limit=3)` returns three rows [verified, `VT:293`]. For a spoken exchange where a turn is a
  sentence, six to ten rows (three to five exchanges) cost about 300-500 tokens and sit *after* the stable household prefix, so the
  llama.cpp prefix cache is unaffected. Whether the Flue brain also keeps its own session context is a verification step before the
  change [unverified: this record read the zoe-data loader only].
- **Pending-question state.** When Zoe has just asked something ("did you mean X?", "which room?") the bare reply ("the second one",
  "yes") must reach the pending handler or the brain with the question attached, never the fast tiers alone. Pending
  confirmations are keyed by panel, not speaker, which was a correctness bug fixed in #2253; add the *asked question itself* to the
  brain context so ellipsis ("and the bedroom?") resolves.
- **Recall every turn, with a budget** (11-02 bundle item 1). The cheapest conversation upgrade there is: the brain stops starting
  every chat cold. Measure "sources used per turn" as a Zoe-score axis.
- **Ambiguity policy in the prompt, not in code.** A two-line rule the brain can follow: if a wrong guess is cheap and reversible,
  act and say what you did ("I've put the kitchen lights on; say if you meant the lounge"); if it is costly (a purchase, a message to a
  person), ask exactly one short question. Gemma 4 E4B-class models follow a rule like this when it is a few-shot example, not when it
  is a paragraph [Experimental; measure on the said-vs-did set].
- **Humour.** A 4B model is a modest comedian. What reads as wit in a household is *callbacks* (Zoe remembers last week's joke) and *dry
  restraint*, both of which depend on recall and brevity. Do not add a "be funny" instruction; add memory, shorter replies, and one
  banned set (no jokes during distress, no sarcasm, no teasing a child or guest per the emotional-safety note).
- **One voice for every tier** (11-02 item 4). Fast-tier lines, fallbacks and confirmations are drafted once, in the brain's manner
  (contractions, no "I'm sorry, I did not catch that"), several variants each so repeats do not echo. Empty transcript becomes a short
  natural re-ask ("Sorry, say that again?"), never a template.

### 2.5 Silence policy - four rules, four counters

| Rule | What it protects | Mechanism | Counter |
|---|---|---|---|
| Do not wake | TV, music, chatter (1,370 near-misses vs 303 wakes; 9% empty-transcript wakes) | Verifier on the wake model (`custom_verifier_models`), threshold sweep offline on the corpus, TV clips must score 0/5 | wake FA/hour, owner FRR |
| Do not reply to unaddressed speech in the follow-up window | The loop that a TV keeps alive (79% of follow-up windows heard speech) | `ZOE_FOLLOWUP_ADDRESSED` on (server-side addressedness), a closing detector ("thanks", "that's all", "ok great") that does not open a window | unaddressed-response rate |
| Do not volunteer | Owner decision: no unprompted spoken brief (W2, `ZOE_PROACTIVE_SPOKEN=0`) | Keep off. Any future proactive line is gated by a motivation score (Inner Thoughts) plus room state and quiet hours | proactive-accept rate when ever enabled |
| Do not fill a pause | A person thinking, or mid-story | A pause is not a turn unless judged one; no backchannel audio until measured (below) | cut-in rate |

Backchannels deserve one sentence of honesty. In a cascade, "mm-hmm" while the user speaks is the hardest behaviour: it reaches the
room's microphone, the endpointer must ignore its own echo, and the Jabra's hardware echo canceller only helps for playback after
the wake. The measured field result is that even full-duplex models do this badly (Moshi backchannel timing JSD 0.957 vs Gemini
0.896 on FD-Bench; Talking Turns). Zoe should give **visual** listening feedback first: the panel orb already exists, a level-driven
"I'm listening" shimmer driven by Silero probability, with a slightly different state when the system is waiting on a possibly
unfinished thought. Zero audio risk, immediate felt presence [Speculative; E5].

### 2.6 Speech output: what Kokoro can express

Proven from `kokoro/pipeline.py` (v0.9.4): the model receives phonemes plus a style vector looked up by phoneme count
(`pack[len(ps)-1]`) and a speed; speed may be a callable of phoneme count; voices can be averaged; word timestamps come back;
text is split to at most 510 phonemes; every chunk is an independent utterance. There is no emotion, emphasis or pause input.

Levers Zoe already controls, in order of value:

1. **Rhythm by construction.** Short sentences, commas where a human would breathe, em-dash thinking pauses. Kokoro reads punctuation as
   prosody; the brain is the prosody controller. A voice-turn style guide in the prompt (spoken-register, 12-18 words/sentence, no
   lists, no parentheses) is worth more than any DSP.
2. **Speed as a function of length.** Give `speed` a callable: slightly faster for short acknowledgements, slightly slower for the last
   clause of an apology or a condolence. Tens of lines, no new dependency, ear-A/B only.
3. **First-sound policy.** The clause cut gets sound out earlier but each unit restarts pitch; the repo's own note says ear-check before
   enabling. A better compromise may be a *first unit of 5-8 words that ends on a natural boundary*, which also masks the next
   chunk's synthesis.
4. **Cached human lines** ("Mm", "Okay", "One sec", "Go on") rendered once per voice, played from memory; zero synthesis latency.
5. **Phoneme overrides** for names (misaki markup), so family names are said right: a trust signal.

What Kokoro cannot do: laugh, sigh, whisper, breathe, change emotion within a sentence. Those need another TTS (section 1.4) and
therefore the owner lifting the "Kokoro is the only voice" rock. Honest expectation: inside the rocks Zoe can sound calm, brisk and
well-paced, not moved.

## 3. Rock-lift analysis (everything here "would require the owner to lift a rock")

| Lift | What it would honestly buy | What it would cost or break | Verdict |
|---|---|---|---|
| **STT: Moonshine v2 Medium -> a streaming model with semantic turn events** (Kyutai STT-1B-en_fr with built-in semantic VAD; Deepgram Flux; both have an eager-end/resume event model) | End of turn from the transcriber's own state; reported about 260 ms p50 for Flux (cloud) vs Zoe's 640-800 ms tail. The 500 ms delay of Kyutai-1B is the model's alignment delay, so it is not a free win. | Cloud options break local/private; Kyutai needs a replay-gate rebuild, a new ~1B GPU resident, and the corpus-measured said-vs-did parity Moonshine currently holds. | **Not worth it.** A Pi-side Smart Turn + Moonshine's streaming partials gets most of the benefit [Speculative]. |
| **Brain: Gemma 4 E4B -> audio-native or full-duplex model** | Hearing tone and timing; sub-500 ms turn latency; overlap and backchannels. | PersonaPlex/Moshi-class models are 7B+ and weak at tools, memory and long reasoning; VoiceChat needs 80 GB; Qwen3-Omni is 30B-A3B. FD-Bench shows fast duplex models are rude in pauses (0.985 takeover) or slow when patient. You lose the router, recall, tools, safety tiers and replay gate - the whole product. | **No.** Not on this box, and not an improvement in manner even on a bigger one. |
| Same family, **adding the E4B audio encoder (mmproj)** to the brain (not a swap) | Gemma hears the flagged turn (E4B passed 19/21 short and 20/21 long audio tests per the llama.cpp PR); about 250 extra prefill tokens per 10 s. | No E4B mmproj on disk; MTP + multimodal co-operation unverified; CUDA VMM build flag; prefill cost on a 5.9 s p90. LISTEN says such models follow the words, not the tone. | Lab only, after the RAM gate. Does not lift the rock but changes the serving command line. |
| **TTS: Kokoro -> expressive** (Chatterbox Turbo/Nano with `[laugh]`, Orpheus tags, Pocket TTS, Sesame CSM) | Real nonverbals, emotion control, voice cloning, perhaps lower first-chunk latency (Pocket TTS about 200 ms, Orpheus 200 ms claims). | "Kokoro is the ONLY voice" (owner 2026-10-21) breaks; a new voice is a new identity for the household; RAM 0.3-3 GB more on an already saturated Orin; watermark (Chatterbox); two stories on licence (Orpheus). Voice-stack replay gate repeats in full. | **Only if the owner decides the voice identity can change.** Cheapest lab path: Chatterbox-Nano or Pocket TTS on the Pi CPU for *nonverbal inserts only*, which still breaks the one-voice rule. |

The pattern across rows: the lifts that would buy a *feeling* (laughter, tone) are TTS and audio-native-model lifts; the lifts that would
buy *speed* are STT and duplex lifts; none buys *intelligence*, which is the brain's and unchanged. The first-order complaint ("half-baked
robot") is manner and memory, both inside the rocks.

### 3.1 Achievability on this box

| Capability | Today (flags/code only) | With innovation (lab + one build) | With research (open question) | Not at all |
|---|---|---|---|---|
| Judged end of turn | adaptive tails on; lexicon trailer | Smart Turn on the Pi; speculative start on | VAP-style next-2-s prediction | - |
| Interruption | duck-decide-resume on in a lab | heard-text handoff to the brain | learned barge classifier on household clips | LiveKit's cloud classifier |
| Unfinished thoughts | continuation window, "Mm?" | turn-act classifier on 270M | - | - |
| References and continuity | history 3 -> 8 rows, recall every turn | rolling one-line summary of the session | long-horizon episodic callbacks | - |
| Silence | addressedness on, closing detector, wake verifier | turn-act "wait" output | proactive scoring (Inner Thoughts) | - |
| Backchannel audio | - | cached clips in an explicit "listening mode" only | in-cascade timing | duplex overlap |
| Prosody / emotion | text cues + per-speaker deltas | emotion2vec+ as a vote (post RAM gate) | Gemma hearing tone | Reliable emotion/sarcasm from one natural clip |
| Expressive voice | speed callable, rhythm in prompt | blended voice for "soft" mode | - | Laughs, sighs, moved voice without a new TTS |
| Full duplex | - | - | distilled small duplex model | On this hardware |

## 4. Emotional intelligence, done honestly

Scope the repo already fixed: the *act* (soften, apologise, offer help, shorten) may apply to anyone present; any *record* of an
inferred state is for consenting adults only, never children or guests (tone record Q18, [emotional-safety note](../../governance/emotional-safety-note.md) sections 4-6);
inferred never supersedes stated (memory authority); crisis language has its own fixed path (section 7 of the note). One tension to
hand to the owner: the 2026-10-05 memory decision says affective memory is on for every member including children, while the safety
note says nothing in Zoe infers, scores or keeps a child's emotional state. This lane assumes the stricter note, and uses
only *in-turn, non-stored* signals for anyone who is not a consenting adult.

### 4.1 What the signals can honestly do

| Signal | Source | Reliability on natural speech | Use |
|---|---|---|---|
| Re-ask, rephrase, "no, that's not what I said", mishear | Text (struggle detector, STT snap) | High precision by design | Apologise once, shorter, offer the other reading. The shipped Alexa-class behaviour. |
| Louder / faster / higher than *this speaker's own* baseline, on *this device* | Prosody delta (Phase 0 of the tone plan) | Arousal only; not valence; needs per-device level normalisation (mic gain, distance) | Corroborate a text cue; one-turn hint "the last reply may have annoyed them"; never alone. |
| Slow, low-range speech late at night ("tired") | Lab-grade only (sleepiness challenge, controlled recordings) | Not evidenced for one clip in a noisy room | Do not infer. Use the clock: after quiet hours start, be quieter, shorter, no jokes. |
| Sarcasm | Audio models F1 about 75 on scripted TV; they over-call it from pitch and pauses | Poor in homes | Do not detect. Treat as ambiguity: if literal reading would act on something costly, confirm. |
| Crisis phrases | Conservative vetted pattern set | Designed over-triggering | The fixed spoken path; ends banter. Unchanged. |

### 4.2 Rules for speaking about feelings

- **Zoe never claims a feeling of her own as fact** ("I'm so happy for you" is fine as social warmth; "I felt lonely while you were
  away" is not). The persona note already bars therapy claims and diagnosis ("you sound depressed").
- **Zoe reports observations of content, not inner states:** "That sounded like a rough morning" about words the person said, not
  "you sound stressed" about a voice. If a prosody delta fired, it changes *her manner* silently (shorter, slower, no joke), it is not
  announced.
- **Cheap to be wrong, expensive to be presumptuous:** a wrong calm reply costs nothing; a wrong "are you okay?" costs trust.
  Ask a caring question only when text and prosody agree *and* the user has invited it previously.
- **Humour is gated by mood hints:** distress tier or a strong hint turns teasing off for the next several turns.
- **A lint on output:** a test that fails if any scripted or generated line matches a "I feel / I'm sad / I missed you" pattern in a
  non-roleplay mode. Cheap to add to the persona-drift scorer (`persona_drift.py` is the existing home).

### 4.3 Where emotion *does* belong in speech output

Kokoro cannot carry emotion, so it reaches the ear through pacing and wording: slower final clause, shorter sentences, a softer voice blend
for a "soft mode" (`labs/kokoro-voice-blend` exists), longer pauses after bad news. A modest range; tell the owner so.
## 5. Smallest useful experiments

All are lab-first and flag-dark. Any change on the voice path (daemon, `voice_tts.py`, `fast_tiers.py`) is replay-gated against
`~/.zoe-voice-samples` under `flock /tmp/zoe-voice-harness.lock`, with said-vs-did and per-stage speed not regressing, and no landing
in the night or waking-hours windows (`night_busy.sh`). Corpus caveats: 1,522 clips; quarantine folders (TV false wakes, non-speech,
duplicates) excluded from any scoring; speaker labels are not ground truth; there are no emotion labels. Effort: S = under a day,
M = a few days, L = over a week.
### E1 - Endpoint arbiter (Smart Turn + trailer lexicon), offline first
- **Hypothesis.** A judged end of turn reduces false cuts without adding median tail, and shortens the tail on complete commands.
- **Implementation.** Offline script: for each corpus clip, take the audio up to each candidate endpoint (300, 450, 640, 800 ms of
  quiet), score with Smart Turn v3.2 ONNX (`opensrc path pypi:...` / the HF model), plus a lexicon check on Moonshine's transcript of
  the same prefix. No daemon change yet.
- **Resources.** CPU on the Jetson or Pi under the test-lane flock; no GPU; model 8 MB.
- **Evaluation.** Label "continued" by the next clip from the same panel starting within 3 s, plus a hand-checked 100-clip subset
  [inferred proxy; state the error]. Report false-cut rate and median tail per policy (fixed 640; fixed 800; arbiter).
- **Success.** At equal false-cut upper bound (+2.7 pt was the accepted figure for the 640 ms change), median tail shrinks >= 150 ms
  on complete utterances, or false cuts fall >= 30% at equal tail. **Failure.** Smart Turn agrees with the fixed rule within noise,
  or its accuracy on the household corpus is below the Portuguese-test level (about 69%).
- **Effort.** S-M.
### E2 - Duck, decide, resume at the panel (the owner's lab, already written)
- **Hypothesis.** Duck-decide-resume ignores backchannels and TV bursts and still stops for a real interruption within 1.1 s.
- **Implementation.** None to write: the Phase 1 lab protocol in `voice-pipeline.md` (inject 10 backchannels, 10 real interruptions,
  clips from the two quarantine folders during a ~20 s reply). Flag `BARGE_DUCK_ENABLED=1` on the Pi, owner present.
- **Resources.** The Pi, the owner, about 30 min. Audio plays only while the owner is at the panel; this lane plays none.
- **Evaluation.** `BARGE_DECIDE` lines: counts of commit / resume / ceiling, time-to-silence on commits, `_duck_leak` heals.
- **Success.** Zero kills on the 10 backchannels and the quarantine clips; 10/10 commits within 1.1 s; no leaked ducked volume.
  **Failure.** Any leaked duck after exit, or commits slower than 1.5 s.
- **Effort.** S (owner time).
### E3 - Continuity: history window and the asked question
- **Hypothesis.** Extending voice history and attaching the pending question makes follow-ups ("and the other one?") resolve.
- **Implementation.** `limit` 3 -> 8 behind a flag; pass Zoe's last question into the prompt as its own line; confirm first whether
  the Flue brain already carries session context.
- **Resources.** Text replay only, no audio, `ZOE_SEAM_RECALL_INJECT` semantics checked; llama prefix cache hit rate watched.
- **Evaluation.** A scripted set of 40 two- and three-turn exchanges (pronouns, ellipsis, "the second one", correction of a number);
  scored by exact intent or tool match, not by taste. Plus TTFT p90 and `prefix_hit` before/after.
- **Success.** Reference-resolution accuracy up >= 15 points; TTFT p90 up < 300 ms. **Failure.** Accuracy flat (then the problem is
  routing, not window) or prefix churn raises p90 by > 500 ms.
- **Effort.** S.
### E4 - One voice for every tier, and the human re-ask
- **Hypothesis.** Rewording tier replies and the empty-transcript line in the brain's manner raises the household's "natural" rating at
  zero latency cost.
- **Implementation.** A single lines file (3-5 variants per slot: ack, re-ask, can't-do, confirmation, closing), loaded by the
  tiers; variants chosen without immediate repeats; cached audio per line.
- **Resources.** Authoring plus a Kokoro render pass; no new model. A style check that bans "I'm sorry, I didn't catch that".
- **Evaluation.** Blind pairwise on 20 turns, current vs new, 3 household raters (aggregate only); "robot-feel" 1-5 per item.
- **Success.** New wins >= 70% of pairs. **Failure.** No preference, or the variants feel random; keep the old and revise.
- **Effort.** S.
### E5 - Listening orb (visual backchannel)
- **Hypothesis.** A visual "I'm hearing you / I'm waiting for the rest" state makes pauses feel attended rather than dead.
- **Implementation.** The daemon already holds Silero probability and a hesitation flag; push two bits to the panel UI over the
  existing push channel; the orb eases between listening, waiting-on-thought, thinking, speaking.
- **Resources.** Pi daemon + kiosk JS; CPU negligible. No audio.
- **Evaluation.** A/B week at the panel, one-question prompt ("did she feel like she was listening?"); count of "hello?" repeats and
  re-wakes within 8 s of a reply.
- **Success.** Re-wake/repeat counts down >= 25%. **Failure.** No change, or distraction complaints.
- **Effort.** S-M.
### E6 - Continuation window and speculative reopen
- **Hypothesis.** Holding the floor open after an "incomplete" verdict recovers thoughts that today become a wrong, answered half-sentence.
- **Implementation.** Turn B1.1 on in a lab (code exists); on a Smart Turn "incomplete" verdict or a trailer lexicon hit, play the cached
  "Mm?" after 2.5 s and keep recording up to 4 s more; concatenate audio into one STT.
- **Resources.** Pi + Orin STT; the held-audio gate (`voice_speculation.py`). Voice-gate replay; new probe cases for "trailing off".
- **Evaluation.** Corpus subset of clips ending mid-clause (found by E1 labels); say-vs-did on the joined utterance; count of wrongly
  prompted "Mm?" on complete commands.
- **Success.** >= 70% of incomplete clips recovered; <= 2% of complete commands see an "Mm?". **Failure.** More than 5% false prompts.
- **Effort.** M.
### E7 - Kokoro prosody levers, ear-first
- **Hypothesis.** A length-aware speed function plus a style guide for voice replies is audibly more natural at no latency cost.
- **Implementation.** Sidecar flag: `speed` as a callable of phoneme count; prompt-side style guide; first unit of 5-8 words.
- **Resources.** The sidecar under `flock`, 20 fixed sentences rendered offline to files (never played from here); the owner or raters listen.
- **Evaluation.** Blind A/B on the 20 sentences, 3 raters; first-chunk latency from `measure_tts.py` must not regress.
- **Success.** Preferred in >= 65% and first chunk +0 ms (within noise). **Failure.** No preference or any chunk-boundary artefact.
- **Effort.** S.
### E8 - Turn-act micro-classifier on the 270M router [Speculative]
- **Hypothesis.** The resident FunctionGemma-270M (or its SetFit stage) can emit {respond, wait, continue, backchannel-worthy, closing}
  from the last two partial transcripts and VAD state faster and cheaper than any model touching the brain slot.
- **Implementation.** Build labels from the corpus (E1 labels + text rules + a few hundred synthetic open/closed clauses), LoRA or head
  training in the lab off the box's live tree, GBNF-constrained output on the existing sidecar on :11436. Zoe's router self-train
  machinery and the DuplexCascade control-token idea are the precedents.
- **Resources.** The sidecar already running; training in a lab window (not waking hours); no new resident model.
- **Evaluation.** Held-out accuracy vs the E1 lexicon baseline; added latency per call (budget 30 ms); effect on false-cut and "wait" recall.
- **Success.** Beats lexicon+Smart Turn by >= 0.05 F1 at <= 30 ms. **Failure.** Ties (ties go to the simpler system).
- **Effort.** M-L.
### E9 - Addressedness and the closing detector (silence policy)
- **Hypothesis.** Ending the follow-up window on a closing phrase and checking addressedness cuts unaddressed replies without hurting
  real follow-ups.
- **Implementation.** `ZOE_FOLLOWUP_ADDRESSED` in a lab plus a closing lexicon; counters for windows opened, speech heard, replied.
- **Resources.** zoe-data + daemon flag; TV clips from quarantine as negatives.
- **Evaluation.** Replay of recorded follow-up windows (153 in the W3 set; 121 had speech); count replies that were not to Zoe.
- **Success.** Unaddressed replies down >= 50% with real follow-up recall >= 95%. **Failure.** Real follow-ups dropped > 5%.
- **Effort.** S-M.
### E10 - Prosody delta as a corroborating vote
Specified in the [tone record](../tone-of-voice-deep-dive-2026-10-26.md) section 6 (Phase 0, +0.10 F1 gate, numbers only, no stored audio, no
children or guests); this lane adds only that its output feeds the *manner* of the next reply (section 4.2) and the E1/E6 hesitation labels. Effort M.

Order of play (value per effort, and to unblock measurement): E2 and E4 (owner-time and authoring, no code risk) -> E3 -> E1 -> E9 ->
E5 -> E7 -> E6 -> E10 -> E8. Not to run: any full-duplex model on the box, any second TTS live (render-to-file labs only after the owner relaxes the one-voice rule), any cloud affective API.

## 6. Metrics (what to count, so "natural" becomes a number)

Computed from logs the stack already writes unless marked new. Aggregates only; no utterances in any report.

### 6.1 Latency
| Metric | Definition | Today | Target |
|---|---|---|---|
| End-of-speech to first sound (EoS-FS) | Pi `Recorded...` to first PCM write; p50 / p90 | about 3.2 s / 6.5 s [inferred] | p50 <= 2.0 s, p90 <= 4.0 s |
| Endpoint tail | speech end to recording closed | about 850 ms median | <= 500 ms on complete utterances |
| TTFA | POST to first PCM | 2.33 s / 5.64 s (n=150) | p90 <= 3.5 s |
| Brain TTFT | server `brain_ttft_ms` | 1.38 s / 5.87 s (n=102) | p90 <= 3.0 s |
| Prefix re-prefill rate | turns re-prefilling > 500 tokens | 29 of 100 | <= 10% |
| Dead-air events | gaps > 1.5 s between first sound and the next unit | not measured (new) | 0 per day |

### 6.2 Turn-taking
| Metric | Definition | Note |
|---|---|---|
| False-cut rate | user resumes within 2 s of the endpoint | New; E1 proxy label |
| Late-endpoint cost | median ms between true end (VAD) and close, per policy | E1 |
| Takeover-in-pause rate | the FD-Bench TOR analogue: reply started during a pause the user then continued through | New, from B1.1 cancel verdicts |
| Wake FA/hour and owner FRR | offline sweep on the corpus; TV clips must score 0/5 | Open since 10-05 |
| Empty-transcript rate; wakes never becoming a turn | 9% and 19% today | Target halved |
| Follow-up window hit rate and unaddressed-reply rate | 79% heard speech today | Reply rate to non-Zoe speech -> near 0 |
| Floor-hold "Mm?" precision | prompted continuations that were real | E6 |

### 6.3 Interruption recovery
| Metric | Definition | Target |
|---|---|---|
| Backchannel survival | reply continues after a "mm-hmm" | >= 95% |
| Real-interruption stop time | onset to player silent | <= 1.1 s (commit), duck audible at once |
| False-barge kills | replies cut by non-speech/TV | ~0 (4 of 8 barges followed by silence today, small n) |
| Heard-text accuracy | `heard_ms` vs a hand-timed listen | within 300 ms |
| Recovery quality | after a commit, the next reply neither repeats nor loses the thread | blind-rated, 20 cases |

### 6.4 Said-vs-did (existing instrument, extended)
- Said-vs-did on the real-voice corpus, baseline-compared (`voice_regression_probe.py`); no previously working command may fail.
- New cases to add: trailing-off utterances, two-turn ellipsis, pending-question answers, closing phrases, interrupted replies.
- Reference-resolution accuracy on the 40-exchange scripted set (E3), scored by intent/tool match.

### 6.5 Felt naturalness and emotional safety
- "Robot-feel" questionnaire (5 items, 1-5) at the panel, monthly, 3 adult raters; blind pairwise on A/B changes.
- Re-wake / repeat / "hello?" count within 8 s of a reply (proxy for dead air).
- Tone-hint precision (E10) and a hard zero on: claims of own feelings, inferred state stored for a child or guest, "you sound
  <emotion>" statements. The last three are lint/test assertions, not survey items.

### 6.6 Instrument cautions
The replay harness is warm and stops before TTS, so its speed numbers are relative and it cannot see barge, duck, endpoint or wake; those
need the panel and a person. Corpus labels are not ground truth. "Today" figures come from the cited lane records, not a fresh measurement.

## 7. Sources

Every external link is inline in the tables above and was opened (or searched, where marked "via search" or "secondary") on 2026-11-03.
Kokoro behaviour is from `pipeline.py` v0.9.4 in the opensrc cache (`~/.opensrc/repos/github.com/hexgrad/kokoro/0.9.4`).
Not fetched, so not load-bearing: GLM-4-Voice, Qwen2.5-Omni, Step-Audio, MiniCPM-o and LLaMA-Omni2 licences; emotion2vec and SenseVoice
licence texts; Kyutai weights licences; the VoiceChat model card; the VAP repository licence.

Zoe records read: [lane 2-I](../architecture-deep-dive-2-2026-10-19/lane-I-panel-voice.md), [lanes N and P](../stack-currency-and-field-2026-10-27/lane-N-inference-speech.md),
[tone record](../tone-of-voice-deep-dive-2026-10-26.md), [architecture deep dive](../architecture-deep-dive-2026-10-18.md),
[Pipecat spike](../../architecture/ambient-voice-pipecat-spike.md), [completeness 11-02](../completeness-and-issues-2026-11-02.md) section 1,
[voice-pipeline.md](../../knowledge/voice-pipeline.md), `CANONICAL.md`. Note: [VOICE_PIPELINE.md](../../architecture/VOICE_PIPELINE.md) is stale
(still describes Faster-Whisper and Piper). Code skimmed: `zoe_voice_daemon.py`, `voice_tts.py`, `fast_tiers.py`, `voice_first_sound.py`,
`voice_speculation.py`, `stt_snap.py`, `struggle_signals.py`.

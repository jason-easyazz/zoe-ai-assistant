---
type: research
title: Incomplete-turn marker, then pause thoughts (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document
description: Deep-research record for P7 of the 2026-10-03 companion-field audit — let Gemma's first token say whether the user's turn was complete (● / ◐ / ○), hold an incomplete turn instead of answering a fragment, re-engage after 5 s / 10 s, and later let a motivation-scored "pause thought" replace B1.6's fixed silence marker. Field half (turn-completeness models and their published numbers, the LLM-marks-completeness pattern, filled-pause and re-engagement research, how the companions do it) plus a read-only trace of Zoe's live endpointing → turn_stream → Kokoro path with file:line, then a flag-dark two-rung design, its token/latency/RAM cost, the interaction with B1.1 and the raise/brief blocks, a corpus measurement plan with negative controls, and a go/no-go against VISION.
---

# Incomplete-turn marker, then pause thoughts (2026-10-04)

Research date: 2026-10-04. The idea is **P7** in
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md). It
upgrades **B1.6** (the fixed 7 s silence marker) in the
[beat-the-bar tracker](../architecture/beat-the-bar-2026-program.md), sits beside **B1.1**
(speculative turn-start, flag-dark) and **B1.5** (acknowledge-while-thinking), and borrows
the deferral machinery B1.1 already built. Sources are cited inline and listed in §7.
**[unverified]** marks a claim taken from a secondary source, a summary, or a number not
measured on our hardware.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are
untouched; nothing here adds RAM on the Jetson; everything ships flag-dark; no live
service was run, restarted or queried; no turn was sent to the live API; no audio was
read; no household data is quoted. Everything about Zoe below is from the tree at
`d89e4a76` (origin/main, 2026-10-04).

## 0. TL;DR

- **The gap is real and already measured on our own corpus.** Zoe's endpointer is
  silence-only: `_Endpointer` closes a turn on 800 ms of Silero quiet, or 640 ms of *deep*
  quiet (`ZOE_VAD_TAIL_MS`, live on the Pi). The 2026-07-27 corpus pass found **~17 % of
  real commands contain a mid-utterance VAD-quiet pause ≥ 480 ms**
  (`zoe_voice_daemon.py:150-168`), the B1.1 sweep found the live endpointer closes before
  a clip's last speech-like chunk on **22 % of 1,171 recordings** (upper bound; much of
  that is noise), and #1766's 560 ms clean tail cut **5 of 246 turns mid-sentence (2 %)**.
  Nothing downstream knows a fragment arrived: the brain answers "set a timer for" as if
  it were a sentence. Smart Turn v3 is only on the LiveKit lane and, as a *veto* on the
  panel lane, was measured **not a win** (it withdrew 101–145 true-end fires to save 20–25
  cancels, −70–100 ms per turn, [b1-speculative-turn-start.md](../architecture/b1-speculative-turn-start.md)).
- **The field has converged on two layers**: an acoustic end-of-turn model on the
  audio (Smart Turn v3 8 M params / 12.6 ms CPU, Krisp 9 M / "69 % of true turn-shifts
  within 200 ms", Deepgram Flux `eot_threshold` 0.7) **and** a semantic completeness
  signal on the words (LiveKit's EOU: "reduces unintentional interruptions by 85 %",
  "falsely indicates the turn is not over 3 % of the time"; OpenAI semantic VAD: a trailing
  "uhhm" scores low and waits up to 2 / 4 / 8 s by `eagerness`; Pipecat
  `FilterIncompleteUserTurnStrategies`: the *reply LLM's first character* is ● complete,
  ◐ cut off, ○ needs time, held turns re-engage after **5.0 s / 10.0 s**). The semantic
  layer is the one Zoe lacks, and the Pipecat shape costs **one token**, no model, no RAM.
  Caveat from the literature: prosody beats text for *when* a turn ends
  (arXiv 2609.11066: adding text *raised* false detections), so the marker must be a
  **hold** signal layered on the silence endpointer, never a replacement for it.
- **Our topology makes the first token unusually cheap to read.** Flue's early-text tap
  (#1761, `early-text.ts`) forwards the first token *before* the runtime's 1 s persistence
  flush; brain TTFT is 309–402 ms while first token → first speakable unit is a median
  **1.08 s** (TTFA breakdown). So the marker arrives ~0.7 s before any audio could — a hold
  decision costs no audible latency. One extra token at 20–27 tok/s is ~40–50 ms of decode
  before the first speakable unit, and the MTP drafter will draft a constant first token
  almost free [unverified].
- **Three traps found in the trace** (§2.6): (1) a held turn yields *no audio*, and the
  daemon treats "transcript but no audio" as a failed turn — it logs a warning, beeps, and
  **does not open the follow-up window** (`zoe_voice_daemon.py:2312-2316`, `:2636`) — so
  the hold must ride a done-frame flag exactly like `conversation_mode`; (2) a "◐"-only
  reply counts as "real reply text emitted" in the Flue wrapper (`zoe_flue_client.py:1417`),
  which would **burn the day's brief and the RAISE** on a turn nobody heard; (3) the replay
  gate classifies a brain turn with no spoken text as `CANT_DO` (`replay_samples.py:264`),
  so without a new `HELD` outcome every held corpus turn would redden the function gate —
  which is also exactly the false-hold instrument we want, once it is named.
- **Verdict (§6): GO for rung 1, flag-dark, measured on the corpus first; rung 2 is
  CONDITIONAL** — it depends on conversation mode (`ZOE_CONVERSATION_OPENER_ENABLED`,
  default OFF), on B2.2's non-LLM scorer (plan §0 doctrine: the decision to speak is not
  the LLM's), and on rung 1's live false-hold rate. B1.6's fixed 7 s marker is retired in
  favour of rung 1's re-engagement + rung 2's scored thought.

## 1. Field — turn-completeness models, markers and re-engagement

### 1.1 Acoustic end-of-turn models (what Smart Turn, Krisp and Flux score)

| Model | What it scores | Size / latency | Published accuracy | Limits |
|---|---|---|---|---|
| **Smart Turn v3** (Pipecat/Daily, 2025-09) | P(turn complete) on the last **≤ 8 s** of 16 kHz mono audio, left-padded; "designed to be used in conjunction with a lightweight VAD"; scored at the VAD pause, not per frame | Whisper-Tiny encoder, **8 M params, 8 MB int8 ONNX**; **12.6 ms** on AWS c7a.2xlarge, 15.2 ms c8g.2xlarge, 33.8 ms t3.2xlarge, **59.8 ms** c8g.medium, 94.8 ms t3.medium; "around 65 ms" on Pipecat Cloud | 23 languages, test-set accuracy **97.10 % (Turkish) … 81.27 % (Vietnamese)**; GPU fp32 variant "+~1 %" | "not designed to run on very short audio segments"; no published number for trailing fillers ("um…") [unverified]. Krisp's independent bench puts **SmartTurn v3.2 at balanced accuracy 77.41, turn-hold F1 86.44** — the weakest of four. On Zoe's own corpus as a veto: NOT a win (§2.3). ([blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/), [repo](https://github.com/pipecat-ai/smart-turn), [HF](https://huggingface.co/pipecat-ai/smart-turn-v3)) |
| **Krisp Turn Prediction v3** (2025) | audio-only end-of-turn from "prosody, pausing patterns"; marketed as separating filler/thinking pauses ("um", "let me think") from true ends | **~9 M params, 30 MB SDK**, on-device; threshold 0.5 | balanced accuracy **88.05**, AUC **94.58**, F1 84.44, **turn-hold F1 91.20**; "69 % of true turn-shifts are detected within 200 ms of silence" (v2: 47 %). Same bench: Deepgram Flux 87.10 / F1-hold 92.60, LiveKit 82.70 / 83.30 | proprietary SDK; vendor-run benchmark [unverified]; 12 languages; interruption model is English-only ([blog](https://krisp.ai/blog/voice-ai-turn-taking-interruption-prediction/), [v2 post](https://krisp.ai/blog/krisp-turn-taking-v2-voice-ai-viva-sdk/)) |
| **Deepgram Flux** (2025) | STT with a conversational state machine; `EndOfTurn` when P(EOT) ≥ `eot_threshold` (**default 0.7**, range 0.5–0.9) or after `eot_timeout_ms` (500–60 000); optional `eager_eot_threshold` (0.3–0.9, unset by default) adds `EagerEndOfTurn` / `TurnResumed` | cloud | "EagerEndOfTurn 150–250 ms earlier than EndOfTurn, at the cost of 50–70 % more LLM calls"; EndOfTurn "within 1.5 s 95 % of the time" [unverified: from the docs summary] | cloud-only; the eager/resumed pair is the same shape as Zoe's B1.1 speculate → resolve/cancel ([config](https://developers.deepgram.com/docs/flux/configuration), [eager EOT](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot)) |

Two pieces of evidence on *what carries* end-of-turn:

- **Prosody over text.** A controlled ablation (arXiv 2609.11066, 2026-09) found "prosodic
  features have the strongest class separability, while text representations overlap
  substantially"; the acoustic-prosodic classifier reached **utterance F1 0.93, false-alarm
  7.8 %, median latency 400 ms**, and "adding text actually increased false detections
  without improving results" ([abs](https://arxiv.org/abs/2609.11066)). LiveKit's own
  roadmap agrees: the text EOU detector is **deprecated** for 2.0 in favour of an audio
  model ([docs](https://docs.livekit.io/agents/build/turns/turn-detector/)).
- **Fillers hold the turn, but less than expected.** Jiang, Ekstedt & Skantze (2023),
  using a Voice Activity Projection model: "while filled pauses do indeed have a
  turn-holding effect, it is perhaps not as strong as could be expected, probably due to
  the redundancy of other cues"; "no difference between 'uh' and 'um'"; "the prosodic
  properties and position of the filler has a significant effect"
  ([arXiv 2305.02101](https://arxiv.org/abs/2305.02101)). Humans take the turn ~200 ms
  after the other speaker stops; current agents take 700–1 000 ms
  ([IWSDS 2025 summary](https://dev.to/kenimo49/200ms-humans-vs-700ms-voice-agents-what-acl-iwsds-2025-says-about-the-turn-taking-gap-2iap), secondary).

Consequence for P7: the semantic marker is **not** the endpointer. The Silero tail
decides *when* the recording closes; the marker decides *whether the words that arrived
are answerable*. That is the layering every vendor below uses.

### 1.2 Semantic completeness on the words

- **LiveKit EOU (text turn detector).** v1 (2024-12): a **135 M** transformer on SmolLM v2
  over the last four turns of transcript; "model predictions are used to dynamically
  shorten or extend the VAD silence timeout"; vs VAD alone it "reduces unintentional
  interruptions by **85 %**" and "falsely indicates that the turn is not over **3 %** of the
  time"; **~50 ms** inference; English only
  ([blog](https://livekit.com/blog/using-a-transformer-to-improve-end-of-turn-detection/)).
  The current multilingual model is Qwen2.5-0.5B distilled from a 7 B teacher, INT8 ONNX,
  **396 MB, 50–160 ms**, TPR/TNR English **99.3 % / 87.0 %**, Hindi 99.4 / 96.3, Korean
  99.3 / 94.5, Chinese 99.3 / 86.6; the per-language `unlikely_threshold` values live in
  the model's `languages.json` (not reproduced here) and "determine how confident the model
  must be before considering the user's turn complete"; endpointing `min_delay` 0.5 s /
  `max_delay` 3.0 s (0.3 / 2.5 s with the audio detector)
  ([HF card](https://huggingface.co/livekit/turn-detector), [docs](https://docs.livekit.io/agents/build/turns/turn-detector/)).
  Note the asymmetry: a 13 % false "not over" rate on English is a 3 s *delay*, not a
  cut — the cheap failure mode. That is the design bias to copy.
- **OpenAI Realtime semantic VAD.** "Uses a semantic classifier to detect when the user
  has finished speaking, based on the words they have uttered … adjusting timeouts
  accordingly — longer waits for trailing speech, shorter for definitive statements";
  `eagerness` `low` / `medium` (= `auto`) / `high` map to max timeouts of **8 s / 4 s / 2 s**
  [unverified: numbers from a secondary summary of the guide]; a trailing "uhhm" is the
  documented example of a low probability. Server VAD defaults: `silence_duration_ms` 500,
  `prefix_padding_ms` 300, `threshold` 0.5 ([guide](https://developers.openai.com/api/docs/guides/realtime-vad)).
- **Speechmatics "semantic turn detection".** SmolLM2-360M-Instruct, read the probability
  of `<|im_end|>` as the next token; "if VAD detects a pause but the SLM indicates
  mid-thought, the silence window extends"; example: "Sure it's 123 764…" then a pause
  while checking notes ([blog](https://blog.speechmatics.com/semantic-turn-detection)). No
  accuracy numbers published.
- **A field anecdote with numbers** (dev.to, 2026): at Deepgram `endpointing=300`, **251 of
  1,140 long answers (22 %) were cut off**; median mid-answer pause **0.9 s**, p90 **2.4 s**,
  97 % of long answers had a pause > 300 ms. Raising to 1 200 ms cut it to 6 % but "added
  roughly 900 ms to every single turn". A two-gate design — heuristic incomplete-sentence
  check (conjunctions, fillers) + an LLM completeness classifier + speculative reply
  generation — reached **3.1 % cutoffs at +140 ms median**, with "19 % of speculative drafts
  cancelled" ([post](https://dev.to/ji_ai/deepgram-endpointing300-cut-off-22-of-my-voice-ai-users-mid-answer-2a2n), single-author, [unverified]).
  Zoe's numbers rhyme: 17 % mid-utterance pauses ≥ 480 ms, B1.1's 14.3 % cancel upper bound.

### 1.3 The LLM-marks-completeness pattern (the piece to borrow)

**Pipecat `FilterIncompleteUserTurnStrategies`** ([docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/filter-incomplete-turns)):

- The reply LLM is instructed (an `instructions` block appended to the system prompt) to
  begin **every** response with one of three characters: `complete_marker` **●** ("user
  finished their thought" → respond normally), `incomplete_short_marker` **◐** ("user was
  cut off mid-sentence" → response suppressed), `incomplete_long_marker` **○** ("user needs
  time to think" → response suppressed). (A secondary write-up of the same feature shows
  ✓ / ○ / ◐ with the same three meanings and timings; the docs' glyphs are used here.)
- On ◐ / ○ the bot holds. After `incomplete_short_timeout` **5.0 s** or
  `incomplete_long_timeout` **10.0 s** with no further user speech, the LLM receives
  `incomplete_short_prompt` / `incomplete_long_prompt` — a system prompt telling it to
  "respond with ●" followed by "a contextual message encouraging the user to continue".
- "Markers are automatically stripped from assistant transcripts." Fail-open: "If LLM
  fails to output markers: system logs a warning … buffered text is pushed normally."
- The public `on_user_turn_stopped` event "fires only after the LLM confirms the turn is
  complete (●)" — i.e. the *marker*, not the VAD, is the turn boundary for everything
  downstream (aggregation, persistence).
- Where it is strong: context-dependent completeness — "a ○ tag when a user pauses in the
  middle of a phone number, but a ✓ tag when the user pauses at the end" (secondary).
  This is exactly the case no acoustic model can score.

Siblings of the pattern: the dev.to two-gate design above (heuristic + "small LLM
classifier that answers with a single token: COMPLETE or INCOMPLETE"), Speechmatics'
`<|im_end|>` probability, and Kyutai **Unmute**'s *outgoing* marker: the system prompt says
"If the user says '...', that means they haven't spoken for a while. You can ask if they're
still there, make a comment about the silence … If it happens several times, don't make
the same kind of comment … If they don't answer three times, say some sort of goodbye
message and end your message with 'Bye!'" ([system_prompt.py](https://github.com/kyutai-labs/unmute/blob/main/unmute/llm/system_prompt.py)).
B1.6 was modelled on that; it has no time threshold in the prompt (the "7 s" is Unmute's
client-side injection cadence [unverified]).

### 1.4 Re-engagement and pause thoughts (what to say after the hold)

- **Inner Thoughts** (CHI 2025, [arXiv 2501.00383](https://arxiv.org/html/2501.00383)):
  triggers are `on_new_message` and **`on_pause` ("set to 10 seconds in our system")**; each
  cycle forms candidate thoughts and scores *intrinsic motivation* on **eight 1–5
  heuristics** (relevance, information gap, expected impact, urgency, coherence,
  originality, balance, dynamics); it speaks when the top thought clears `imThreshold`.
  Conditions: non-stop (`system1Prob` 0.7), **active contributor** (`system1Prob` 0.2,
  `imThreshold` **3.59**), **selective** (`system1Prob` 0, `imThreshold` **4.09**). Field
  study, **12 participants / 6 pairs**: active rated best by 6; selective "the least
  preferred. Only 2 participants selected it as the best, while 7 rated it as the worst."
  GPT-3.5/4; no latency reported. Lesson for rung 2: tune toward *active*; a long silence
  with nothing said reads as absence, not tact.
- **Companions.** *Moshi* is full-duplex with an "inner monologue" text stream, 160 ms
  theoretical / 200 ms practical latency; it learns pauses, overlaps and backchannels from
  data rather than rules ([arXiv 2410.00037](https://arxiv.org/abs/2410.00037)). *Sesame*
  CSM "can only model the text and speech content in a conversation — not the structure of
  the conversation itself"; turn-taking and pauses are listed as future duplex work
  ([post](https://www.sesame.com/research/crossing_the_uncanny_valley_of_voice)). *Hume EVI*
  documents "pausing EVI's responses" and prosody for mood; nothing public on silence
  re-engagement [unverified] ([FAQ](https://dev.hume.ai/docs/speech-to-speech-evi/faq)).
  None of the three publishes a hold/re-engage rule; the explicit rules live in the
  pipeline frameworks (Pipecat, Unmute) and in Nomi's "one unanswered, then wait" cadence
  (already recorded for P1). So the field's *best practice* for a text-LLM cascade is:
  **one marker, one hold, one re-engagement, then wait.**

## 2. Our system — the live path, read-only, file:line

### 2.1 The endpointer (Pi daemon)

`scripts/setup/zoe_voice_daemon.py`:

- **`_Endpointer`** (`:490`) — VAD mode stops after `VAD_ENDPOINT_SILENCE_S` (0.8 s) of
  Silero speech-absence once speech was heard; amplitude mode is the fallback. `push()`
  (`:580`) counts `_quiet` and `_deep_quiet` chunks (80 ms each); borderline chunks reset
  the deep counter ("an ambiguous pause must never take the fast exit").
- **Deep-quiet fast tail** `ZOE_VAD_TAIL_MS` (`:169`, comment `:150-168`): close after N ms
  of consecutive *deep* quiet (prob < `ZOE_VAD_TAIL_DEEP_PROB`). Live on the Pi at
  **640 ms** (W1.2b, plan §6). The comment carries the corpus evidence: "~17 % of real
  commands contain a mid-utterance VAD-quiet pause >= 480 ms"; true end-of-turn silence
  p90 median **0.062** vs mid-utterance pauses **0.179**; false-cut upper bounds 480 ms:
  8.3 % deep-gated vs 17.4 % plain; 640 ms: 3.6 % vs 8.3 %.
- **Adaptive tail** (#1766, flag-dark, `:170-192`): `ZOE_VAD_CLEAN_TAIL_MS` closes a *clean
  fall* sooner (floor 500 ms; needs `ZOE_VAD_CLEAN_MIN_SPEECH_MS` 480 of speech; "it cannot
  tell a finished sentence from a clean mid-sentence pause … on 246 corpus turns 560 ms
  ended 124 earlier (median 160 ms) and cut 5 mid-sentence (2 %)"), and
  `ZOE_VAD_HESITATION_TAIL_MS` (cap 1 500 ms) waits *longer* when the quiet run went deep
  and came back ambiguous — "breath, 'um', trailing voicing". Rule order in `push()`:
  clean → deep → hesitation/quiet (`:620-637`), with `tail_rule` logged. **This is the only
  place today that reacts to a filler**, and it is acoustic and flag-dark.
- **B1.1 hook** `ZOE_SPECULATIVE_TURN` / `ZOE_SPECULATIVE_TAIL_MS` 320 (`:202-203`,
  `speculative_ready()` `:567`): fires one speculative POST at the first deep-quiet verdict
  and keeps recording; speech after the fire forces `resolve`.
- **Follow-up window** `FOLLOW_UP_LISTEN_S` **5.0 s** (`:322`), `FOLLOW_UP_MAX_TURNS` 5,
  `FOLLOW_UP_VAD_THRESHOLD` 0.35 (`:326`); `_follow_up_listen()` (`:2434`) opens a fresh
  mic stream, drains 150 ms of chime echo, keeps a ~320 ms lookback ring, and returns
  `None` on silence. The loop in `voice_command()` (`:2636-2654`) runs **only while
  `played_audio` is True** and ends with "No follow-up speech detected, returning to wake
  mode" (`:2644`).
- **Conversation window** `CONV_WINDOW_S` **12.0 s**, `CONV_MAX_TURNS` 40, `CONV_MAX_S` 300,
  `CONV_SILENT_WINDOWS` 2 (`:333-337`); entered only when the last done frame carried
  `conversation_mode` (`_last_turn_flags`, `:340-343`, set at `:2202-2204`); loop at
  `:2594-2630`. The server side of that flag is `ZOE_CONVERSATION_OPENER_ENABLED`
  (`conversation_opener.py:109-113`, **default OFF**).

### 2.2 The turn stream (server) and the first-sentence rule

`services/zoe-data/routers/voice_tts.py`:

- `POST /api/voice/turn_stream` → `voice_turn_stream()` (`:4977`): registers the B1.1 gate
  before STT, runs Moonshine, broadcasts `voice:transcript`, runs the conversation
  opener/ender fast-path (`:5103-5170`, canned Kokoro ack + flags on the done frame — the
  **template for a no-brain spoken line**), then launches `voice_command(stream=True)`.
- Streaming brain loop (`:4445-4512`): each delta is checked for `__TOOL__:` /
  `__THINKING__:` sentinels (`_VOICE_TOOL_SENTINEL_PREFIXES`, `:968`; on the first
  tool_call with nothing spoken yet, one templated filler is spoken, `record=False`) and for
  `__ESCALATE*__:` markers (`:642`), then `_t_first_token` is stamped (`:4489`), the delta
  is appended to `token_buf`, and **the first speakable unit is cut by
  `_extract_first_unit()`** (`:1088`, called `:4499-4500`): a closed sentence once ≥
  `_FIRST_UNIT_MIN_CHARS` **12** chars; a clause break (`,;:—–` + space) only once the
  opening is ≥ `_FIRST_UNIT_CLAUSE_MIN` **60** chars; a word-boundary flush by
  `_FIRST_UNIT_SOFT_CAP` **90**. Later sentences go through `_extract_complete_sentences()`
  (`:909`).
- `_emit_sentence()` (`:4349`) appends to `full_reply_parts` (unless `record=False`),
  broadcasts `voice:responding`, synthesises via `_synthesize_kokoro_sidecar()`, paces
  (`ZOE_VOICE_TTS_MIN_SENTENCE_CADENCE_MS`), then yields a `{"chunk", "text", "provider"}`
  header line and a base64 WAV line. `_voice_preprocess()` (`:682`) strips markdown/units
  for TTS. **A marker character must be removed before `token_buf += delta`**, otherwise it
  (a) counts toward the 12-char minimum, (b) reaches Kokoro as a glyph, and (c) lands in
  `final_reply` and the chat save (`_schedule_voice_chat_save`, `:2905`).
- The done frame `{"done": true, "reply", "panel_id", …flags}` (`:4521`) is where
  `conversation_mode` / `conversation_end` already ride; the daemon copies named flags
  into `_last_turn_flags` (`zoe_voice_daemon.py:2202-2204`).

### 2.3 Smart Turn on our hardware (what we already measured)

`services/zoe-data/voice_turn.py` wraps Smart Turn v3.2-cpu (numpy log-mel, ORT,
`ZOE_SMART_TURN_THREADS` default 1, `end_of_turn_prob()` `:160`), **consumed only by the
LiveKit lane** (`b1-speculative-turn-start.md` §"dead time"). On real voice it scored
"complete-utterance 0.90 vs mid-sentence 0.02" (plan §6, W1.2). As a *veto* on the panel
lane's speculative fire, offline over 1,171 recordings: at tail 320 / veto 0.8 it removed
25 cancels (155 → 130) but withdrew 145 fires "most of them at TRUE ends (a prefix scored
mid-silence reads as 'not finished')", 63 ms per score (95 ms cold), mean saving per turn
244 → 174 ms. **Verdict in the doc: NOT a win.** Two readings for P7: (1) an acoustic
completeness score on a *prefix* is biased toward "not finished" — the same bias will
affect a text marker on a speculative prefix (§4.5); (2) the panel lane has *no* semantic
signal at all, so the marker is additive, not a re-run of the veto.

### 2.4 Where a marker would be stripped (the sidecar and the client)

- **Outgoing blocks.** `zoe_flue_client.py` folds context blocks into the user message —
  recall/offer before the words; `[Today …]` (brief), continuity and `[RAISE …]` after
  (`:1530-1545`). The block list `_FLUE_CONTEXT_BLOCKS` (`:654-659`) is "pinned equal to
  `FLUE_CONTEXT_BLOCKS`" in `labs/flue-zoe-brain-2x/src/context-blocks.ts:28-33`;
  `stripContextBlocks()` (`:52`) / `elideStaleBlocks()` (`:100`) elide whole balanced
  regions from every user message but the last, under `ZOE_BRAIN_ELIDE_STALE_BLOCKS`
  (default OFF). The `[RAISE` open line is matched by prefix, so labels can carry text
  (`selector.py:52-57`). **This is the pattern for a `[HELD` continuation block** (§4.3).
- **Incoming deltas.** The sidecar emits text deltas plus `__TOOL__:` / `__THINKING__:`
  sentinels (`streaming.ts:121-122`); the Python client passes them through unchanged
  (`zoe_flue_client.py:56-70`). `early-text.ts` taps the provider stream so the first
  token leaves before the runtime's `CANONICAL_FLUSH_DELAY_MS = 1e3` persistence flush
  ("one early token, ~0.84–1.09 s of silence, then 17–26 deltas inside 2 ms", `:15-16`;
  `ZOE_FLUE_EARLY_TEXT`). **The first token is therefore the earliest thing the server
  ever sees from the brain** — the marker needs no new channel.
- **Assistant-side persistence.** Flue stores the model's output verbatim; the sidecar
  strips nothing from assistant messages today (context-blocks.ts touches `role === 'user'`
  only, `:100-115`). Pipecat strips markers from transcripts; here the stored "◐" in Flue's
  history is harmless (and acts as an in-context example of the format) — the strip must
  happen on the wire in the Python client and before the chat save, not in the store.
- **System prompt.** The soul + doctrine blocks are assembled in the sidecar
  (`agents/zoe.ts`; `context-window.ts:55` "the system prompt (soul + every doctrine block)
  is NEVER touched"); `user-model.ts:35-42` shows the flag-gated suffix idiom
  (`withUserModelBlock`: flag off ⇒ `''` ⇒ "prompt unchanged"). The marker instruction is
  one more suffix of that shape.

### 2.5 The raise and brief blocks (what a held turn must not consume)

`zoe_flue_client.py:1385-1430`: the streaming wrapper prepares the first-turn day brief
(`brief_first_turn.prepare`, `ZOE_BRIEF_ON_FIRST_TURN`, `brief_first_turn.py:92-93`,
default OFF) and the proactivity `[RAISE …]` (`proactive_selector.prepare`,
`ZOE_PROACTIVE_SELECTOR`, caps `ZOE_PROACTIVE_RAISE_PER_DAY` 2 / `ZOE_PROACTIVE_RAISE_GAP_S`
7 200), injects them after the user's words, and after the stream **settles both with
`produced=emitted`** (`:1427-1429`), where `emitted` becomes True on any delta that is not a
sentinel and not the fallback text (`:1417`). A one-character "◐" reply satisfies that
test. Unless the marker is consumed *inside* this wrapper before the `emitted` check, a
held turn **spends the day's brief and one of the two daily raises on words nobody
heard** — and the brain, told to "raise once, naturally", may have folded the raise into a
reply that was then suppressed.

### 2.6 What happens today when a turn yields no audio (the daemon traps)

`_do_single_turn_stream()` (`zoe_voice_daemon.py:2082`): after the stream ends, if nothing
played and a transcript arrived, it logs **"turn_stream: transcript but no audio … NOT
re-POSTing"** (`:2312`), optionally plays the retry chime, and **returns False**
(`:2313-2316`). `voice_command()`'s follow-up loop is `while played_audio …` (`:2636`), so
a False ends the turn: no follow-up window, straight back to wake mode (plus
`POST_PLAY_COOLDOWN_S`). A held turn that simply emits no audio would therefore be the
*worst* of both worlds — no answer and no listening. The conversation-mode flags show the
fix shape: a done-frame flag the daemon reads before deciding what to do next.

Also relevant: `_is_junk_transcript()` (`:1326`) drops < 2-char and hallucination
transcripts on the daemon; the server's `voice_turn_count{outcome="empty_transcript"}`
path returns before the brain. Neither sees a *fragment* — "set a timer for" is a clean,
non-junk transcript.

### 2.7 The replay corpus as a source of incomplete-utterance negatives

`~/.zoe-voice-samples` (not read here) is the permanent replay-gate corpus
([voice-pipeline.md](../knowledge/voice-pipeline.md) §"regression corpus"):
auto-captured by `ZOE_VOICE_SAVE_AUDIO` at `voice_tts.py:2661-2673` as
`HHMMSS_mmm.wav`, top-level only, **1,099 top-level after the 2026-08-04 curation, 1,171
usable 16 kHz recordings** in the B1.1 sweep. `scripts/maintenance/curate_voice_corpus.py`
buckets every WAV into keep / `quarantine-format` / `quarantine-nonspeech` (Silero peak
< 0.20; 0.20–0.50 kept as BORDERLINE; off-rate captures kept as DRIFT), never deletes, and
every consumer globs `*.wav` non-recursively (`replay_samples.py::_select`, `:94`). There
are **no sidecar transcripts or labels** — the harness transcribes at replay time, and
`_classify()` (`:250-268`) yields `EMPTY / ERROR / CANT_DO / DEFERRED / OK`.

What the corpus can and cannot give P7:

- **Complete utterances (positives for ●): the whole corpus.** Every clip is a real
  post-endpointing turn the owner meant as a command or a sentence; the expected marker is
  ● on essentially all of them. This is the **false-hold** instrument.
- **Incomplete utterances (negatives, expected ◐): not labelled, but constructible.**
  `scripts/perf/measure_endpointing.py` already loads the shipped daemon source off-Pi,
  finds speech ends with the daemon's own VAD (`trim_to_speech_end`, `:182`), and knows
  which clips contain an internal quiet run ≥ a chosen length (the 17 % set). Cutting each
  such clip at its *first* internal pause ≥ 480 ms yields a real-voice prefix the live
  endpointer *could* have closed on — the natural ◐ set (expected ~200 clips). A second,
  text-only set (transcript minus its last 1–3 words; transcript + "um"; transcript ending
  on "and" / "to" / "for") costs nothing and isolates the brain from STT.
- **Caveat.** The clips were captured after endpointing, so "cut" prefixes are synthetic
  by construction, exactly as `measure_endpointing.py`'s docstring warns ("those samples
  were captured post-endpointing, already trimmed"). The natural-pause prefixes are the
  closest the corpus gets to a real fragment.

## 3. The shape of the gap

| | Field best | Zoe today | Gap |
|---|---|---|---|
| Acoustic end-of-turn | Smart Turn v3 / Krisp v3 at the VAD pause | Silero deep-quiet tail (640 ms); Smart Turn on LiveKit lane only; veto measured not a win | None worth closing on the panel lane now (prosody is already the endpointer; the veto evidence stands) |
| Semantic completeness | LiveKit EOU (−85 % interruptions, 3 % false "not over"), OpenAI semantic VAD, Pipecat ●/◐/○ | **nothing** — a fragment is answered as a sentence | **P7 rung 1** |
| Hold → re-engage | Pipecat 5 s / 10 s + contextual nudge; Unmute "…" × 3 then goodbye | follow-up window 5 s → silent return to wake; B1.6 planned a fixed 7 s marker | **P7 rung 1** (re-engage line), retires B1.6 |
| Pause thought | Inner Thoughts: on_pause 10 s, 1–5 motivation, active > selective | none inside a conversation; nightly selector raises ≤ 1 item on an *open* turn | **P7 rung 2** (conditional) |

## 4. Design — flag-dark, two rungs

### 4.1 Rung 1 — the marker and the hold (`ZOE_TURN_MARKER`, default OFF)

**Brain side (sidecar).** A flag-gated system-prompt suffix in the `withUserModelBlock`
idiom (`user-model.ts:35-42`), appended last so the llama.cpp prompt cache keeps its
prefix: *"Begin every reply with exactly one character and nothing else before it: ● if
the user has finished their thought, ◐ if they were cut off mid-sentence, ○ if they are
pausing to think. After ◐ or ○ write nothing more."* The three glyphs are single tokens
in Gemma's tokenizer or near enough [unverified — measure with the tokenizer, pick
glyphs that are one token each]. Fail-open like Pipecat: no marker ⇒ warn once per
process, treat as ●.

**Client side (`zoe_flue_client.py`, the streaming wrapper `:1385-1430`).** Consume the
first non-sentinel delta's leading marker *before* the `emitted` check (`:1417`):

- `●` → strip the glyph, forward the rest, `emitted` as today.
- `◐` / `○` → do **not** mark `emitted`; `settle(produced=False)` for both the brief and
  the raise (so neither is spent — §2.5); yield a single sentinel delta `__HOLD__:short` /
  `__HOLD__:long` and close the turn (abort the sidecar turn if `ZOE_FLUE_ABORT_ON_CANCEL`
  is on — otherwise let it drain; after ◐/○ the model is told to write nothing).
- The sentinel joins `_VOICE_TOOL_SENTINEL_PREFIXES`' family: never synthesised.

**Server side (`voice_tts.py` brain loop `:4445-4512`).** On `__HOLD__:*`: emit no
sentence, skip the chat save (or defer it, §4.3), and send the done frame as
`{"done": true, "reply": "", "hold": "short"|"long", "held_transcript": <text>}`.
Metrics: `voice_turn_count{outcome="held"}`.

**Daemon side (`zoe_voice_daemon.py`).** Read `hold` into `_last_turn_flags` beside
`conversation_mode` (`:2202-2204`); in `_do_single_turn_stream` treat `hold` as a *success
without audio* (return True before the "transcript but no audio" warning, `:2312`, and no
chime). In `voice_command()`, before the follow-up loop: if `hold` is set, open
`_follow_up_listen(window_s = 5.0 | 10.0)` **without** the follow-up chime and with the
orb in a "waiting" state (the P1 orb state), keeping the held transcript. Then:

- speech → next turn carries `{"continuation_of": held_transcript}`; the server prepends
  the held words to the new transcript (`held + " " + rest`) before the router and the
  brain, inside a `[HELD — the user paused mid-sentence; this is the whole utterance]`
  … `[END HELD]` block registered in both `_FLUE_CONTEXT_BLOCKS` and `FLUE_CONTEXT_BLOCKS`
  (pinned-equal test) so elision keeps working. The brain sees its own "◐" in history and
  the full text in the new message; the stale fragment turn elides under
  `ZOE_BRAIN_ELIDE_STALE_BLOCKS` like any other block.
- silence for the whole window → **one** re-engagement, then the ordinary 5 s follow-up,
  then wake mode. The line is **templated and canned**, not a brain call: "Go ahead, I'm
  listening." (short) / "Take your time." (long), synthesised the way the conversation
  opener ack is (`voice_tts.py:5140-5141`, Kokoro cache hit) via a tiny
  `POST /api/voice/reengage {panel_id, hold}` or, cheaper, a pre-synthesised WAV on the Pi
  beside the follow-up chime. Pipecat asks the LLM for a contextual nudge; Zoe's doctrine
  (plan §0, evidence in the 2026-10-03 audit) keeps the decision *and* the words out of the
  LLM for this rung — zero brain cost, zero chance of the nudge hallucinating.
- a second silence → no second nudge (Nomi's "one unanswered, then wait"); the held
  fragment is discarded (logged, not persisted).

**Scope.** Only brain-lane turns carry a marker. Fast-tier turns (router hit → intent →
spoken result) are answered as today; a router *abstain* on a short transcript is a
cheap second signal to log alongside the marker, not a gate. Not in conversation mode
*and* not in the follow-up window (a wake-word turn) the hold works the same way — the
hold window *is* a follow-up window with a different exit.

### 4.2 Rung 2 — pause thoughts (`ZOE_PAUSE_THOUGHTS`, default OFF; conditional)

Inside **conversation mode only** (the user opened the floor: `CONV_WINDOW_S` 12 s,
`ZOE_CONVERSATION_OPENER_ENABLED` — itself OFF today), when a listen window closes silent
and no hold is pending:

1. **Candidate** — one item from the sources that already exist: the proactive selector's
   open-loop candidates (`proactive/selector.py`, the `proactive_candidates` table, alembic
   0033) or the last recall packet's top fact. No new retrieval path; no brain call to
   *find* the thought.
2. **Score 1–5** with B2.2's hand rubric (relevance to the last two turns, information gap,
   urgency, time since Zoe last spoke, whether Jason answered the last raise) — the
   non-LLM scorer the plan's §0 doctrine requires and P10 later replaces with a learned
   head. Inner Thoughts' eight heuristics are the vocabulary; the weights are ours.
3. **Speak above a threshold tuned toward *active*** (Inner Thoughts: 3.59 beat 4.09; the
   selective setting was rated worst by 7 of 12). One thought per conversation; never
   during TTS or a barge-in (B1.6's rule); never when a hold is open. The thought is
   spoken through the normal brain turn with a `[RAISE …]`-style block, so the brain
   *phrases* it but did not *choose* it.
4. A silent window with a score below threshold ends the conversation exactly as today
   (`CONV_SILENT_WINDOWS` 2).

Rung 2 replaces B1.6's fixed "…" marker: instead of a timer that says *something*, a
scored candidate that says *this* or nothing. Its preconditions are listed in §6.

### 4.3 Persistence and the B1.1 deferral machinery

A held turn is a turn whose verdict is "wait for the continuation" — the same shape as a
speculative turn whose verdict is `resolve`. #1742 built exactly the hold: turn-level
side-effect deferral, `_spawn_bg` queue, `execute_intent` guard, intent-dispatch hold,
"held-then-once-on-commit / dropped-on-cancel" (`test_voice_speculation_write_deferral.py`).
Rung 1 should **bind the held turn's effects to that gate** (chat save, intent dispatch,
`voice:transcript` card) and release them on the continuation or drop them on abandon,
rather than inventing a second deferral path. The fragment's `voice:transcript` broadcast
("Heard: …") is the one effect that *should* go out immediately — the panel showing
"Heard: set a timer for" while the orb waits is the honest UI.

### 4.4 Cost

| | Rung 1 | Rung 2 |
|---|---|---|
| Tokens / turn | **+1 output token** (the marker) + ~60 prompt tokens once per session (suffix, cached prefix) | one normal brain turn when a thought clears the bar; the scorer is numpy/regex |
| Latency | TTFT unchanged; first speakable unit +1 token ≈ **40–50 ms at 20–27 tok/s** (less with MTP, [unverified]); `_FIRST_UNIT_MIN_CHARS` must be checked on the stripped buffer. Against a measured 1.08 s first-token→first-unit (TTFA breakdown) this is noise; the early-text tap delivers the marker ~0.7 s before any audio could play | the re-engage line is a Kokoro cache hit (~0 ms synth) or a Pi-local WAV |
| RAM (Jetson) | **0** | **0** |
| RAM (Pi) | 0 | 0 |
| Risk | marker compliance of a 4 B model (fail-open covers absence; a *wrong* marker is the measured quantity); glyph tokenisation; any un-stripped glyph reaching Kokoro | speaking when unwanted — bounded by conversation mode, one per conversation, the owner's no-unprompted-speech decision (which is about *between* conversations, `ZOE_PROACTIVE_SPOKEN=0`, not inside one) |

### 4.5 Interaction with B1.1 speculative turn-start

- A speculative prefix is, by construction, often a fragment; Smart Turn as a veto on it
  withdrew true-end fires (§2.3), and a text marker will show the same "not finished" bias.
  Rule: **on a speculative stream the marker is informational only** — it is logged with
  the verdict so the two can be compared (marker ◐ ∧ verdict `commit` = a false hold the
  daemon disproved for free; marker ● ∧ verdict `cancel` = a false ● with a free label).
  The daemon's verdict stays the authority on whether more speech came.
- After a `commit` with a ◐ marker: the daemon heard silence for the full tail *and* the
  model thinks the words are incomplete → run the rung-1 hold window. After `resolve`
  (equivalent): release as today. The gate already holds audio until the verdict, so a
  ◐ never needs a second hold path — the done frame simply carries `hold`.
- One free win: the marker gives B1.1 a *reason* for its cancels. The live flip criteria
  (< 30 % cancels, ≥ 250 ms saving) do not change.

### 4.6 Interaction with the raise and brief blocks

- Settle both with `produced=False` on a held turn (§2.5), so a hold never spends them.
- Never inject `[RAISE …]` or `[Today …]` on the **continuation** turn that also carries
  `[HELD …]`: the brain is answering a stitched utterance; keep its prompt simple. The
  wrapper already enforces "the brief wins a turn they share"; extend that precedence to
  "a HELD turn carries neither".
- The templated re-engage line carries no block at all (no brain call).

## 5. Measurement — corpus first, negative controls, then a live week

All offline runs under `flock /tmp/zoe-voice-harness.lock`, within the voice-gate RAM
rules (≥ 2 GB quiet headroom, brain stopped for nothing — this needs the live brain, so
it runs as replay traffic with the replay-isolation envelope, never against the owner's
session). No audio is read by hand; the harness transcribes.

| # | Set | Build | Expected | Metric / bar |
|---|---|---|---|---|
| A | **Complete** | `replay_samples.py --last 300` transcripts, replayed with `ZOE_TURN_MARKER=1` | ● | **false-hold rate** = ◐+○ / N. Bar: **≤ 3 %** (LiveKit's false "not over"); stretch ≤ 1 % on router-hit commands |
| B1 | **Natural fragments** | `measure_endpointing.py`-style cut of every corpus clip at its first internal quiet run ≥ 480 ms (the ~17 % set), transcribe, replay | ◐ | **hold recall** = ◐+○ / N. Bar: **≥ 80 %**; report ○ vs ◐ split |
| B2 | **Text fragments** | A's transcripts minus last 1–3 words; + " um"; ending on and/to/for/the | ◐ / ○ | hold recall ≥ 90 % (brain-only, STT excluded) |
| C | **Latency** | A, timestamps per delta through a verbatim `_extract_first_unit` (the TTFA-breakdown method) | — | added ms to first speakable unit, median ≤ **60 ms**; TTFT unchanged within noise |
| D | **Compliance** | A ∪ B | a marker on 100 % | marker-absence rate (fail-open count) ≤ 2 %; glyph token count = 1 each |

**Negative controls (the instrument must go red):**

1. **Flag off** → zero markers in any delta, zero `__HOLD__` sentinels, byte-identical
   stream (diff against the baseline run). Proves the flag.
2. **Break the strip** (unit test feeds "●Hello." into the loop) → the Kokoro request text
   must be "Hello."; the chat-save text must be "Hello."; remove the strip → both fail.
3. **Break the hold** (unit test: a `__HOLD__:short` delta) → no `chunk` line, done frame
   carries `hold`, `brief_first_turn.settle(produced=False)` and
   `proactive_selector.settle(produced=False)` are called; swap to `produced=True` → fail.
4. **Daemon** (`tests/unit` against the shipped source, `measure_endpointing.py`'s loader):
   a done frame with `hold` returns True from `_do_single_turn_stream` and opens one
   5 s / 10 s window with no chime; a second silent window does not re-engage again.
5. **Label shuffle** on A ∪ B1: accuracy must drop to ~50 % — proves the scorer is reading
   the markers, not the file order (the #1642 lesson).
6. **Replay gate**: add `HELD` to `_classify()` so a held corpus turn is neither `OK` nor
   `CANT_DO`; the gate's function bar then measures false holds directly
   (`CANT_DO+ERROR must not rise`, `HELD` reported). Without the new class the gate goes
   red on the first hold — run it once *without* the class to see it redden, then add it.

**Live week (operator step, after replay PASS with the head bound):** `ZOE_TURN_MARKER=1`
on zoe-data and the Pi `.env.voice`; count per day: holds, re-engages, continuations that
stitched, holds abandoned, and the owner's ear-check on "Go ahead, I'm listening" (does it
feel like attention or like a prompt?). Flip bar: false holds on real turns ≤ 3 %, no
double-speak, no spent brief/raise on a held turn (query the settle logs), RAM flat.

Rung 2 measurement waits for rung 1 and conversation mode; its instrument is the Samantha
bar (S4 emotional thread, S5 hook) plus a counted "thought spoken / ignored / answered"
log in `proactive_responses`, which P10 later trains on.

## 6. Go / no-go against VISION

| Principle | Rung 1 | Rung 2 |
|---|---|---|
| Rocks fixed | ✅ prompt suffix + one token; Gemma, Moonshine, Kokoro untouched | ✅ |
| Local, private, fast | ✅ no new model, no network; +~50 ms before first audio, hold decision 0.7 s before audio | ✅ scorer is local numpy/regex |
| Lab-prove before prod | ✅ corpus sets A/B exist or are one script away; replay gate gains a `HELD` class; flag-dark | ⚠️ needs conversation mode on (OFF) and B2.2's rubric |
| Build it to stick | ✅ six negative controls, two of them break-the-fix unit tests; the gate reddens on false holds by design | ⚠️ instrument is the Samantha bar + a log, weaker |
| Borrow the piece | ✅ Pipecat's marker + timeouts; Nomi's one-nudge rule; B1.1's own deferral gate | ✅ Inner Thoughts' trigger + score, not its LLM judge |
| Owner decisions | ✅ no unprompted speech *between* conversations is untouched; the nudge only follows the owner's own fragment | ⚠️ inside-conversation speech was sanctioned as B1.6; keep it there, one per conversation |
| Understand before changing | ✅ three traps found in the trace (§2.5–2.6) — the naïve port would have burnt the brief and closed the mic | — |

**GO — rung 1, flag-dark (`ZOE_TURN_MARKER`), measured on sets A/B/C/D with the controls
in §5 before any Pi deploy, replay-gated, then an operator week.** Retire B1.6's fixed
marker in the tracker in favour of P7; record the three traps there.

**CONDITIONAL — rung 2 (`ZOE_PAUSE_THOUGHTS`).** Build after (a) rung 1's live false-hold
rate is ≤ 3 %, (b) conversation mode is on, (c) B2.2's rubric exists. If (b) never flips,
rung 2 has no home: the 5 s follow-up window is too short for a "longer pause", and
speaking into a closed window is exactly the unprompted speech the owner declined.

**NO-GO (explicitly):** porting Smart Turn into the daemon as the completeness signal (the
veto evidence stands; prosody is already the endpointer); asking the brain to *write* the
re-engagement line in rung 1 (doctrine + cost); a second LLM call to classify
completeness (the first token is free, a second call is not — and on the Jetson a second
call is RAM-adjacent).

## 7. Sources

Field: [Pipecat FilterIncompleteUserTurnStrategies](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/filter-incomplete-turns) ·
[Smart Turn v3 blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/) ·
[smart-turn repo](https://github.com/pipecat-ai/smart-turn) · [HF card](https://huggingface.co/pipecat-ai/smart-turn-v3) ·
[LiveKit EOU blog](https://livekit.com/blog/using-a-transformer-to-improve-end-of-turn-detection/) ·
[LiveKit turn detector docs](https://docs.livekit.io/agents/build/turns/turn-detector/) ·
[livekit/turn-detector HF](https://huggingface.co/livekit/turn-detector) ·
[Krisp turn-taking + interruption](https://krisp.ai/blog/voice-ai-turn-taking-interruption-prediction/) ·
[Krisp v2](https://krisp.ai/blog/krisp-turn-taking-v2-voice-ai-viva-sdk/) ·
[Deepgram Flux configuration](https://developers.deepgram.com/docs/flux/configuration) ·
[Deepgram eager EOT](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot) ·
[OpenAI Realtime VAD guide](https://developers.openai.com/api/docs/guides/realtime-vad) ·
[Speechmatics semantic turn detection](https://blog.speechmatics.com/semantic-turn-detection) ·
[dev.to endpointing=300](https://dev.to/ji_ai/deepgram-endpointing300-cut-off-22-of-my-voice-ai-users-mid-answer-2a2n) ·
[Jiang/Ekstedt/Skantze 2023](https://arxiv.org/abs/2305.02101) · [arXiv 2609.11066](https://arxiv.org/abs/2609.11066) ·
[IWSDS 2025 summary](https://dev.to/kenimo49/200ms-humans-vs-700ms-voice-agents-what-acl-iwsds-2025-says-about-the-turn-taking-gap-2iap) ·
[Inner Thoughts](https://arxiv.org/html/2501.00383) · [Unmute system_prompt.py](https://github.com/kyutai-labs/unmute/blob/main/unmute/llm/system_prompt.py) ·
[Moshi](https://arxiv.org/abs/2410.00037) · [Sesame](https://www.sesame.com/research/crossing_the_uncanny_valley_of_voice) ·
[Hume EVI FAQ](https://dev.hume.ai/docs/speech-to-speech-evi/faq).

Ours: `scripts/setup/zoe_voice_daemon.py` · `services/zoe-data/routers/voice_tts.py` ·
`services/zoe-data/zoe_flue_client.py` · `services/zoe-data/proactive/selector.py` ·
`services/zoe-data/brief_first_turn.py` · `services/zoe-data/conversation_opener.py` ·
`services/zoe-data/voice_turn.py` · `services/zoe-data/tests/replay_samples.py` ·
`scripts/perf/measure_endpointing.py` · `scripts/maintenance/curate_voice_corpus.py` ·
`labs/flue-zoe-brain-2x/src/{context-blocks,early-text,streaming,user-model,context-window}.ts` ·
[b1-speculative-turn-start.md](../architecture/b1-speculative-turn-start.md) ·
[panel-ttfa-breakdown-2026-09-28.md](../knowledge/panel-ttfa-breakdown-2026-09-28.md) ·
[voice-pipeline.md](../knowledge/voice-pipeline.md) ·
[samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md) §5–6 ·
[beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md) B1.1/B1.5/B1.6 ·
[companion-field-vs-samantha-2026-10-03.md](companion-field-vs-samantha-2026-10-03.md) §1–2.

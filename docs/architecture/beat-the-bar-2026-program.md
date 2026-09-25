---
type: program-plan
date: 2026-09-25
audience: Jason + every agent — the single tracker for "Zoe better than anything else"
status: 🔨 active — NEXT ACTION is always §0
---
# Beat the 2026 bar — the program (tracker)

> **Why this exists.** Apple's home device (Siri AI + a 7-inch home hub, expected
> October 2026), Gemini for Home, Alexa+, Meta Muse and OpenAI's GPT-Live set the 2026
> bar for a home companion. The research behind this plan is in
> [`docs/knowledge/state-of-zoe-review-2026-09-25.md`](../knowledge/state-of-zoe-review-2026-09-25.md)
> (§5, §7 and the survey appendices). This file is the **tracker**: one place where every
> item has a state, an owner, a gate, and where the next action is unambiguous. Update
> it when a step lands (same PR). The rocks are fixed (Gemma 4 E4B+MTP, Moonshine,
> Kokoro); everything here optimises around them. RAM is the ceiling on every line.
>
> **State key:** ⬜ not started · 🔨 in progress · ✅ done · ⛔ blocked (says on what) ·
> 🧑 operator step (needs Jason or root on the box)

## 0. NEXT ACTION (keep this current)

1. 🧑 **Root window on the box (one sitting, ~20 min):** shrink zram (B0.1), rotate the
   Telegram token + vacuum the journal (B0.3), then let one nightly replay gate and one
   self-hosted test run go green. Everything in §1–§8 that is voice-path assumes this.
2. Merge PR #1680 (this program's parent review), then open the small PR that removes
   the retired 1.x Telegram lab (B0.5) — 35 of 52 Dependabot alerts.
3. Start B1.1 (speculative turn-start) as the first lab spike — it is the largest
   perceived-latency win and it is where Zoe can beat GPT-Live/Siri locally.

## 1. Where Zoe already beats the bar (protect these)

| Capability | Zoe | The bar | Guard |
|---|---|---|---|
| Fully local, offline-capable, nothing leaves the house | ✅ | Apple: personal context on-device but reasoning may go to PCC/Gemini; Google/Amazon: cloud | `test_canonical_invariants.py`; replay gate |
| Per-panel voice + face identity, consented, local | ✅ (flags) | Amazon Omnisense (cloud); Apple: single-user Siri | biometric retention policy |
| Speaks first (spoken morning brief, presence-gated) | ✅ | Gemini Daily Brief is text; Alexa+ nudges | W2 record |
| Per-stage latency budget + said-vs-did replay gate | ✅ | nobody publishes one | `voice_regression_probe.py` |
| Self-evolution harness (edits her own code behind a PR gate) | ✅ (paused) | none | Multica/Flue executor |
| HA + Music Assistant as hidden organs | ✅ | Gemini for Home needs a $10/mo sub | — |

## 2. The 2026 checklist (what a home companion must do) and Zoe's gap

| # | Bar (source) | Zoe today | Gap → program item |
|---|---|---|---|
| 1 | Full-duplex-feeling turn-taking with backchannels; interruptions counted (GPT-Live "80% fewer interruptions", Sesame Turnbench) | barge-in + Smart Turn v3, cancels on false interruptions | B1.1–B1.4 |
| 2 | Sub-second first audio; speak while tools run | ~1 s local; tools block | B1.1, B1.5 |
| 3 | Memory the user can see, edit, regenerate, export (Claude topics, ChatGPT Dreaming, Siri Recap) | Postgres+Chroma, no user surface | B3.4 |
| 4 | Summaries, not transcripts (Siri Recap) | transcripts kept | B3.5 |
| 5 | Actionable daily digest, morning **and evening** (Daily Brief, Home Brief) | morning brief | B2.3 |
| 6 | Standing "watch and tell me" agents (Spark, Copilot Autopilot) | none | B2.4 |
| 7 | Presence-triggered routines: who is here → what happens (Omnisense) | ID exists; not wired to proactivity | B2.1 |
| 8 | Conversation state across days and devices (Alexa+, Siri app) | per-session | B3.6 |
| 9 | Narrated home events + voice-authored automations (Gemini for Home) | HA control only | B2.3, B7.3 |
| 10 | Approval-before-action; connectors off by default | yes (flags) | keep |
| 11 | On-device model with audio input (Gemma 4 E4B, AFM 3) | E4B text; audio unused | B5.5 |
| 12 | "Ask about what you see" (glasses, panel camera) | face-ID only | B7.4 |
| 13 | Cheap speech on every endpoint (Pocket TTS on CPU) | Orin-only TTS | B5.4 |
| 14 | A named, consistent persona; safety-gated companionship | Zoe persona; demo-users guardrail | B3.7 |

## 3. Program items

### B0 — Platform floor (everything else waits on this)
- B0.1 🧑 **zram shrink**: `swapoff` all 8 zram devices with brain+Kokoro stopped, set each
  `disksize` to 244 MiB, `mkswap`+`swapon -p 5`, and change `/ 2 /` → `/ 8 /` in
  `/etc/systemd/nvzramconfig.sh`. Gate: `free -m` available ≥ 2 GB idle; nightly gate PASS.
- B0.2 ✅ 2026-09-25: openclaw-gateway + Hermes keep-warm off; router swap guard; zoe-data lean
  restart; HNSW rebuild; journald persistent; log rotation; docker prune; security pip set;
  Node 22.23.3; zoe-auth rebuilt; replay gate PASS 13/13.
- B0.3 🧑 Telegram token rotation (BotFather) + `journalctl --rotate && --vacuum-time=1s`.
- B0.4 ⬜ **llama.cpp rebuild ≥ b11178** and re-enable `--flash-attn on` +
  `--cache-type-v q8_0` with MTP (upstream fix PR #25148, 2026-06-30). Keep `--fit off`.
  Gate: 20-turn multi-prompt replay under `flock`; RSS/TTFT vs baseline; watch #25522.
- B0.5 ⬜ Retire `labs/flue-zoe-telegram/` (1.x) by removal; update `labs/AGENTS.md`;
  clears 35 Dependabot alerts. Small PR after #1680.
- B0.6 ⬜ Decide the deploy pip contract (deploy installs 9 of 39 packages). Either
  `pip install -r` in deploy (voice-gated) or state in the file header that the box is
  hand-managed. Declare `tzlocal`; drop `passlib`; fix the onnxruntime/ctranslate2 comments.
- B0.7 ⬜ **Python 3.12 venv for zoe-data only** (Kokoro + llama-server stay on 3.10/CUDA 12.6);
  CPU torch for Resemblyzer, onnxruntime 1.30, websockets 17, av 18, numpy 2, sklearn 1.9
  (re-export the router head). Gate: full `ci_safe` lanes in the venv + replay gate +
  `memory_recall_probe`. Target: before 2026-10-31 (3.10 EOL).
- B0.8 ⬜ MemPalace 3.10 + Chroma 1.5.x migration **on a copy** (needs B0.7); reconcile row
  counts against `export_memory_store.py`; self-recall probe.
- B0.9 ⬜ APScheduler 3.11.3 via `export_jobs`/`import_jobs` with pytz present; `tzlocal>=3`
  in both workflows. Gate: reminder + autopilot row counts unchanged.
- B0.10 ⬜ GitHub: allow-list `voice-gate.yml` + `break-glass.yml` under the new
  `pull_request_target` protection before 2026-11-02; set Copilot code review to Lite
  before 2026-09-28; add CodeRabbit (free on this public repo); Greptile to Starter.
- B0.11 ⬜ Omnigent: bake the `url=` Serena entry into the image (patched live 2026-09-25 in
  `/root/.codex/config.toml`; a container recreate reverts it); renew the Claude login before
  2026-10-11; move the polly lane off `claude-sdk` OAuth (policy).
- B0.12 ⬜ HA tool-name sweep (`domain__Tool` prefixes) → HA 2026.9/10 upgrade; adopt the
  MCP `device_id` meta so panel commands resolve to their room. Then MA 2.10 client check.
- B0.13 ⬜ JetPack 7.2.x reflash window — only after B0.7/B0.8 and when the J401 BSP + an
  Orin wheel index exist.

### B1 — Turn-taking that feels like a person (beats GPT-Live locally)
- B1.1 ⬜ **Speculative turn-start with a speculation gate**: fire the brain on Smart Turn's
  first "complete" (or a short VAD stop), hold TTS frames until the turn is confirmed,
  drop on cancel, keep if the final transcript is equivalent. Source: HF
  `speech-to-speech --speculative_reopen_ms`, Pipecat `speculation_gate.py`, LiveKit
  `_transcripts_equivalent`. Gate: replay corpus; cancellations < 30%; RAM flat. Owner: agent.
- B1.2 ⬜ **False-interruption pause → 2 s timer → resume** if no final transcript arrives
  (LiveKit `agent_activity.py`). Gate: replay with injected 300 ms noise bursts mid-reply.
- B1.3 ⬜ **Unmute interruption policy**: text-confirmed interrupt immediately; VAD-only
  interrupt needs ≥3 s of TTS elapsed and low continue-probability; `MinWords` (2–3) from a
  fast STT pass before cancelling; VAD hysteresis gap 0.15. Gate: false-barge count on the
  corpus with Kokoro playing through the panel speaker.
- B1.4 ⬜ **Smart Turn v3.2 + Silero v6.2 file** (drop-ins) and **backchannels**: reuse the
  Smart Turn "incomplete" score to play a soft "mm-hm" after ≥0.7 s of speech, 2.5 s
  cooldown. Add "interruptions per conversation" and "silence before first audio" to the
  replay harness.
- B1.5 ⬜ **Acknowledge-while-thinking + async tools**: fast tier emits a first clause or
  backchannel in ~300 ms, tool calls run async, the brain revises mid-utterance
  (GPT-Live "delegated backend", Sesame "speak while searching"). Flag-gated.
- B1.6 ⬜ **7 s silence marker** so Zoe speaks first inside a conversation, escalating
  (Unmute `system_prompt.py`); never during TTS or barge-in.
- B1.7 ⬜ Spoken-text-only context: truncate the stored assistant turn to what Kokoro
  actually played; mark `interrupted` (LiveKit tts-aligned transcript).
- B1.8 ⬜ `ask_question(answers[{id, sentences}])` primitive with GBNF-constrained
  recognition for confirmations and menus (HA assist_satellite + speech-to-phrase).
- B1.9 ⬜ Template fast path for the router's top-20 highest-precision intents (zero brain
  call; TTS-safe phonetic normaliser) — axiom-voice-agent.
- B1.10 ⬜ Moonshine 0.1.5 with `keyterms` (household names, rooms, devices). Voice-gated.
- B1.11 ⬜ Flue 2.1.1 in the brain sidecar (first-delta latency fix, retryable connection
  errors, truncated tool-batch recovery); then 2.2.0 for the llama.cpp tool-call fixes.

### B2 — Proactivity with judgement (beats Daily Brief / Alexa+ nudges)
- B2.1 ⬜ **Presence-triggered routines**: emit `person_recognized(panel, person, ts)` from the
  panel ID path into the proactive engine; "Zoe speaks first" on first-recognition-of-the-day
  rather than a 07:30 timer (Omnisense).
- B2.2 ⬜ **Candidate-selection → delivery-gating split** for proactive turns with a
  three-valued verdict (Immediate / Delayed / Silent) scored by a cheap non-LLM trigger over
  structured events using When2Talk's four factors + a Frigate-style 0–2 salience level;
  **unread/ignored backoff** (interval doubling); log Jason's reaction as accept/reject for
  a later reward model (N.E.K.O, Nomi, ProactiveAgent). Lab first: replay 30 days of
  trigger contexts through the rubric and count "should have been Silent".
- B2.3 ⬜ **Evening Home Brief** from HA event history (what happened, what is open, what
  needs a decision), spoken + card; reuse the morning generator (Gemini for Home).
- B2.4 ⬜ **Standing watch agents via Telegram** ("tell me when X"), each with a
  name/role/objective (Spark information agents, Copilot Autopilot). `watchers` table on
  the existing scheduler; first watcher = HA entity threshold → Telegram → panel.
- B2.5 ⬜ Per-fact `pinned` / `suppress_proactive` ("don't mention this again") and
  strength-weighted sampling instead of top-k, so she does not repeat the same three things.
- B2.6 ⬜ Sleep-time precompute: at idle, draft tomorrow's brief and 3 likely asks per
  person (Letta); prefix-cache friendly.

### B3 — Memory that is sound and visible (beats Siri Recap / ChatGPT Dreaming)
- B3.1 ⬜ **Bi-temporal supersession + "keep the richer fact"** at idle: `valid_from /
  valid_until / expired_at / superseded_by` on `facts` and `person_relationships`;
  contradiction only if intervals overlap (Graphiti `edge_operations.py`); reconciliation
  with mem0's update prompt, top-10 neighbours, integer ids. Fixes the distilled-vs-richer
  dedupe bug (M7/M9) without deleting anything. Lab: 50-pair fixture from demo transcripts.
- B3.2 ⬜ **Deterministic dream gating** for the idle consolidator (≥N new facts, ≥H hours,
  idle ≥M min, cancel on speech) + a hard brain-call budget per window + a 40-line profile
  cap (Honcho, Memobase). Explains the 44 zero-effect digests; make them explainable.
- B3.3 ⬜ **Importance-sum reflection** reusing `emotional_moment.intensity`; insights carry
  ≥2 evidence ids (Generative Agents); that is what the emotional follow-up fires on.
- B3.4 ⬜ **User-visible memory page** on the touch UI: consolidated topics, edit/delete,
  "regenerate my summary", ask-my-memory, JSON export/import (Claude, ChatGPT).
- B3.5 ⬜ **Summaries, not transcripts** for episodic memory, sensitive-data exclusion,
  "what did we talk about" surface (Siri Recap).
- B3.6 ⬜ One conversation across surfaces and days (session continuity keyed on person, not
  device) — the samantha plan's W15.
- B3.7 ⬜ **Zoe's own thread**: persona/human blocks with `limit`, `read_only`, idle-writer
  only, review-before-apply (Letta); GBrain takes-vs-facts schema for her opinions
  (`holder=zoe`, confidence, dated).
- B3.8 ⬜ Transition-capturing extraction rule ("switched from X to Y") + LLM-free
  Personalized-PageRank multi-hop over the people graph (HippoRAG 2).
- B3.9 ⬜ Speaker-cluster-gated owner attribution in consolidation (Omi): a "Jason" fact is
  promoted only when the source turn carried a voice-ID match.
- B3.10 ⬜ A 60-item Jason-corpus mini-LongMemEval dominated by *knowledge updates* and
  *abstention*, as a memory replay gate.

### B4 — Identity and presence (where Apple and Amazon are weakest locally)
- B4.1 ⬜ Speaker-verification **margin rule** (distance < θ and ≥0.10 over the second-best
  profile) + sherpa-onnx ERes2Net/CAM++ embedder in shadow during the W5 week (Omi).
- B4.2 ⬜ Speaker-gated wake word (openWakeWord custom verifier) or a purpose-trained
  "Hey Zoe" (2026 trainers).
- B4.3 🧑 Decide `ZOE_FACE_ID_ENABLED` (on, against the retention policy) → build the
  face enroll/delete UI (ZOE-6129) or turn it off.
- B4.4 ⬜ Dual-channel mic (processed for wake/STT, raw for voice-ID) on one panel (VPE).
- B4.5 ⬜ Presence hardware: Raspberry Pi AI Camera (IMX500) person-detect stream as a
  zero-CPU presence signal; HA device presence (Phase 2.5).

### B5 — Voice quality and expressiveness
- B5.1 ⬜ **Kokoro on ONNX Runtime CUDA** (same model, same voices): 2.3 GB → ~0.6–1 GB;
  RTF < 0.3 required; jetson-containers `kokoro-tts-onnx` recipe.
- B5.2 ⬜ Emotion → bounded, auditable prompt modifiers under immutable rules (GLaDOS
  constitution); Gemma emits one expression tag per sentence (OLV convention); a 48-dim
  emotion vector as the shared wire format (Hume schema).
- B5.3 ⬜ Expressive-lane bake-off for W11: Chatterbox Nano/Turbo, Supertonic 3, NeuTTS Air.
- B5.4 ⬜ **Pocket TTS on the Pi panels** (100M, MIT, CPU) for local acks/toasts when the
  Orin is busy or the brain is stopped.
- B5.5 ⬜ Gemma 4 E4B **audio input** for paralinguistics on flagged turns only (BF16 mmproj
  costs RAM — after B0.1/B5.1).
- B5.6 ⬜ Voxtral Realtime as an offline second-opinion ASR judge in the replay harness.

### B6 — Brain headroom (optimise around the rock)
- B6.1 = B0.4 (FA rebuild). B6.2 ⬜ sha256 the local GGUF + MTP drafter vs Unsloth's
  2026-07-17 re-upload; stage + replay-gate if different. B6.3 ⬜ Domain-prefixed tool
  names (`ha__`, `ma__`, `memory__`). B6.4 ⬜ Consider an E2B "fast/cheap turn" lane only
  if RAM allows after B0.1/B5.1 (AICore's variant-by-task split) — not a rock change.
- B6.5 ⬜ Client defaults: `zoe_flue_client` → `:3579`/wire 2; `ZOE_BRAIN_FAILOVER=1` after
  a gate (B1 in the register).

### B7 — Window into Zoe (UI)
- B7.1 ⬜ AG-UI 1.0 (`ACTIVITY_SNAPSHOT/DELTA`) + a fixed A2UI-style component catalog as the
  carrier for brain-built cards (never raw HTML from the 4B).
- B7.2 ⬜ Ask-card conversation mode (PR-1a) → retire `voice.html`.
- B7.3 ⬜ Voice-authored automations via HA (Gemini for Home) — later.
- B7.4 ⬜ "Ask about what you see" via the panel camera, one-shot.

### B8 — Self-evolution (nobody else has it)
- B8.1 ⬜ Rebuild the executor on Flue 2 (`init()` handles + `durable: true` tools);
  unpause Multica; land ≥3 real tickets (Phase 2 exit).
- B8.2 ⬜ SkillSpector 2.12 with the LLM stage on the local llama-server.
- B8.3 ⬜ Standing watchers (B2.4) as the first Zoe-authored background agents.

## 4. Sequencing (dependencies)

```
B0.1 zram ─┬─> nightly gate PASS ─┬─> B0.4 FA rebuild ─> B6.*, B1.11
           │                      ├─> B1.1..B1.10 (each replay-gated)
           │                      └─> B5.1 Kokoro ONNX ─> B5.2/B5.3/B5.5
B0.7 py3.12 venv ─> B0.8 Chroma/MemPalace ─> B3.1 (design against the new store)
B3.2 dream gating ─> B3.3 reflection ─> B2.2 delivery policy ─> B2.1/B2.3/B2.4
B4.3 face decision ─> B4.1/B4.2 shadow week (Pi on) ─> B3.9
B8.1 executor ─> B8.3 watchers (B2.4 can ship on the plain scheduler first)
```

## 4b. Triage of the inherited plans (2026-09-25, on Jason's "do we need to finish this?")

The test for each: does it move Zoe toward the north star, and does what exists actually
work today. Verdicts are recorded here so they are not re-litigated in chat.

| Inherited plan / idea | Verdict | Why |
|---|---|---|
| **oh-my-pi as a builder harness** (`omp-builder-adoption.md`, staged binary, H8) | **RETIRE the staging** | Its own trial found a cost leak, not a quality win; the fence is a wrapper + overlay the doc admits is not the top of the stack; it needs a metered OpenRouter key in a flat-rate economy; hashline was disabled upstream for small models. Keep the five pinned ideas as design inputs (replay invariant → B1.1, fact IDs → B3.1). Remove `/home/zoe/.local/bin/omp` + the container mount; mark the record "evaluated, not adopted". |
| Web-search + claim-backing spike (PR #1610, 62 files, conflicting) | **Re-land small as B10** | Jason asked for live lookups and "are you sure?" backing on 2026-07-24; the valuable part is a few hundred lines of Python. Close #1610, open a ≤300-line PR from its `labs/web-search-spike` core when B1.1 is in review. |
| Ask-card conversation mode (PR-1b/1c) + `voice.html` retirement | Finish | One front door; removes a legacy surface; needed for B1 on the panel. |
| Panel identity W5 shadow week, face enroll/delete UI | Finish (when the panel is on) | Multi-user identity is the edge Apple/Amazon lack locally. Face-ID: build the delete screen or turn the flag off (policy). |
| Relationship graph / recall boost enablement | Finish the measurement only | Merged; verify the running state and measure lift on real data; no more docs. |
| OpenClaw runtime code | Delete | Gateway stopped 2026-09-25; 31 skills never ran; router + trigger still mounted in `main.py`. Gated deletion PRs. |
| Desktop UI overhaul (waves 0–6) | **Park after Wave 0/1 (XSS + data-loss)** | The panel, Telegram and phone are the surfaces; 30k lines of desktop pages are not the product. Revisit after B1–B3. |
| Multica full-autonomy program (PR #1641) + Hermes retirement gates + retirement-gates packet | **Park** | Self-evolution is a pillar, but the warden plan is the most expensive, least user-visible work on the board. Keep the executor's minimal Phase 2 (B8.1); close #1641 as parked with a pointer; do not execute the gates packet. |
| Router self-train loop (`ZOE_ROUTER_SELFTRAIN`) | Park | Ratchet rejected its only candidate; 91.4% is fine; needs data and RAM that B1 needs more. |
| Brain tool-selection flake investigation | Drop | Not user-visible (router decides first); its own doc says dropping is legitimate. |
| Music discovery batch (`ZOE_MUSIC_DISCOVERY`) | Park (flag off) | Never ran (memory gate); revisit after B0.1. |
| Tauri desktop shell, orb-reacts-to-music, Pinterest-style ideas board | Park | Not on the path. |
| Telegram voice notes (W8) | Later, after B1 | Cheap once the panel lane is right. |
| Chat-split / typed-config / voice_tts split (Wave 4 tech debt) | Only when touching those files | Refactor-as-you-go; no standalone PRs. |

## 5. Explicitly NOT doing (and why)
Speech-to-speech models (replace the brain rock, >8 GB); Pipecat as a framework (lift the
pieces instead); Graphiti/Neo4j, MemOS/MIRIX, Second-Me; TEN turn detection (7B);
vLLM on Orin (no MTP); a Jetson reflash before B0.7/B0.8; any LoCoMo leaderboard claim as
a decision input.

## 6. Change log
- 2026-09-25 — created from the return-from-absence review; B0.2 done the same day.

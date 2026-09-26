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

1. 🧑 **Root / operator window on the box (one sitting, ~30 min):** shrink zram (B0.1);
   rotate the Telegram token + vacuum the journal (B0.3); rotate the Postgres password
   (B0.14); set `ZOE_DEFAULT_MEDIA_PLAYER` to the real entity and turn `ZOE_MUSIC_DISCOVERY`
   off (§4b); **swap in the verified Gemma re-upload** (B6.2 — tensors are byte-identical,
   only the chat template changed; staged + checksummed, the swap itself was refused to the
   agent as a production deploy) and restart `llama-server`, then run one replay gate; move
   the ignored leftover `modules/zoe-music/` (root-owned `__pycache__`, retired in #1653) out
   of the live checkout — it makes `test_no_zoe_music_module` red locally while CI is green.
2. **Queue state (2026-09-26 pm):** MERGED today — #1695 (B0.12 pt 1), #1698 (B0.6 deps
   contract), #1696 (B1.10 hold + keyterms plumbing), #1693 (B9.1 Omi lab). Still queued:
   #1691 (B10.0) and #1692 (B3.1 lab), each with ~10–30 review threads closed by
   failing-test-first fixes. #1694 (B1.11) parked — see B1.11. Voice-scope PRs need a
   head-bound probe after EVERY `update-branch` (strict mode); the Kokoro-paused window
   (`systemctl --user stop kokoro-tts` → probe with `--service-dir` → start → verify
   `curl :10201/health` shows `pipeline_loaded: true` AND `device: cuda`, and `/readyz`
   `dependencies.tts` names the `kokoro-sidecar` provider — `tts.ok` alone is not enough,
   it also goes green on the espeak/edge fallback or on a CPU-mode Kokoro) frees ~2 GB and is
   what made today's runs possible under the 700 MB floor. Hold the
   other PRs (drop `auto-merge`) while a voice PR lands, or it goes behind again.
3. **B0.4 llama.cpp rebuild** (brain-stop window, ~1 h compile) — after the Gemma swap has
   its own replay gate, so the two changes are attributable separately.

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
- B0.1 🧑 **zram shrink** (root; with llama-server + kokoro-tts stopped): for each of
  `/dev/zram0..7`: `swapoff`, then **`zramctl --reset /dev/zramN`** (a `disksize` write on an
  initialised device fails EBUSY), then `echo $((244*1024*1024)) > /sys/block/zramN/disksize`,
  `mkswap`, `swapon -p 5`; then change `/ 2 /` → `/ 8 /` in `/etc/systemd/nvzramconfig.sh` so it
  survives a reboot; `echo 3 > /proc/sys/vm/drop_caches` before starting the brain. Gate:
  `free -m` available ≥ 2 GB idle; the next nightly replay gate PASS.
- B0.2 ✅ 2026-09-25 (all verified on the box, evidence in the review §1): Telegram
  Happy-Eyeballs fix; openclaw-gateway + Hermes keep-warm off; health/watchdog scripts fixed;
  router swap guard applied; zoe-data lean restart; HNSW rebuild (recall `ok`); backup script
  SQLite snapshot; journald persistent; daily log rotation + log dir 750/640; docker prune
  12 GB; Dependabot alerts on; security pip set (anyio critical etc.) + 6 drifted pins;
  Node 22.23.3 on both Flue units; zoe-auth image rebuilt; Omnigent container Codex → shared
  Serena URL; Multica 401 explained; **replay gate PASS 13/13** (first in 41 runs).
  **2026-09-26 batch:** transformers 5.17.0, mcp 1.30.0, pydantic 2.13.5, alembic 1.20.0,
  PyJWT 2.15.0, pywebpush 2.5.0, livekit 1.1.20, ddgs 9.16.0, SQLAlchemy 2.0.54 installed
  (dry-run first; fastembed held at 0.8.0); zoe-data restarted (`/readyz` all ok, no new
  errors); Silero VAD file v6.2.1 in place; `ci_safe` lane 442 passed locally; **replay gate
  PASS #2** (13/13 scoreable, 7 EMPTY as baseline; medians stt 406 / brain 1962 / e2e
  1753 ms). Two pip-declared conflicts are pre-existing and belong to packages zoe-data
  does not import (`livekit-agents` 1.5.10 wants livekit 1.1.8; `memu-py` wants
  httpx<0.28) — candidates for removal in B0.7, not blockers.
  **2026-09-26 pm:** the post-merge deploy of #1695 FAILED — `services/homeassistant-mcp-bridge/tests/`
  in the live checkout was root-owned, git could not create the new test file, and the
  aborted `git reset --hard` left the live tree partially updated (HEAD stayed, 7 files
  moved ahead, no service restarted). Fixed by 🧑 `chown -R zoe:zoe` + deploy re-run (green,
  live clean). The HA bridge is a Docker container with the source bind-mounted and deploy
  does NOT restart it — after a SOURCE-only bridge merge, `docker restart
  homeassistant-mcp-bridge` (`/tools/names` live, scheme `legacy` against HA 2026.5.2); after a
  merge that touches the bridge's `requirements.txt` or Dockerfile, a restart reuses the old
  image's packages — rebuild instead: `docker compose up -d --build homeassistant-mcp-bridge`. Other root-owned paths in the
  checkout are runtime data only (`homeassistant/`, `.pi`, `.polly`, `scripts/n8n`).
  Deploys for #1698 and #1696 then ran green; live = main; drift check 0 MISMATCH.
- B0.14 ✅ 2026-09-26 (audit merged as #1687 — redacted; corrections #1688; the
  un-redacted #1684 was closed and its branch deleted because household data had reached a
  public branch. Fixes in **PR #1689** — rebuilt clean after a fake-password fixture tripped
  GitGuardian's history scan — each with a negative-controlled test, plus the reminder
  same-day fallback, ZOE_TIMEZONE-relative dates, no titles in logs, lenient stored times.
  All five live-verified on the box after the 2026-09-26 restart — people endpoint 200,
  scheduler line redacted, a date-only reminder for a test user fired via the same-day
  fallback with id-only logging, media-player fail-soft, honest capabilities. Remaining 🧑
  steps below. Original list): (1) `proactive/scheduler.py` logs the Postgres password at
  startup — redact, then 🧑 rotate the password per the secret-topology runbook; (2)
  `GET /api/memories/people` 500 (`COLLATE NOCASE` on Postgres) + sweep the class; (3)
  reminders: date-only rows skipped and a literal `"tomorrow"` due date swallowed; (4)
  `ZOE_DEFAULT_MEDIA_PLAYER` names a non-existent HA entity → fail soft + 🧑 set the right id;
  (5) web search advertised to the brain but no tool registered → make the capability honest
  (the tool itself is B10). Also 🧑 set `MEMORY_DIGEST_MODEL` in the live `.env` to the real
  model name (stale `gpt-4o-mini`, harmless, misleading).
- B0.15 ⬜ **DGX Spark evaluation as BUILDER capacity** (Jason is considering one). The brain
  rock (Gemma 4 E4B-QAT + MTP) is fixed and this item does not touch it. What a 128 GB /
  CUDA-13-on-ARM box adds: (a) a local **builder lane** — Pi through Omnigent's `pi` harness
  against a local OpenAI-compatible server (no metered key) running a 30B-class model for fix
  packets and reviews (candidates: Gemma 4 26B-A4B, Gemma 4 31B, Qwen3.6 35B-A3B, Muse
  Glimmer 30B, gpt-oss-120B 4-bit; watch llama.cpp #29168 MoE-fusion acceptance drop); (b)
  **training on-box** that the Orin cannot do (router self-train, tool-calling fine-tunes of
  E4B on Zoe's corpus — optimising *around* the rock); (c) headroom for the voice stack.
  Gate: a written bake-off (tokens/s, RAM, tool-call accuracy on Zoe's corpus) for the
  builder lane only. Any question about the production brain on new hardware is a separate,
  deliberate CANONICAL decision for Jason, out of scope here.
- B0.3 🧑 Telegram token rotation (BotFather) + `journalctl --rotate && --vacuum-time=1s`.
- B0.4 ⬜ **llama.cpp rebuild ≥ b11178** and re-enable `--flash-attn on` +
  `--cache-type-v q8_0` with MTP (upstream fix PR #25148, 2026-06-30). Keep `--fit off`.
  Gate: 20-turn multi-prompt replay under `flock`; RSS/TTFT vs baseline; watch #25522.
- B0.5 ⬜ Retire `labs/flue-zoe-telegram/` (1.x) by removal; update `labs/AGENTS.md`;
  clears 35 Dependabot alerts. Small PR after #1680.
- B0.6 ✅ 2026-09-26 **Deploy pip contract decided: the box is hand-managed, BOX FIRST, FILE
  SECOND** (the header of `services/zoe-data/requirements.txt` already said so; kept). The
  file is reconciled to the box after the gated batch (drift check: 0 MISMATCH; only
  `moonshine-voice` unpinned, owned by #1696); `deploy.yml`'s hard-coded list bumped in the
  same pass (alembic 1.20.0, pywebpush 2.5.0 — it would otherwise DOWNGRADE the box on the
  next deploy); the CI lane's av/aiortc aligned to 17.1.0/1.15.0 (x86_64 cp310 wheels
  re-verified); `tzlocal` declared; `passlib` dropped (no consumer); onnxruntime comment
  corrected (1.23.2 is the last cp310 wheel — moves only with B0.7). Not adopted:
  `pip install -r` in deploy — it would make every deploy a 39-package resolve on the live
  box, which is exactly the class of unobserved runtime change the header forbids.
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
- B0.12 🔨 HA tool-name sweep (`domain__Tool` prefixes) → HA 2026.9/10 upgrade; adopt the
  MCP `device_id` meta so panel commands resolve to their room. Then MA 2.10 client check.
  Part 1 = draft PR #1695: sweep found NO live call site (bridge is pure REST; HA's
  `mcp_server` is not loaded on 2026.5.2); `ha_tool_names.py` + `GET /tools/names` centralise
  the spelling with `/api/config` version detection; runbook
  `docs/knowledge/ha-2026-9-upgrade-runbook.md`. Part 2 = the stepped upgrade (operator) then
  MCP-server + `device_id` adoption.
- B0.13 ⬜ JetPack 7.2.x reflash window — only after B0.7/B0.8 and when the J401 BSP + an
  Orin wheel index exist.

### B1 — Turn-taking that feels like a person (beats GPT-Live locally)
- B1.1 🔨 **Speculative turn-start with a speculation gate** — draft PR #1685 (flag-dark
  `ZOE_SPECULATIVE_*`, server-side gate + daemon verdict, 30 tests, break-the-fix controls;
  stays dark until phase 2 defers write side-effects to commit; needs the panel on + a replay
  gate bound to its head — the 2026-09-25 attempt skipped on the 700 MB floor, so it waits
  for B0.1 or a quiet nightly): fire the brain on Smart Turn's
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
- B1.10 ⏸ HELD 2026-09-26 — Moonshine 0.1.5 passes said-vs-did (13/13 OK in-process off/on
  keyterms and remote; 7 EMPTY = baseline) but the STT stage is ~1.9× slower per file on the
  Orin. **Root-caused** with the per-session ONNX log (`options={"log_ort_run": True}`):
  encoder / adapter / cross-KV runs identical (~50 ms), same 8 decoder steps, same input
  shapes, but EACH decoder step is 57–70 ms in 0.1.5 vs 20–22 ms in 0.0.62. Not the model
  bundle (0.1.5 lib + old bundle: still 60 ms/step), not the ONNX Runtime binary (0.0.62's
  libonnxruntime swapped into the 0.1.5 package: still ~70 ms/step), not threads
  (`MOONSHINE_ORT_SINGLE_THREAD=1` → 1010 ms median; 2/4-core pinning 860–880; OMP 2 → 584),
  not speculative decoding (off → 508), not the update interval (10 s → 525). The cost is in
  libmoonshine 0.1.x's own decoder-run path. No matching upstream issue (searched 2026-09-26).
  Shipped by **PR #1696 (merged)**: pin `moonshine-voice==0.0.62` recorded, the
  `ZOE_MOONSHINE_KEYTERMS` plumbing (feature-detected, dormant on 0.0.62, visible on `/readyz`),
  runbook `docs/knowledge/moonshine-0-1-5-upgrade.md` §8 with the numbers. Next: 🧑 file
  upstream at moonshine-ai/moonshine with these numbers (outward-facing — Jason's call); retest
  on the next release with the same engine-only A/B.
- B1.11 ⏸ PARKED 2026-09-26 — Flue 2.1.1 (`@flue/*` 2.0.1 → 2.1.1 in both 2x sidecars; hono /
  nanoid advisories cleared, `npm audit` 0; 209/209 + 44/44 tests; store format unchanged, one
  fold-checkpoint re-fold on first start). Draft **PR #1694** was proven the way the contract
  intends — the 2.1.1 build from the PR worktree ran on a parallel port :3580 with an isolated
  store and the head-bound replay PASSED twice — but the branch upgrades the auto-deployed trees
  IN PLACE and `labs/AGENTS.md` mandates a SIBLING directory for version bumps. 🧑 Jason
  decides: (a) the two-PR sibling + cutover route (as #1675 did for 1.x → 2.x), or (b) amend
  the contract to allow in-place patch/minor dependency bumps that carry head-bound
  parallel-port evidence. Then 2.2.0 for the llama.cpp tool-call fixes.

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
- B3.2 🔨 **Deterministic dream gating** for the idle consolidator (first step MERGED as #1682:
  the alert distinguishes idle / no-history / extractor vs processing errors, one cutoff for
  selection and probe, the emotional pass survives a fact-parse failure, consolidation has its
  own verdict vocabulary, counts on the status endpoint) (≥N new facts, ≥H hours,
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
- B6.1 = B0.4 (FA rebuild). B6.2 🔨 **Re-upload staged, swap is an operator step.** Downloaded
  to `~/models/gemma4-e4b-qat/staging-hf-20260717/`, sha256 verified against the HF LFS oids
  (df0fd4ee… / 423074e5…). Diffed with the gguf-py reader: **all 666 + 49 tensors
  byte-identical, tokenizer identical; the ONLY change is `tokenizer.chat_template`** (16804 →
  18808 chars) — `null` tool arguments render as `null`; pre-serialised JSON-string tool
  arguments no longer double-wrap; `image_url`/`input_audio` content parts; `messages and …`
  guards on empty histories; O(1) continuation tracking; a `<|channel>thought` opener after a
  tool response when thinking is enabled. The live server uses the embedded template
  (`--jinja`), so this is a prompt-format change on the tool-calling path and must be
  replay-gated. 🧑 Swap (both files, keep the old ones beside them) — run as ONE script: it
  refuses to start on a partial earlier attempt (a leftover `.pre-hf-20260717` backup would
  make a bare `mv -n` skip silently and leave the pair at mixed versions), restores BOTH
  files if anything fails mid-way (so the production names never point at a partial or
  unverified pair), and verifies the installed pair with `sha256sum -c` BEFORE the restart:
  ```
  set -eu; cd ~/models/gemma4-e4b-qat
  A=gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf; B=mtp-gemma-4-E4B-it.gguf; BK=pre-hf-20260717; ST=staging-hf-20260717
  for f in $A $B; do
    [ -e "$f.$BK" ] && { echo "STOP: $f.$BK exists — partial earlier attempt, inspect first"; exit 1; }
    [ -e "$ST/$f" ] || { echo "STOP: staged $ST/$f missing"; exit 1; }
  done
  restore() {  # put the previous pair back; park whatever was moved in as *.failed
    for f in $A $B; do
      if [ -e "$f.$BK" ]; then [ -e "$f" ] && mv -f "$f" "$ST/$f.failed"; mv "$f.$BK" "$f"; fi
    done
    echo "RESTORED the previous pair — verify with the PREVIOUS hashes below before any restart"
  }
  trap restore ERR
  mv "$A" "$A.$BK"; mv "$ST/$A" "$A"; mv "$B" "$B.$BK"; mv "$ST/$B" "$B"
  sha256sum -c <<'SUMS'
  df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3  gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf
  423074e537504b4f9ec5eafed5c639fac82c96631626efccacdd3c4039b20605  mtp-gemma-4-E4B-it.gguf
  SUMS
  trap - ERR; echo "SWAP OK — restart llama-server now"
  ```
  Any failing `mv` or a hash mismatch triggers `restore` (the running server keeps its
  already-open files either way; nothing changes until the restart). Then
  `systemctl --user restart llama-server` (health probe waits for model + draft), then
  `systemctl --user start zoe-voice-regression.service` and read
  `~/.cache/zoe/voice_regression_last.json`. **Rollback after a failed gate** (reverse both
  files, then verify against the PREVIOUS pair's hashes, measured on the box 2026-09-26,
  then restart):
  ```
  set -eu; cd ~/models/gemma4-e4b-qat; BK=pre-hf-20260717; ST=staging-hf-20260717
  for f in gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf mtp-gemma-4-E4B-it.gguf; do   # check BOTH backups first:
    [ -e "$f.$BK" ] || { echo "STOP: $f.$BK missing — already rolled back, or never swapped; nothing moved"; exit 1; }
  done
  for f in gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf mtp-gemma-4-E4B-it.gguf; do mv "$f" "$ST/$f"; mv "$f.$BK" "$f"; done
  sha256sum -c <<'SUMS'
  b3052f962d6449b4eb2075733c068bdec1c51eadb7b237e6c3157bfbb7b1dae0  gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf
  b0005dc39d47ede950c3ec413cb20e832f15b216126eae368d9f572676153cb6  mtp-gemma-4-E4B-it.gguf
  SUMS
  systemctl --user restart llama-server
  ``` B6.3 ⬜ Domain-prefixed tool
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

### B9 — Omi wearable: a roaming, consented microphone (W6 delivery vehicle) — 🔨 plan merged (#1683)
Plan: [`omi-integration-plan.md`](omi-integration-plan.md) (two review rounds: every phase
replay-gated, no transcript text in shadow mode, a real multi-speaker detector, storage-off
firmware mandatory, scoped rows incl. the existing panel writer, "same eligible speaker" rule;
§6.1 lists what is still unverified). Pendant → BLE → the Pi
panel (later a Pi Zero 2 W dock) → Opus decode + Silero → existing `/api/voice/ambient` →
owner-only, speaker-gated `ambient_memory`. Nothing to Omi's cloud; the phone app is out
(one bonded central only; their backend needs Firebase/GCS/Pinecone/OpenAI; their own
maintainers call their speaker-ID "not reliable enough to trust"). The consumer pendant is an
nRF5340 with an SD ring buffer that records whenever powered — a retention Zoe cannot gate
from outside, hence B9.0.
- B9.0 🧑 **Consent posture decisions**: (a) firmware variant `omi-zoe` with offline SD storage
  OFF vs stock + `RING_CLEAR` on connect; (b) default retention window (proposal 7 d); (c) which
  household members may opt into ambient; (d) legal sanity check of the discard-unknown rule
  under the WA Surveillance Devices Act 1998 s5/s9 for guests. Gate: written answers in the
  plan's §8 before B9.4.
- B9.1 🔨 **Lab receive** (`labs/omi-receiver/omi_bridge.py`, bleak + opuslib, off-Orin): connect,
  decode, 10-min WAV, reconnect-on-drop. Code + fixture tests + manual protocol: #1693 (draft;
  the pendant has not been run yet — every gate number is still hardware-only). Gate: <1 % packet gaps at 3 m/one wall; Moonshine WER on
  20 corpus sentences ≤ panel + 5 pts; battery drop/h logged.
- B9.2 ⬜ **Bridge thread in the Pi daemon, flag-dark** (`OMI_BRIDGE_ENABLED`,
  `ZOE_AMBIENT_OMI_ENABLED`, both off): `source="omi"`, `device_id`, `speaker_id`, `expires_at`
  on `ambient_memory` (one migration). Gate: ci_safe tests (flag off ⇒ no row); replay gate
  PASS with the thread live; Pi RSS +≤120 MB.
- B9.3 ⬜ **Speaker gate in shadow** on the Orin (sherpa-onnx embedder per B4.1; margin rule;
  multi-speaker ⇒ discard). Gate: one shadow week; negative control — another voice alone for
  10 min ⇒ zero owner verdicts.
- B9.4 ⬜ **Physical consent**: long-press session toggle + haptic + LED; per-profile
  `ambient_consent_at`; retention purge in the idle consolidator; firmware per B9.0. Gate:
  stranger test ⇒ no row/file/log text; toggle-off stops posts ≤1 s.
- B9.5 ⬜ **Attributed storage + promotion** (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`): admission gate,
  `[ambient:omi]` citation, rows on the memory page with delete. Gate: `memory_recall_probe`
  unchanged; 20-item "said near Zoe" set ≥80 % attributed recall.
- B9.6 ⬜ Optional offline drain on reconnect (session-gated ring only). B9.7 ⬜ Optional
  push-to-talk via the button (replies on the nearest panel/Telegram; the CV1 has no speaker).
- Not doing: Omi app/backend/webhooks/MCP (cloud), DevKit 2 purchase, diarization in v1.
- Open questions for Jason are in the plan's §8 (which device, is the phone app paired, where
  the pendant lives during the day, retention, minors, who does the legal check).

### B10 — Web lookup + claim backing (Jason, 2026-07-24)
- B10.0 ⬜ **Make the existing chat fallback honest first** (found by the audit re-check
  2026-09-26): `research_evidence.fetch_web_fallback_results` reaches DuckDuckGo with no brain
  tool, but DDG now answers scripted fetches with a challenge page (HTTP 202) and the function
  swallows it and returns `[]`, so a research turn silently degrades to placeholders. Surface
  "nothing found", and prefer the configured Tavily key (`web_search_provider`) when set.
- B10.1 ⬜ Re-land the Python core of the web-search spike (PR #1610, now closed: DDG/Wikipedia/
  HN scrapers, block detection, consensus merge, ≤350-token voice packet; 44 offline fixture
  tests) as a ≤300-line PR behind the approved research/delegation seam (the cut-list cut a
  direct `web_search` tool on purpose); Tavily stays the opt-in primary. B10.2 ⬜ Wire a `web_search`
  tool into the Flue brain lane behind a flag; "are you sure?" triggers a backed re-answer.
  Gate: fixture tests + replay corpus unchanged + 20 live lookups scored by hand.

## 4. Sequencing (dependencies)

```
B0.1 zram ─┬─> nightly gate PASS ─┬─> B0.4 FA rebuild ─> B6.*, B1.11
           │                      ├─> B1.1..B1.10 (each replay-gated)
           │                      └─> B5.1 Kokoro ONNX ─> B5.2/B5.3/B5.5
B0.7 py3.12 venv ─> B0.8 Chroma/MemPalace ─> B3.1 (design against the new store)
B3.2 dream gating ─> B3.3 reflection ─> B2.2 delivery policy ─> B2.1/B2.3/B2.4
B4.3 face decision ─> B4.1/B4.2 shadow week (Pi on) ─> B3.9
B8.1 executor ─> B8.3 watchers (B2.4 can ship on the plain scheduler first)
B0.1 ─> B9.1 ─> B9.2 ─> (B4.1, B4.3) ─> B9.3 ─> B9.0 ─> B9.4 ─> B9.5 ─> B3.9
```

Diagnosed 2026-09-25 (PR #1682): the 44 zero-effect nightly digests were an **idle
household**, not a bug — no owned user turn since 09-03; the alert now distinguishes "idle"
from "processed 0 of N eligible" (B3.2 gains that verdict as its first gate).

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
- 2026-09-26 (pm) — Moonshine 0.1.5 measured + root-caused (decoder-step cost inside the
  0.1.x library), HELD; Flue 2.1.1 PARKED on the sibling-directory contract; deploy
  root-ownership wedge found + fixed; #1693/#1695/#1696/#1698 merged and deployed; bridge
  restarted by hand; ~70 review threads closed across the day's PRs.
- 2026-09-26 — gated Python batch on the box + replay gate PASS #2; B0.6 deps contract
  (this PR); B0.14 ✅ via #1689; B6.2 re-upload diffed (template-only) and staged; B10.0
  (#1691), B0.12 pt 1 (#1695), B1.11 (#1694), B1.10 (#1696) opened by agents; #1692/#1693
  in review-thread rounds; §0 rewritten around the operator window + queue order.
- 2026-09-25 — created from the return-from-absence review; B0.2 done the same day; B1.1
  (#1685), B3.2 first step (#1682), B9 plan (#1683), the feature audit (#1684) and the 1.x
  Telegram-lab retirement (#1681) opened as drafts; B0.14/B0.15 added; §4b triage recorded.
- 2026-09-26 — #1680/#1681/#1682/#1683/#1687 merged; PO-token container recreated on 2.0.0
  and verified; #1684 replaced by the redacted #1687; #1686 replaced by the clean #1689; B9
  linked; B10.0 added (DDG challenge page); §0 refreshed.

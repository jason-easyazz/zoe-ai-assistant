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
> 🧑 operator step (needs Jason or root on the box) · ⏸ parked · ❌ measured, not a win

## 0. NEXT ACTION (keep this current)

State as of **2026-09-28 02:30 AWST**:
- live = `main` `b409dfe5`, and every deploy is green.
- zoe-data runs on CPython 3.12.13 with uvicorn 0.53.0; `/readyz` is ready and self-recall ok.
- The brain sidecar is on Flue 2.1.1 + hono 4.13.9, restarted 02:26.
- pi is 0.87.1 on the host and in Omnigent.

1. 🧑 **Operator window on the box.** The agent cannot do these steps; each one is a production restart,
   a secret, or a root change:
   - **B0.8 Chroma 1.5 cutover.** PR #1745 has the client, the pins and the format guards, and
     it has **not been executed**. The agent's first window step (stop zoe-data and the four
     timers) was refused by the permission system. The box is unchanged: old palace, venv on
     chromadb 0.6.3 / mempalace 3.3.1. Run
     [`chroma-1-5-migration.md`](../knowledge/chroma-1-5-migration.md) §5 in order. Age note:
     #1745 adopts `mempalace==3.10.0` (14-day rule clears 2026-09-30); Jason's 2026-09-27
     "do them all now, don't worry about the age rule" covers this batch — record the waiver
     in the PR when the window runs, or wait until 09-30.
   - **B0.12 apply.** #1727 is files only. Live is still `0.0.0.0:5432` (`pgvector:pg17` =
     17.10) and `0.0.0.0:8007`. Run the PR's apply sequence: rebuild the bridge, then recreate
     Postgres on the pinned 17.11 digest.
   - **Music Assistant.** Still **2.8.7**. Step 0 of #1723 is applied (bgutil plugin 2.0.0 +
     yt-dlp 2026.8.19, no plugin/server mismatch lines), but the YouTube cookies have rotated
     and the provider does not load. Do the panel re-auth first, then the 2.10.3 re-create per
     `music-ytdlp-js-runtime.md`.
   - **Secrets.** Revoke the Telegram token and vacuum the journal (B0.3). Rotate the Postgres
     password (B0.14).
   - **Needs the panel on.** Make one real `/ws/voice/` turn (the last B0.7 item, and the uvicorn
     0.53 websockets-sansio proof for #1743). (The Pi provisioning helper is NOT deployed on the
     live panel — `scripts/setup/touchscreen/README.md` — so #1741's poll secret applies only
     to panels provisioned in future; re-align those helpers as a set before the next pairing.)
   - **Brain.** Swap in the verified Gemma re-upload (B6.2). Only the template changed, and
     the files are staged and checksummed. Do it in its own window so the brain change stays
     separately attributable, then run one replay gate.
   - **Housekeeping.**
     - Set `ZOE_DEFAULT_MEDIA_PLAYER` and turn `ZOE_MUSIC_DISCOVERY` off (§4b).
     - Move the leftover `modules/zoe-music/` out of the live checkout. It holds a root-owned
       `__pycache__` (retired in #1653), and it makes `test_no_zoe_music_module` red locally.
     - Omnigent Claude re-login **before 2026-10-11** (B0.11).
     - Actions event policy **before 2026-11-02** (B0.10 b).
     - Dismiss the chromadb Dependabot alert (the pin is held until B0.8).
     - L4T 36.5; HA `auth_oidc` 1.2.1.
     - Add the nvm bin to the self-hosted runner's `PATH`; set `vm.page-cluster`.
     - Prune old MemPalace snapshots.
     - `ggshield install --mode global --force` (B0.10 c).
2. **Verify today:** the **07:30 morning brief** is the first run after #1726:
   `grep -E "T(07:[3-5][0-9]:[0-9]{2}\+0800|23:[3-5][0-9]:[0-9]{2}\+0000).*(morning_checkin: users kept|PROACTIVE_SPOKEN)" ~/.zoe-logs/zoe-data.app.log`
   — the app log carries its UTC offset; lines are `+0800` (AWST, `T07:3x`) when the service
   runs with the box's local zone and `+0000` (`T23:3x` of the previous date) when it runs in
   UTC, so the pattern matches both (the trigger can fire anywhere in the 07:30–07:59 window).
   - The brief's own path logs `PROACTIVE_SPOKEN trigger=morning_checkin user=jason …` — that
     line is the proof; the recipient helper's `users kept=N` line precedes it. Do not rely on
     the autopilot `fired for N user(s)` line, which comes from a different path.
   - With the panel off, `outcome=absent` on that PROACTIVE_SPOKEN line still proves the brief
     was created; with the panel on and idle as guest expect `tier=bound_guest` and only the
     generic line spoken; `tier=owner` speaks the full brief.

   Also check the Monday 02:31 dreaming run on the new venv drop-in (B3.2).
3. **Queue state:** the 09-27 wave **landed: 24 PRs**, listed in §6. Still open:
   - #1745: the B0.8 cutover, 🧑.
   - #1715: B5.1 evidence, parked draft.

   Dependabot #1734–#1740 were closed, each with a rationale (B0.10). The ~14-day age rule
   **stays** in `AGENTS.md`; Jason waives it per batch (the 09-27 evening batch
   #1743/#1744/#1746 was waived).

   Voice-scope PRs need a head-bound probe after EVERY `update-branch` (strict mode). Use the
   Kokoro-paused window:
   - `systemctl --user stop kokoro-tts`, run the probe with `--service-dir`, then start Kokoro.
   - Verify `curl http://localhost:10201/health` shows `pipeline_loaded: true` AND
     `device: cuda`.
   - Verify `/readyz` `dependencies.tts` names the `kokoro-sidecar` provider. `tts.ok` alone
     is not enough: it also goes green on the espeak/edge fallback or on a CPU-mode Kokoro.

   The window frees ~2 GB. Hold the other PRs (drop `auto-merge`) while a voice PR lands, or
   it goes behind again.
4. **Next engineering, in order:**
   1. **B7.5** app-connection handoff engine (QR + send-to-phone, music flows first).
   2. **B1.1** flip, once the panel is on (Pi proof → head-bound replay → operator flag-on
      week).
   3. The `samantha_bar` harness.
   4. **B3.2/B3.3** on the new store, after the B0.8 cutover.
   5. Brief-on-arrival (**B2.1**).
   6. A Kokoro venv without scikit-learn/pandas (B6.6 d).
   7. The 24 h `--cache-ram` occupancy measurement (B0.4/B6.6).

## 1. Where Zoe already beats the bar (protect these)

| Capability | Zoe | The bar | Guard |
|---|---|---|---|
| Fully local, offline-capable, nothing leaves the house | ✅ | Apple: personal context on-device but reasoning may go to PCC/Gemini; Google/Amazon: cloud | `test_canonical_invariants.py`; replay gate |
| Per-panel voice + face identity, consented, local | ✅ (flags) | Amazon Omnisense (cloud); Apple: single-user Siri | biometric retention policy |
| Speaks first (spoken morning brief, presence-gated) | 🔨 silent since 08-16 (guest-owned kiosk presence + session-created recipient rule); fix MERGED #1726 (+ `emotional_followup` #1731); first check 2026-09-28 07:30 | Gemini Daily Brief is text; Alexa+ nudges | W2 record; [recipients record](../knowledge/synthetic-users-and-proactive-recipients.md) |
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
| 15 | Household face recognition on a home display (Apple's ~7-inch Siri display, October 2026 per Gurman 2026-09-21) | per-panel face-ID behind flags, no enroll/delete UI | B4.3 → B4.1/B4.2; keep ahead of B7.4 |

## 3. Program items

### B0 — Platform floor (everything else waits on this)
- B0.1 ✅ 2026-09-27 — 8 × 244 MB zram persisted by the operator (`/ 8 /` in
  `nvzramconfig.sh`); idle MemAvailable ≈ 2.5–2.7 GB (was ≈ 1.1 GB). Original recipe, kept
  for a reflash: **zram shrink** (root; with llama-server + kokoro-tts stopped): for each of
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
  errors); Silero VAD file v6.2.1 put in place — **reverted 2026-09-27, it detected no speech
  (see B1.4)**; `ci_safe` lane 442 passed locally; **replay gate
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
- B0.3 🧑 Telegram token rotation (BotFather) + `journalctl --rotate && --vacuum-time=1s`. Still open 2026-09-28.
- B0.4 ✅ **llama.cpp rebuild at b11194** and re-enable `--flash-attn on` +
  `--cache-type-v q8_0` with MTP (upstream fix PR #25148, 2026-06-30). Keep `--fit off`.
  Gate: 20-turn multi-prompt replay under `flock`; RSS/TTFT vs baseline.
  **✅ APPLIED 2026-09-27** (#1709; the live unit also carries B6.6's `--ctx-size 8192`,
  whose template change is #1716): the live `~/.config/systemd/user/llama-server.service` runs
  `%h/llama.cpp-b11194/build-jetson/bin/llama-server` with `--flash-attn on --cache-type-k q8_0
  --cache-type-v q8_0 --parallel 1 --fit off --reasoning off --load-mode mmap+mlock
  --cache-ram 2048`. Brain RSS 7.3 → 5.7 GB; replay median brain ≈ 1.5–1.8 s (was ≈ 2.0+);
  replay **PASS 13/13** after apply. `--parallel 1` is deliberate (#28286, open: draft-MTP +
  parallel > 1 leaks content between slots). `--cache-ram 512` loses prompt-cache hits and 0
  gives ~5 s TTFT — keep 2048. Rollback backup: `~/.cache/zoe/llama-server.service.b9733`.
  ⬜ Follow-ups: a `--ctx-checkpoints` trial; a 24 h `--cache-ram` occupancy measurement.
  After a llama.cpp/docker build, CUDA can fail to allocate (`NvMap … error 12`) with
  MemAvailable high — recovery without root is in [voice-pipeline.md → "Stopping the brain
  does NOT guarantee it restarts"](../knowledge/voice-pipeline.md) (stop Kokoro, restart the
  brain first — `reset-failed` if it crash-looped — then start Kokoro).
  **Build history (2026-09-27, before the apply):** Build at
  `~/llama.cpp-b11194/build-jetson` (`9f70b2cec`; CUDA=ON, arch 87, `GGML_CUDA_FA=ON`,
  `GGML_CUDA_GRAPHS=ON`, NATIVE, Release). Two brain windows, E4B-QAT + MTP, FA on:
  **(A) K q8_0 / V q8_0 → PASS 11/11 scoreable, 0 fail, brain median 1754 ms, 0 error lines;
  (B) K q8_0 / V f16 → PASS 11/11, 1752.5 ms, 0 errors.** Adopted **A** (same latency,
  smaller KV; q8_0/q8_0 is also a default `GGML_CUDA_FA_QUANTS` pair, which settles gate
  item (1) below). Flag renames: `--mlock` → `--load-mode mmap+mlock`, `enable_thinking`
  kwargs → `--reasoning off`. Tracked template + apply/rollback recipe:
  `scripts/setup/systemd/llama-server.service`, [voice-pipeline.md](../knowledge/voice-pipeline.md)
  ("Brain build + flags — B0.4"). ✅ #1716 merged: the tracked template carries
  `--ctx-size 8192`, the same as the live unit (B6.6).
  **Gate items (2) and "keep
  `--fit off`" are satisfied in the template.** It runs `--parallel 1`, because #28286 (open) leaks
  content between concurrent draft-MTP requests with no garbage-token signature, and the live
  unit's `--parallel 2` was exposed to it. Concurrent requests now queue, and the whole ctx
  (8192 since B6.6) belongs to the one slot. `--fit off` is explicit, since b11194 defaults it to `on`. Both are
  pinned in `tests/unit/test_llama_server_unit_flags.py`. They were added after the two windows,
  so the apply-window replay is what measures the exact committed config.
  **2026-09-26 (ecosystem-watch §1):** b11194 ≡ b11178 for this build — 16 commits
  b11178→b11194, none touching CUDA arch 87 / FA / MTP / Gemma / jinja (only cpp-httplib
  0.58.0, #29407); source build stays mandatory (prebuilt arm64 asset is CUDA 13.4).
  **#25522 DROPPED** — every repro is multi-GPU `--split-mode tensor`, N/A on one Orin.
  Inherits #28285 (SM87 MMQ crossover, benchmarked on AGX Orin), #29152 (Gemma 4 FA),
  #28549 (CUDA graph for the MTP draft — ggml's `GGML_CUDA_GRAPHS_DEFAULT` is OFF at b11194,
  confirm the built binary has it on). Gate adds: (1) f16-K / q8_0-V is a MIXED pair vs the
  `GGML_CUDA_FA_QUANTS` default (`q4_0-q4_0;q8_0-q8_0;f16-f16;bf16-bf16`) — verify it lands on a
  compiled FA kernel, else build `-DGGML_CUDA_FA_ALL_QUANTS=ON` or run q8_0/q8_0; (2) keep
  `-np 1` (#28286 draft-MTP cross-slot contamination, fix #29454 open); (3) the replay must
  include multi-tool + thinking turns for #28827 (template re-injects `thinking_text` without
  the leading `\n` → trailing garbage; moot while `enable_thinking=false`, UNVERIFIED on E4B);
  (4) budget the separate MTP CUDA compute arena (#27282 / PR #27489, OOM near the limit).
  Watch #29391 / fix #29467 (b11159+: a slot `bad allocation` under prefix reuse aborts the
  whole server — memory-pressure-triggered). Chat template: llama.cpp uses its own
  `common/jinja` (not minja); every construct in the 2026-07-17 re-upload is supported at
  b11194, so `--jinja` renders; #28511 + #29115 (Gemma 4 typed content / `tool_choice`
  grammar) are in.
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
  **Safe-now train 2026-09-27 — #1722 MERGED** (ecosystem-watch 09-27 §7(a)1; voice-gated because
  both manifests are): psycopg2-binary 2.9.12 → 2.9.13 + prometheus-client 0.25.0 → 0.26.0 in
  both manifests, `validate.yml`'s slim list and `deploy.yml`'s 3.10 fallback list. Proven in a
  throwaway 3.12.13 venv (build + `--check` no drift, ci_safe offline green). **joblib 1.6.0 NOT
  moved:** the pin must equal the router heads' training pin (`labs/setfit-router/requirements.txt`,
  `services/zoe-data/AGENTS.md`) and 1.6.0 adds a `cloudpickle>=3.0` dependency; both heads
  load and predict identically under 1.6.0, so it can ride the sklearn 1.9 re-export PR.
  (Since B6.6(e) the runtime serves numpy exports, so this hold now protects only the
  one-release `ZOE_ROUTER_HEADS_BACKEND=joblib` fallback; after it is removed the pin is a
  plain librosa-transitive pin.)
  **Age-waived batch 2026-09-27 — #1743 MERGED, live.** The deploy refreshed the venv and
  restarted zoe-data at 2026-09-28 01:36 AWST; the venv reports uvicorn 0.53.0, fastembed
  0.8.1, ag-ui-protocol 1.0.0 and livekit-protocol 1.1.27. Jason waived the 14-day rule for
  this batch only.
  - uvicorn 0.49.0 → **0.53.0**. `--ws auto` now resolves to websockets-sansio, and the venv
    build smoke asserts `websockets_sansio_impl`. A real-socket A/B was identical except for
    the deflate window bits (12). Rollback is `--ws websockets`.
  - ag-ui-protocol **1.0.0**: the wire bytes are identical.
  - fastembed **0.8.1**: the router model re-downloads once, and the embeddings are
    bit-identical.
  - livekit-protocol **1.1.27**.

  🧑 Still to do: one real panel `/ws/voice/` turn (B0.7).
- B0.7 ✅ **Python 3.12 venv for zoe-data only** (Kokoro + llama-server stay on 3.10/CUDA 12.6).
  **✅ CUTOVER LIVE 2026-09-27 16:13** — #1706 + #1717 MERGED, drop-in installed; zoe-data's
  MainPID exe is uv CPython 3.12.13 (`~/.zoe/venvs/zoe-data-py312`); `/readyz` ready,
  brain/stt/tts ok, `memory_capture` self-recall ok; replay `--stt remote` with the venv python
  **PASS 13/13** (medians STT 378 / brain 1825 / e2e 1578 ms, relative), VAD stage pass_frac
  0.958; requirements drift 0 problems. Rollback = delete the drop-in + `daemon-reload` +
  restart. 🧑 Remaining: one real `/ws/voice/` panel turn (needs the panel on). Since #1743, that
  turn is also the transport proof for uvicorn 0.53's websockets-sansio (B0.6).
  **PR #1706**: every pin MEASURED to resolve on cp312/aarch64 (`requirements-py312.txt`,
  pins identical to the box wherever a cp312 wheel exists), `scripts/setup/build_py312_venv.sh`
  (uv, idempotent, `--dry-run`/`--check`), runbook `docs/knowledge/python-312-venv-migration.md`
  (drop-in switch, gates, rollback); venv built + import-verified in a worktree, `ci_safe`
  zoe-data lane 6445 passed on 3.12. Blockers solved in-manifest: Resemblyzer needs a
  `--no-deps` phase (webrtcvad sdist fails to import on setuptools 84 → `webrtcvad-wheels`;
  PyPI aarch64 torch is a CUDA-13 bundle → exact CPU wheel); `prometheus-client` +
  `livekit-protocol` were undeclared direct imports. **av 18 is capped by aiortc, not Python.**
  Step-ups (onnxruntime 1.30, websockets 17.1, numpy 2, sklearn 1.9 + head re-export) each
  resolve and follow one at a time, replay-gated. **Cutover PR #1717** (merged): tracked drop-in
  `scripts/setup/systemd/zoe-data.service.d/60-py312-venv.conf` (empty `ExecStart=` reset + the
  template's exact uvicorn args on the venv python); `deploy.yml` reads the interpreter back from
  systemd (`scripts/deploy/zoe_data_python.sh`) — venv → `build_py312_venv.sh --refresh`
  (`--offline` first), 3.10 → the old `pip3 --user` list — and runs Alembic with it, so the
  drop-in alone is the switch and deleting it is the rollback; a both-manifests exact-pin parity
  test keeps the py312 manifest on the box's pins (e.g. `moonshine-voice==0.1.3`, #1714).
  Runbook §8 steps done 2026-09-27 except the real `/ws/voice/` turn (above). Target was
  before 2026-10-31 (3.10 EOL).
  **Two-interpreter split (2026-09-26, §10):** system 3.10 = CUDA consumers from `jp6/cu126`
  (cp310-only: torch, onnxruntime-gpu 1.23/1.24; last upload 2026-04-01) — after EOL frozen
  at ORT 1.23.2, numpy 2.2.6, av 17.1.0, sklearn 1.7.2, websockets 16.1.1 (all dropped cp310
  on PyPI); venv 3.12 = zoe-data (end state ORT 1.30, numpy 2.5, av 18 once aiortc lifts `av<18`, CPU torch
  2.14; #1706's cut 1 holds the box pins). The memory
  store does NOT move with the venv: it carries the hard-held `chromadb==0.6.3` +
  `mempalace==3.3.1` (both py3-none-any; `chroma-hnswlib 0.7.6` ships cp312 aarch64) until
  B0.8's copy migration + reconciliation pass — a 1.5.x client on the live 0.6.x palace is
  the silent drawer-write-drop failure. chromadb 1.5.9 (abi3) is B0.8's target, not B0.7's.
  Needs a per-interpreter `requirements.txt` (two files: #1706) + a voice-gate
  probe re-baseline pointed at the interpreter that runs STT (the probe never installs
  requirements — see `reference_voice_gate_instrument_facts`).
- B0.8 🧑 MemPalace 3.10 + Chroma 1.5.x migration **on a copy** (needs B0.7 ✅); reconcile row
  counts against `export_memory_store.py`; self-recall probe.
  **Recipe (2026-09-27): per-collection rebuild, NOT `mempalace migrate`.** mempalace 3.10's
  `extract_drawers_from_sqlite()` (`migrate.py`) selects every embedding in `chroma.sqlite3`
  with no collection filter and adds them all to a fresh `mempalace_drawers`. Zoe's palace is
  ~98% audit rows (live 2026-09-27: drawers 365, `mempalace_audit` 21,389, plus 9 leftover
  `mempalace_audit_sec_*` test collections with 11 rows). So the tool would push every audit
  summary into recall as a drawer, re-embed ~21k rows, and drop the audit collection. Its happy
  path is worse: it does nothing, and the 1.5 client's first open migrates the 0.6 sysdb in
  place, forward-only. Instead, `scripts/maintenance/chroma_migrate_rehearsal.py run` works on
  a copy only. It rsyncs the segments and takes a SQLite online-backup snapshot, then exports
  each collection straight from the copy's SQLite without loading the 0.6.3 HNSW. In a
  throwaway uv venv (`chromadb==1.5.9`, ORT/numpy/tokenizers pinned to the zoe-data venv's) it
  builds a NEW store:
  - drawers are re-embedded with the same MiniLM (archive SHA `913d7300…` asserted, identical
    in 0.6.3 and 1.5.9)
  - audit rows get `memory_service`'s constant vector
  - the `_sec_` leftovers are skipped and listed
  - the effective HNSW settings are kept: **the live drawers are `l2`, resize 2.0** since the
    09-25 rebuild, not `cosine`. Distances feed the `1/(1+dist)` blend, so the space must not change silently.
  - `config.json` gets `embedding_model: minilm`

  **Rehearsed 2026-09-27 (#1732 MERGED), all 10 proofs PASS**, each in its own subprocess:
  - counts via API and export-SQL, with the HNSW config kept
  - per-id metadata hash, plus a negative control that catches a single mutated row
  - write round-trip on each collection across 3 fresh processes
  - embedding cosine over 200 drawers: min 1.0000, plus a mis-pairing negative control
  - demo-user recall: top-10 identical for 20/20 queries
  - a 0.6.3 client on the migrated copy fails loudly (`KeyError '_type'`)

  Peak RSS was 372 MB (the rebuild) and the whole run took 4.4 min. Runbook, measured table
  and cutover/rollback: [docs/knowledge/chroma-1-5-migration.md](../knowledge/chroma-1-5-migration.md).
  **Remaining = the 🧑 cutover window.** **Cutover PR #1745** is open and prepared:
  - `requirements-py312.txt` moves to `chromadb==1.5.9` + `mempalace==3.10.0`. The 3.10
    manifest keeps 0.6.3 and must never open the palace.
  - zoe-data opens the drawers collection with raw chromadb: one `PersistentClient` per
    directory and one cached MiniLM EF. That measured 0.18–0.27 s per query, against
    0.42–0.89 s uncached.
  - Read-only format guards refuse a client of the wrong major version.

  The cutover was **NOT executed on 2026-09-27 (22:19)**. The agent's first window step (stop
  zoe-data + the four timers) was refused by the permission system. The box is unchanged: old
  palace, venv on 0.6.3 / 3.3.1, every timer active. The operator sequence is runbook §5:
  merge #1745 → lock → stop → `chroma_migrate_rehearsal.py run --date cutover-<date>` (10/10)
  → swap → `build_py312_venv.sh --refresh` → ff → `/readyz` self-recall ok → demo parity +
  tombstones + RSS → replay → re-deploy → re-arm the timers. Rollback is §6.
  mempalace **3.10.0** was uploaded 2026-09-16, so it
  passes the 14-day rule on 2026-09-30. The rehearsal needs no mempalace (its EF's `name()` is
  `"default"`, the same identity the rebuild persists). chromadb 1.5.9 (#6953 legacy `hnsw:`
  keys; cp39-abi3 aarch64) is the target. 3.10's `get_collection()` rejects names other than
  the drawers collection, so audit callers for `_skip_name_check=True`. Fix the
  `requirements.txt` comment (~line 82): the chromadb bound flipped at mempalace **3.4.0**
  (2026-06-06), not 3.6.0 (`migrate.py`'s docstring is wrong the same way).
- B0.9 ⬜ APScheduler 3.11.3 via `export_jobs`/`import_jobs` with pytz present; `tzlocal>=3`
  in both workflows. Gate: reminder + autopilot row counts unchanged.
- B0.10 🔨 GitHub review-pipeline housekeeping — split into dated sub-items 2026-09-26
  (ecosystem-watch §9); Greptile to Starter unchanged:
  - (a) ✅ 2026-09-27 — Jason set Copilot review effort to **Lite**. Original item: 🧑 **before 2026-09-28** — Copilot code-review effort = **Lite** (the Default value
    flips to Balanced, ~5× dearer: Lite ≈ $0.05–1 vs Balanced ≈ $0.25–5 of credits per
    review; changelog 2026-08-28). Repo: Settings → Code, planning, and automation → Copilot →
    Code review → "Review effort level" → Lite (not Default). Account: profile → Copilot
    settings → Code review (also the auto-review toggles). `gh pr edit --add-reviewer
    @copilot` passes no effort — which default it takes is UNVERIFIED.
  - (b) 🧑 **before 2026-11-02** — repo Actions **event policy** allowing
    `pull_request_target` for `.github/workflows/voice-gate.yml` +
    `.github/workflows/break-glass.yml`. Measured 2026-09-26:
    `gh api repos/<owner>/<repo>/actions/policies` → `{"total_count":0}` — no policy exists,
    so the default rule (GA changelog 2026-09-17, evaluate mode now) auto-enforces on 11-02
    and both workflows FAIL with `Event 'pull_request_target' is not allowed …` (a failed
    run, not a skip). UI: Settings → Actions → Policies → new rule, type
    `restrict_action_events`, `allowed_events: [pull_request_target, workflow_dispatch]`,
    `include` the two workflow paths, enforcement `evaluate` first → `active`. Both
    workflows also trigger on `workflow_dispatch` (break-glass's manual PR+reason run is its
    documented outage path), and an event rule controls "which events are permitted" — so
    listing only `pull_request_target` would plausibly block manual runs of the included
    paths (docs do not say outright; UNVERIFIED). A community thread says a user-created
    rule enforces immediately, so in `evaluate` confirm BOTH events in the Actions tab
    (`event:pull_request_target`, `event:workflow_dispatch`) before flipping. API: `POST
    /repos/{owner}/{repo}/actions/policies` via `gh api -X POST … --input body.json` (no `gh`
    subcommand; read the REST page for field names); `GET/PUT/DELETE …/policies/{id}`.
  - (c) ✅ `ggshield-action` v1.53.0 → v1.54.0 (#1731) → **v1.55.0 (#1744)** in `validate.yml` (1.55.0 2026-09-24; no
    breaking change to `secret scan ci`, exit codes or `.gitguardian.yaml` in 1.53–1.55) +
    🧑 `ggshield install --mode global --force` on the box: ggshield 1.53 fixed the global hook
    skipping repo-local hooks in git worktrees (all Zoe work is in worktrees) and an existing
    global install only picks the fix up on re-install.
  - (d) 🧑 optional — CodeRabbit (free on public repos; non-blocking, no required check;
    drafts skipped by default): install via coderabbit.ai → Login with GitHub → only this
    repo, plus a minimal `.coderabbit.yaml` (`profile: chill`,
    `request_changes_workflow: false`, `auto_review.drafts: false`). Its inline comments are
    review threads and count toward `required_conversation_resolution`.
  - (e) ⬜ optional review-scoped `.github/copilot-instructions.md` — Copilot review ingests
    `AGENTS.md` wholesale (~600 lines billed per review); a short review-only file bounds it.
  - Review pipeline, 2026-09-26: the **Codex code-review quota was exhausted** mid-day, so the
    cross-vendor pass is unavailable until it refills — fallback for the day's PRs is Greptile
    (label) + Copilot; the deterministic gate is unchanged.
  - (f) ✅ 2026-09-27: Dependabot version updates configured by #1731. npm and pip each get a
    minor-patch group, weekly, limit 2; the rocks and chromadb are ignored.
    - The first batch, #1734–#1740, was closed with a rationale on each. #1734, #1735 and
      #1739 were superseded by #1746, #1744 and #1743. Deliberately held: pi-ai 0.87 in
      brain-2x breaks the tool cap (#1736); the TypeScript 7 and `@types/node` 26 majors;
      tzlocal 5 goes with B0.9.
    - The ~14-day age rule **stays** in `AGENTS.md`. Jason overrules it per batch; the 09-27
      evening batch (#1743, #1744, #1746) was waived.
- B0.11 🔨 Omnigent: bake the `url=` Serena entry into the image (patched live 2026-09-25 in
  `/root/.codex/config.toml`; a container recreate reverts it); renew the Claude login before
  2026-10-11; move the polly lane off `claude-sdk` OAuth (policy). PR #1700 (merged): the file
  lives in the `omnigent-codex` VOLUME with no tracked owner — now a tracked template
  (`modules/omnigent/codex-mcp.toml`) baked into the image and seeded idempotently by
  `entrypoint.sh` on every boot (hooks.state kept); renewal steps + the polly-lane policy note
  recorded in `docs/knowledge/omnigent-container-config.md`. Post-merge: coordinator rebuilds +
  recreates the container (`docker compose ... up -d --build` from `modules/omnigent/`); the
  login renewal and the policy decision remain operator steps. **2026-09-27:** container rebuilt
  on **Node 22.23.3** (`docker exec zoe-omnigent node --version`). After #1746 merged, the
  operator session rebuilt it again: `docker exec zoe-omnigent pi --version` = **0.87.1**
  (2026-09-28 ~02:15). The host-global pi is also 0.87.1.
- B0.12 🔨 HA tool-name sweep (`domain__Tool` prefixes) → HA 2026.9/10 upgrade; adopt the
  MCP `device_id` meta so panel commands resolve to their room. Then MA 2.10 client check.
  Part 1 = PR #1695 (merged): sweep found NO live call site (bridge is pure REST; HA's
  `mcp_server` is not loaded on 2026.5.2); `ha_tool_names.py` + `GET /tools/names` centralise
  the spelling with `/api/config` version detection; runbook
  `docs/knowledge/ha-2026-9-upgrade-runbook.md`. Part 2 = the stepped upgrade (operator) then
  MCP-server + `device_id` adoption. **Pre-flight (2026-09-26, §6/§7):** latest HA is
  2026.9.3 (09-18; no 2026.10 beta yet); recorder `SCHEMA_VERSION = 53` on 2026.5.2, 2026.9.3
  and `dev` — no DB migration, rollback-safe on the schema axis; `mcp_server` `require_admin`
  (#180713, lands 2026.10) migrates an EXISTING entry to `require_admin: False`, so Zoe keeps
  access until flipped; `device_id` meta → `LLMContext.device_id` is #182057 (09-13). Check
  the `auth_oidc` v1.2.1 kiosk auto-login path before/after (hass-oidc-auth #422, open
  09-15: `trusted_networks` + `allow_bypass_login` tablets land on `/auth/oidc/welcome` — the
  panel's path). HA 2026.9 removed `VacuumEntityFeature.BATTERY`: any localtuya vacuum with a
  battery DP crashes on load (rospogrigio #2285 open, fix PR #2286 unmerged; xZetsubou master
  fixed 09-05, no tagged release) — if one exists, stop at 2026.8 or move fork. The
  `ToolResult` deprecation (2027.11) has no Zoe impact. MA: pin `2.10.4` (`stable` ≡ same
  digest); bump `music-assistant-client` to 1.5.1 first; probe Sendspin re-pairing
  (aiosendspin 9.1.1, PIN-pairing breaking at 9.0.0), the panel's shairport-sync 5.1 in
  PTP/Automatic mode (support #6243 pattern — pin the streaming mode if silent), and the
  bgutil 2.0.0 localhost bind reachable from MA's namespace (`127.0.0.1:4416`).
  **MA 2.10.3 pt (2026-09-27, #1723 MERGED):** YouTube Music DOWN since 09-25 18:16 — bgutil
  plugin 1.3.1 inside MA vs server 2.0.0 (major mismatch; MA installs the plugin only on
  container CREATE, never on restart) + stale yt-dlp 2026.07.04 + rotated cookies. Pin moved
  to 2.10.3 (`sha256:88587222…`, closes the 3 MA advisories), stage 1 green (deno 2.9.5);
  probe now forces `web_embedded` (`tv` broken upstream) and fails on a PO plugin/server
  major mismatch (red on live, green on a fresh 2.10.3 container). 2.10 moved provider
  credentials to setup-flow `setup_data` (one-way settings migration): the old
  `save_provider` Reconnect is a silent no-op there, first connect errors,
  `get_entries(provider_domain)` + `music/recommendations` are gone. Same PR: zoe-data
  reads MA's version from `/info` and on ≥2.10 drives `config/providers/reconfigure` /
  `setup` + `config/flows/submit` (reconnect in place, first connect, phone form, OAuth);
  empty "for you" shelf + one log line; the 2.8 path unchanged. **Canonical recipe** (🧑,
  live not touched by the agent): step 0 today = in-place `uv pip install` yt-dlp
  2026.8.19 + plugin 2.0.0, `docker restart`, full probe, panel re-auth; then deploy
  zoe-data → stopped store backup → re-create on 2.10.3 → full probe → panel re-auth →
  Sendspin re-pair / "Zoe Panel" AirPlay mode check. Recipe + API table:
  `docs/knowledge/music-ytdlp-js-runtime.md`. **Live 2026-09-28 01:00:**
  - MA is still **2.8.7** (`/info`).
  - Step 0 is applied: plugin 2.0.0 and yt-dlp 2026.8.19, with no mismatch lines in the
    last 2 h.
  - The ytmusic provider still fails to load, because the cookies have rotated.

  🧑 Panel re-auth, then the 2.10.3 re-create.
  **Network hardening (2026-09-27, #1727 MERGED — files only, 🧑 apply pending):** `zoe-database` → `127.0.0.1:5432` + `pgvector/pgvector:0.8.6-pg17@sha256:cf134a76…`
  (PostgreSQL 17.10 → 17.11, ~25 CVEs; minor = same data dir; `vector` 0.8.2 → 0.8.6 update
  scripts are no-ops, no hnsw/ivfflat index exists); `multica-backend` → `zoe-database:5432`
  (was `host.docker.internal`, unreachable once loopback-bound) and re-pinned to the digest it
  actually runs (v0.3.1 — #1562's pin was never deployed); HA bridge → `127.0.0.1:8007`, exact
  pins (starlette 1.6.0, anyio 4.15.1, idna 3.19, click 8.5.0 …) on
  `python:3.11.16-slim-bookworm@sha256`, rebuild required; CD/in-app updater `compose up` now
  `--no-deps` so a deploy cannot recreate Postgres. Guard: `tests/unit/test_compose_loopback_binds.py`
  (`LAN_LEDGER`). Apply sequence in the PR body. Live 2026-09-28: still `0.0.0.0:5432` /
  `0.0.0.0:8007`, image `pgvector/pgvector:pg17` (17.10) — not applied yet.
- B0.13 ⬜ JetPack 7.2.x reflash window — only after B0.7/B0.8 and when the J401 BSP + an
  Orin wheel index exist.

### B1 — Turn-taking that feels like a person (beats GPT-Live locally)
- B1.1 🔨 **Speculative turn-start with a speculation gate** — PR #1685 MERGED (flag-dark
  `ZOE_SPECULATIVE_*`, server-side gate + daemon verdict, 30 tests, break-the-fix controls;
  stays dark until the flip criteria below are met; needs the panel on + a replay
  gate bound to its head — the 2026-09-25 attempt skipped on the 700 MB floor, so it waits
  for B0.1 or a quiet nightly). **2026-09-27 groundwork — #1742 MERGED (flag dark, deployed
  16:54Z):** phase 2 DONE in code — every side effect of a speculative turn waits for the
  verdict (turn-level hold + `execute_intent` / Skybridge / expert funnels + `_spawn_bg`
  queue + the brain-tool intent-dispatch hold; once on commit, dropped on cancel; negative
  controls). The review rounds added two things:
  - a wire-2 turn-id echo. The Flue seam carries ` zoe-spec:<id>`, and the 2.x sidecar strips
    it before the model and echoes `speculative_turn_id` on every dispatch. The brain-tool
    hold is therefore keyed on the originating turn, not the user.
  - fail-closed refusal of an unknown turn id, for example after a zoe-data restart.

  Offline over 1171 corpus recordings at the live 640 ms close: tail 320 →
  **14.3 % cancels (upper bound), 320 ms median saving**; 400 → 9.3 % / 240 ms; 480 →
  5.0 % / 160 ms; 560 → 2.3 % / 80 ms; 640 inert. Smart Turn veto: NOT a win (−1 pt cancels,
  −70–100 ms mean saving/turn). Moonshine `vad_threshold`: keep default (4 of 6 always-EMPTY
  clips are empty even with VAD off; at most 1 command-shaped recovery at 0.0, +0.7 s wall;
  engine output not deterministic across loads). Remaining before the flip (next, once the panel is on): Pi proof, head-bound
  replay PASS, RAM flat, operator flag-on week (< 30 % live cancels, ≥ 250 ms median saving,
  zero double-speak / duplicate writes). Original plan: fire the brain on Smart Turn's
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
- B1.4 🔨 **Smart Turn v3.2 + Silero v6.2 file** (drop-ins) and **backchannels**: reuse the
  Smart Turn "incomplete" score to play a soft "mm-hm" after ≥0.7 s of speech, 2.5 s
  cooldown. Add "interruptions per conversation" and "silence before first audio" to the
  replay harness.
  **2026-09-27 — v6.2.1 file REVERTED; VAD now replay-gated.** The v6.2.1 export put in place
  on 09-26 (B0.2) loaded cleanly but scored ~0.001 on real speech (0/12 clips ≥ 0.5 vs 12/12 on
  v6.0): barge-in / idle listening were silently off for a day. Live file restored to **v6.0**
  (md5 `00bdd414…`; the bad one kept as `silero_vad.onnx.v6.2.1-INCOMPATIBLE-20260927`).
  The incident silently killed barge-in for ~1 day and was caught only by a manual probe;
  **#1713 MERGED** adds the probe's `--vad-check` stage + the voice-gate check, so a dead VAD
  now FAILS replay **when the stage is scored** — it returns `skip` (no opinion, gate stays
  green) if the model file is absent, fewer usable 16 k clips than the minimum, or
  MemAvailable < 400 MB; read the artifact's `vad` block, not just the verdict. ✅ Nit: `voice_vad.py` docstring said "v5" — fixed with the loader fix below.
  **2026-09-27 (late) — CORRECTION: v6.2.1 was NOT incompatible; our loader was**
  (**#1721 MERGED**). `voice_vad.py` fed bare 512-sample hops; upstream
  `OnnxWrapper` prepends the previous 64 samples (576-sample input) and v6 models are
  calibrated for it. With the context: v6.2.1 42/44 stride clips (was 0/44), probe stage
  20/24 → pass; live v6.0 corpus-wide 95.9 % > 0.5 (was 94.0 %), median detection lag after
  energy onset 0 ms (was 128 ms), noise floor 0.114 (was 0.434); probe stage newest-24 moved
  23/24 → 19/24 (quiet recent captures; floor 60 % unchanged) — **VAD-stage numbers
  re-baselined by #1721** (post-deploy replay VAD stage 0.792). Does not move live panel latency (the Pi daemon's torch.hub wrapper
  already handles context; `voice_vad.py` serves the dormant LiveKit lane + the probe).
  **v6.0 stays live.** ⬜ Follow-up: A/B v6.0 vs v6.2.x on TTS-echo/noise false triggers
  (+ onset) at the barge threshold, re-check the barge knobs and the curator's 0.20
  non-speech line/quarantine (all tuned on the context-less loader) —
  [voice-pipeline.md → The VAD stage](../knowledge/voice-pipeline.md).
  Permanent gate: `voice_regression_probe.py` VAD stage (real `voice_vad` over the newest 24
  clips, FAIL < 60 %, also on the memory-skip path) + `voice_gate_check.py` `vad` block +
  `voice_vad.py`/`voice_turn.py`/`*silero*` in `VOICE_PATH_PATTERNS` — [voice-pipeline.md →
  The VAD stage](../knowledge/voice-pipeline.md), runbook §8. **Any future Silero file = run
  the stage against it (`ZOE_SILERO_VAD_MODEL=<candidate>`) before the swap.** The v6.2.x
  streaming file now passes that bar (with the fixed loader) but stays ⬜ until the
  false-trigger A/B above; the paragraph below predates the revert.
  **2026-09-26 (§12; wording per #1705):** the Silero v6.2.1 file is already in place (B0.2).
  Silero v6.2.2's `silero_vad_16k_sequence.onnx` (GIL-releasing, `sequence=True`) is an
  OFFLINE whole-utterance graph — **NOT a live drop-in**: `services/zoe-data/voice_vad.py`
  runs the streaming model in 512-sample hops with a `(2,1,128)` recurrent state, and a file
  swap would NOT fall back to RMS (RMS is chosen only when the model fails to load; a model
  that loads but rejects streaming inputs makes `process_hops` swallow the error and report
  no speech — a silent failure). Live VAD stays on the streaming model (v6.0 since the
  09-27 revert); evaluate the sequence
  model for the replay/lab path only (whole clip in hand). Validation for ANY live VAD change
  = the probe's VAD stage (above) + the VAD unit lanes (`test_livekit_vad_segmentation.py`,
  `test_voice_barge_in.py`) + a live barge-in count on the panel with Kokoro playing (B1.3
  metric) — the replay itself starts at STT, and the VAD stage scores detection, not
  barge-in behaviour. New lab item (small, B1/B5): **Parakeet
  Redux** (Moondream 2026-09-22; 178 MB / 149M ternary encoder, CPU, streaming, CC-BY-4.0)
  WER + per-file ms vs Moonshine 0.0.62 on the replay corpus — a benchmark, not a rock swap.
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
- B1.10 ✅ 0.1.3 ADOPTED 2026-09-27 — `moonshine-voice==0.1.3` live on the box (bundle
  `quantized_26_07_30`) and pinned: replays 13/13 OK / 7 EMPTY (in-process keyterms off 326 ms,
  on 351 ms, remote live 357 ms); engine A/B 299/306 vs 0.0.62's 302/328 ms median, decoder
  step 23–25 ms. Keyterms are available: the operator sets `ZOE_MOONSHINE_KEYTERMS` in the live
  `.env` (never committed), verified by `/readyz` `keyterms.applied`. Runbook §10. The 0.1.5
  history follows. 0.1.5 ⏸ HELD 2026-09-26 — Moonshine 0.1.5 passes said-vs-did (13/13 OK in-process off/on
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
  on the next release with the same engine-only A/B. 2026-09-26 (§2): upstream is silent since
  0.1.5 (zero commits, no perf issue filed by anyone); watch moonshine #229 (shared Silero VAD
  across concurrent streams); the issue draft is in ecosystem-watch §2.
  2026-09-27 (runbook §9): cause found — 0.1.5 hard-codes `DisableCpuMemArena` (~34 ms/step)
  + `disable_prepacking` (~6 ms/step) on the streaming sessions (upstream `4a7f85c`); no option
  or env var reverts them; best config-only mitigation (glibc `MALLOC_*`) is still ~30 % slower
  per step, so HOLD stands (decoder step 63 ms vs 21 ms; runbook §8–§10). The 0.1.3 A/B is
  done (adopted, above). Next: 🧑 the upstream issue naming the flags — Jason's call.
- B1.11 ✅ LIVE 2026-09-27 (#1694 MERGED, landed in place per operator decision (b)) — Flue 2.1.1 (`@flue/*`
  2.0.1 → 2.1.1 in both 2x sidecars; `pi-ai` held at 0.83.0; `hono` pinned 4.13.7, the newest
  release ≥14 days old — 4.13.8/4.13.9 are too young; nanoid advisories cleared; `npm audit
  --omit=dev` 0 in both trees; 231/231 brain + 44/44 telegram tests, typecheck, build and both
  built smokes green on the merged tree; store format unchanged, one fold-checkpoint re-fold on
  first start). `labs/AGENTS.md` amended the same day: in-place PATCH/MINOR bumps of the two
  auto-deployed Flue trees are allowed with head-bound parallel-port proof + the land-time
  voice-gate replay; majors still take route (a), sibling + cutover. **PR #1694**: the
  final-head build ran as a second sidecar on :3580 with an isolated `ZOE_BRAIN_DB` store and
  the head-bound replay (`--service-dir` on the PR worktree, remote STT) PASSED — sha, OK count,
  medians and VAD in the PR's evidence comment (a commit cannot carry numbers measured on
  itself). It landed with the head-bound voice-gate replay. Follow-on bumps:
  - hono 4.13.7 (#1719, which also lets `zoe_core_client` accept pi ≥0.84's `toolcall_start`
    shape), then 4.13.9 (#1744, age-waived; live in both sidecars, and the brain restarted
    by deploy at 02:26, `/health` ok).
  - pi-coding-agent 0.82.1 → 0.85.1 (#1720) → **0.87.1** (#1746, age-waived; undici 8.10.2).
    It moves at all three pin sites: zoe-core, the Omnigent Dockerfile and
    `pi_runtime_probe`. **Live 2026-09-28:** the host-global pi under nvm 22.22.0 is 0.87.1,
    and so is `zoe-omnigent`. Core-lane pi workers that are already running pick it up on
    their next recycle.
  - `pi-ai` stays **0.83.0** in the 2x sidecar. Dependabot #1736 was closed because 0.87
    breaks the cap (below).
  - Still open upstream: brace-expansion 5.0.9 inside pi's shrinkwrap. The fix needs a pi
    release.

  Next: 2.2.0 for the llama.cpp tool-call fixes, only after the port below.
  **Flue 2.2.0 is NOT drop-in (2026-09-26, §4):** it exists only as `2.2.0-next.1` on npm
  (2026-09-25; no 2.1.2, no 2.2.0 final) and bumps Pi to 0.87.1. Pi ≥0.86 changes the
  `ProviderStreams` input from `Context` to `TranscriptContext` (system prompt + tools travel
  in the leading system message). Zoe's `capped-completions.ts` sets `tools: []` to enforce
  the iteration cap and `context-window.ts:189-190 / 259-260` read `context.systemPrompt` /
  `context.tools` for the budget — under 0.86+ the cap becomes a **silent no-op** and the
  budget reads `undefined`. Port to `getCurrentSystemPrompt()` / `getCurrentTools()` plus a
  tools-removed system message and re-verify with a cap negative control (the cap must still
  trip) BEFORE any 2.2.0 bump. Only #9816 (0.87.0: no strict tool schemas for endpoints that
  do not advertise them) is a Flue-reachable llama.cpp fix; #8275 (thinking budget) came in
  0.84.3; #9528 (`enable_thinking`) is CLI-side. 2.1.1 already swapped the `flue.tool.call.*`
  trace attrs for `gen_ai.tool.call.*` (check trace consumers). Pins are
  `@earendil-works/pi-ai` 0.83.0 (2x sidecar) / `pi-coding-agent` 0.87.1 (`zoe-core`, since #1746), not
  `@mariozechner/*` (dead scope, last publish 0.73.1).

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
- B3.1 🔨 **Bi-temporal supersession + "keep the richer fact"** at idle — lab spike in
  PR #1692 (merged) (flag-dark `ZOE_BITEMPORAL_SUPERSEDE`, no prod wiring): `valid_from /
  valid_until / expired_at / superseded_by` on the fact rows (Chroma metadata keys) and
  `person_relationships` (Alembic plan, not applied); contradiction only if intervals
  overlap (Graphiti `edge_operations.py`); reconciliation with mem0's update prompt,
  top-10 neighbours, integer ids. Fixes the distilled-vs-richer dedupe bug (M7/M9)
  without deleting anything. Lab: 50-pair SYNTHETIC fixture scores 1.00 with the fake
  judge; controls without the overlap rule / richer rule fail 0/10 historical, 4/8
  richer. Design + gates: `b3-1-bitemporal-supersession.md`. Next: real-brain judge run,
  flip the two `test_live_dedup.py` strict-xfails, idle pass behind B3.2's gates.
- B3.2 🔨 **Deterministic dream gating** for the idle consolidator (first step MERGED as #1682:
  the alert distinguishes idle / no-history / extractor vs processing errors, one cutoff for
  selection and probe, the emotional pass survives a fact-parse failure, consolidation has its
  own verdict vocabulary, counts on the status endpoint) (≥N new facts, ≥H hours,
  idle ≥M min, cancel on speech) + a hard brain-call budget per window + a 40-line profile
  cap (Honcho, Memobase). Explains the 44 zero-effect digests; make them explainable.
  ⬜ **Dreaming-cycle scheduling (checked 2026-09-27):** an agent reported the weekly
  dreaming/portrait cycle unscheduled; the box contradicts that — the `--user` timer
  `zoe-dreaming.timer` (02:30 AWST nightly) runs `scripts/maintenance/zoe-nightly-dreaming.py`
  → `run_dreaming_for_all`, whose weekly phases (consolidation, synthesis, portrait, agent sync)
  gate on a **UTC** Sunday = the Monday 02:30 AWST run; last weekly pass 2026-09-21 (portrait
  `ok` for jason, 66 memories). Follow-ups: ✅ 2026-09-27 17:48, the operator installed the
  drop-in for `zoe-dreaming.service`. It is a box-only unit with no repo template under
  `scripts/setup/systemd/`, and it used to run `/usr/bin/python3` (3.10). The drop-in is
  `~/.config/systemd/user/zoe-dreaming.service.d/60-py312-venv.conf`, overriding
  `ExecStart` to `%h/.zoe/venvs/zoe-data-py312/bin/python
  /home/zoe/assistant/scripts/maintenance/zoe-nightly-dreaming.py` + `daemon-reload`; imports
  (chromadb, db_pool, memory_digest) verified to resolve under the venv 2026-09-27; verify the
  Mon 2026-09-28 02:31 AWST run log. ✅ It also iterated ~24 users incl. test/probe ids: now filtered by `user_filters.is_synthetic_user` (dreaming, consolidation, music, portrait, proactive triggers; `ZOE_SYNTHETIC_USER_ALLOWLIST`), and the leaking probe chat sessions are purged nightly (#1726 MERGED; `emotional_followup` joined in #1731; [record](../knowledge/synthetic-users-and-proactive-recipients.md)).
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
  Multi-speaker-detector candidate for the B9.3 shadow week (2026-09-26, §12): NVIDIA
  **Nemotron 3 Diarization** (09-23; ~100M params, streaming/offline, up to 8 speakers,
  320 ms labels; ONNX/GGUF ports 09-23/24) — not on the hot path.
- B4.2 ⬜ Speaker-gated wake word (openWakeWord custom verifier) or a purpose-trained
  "Hey Zoe" (2026 trainers).
- B4.3 🧑 Decide `ZOE_FACE_ID_ENABLED` (on, against the retention policy) → build the
  face enroll/delete UI (ZOE-6129) or turn it off. More urgent, not less (2026-09-26): Apple's
  October home display ships household face recognition (§2 row 15) — keep B4.3 ahead of B7.4.
- B4.4 ⬜ Dual-channel mic (processed for wake/STT, raw for voice-ID) on one panel (VPE).
- B4.5 ⬜ Presence hardware: Raspberry Pi AI Camera (IMX500) person-detect stream as a
  zero-CPU presence signal; HA device presence (Phase 2.5).

### B5 — Voice quality and expressiveness
- B5.1 ❌ ⏸ **Kokoro on ONNX Runtime CUDA — NOT A WIN, parked 2026-09-27** (evidence in
  draft PR #1715): fp16 on GPU saves only ~0.5–1 GB (not the hoped 1.3–1.7), is slower on new
  text and returns silent NaN output on 9 % of runs; CPU RTF 0.70. **Keep PyTorch CUDA Kokoro.**
  Original goal (same model, same voices): 2.3 GB → ~0.6–1 GB;
  RTF < 0.3 required; jetson-containers `kokoro-tts-onnx` recipe.
  **Recipe (2026-09-26, §3):** `kokoro-onnx==0.6.1` (2026-08-19; PR #198 re-export with
  `speed`/duration outputs, **fp16 164 MB / int8 114 MB** graphs in release `model-files-v1.1`
  beside the 326 MB fp32) + `onnxruntime_gpu==1.24.0` cp310 aarch64 from
  `https://pypi.jetson-ai-lab.io/jp6/cu126/` (verified 09-26; PyPI ships no aarch64 GPU wheel,
  so it lives on system 3.10 beside llama-server, not in the B0.7 venv). The `[gpu]` extra is
  x86_64-only — install `kokoro-onnx` plain and bring ORT-GPU; pin numpy to the ORT wheel's
  ABI. The jp6/cu126 **1.24.0** wheel is **numpy-2-header-built** (scanner evidence in
  `docs/knowledge/numpy2-jetson-migration.md`), so numpy 2.x satisfies both it and
  kokoro-onnx; the NVIDIA-forum "built on numpy 1.x" trap is about 1.23.0 and predates 1.24.0
  (an actual `import onnxruntime` under numpy 2 is still undone — first step of the window).
  No Kokoro weights since 2025-04 and no published Jetson RAM numbers — the 2.3 GB → ~0.6–1 GB claim is a hypothesis:
  **measure with a CPU-EP control** (same graph on the CPU provider), gate RTF < 0.3 + the
  replay corpus. CPU fallback if the sidecar ever loses CUDA: Moonshine 0.1.5's two-stage
  Kokoro ORT graph (~100–200 MB).
- B5.2 ⬜ Emotion → bounded, auditable prompt modifiers under immutable rules (GLaDOS
  constitution); Gemma emits one expression tag per sentence (OLV convention); a 48-dim
  emotion vector as the shared wire format (Hume schema).
- B5.3 ⬜ Expressive-lane bake-off for W11: Chatterbox Nano/Turbo, Supertonic 3, NeuTTS Air.
- B5.4 ⬜ **Pocket TTS on the Pi panels** (100M, MIT, CPU) for local acks/toasts when the
  Orin is busy or the brain is stopped. Still the strongest candidate at v3.3.0 (2026-09-24;
  retrained ES/IT/PT/DE, new NL/FR, training code released 08-25).
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
  replay-gated — include multi-tool-call + thinking turns so llama.cpp #28827 (trailing
  garbage from the re-injected `thinking_text`, see B0.4) shows if it reproduces on E4B.
  🧑 Swap (both files, keep the old ones beside them) — run as ONE script: it
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
- B6.5 ✅ Client defaults: `zoe_flue_client` → `:3579`/wire 2 — PR #1701 MERGED (both in-code
  defaults flipped + pinned with the retired `:3578`/wire-1 pair as negative control;
  `ZOE_FLUE_WIRE=1` stays opt-in for parity; voice-scope, head-bound replay gate before ready).
  `ZOE_BRAIN_FAILOVER=1` deliberately NOT flipped — default stays off; its three-step gate
  (failover suite green → replay PASS with the flag exported → live stop-the-sidecar drill
  read from `BRAIN_LANE`) is now spelled out in `services/zoe-data/.env.example` (B1 in the
  register).
- B6.6 ✅ **Resident-memory hygiene** ((a)–(e) landed 2026-09-27; ⬜ follow-ups inline) — measured 2026-09-27
  ([resident-memory-hygiene-2026-09-27.md](../knowledge/resident-memory-hygiene-2026-09-27.md)).
  zoe-data's `import main` loads no heavy library (~64 MB); the ~1.2 GB is the hot-path set
  (Moonshine, router head, fastembed, Chroma). ✅ #1711: cached, CPU-pinned `VoiceEncoder` + a
  fresh-interpreter test that `import main` stays free of resemblyzer/torch/transformers.
  ✅ #1712 MERGED — voice-gated follow-ups: (a) Smart Turn
  features → pure-numpy log-mel (`voice_turn.log_mel_features`), bit-identical to transformers'
  numpy path, so torch/transformers are never loaded (fresh-process RSS 446–455 → 84 MB); old
  vs new on 302 corpus turns: median |Δp| 0, max 0.21, 0 decisions flipped at 0.5 (the model
  is 1-ULP-sensitive on some turns — the same spread as transformers' own torch-vs-numpy
  paths); (b) `/api/voice/enroll` + `/identify` run the speaker embedding in
  `asyncio.to_thread`, so a first enrolment (~6 s) no longer freezes the event loop. Details:
  [voice-pipeline.md](../knowledge/voice-pipeline.md) (Smart Turn section). Music Assistant
  (~1.0 GB RSS+swap, flat): **no action** — no `mem_limit`, no restart timer (re-auth risk);
  ⬜ re-measure RSS+swap in a few days on the same container start, and add a weekly restart
  timer only if it grows > ~100 MB/day.
  (c) **`--ctx-size 8192` APPLIED 2026-09-27 14:13** (p99 prompt observed 3280 tokens; the
  Flue 2.x sidecar windows prompts to 8192); PR #1716 (unit template + Pi compaction + portrait
  token budget + tool-result fitter) **MERGED**. Backup:
  `~/.cache/zoe/llama-server.service.pre-b6-6`. ✅ #1725 fixed the stale `--ctx-size 16384
  --parallel 2` comments in the sidecar (`context-window.ts`, `capped-completions.ts`, README).
  (d) ✅ **Kokoro glibc arena cap APPLIED 2026-09-27 (#1724 MERGED)**. The tracked drop-in
  `scripts/setup/systemd/kokoro-tts.service.d/40-memory-tuning.conf` sets `MALLOC_ARENA_MAX=2` +
  `MALLOC_TRIM_THRESHOLD_=131072`. Measured in a same-age controlled A/B: **−113 to −125 MB anon**
  (VmRSS −73 to −112 MB), with synth p50/p95 within noise over two ABAB rounds. The predicted
  −400 to −800 MB did not happen: the "arena bloat" was mostly live data. Details and table:
  [voice-pipeline.md](../knowledge/voice-pipeline.md) (Kokoro sidecar memory). ⬜ The remaining
  Kokoro RAM lever is a dedicated venv without scikit-learn/pandas (~−100 MB).
  (e) ✅ **Router heads on numpy — no scikit-learn/scipy in zoe-data** (#1730 MERGED, LIVE:
  the running zoe-data maps 0 sklearn/scipy files, checked 2026-09-28; −73 MB). Both stage-1
  heads (logreg 13×384; MLP 384→256 relu→13 softmax) are exported to `.npz` + JSON by
  `scripts/maintenance/export_router_heads.py` and served by `router_heads_numpy.py`
  (`ZOE_ROUTER_HEADS_BACKEND=numpy` default, `joblib` = one-release fallback). Parity vs
  sklearn 1.7.2 `predict_proba`: **max-abs 0.0** (bit-identical) on 1,291 embedded corpus
  utterances + 1,000 unit + 1,000 Gaussian random vectors; negative control (one weight
  +1e-2) goes red. Head load in a fresh capped interpreter: **+72.7 MB / 1.23 s / 815 modules
  → +1.7 MB / 0.012 s / 7 modules**; `import main` unchanged (heads were already lazy). The
  scikit-learn/joblib pins stay (librosa, via Resemblyzer, declares them), held at the
  training pins until the fallback is removed; scipy stays (Resemblyzer imports it).

- B6.6 ✅ **Brain flags tuning** (2026-09-27, two replay-gated brain windows on b11194, one
  flag vs the live set per run, same-session control; evidence in
  `docs/knowledge/brain-flags-tuning-2026-09.md`). **`--ctx-size 16384 → 8192` ADOPTED**
  (#1716 MERGED; live since 2026-09-27 14:13). It saves −170 MiB RSS at
  load, replay PASS. 36 h of live traffic (630 turns): prompt+reply p99 3280 / max 3338. The
  Flue client already windows to 8192. **`--cache-ram` stays 2048**: `0` = +4.1 s TTFT on every
  repeat chat turn (one slot rotates ~5 prompts per turn), and `512` is unprovable because one
  main-turn entry is ~170 MiB with SWA checkpoints. **Draft-MTP stays n-max 4 / p-min 0.6**:
  3/6/8 and 0.5/0.7 are all within ~±3 % noise, and 6/8 are slightly worse, as upstream
  #27210 predicts. **#1728 re-confirmed this with a full 3×3 grid.** It covered n-max {3,4,6}
  × p-min {0.4,0.6,0.8} over 14 runs, and every run passed 13/13. The arms that beat the
  control rode luckier prefix-cache reuse, and decode ms/token stayed flat. So nothing
  changed, and the live unit was restored byte-identical.

  **Router sidecar hardened live (#1728).**
  - `--cache-ram 64`: when unset, the default was 8 GiB inside a 1 GiB cgroup.
  - `--ctx-size 1024`: the longest live request was 87 tokens.
  - The 81-case corpus decisions are identical (91.4 %, 0 chat-FP).

  **Pre-brain latency (#1725).** The prompt-cache misses came from tool-list churn:
  progressive disclosure retracted groups and inserted new ones mid-block.
  - The tool block is now append-only per session.
  - The context loads run concurrently.
  - New `VOICE TIMING` / `FLUE_PROMPT_CACHE` log lines.

  Post-deploy, misses fell from 6–7/14 to 2/14, both first activations of a group. The replay
  brain median is 1476 ms.

  **Lazy memory packet (#1733).** The Flue lane never read the ~500 ms voice memory packet. It
  is now built only on a core/legacy failover hop (`ZOE_VOICE_MEMORY_PACKET_LAZY`, default
  on), which saves ~500 ms per turn and keeps the failover budget. No live panel turn has run
  since, so `packet=skipped` is not yet seen in the app log.

  Follow-up ⬜ `--ctx-checkpoints` (default 32 × ~10.6 MiB per cache entry)
  + a 24 h live cache-occupancy read, before `--cache-ram` is revisited.

### B7 — Window into Zoe (UI)
- B7.1 ⬜ AG-UI 1.0 (`ACTIVITY_SNAPSHOT/DELTA`) + a fixed A2UI-style component catalog as the
  carrier for brain-built cards (never raw HTML from the 4B).
- B7.2 ⬜ Ask-card conversation mode (PR-1a) → retire `voice.html`.
- B7.3 ⬜ Voice-authored automations via HA (Gemini for Home) — later.
- B7.4 ⬜ "Ask about what you see" via the panel camera, one-shot.
- B7.5 ⬜ **NEXT** — **App-connection handoff engine (QR + send-to-phone)** — VISION principle 8 (#1729; Jason,
  2026-09-27): app/account sign-ins show a QR on the panel and finish on the phone, and the
  panel card reflects completion live. Today the music QR (the reference flow) never learns it
  finished, and the token/QR mechanics are copied three times (`music_setup`, `smart_home_setup`,
  `telegram_link`). Plan, in order:
  (a) shared `auth_handoff` backend (`mint` → `pending|opened|completed|failed|expired`,
  QR fetched by id so the token stays out of URLs/logs) + `routers/handoff.py` + one panel
  `authCard` with a countdown and live state from a `handoff_update` ui_action (auto-close +
  toast, replacing the static **Done**); migrate music flows first (YouTube Music, Spotify/Tidal/
  Deezer OAuth, Qobuz form), and point the chat/voice "set up music" reply at the Sources card
  instead of Music Assistant;
  (b) Telegram "send to my phone" button on the card for members with a linked Telegram
  (zero-scan, works with the panel asleep); guests always get the QR;
  (c) QR onboarding for new members — admin taps "Add person" → QR/Telegram carrying the setup
  token → phone page sets password + PIN (replaces the WARNING-log bootstrap token).
  The security fixes on these flows landed separately in **#1741**:
  - pairing needs a member session and a per-attempt poll secret, and uncollected tokens are
    swept;
  - the YT Music viewer is locked per session;
  - `setup_qr.py` issues one-use QR handles, so the setup token is out of query strings.

  Step (a) builds on those handles. #1741's "set up music" reply already points panel chat at
  Music → Browse → Sources.

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
  OFF vs stock + `RING_CLEAR` on connect (= `0x13 CLEAR` on the stock ≥3.0.20 ring
  protocol); (b) default retention window (proposal 7 d); (c) which
  household members may opt into ambient; (d) legal sanity check of the discard-unknown rule
  under the WA Surveillance Devices Act 1998 s5/s9 for guests. Gate: written answers in the
  plan's §8 before B9.4.
- B9.1 🔨 **Lab receive** (`labs/omi-receiver/omi_bridge.py`, bleak + opuslib, off-Orin): connect,
  decode, 10-min WAV, reconnect-on-drop. Code + fixture tests + manual protocol: #1693 (draft;
  the pendant has not been run yet — every gate number is still hardware-only). Gate: <1 % packet gaps at 3 m/one wall; Moonshine WER on
  20 corpus sentences ≤ panel + 5 pts; battery drop/h logged.
  **Protocol facts (2026-09-26, §8, read from `sdks/device/PROTOCOL.md` + the app — the
  docs.omi.me Protocol page is stale, no codec 21):** codec ids **0 = PCM16, 1 = PCM8,
  20 = Opus 160-sample/10 ms (DevKit), 21 = Opus FS320 320-sample/20 ms (CV1)**; **3-byte
  header** (u16 LE packet number + u8 index) then Opus; CV1 = 32 kbps VBR ≈ 4 kB/s of Opus
  (50 pkt/s × ~80 B + 3 B header ≈ 4.2 kB/s on the wire; matches `omi-integration-plan.md`),
  PCM16 mono 16 kHz out; UUIDs as in `omi_bridge.py`. Ring protocol needs firmware
  **≥3.0.20** — 🧑 confirm via DIS `2A26` before relying on it (CV1 config reports 3.0.21;
  `0x10 INFO` / `0x11 READ` / `0x12 ADVANCE` / `0x13 CLEAR` / `0x03 STOP`, big-endian ints,
  444-byte records = 4-byte timestamp + 440 audio; storage UUID UNVERIFIED). No local STT
  anywhere upstream (cloud stack; "Local AI provider" PR #13538 unmerged) — Moonshine stays
  Zoe's job. Firmware 08-27 removed software VAD for T5838 AAD hardware VAD, so the pendant
  may already drop silence: the "<1 % packet gaps" metric must separate AAD-gated silence
  from BLE loss. No GitHub firmware release since v2.0.4 (2024-11); firmware ships by app OTA.
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
- B10.0 ✅ **Make the existing chat fallback honest first** — PR #1691 MERGED
  (found by the audit re-check 2026-09-26): `research_evidence.fetch_web_fallback_results` reaches DuckDuckGo with no brain
  tool, but DDG now answers scripted fetches with a challenge page (HTTP 202) and the function
  swallows it and returns `[]`, so a research turn silently degrades to placeholders. Surface
  "nothing found", and prefer the configured Tavily key (`web_search_provider`) when set.
  #1691: `classify_ddg_response` (challenge = `blocked`, never `no_results`), `fetch_web_fallback`
  → `WebFallbackOutcome` recorded in the package as `web_lookup` + an honest card row,
  `ZOE_WEB_FALLBACK_PROVIDER` (auto = Tavily-first when keyed | duckduckgo | off), one INFO line
  per lookup (query length, never text); mutation-checked negative controls. Voice-gate scope CLEAR.
- B10.1 ✅ **Flag-dark `web_search` brain tool over the B10.0 lookup** — PR #1702 MERGED (dark).
  Not the #1610 spike (DDG/Wikipedia/HN scrapers, consensus merge — no new scraping, no new
  HTTP client): `ZOE_WEB_SEARCH_TOOL=1` (default 0 = byte-identical) serves
  `POST /api/system/web-search` (= `fetch_web_fallback`, ≤5 title/url/snippet rows, outcome
  `status` verbatim, same `require_intent_dispatch_auth` gate as intent-dispatch, query never
  logged) and the Flue sidecar registers a thin `web_search` wrapper under its OWN copy of the
  flag (`optionalZoeTools()`; the 21-tool `zoeTools` set untouched, ungrouped = always
  disclosed). `/api/system/status` `web_lookup.tool_enabled`; capability prose advertises it
  only under the flag AND once the sidecar confirms it on `/health` (B0.14 test flag-aware). Seam:
  the sidecar's tools are static TS `defineTool`s wrapping zoe-data endpoints — there is no
  HTTP tool catalogue. Voice-gate scope VOICE via the three sidecar `src/` files (flag-dark).
  Cut-list item 10 tension stated in the PR (the `research`→`delegate-sync` seam it preferred
  is broken; this is the tracker's B10.2 tool, dark by default). Tavily stays the opt-in
  primary. B10.2 ⬜ now = flip it live: both `.env`s on, 20 live lookups scored by hand,
  "are you sure?" triggers a backed re-answer; then decide whether the `research` seam is
  still needed. Gate: fixture tests + replay corpus unchanged + the 20 scored lookups.

## 4. Sequencing (dependencies)

```
B0.1 zram ─┬─> nightly gate PASS ─┬─> B0.4 FA rebuild ─> B6.*, B1.11
           │                      ├─> B1.1..B1.10 (each replay-gated)
           │                      └─> B5.1 Kokoro ONNX ❌ parked (not a win) — no longer gates anything
           B5.2 (prompt modifiers) / B5.3 (expressive bake-off) ─ independent; B5.5 (audio input) needs a RAM decision without the B5.1 saving
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
| Web-search + claim-backing spike (PR #1610, 62 files, conflicting) | **Re-land small as B10** | Jason asked for live lookups and "are you sure?" backing on 2026-07-24; the valuable part is a few hundred lines of Python. Close #1610, open a ≤300-line PR from its `labs/web-search-spike` core when B1.1 is in review. ✅ Done: #1610 closed; re-landed as B10.0 (#1691) + flag-dark B10.1 (#1702); B10.2 (flip) ⬜. |
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
- 2026-09-27/28 (overnight refresh, state as of 2026-09-28 02:30 AWST) — **the 09-27 wave
  landed: 24 PRs.** In merge order:
  - #1718: tracker.
  - #1716: B6.6 ctx 8192.
  - #1719: hono 4.13.7; pi ≥0.84 `toolcall_start` shape.
  - #1720: pi 0.85.1, three-way pin.
  - #1694: B1.11 Flue 2.1.1, in place. `labs/AGENTS.md` now allows in-place patch/minor bumps
    with parallel-port proof.
  - #1721: VAD 64-sample context. v6.2.1 was not incompatible. Probe re-baselined.
  - #1727: B0.12 loopback Postgres/HA bridge, files only.
  - #1726: proactive household recipients, guest-panel tiered presence, synthetic-user
    filter, purge. This is the root cause and fix for the morning brief that had been silent
    since 08-16.
  - #1722: psycopg2 2.9.13, prometheus-client 0.26.
  - #1723: MA 2.10.3 pin, zoe-data setup-flow port, the YouTube Music incident.
  - #1724: Kokoro arena, −120 MB.
  - #1725: pre-brain latency. Tool-list churn root cause; misses 6–7/14 → 2/14.
  - #1728: router cache-ram/ctx, applied live. MTP A/B: no win, keep 4/0.6.
  - #1729: VISION principle 8; QR/phone app connections; B7.5.
  - #1731: housekeeping. Tests no longer write to the live memory store; ggshield 1.54;
    Dependabot grouping.
  - #1732: B0.8 rehearsal, 10/10 proofs, 372 MB peak, 266 s.
  - #1730: numpy router heads, −73 MB, live.
  - #1733: lazy memory packet, ~−500 ms/turn.
  - #1741: auth hardening. Its deploy failed at migrate.
  - #1747: migration 0029 hotfix; the deploy went green.
  - #1742: B1.1 groundwork, dark.
  - #1743: uvicorn 0.53 sansio, ag-ui 1.0, fastembed 0.8.1, livekit-protocol 1.1.27.
  - #1744: ggshield 1.55, hono 4.13.9.
  - #1746: pi 0.87.1, three-way.

  Box changes:
  - Serena stale-cache fix: ≈1 GB → 141 MB, and the reaper churn stopped.
  - Omnigent rebuilt on Node 22.23.3.
  - The router unit and the dreaming venv drop-in applied.

  Not executed: the B0.8 cutover (#1745). The permission system refused stopping zoe-data;
  it is 🧑 per runbook §5.

  Process:
  - Copilot review effort was set to Lite by Jason.
  - Dependabot #1734–#1740 were closed with a rationale on each.
  - The age rule stays; it is waived per batch.

  §0 was rewritten around the operator queue, and review §9 was refreshed.
- 2026-09-27 (night) — voice memory packet lazy on the Flue lane (draft PR): the live sidecar
  never reads `history` / `db_memory_context` / `portrait`, so the ~500 ms gather measured after
  #1725 is built only on a core/legacy failover hop (`ZOE_VOICE_MEMORY_PACKET_LAZY`, default on;
  `VOICE TIMING … packet=skipped`). Voice path: operator lands with the replay gate.
- 2026-09-27 (night) — B6.6(e) router heads on numpy (draft PR): sklearn/scipy/joblib no
  longer imported by zoe-data; parity 0.0 on corpus + random; head load +72.7 → +1.7 MB.
- 2026-09-27 (night) — B0.8 rehearsal done on a copy. The recipe changed from `mempalace migrate`,
  which would merge ~21k audit rows into drawers and drop the audit collection, to a
  per-collection rebuild (`chroma_migrate_rehearsal.py`). All 10 proofs pass; peak RSS 372 MB;
  4.4 min. Cutover is 🧑 (runbook `chroma-1-5-migration.md`).
- 2026-09-27 (eve) — B7.5 added: app-connection handoff engine (QR + send-to-phone), from VISION principle 8 (panel voice first / touch second / phone for keyboards; app connections via QR).
- 2026-09-27 (eve) — B6.6 (d): Kokoro `MALLOC_ARENA_MAX=2` drop-in applied live. Measured −113 to −125 MB anon, not the predicted −400 to −800 MB, with latency within noise (ABAB).
- 2026-09-27 (eve) — B0.6 safe-now Python train: psycopg2-binary 2.9.13 + prometheus-client
  0.26.0 (both manifests + CI/deploy lists); joblib 1.6.0 held on the router-head training-pin contract.
- 2026-09-27 (eve) — B0.12 network hardening drafted (loopback Postgres + HA bridge, PG 17.11,
  bridge deps patched, `--no-deps` on automated compose ups); operator apply pending.
- 2026-09-27 (late) — B1.4 correction: the Silero "v6.2.1 incompatible" verdict was a loader
  bug — `voice_vad.py` lacked upstream's 64-sample context (fix/vad-64-sample-context); v6.0
  stays live pending a false-trigger A/B; probe VAD-stage numbers re-baseline.
- 2026-09-27 (late) — B1.11 route (b) approved: `labs/AGENTS.md` allows in-place PATCH/MINOR
  bumps of the auto-deployed Flue trees with head-bound parallel-port proof; #1694 merged with
  main (hono 4.13.7 by the 14-day rule), final-head :3580 proof PASS, landing in place.
- 2026-09-27 (pm) — live-reality refresh: B0.1 ✅ (zram 8 × 244 MB persisted); B0.4 ✅ applied
  (b11194, FA on, q8_0 KV, RSS 7.3 → 5.7 GB, replay 13/13); B0.7 ✅ cutover live (#1706 + #1717,
  CPython 3.12.13, replay 13/13); B5.1 ❌ parked (#1715); B6.6 ctx 8192 applied, #1712 merged,
  #1716 landing; B1.4 VAD gate (#1713); B1.10 0.1.5 hold cause recorded; B3.2 dreaming-timer
  check; merged-PR annotations on B1.1/B3.1/B0.11/B6.5/B10.0/B10.1. Process: Codex code-review
  quota exhausted since 2026-09-26 (fallback Greptile + Copilot); voice PRs land strictly
  serially — `~/.cache/zoe/voice_regression_last.json` is one slot bound to `revision.commit`,
  so every push/`update-branch` needs a fresh probe + gate rerun; never probe during a
  `deploy.yml` restart (collision → ERROR verdicts; landing scripts use a `wait_deploy` guard +
  `/tmp/zoe-brain-window.lock`); post-build NvMap error 12 recovery → B0.4 row.
- 2026-09-27 — B0.12 MA pt: YouTube Music outage root-caused (bgutil plugin/server major mismatch), MA pin → 2.10.3 (draft PR), probe fixed + PO-major check; zoe-data MA-version switch (2.10 setup-flow API) in the same PR; operator recipe: step 0 on 2.8.7 today, then deploy → backup → re-create.
- 2026-09-27 — B6.6 follow-ups (draft PR): Smart Turn numpy log-mel (no torch in zoe-data from LiveKit) + speaker embedding off the event loop.
- 2026-09-27 — B1.4: Silero v6.2.1 file reverted to v6.0 (it detected no speech — barge-in /
  idle listening silently off for a day); permanent gate = the probe's VAD stage + the gate
  check's `vad` block + `voice_vad.py`/`voice_turn.py`/`*silero*` voice-path patterns
  (fix/vad-real-model-gate); B0.2's "v6.2.1 in place" annotated; runbook §8.
- 2026-09-27 — B6.6 added: resident-memory audit of zoe-data imports (speaker ID cached + CPU-pinned, import-hygiene test) and Music Assistant (no action, re-measure rule).
- 2026-09-26 (pm, fold) — ecosystem-watch 2026-09-26 (#1703; B1.4 wording per #1705) folded
  into the rows: B0.4 b11194 gate (#25522 dropped), B0.7 two-interpreter split, B0.8 migrate
  recipe + 3.4.0 correction, B0.10 split into dated 🧑 sub-items (Copilot Lite 09-28, Actions
  event policy 11-02, ggshield 1.55 + worktree hook, CodeRabbit optional) + Codex quota note,
  B0.12 HA/MA pre-flight, B1.4 Silero sequence-model correction + Parakeet lab item, B1.10
  upstream silence, B1.11 Flue 2.2.0 not-drop-in, B4.1 Nemotron candidate, B4.3 urgency,
  B5.1 ONNX recipe, B5.4 Pocket v3.3, B6.2 replay scope, B9.0/B9.1 protocol facts, §2 row 15.
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

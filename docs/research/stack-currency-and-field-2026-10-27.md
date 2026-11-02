---
type: research
title: Stack currency, best-practice setup and the field (2026-10-27) - every installed component against its latest, what comparable projects do differently, and what moved in the last fortnight
date: 2026-10-27
status: research-only; nothing installed, built or restarted. Handed to the "Zoe comprehensive review and modernization" session as an upgrade plan with four tiers (security now, one routine batch, lab or replay, hold).
description: Owner ask - "check every package/project we have installed on zoe is set up the best way possible, check that it's running the most recent update, look at other similar projects and learn from them, then look at new developments; the space is moving incredibly fast." Four lanes (inference and speech, app and data and containers and dev tools, the Pi panel, the field since 2026-10-10) over a measured live inventory; the author verified the load-bearing versions against npm, PyPI and GitHub and diffed the llama.cpp history on the box.
evidence: docs/research/stack-currency-and-field-2026-10-27/ (four lane reports with [P]/[S]/[U] tags). Live inventory read 2026-10-27 from the running processes, venvs, containers and the panel over read-only ssh. Author checks - registry versions for 25 components; `git log b11194..b11932` in the llama.cpp clone; live nginx version and config patterns; Starlette versions in both services; Tailscale state on both hosts.
builds_on: docs/research/architecture-deep-dive-2026-10-18.md, docs/research/architecture-deep-dive-2-2026-10-19.md, docs/architecture/self-evolution-nightly-loop.md, docs/CANONICAL.md (the rocks are fixed), AGENTS.md (the 14-day age rule for dependencies)
---

# Stack currency, best-practice setup and the field (2026-10-27)

> The rocks do not change: Gemma 4 E4B-QAT + MTP, Moonshine v2 Medium, Kokoro. Nothing below proposes a swap; several items propose newer builds, runtimes and settings *around* them. Registry-verified versions are marked [R]; lane findings carry the lanes' own tags.

## 0. Answer first

1. **Security now (waive the 14-day rule, one PR):** nginx image 1.31.6 → 1.31.8 [R] (five fixes on 2026-10-14; Zoe's configs do not use the alias-plus-rewrite pattern the worst one needs, so exposure is low, but the bump is routine); Docker 29.1.3 → 29.9.2 [R] (about 30 fixes since, including an authorization-plugin bypass; bounces every container, so schedule it outside the night window); cloudflared from the floating `:latest` to a digest at least 14 days old (2026.10.5 is today's [R]); Python 3.10 reached end of life this month, which touches only the Kokoro sidecar venv on this box.
2. **The biggest single improvement around the brain is a llama.cpp lab build.** The live build b11194 (2026-09-25) is about 740 builds behind b11932 [R]. The diff on the box shows 86 CUDA commits, 68 server commits and 13 speculative-decoding commits, among them a CUDA flash-attention race fix (Zoe runs flash attention on), two SWA prompt-cache fixes (an off-by-two in the reuse threshold and a checkpoint-invalidation bug, both plausible causes of the panel's warm re-prefills), slot pinning and "prefer empty slots", draft state saved with the slot, and `--slot-save-path` as an env flag. Replay-gated lab build, then an A/B of `--parallel 2`: the "never raise parallel with MTP" rule rests on one upstream issue reproduced only on a different model family, closed unresolved by a stale bot. Keep the panel's KV resident is the goal.
3. **Two cheap RAM wins:** torch is in the zoe-data venv (2.14, CPU) only as Resemblyzer's dependency, and Resemblyzer is a 2019-era fallback behind ResNet34; retire it and torch goes (about 360 MB when loaded). On the Pi, torch and torchaudio 2.11 (917 MB on disk, an estimated 300 to 500 MB resident) exist only to load Silero through torch.hub; the ONNX path with the fixed loader removes them. Both are voice-path, replay-gated.
4. **The Silero "incompatibility" was Zoe's own bug, now fixed.** The loader fed bare 512-sample hops where upstream prepends 64 samples of context; fixed 2026-09-27 and pinned by a test. The 6.2.1 export on the box is misnamed "INCOMPATIBLE" and is usable; 6.2.3 [R] changes nothing in the streaming contract. Pending: the echo-and-noise false-trigger A/B, then adopt.
5. **Currency gaps that are worth the lab:** Moonshine 0.1.3 → 0.1.5 [R] (no changelog published, read the diff, replay); the brain sidecar on Flue 2.1.1 while the Telegram bot runs 2.3.0 [R] (2.3.0 kills the process group on abort and fixes a finish-hook regression; breaking: `readSubmissionReply` needs settlements, `durationMs` semantics changed; pi-ai 0.83 → 1.4.0 [R] is a major line); Music Assistant 2.8.7 → 2.10.8 [R] (snapshot the store first; 2.10 retired the local audio provider); LiveKit server 1.9.3 → 1.13.9 or 1.13.10 (1.13.11 is two days old; 1.13.8 regressed egress); Omnigent 0.7.0 → 0.19.1 (twelve minors; 0.19.0 tightened login and permissions, so lab first); fastembed 0.8.1 → 0.9.0 (vector compatibility test).
6. **Routine batch, one PR:** uvicorn 0.54.0, apscheduler 3.11.3 (4.x is still alpha, hold), hono 4.13.14, FastAPI and Starlette at the newest releases at least 14 days old, Home Assistant 2026.10.4, shairport-sync 5.6.2 on the panel (fixes the silence after about 12.5 hours of playback; nqptp first), the panel's pending apt updates (Chromium 155.0.8059.39 is missing the 13 October fixes and runs without a sandbox).
7. **Setup, not versions, is where most of the gain is.** Postgres runs as a superuser app role with no statement or idle-transaction timeout; Chroma runs in-process (a native crash takes the voice API down; isolate it as a server or lab-compare pgvector, which is already in the image and unused); the Pi runs PulseAudio with no echo-cancel module, which is correct because the Jabra does hardware AEC, but has no role-based ducking; the Pi's SD writes are Chromium profile and shader churn (13 GB in 10 days, not the HTTP cache), so the whole profile goes on tmpfs; the panel has no update channel, no health reporting and no rollback, while Home Assistant Voice PE's OTA is the bar; the kiosk trusts a self-signed certificate by flag instead of a household CA in its trust store.
8. **The field, 10 to 27 October:** nothing new around the rocks (no Gemma 4 point release or new E4B QAT, no Moonshine or Kokoro release beyond the above; EmbeddingGemma 2 is worth one A/B against MiniLM on the recall eval). Two papers sharpen the nightly loop: HarnessTTT (validate generated components before running them, keep the best harness across rounds, leave-one-out baselines) and Voice-Light (speculative output never enters history; a Silero timing policy beat a learned end-of-turn model 95.6% to 12.5% recall, with a 758 ms median endpoint-to-first-audio). Security: eleven unpatched Tailscale and Headscale flaws disclosed around 25 October; Tailscale is installed but inactive on both of Zoe's hosts, so nothing live is exposed, but the Perth-trip plan must avoid Tailscale SSH and use deny-by-default ACLs. Apple HomeView ships 13 November with no third-party or intercom detail; Home Assistant 2026.11 replaces HACS with a Marketplace that migrates on restart (back up first) and makes MFA opt-in per auth provider (touches `auth_oidc`); iOS 26 terminates an app that takes a VoIP push without reporting a call (confirms the iPhone-app rule: APNs for notices, CallKit only for real calls).
9. **A process finding that outranks any version:** the four live security items from round 2 (held in the private note on the box) are unchanged a week later. One of them has a merged fix whose container was never recreated, because the deploy workflow never touches the modules compose; the other three have no PR. The round-2 record itself is still an open draft. Thirty-four PRs merged in the same week. Merged is not live, and the queue is optimising for merges.

## 1. Live inventory (measured 2026-10-27) against latest

| Component | Live | Latest [R] unless noted | Action |
|---|---|---|---|
| llama.cpp (brain, router) | b11194 (2026-09-25), two separate server binaries | b11932 | lab build, replay; then `--parallel 2` A/B |
| Gemma 4 E4B QAT + MTP GGUF | Unsloth UD, 2026-07-17 | no newer found [S] | none |
| Kokoro / misaki | 0.9.4 / 0.9.4, torch 2.8 CUDA, py3.10 | 0.9.4 (2025-04) | measure fp16 and lazy load; torch 2.10 cu126 exists for JetPack 6 (fixes a `torch.load` advisory affecting <2.10) |
| Moonshine | moonshine-voice 0.1.3 | 0.1.5 (2026-08-24) | read the diff, replay |
| onnxruntime | 1.23.2 (box), 1.24.4 (Pi) | 1.31.1 [S] | routine on the Pi, replay on the box |
| Silero VAD | v6.0 ONNX live; 6.2.1 export misnamed; Pi pip 6.2.1 | 6.2.3 | A/B then adopt; Pi to the ONNX path (drops torch) |
| openWakeWord | 0.6.0, custom `hey_zoe.onnx` | 0.6.0 (dormant since 2024-02) | keep; train a `custom_verifier_models` on real clips; measure before any replacement |
| Smart Turn | v3.2 | v3.2 | none |
| Speaker ID | ResNet34 INT8 ONNX acting; Resemblyzer fallback (pulls torch into zoe-data) | ResNet34 / CAM++ near-tie | retire Resemblyzer, drop torch |
| JetPack | R36.4.3 (6.2) | 6.2.2 minor; 7.2.1 is a reflash | 6.2.2 next; 7.2.1 only as an A/B on a cloned NVMe |
| Python | 3.10 host and Kokoro venv; 3.12 zoe-data | 3.10 EOL 2026-10 | Kokoro venv to 3.12 when torch wheels allow |
| FastAPI / Starlette / uvicorn / pydantic | 0.143 / 1.7 / 0.53 / 2.14.1 | 0.162 / 1.10 / 0.54 / current | newest at least 14 days old in the routine PR; both services are past the Starlette host-header fix |
| asyncpg / alembic / apscheduler | 0.32 / 1.20 / 3.10.4 | current / current / 3.11.3 (4.x alpha) | apscheduler 3.11.3 routine |
| chromadb / fastembed | 1.5.9 in-process / 0.8.1 | 1.5.9 (no release in 5 months) / 0.9.1 | isolate Chroma or lab pgvector; fastembed 0.9.0 with a vector test |
| livekit python / aiortc / livekit-server | 1.1.20 / 1.15 / v1.9.3 on demand | 1.2.0 / 1.15 / v1.13.11 | server to 1.13.9 or 1.13.10 with a memory cap and pin; python 1.2.0 has an aarch64 wheel, repro the Tegra PeerConnection failure in the lab |
| Postgres | 17.11 (pgvector image 0.8.6, extension not installed) | 17.11; 17.12 on 2026-11-12 | timeouts, app role, `pg_stat_statements` now; 17.12 when out |
| Home Assistant | 2026.10.2 + auth_oidc, hacs, localtuya, zoe_conversation | 2026.10.4; 2026.11 beta imminent | 2026.10.4 routine; 2026.11 checklist (Marketplace migration, MFA opt-in, update entities) |
| Music Assistant | 2.8.7 (idle-reaped) | 2.10.8 | snapshot `~/.zoe/music-assistant`, then 2.10.5+ in the lab, then live |
| cloudflared | `:latest` (2026.9.3 running) | 2026.10.5 | pin a digest at least 14 days old |
| nginx (zoe-ui) | 1.31.6 by digest | 1.31.8 | security-now digest bump in compose, deploy and validate |
| Docker | 29.1.3 | 29.9.2 | security-now, outside the night window |
| Flue runtime (brain / bot) | 2.1.1 / 2.3.0; pi-ai 0.83.0 / 1.2.0 | 2.3.0 / pi-ai 1.4.0 | brain to 2.3.0 with the breaking notes, replay; drop the direct pi-ai pin if only transitive |
| grammy / hono / Node | 1.46.0 / 4.13.13 / 22.23.3 | 1.46.0 / 4.13.14 / 22 LTS | hono routine |
| Omnigent | 0.7.0 (2026-07-27) | 0.19.1 (2026-10-21) | lab from 0.18.0; the login and permission changes matter for the self-evolution skill |
| Serena / codebase-memory / codex / opensrc | 1.7.0 / crash-prone under memory pressure / 0.163.0 / 0.7.2 | Serena 2.1.0 [S]; codebase-memory has a memory-budget setting in newer builds [S] | Serena on a second port first; set the codebase-memory budget |
| Panel OS | Pi OS bookworm, kernel 6.12.34 (June 2025), X11 + openbox, PulseAudio 16.1 | trixie (2026-10-06 images), in-place upgrade unsupported | apt updates now; clean trixie reflash later (venv rebuild, PipeWire decision then) |
| Chromium kiosk | 155.0.8059.39, `--no-sandbox`, `--ignore-certificate-errors` | 155.0.8059.79 (13 Oct security), 156 stable | apt now; household CA in the NSS db, then drop both flags |
| shairport-sync / nqptp | 5.1 (local build) beside the distro 3.3.8 | 5.6.2 (2026-10-25) | nqptp first, then 5.6.2 |
| Pi voice venv | openwakeword 0.6.0, silero-vad 6.2.1, torch 2.11, tflite-runtime 2.14 (retired line), numpy 2.4.4, requests without a Session | see above | drop torch and tflite-runtime; `requests.Session`; pins made reproducible |

## 2. Security now

- nginx digest bump (compose, `deploy.yml`, `validate.yml`; `test_nginx_delivery_layer.py` pins the digest). Zoe's configs hold one `alias` (the developer page) and one `rewrite ... break` (the HA proxy) in different locations, and no `$request_filename`, HTTP/3 or client-certificate verification, so the critical pattern is absent; bump anyway.
- Docker engine 29.1.3 → 29.9.2, scheduled outside 01:50 to 04:55; note containerd lowered the default open-file limit to 1024.
- cloudflared pinned by digest; Postgres 17.12 on 12 November.
- Tailscale and Headscale: inactive here; the Perth-trip design must adopt the hygiene (no Tailscale SSH, deny-by-default ACLs, Headscale 0.29.6, one minor at a time).
- The Kokoro sidecar's torch 2.8 is below the fix version of a `torch.load` advisory; it loads only trusted local weights, so this rides the Kokoro venv move rather than an emergency.
- Chromium on the panel: the pending apt updates, then the sandbox and certificate flags.
- The standing round-2 items (section 0, item 9) still outrank all of the above.

## 3. Inference and speech: best-practice setup around the rocks

- **llama.cpp lab build** of a current tag with the same flags, replay-gated; read `git log b11194..<tag>` for `tools/server`, `ggml-cuda`, `common/speculative` and `src/models/gemma4` (6 commits). Then: `--parallel 2` byte-compared against solo outputs on distinct concurrent prompts; `--slot-save-path` for the panel's session; verify the live `--cache-ram` (the process shows 1024 for the brain and 64 for the router; the template says 2048; the drop-in wins and the unit drift check should say so).
- **Kokoro:** no newer release exists; the 2.3 GB resident is mostly the CUDA context; measure fp16 and lazy module loading; ONNX and TensorRT providers do not help on Orin per the field; move the venv to Python 3.12 with torch 2.10 cu126 when convenient. Since 2026-10-25 Kokoro is the only voice (no cloud fallback), so its availability is now a floor on the Zoe score.
- **Moonshine 0.1.5:** read the diff (streaming partials and speaker id landed in the toolkit in February; the release has no notes), then replay.
- **Endpointing:** the only published peer number on Orin is 700 ms of quiet after 350 ms minimum speech, sentence-streamed TTS; Zoe is at 640 deep / 800 any-quiet. Voice-Light's result (a Silero timing policy beat a learned end-of-turn model) supports finishing the Silero feed fix and the clean-tail flag before any learned endpointer.
- **Speaker ID:** retire Resemblyzer (and torch) from zoe-data; keep ResNet34; thresholds from Zoe's own corpus remain the method; do not adopt the new NVIDIA diarization model (evaluation-only licence).
- **Wake word:** keep openWakeWord 0.6.0; train the per-wakeword verifier on real "hey zoe" clips; the Pi's near-miss ring buffer from round 2 is the negative set; any replacement needs a measured false-accepts-per-hour on the Pi first.
- **JetPack:** 6.2.2 is the sane next step; 7.2.1 (Ubuntu 24.04, CUDA 13.2, Python 3.12) only as an A/B on a cloned NVMe, because it is a reflash plus rebuilds of llama.cpp and the Kokoro wheels.

## 4. App, data, containers and tools: best-practice setup

- **Postgres:** `idle_in_transaction_session_timeout` 30 s, `statement_timeout` 15 s, `lock_timeout` 5 s on the app role, `shared_buffers` 256 MB, `jit` off, a non-superuser app role and a migrator role, `pg_stat_statements` (round-2 D-3).
- **Chroma:** no release in five months; the crash-isolation argument stands. Minimum: run it as a server container; better: a lab comparison of pgvector in the existing image on recall quality and latency, then decide.
- **Flue brain sidecar to 2.3.0:** the gap holds the process-group kill on abort (relevant to the brain's cancel path) and the finish-hook fix; breaking changes listed in section 0; pi-ai 1.x is a major line, so check whether the brain imports it directly. Replay-gated.
- **Home Assistant 2026.11 checklist:** back up before the Marketplace migration replaces HACS on restart; `support_mfa` removal touches `auth_oidc`; legacy `device_tracker` deprecated; update entities gain `has_update`, which the Zoe page can read.
- **Music Assistant 2.10:** snapshot the store; the local audio provider is retired in favour of Sendspin; ytmusic gained cookie-expiry reporting, which is the health signal the music card has been missing.
- **LiveKit server 1.13.9 or 1.13.10:** log redaction, request-size limits, TURN quotas, lower per-packet allocations; pin and cap (round-2 A10).
- **Omnigent 0.18 then 0.19.1 in the lab:** the permission and login tightening is what the self-evolution skill contract wants; confirm an API-key mode before the household Tier 2 design relies on it.
- **codebase-memory:** set the memory budget the newer builds expose, which targets the crashes this session hit; **Serena 2.1.0** on a second port before switching the shared server.
- **Cloudflare Access:** every path except `/manifest.json` and `/sw.js` is behind Access; keep it, pin cloudflared, and add a service token for the Mac panel rather than a shared identity.

## 5. The panel: best-practice setup

- Now, over ssh: the pending apt updates; nqptp then shairport-sync 5.6.2; a ten-minute `fatrace` to confirm the writer, then Chromium's whole `--user-data-dir` on tmpfs; the panel agent posts git sha, throttle bits, temperature, RAM, daemon uptime and CDP health to zoe-data every minute (the first "panel as code" piece); remove tflite-runtime and make the pins reproducible.
- Next: the Active Cooler (owner at the panel; curve via `fan_temp0..3`); drop torch by moving Silero to onnxruntime and keeping ResNet34 ONNX (replay-gated, barge-in VAD is on the voice path); household CA in the NSS db and the OS store, then remove `--ignore-certificate-errors` and `--no-sandbox` (the kiosk runs as `pi`); PulseAudio role-based ducking (AirPlay as `music`, TTS as `phone`) rather than an echo-cancel module on top of the Jabra's hardware AEC; a shared `requests.Session`; a hardware watchdog plus a CDP heartbeat.
- Later: a clean trixie reflash with the PipeWire decision and a Python 3.13 venv; USB SSD or NVMe boot and a read-only root with a data partition; a signed pull updater with health-gated rollback (the release change class of the self-evolution design); the LD2410 mmWave and XVF3800 mic array as measured A/Bs, not purchases.

## 6. The field, 10 to 27 October (newest first, with the piece to borrow)

- **Tailscale and Headscale, eleven unpatched flaws (about 25 Oct):** not live here; it rewrites the trip plan's hygiene. Piece: deny-by-default ACLs, no Tailscale SSH.
- **nginx 1.31.7 and 1.31.8 (14 and 21 Oct), Docker 29.9.x (20 Oct):** section 2.
- **HarnessTTT (arXiv 2610.17370):** a frozen 9B with learned harnesses beats evolutionary baselines. Piece: validate generated components before running them, keep the best harness across rounds, leave-one-out baselines. Skip the RL.
- **Voice-Light (arXiv 2609.20995, code public):** cascaded full-duplex with speculative generation that stays private; cancelled audio never enters history. Piece: both rules into the speculative turn-start and the memory authority.
- **Apple HomeView (ships 13 Nov):** hands-on only; confirms the guest-default pattern and the iPhone app's role. Ignore the hardware.
- **Home Assistant 2026.10.4 and the 2026.11 beta:** Marketplace replaces HACS; MFA opt-in; no voice changes found. **Music Assistant 2.10.8.**
- **Gemma:** EmbeddingGemma 2 (740M, 6 Oct) is the only post-June release; one A/B against MiniLM on the recall eval, probably a no. No Gemma 4 point release, no new E4B QAT, no Jetson news; JetPack 7.2.1 is the platform reset; Orin Nano 2 announced for 2027.
- **Matrix:** eight Matrix 2.0 MSCs in final comment, FluffyChat 2.10 with MatrixRTC calls; keep Matrix text-only for now.
- **Claude Agent SDK:** per-tool MCP permission policy and an effort parameter; builder lane only, relevant to the Omnigent skill contract.
- **Alexa+ free for Prime (22 Oct), Gemini for Home "stop means stop" fix:** the latter is worth replay cases.
- **Nothing new** for Moonshine, Kokoro, Smart Turn, openWakeWord, Letta or mem0 that is load-bearing; GitHub trending has no household companion; Voxtral TTS is non-commercial; a new NVIDIA diarization model is evaluation-only.

## 7. The upgrade plan handed over

**(a) Security now, waive the age rule:** nginx 1.31.8 digest; Docker 29.9.2 outside the night window; cloudflared digest; the panel's apt updates. **Plus the round-2 live items that never happened** (the private note's P0 list: one merged config to apply by recreating its container, two services to lock down, the host firewall ruleset for the owner, one token rotation for the owner, the off-box backup install as owner steps then agent).

**(b) One routine PR:** uvicorn 0.54.0, apscheduler 3.11.3, hono 4.13.14, FastAPI and Starlette at the newest release at least 14 days old, Home Assistant 2026.10.4, shairport-sync 5.6.2 with nqptp on the panel, tflite-runtime removed, a `requests.Session` in the daemon, Postgres timeouts and roles.

**(c) Lab or replay, one at a time:** llama.cpp current tag (then `--parallel 2`, `--slot-save-path`); Resemblyzer and torch out of zoe-data; torch out of the Pi via the Silero ONNX path; Silero 6.2.x echo-and-noise A/B; Moonshine 0.1.5; Flue brain 2.3.0 with the pi-ai question; Music Assistant 2.10.5+ after a store snapshot; LiveKit server 1.13.9/10 pinned and capped; fastembed 0.9.0; Chroma as a server or pgvector lab; Omnigent 0.18 then 0.19.1; Serena 2.1.0 on a second port; Kokoro fp16 and the 3.12 venv; JetPack 6.2.2; the Chromium profile on tmpfs; the household CA on the panel; the panel health push.

**(d) Hold, with the reason:** apscheduler 4 (alpha); Home Assistant 2026.11.0 until .2 (Marketplace migration); Music Assistant 2.11 (beta); anything under 14 days old today (fastapi 0.160 to 0.162, starlette 1.9 and 1.10, fastembed 0.9.1, livekit 1.2.0 and server 1.13.11, cloudflared 2026.10.1 to .5, hono 4.13.14 is 2 days old so it waits too); JetPack 7.2.1 (reflash); a trixie reflash of the panel (owner at the panel, PipeWire decision); any wake-word replacement before a measured false-accept rate exists.

**For the nightly loop design:** fold in HarnessTTT's three rules and Voice-Light's two; the panel health push and the update entities become Zoe-score and Zoe-page inputs.

## 8. Not verified, and limits

- The llama.cpp changelog was diffed by commit title only; no build or run. Omnigent's changelog was read in part; its API-key mode is unconfirmed. codebase-memory's current version and memory-budget flag are secondary. The CVE identifiers for nginx, Docker, torch and transformers were not read at NVD or the vendor pages (several primaries returned 403); the version gaps are registry-verified, the severities are not. Comparable-stack settings (Pipecat, OVOS, linux-voice-assistant) were not found at primary sources. The Pi's SD-write attribution is from process write counters, not `fatrace`. Nothing was installed, built, pulled or restarted.

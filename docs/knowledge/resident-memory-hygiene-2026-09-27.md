---
type: Reference
title: Resident memory hygiene — zoe-data speaker ID + Music Assistant (2026-09-27)
description: Measured import cost of every heavy library zoe-data can load, which of them are actually resident in the live process, why the speaker-ID stack was already lazy (and what the real cost of first use is), the Smart Turn torch landmine, and the Music Assistant 2.8.7 footprint with the reasoned "no restart timer, no mem_limit — re-measure" decision.
tags: [memory, performance, zoe-data, speaker-id, resemblyzer, torch, music-assistant, jetson]
timestamp: 2026-09-27T02:30:00Z
---

# Resident memory hygiene (2026-09-27)

Tracker row: B6.6 in [the program](../architecture/beat-the-bar-2026-program.md).
Earlier profile this builds on: [memory-pressure-profile.md](memory-pressure-profile.md).

## 1. zoe-data — what is resident, and what each library costs

**Method.** Every measurement ran in a fresh interpreter inside
`systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0` (a runaway import is killed,
never swapped onto the brain), under `nice -n 15 unshare -rn`, with `CUDA_VISIBLE_DEVICES=`
where torch was involved. RSS = `/proc/self/status` `VmRSS` before/after the import.
Which libraries the LIVE process holds was read from `/proc/<pid>/maps` (zero cost, no restart).

| import (fresh interpreter) | RSS delta | pulls in | resident in live zoe-data? |
|---|---:|---|---|
| `resemblyzer` | **375 MB** | torch, librosa, scipy | no |
| `torch` (2.8.0, CUDA 12.6 build) | **358 MB** | numpy | no |
| `transformers.WhisperFeatureExtractor` | **360 MB** | **torch** (unconditional in 5.17: `if is_torch_available(): import torch`; `USE_TORCH=0` does not stop it) | no — see §2 |
| `sklearn` | 130 MB | scipy, pandas, pyarrow | was yes (router heads, `joblib.load`) — **no since B6.6(e)**: heads served by `router_heads_numpy.py` |
| `pandas` | 81 MB | pyarrow | was via sklearn only (not mapped in the live py3.12 process, 2026-09-27) |
| `chromadb` / `mempalace.palace` | 70–76 MB | onnxruntime, tokenizers | yes (memory) |
| `fastembed` | 69 MB | onnxruntime, tokenizers, PIL | yes (semantic router) |
| `pyarrow` | 43 MB | numpy | yes (via pandas) |
| `onnxruntime` | 35 MB | numpy | yes (VAD, fastembed, chroma) |
| `moonshine_voice` | 22 MB (+ ~429 MB of `.ort` models once loaded) | — | yes (STT rock) |
| `scipy` / `numpy` | 22 / 20 MB | — | yes |
| `import main` | **~64 MB total** | *none of the above* | — |

**Findings.**

- `import main` loads **no** heavy library at all — numpy, torch, resemblyzer, sklearn and
  the ONNX stack are all already deferred to lifespan warm-up or first use. The ~1.2 GB live
  RSS is the lifespan/first-use set: Moonshine models + ORT arenas, the fastembed router, the
  router heads (live `ZOE_ROUTER_HEAD=active` — every chat turn; numpy since B6.6(e), which
  took the head load from +72.7 MB / 1.23 s / 815 modules to +1.7 MB / 0.012 s), Chroma. Every one of
  those is on a hot or routine path, so none is a lazy-load candidate.
- **Speaker ID was already lazy** — `voice_speaker_id._compute_resemblyzer_embedding`
  imported resemblyzer inside the function — so lazy-loading it saves **0 MB at startup**
  (paired measurement, same env: `import main` 71.1/71.7 MB before vs 71.4/71.6 MB after).
- The real cost is **first use** (server-side embedding runs only on `/api/voice/enroll`
  and on `/api/voice/identify` requests that send raw audio; the Pi daemon always sends a
  precomputed `embedding_base64`): **+568 MB RSS and 6.1 s on the first call, 0.34 s /
  +2 MB on the next**, measured on a real corpus clip. That memory then stays resident
  until zoe-data restarts — importing torch cannot be undone.

**What changed (PR for B6.6).** `voice_speaker_id.py` now builds the `VoiceEncoder` once
behind a double-checked lock and reuses it (previously every call re-read the weights), and
pins it to **CPU**: resemblyzer's default is `cuda if torch.cuda.is_available()`, and this
box's torch is a CUDA build, so an enrolment could open a CUDA context inside zoe-data, on
the unified memory the brain and Kokoro depend on (NvMap sits outside every cgroup guard).
CPU also matches the voice daemon, which computes the same embedding on the Pi's CPU. Import
errors still degrade to `None` (the endpoints' 503) and are not cached. Pinned by
`services/zoe-data/tests/test_speaker_id_lazy_load.py`, including a fresh-interpreter check
that `import main` leaves `resemblyzer`, `torch` and `transformers` out of `sys.modules`.

**Not done here, recommended:**

- ✅ (B6.6 follow-up PR) Both endpoints used to call the embedding **synchronously inside
  `async def` handlers** (`routers/voice_tts.py`), so the first enrolment blocked zoe-data's
  event loop for ~6 s — every live voice turn and WebSocket stalled with it. They now `await
  asyncio.to_thread(_compute_resemblyzer_embedding, wav_path)`; pinned behaviourally by
  `services/zoe-data/tests/test_voice_embedding_off_loop.py`.
- To give the 568 MB back after an enrolment session, run the embedding in a short-lived
  subprocess instead of in-process. Cost: ~6 s per phrase instead of once per process (the
  3-phrase enrol flow has a 60 s client timeout, so it fits). Worth it only if enrolment
  becomes more than a one-off.

## 2. zoe-data — the Smart Turn torch landmine (fixed in the B6.6 follow-up PR)

`voice_turn.SmartTurnDetector` (LiveKit end-of-turn scorer, live `ZOE_SMART_TURN_ENABLED=1`)
imported `transformers.WhisperFeatureExtractor` only to compute an 80×800 log-mel, and in
transformers 5.17 that import drags in **torch: +360 MB** the first time a LiveKit session
scores a turn — resident for the rest of the process. The model itself is ONNX, so the
features are now a pure-numpy log-mel (`voice_turn.log_mel_features`), **bit-identical** to
transformers' numpy path; a fresh process running the detector + one scoring call dropped from
446–455 MB to 84 MB RSS. Parity numbers and the end-of-turn A/B on the corpus are in
[voice-pipeline.md](voice-pipeline.md) (Smart Turn section).

## 3. Music Assistant (`zoe-music-assistant`, MA 2.8.7)

**Measured.** One process (`mass`, PID 1 in the container), container up since
2026-09-24 06:11 UTC (~3 days). Over a 10-minute sample:
RSS 855 → 548 MB while VmSwap rose to 456 MB — **RSS + swap ≈ 0.98–1.0 GB, flat**, and
`VmHWM` 970 MB, so the ~0.9 GB `docker stats` figure is the whole anonymous footprint, and
the kernel is already paging the cold half of it out under pressure. The library is tiny
(`library.db` 588 KB), and MA logs show no memory errors in 72 h. The July profile saw
~357 MB (32 MB RSS + 325 MB swap) — a different uptime, so it cannot tell growth from a
larger working set (YouTube Music + PO-token provider, Sendspin/AirPlay panel speaker were
added since).

**Upstream.** No 2.8.x memory-leak report. The memory complaints are 2.9.0 (larger raw-PCM
buffer + audio analysis; 2.9.1 trimmed idle use and gates heavy features behind a RAM
minimum — [support#5633](https://github.com/music-assistant/support/issues/5633)), a
duplicate web-player stream loop (closed, [#6064](https://github.com/music-assistant/support/issues/6064)),
and a 2.10rc report the maintainers attributed to Home Assistant, not MA
([#6408](https://github.com/music-assistant/support/issues/6408)). Relevant for the next
image bump: 2.9+ uses *more* RAM by design; disable the audio-analysis / smart-fades
providers and set the stream buffer to Minimal.

**Decision: no action.**

- `mem_limit` / `deploy.resources` in `docker-compose.modules.yml` — rejected: a limit
  turns a slow working set into an OOM kill mid-playback, `unless-stopped` restarts it, and
  every restart carries the YouTube Music re-auth risk
  ([music-ytdlp-js-runtime.md](music-ytdlp-js-runtime.md) §Re-auth risk).
- Weekly `docker restart` timer — not justified yet: no measured growth, and each restart
  risks the re-auth (search silently returns nothing until someone scans the QR). The one
  upside: MA detects its streamserver publish IP only at container start, and the box is
  DHCP-dynamic — on 2026-07-13 it kept advertising the old address, so music "played" while
  every player stayed idle (Sonos `ERROR_PLAYBACK_NO_CONTENT`; `docker logs
  zoe-music-assistant | grep "Starting streamserver on"` vs `ip -4 addr`), and a restart
  fixed it. The logs today still show both `.53` and `.218` starts. That is better fixed for
  good by pinning `publish_ip` (MA UI → Settings → Core → Streamserver) or a DHCP
  reservation than by a blind weekly restart.
- The footprint is already mostly paged out under pressure, and the voice stack is guarded
  by `MemorySwapMax=0`, so MA competes for swap, not for the brain's RAM.

**Re-measure (decides whether a timer is ever warranted).**

```bash
docker inspect zoe-music-assistant --format '{{.State.StartedAt}}'
docker exec zoe-music-assistant sh -c 'grep -E "VmRSS|VmSwap|VmHWM" /proc/1/status'
```

Take RSS + VmSwap at least twice, days apart, on the same container start. If it climbs by
more than ~100 MB/day, or passes 1.5 GB, add a weekly restart **user timer** at a quiet
hour (Sunday ~04:00), and pair it with a post-restart `music_jsruntime_probe.sh` plus a
provider-state check so a failed YouTube Music re-auth is announced instead of discovered.
Baseline: 2026-09-27 02:24 UTC, ~3 d uptime → 548 MB RSS + 456 MB swap = ~1.0 GB.

---
type: canonical-declaration
audience: human-first (Jason) + all agents — READ FIRST, with VISION
status: LOCKED — changing anything here is a deliberate act, not a drive-by edit
---
# Zoe — Canonical Systems (the locked-in truth) 🔒

> **What is actually live, and what is settled.** If a system isn't listed here as
> canonical, it is **not load-bearing** — do not extend it, build on it, or resurrect
> it. Retired systems are **removed**, not kept around to distract.
>
> This is the antidote to drift: we kept re-deciding the models and voice because the
> repo never said, in one place, *what was locked*. Now it does. Read this with
> [`VISION.md`](VISION.md) (the why). The rocks below are enforced by a CI test
> (`services/zoe-data/tests/test_canonical_invariants.py`) — you cannot quietly swap one.

## ⚓ The Rocks — settled, do not swap (only optimise *around* them)

These are fixed. They have been re-litigated enough times that they are now **locked**.
Changing one means editing this file **and** the lock-in test, in a PR, on purpose.

| Role | Canonical choice | Where it actually lives |
|---|---|---|
| **Brain (LLM)** | **Gemma 4 E4B-QAT + MTP drafter** | host-native `llama-server` on `:11434` → `~/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (+ `mtp-gemma-4-E4B-it.gguf`) |
| **STT** | **Moonshine v2 Medium** | `services/zoe-data` (`warm_moonshine` warmup on startup) |
| **TTS** | **Kokoro** (PyTorch on CUDA, `ZOE_KOKORO_BACKEND=pytorch`) | `/api/voice/synthesize` waterfall: Kokoro → Edge TTS → espeak-ng. CUDA is load-bearing, not a nicety: the ONNX/CPU backend synthesizes **slower than real time** (RTF ~1.0–1.8x), which starves the sentence-streamed voice pipe and makes replies play back in pieces. CUDA runs at RTF ~0.08x. Costs ~2.3 GB unified memory; `/health` reports `degraded=true` if it ever falls back to CPU. |

<!-- LOCKED-ROCKS: machine-readable; the CI test parses this block. Do not edit casually. -->
```yaml
rocks:
  brain:
    family: "Gemma 4"
    variant: "E4B-QAT"
    drafter: "MTP"
    serving: "host-native llama-server :11434"
  stt:
    name: "Moonshine v2 Medium"
    loader_marker: "warm_moonshine"
  tts:
    name: "Kokoro"
```

## 🟢 Canonical live systems — the spine

The Pi-as-brain path and the services it depends on. These are real and load-bearing.

- **Brain dispatch** — `services/zoe-data/brain_dispatch.py` picks the brain 3 ways,
  priority **flue > core > legacy** (all share the Gemma 4 E4B-QAT + MTP rock on
  host-native `llama-server :11434`):
  - **`flue`** (LIVE on this deployment) — the Flue Pi-Agent sidecar
    `labs/flue-zoe-brain` on `:3578` (systemd user unit, token auth), reached via
    `ZOE_BRAIN_BACKEND=flue`. It reimplements Zoe's persona + ability slot-shapes
    and calls back into zoe-data via `POST /api/system/intent-dispatch`
    (`services/zoe-data/zoe_flue_client.py`). See
    [`architecture/zoe-flue-integration.md`](architecture/zoe-flue-integration.md).
  - **`core`** (shipped default, currently the dormant fallback) —
    **`services/zoe-core`**, the **Pi agent** (TypeScript coding-agent +
    `extensions/*`, `pi --mode rpc` via `services/zoe-data/zoe_core_client.py`).
    Wired + tested — **not retired**; extend it, don't archive it.
  - **`legacy`** — `services/zoe-data/zoe_agent.py`, the last fallback (only when
    `ZOE_BRAIN_BACKEND` is not `flue` AND `ZOE_USE_CORE_BRAIN` is off).
- **`services/zoe-data`** — FastAPI app (`:8000`): voice/chat path, memory router, Skybridge.
- **`zoe-database`** — PostgreSQL (asyncpg, `$1` placeholders). Relational + temporal memory.
- **Chroma / MemPalace** — vector store for memory (raw-first).
- **`llama-server`** (host-native, `:11434`) — serves the brain rock above.
- **`services/zoe-ui`** — the touch/web UI. The **estate** (`dist/touch/home.html`) is the
  panel chrome; the old Skybridge front-end (`skybridge.html` + its JS/CSS) is **retired** (a
  compat redirect stub remains). The server-side Skybridge resolve/timers engine (`/api/skybridge/*`,
  `skybridge_service.py`) is still live — the estate calls it.
- **`zoe-auth`**, **`zoe-cloudflared`** — auth + edge tunnel (infra).

## 🧩 Live modules (don't mistake these for dead)

Running as containers today — **keep**:
- `modules/zoe-music` → `zoe-music-assistant` — **being replaced** (see below); keep
  running until Zoe can drive Music Assistant.
- `modules/omnigent` → `zoe-omnigent` (remote-coding agent).

## 🟠 Being replaced — keep until the replacement is proven

- **`modules/zoe-music`** → migrating to **Music Assistant** (host service on `:8095`,
  proxied at `/modules/music-assistant/`). The goal is *Zoe intelligently controls
  Music Assistant*. Don't pull `zoe-music` until that's built and lab-proven (no music
  gap). Tracked in [`PLANS.md`](PLANS.md) / [`IDEAS.md`](IDEAS.md).

## 🔴 Retired — do not resurrect

- **`docs/archive/`** — removed from the working tree (2026-06-25). Every byte stays in
  git history — recover with `git log -- docs/archive` if ever needed. Do **not** re-add
  it, and do not re-introduce a `docs/archive/` graveyard. Enforced by
  `test_no_docs_archive_graveyard` in `services/zoe-data/tests/test_canonical_invariants.py`.
- **`modules/orbit`** (social-interaction platform) — retired 2026-06-24. Was wired in
  `docker-compose.modules.yml` (not running). Tracked in git → `git log --all -- modules/orbit`.
- **`modules/agent-zero`** — retired 2026-06-24, no longer used. In git history.
- **`modules/jag-board`** — retired from the repo 2026-06-24. Was **gitignored** (never in
  git), so it is **preserved off-repo** at `~/zoe-archives/jag-board` rather than deleted.
- **`modules/questionable-decisions`** (`zoe-qd-game`) — retired 2026-06-24; moved to an
  internet server (the authoritative copy now lives there).
- **Dockerized `zoe-llamacpp`** — retired; the brain is host-native `llama-server`.
- **wyoming-piper TTS** — retired (replaced by Kokoro to reclaim ~2 GB RAM).
- **whisper as *primary* STT** — superseded by Moonshine (a faster-whisper worker may
  exist as a secondary/fallback, but Moonshine is the rock).

## 📏 The rule (how this stays locked)

1. **Not listed here = not load-bearing.** Don't build on, extend, or cite retired systems.
2. **Swapping a rock is deliberate.** Edit this file *and* `test_canonical_invariants.py`
   in a reviewed PR — never a silent config change.
3. **Retire by removing, not hoarding.** When something is superseded, delete it from the
   tree (git keeps history) and move its row to *Retired* here. No `docs/archive` graveyard.
4. **When in doubt, ask.** A "dead-looking" system may be live (two `modules/` were) —
   verify with `docker ps` + referrer search before touching it.

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
| **TTS** | **Kokoro** (PyTorch on CUDA, out-of-process sidecar) | `/api/voice/synthesize` waterfall: Kokoro → Edge TTS → espeak-ng. Kokoro runs in the `kokoro-tts.service` PyTorch sidecar (port 10201); zoe-data holds no in-process TTS model. CUDA is load-bearing, not a nicety: on CPU the sidecar synthesizes **slower than real time** (RTF ~1.0–1.8x), which starves the sentence-streamed voice pipe and makes replies play back in pieces. CUDA runs at RTF ~0.08x. Costs ~2.3 GB unified memory; the sidecar falls back to CPU on its own if CUDA cannot load and `/health` reports `degraded=true` when it does. |

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
  router:
    # A rock that is ALLOWED TO IMPROVE. What is locked is the ARCHITECTURE and
    # the contract — not the checkpoint. The self-train loop exists to replace
    # the checkpoint, guarded by its ratchet, so pinning a hash here would fight
    # the design. Swapping the two-stage SHAPE is what must fail CI.
    architecture: "two-stage"
    stage1: "SetFit MLP shortlist"
    stage2: "FunctionGemma-270M GBNF decode"
    sidecar_service: "functiongemma-router.service"
    sidecar_port: "11436"
    flag: "ZOE_ROUTER_HEAD"
    stage1_artifact: "services/zoe-data/models/router_head_mlp.joblib"
    stage2_artifact_dir: "~/models/functiongemma-router"
    # Stage 2 ONLY. The ratchet never touches stage1_artifact: router_selftrain.py
    # has no reference to a router/SetFit head, an MLP, or a .joblib in its 1095
    # lines, and the only model ARTIFACT it promotes in production is the stage-2
    # SERVED_GGUF under stage2_artifact_dir (:98, :880-882). Everything else it
    # writes (rollback restores, deployment markers, provenance, last-known-good
    # archives) lives there too — outside the repo, so no ratchet verdict ever
    # reaches git.
    # Stage-1 heads are HAND-COMMITTED; what covers them is the voice replay gate
    # (services/zoe-data/models/* is in VOICE_PATH_PATTERNS), not this ratchet.
    checkpoint_pinned: "no — stage-2 checkpoint owned by the ZOE_ROUTER_SELFTRAIN ratchet; stage-1 head hand-committed, replay-gated"
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
- **Two-stage router** (LIVE, `ZOE_ROUTER_HEAD=active`) — a fast tool-router *front* on the voice
  path: SetFit MLP shortlist → `functiongemma-router.service` GBNF decoder (host-native, `:11436`).
  The Gemma 4 E4B rock stays the brain and the fallback for every abstain/miss. Self-training loop
  (`ZOE_ROUTER_SELFTRAIN`, default OFF) can mine → retrain → ratchet-promote. See [`PLANS.md`](PLANS.md).
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
- `modules/omnigent` → `zoe-omnigent` (remote-coding agent).

`modules/` now holds exactly one module. Everything else under it was retired; see below.

## 🎵 Music — the name collision, stated plainly

**The live music system is `zoe-music-assistant`: the UPSTREAM Music Assistant server
container (`ghcr.io/music-assistant/server:stable`), defined in `docker-compose.modules.yml`,
on host port `:8095`, proxied at `/modules/music-assistant/`.** Zoe drives it from
`services/zoe-data/music_service.py` + `routers/music_setup.py`.

**There is no in-repo `zoe-music` module.** `modules/zoe-music/` was a separate,
first-party FastAPI bridge on `:8100` — an entirely different thing that merely shared a
name prefix. It was deleted 2026-08-05 (see *Retired* below). `zoe-music-assistant` is
**not** a renamed or evolved `zoe-music`; it never was. If you are reading a doc that
implies one became the other, that doc is wrong — the two systems shared nothing but the
first two words of their names, and that confusion is exactly why the dead module survived
five months after it stopped running.

Do not grep for `zoe-music` and treat a `zoe-music-assistant` hit as a module reference.

## 🔴 Retired — do not resurrect

- **`docs/archive/`** — removed from the working tree (2026-06-25). Every byte stays in
  git history — recover with `git log -- docs/archive` if ever needed. Do **not** re-add
  it, and do not re-introduce a `docs/archive/` graveyard. Enforced by
  `test_no_docs_archive_graveyard` in `services/zoe-data/tests/test_canonical_invariants.py`.
- **`modules/zoe-music`** — the first-party music bridge module (`:8100`). Retired
  2026-08-05 by deletion. It had been dead in practice since 2026-02-16, when it was
  dropped from `enabled_modules` and its nginx route removed: no workflow ever built it,
  and **no `zoe-music` container was ever created on this host** (its four stale local
  images predate the module's own first commit). It is **not** the live music system —
  see *Music — the name collision* above. In git history →
  `git log --all -- modules/zoe-music`. Enforced by `test_no_zoe_music_module` in
  `services/zoe-data/tests/test_canonical_invariants.py`.
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

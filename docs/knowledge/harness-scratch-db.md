---
type: Reference
title: The harness scratch database (P-2) - the bar, the day-sim and the person bench never write the live DB
description: scripts/perf/harness_scratch.py builds a throwaway Zoe per run - a scratch Postgres from the live image on its own network, migrated to head, plus a harness zoe-data and Flue pair on it with fresh tokens, scratch paths, the live flags and every house-control endpoint black-holed - and tears it down. Measured RAM, the isolation guarantees and their limits, how the three harnesses use it, owner steps.
tags: [harness, bar, day-sim, person-bench, postgres, isolation, ram, p-2]
timestamp: 2026-10-19T20:00:00+08:00
---

# The harness scratch database (P-2)

Deep dive 2 (P-2): the bar, the day-sim and the person bench must write to a scratch database, never the live one. Before this, each
run sent its turns to the live zoe-data (:8000) and wrote the live Postgres directly (backdating, transcript reads), cleaning up after
itself with a "proven teardown". Now every live run of the three harnesses brings up its own stack and destroys it.

Code: `scripts/perf/harness_scratch.py` (the stack; CLI `up` / `status` / `down` / `check-dsn`), `samantha_bar.scratch_stack_for` /
`close_scratch` (the one entry the three mains share). Tests: `tests/unit/test_harness_scratch.py` (CI: the guard, the environments,
the wiring of the three mains, no hardcoded self-call; opt-in `ZOE_SCRATCH_PG_TEST=1`: a real scratch Postgres, migrated, the
evolution queue exercised on it through asyncpg, torn down).

## What one run builds

1. **Scratch Postgres**: the image id of `zoe-database` (same digest), container `zoe-harness-scratch-<tag>` on its OWN bridge network
   of the same name (not `zoe-network`), published on 127.0.0.1 on a free port only, `--memory 384m` with no swap, 1.5 cpus, 256 pids,
   fsync off. The password is random, passed by a 0600 env file, kept only in the 0600 state file
   `~/.cache/zoe/harness-scratch/<tag>.json`. The schema comes from THIS checkout alembic chain (`alembic upgrade head`, 1.5 s on the
   box) - nothing is read from the live database.
2. **A harness zoe-data** (the service interpreter, `uvicorn main:app` on 127.0.0.1, its own port) with: the scratch DSN; fresh
   internal and brain tokens; HOME, the palace, logs, the router-retrain dir and the voice-sample dir under the stack root; the live
   service FLAGS (every `ZOE_*` name from the service env files that is not a secret and not a location); every house-control and
   agent endpoint (Home Assistant and its bridge, Music Assistant, LiveKit, zoe-auth, Kokoro, Hermes, OpenClaw, Omnigent, Multica, the
   panel host) pointed at a black hole; every self URL (`ZOE_SELF_URL` and friends) at itself; outbound HTTP(S) through a black-hole
   proxy (loopback direct), so a model it would download fails instead - the STT model is the one that does, so STT stays cold;
   `ZOE_HARNESS=1` (the live-palace guard), `ZOE_VOICE_SAVE_AUDIO=0`, `ZOE_MULTICA=false`. The memory embedder loads from the real
   model cache through a symlink.
3. **A harness Flue sidecar** (`labs/flue-zoe-brain-2x/dist/server.mjs`, its own port, its own session DB under the stack root) whose
   tool calls go to the HARNESS zoe-data with the scratch tokens. The brain itself (llama-server) and the router sidecar are shared
   compute, as for any harness turn.

`down` stops only the processes this stack started (pid AND start time checked), removes the container and the network, deletes the
stack root and the state file. Each harness main runs `close_scratch` in `finally`.

## Measured (2026-10-19, the Orin, the voice stack up)

| piece | RSS |
|---|---|
| scratch Postgres (docker stats, after migrations) | 25-38 MiB (cap 384 MiB) |
| harness Flue | 74-115 MB at start, ~32 MB idle |
| harness zoe-data (STT cold; memory embedder and semantic router warm) | 430-600 MB |
| **the stack** | **~490-750 MB**; MemAvailable fell 2040 -> 1422 MB at start, and `down` gave back ~500 MB |

So the full second instance fits, but not with much to spare: `up` refuses below `MIN_MEM_SERVE_MB` (1900 MB MemAvailable), and the
harness own 1.2 GB floor (`samantha_bar.MIN_MEM_MB`) still applies after the stack is up - at the 1.8-2.1 GB the box shows with the
voice stack up, a run will sometimes wait and then refuse (never swap). The schema-only alternative (a scratch database the
harness in-process code points at) was not needed: the in-process cells already use SQLite built from the migrations, and the HTTP
cells need a zoe-data on the scratch database anyway.

## The order inside a harness

The quiet-hours guard (#2145) runs FIRST (`scratch_stack_for` asks it before anything starts: nothing comes up in waking hours or
with the panel in use; the nightly-window and deploy checks sit beside it, so a refused run starts nothing), then the stack, then the harness own gates (readyz on the scratch instance, the forget path, memory headroom),
then the run. `DATA_BASE` is the scratch zoe-data for the whole run; the live client is built from `stack.token` / `stack.dsn`; the
live `POSTGRES_URL` is read in one place only, to refuse a stack that reaches the live server (`assert_not_live`: the live host and
port - any database on it - or 5432 on this box). Results carry `scratch` (tag, ports, RAM; never the DSN or a token).

## Limits, stated plainly

- A code path that hardcodes `http://127.0.0.1:8000` reaches the LIVE service, which trusts loopback callers. The two that did
  (the MCP broadcast, the review intent GETs) now read `ZOE_SELF_URL`; `test_no_hardcoded_self_call_reaches_the_live_service` keeps
  it that way. A hardcoded address of another house service would not be black-holed by the env (none found: their code defaults are
  all env reads, and each is overridden).
- `samantha_day_sim.py --allowlisted` is refused on the scratch stack (it needs an admin session to prove the teardown of an
  allowlisted id; the scratch instance has none). `user_model_ab.py`, `recall_evidence_probe.py` and the voice regression probe
  still run against the live service (out of scope here; ledger).
- The harness turns still share the one llama slot with the household (quiet hours remain the answer), and the harness zoe-data
  sends one KV warm-up prompt at start (as the live one does).

## Owner steps

1. None to enable: the next live run of the bar, the day-sim or the person bench uses the scratch stack (`ZOE_PERF=1`, inside the
   quiet hours). The first run after this lands is the check: its result file has `scratch.ram`, and nothing new appears in the live
   `chat_messages` for a `demo_bar_` user.
2. Optional, after a few runs: lower `samantha_bar.MIN_MEM_MB` while the scratch stack is up if the runs keep waiting on memory
   (the household is asleep in the quiet hours; the stack itself is capped).
3. The 50 harness users the live database still holds from before (P-2 purge, `purge_harness_users.sql`, dry run first) are
   unchanged by this.

The scratch instance runs THIS checkout's code (migrations, zoe-data, the Flue build) - the settings dir (possibly the main checkout) is only
read for flags. It sets `ZOE_TTS_MODE=offline` so `/readyz` does not wait on the black-holed Kokoro sidecar, and its Flue is loopback-only
with `ZOE_BRAIN_OPEN=0`.

# labs — lab-only experiments & spikes

## Purpose

Throwaway-grade experiments and de-risking spikes that must run **isolated from
the Zoe runtime**. Nothing here is imported, built, or executed by any production
service, Docker image, or CI job. Lab work proves (or kills) an idea before any
prod migration — per the Samantha guardrail: **lab-prove before prod**.

## Ownership

Owned by whoever is running the spike. Each spike lives in its own subdirectory
with its own README/RUNBOOK and is self-contained.

## Forbidden

- Do **not** wire any lab code into the live voice path, `zoe-data`, a systemd
  unit, a Docker image, or CI. Labs run as hand-started processes only.
  (Deliberate exceptions, each an **operator opt-in** user-unit *template* that is
  never auto-installed or auto-enabled:
  `flue-zoe-brain/` → `scripts/setup/systemd/flue-zoe-brain.service` (port 3578);
  production reaches the sidecar only through zoe-data's `ZOE_BRAIN_BACKEND=flue`
  seam. The **shipped repo default is OFF** (`core` = `services/zoe-core`), but the
  seam is production-reachable and **this deployment flipped it live on 2026-07-03**
  — so `flue-zoe-brain/` is lab-hosted yet production-reachable, not "never live";
  `flue-zoe-telegram/` → `scripts/setup/systemd/flue-zoe-telegram.service` (the
  long-poll Telegram bot; the operator installs it with their own bot token) plus
  its supervisor `scripts/setup/systemd/flue-zoe-telegram-watchdog.{service,timer}`
  (polls the bot's `GET /health` once a minute and restarts it when the poll loop
  has died but the process is still alive — the recovery the app's 503 health
  signal was designed for; also operator opt-in, never auto-enabled).
  No other lab may ship a unit without amending this contract.)
- Do **not** let lab **harness/agent** work point at the local voice brain on
  `:11434` (Gemma-4-E4B) for *its own* engineering work — harnesses must use a
  separate harness model so the live GPU slot is never contended. (Exception: a
  spike whose explicit subject **is** the Gemma brain — e.g. `flue-zoe-brain/`,
  porting Zoe's brain onto Flue per `docs/architecture/zoe-flue-integration.md`
  Seam M — points at `:11434` by design. The prod seam that reaches it ships
  default-OFF (`core`), but is production-reachable and is **live on this
  deployment since 2026-07-03** via `ZOE_BRAIN_BACKEND=flue`.)
- Do **not** promote a spike to prod without passing its stated acceptance bar
  (the "Samantha tests") and showing no voice-latency regression.

## Work Guidance

- Keep each spike in its own `labs/<name>/` subtree; pin dependencies.
- Mark unverified third-party API calls in-source (e.g. `// FLUE-API:`) so a human
  confirms them against the installed package on first run.

## Verification

Repo structure validator must pass (`labs/**/*` is an approved manifest pattern in
`.zoe/manifest.json`). Lab code is not covered by production CI by design.

## Child DOX Index

- `kokoro-voice-blend/` — custom "Zoe" persona voice spike: pure-numpy blends
  (linear + slerp) of Kokoro style tensors from the stock voices bin, committed
  candidate tensors (`voices/*.npy`, float16) + reproducible generator
  (`blend_zoe_voices.py`) + audition WAVs under `/tmp/zoe-voice-blend-samples/`.
  Audio synthesis runs a one-shot CPU kokoro-onnx (~600MB) and MUST hold
  `flock /tmp/zoe-voice-harness.lock`; never loads a second full Kokoro. Not
  wired anywhere; deployment (augmented voices bin + env flip) is a documented
  operator step gated on the voice replay harness — see its README.
- `flue-harness-spike/` — Flue autonomous-harness substrate spike (scout → implement
  → verify → openPR slice); README + RUNBOOK + FINDINGS are records, not contracts.
- `flue-zoe-brain/` — Flue-hosted Pi `Agent` on the local Gemma brain (a third
  implementation behind the `run_zoe_core` seam, per
  `docs/architecture/zoe-flue-integration.md`). Serves 21 tools (20 capability
  tools against zoe-data + the `activate_abilities` activator; Waves 1–3 of the
  cut-list record `docs/knowledge/flue-cutover-tool-cut-list.md` §3, plus the
  `remember_emotional_moment` emotional-thread capture signal per
  `docs/architecture/zoe-memory-emotional-thread-handoff.md` — the parity
  target) with progressive
  tool disclosure at the wire (always-on core + activated groups per call;
  `src/tools/tool-groups.ts`), per-request acting identity (the seam-forwarded
  `user_id` rides a message envelope, is bound per-turn by the AbortSignal in the
  capped-completions provider, and read by tools via `currentUserId(signal)` —
  `src/request-identity.ts`; env fallback), identity fail-closed, writes
  dry-run-gated.
  Emits the Seam-A text-delta + `__TOOL__`/`__THINKING__` sentinel stream
  (byte-pinned to the prod contract) via content-negotiated NDJSON on the
  agent route (`src/streaming.ts`); whole-result `?wait=result` unchanged.
  Reached from prod via the `ZOE_BRAIN_BACKEND=flue` seam — shipped default-OFF
  (`core`) but production-reachable and **live on this deployment since
  2026-07-03**; supervised via the opt-in unit template (see Forbidden above).
  Operator measurement checklists pending in `flue-zoe-brain/LANDING.md`.
  Quality gates against the live brain live in `flue-zoe-brain/parity/` — a
  committed harness (`gatelib.py` shared library + `run_gates.py` runner that
  discovers `*_gate.py` modules; corpus + adversarial gates today). LAB-only,
  hand-run against the live host, never CI-wired. See
  `flue-zoe-brain/parity/README-GATES.md`.
- `needle-benchmark/` — LAB benchmark of Cactus Needle (26M single-shot
  function-calling model, MIT) as a CPU-only tool/intent router in front of the
  Gemma brain: routing accuracy vs the current Tier-0 regex + Tier-1 embedding
  routing on a labeled corpus, per-decision CPU latency, pre-brain
  classifier-chain cost, and tool-prefilter token savings. Hand-run only (venv
  built OUTSIDE the repo by `setup.sh`; weights from HF at run time, never
  committed). README.md carries the four measured numbers + the adopt/reject
  recommendation. Never wired into the voice path, `fast_tiers`, or CI.
- `digarr-spike/` — evaluation record for digarr (MIT, Bun/PGlite) as a hidden
  music-discovery engine behind Zoe: verified batch-run config against the local
  Gemma llama-server, footprint numbers, listening-source findings, and the
  recommendations→Music Assistant playlist bridge design. README is a record,
  not a contract; nothing runs resident and nothing is prod-wired.
- `flue-zoe-telegram/` — Flue Telegram channel: long-poll bot bridged to zoe-data's
  `/api/chat` (NOT a Flue LLM agent; `src/agents/zoe.ts` is a build-only placeholder
  and registers no model provider — never points at the voice brain on `:11434`).
  Maps each verified sender to their **real Zoe user** via account linking: a user
  stores their numeric telegram id in their profile (`PUT /api/user/profile/telegram`),
  the bot resolves it (`GET /api/system/resolve-telegram/<id>`, internal-only) and
  forwards the turn as that user over zoe-data's **trusted** `/api/chat` override
  (`X-Zoe-User-Id`, honoured only for loopback / valid `X-Internal-Token` — a public
  request can't impersonate; `auth.resolve_acting_user`). Unlinked senders are told
  their id and refused (never reach the brain as a real user). Ships the opt-in unit
  template above. Hand-started, demo-only; README is a record, not a contract.

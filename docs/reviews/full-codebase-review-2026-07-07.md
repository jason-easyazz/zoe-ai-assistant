# Full-codebase review — 2026-07-07

**Method:** five parallel read-only domain reviews (zoe-data core/routers · voice lane ·
memory stack · brain/labs lane · infra/CI/ops/security) against the tree at `6964aa9c`,
each deduped against what's already tracked in
[`tech-debt-remediation-plan.md`](../architecture/tech-debt-remediation-plan.md),
[`PLANS.md`](../PLANS.md), and
[`samantha-evolution-plan.md`](../architecture/samantha-evolution-plan.md).
Line numbers are anchors, not gospel — re-search the symbol before editing.

**One-line verdict:** production-safe and structurally sound; the debt is concentrated
in (1) three live-path voice/proactive bugs, (2) silent-degradation blind spots
(swallowed errors, unbounded growth, fire-and-forget tasks), and (3) pre-W6 ambient
scoping. Secrets hygiene is clean; deploy rollback is solid; soft-delete and
user-scoping on the *active* memory paths are correctly enforced.

## Fix now — new, live-path (F1–F5, each a small PR)

| # | Sev | Where | Problem → fix |
|---|---|---|---|
| F1 | ~~HIGH~~ RESOLVED (#1143/#1146 — the missing-rowcount class was a dead duplicate; the live path worked since #860) | `proactive/engine.py` ~:275 + `db_compat.py` `_Cursor` | `cur.rowcount` doesn't exist on the compat cursor; the exception is **caught**, so the expired-`proactive_pending` prune is a **silent no-op that has never run** (hourly warning + unbounded growth; noted in buildplan §6 2026-06-26, never fixed). Add a `rowcount` property to `_Cursor` + a regression test. *(reframed on verification — see the issue register)* |
| F2 | ~~HIGH~~ | `routers/voice_livekit.py` ~:878-890 / ~:1040-1045 | **RETRACTED on verification:** the barge handler sets LISTENING and cancels in one synchronous block on a single-threaded loop, and the done-callback checks state at execution time — no realistic race window (the code's ordering comment is load-bearing). A task-identity guard remains optional belt-and-braces only. |
| F3 | HIGH | `routers/voice_livekit.py` ~:596 (brain call), ~:554 (empty STT) | **No explicit timeout at the brain call site** (`brain_dispatch.py` configures none; hang behaviour depends on HTTP-client defaults — a wedge can hold PROCESSING); and an **empty transcript returns to ambient with zero user feedback**. Wrap in `asyncio.wait_for(…, ~15s)` with a spoken error + state reset; speak "didn't catch that" on empty STT. |
| F4 | ~~HIGH~~ | `voice_tts.py` ~:4492 (ambient insert), `mcp_server.py` ~:2982 (ambient FTS read) | **FIXED (PR #1145):** `ambient_memory` had **no user scoping**: insert bound no user/speaker; the read queried globally. Migration `0017` adds `user_id` + `(user_id, timestamp)` index; the write path skips unresolvable panels (never store ownerless audio); `ambient_search` filters on the caller's resolved user unconditionally. Hard prerequisite for W6 — folded there as W6.0. |
| F5 | MED | `.github/workflows/validate.yml` ~:109-131 | zoe-auth tests are **hand-enumerated** — the exact silent-skip class just closed for zoe-data. Move to marker/dir-based selection. |

## Hardening — new, important, not urgent (H1–H8)

- **H1** `routers/chat.py` ~:1183 — `_SESSION_LOCKS` grows forever; reap on access or timer.
- **H2** `routers/push.py` ~:106, `db_pool.py` ~:53/:149/:191/:309 — bare `except: pass`
  hides dead clients and broken pools; log + count (feeds the W16 scoreboard).
- **H3** `main.py` lifespan — several background tasks are fire-and-forget (no stored
  ref/shutdown hook: moonshine warmup, semantic router, idle consolidation); store +
  cancel on shutdown.
- **H4** deploy reproducibility — `deploy.yml` ~:71 installs with pinned top-level but
  **unpinned transitive deps** (CI↔prod drift); adopt a lock file / `--require-hashes`.
- **H5** `docker-compose.yml` :41/:72 — `POSTGRES_PASSWORD` as plain container env;
  move to Docker secrets.
- **H6** `services/zoe-ui/nginx.conf` :211/:493 — `X-Forwarded-Proto http` hardcoded
  (lies to HA behind HTTPS); use `$scheme`.
- **H7** voice-lane resilience trio: Kokoro failure has no circuit-breaker (cascades to
  Edge TTS under sidecar restart); stale Silero VAD state on participant reconnect;
  module-global cooldown watchdog is a restart task-leak. All `voice_livekit.py`.
- **H8** client JS: `skybridge-voice.js` `audioChunks` unbounded (long recording can
  kill the tab); cap/rolling buffer.

## Hygiene backlog (fold into existing waves; not separately urgent)

`person_extractor_llm.py` dark flag wired nowhere (verify #1080's intended follow-up or
remove) · vestigial PTT path in `voice_livekit.py` (deprecate/log) · TTS waterfall logic
duplicated across the three voice lanes (extract one helper — do it WITH W1.3, which
touches the same code) · digest/consolidation cron loops in `routers/system.py`
~:698/:748 are tz-naive (use `ZOE_TIMEZONE`) · `.zoe/manifest.json` stale
(updated 2026-06-24) · `modules/zoe-music` references retired `zoe-mcp-server:8003` ·
`numpy<2` pin re-check · `migrate.sh` pgpass piping · MemPalace migration double-gate ·
LiveKit STT temp-file sweep on agent start · zoe-auth CORS allows `http://zoe.local`.

## Retirement-vector watch (brain/labs lane — mostly correct, keep it that way)

- Multica sync (`main.py` ~:1038, gated `ZOE_MULTICA`, default off), OpenClaw
  gateway/skills-watcher, and the paused `hermes-agent.service` are all **dormant but
  resurrectable**; verify the hermes unit is `disabled` (not just stopped) and keep the
  §8 retire-gates as the only re-entry.
- Flue doctrines are sound; two watch items: recall-doctrine interplay (3 overlapping
  recall triggers on a 4B — watch call rates on the next parity run) and identity-leak
  recurrence ("I'm Gemma…") on live voice.
- The quality re-run blocker now has its unblock: #1058 merged the operator script that
  mints the authenticated parity-gate test user — run it, then re-run the parity gate.
- Telegram bot is text-only (`app.ts` ~:53 `message:text`) — voice notes are W8, as
  planned; the front-door re-slot question (direct `/api/chat` w/ channel tag vs the
  formal profile path) needs a one-line decision recorded in the Flue plan.

## Confirmations worth recording

- **W3.2 was already executed** — #1084 shipped the audit null-embedding
  (`_AUDIT_NULL_EMBEDDING`, `memory_service.py:77/:1505`); plan §6 ticked, packet
  retired in this PR.
- Soft-delete + user-scoping are enforced on all *active* memory read paths (verified
  both `_metadata_read` and `_semantic_search`).
- No tracked secrets/env files; Cloudflare creds mounted not embedded; deploy rollback
  (pre-pull SHA + health gate) verified solid; the two-lane CI design is working as
  intended (43/274 zoe-data tests on the fast lane, catch-all on self-hosted).
- `docs/archive/` still has 241 **untracked** files on the live checkout (tracked
  tech-debt item) — clean off disk before someone `git add`s them.

## Recommended execution order

1. **F1** (one-file fix; the prune has never run) → 2. **F3** voice-lane robustness
   (replay-gated; F2 is retracted) → 3. **F4** ambient scoping migration (before any W6
   motion) → 4. **F5 + H4 + H5** infra batch → 5. fold the hygiene list into tech-debt
   Wave 4 / W1.3 as noted; the deeper
   [`issue-register-2026-07-07.md`](issue-register-2026-07-07.md) carries the verified
   second-pass items. F-items are cheap-model-executable with the P0 protocol from
   [`samantha-evolution-packets.md`](../architecture/samantha-evolution-packets.md).

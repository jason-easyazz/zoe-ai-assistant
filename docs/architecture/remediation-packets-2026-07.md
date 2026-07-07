# Remediation packets — 2026-07 (cheap-model executable)

> Fix packets for the verified findings in
> [`full-codebase-review-2026-07-07.md`](../reviews/full-codebase-review-2026-07-07.md)
> and [`issue-register-2026-07-07.md`](../reviews/issue-register-2026-07-07.md).
> **Inherit the P0 protocol from
> [`samantha-evolution-packets.md`](samantha-evolution-packets.md)** (worktree, flag
> rules, replay gate command, validate.yml enumeration, merge mechanics, STOP
> conditions) — it is not repeated here. One packet = one PR. Anchors were captured
> across two nearby snapshots (`6964aa9c` for the register, `66f0cce5` here) — treat ALL
> line numbers as hints and re-search the quoted symbol before editing (the P0 rule).
>
> **Test doctrine for every packet:** ship a regression test that FAILS on the old
> code; mark it `ci_safe` (module-level `pytestmark = pytest.mark.ci_safe`) so the fast
> lane runs it; voice-path packets additionally run the replay gate. No new pip/npm
> dependency is needed unless the packet says so.

## P-F1 — `_Cursor.rowcount` — ✅ RESOLVED (#1143); packet had targeted a stale copy

Audit correction: the packet pointed at `db_compat.py`'s local `AsyncpgCompat`/`_Cursor`
— which turned out to be a **dead duplicate** (no runtime caller constructs them;
`get_compat_db` yields `db_pool.AsyncpgCompat`, which has had `rowcount` since #860, so
the proactive prune works on the live path). #1143 pinned the live path with regression
tests; the dead duplicate was then retired-by-removal (this PR). Do not re-execute.

---

## P-F3 — LiveKit brain timeout + empty-transcript feedback (voice path — replay gate)

- **Fix approach:** in `routers/voice_livekit.py` wrap the `brain_oneshot(...)` call
  (~:596-599) in `asyncio.wait_for(..., timeout=float(os.environ.get(
  "ZOE_LIVEKIT_BRAIN_TIMEOUT_S", "20")))`; on `TimeoutError` treat exactly like the
  existing `llm_ok = False` branch (it already synthesizes an apology path — reuse it,
  don't invent a new one). For empty transcript (~:554): instead of silent
  `state: ambient`, synthesize the short canned line "Sorry, I didn't catch that."
  via the SAME `_synth` call the reply path uses, then return to ambient. Both changes
  guarded so failures degrade to today's behaviour (never crash the loop).
- **Tests:** extend `tests/test_voice_barge_in.py`-style fake harness or new
  `test_livekit_failure_paths.py`: brain hang (mock that never returns) → timeout →
  apology sent + state reset; empty transcript → one TTS message sent. `ci_safe` +
  validate.yml. **Replay gate mandatory.**
- **Software:** none new. **STOP:** do not touch the happy path or the barge-in logic.

## P-F4 — `ambient_memory` user scoping (migration 0016) — DONE (PR #1145)

> Landed as migration **0017** (`0016_ui_layouts` took the number after this packet was
> written; content unchanged). Live `ambient_memory` verified **0 rows** 2026-07-07
> before writing the downgrade — the STOP condition did not fire.

- **Fix approach:** new alembic `0016_ambient_memory_user_scope.py`: add
  `user_id TEXT` (nullable — table is ~empty, capture flag-OFF; enforce NOT NULL at the
  W6 packet instead) + index `(user_id, timestamp)`. Write path
  (`routers/voice_tts.py` ambient insert, ~:4434/:4492 region): resolve the panel's
  user via the existing `ui_panel_sessions`/panel-binding lookup and bind `user_id`
  (NULL only when unresolvable — and then SKIP the insert; do not store ownerless
  audio). Read path (`mcp_server.py` `ambient_search`, ~:2972-2982): add a mandatory
  `WHERE user_id = ?` from the caller's resolved user.
- **Tests:** new `test_ambient_memory_scoping.py`: insert binds resolved user;
  unresolvable → no row; user A cannot read user B's rows via the tool. `ci_safe` +
  validate.yml.
- **Software:** none new (alembic already in stack). **STOP:** never write a migration
  `downgrade` that loses user data. For 0016 specifically: the `user_id` column rollback
  is `DROP COLUMN`; the P-R4 table drops roll back by recreating the **empty schema
  shells** (both tables verified zero readers/writers AND zero rows before dropping —
  verify the zero-rows part live, and STOP if they contain data).

## P-F5 — zoe-auth CI test selection

- **Verified state:** `services/zoe-auth/tests/` has 8 test files; `validate.yml`
  (~:127-131) enumerates only 4 (`test_auth`, `test_smoke`, `test_oidc_jwt`,
  `test_request_logging`) — `test_oidc_login`, `test_rbac`, `test_security` never run
  on the fast lane.
- **Fix approach:** FIRST run the 3 un-enumerated files locally (they may be red —
  possibly why they were left out). Green → switch the workflow step to
  `pytest services/zoe-auth/tests -q` (small suite; no marker machinery needed) and
  delete the enumeration. Red → fix if trivial, else mark
  `pytest.mark.skip(reason=...)` with a dated reason and file the follow-up in the
  issue register. **STOP:** if a red test implies a real auth bug, stop and report —
  that outranks this packet.

## P-F6 — Voice-turn identity + silent FK swallow (the W0 root cause; voice path — replay gate)

- **Diagnosis (live, 2026-07-07):** panel turn reaches `/api/voice/wake` +
  `/turn_stream` (200s); `ui_panel_sessions` binds `zoe-touch-pi → jason`; yet the turn
  resolves `effective_user = "voice-guest"`, and `_schedule_voice_chat_save`
  (`voice_tts.py` ~:2240) skips only `("guest","voice-daemon","")` → the write proceeds
  and Postgres rejects on FK `users` ("voice-guest not present"), error swallowed →
  **zero voice conversations ever persisted**.
- **Fix approach:** (1) in the voice-turn identity resolution (the `_require_voice_auth`
  / effective-user path feeding those call sites), fall back to the panel's bound user
  from `ui_panel_sessions` (the lookup ALREADY EXISTS at `voice_tts.py` ~:770/:879 —
  reuse it) before settling on a guest sentinel; (2) add `voice-guest` to the
  `_schedule_voice_chat_save` skip set (skybridge_service.py treats it as
  guest-equivalent, e.g. :1149); (3) the save's failure path logs a WARNING with the
  user id instead of `except: pass`. Keep PIN/sensitive-scope gating untouched — this
  only affects conversation persistence attribution, same trust level the panel binding
  already grants.
- **Tests:** `test_voice_identity_persistence.py`: bound panel → save called with
  jason; unbound → skipped (no write attempt, no FK error); FK failure → warning
  logged. `ci_safe` + validate.yml. **Replay gate mandatory.**
- **DoD:** re-run P-W0: spoken panel turn → `voice-panel-*` row with
  `metadata.user_id='jason'` → `MEMORY_IDLE_CONSOLIDATE` sweep names jason.
- **STOP:** if identity resolution here is shared with tool *authorization* (not just
  persistence), stop and report — widening authz is out of scope.

## P-H batch (one PR each unless noted)

- **P-H1** `_SESSION_LOCKS` (`routers/chat.py` ~:1183): evict entries older than a TTL
  on each access (store `(lock, last_used)`); test: dict doesn't grow across N fake
  sessions.
- **P-H2** observability of swallowed errors: `routers/push.py` ~:106 and `db_pool.py`
  bare excepts → `logger.warning` + a counter dict the health endpoint exposes (feeds
  W16). Test: failure increments counter. No behaviour change otherwise.
- **P-H3** lifespan task hygiene (`main.py`): store refs for the fire-and-forget tasks,
  cancel+await in shutdown; test via lifespan harness (pattern exists in
  `test_voice_barge_in.py`'s lifecycle tests).
- **P-H4** deploy reproducibility: generate `requirements.lock` with **uv**
  (`uv pip compile`) or pip-tools — uv is already on the host (`~/.local/bin`);
  `deploy.yml` installs from the lock. Software: uv (present) — no runtime change.
- **P-H5** compose secrets: move `POSTGRES_PASSWORD` to docker compose `secrets:`
  (file-based under `config/`, gitignored); zoe-database + zoe-auth read
  `POSTGRES_PASSWORD_FILE`. Software: docker compose ≥ v2 (present). **Operator step:**
  create the secret file before deploy.
- **P-H6** nginx: `X-Forwarded-Proto $scheme` at :211/:493; verify HA still logs in
  (panel check). No new software.
- **P-H7** voice resilience trio (replay-gated, one PR): Kokoro circuit-breaker (skip
  for `ZOE_KOKORO_SKIP_S=60` after 3 consecutive failures), VAD reset on participant
  reconnect, watchdog task owned by the room loop not module global.
- **P-H8** `skybridge-voice.js`: cap `audioChunks` (rolling window); bump `SW_VERSION`
  if precached.

## P-R register HIGHs

- **P-R1 manifest.json:** create a minimal valid PWA manifest in `dist/` (name/short_name
  "Zoe", `start_url:"/"`, `display:"standalone"`, theme/background colors from
  `tokens.css`, one 192px + one 512px icon — icons exist under `dist/` already; if not,
  generate from the orb asset) OR remove the precache entry + the 5 page links. Prefer
  create (restores PWA install). Bump `SW_VERSION`.
- **P-R2 websocket-sync fallback pollers:** store interval IDs on the instance; create
  only if absent; clear on reconnect/success. Test: simulated double-fallback yields
  one interval (jsdom or logic-extracted unit).
- **P-R3 phantom API docs:** rewrite `docs/api/VOICE_API.md` from the actual code
  (`/ws/voice/` message types + real `/api/voice/*` routes — enumerate from
  `routers/voice_tts.py` decorators); mark `HOUSEHOLD_API.md` DEPRECATED with a pointer
  to scope rules; verify `MUSIC_API.md` endpoint-by-endpoint against
  `routers/music.py` before touching it.
- **P-R4 trust-gate reconcile (decision packet, not code):** add a status header to
  `TRUST_GATE.md` (PROPOSAL, superseded-by-W15) and drop `trust_allowlist`/`trust_audit`
  in the same 0016 migration as P-F4 (zero readers/writers; **confirm zero rows live
  before dropping** — the downgrade only recreates empty shells, per P-F4's STOP note) —
  or keep them if W15 will use them; record the choice in the samantha plan W15 section.
- **P-R5 collection-error gate:** add a fast-lane step
  `pytest tests/ --collect-only -q` (collection must succeed even where execution is
  self-hosted-only); then fix the 5 broken-collection files it exposes (DB path,
  `PersonCreate` import) or move them out of collection reach.
- **P-R6 composite indexes:** same 0016 migration: `chat_messages(session_id,
  created_at)`, `reminders(user_id, due_date)`, `ui_panel_sessions(panel_id,
  last_seen_at)`. Verify with `EXPLAIN` before/after on the live-shaped lab DB.

## Order & ownership

F1 → F3 → F4(+R4,R6 share migration 0016) → F5 → R1/R2 (panel-visible wins) → H-batch →
R3/R5. Everything here is Omnigent-fleet / cheap-model executable; voice packets
(P-F3, P-H7, P-H8) carry the replay gate; anything that trips a STOP reports instead of
improvising. After each merge, tick the item in the review/register docs.

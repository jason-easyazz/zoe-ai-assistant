# Issue register — 2026-07-07 (deep pass)

Second-round audit (4 domains: tests/flags · frontend · docs-drift · data layer) on top of
[`full-codebase-review-2026-07-07.md`](full-codebase-review-2026-07-07.md).
**Every item carries a verification marker:** `[V]` = independently re-verified at the
cited location this session; `[A]` = agent-reported and plausible, verify before fixing.
Rejected findings are listed at the bottom **so future audits don't re-report them**.
**Execution:** fix packets (researched approach, tests, software, STOP conditions) live in
[`remediation-packets-2026-07.md`](../architecture/remediation-packets-2026-07.md).
Tree: `6964aa9c`+. Line numbers are anchors — re-search the symbol before editing.

## HIGH — fix or decide soon

- [V] **SW precaches a file that doesn't exist** — `dist/sw.js:97` precaches
  `/manifest.json`; no such file in `dist/` (5+ pages link it). Breaks PWA
  install/offline. Create the manifest or drop the entry.
- [V] **Fallback pollers stack forever** — `dist/js/websocket-sync.js` ~:214-232:
  every `fallback` event creates an anonymous `setInterval` (5s) that is never stored or
  cleared; reconnect cycles accumulate pollers. Store + clear interval IDs.
- [V] **Phantom API docs will mislead code-generating agents** —
  `docs/api/VOICE_API.md` documents `duck`/`unduck`/`interrupt`/`state` HTTP endpoints
  that don't exist (0 hits; the real interface is `/ws/voice/` at `main.py:2430` plus
  the real `/api/voice/*` enroll/identify/ambient routes); `docs/api/HOUSEHOLD_API.md`
  documents an `/api/household` API with zero code. Mark DEPRECATED/rewrite.
- [V] **TRUST_GATE designed, never built, tables orphaned** — `docs/architecture/TRUST_GATE.md`
  has no implementation (0 code hits); `trust_allowlist` + `trust_audit` (alembic 0001)
  have no readers/writers. Mark the doc PROPOSAL; note it overlaps the newer W15 trust
  boundary (samantha plan §9) — reconcile rather than implement both.
- [A] **Broken test collection masked by continue-on-error** — `tests/integration/`:
  `test_comprehensive.py:86` (sqlite3 can't open DB), `test_hallucination_benchmark.py:23`
  (ImportError `PersonCreate`), + 3 more collection errors; the only lane running them
  (self-hosted catch-all) is `continue-on-error`, so they can never fail anything. Fix
  or delete; consider a collection-only gate (`pytest --collect-only`) in the fast lane.
- [A] **Config-default drift** — `ZOE_HA_BRIDGE_URL` default defined in ~8 places,
  `ZOE_AUTH_URL` in 4, `ZOE_TIMEZONE` in 15+ (one divergent-default case verified by the
  agent for `ZOE_KOKORO_VOICE` in `tts_waterfall.py:115/:123`). This IS the tech-debt
  plan's Wave-4 typed-config item — treat these as its seed list, not a new workstream.
- [A] **Two module-level-skipped test files with no plan** — `tests/unit/test_auth_security.py:3`,
  `tests/unit/test_experts.py:3`; skip reasons stale. Fix or delete.

## MED

- [V] `chat_messages` has only `idx_chat_messages_session` (alembic 0001:663) against the
  consolidation sweep's `WHERE session_id … ORDER BY created_at` — add composite
  `(session_id, created_at)`. Same pattern reported [A] for `reminders (user_id, due_date)`
  and `ui_panel_sessions (panel_id, last_seen_at)` (the W2 presence query will use this).
- [A] `dashboard.js:427` — widget-layout edit mode saves nowhere ("TODO: Save to backend
  API"); user edits silently dropped. Implement or disable edit mode.
- [A] `zoe-orb.js` — 6 `addEventListener` with no cleanup across navigations;
  `common.js:119` theme `setInterval` never cleared. Leak-pattern sweep in one PR.
- [A] Alembic downgrade hygiene: `0001` downgrade CASCADE-drops everything with no
  guard; `0015` downgrade breaks if temporal rows exist (documented as accepted). Add a
  production downgrade guard (refuse unless `ZOE_ALLOW_DOWNGRADE=1`).
- [A] JSON/metadata parsing inconsistent: `chat_messages.metadata` defensive in some
  readers, crash-prone in others; `events.metadata` read via `json.loads` with no guard
  (`calendar_utils.py:98`). One tolerant helper, used everywhere.
- [A] `proactive_scheduled.schedule_generation` (added 0012) may not be written on
  insert (a reliability test asserts its absence) — verify writer, fix or drop column.
- [A] Six live-service test files run without CI guards / `ci_safe` decisions
  (`tests/unit/test_tool_calling.py`, `test_ui_functionality.py`, integration/e2e
  live-POST files) — classify each: guard, mark, or move.
- [A] ~60 `ZOE_*` flags read in code but documented nowhere (largest clusters:
  `ZOE_PI_INTENT_*`, `ZOE_MULTICA_*`, compose/agent budgets). Generate a flag registry
  doc from grep as part of Wave-4 typed config.
- [A] Docs drift (docs agent, spot-checked): stale `services/zoe-core` *Python* paths in
  `docs/modules/*` + `PLATFORM_OVERRIDES.md:68` (zoe-core is TypeScript now);
  `docs/knowledge/index.md` still lists graphify as installed tooling;
  `DATABASE_CONSOLIDATION_PLAN.md` says "Ready for Execution" but was never executed —
  add status headers (PROPOSAL/ARCHIVED) rather than deleting.
- [A] `dashboard_layouts` table: reader exists, no writer found (write-less);
  `people_field_definitions.options_json` / `people_field_values.value_json` written
  nowhere read — dark data candidates; confirm then retire columns/table.
- [A] `routers/transactions.py:22` `_is_sqlite()` dialect branching survives the
  Postgres migration — eliminate with the compat-layer cleanup.

## LOW (batch into existing waves)

Duplicate `clear-cache.html`/`-v2` · orphan pages (cook/games/music/updates.html) not
linked anywhere · `auth.js` token in localStorage + inline-JS logout handler ·
`notifications-panel.js` sessionStorage writes without quota guard · z-index scale
sprawl (100→10000) · 3 overlapping widgets-*.css files · `replay_samples.py` in tests/
with zero tests (rename) · `test_voice_barge_in.py` skips w/o the Silero model on CI
(document host-only) · `memory_idle_consolidation.py:55` misleading comment about
`IF NOT EXISTS` · `ZOE_MOONSHINE_ARCH` read twice w/ implicit fallback.

## Rejected on verification (do NOT re-report)

- **"Barge-in done-callback race" (was F2 in the 2026-07-07 review)** — WRONG: the barge
  handler sets LISTENING and cancels in one synchronous block (`voice_livekit.py`
  ~:878-890, the ordering comment is load-bearing); `_on_pipeline_done` checks state at
  execution time on a single-threaded loop. No realistic failure window. F2 retracted.
- **"push_subscriptions is an orphan table"** — WRONG: `routers/push.py` :73/:96/:146/:187
  insert/select/delete it.
- **"MUSIC_API.md documents endpoints that don't exist at all"** — OVERSTATED: a music
  router exists and is registered (`main.py:1851`); the doc needs endpoint-by-endpoint
  verification, not deprecation on sight.
- **"ZOE_EXPRESSIVE_TTS / ZOE_LIVEKIT_STREAM_TTS / ZOE_CROSS_SURFACE_THREAD are dead
  flags"** — WRONG: they are *planned* flags named by the Samantha evolution plan/packets
  for unbuilt workstreams (W11/W1.3/W17), intentionally not yet read by code.
- **"F1 crashes hourly" / "the prune never ran"** — BOTH WRONG (final correction,
  #1143/#1146): the missing-`rowcount` class in `db_compat.py` was a **dead duplicate**
  no runtime caller constructs; the engine's real path (`get_compat_db` →
  `db_pool.AsyncpgCompat`) has had `rowcount` since #860, so **the hourly prune works**.
  #1143 pinned the live path with regression tests; #1146 removed the dead class.
  Lesson recorded: verify which copy of a symbol the runtime actually imports before
  filing against it.
- **"F3: panel freezes forever on brain hang"** — REFRAMED: `brain_dispatch.py` sets no
  timeout at the call site; actual hang behaviour depends on the underlying HTTP client
  defaults. The fix stands (explicit `asyncio.wait_for` + spoken error), the certainty
  of "forever" doesn't.

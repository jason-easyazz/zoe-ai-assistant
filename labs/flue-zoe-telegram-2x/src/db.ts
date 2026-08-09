/**
 * Durable session storage for the Telegram channel. File-backed SQLite on the
 * Jetson so Flue's own per-conversation state survives a process restart.
 * Single-host is the right fit here (one box owns the state).
 *
 * ⚠ THE 2.x SCHEMA BOUNDARY IS ONE-WAY, AND IT IS WHY THIS BUILD RUNS PARALLEL.
 * Flue 2.x stores schema version 8; the 1.0.0-beta line stored version 5.
 * "Pre-1.0 persisted schemas are reset-only — the runtime rejects an older
 * database BEFORE ANY APPLICATION CODE RUNS, and there is no in-place migration"
 * (@flue/runtime docs/guide/migration.md). Two consequences:
 *   1. A 2.x process pointed at the live beta DB refuses to start. It cannot be
 *      "tried" against the running bot's data directory.
 *   2. Rollback is equally blocked: once 2.x has written a v8 store, the beta
 *      runtime cannot read it either. Reverting the unit alone does NOT restore
 *      service — a state wipe is required in BOTH directions.
 *
 * WHAT THAT ACTUALLY COSTS ON THIS SERVICE — and it is the reason the Telegram
 * channel is the right pathfinder. Nothing on the user-visible path reads this
 * store: replies come from zoe-data's /api/chat keyed by `sessionFor(chatId)`
 * (a string, not a Flue conversation), and the `/new` epoch map is our own JSON
 * (src/brain.ts) which copies across verbatim. The only thing a fresh v8 store
 * discards is Flue's record of a placeholder agent that is never dispatched.
 * So the brain sidecar's hardest cutover risk — losing live session history —
 * is, here, empirically nil. Cutover still starts on a FRESH store, by choice
 * rather than by necessity.
 *
 * Override with ZOE_TELEGRAM_DB — and DO override it for any trial run.
 *
 * Tests take a different route again: `start({ agents: [...] })` from
 * `@flue/runtime/node` defaults to in-memory SQLite, so a test run touches no
 * file at all.
 *
 * LAB ONLY.
 */
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sqlite } from '@flue/runtime/node';

// Anchor the DB at the PACKAGE ROOT (the parent of the built `dist/`), not the
// built module's own dir — so `dist/data/...` (which a clean rebuild would wipe)
// is never used and the path is independent of the process cwd. In the Vite Node
// build `import.meta.url` is the emitted module under `dist/`, so `../data`
// resolves to `<package>/data`.
const moduleDir = dirname(fileURLToPath(import.meta.url));
const dbPath = process.env.ZOE_TELEGRAM_DB ?? join(moduleDir, '..', 'data', 'zoe.db');

export default sqlite(dbPath);

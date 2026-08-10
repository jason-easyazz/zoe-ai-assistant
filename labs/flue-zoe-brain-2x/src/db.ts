/**
 * Persistence for the lab-only Zoe-brain sidecar.
 *
 * Flue's Node build discovers `src/db.ts` and uses its default-exported
 * `PersistenceAdapter` for durable conversation state. Per the integration plan
 * (§3 Seam B), this DB holds ONLY Flue's own conversation durability — never Zoe
 * business data. MemPalace in zoe-data stays the system-of-record.
 *
 * ⚠ THE 2.x SCHEMA BOUNDARY IS ONE-WAY, AND IT IS WHY THIS BUILD RUNS PARALLEL.
 * Flue 2.x stores schema version 8; the 1.0.0-beta line stored version 5.
 * "Pre-1.0 persisted schemas are reset-only — the runtime rejects an older
 * database BEFORE ANY APPLICATION CODE RUNS, and there is no in-place migration"
 * (@flue/runtime migration guide). Two consequences that must not be discovered
 * at deploy time:
 *   1. A 2.x process pointed at the live beta DB refuses to start. It cannot be
 *      "tried" against the running sidecar's data directory.
 *   2. Rollback is equally blocked: once 2.x has written a v8 store, the beta
 *      runtime cannot read it either. Reverting the unit alone does NOT restore
 *      service — a state wipe is required in BOTH directions.
 * So this port is proven on a THROWAWAY data dir via ZOE_BRAIN_DB, never in
 * place, and any real cutover needs a drain plus an explicit, operator-made
 * decision about discarding live session history. Nothing here migrates beta
 * state; there is no supported path that could.
 *
 * Tests take a different route again: `start({ agents: [Zoe] })` from
 * `@flue/runtime/node` defaults to in-memory SQLite, so a test run touches no
 * file at all.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sqlite } from '@flue/runtime/node';

// Anchor the DB at the PACKAGE ROOT (the parent of the built `dist/`), not the
// built module's own dir — so `dist/data/...` (which a clean rebuild would wipe)
// is never used, the path is independent of the process cwd, and a missing dir
// can't fail before /health. In the Vite Node build `import.meta.url` is the
// emitted module under `dist/`, so `../data` resolves to `<package>/data`.
// Override with ZOE_BRAIN_DB — and DO override it for any 2.x trial: see the
// one-way schema note above.
const distDir = dirname(fileURLToPath(import.meta.url));
const dbPath = process.env.ZOE_BRAIN_DB ?? join(distDir, '..', 'data', 'zoe-brain.db');

export default sqlite(dbPath);

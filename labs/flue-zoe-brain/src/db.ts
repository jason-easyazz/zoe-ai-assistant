/**
 * Persistence for the lab-only Zoe-brain sidecar.
 *
 * Flue's Node build auto-discovers `src/db.ts` and uses its default-exported
 * `PersistenceAdapter` for durable conversation/run state. Per the integration
 * plan (§3 Seam B), this DB holds ONLY Flue's own conversation/run durability —
 * never Zoe business data. MemPalace in zoe-data stays the system-of-record.
 *
 * LAB ONLY.
 */
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sqlite } from '@flue/runtime/node';

// Anchor the DB at the PACKAGE ROOT (the parent of the built `dist/`), not the
// built module's own dir — so `dist/data/...` (which a clean rebuild would wipe)
// is never used, the path is independent of the process cwd, and a missing dir
// can't fail before /health. In the Node build `import.meta.url` is the emitted
// module under `dist/`, so `../data` resolves to `<package>/data`. Override with
// ZOE_BRAIN_DB.
const distDir = dirname(fileURLToPath(import.meta.url));
const dbPath = process.env.ZOE_BRAIN_DB ?? join(distDir, '..', 'data', 'zoe-brain.db');

export default sqlite(dbPath);

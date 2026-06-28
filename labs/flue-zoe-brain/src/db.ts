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

// Anchor the DB beside this package, independent of the process working
// directory — so starting `dist/server.mjs` from the repo root can't drop a
// `data/` db next to Zoe's live data, and starting from a dir without `./data`
// can't fail before /health. Override with ZOE_BRAIN_DB.
const here = dirname(fileURLToPath(import.meta.url));
const dbPath = process.env.ZOE_BRAIN_DB ?? join(here, 'data', 'zoe-brain.db');

export default sqlite(dbPath);

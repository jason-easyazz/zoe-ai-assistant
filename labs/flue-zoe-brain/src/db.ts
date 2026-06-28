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
import { sqlite } from '@flue/runtime/node';

export default sqlite('./data/zoe-brain.db');

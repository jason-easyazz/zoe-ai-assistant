import { defineConfig } from '@flue/runtime/config';

/**
 * Project config for the lab-only Zoe Telegram channel.
 *
 * `target: 'node'` builds a self-contained Node server (`dist/server.mjs`) —
 * the same artifact `scripts/setup/systemd/flue-zoe-telegram.service` runs.
 *
 * Flue 2.x notes (both confirmed against @flue/runtime@2.0.1, not inferred):
 *   - `defineConfig` moved from `@flue/cli/config` (that subpath no longer
 *     exists — @flue/cli@2.0.1 ships no `exports` map) to `@flue/runtime/config`.
 *   - the `root` / `output` fields are retired; Vite owns both, and strict
 *     validation REJECTS them here.
 *
 * LAB ONLY — not a production unit.
 */
export default defineConfig({
  target: 'node',
});

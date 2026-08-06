import { defineConfig } from '@flue/runtime/config';

/**
 * Project config for the lab-only Zoe-brain sidecar.
 *
 * `target: 'node'` builds a self-contained Node server (`dist/server.mjs`).
 * Provider/model registration is a runtime concern and lives in `src/app.ts`
 * via `setProvider(...)`, not here.
 *
 * Flue 2.x notes:
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

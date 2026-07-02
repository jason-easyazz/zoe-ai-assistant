import { defineConfig } from '@flue/cli/config';

/**
 * Build-time config for the lab-only Zoe-brain sidecar.
 *
 * `target: 'node'` builds a self-contained Node server (`dist/server.mjs`).
 * Provider/model registration is a runtime concern and lives in `src/app.ts`
 * via `registerProvider(...)`, not here.
 *
 * LAB ONLY — not a production unit.
 */
export default defineConfig({
  target: 'node',
});

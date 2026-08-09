import { flue } from '@flue/vite';
import { defineConfig } from 'vite';

/**
 * Vite build for the lab-only Zoe Telegram channel.
 *
 * Flue 2.x deleted `flue build` / `flue dev`: a Flue application is a Vite
 * project and `vite build` / `vite dev` are the only commands
 * (@flue/runtime docs/guide/migration.md, "Build and dev commands"). The
 * `flue()` plugin is what runs the `'use agent'` source scan that REGISTERS
 * agents and what resolves `src/app.ts` + `src/db.ts` into `dist/server.mjs`.
 *
 * Node target and entry discovery live in `flue.config.ts` (the host-independent
 * project config), which this plugin auto-discovers.
 *
 * LAB ONLY — not a production unit.
 */
export default defineConfig({
  plugins: [flue()],
});

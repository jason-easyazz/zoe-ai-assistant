import { flue } from '@flue/vite';
import { defineConfig } from 'vite';

/**
 * Vite build for the lab-only Zoe-brain sidecar.
 *
 * Flue 2.x deleted `flue build` / `flue dev`: a Flue application is a Vite
 * project and `vite build` / `vite dev` are the only commands
 * (@flue/runtime/docs/guide/migration.md, "Build and dev commands"). The
 * `flue()` plugin is what runs the `'use agent'` source scan that REGISTERS
 * agents — mounting `createAgentRouter(Zoe)` in app.ts exposes the routes but
 * registers nothing, so without this plugin the built server has no agents.
 *
 * Node target and the entry paths live in `flue.config.ts` (the host-independent
 * project config), which this plugin auto-discovers.
 *
 * LAB ONLY — not a production unit.
 */
export default defineConfig({
  plugins: [flue()],
});

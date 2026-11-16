import { flue } from '@flue/vite';
import { defineConfig } from 'vite';

/**
 * LAB ONLY. `dedupe` forces the one shared sidecar module (src/streaming.ts, imported by relative path)
 * to resolve @flue/runtime, pi-ai and hono from THIS app's node_modules, so there is a single runtime
 * instance (a second copy would split `observe`'s subscribers and the stream would see nothing).
 */
export default defineConfig({
  plugins: [flue()],
  resolve: { dedupe: ['@flue/runtime', '@earendil-works/pi-ai', 'hono', 'valibot'] },
});

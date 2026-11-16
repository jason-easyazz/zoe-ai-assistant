/**
 * LAB ONLY entrypoint: the kernel-bridge brain on its OWN port, pointed at the running llama server.
 * Not the live sidecar (:3579): different directory, build, port, agent name, tools and state. It
 * reuses ONE module of the sidecar's source, the Seam-A NDJSON stream (src/streaming.ts), unchanged
 * and read-only, so the frames captured in a run are the genuine wire format. No auth gate: it binds
 * 127.0.0.1 only and the runner owns the port for the duration of the run.
 */
import { setProvider } from '@flue/runtime';
import { createAgentRouter } from '@flue/runtime/routing';
import { Hono } from 'hono';
import { seamAStreamingMiddleware } from '../../../flue-zoe-brain-2x/src/streaming.ts';
import { Lab } from './agents/lab.ts';
import { createLabProvider } from './provider.ts';

export function createApp(): Hono {
  setProvider(createLabProvider());
  const app = new Hono();
  app.get('/health', (c) => c.json({ ok: true, service: 'zoe-os-brain-lab-app', at: new Date().toISOString() }));
  app.use('/agents/*', seamAStreamingMiddleware());
  app.route('/agents/lab', createAgentRouter(Lab));
  return app;
}

export default createApp();

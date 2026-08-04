/**
 * Boot the REAL channel in-process — real grammY transport, real route map, real
 * handler wiring — against a mock Bot API and a mock zoe-data. No listening
 * socket for the app itself (Hono is driven through `app.fetch`), no real
 * Telegram, no real zoe-data, no file on disk except a temp epoch map.
 *
 * ENV BEFORE IMPORT IS THE WHOLE TRICK. `src/telegram.ts` snapshots
 * `TELEGRAM_API_ROOT` and constructs the `Bot` at module load, `src/brain.ts`
 * snapshots `ZOE_DATA_URL` at module load, and `src/app.ts` starts long-polling
 * at module load. So every `src/*` import here is DYNAMIC and happens after the
 * assignments below. A static import at the top of this file would build the bot
 * against api.telegram.org and point the bridge at the live zoe-data on :8000 —
 * the same class of near-miss the brain port recorded.
 */
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Hono } from 'hono';
import { startMockTelegram, type MockTelegram } from './mock-telegram.ts';
import { startMockZoeData, type MockZoeData } from './mock-zoe-data.ts';

export interface ChannelHarness {
  app: Hono;
  telegram: MockTelegram;
  zoeData: MockZoeData;
  /** The live poll-health object /health reads. */
  health: { polling: boolean };
  /** GET /health through the real route map. */
  health$(): Promise<Response>;
  stop(): Promise<void>;
}

export interface HarnessOptions {
  /** Extra env applied before the dynamic imports; restored on stop(). */
  env?: Record<string, string | undefined>;
  /** Start the long-poll loop (default true). */
  poll?: boolean;
}

export async function startChannelHarness(options: HarnessOptions = {}): Promise<ChannelHarness> {
  const telegram = await startMockTelegram();
  const zoeData = await startMockZoeData();

  const saved = new Map<string, string | undefined>();
  const setEnv = (key: string, value: string | undefined) => {
    if (!saved.has(key)) saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  };

  setEnv('TELEGRAM_BOT_TOKEN', '123456:test-token-not-real');
  setEnv('TELEGRAM_API_ROOT', telegram.url);
  setEnv('ZOE_DATA_URL', zoeData.url);
  setEnv('ZOE_BRAIN_URL', undefined);
  setEnv('ZOE_INTERNAL_TOKEN', 'seekrit');
  setEnv('SESSION_EPOCHS_PATH', join(mkdtempSync(join(tmpdir(), 'tg2x-epochs-')), 'epochs.json'));
  for (const [key, value] of Object.entries(options.env ?? {})) setEnv(key, value);

  const appModule = await import('../../src/app.ts');
  const { bot } = await import('../../src/telegram.ts');
  const app = appModule.default;

  return {
    app,
    telegram,
    zoeData,
    health: appModule.pollHealth,
    health$: async () => app.fetch(new Request('http://telegram.test/health')),
    stop: async () => {
      try {
        await bot.stop();
      } catch {
        // bot.stop() throws if the loop never started; that is fine here.
      }
      await telegram.close();
      await zoeData.close();
      for (const [key, value] of saved) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    },
  };
}

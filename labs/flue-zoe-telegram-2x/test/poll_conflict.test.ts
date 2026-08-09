/**
 * The 409 path — a live process that is deaf, and the ONLY signal anyone gets.
 *
 * A Telegram bot token allows exactly one `getUpdates` consumer. If a second one
 * is running, this bot's poll loop dies while the node process stays perfectly
 * alive: `systemctl status` is green, `Restart=always` never fires, and the box
 * looks healthy while nobody's messages are answered.
 * `flue-zoe-telegram-watchdog.timer` exists solely to catch that, and the thing
 * it polls is this app's `/health` returning 503.
 *
 * So the 503 is a load-bearing external contract of the port, not an
 * implementation detail — this file proves the app still produces it, and
 * deliberately gets there through a REAL 409 from the mock Bot API rather than by
 * poking the health flag by hand.
 *
 * Its own process (node --test runs one process per file) because the transport
 * module is a module-load singleton.
 */
import assert from 'node:assert/strict';
import { after, before, it } from 'node:test';

import { waitFor } from './helpers/mock-zoe-data.ts';
import { startChannelHarness, type ChannelHarness } from './helpers/harness.ts';

let h: ChannelHarness;

before(async () => {
  h = await startChannelHarness();
});

after(async () => {
  await h.stop();
});

it('a 409 Conflict from getUpdates flips /health to 503 and does NOT retry', async () => {
  const up = await waitFor(() => h.health.polling, 15_000);
  assert.ok(up, 'precondition: the bot must be polling before we can break it');

  h.telegram.failNextPollWith409();
  // Nudge the loop so the next getUpdates happens promptly.
  h.telegram.push({ update_id: 1 });

  const down = await waitFor(() => !h.health.polling, 15_000);
  assert.ok(down, 'a 409 must stop the loop and mark the process unhealthy');

  const res = await h.health$();
  assert.equal(res.status, 503, 'the watchdog polls for exactly this');
  assert.deepEqual(await res.json(), {
    ok: false,
    service: 'flue-zoe-telegram',
    polling: false,
  });

  // No retry storm: the app must not have resumed polling on its own.
  const callsAfter = h.telegram.methods.filter((m) => m === 'getUpdates').length;
  await new Promise((r) => setTimeout(r, 500));
  assert.equal(
    h.telegram.methods.filter((m) => m === 'getUpdates').length,
    callsAfter,
    'the app must not retry after a 409 — a retry storm just hammers Telegram',
  );
});

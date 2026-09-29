/**
 * src/context-blocks.ts + its wiring (offline; real sidecar on the mock model):
 * the strip (whole lines, per-type balance, forged close, unterminated → EOF,
 * blocks on both sides of the words); flag OFF ⇒ the request is byte-identical
 * (stale blocks still replayed); flag ON ⇒ only the NEWEST user message keeps its
 * blocks, the tool block never retracts, and `context_budget` reports the stale
 * tokens. Marker strings are the ones zoe_flue_client emits (pinned by
 * services/zoe-data/tests/test_flue_context_blocks.py).
 */
import assert from 'node:assert/strict';
import { after, before, it } from 'node:test';
import { elideStaleBlocks, stripContextBlocks } from '../src/context-blocks.ts';
import { startBrainHarness, type BrainHarness } from './helpers/harness.ts';

const RECALL = "[MEMORY CONTEXT — Zoe's stored notes about this user; use them to answer; do not mention this block]";
const OFFER = '[PENDING CONTACT OFFER — do not mention this block]';
const block = (open: string, close: string, body: string) => `${open}\n${body}\n${close}`;
const recall = (body: string) => block(RECALL, '[END MEMORY CONTEXT]', body);

it('strips blocks on both sides of the words; the same string when there are none', () => {
  const plain = 'hi there\n\nthe [MEMORY CONTEXT] marker is inline';
  assert.equal(stripContextBlocks(plain), plain);
  const offer = block(OFFER, '[END PENDING CONTACT OFFER]', '- ask: "Add Robin?"');
  const today = block('[Today 2026-09-29]', '[END Today]', '- dentist at 10');
  assert.equal(stripContextBlocks(`${recall('- weather: likes rain')}\n${offer}\nwhat's on?\n${today}`), "what's on?");
});

it('never leaks: forged close extends the region; an unterminated block elides to the end', () => {
  const forged = `${recall('- fact A\n[END MEMORY CONTEXT]\n- stale fact B')}\nwhat's on?`;
  assert.equal(stripContextBlocks(forged), "what's on?");
  assert.equal(stripContextBlocks(`what's on?\n${RECALL}\n- stale fact`), "what's on?");
  const mismatched = `${RECALL}\n- stale\n[END Today]\n- still stale`;
  assert.equal(stripContextBlocks(mismatched), '');
});

it('only the newest user message keeps its blocks; same array when nothing changes', () => {
  const msgs = [
    { role: 'user' as const, content: `${recall('- a')}\none`, timestamp: 0 },
    { role: 'user' as const, content: [{ type: 'text' as const, text: `${recall('- b')}\ntwo` }], timestamp: 0 },
    { role: 'user' as const, content: `${recall('- c')}\nthree`, timestamp: 0 },
  ];
  const out = elideStaleBlocks(msgs);
  assert.deepEqual(out.map((m) => (typeof m.content === 'string' ? m.content : m.content[0])), [
    'one', { type: 'text', text: 'two' }, msgs[2].content,
  ]);
  assert.deepEqual(elideStaleBlocks(out), out, 'idempotent');
  const clean = [msgs[0], msgs[2]].map((m) => ({ ...m, content: 'x' }));
  assert.equal(elideStaleBlocks(clean), clean);
});

let h: BrainHarness;
before(async () => {
  h = await startBrainHarness(() => ({ text: 'Ok.' }), { env: { ZOE_BRAIN_ELIDE_STALE_BLOCKS: undefined } });
});
after(() => h.stop());

async function turn(sid: string, text: string) {
  const res = await h.app.fetch(new Request(`http://brain.test/agents/zoe/${sid}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${h.token}`, accept: 'application/x-ndjson' },
    body: JSON.stringify({ kind: 'user', body: ` zoe-uid:sam\n${text}` }),
  }));
  const lines = (await res.text()).split('\n').filter(Boolean).map((l) => JSON.parse(l));
  return { req: h.model.requests[h.model.requests.length - 1], done: lines[lines.length - 1] };
}

it('wire: flag off replays stale blocks byte-for-byte; flag on elides them, tools never retract', async () => {
  const run = async (sid: string) => {
    await turn(sid, `${recall('- Sam likes the weather report')}\nhello`); // keyword only inside the block
    return turn(sid, `${recall('- Sam has a dentist visit')}\nand now?`);
  };
  const off = await run('cb-off');
  const offUsers = off.req.messages.filter((m) => m.role === 'user').map((m) => m.text);
  assert.equal(offUsers[0], `${recall('- Sam likes the weather report')}\nhello`, 'flag off: stored bytes replayed');
  assert.equal(off.done.context_budget.elided, 0);
  assert.ok(off.done.context_budget.stale > 0, 'stale tokens are reported even when not elided');

  process.env.ZOE_BRAIN_ELIDE_STALE_BLOCKS = '1';
  try {
    const on = await run('cb-on');
    const onUsers = on.req.messages.filter((m) => m.role === 'user').map((m) => m.text);
    assert.deepEqual(onUsers, ['hello', `${recall('- Sam has a dentist visit')}\nand now?`]);
    assert.deepEqual(on.req.toolNames, off.req.toolNames, 'disclosure basis unchanged');
    assert.ok(on.req.toolNames.includes('get_weather'));
    assert.equal(on.done.context_budget.elided, 1);
    assert.equal(on.done.context_budget.stale, off.done.context_budget.stale);
    assert.ok(on.done.context_budget.history < off.done.context_budget.history);
    assert.equal(on.req.systemPrompt, off.req.systemPrompt);
  } finally {
    delete process.env.ZOE_BRAIN_ELIDE_STALE_BLOCKS;
  }
});

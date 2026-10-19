/**
 * scripts/harness-overlay.ts: the versioned overlay root, the ledger, switch and revert, and the
 * hot-reload confirmation. LAB-ONLY, offline: no unit is signalled - the reload path is exercised
 * against THIS process (its own SIGHUP handler, installed by a real createApp() with the flag set)
 * and a local /health server that serves this process's live harness view.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'jason';
delete process.env.ZOE_BRAIN_HARNESS_DIR;

const ctl = await import('../scripts/harness-overlay.ts');
const harness = await import('../src/harness.ts');

const scratch = mkdtempSync(join(tmpdir(), 'harness-ctl-test-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
let n = 0;
const NO_RELOAD = { reload: 'none' };

function freshRoot(): string {
  const root = join(scratch, `root${n++}`);
  mkdirSync(root);
  return root;
}

describe('init, switch, revert and the ledger', () => {
  it('init renders the compiled harness as a version and points current at it', async () => {
    const root = freshRoot();
    const r = await ctl.init(root, 'owner', NO_RELOAD);
    assert.equal(r.from, null);
    assert.ok(lstatSync(join(root, 'current')).isSymbolicLink());
    assert.equal(readlinkSync(join(root, 'current')), r.to, 'a RELATIVE link to the version name');
    assert.equal(harness.loadOverlay(join(root, 'current')).instructions, (await import('../src/agents/zoe.ts')).ZOE_INSTRUCTIONS);
    const ledger = ctl.readLedger(root);
    assert.deepEqual(ledger.map((e) => e.action), ['init', 'reload']);
    assert.equal(ledger[0].who, 'owner');
    assert.equal(ledger[0].to_sha256, r.toSha);
    assert.equal(ledger[1].outcome, 'skipped');
  });

  it('switch records who, when, from/to and both shas; revert restores the previous overlay', async () => {
    const root = freshRoot();
    const first = await ctl.init(root, 'owner', NO_RELOAD);
    ctl.renderOverlay(join(root, 'cand-1'));
    writeFileSync(join(root, 'cand-1', 'doctrines', 'voice-delivery.md'), 'Short sentences only.\n');
    const sw = await ctl.switchTo(root, 'cand-1', 'night-loop', 'proposal 12', NO_RELOAD);
    assert.equal(sw.from, first.to);
    assert.equal(sw.fromSha, first.toSha);
    assert.notEqual(sw.toSha, first.toSha);
    assert.equal(ctl.currentVersion(root), 'cand-1');
    const entry = ctl.readLedger(root).filter((e) => e.action === 'switch')[0];
    assert.equal(entry.who, 'night-loop');
    assert.equal(entry.reason, 'proposal 12');
    assert.equal(entry.from, first.to);
    assert.equal(entry.to, 'cand-1');
    assert.ok(!Number.isNaN(Date.parse(entry.ts)));

    const rv = await ctl.revert(root, 'owner', 'undo that', NO_RELOAD);
    assert.equal(rv.to, first.to);
    assert.equal(rv.toSha, first.toSha);
    assert.equal(ctl.currentVersion(root), first.to);
    const actions = ctl.readLedger(root).filter((e) => e.action !== 'reload').map((e) => e.action);
    assert.deepEqual(actions, ['init', 'switch', 'revert']);
  });

  it('refuses to switch to a refused overlay (current is untouched, nothing ledgered)', async () => {
    const root = freshRoot();
    const first = await ctl.init(root, 'owner', NO_RELOAD);
    mkdirSync(join(root, 'bad'));
    writeFileSync(join(root, 'bad', 'restraint.md'), 'never mind');
    const before = ctl.readLedger(root).length;
    await assert.rejects(ctl.switchTo(root, 'bad', 'night-loop', undefined, NO_RELOAD), /unknown entry/);
    assert.equal(ctl.currentVersion(root), first.to);
    assert.equal(ctl.readLedger(root).length, before);
  });

  it('refuses bad version names, paths out of the root, and a missing --who', async () => {
    const root = freshRoot();
    await ctl.init(root, 'owner', NO_RELOAD);
    await assert.rejects(ctl.switchTo(root, '../etc', 'x', undefined, NO_RELOAD), /bad version/);
    await assert.rejects(ctl.switchTo(root, 'current', 'x', undefined, NO_RELOAD), /bad version/);
    await assert.rejects(ctl.switchTo(root, 'missing', 'x', undefined, NO_RELOAD), /not a version directory/);
    symlinkSync(scratch, join(root, 'linked'));
    await assert.rejects(ctl.switchTo(root, 'linked', 'x', undefined, NO_RELOAD), /not a version directory/);
    ctl.renderOverlay(join(root, 'ok'));
    await assert.rejects(ctl.switchTo(root, 'ok', ' ', undefined, NO_RELOAD), /--who/);
  });

  it('revert refuses when there is nothing before, or the ledger and current disagree', async () => {
    const root = freshRoot();
    await ctl.init(root, 'owner', NO_RELOAD);
    await assert.rejects(ctl.revert(root, 'owner', undefined, NO_RELOAD), /no previous overlay/);
    ctl.renderOverlay(join(root, 'hand'));
    rmSync(join(root, 'current'));
    symlinkSync('hand', join(root, 'current')); // a hand edit the ledger never saw
    await assert.rejects(ctl.revert(root, 'owner', undefined, NO_RELOAD), /fix by hand/);
  });

  it('exit status: 0 only for confirmed or on-disk-by-design; every other outcome is 3', () => {
    const r = (outcome: string) => ({ from: null, to: 'v', fromSha: null, toSha: null, reload: { outcome, ms: 0, detail: '' } }) as never;
    assert.equal(ctl.switchExitCode(r('confirmed')), 0);
    assert.equal(ctl.switchExitCode(r('skipped')), 0);
    assert.equal(ctl.switchExitCode(r('timeout')), 3);
    assert.equal(ctl.switchExitCode(r('refused')), 3);
    assert.equal(ctl.switchExitCode(r('not_signalled')), 3, 'a brain that was never signalled did not switch');
    assert.equal(ctl.switchExitCode(r('something-new')), 3, 'an unknown outcome fails closed');
  });

  it('a /health harness on a DIFFERENT overlay root, or no main pid, is a non-switch: not_signalled, exit 3', async () => {
    const root = freshRoot();
    await ctl.init(root, 'owner', NO_RELOAD);
    ctl.renderOverlay(join(root, 'cand'));
    // A /health that claims a harness rooted somewhere else, plus a live pid that must NOT be signalled.
    const server = createServer((_req, res) => {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ ok: true, harness: { dir: join(scratch, 'some-other-root', 'current'), loaded_at: '2000-01-01T00:00:00Z' } }));
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
    const healthUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/health`;
    try {
      const other = await ctl.switchTo(root, 'cand', 'night-loop', undefined, { reload: `pid:${process.pid}`, healthUrl });
      assert.equal(other.reload.outcome, 'not_signalled');
      assert.match(other.reload.detail, /not signalled/);
      assert.equal(ctl.switchExitCode(other), 3);
      assert.equal(ctl.currentVersion(root), 'cand', 'the switch is still on disk');

      // Same root, but the pid is unusable (pid 1 / non-integer): also a non-switch.
      const rootB = freshRoot();
      await ctl.init(rootB, 'owner', NO_RELOAD);
      ctl.renderOverlay(join(rootB, 'cand'));
      const sameRoot = createServer((_req, res) => {
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({ ok: true, harness: { dir: join(rootB, 'current'), loaded_at: '2000-01-01T00:00:00Z' } }));
      });
      await new Promise<void>((r) => sameRoot.listen(0, '127.0.0.1', r));
      try {
        const url = `http://127.0.0.1:${(sameRoot.address() as AddressInfo).port}/health`;
        const nopid = await ctl.switchTo(rootB, 'cand', 'night-loop', undefined, { reload: 'pid:0', healthUrl: url });
        assert.equal(nopid.reload.outcome, 'not_signalled');
        assert.match(nopid.reload.detail, /no main pid/);
        assert.equal(ctl.switchExitCode(nopid), 3);
      } finally {
        sameRoot.close();
      }

      // Negative control: the by-design disk-only cases still exit 0.
      const none = await ctl.switchTo(root, 'cand', 'night-loop', undefined, NO_RELOAD);
      assert.equal(ctl.switchExitCode(none), 0);
      const bare = createServer((_req, res) => res.end(JSON.stringify({ ok: true })));
      await new Promise<void>((r) => bare.listen(0, '127.0.0.1', r));
      try {
        const url = `http://127.0.0.1:${(bare.address() as AddressInfo).port}/health`;
        const noHarness = await ctl.switchTo(root, 'cand', 'night-loop', undefined, { reload: `pid:${process.pid}`, healthUrl: url });
        assert.equal(noHarness.reload.outcome, 'skipped');
        assert.equal(ctl.switchExitCode(noHarness), 0);
      } finally {
        bare.close();
      }

      // An UNAVAILABLE or malformed /health is not the deliberate no-harness case: the brain was not
      // signalled, so the switch is unconfirmed -> not_signalled, exit 3 (never the silent exit-0 skip).
      const broken: Array<[string, (res: import('node:http').ServerResponse) => void]> = [
        ['non-2xx', (res) => { res.statusCode = 503; res.end('{"ok":false}'); }],
        ['bad JSON', (res) => { res.end('<html>not json'); }],
        ['non-object JSON', (res) => { res.end('null'); }],
      ];
      for (const [label, answer] of broken) {
        const bad = createServer((_req, res) => answer(res));
        await new Promise<void>((r) => bad.listen(0, '127.0.0.1', r));
        try {
          const url = `http://127.0.0.1:${(bad.address() as AddressInfo).port}/health`;
          const r = await ctl.switchTo(root, 'cand', 'night-loop', undefined, { reload: `pid:${process.pid}`, healthUrl: url });
          assert.equal(r.reload.outcome, 'not_signalled', label);
          assert.equal(ctl.switchExitCode(r), 3, label);
        } finally {
          bad.close();
        }
      }
      // Connection refused (nothing listening) is the same.
      const dead = createServer();
      await new Promise<void>((r) => dead.listen(0, '127.0.0.1', r));
      const deadUrl = `http://127.0.0.1:${(dead.address() as AddressInfo).port}/health`;
      await new Promise<void>((r) => dead.close(() => r()));
      const unreachable = await ctl.switchTo(root, 'cand', 'night-loop', undefined, { reload: `pid:${process.pid}`, healthUrl: deadUrl });
      assert.equal(unreachable.reload.outcome, 'not_signalled');
      assert.equal(ctl.switchExitCode(unreachable), 3);
    } finally {
      server.close();
    }
  });

  it('revert picks its target under the lock (a held lock refuses before reading the ledger)', async () => {
    const root = freshRoot();
    await ctl.init(root, 'owner', NO_RELOAD);
    ctl.renderOverlay(join(root, 'v2'));
    await ctl.switchTo(root, 'v2', 'x', undefined, NO_RELOAD);
    writeFileSync(join(root, '.harness-overlay.lock'), '1');
    await assert.rejects(ctl.revert(root, 'owner', undefined, NO_RELOAD), /holds/);
    rmSync(join(root, '.harness-overlay.lock'));
    assert.equal((await ctl.revert(root, 'owner', undefined, NO_RELOAD)).from, 'v2');
  });

  it('the lock refuses a concurrent run and a stale lock is cleared', async () => {
    const root = freshRoot();
    await ctl.init(root, 'owner', NO_RELOAD);
    ctl.renderOverlay(join(root, 'v2'));
    writeFileSync(join(root, '.harness-overlay.lock'), '1');
    await assert.rejects(ctl.switchTo(root, 'v2', 'x', undefined, NO_RELOAD), /holds/);
    rmSync(join(root, '.harness-overlay.lock'));
    await ctl.switchTo(root, 'v2', 'x', undefined, NO_RELOAD);
    assert.ok(!existsSync(join(root, '.harness-overlay.lock')));
  });
});

describe('hot reload: SIGHUP + /health confirmation, under the 10 s budget', () => {
  it('switch and revert are confirmed by the brain without a restart; a brain without the flag is never signalled', async () => {
    const root = freshRoot();
    const first = await ctl.init(root, 'owner', NO_RELOAD);
    ctl.renderOverlay(join(root, 'cand'));
    writeFileSync(join(root, 'cand', 'manner.md'), 'Be brief.\n');

    // A /health server answering from THIS process's live harness, like the real app does.
    let withFlag = false;
    const server = createServer((_req, res) => {
      const h = harness.activeHarness();
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ ok: true, ...(withFlag && h.enabled ? { harness: harness.harnessHealth(h) } : {}) }));
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
    const healthUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/health`;
    const opts = { reload: `pid:${process.pid}`, healthUrl };
    try {
      // 1. The brain runs WITHOUT the flag: switch is on disk only, no signal (Node would exit on SIGHUP).
      const skipped = await ctl.switchTo(root, 'cand', 'night-loop', undefined, opts);
      assert.equal(skipped.reload.outcome, 'skipped');
      assert.match(skipped.reload.detail, /without ZOE_BRAIN_HARNESS_DIR/);
      await ctl.revert(root, 'night-loop', undefined, NO_RELOAD);

      // 2. Start the real app with the flag: it installs the SIGHUP handler.
      process.env.ZOE_BRAIN_HARNESS_DIR = join(root, 'current');
      const { createApp } = await import('../src/app.ts');
      createApp();
      withFlag = true;
      assert.equal(harness.activeHarness().label, first.to);

      const sw = await ctl.switchTo(root, 'cand', 'night-loop', 'canary', opts);
      assert.equal(sw.reload.outcome, 'confirmed', sw.reload.detail);
      assert.ok(sw.reload.ms <= ctl.RELOAD_BUDGET_MS, `${sw.reload.ms} ms`);
      assert.equal(harness.activeHarness().label, 'cand');
      assert.equal(harness.activeHarness().manner, 'Be brief.');

      const rv = await ctl.revert(root, 'owner', 'undo that', opts);
      assert.equal(rv.reload.outcome, 'confirmed', rv.reload.detail);
      assert.ok(rv.reload.ms <= ctl.RELOAD_BUDGET_MS, `${rv.reload.ms} ms`);
      assert.equal(harness.activeHarness().label, first.to);
      const reloads = ctl.readLedger(root).filter((e) => e.action === 'reload').map((e) => e.outcome);
      assert.deepEqual(reloads.slice(-2), ['confirmed', 'confirmed']);
      console.log(`# measured hot reload: switch ${sw.reload.ms} ms, revert ${rv.reload.ms} ms`);
    } finally {
      server.close();
      delete process.env.ZOE_BRAIN_HARNESS_DIR;
      harness.resetHarnessForTests();
    }
  });
});

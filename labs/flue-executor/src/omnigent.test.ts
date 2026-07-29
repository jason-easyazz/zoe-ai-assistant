/**
 * Unit tests for the omnigent lane's session-id guard (no DB, no network, no
 * Omnigent — run with `npm run test:unit`).
 *
 * The guard exists because the session id is interpolated into the docker-exec
 * `sh -c` kick string. The load-bearing test is the LAST one: it drives the
 * real `spawnOmnigentWorker` with a hostile id over a stubbed fetch + fake pool
 * and asserts the task is failed with the refusal reason. Delete the
 * `assertSafeSessionId` call from the call site and that test goes red (the run
 * proceeds to the kick and fails for some other reason instead) — the guard is
 * pinned by behaviour, not only by the helper's own unit tests.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import type pg from 'pg';
import type { ExecutorConfig } from './config.ts';
import type { TaskRow } from './queue.ts';
import { SESSION_ID_RE, assertSafeSessionId, spawnOmnigentWorker } from './omnigent.ts';

test('accepts both real id shapes (0.7.0 dropped the conv_ prefix)', () => {
  // <=0.4.0 shape and the bare-hex 0.7.0 shape must BOTH pass, or the guard
  // rejects every live dispatch instead of only the hostile ones.
  assert.equal(
    assertSafeSessionId('conv_dc2e28f9de3e4074ab7a2cb6279f5d47'),
    'conv_dc2e28f9de3e4074ab7a2cb6279f5d47',
  );
  assert.equal(
    assertSafeSessionId('dc2e28f9de3e4074ab7a2cb6279f5d47'),
    'dc2e28f9de3e4074ab7a2cb6279f5d47',
  );
});

test('rejects degenerate short ids (the {16,} floor, in lockstep with the other two sites)', () => {
  // A short id is not merely odd — cross_review.sh's cleanup kills by SUBSTRING
  // match on /proc/<pid>/cmdline, where "e" matched 5 live container processes
  // (the omnigent server included) against 2 for a real id. This guard must not
  // accept what its Python/bash siblings reject; drop {16,} back to + and every
  // case below goes green, which is what makes this a control rather than decor.
  for (const value of ['e', 'ab', 'abc123', 'conv_abc123', '0123456789abcde']) {
    assert.equal(SESSION_ID_RE.test(value), false, JSON.stringify(value));
    assert.throws(() => assertSafeSessionId(value), /refusing unsafe omnigent session id/);
  }
});

test('rejects every shell metacharacter, prefixed or bare', () => {
  const hostile = [
    'conv_a;rm -rf', 'conv_$(x)', 'conv_`x`', 'conv_a b', 'conv_a|b',
    'a;rm -rf', '$(x)', '`x`', 'a b', 'a|b', 'a&b', 'a>b', 'a<b', "a'b", 'a"b',
    '../etc', 'a/b', 'a\\b', 'conv_', '', ' ', 'conv_a\t',
  ];
  for (const value of hostile) {
    assert.throws(
      () => assertSafeSessionId(value),
      /refusing unsafe omnigent session id/,
      `expected refusal for ${JSON.stringify(value)}`,
    );
  }
});

test('rejects a trailing newline (a newline is a shell command separator)', () => {
  // JS `$` (no `m` flag) already anchors at the true end of the string — unlike
  // Python's `$`, which also matches before a final newline and needed `\Z`
  // (#1589). Pinned explicitly so a future edit to SESSION_ID_RE — adding `m`,
  // or porting the Python source verbatim — cannot silently reopen the hole:
  // `-r abc\n > /tmp/…log` would split the kick and run `.log` as a command.
  for (const value of ['conv_abc\n', 'abc\n', 'abc\r\n', 'conv_abc\nrm -rf /', '\nabc']) {
    assert.equal(SESSION_ID_RE.test(value), false, JSON.stringify(value));
    assert.throws(() => assertSafeSessionId(value), /refusing unsafe omnigent session id/);
  }
});

test('rejects non-string ids (the API response is not typed at runtime)', () => {
  for (const value of [undefined, null, 42, {}, ['conv_abc']]) {
    assert.throws(() => assertSafeSessionId(value), /refusing unsafe omnigent session id/);
  }
});

test('spawnOmnigentWorker refuses a hostile session id before the kick', async () => {
  // Negative control: this is the assertion that dies if the guard is removed
  // from the call site. With the guard, the run stops at session creation and
  // the task is failed with the refusal reason; without it, the run continues
  // to POST comments / launch a runner / docker-exec the kick and fails with a
  // different reason (or none), so the reason match below goes red.
  const hostile = 'conv_abc; touch /tmp/flue-exec-guard-should-never-run';
  const seen: string[] = [];
  const failures: string[] = [];

  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    seen.push(`${init?.method ?? 'GET'} ${path}`);
    const body = path.endsWith('/v1/hosts')
      ? { hosts: [{ host_id: 'host_1', status: 'online' }] }
      : path.endsWith('/v1/sessions')
        ? { id: hostile }
        : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof globalThis.fetch;

  // Minimal pg.Pool stand-in: reportTransition takes a client and runs a
  // transaction; every UPDATE claims one row so the transition "wins".
  const client = {
    query: async (sql: string, params?: unknown[]) => {
      if (/failure_reason=\$2/.test(sql)) failures.push(String(params?.[1] ?? ''));
      return { rows: [], rowCount: 1 };
    },
    release: () => {},
  };
  const pool = {
    query: async () => ({ rows: [], rowCount: 1 }),
    connect: async () => client,
  } as unknown as pg.Pool;

  const cfg = {
    mode: 'lab', dispatch: 'full',
    databaseUrl: '', adminDatabaseUrl: '',
    runtimeId: '00000000-0000-4000-8000-00000dab0002', runtimeName: '', killSwitchPath: '',
    pollMs: 1000, workerTimeoutMs: 60_000,
    omnigentBaseUrl: 'http://127.0.0.1:6767',
    omnigentAgentId: 'ag_test',
    // Deliberately not a real container: if the guard were gone, the kick would
    // shell out here, and it must not reach the live zoe-omnigent container.
    omnigentContainer: 'flue-exec-unit-test-no-such-container',
    omnigentTimeoutMs: 1000, omnigentPollMs: 1000,
  } satisfies ExecutorConfig;

  const task: TaskRow = {
    id: '00000000-0000-4000-8000-0000dead0001',
    agent_id: '00000000-0000-4000-8000-00000dab0003',
    issue_id: null, status: 'dispatched', attempt: 1, max_attempts: 1,
    context: { lane: 'heavy', phase: 'implement' }, work_dir: null,
  };

  try {
    await spawnOmnigentWorker(pool, cfg, task);
  } finally {
    globalThis.fetch = realFetch;
  }

  assert.equal(failures.length, 1, `expected exactly one failure write, got ${failures.length}`);
  assert.match(failures[0], /refusing unsafe omnigent session id/);
  // The brief must never have been staged against the hostile id: the guard
  // fires between session creation and the first use of the id.
  assert.deepEqual(
    seen.filter((s) => s.includes('/comments') || s.includes('/runners')),
    [],
  );
});

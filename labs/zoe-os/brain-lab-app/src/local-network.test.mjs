import { test } from 'node:test';
import assert from 'node:assert/strict';
import { localFetch, localOrigin } from './local-network.mjs';

test('local requests cannot opt into redirects; rejected URLs never reach the client', async () => {
  const seen = [];
  const fetcher = localFetch(async (url, init) => { seen.push([url, init.redirect]); return 'synthetic'; });
  for (const host of ['127.0.0.1', '127.2.3.4', '[::1]', 'localhost']) {
    await fetcher(`http://${host}:8080/v1/completions`, { redirect: 'follow' });
  }
  assert.equal(seen.length, 4);
  assert.ok(seen.every(([, redirect]) => redirect === 'error'));
  await fetcher(new URL('http://localhost:80/v1/completions'));
  assert.deepEqual(seen.at(-1), [new URL('http://localhost/v1/completions'), 'error']);
  assert.throws(() => localOrigin('http://localhost'), /loopback/);
  for (const url of ['https://localhost:80', 'http://example.invalid:80',
    'http://user:pass@localhost:80', 'http://127.1:80', 'http://%6cocalhost:80',
    'http://localhost:0', 'http://localhost:80\\@example.invalid']) {
    assert.throws(() => fetcher(url), /loopback/);
  }
  assert.equal(seen.length, 5);
  assert.equal(localOrigin('http://[::1]:8080/v1'), 'http://[::1]:8080');
});

/**
 * get_time must answer in ZOE_TIMEZONE (default Australia/Perth), not the sidecar host's
 * locale/zone (LAB-ONLY, offline).
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/local_time.test.ts
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const { describeNow, zoeTimeZone } = await import('../src/local-time.ts');

// 2026-10-11T22:30:00Z = Mon 12 Oct 06:30 in Perth (UTC+8), Mon 12 Oct 09:30 in Sydney (AEDT +11),
// but still Sun 11 Oct 22:30 in UTC.
const INSTANT = new Date('2026-10-11T22:30:00Z');

test('defaults to Australia/Perth regardless of the host zone', () => {
  delete process.env.ZOE_TIMEZONE;
  assert.equal(zoeTimeZone(), 'Australia/Perth');
  const out = describeNow(INSTANT);
  assert.match(out, /Monday/);
  assert.match(out, /12/);
  assert.match(out, /6:30/);
});

test('ZOE_TIMEZONE is honoured, read per call', () => {
  process.env.ZOE_TIMEZONE = 'Australia/Sydney';
  assert.match(describeNow(INSTANT), /9:30/);
  process.env.ZOE_TIMEZONE = 'UTC';
  const utc = describeNow(INSTANT);
  assert.match(utc, /Sunday/);
  assert.match(utc, /10:30/);
  delete process.env.ZOE_TIMEZONE;
});

test('an unknown zone falls back to the default instead of throwing', () => {
  process.env.ZOE_TIMEZONE = 'Not/AZone';
  assert.equal(zoeTimeZone(), 'Australia/Perth');
  assert.match(describeNow(INSTANT), /6:30/);
  delete process.env.ZOE_TIMEZONE;
});

test('the get_time tool is wired through describeNow, not the host locale', () => {
  const src = readFileSync(new URL('../src/tools/zoe-tools.ts', import.meta.url), 'utf8');
  assert.match(src, /return describeNow\(\);/);
  assert.doesNotMatch(src, /toLocaleTimeString/);
});

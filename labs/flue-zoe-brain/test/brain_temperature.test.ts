/**
 * Unit coverage for the prod-parity sampling temperature (LAB-ONLY, offline).
 *
 * Every brain model call must carry temperature 0.5 (the canonical prod brain's
 * pinned value, services/zoe-data/zoe_agent.py) unless the caller explicitly
 * sets one or the operator overrides via ZOE_BRAIN_TEMPERATURE. Without it,
 * llama-server's 0.7 default applies — measurably raising the MTP fork-point
 * token glitch ("I don'm …"): 0.7 corrupted ~3.5% of fork-heavy replies
 * (5/128); 0.5 was 0/60 flue-side and 0/74 prod-side.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/brain_temperature.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const { brainTemperature, withBrainTemperature } = await import(
  '../src/providers/capped-completions.ts'
);

test('defaults to the prod-parity 0.5', () => {
  delete process.env.ZOE_BRAIN_TEMPERATURE;
  assert.equal(brainTemperature(), 0.5);
});

test('ZOE_BRAIN_TEMPERATURE overrides, read per call', () => {
  process.env.ZOE_BRAIN_TEMPERATURE = '0.7';
  assert.equal(brainTemperature(), 0.7);
  process.env.ZOE_BRAIN_TEMPERATURE = '0';
  assert.equal(brainTemperature(), 0); // 0 is a valid (greedy) setting
  delete process.env.ZOE_BRAIN_TEMPERATURE;
});

test('invalid or out-of-range env falls back to the default', () => {
  for (const bad of ['', 'abc', '-1', '2.5', 'NaN']) {
    process.env.ZOE_BRAIN_TEMPERATURE = bad;
    assert.equal(brainTemperature(), 0.5, `env ${JSON.stringify(bad)}`);
  }
  delete process.env.ZOE_BRAIN_TEMPERATURE;
});

test('merges into undefined/empty options', () => {
  delete process.env.ZOE_BRAIN_TEMPERATURE;
  assert.equal(withBrainTemperature(undefined).temperature, 0.5);
  assert.equal(withBrainTemperature({}).temperature, 0.5);
});

test('an explicitly-set caller temperature wins', () => {
  delete process.env.ZOE_BRAIN_TEMPERATURE;
  assert.equal(withBrainTemperature({ temperature: 1.1 }).temperature, 1.1);
  assert.equal(withBrainTemperature({ temperature: 0 }).temperature, 0);
});

test('does not mutate the caller options object', () => {
  const original: { temperature?: number; other?: string } = { other: 'x' };
  const merged = withBrainTemperature(original);
  assert.equal(merged.temperature, 0.5);
  assert.equal(original.temperature, undefined);
});

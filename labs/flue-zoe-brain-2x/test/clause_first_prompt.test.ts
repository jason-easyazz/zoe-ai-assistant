/**
 * ZOE_VOICE_CLAUSE_FIRST_PROMPT (default OFF): a static clause-first opening instruction for the voice brain.
 * LAB-ONLY, offline. Pins: flag parsing, OFF = the pre-flag bytes (the golden lives in persona_layer.test.ts),
 * ON = exactly one extra paragraph right after the voice-delivery doctrine, the text is static (cache-safe), and
 * it carries the "never trade correctness for a fast opening" guard.
 *
 * Run: node --experimental-strip-types --test test/clause_first_prompt.test.ts
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'jason';
delete process.env.ZOE_VOICE_CLAUSE_FIRST_PROMPT;

const z = await import('../src/agents/zoe.ts');

describe('ZOE_VOICE_CLAUSE_FIRST_PROMPT', () => {
  it('only an explicit on-value enables it', () => {
    for (const v of ['1', 'true', 'YES', ' on ']) assert.equal(z.clauseFirstPromptEnabled({ ZOE_VOICE_CLAUSE_FIRST_PROMPT: v }), true, v);
    for (const v of [undefined, '', '0', 'false', 'off', 'maybe']) assert.equal(z.clauseFirstPromptEnabled({ ZOE_VOICE_CLAUSE_FIRST_PROMPT: v }), false, String(v));
  });

  it('is OFF in the module default: ZOE_INSTRUCTIONS has no clause-first text', () => {
    assert.equal(z.ZOE_INSTRUCTIONS.includes(z.CLAUSE_FIRST_DOCTRINE), false);
    assert.equal(z.ZOE_INSTRUCTIONS, z.buildZoeInstructions(false));
  });

  it('ON adds exactly one paragraph, straight after the voice doctrine, and nothing else moves', () => {
    const on = z.buildZoeInstructions(true);
    const off = z.buildZoeInstructions(false);
    assert.equal(on.replace(`\n\n${z.CLAUSE_FIRST_DOCTRINE}`, ''), off);
    assert.equal(on.indexOf(z.CLAUSE_FIRST_DOCTRINE), on.indexOf(z.VOICE_DELIVERY_DOCTRINE) + z.VOICE_DELIVERY_DOCTRINE.length + 2);
    assert.ok(on.endsWith(z.PROMPT_CONFIDENTIALITY_DOCTRINE), 'the confidentiality rule keeps the last position');
  });

  it('is static text (prefix-cache safe) and small', () => {
    assert.equal(z.buildZoeInstructions(true), z.buildZoeInstructions(true));
    assert.ok(z.CLAUSE_FIRST_DOCTRINE.length < 450, `${z.CLAUSE_FIRST_DOCTRINE.length} chars`);
    assert.doesNotMatch(z.CLAUSE_FIRST_DOCTRINE, /\$\{|Date|\d{4}-\d{2}/);
  });

  it('keeps the correctness guard and sits before (is subordinate to) the tool rules', () => {
    assert.match(z.CLAUSE_FIRST_DOCTRINE, /never guess, skip a tool/);
    const on = z.buildZoeInstructions(true);
    assert.ok(on.indexOf(z.CLAUSE_FIRST_DOCTRINE) < on.indexOf(z.ACTIVATOR_DOCTRINE));
  });
});

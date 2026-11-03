/**
 * soul.ts vs services/zoe-core/SOUL.md: one source of truth, the drift declared and pinned.
 * LAB-ONLY, offline.
 *
 * Round 1 of the architecture deep dive (2026-10-18) found src/soul.ts claiming to be a "verbatim
 * copy" of SOUL.md while carrying a RECALL paragraph SOUL.md lacks, with no test tying them. The
 * resolution: src/soul.ts is the source of truth for the live brain; SOUL.md (the dormant core
 * lane's soul) must equal it with exactly the declared flue-only paragraphs removed. The overlay's
 * rendered soul.md is ZOE_SOUL itself. So an edit to any of the three fails here until reconciled.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

const { FLUE_ONLY_SOUL_PARAGRAPHS, ZOE_SOUL } = await import('../src/soul.ts');
const { renderCompiledFiles } = await import('../scripts/harness-overlay.ts');

const SOUL_MD = readFileSync(new URL('../../../services/zoe-core/SOUL.md', import.meta.url), 'utf8');

/** The soul with the given paragraphs removed (paragraphs are separated by one blank line). */
function without(soul: string, paragraphs: readonly string[]): string {
  return soul
    .split('\n\n')
    .filter((p) => !paragraphs.includes(p))
    .join('\n\n');
}

describe('soul.ts is SOUL.md plus the declared flue-only paragraphs', () => {
  it('SOUL.md == ZOE_SOUL minus FLUE_ONLY_SOUL_PARAGRAPHS, byte for byte (one trailing newline)', () => {
    assert.ok(SOUL_MD.endsWith('\n') && !SOUL_MD.endsWith('\n\n'));
    assert.equal(without(ZOE_SOUL, FLUE_ONLY_SOUL_PARAGRAPHS), SOUL_MD.slice(0, -1));
  });

  it('each flue-only paragraph is in the soul exactly once and absent from SOUL.md', () => {
    assert.ok(FLUE_ONLY_SOUL_PARAGRAPHS.length > 0);
    for (const p of FLUE_ONLY_SOUL_PARAGRAPHS) {
      assert.equal(ZOE_SOUL.split('\n\n').filter((x) => x === p).length, 1);
      assert.ok(!SOUL_MD.includes(p.slice(0, 60)));
    }
  });

  it('NEGATIVE CONTROL: the check sees an undeclared paragraph and an edited one', () => {
    assert.notEqual(without(ZOE_SOUL, []), SOUL_MD.slice(0, -1), 'with nothing declared the drift must show');
    const edited = ZOE_SOUL.replace('Use contractions.', 'Use contractions often.');
    assert.notEqual(without(edited, FLUE_ONLY_SOUL_PARAGRAPHS), SOUL_MD.slice(0, -1));
  });

  it('the overlay renders soul.md from ZOE_SOUL, so the overlay cannot fork the soul silently', () => {
    assert.equal(renderCompiledFiles().get('soul.md'), ZOE_SOUL + '\n');
  });
});

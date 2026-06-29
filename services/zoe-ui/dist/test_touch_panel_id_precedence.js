/*
 * Controlled test for touch-ui-executor.js getPanelId().
 *
 * Registered panel ids in `zoe_panel_id` must win over the generated
 * `zoe_touch_panel_id` alias so /ws/push subscribes to the registered panel
 * channel. A fresh browser with no registered id still gets the generated
 * alias fallback.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');

function extract(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, 'missing function ' + name);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error('unbalanced braces for ' + name);
}

function makeGetPanelId({ search = '', stored = {}, random = 0.123456789 } = {}) {
  const writes = [];
  const window = { location: { search } };
  const localStorage = {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(stored, key) ? stored[key] : null;
    },
    setItem(key, value) {
      stored[key] = String(value);
      writes.push([key, String(value)]);
    }
  };
  const Math = Object.assign({}, global.Math, { random: () => random });
  const URLSearchParams = global.URLSearchParams;
  // eslint-disable-next-line no-eval
  const isGeneratedPanelAlias = eval('(' + extract('isGeneratedPanelAlias') + ')');
  // eslint-disable-next-line no-eval
  const generatePanelAlias = eval('(' + extract('generatePanelAlias') + ')');
  // eslint-disable-next-line no-eval
  const getPanelId = eval('(' + extract('getPanelId') + ')');
  return { getPanelId, generatePanelAlias, isGeneratedPanelAlias, stored, writes };
}

let harness = makeGetPanelId({
  stored: {
    zoe_panel_id: 'zoe-touch-pi',
    zoe_touch_panel_id: 'panel_0e3ko5bl'
  }
});
assert.strictEqual(harness.getPanelId(), 'zoe-touch-pi', 'registered id must beat generated alias');

harness = makeGetPanelId({ stored: { zoe_touch_panel_id: 'panel_alias' } });
assert.strictEqual(harness.getPanelId(), 'panel_alias', 'unregistered browser keeps existing generated alias');

harness = makeGetPanelId({ stored: {}, random: 0.5 });
const generated = harness.getPanelId();
assert.ok(generated.startsWith('panel_'), 'fresh browser mints generated alias');
assert.strictEqual(generated, 'panel_i0000000', 'short base-36 random output is padded to fixed alias width');
assert.strictEqual(harness.isGeneratedPanelAlias(generated), true, 'freshly minted alias is classified as generated');
assert.strictEqual(harness.stored.zoe_touch_panel_id, generated, 'generated alias is persisted');
assert.strictEqual(harness.stored.zoe_panel_id, undefined, 'generated alias does not become registered id');

harness = makeGetPanelId({
  search: '?panel_id=living-room',
  stored: {
    zoe_panel_id: 'zoe-touch-pi',
    zoe_touch_panel_id: 'panel_0e3ko5bl'
  }
});
assert.strictEqual(harness.getPanelId(), 'living-room', 'explicit URL panel_id overrides stored ids');
assert.deepStrictEqual(harness.writes, [
  ['zoe_panel_id', 'living-room'],
  ['zoe_touch_panel_id', 'living-room']
], 'URL panel_id is persisted to both storage keys for later pages');

harness = makeGetPanelId({
  search: '?panel_id=panel_0e3ko5bl',
  stored: {
    zoe_panel_id: 'zoe-touch-pi',
    zoe_touch_panel_id: 'panel_oldalias'
  }
});
assert.strictEqual(harness.getPanelId(), 'panel_0e3ko5bl', 'explicit URL alias still selects that page identity');
assert.deepStrictEqual(harness.writes, [
  ['zoe_touch_panel_id', 'panel_0e3ko5bl']
], 'generated URL aliases stay fallback-only and do not overwrite the registered id');

for (const random of [0, 0.000001, 0.1, 0.5, 0.999999999999]) {
  harness = makeGetPanelId({ stored: {}, random });
  const alias = harness.generatePanelAlias();
  assert.match(alias, /^panel_[a-z0-9]{8}$/i, `generated alias has fixed detector width for random=${random}`);
  assert.strictEqual(harness.isGeneratedPanelAlias(alias), true, `generated alias is detected for random=${random}`);
}

for (let i = 0; i < 500; i++) {
  harness = makeGetPanelId({ stored: {}, random: (i + 1) / 1000 });
  const alias = harness.generatePanelAlias();
  assert.match(alias, /^panel_[a-z0-9]{8}$/i, `generated alias has fixed detector width at iteration ${i}`);
}

console.log('getPanelId precedence: all assertions passed');

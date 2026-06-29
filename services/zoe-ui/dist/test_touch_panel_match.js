/*
 * Controlled test for touch-ui-executor.js panelMatches() — the auth-gating
 * helper. A silent wrong-panel reject (matching only the stale state.panelId)
 * was the root cause of "auth/voice cards never appear", so this pins the alias
 * resolution: registered id matches, stale generated id still matches, a foreign
 * id is rejected, and an empty/absent target broadcasts.
 *
 * Extracts the REAL panelMatches body from the source and runs it against mocked
 * state / window.location / localStorage — no DOM or kiosk needed.
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

// Build panel match helpers bound to a mockable harness scope.
function makePanelFns({ statePanelId = '', urlPanelId = null, lsTouch = null, lsPanel = null } = {}) {
  const state = { panelId: statePanelId };
  const window = { location: { search: urlPanelId ? '?panel_id=' + encodeURIComponent(urlPanelId) : '' } };
  const localStorage = {
    getItem(k) {
      if (k === 'zoe_touch_panel_id') return lsTouch;
      if (k === 'zoe_panel_id') return lsPanel;
      return null;
    }
  };
  const URLSearchParams = global.URLSearchParams;
  // eslint-disable-next-line no-eval
  const isGeneratedPanelAlias = eval('(' + extract('isGeneratedPanelAlias') + ')');
  // eslint-disable-next-line no-eval
  const collectPanelIdentity = eval('(' + extract('collectPanelIdentity') + ')');
  // eslint-disable-next-line no-eval
  const panelMatches = eval('(' + extract('panelMatches') + ')');
  // eslint-disable-next-line no-eval
  const panelMatchesAuthTarget = eval('(' + extract('panelMatchesAuthTarget') + ')');
  return { panelMatches, panelMatchesAuthTarget };
}

function makePanelMatches(opts) {
  return makePanelFns(opts).panelMatches;
}

// The live regression: state.panelId is a stale generated id; the registered id
// lives in localStorage; the backend addresses the registered id.
const live = makePanelMatches({ statePanelId: 'panel_0e3ko5bl', lsTouch: 'zoe-touch-pi', lsPanel: 'zoe-touch-pi' });
assert.strictEqual(live('zoe-touch-pi'), true, 'registered id (backend target) must match — this was the bug');
assert.strictEqual(live('panel_0e3ko5bl'), true, 'stale state.panelId must still match');
assert.strictEqual(live(''), true, 'empty/unaddressed target broadcasts to this panel');
assert.strictEqual(live(undefined), true, 'absent target broadcasts to this panel');
assert.strictEqual(live('kitchen-panel-2'), false, 'a genuinely different panel must be rejected');

// URL-provided id is also an alias.
const viaUrl = makePanelMatches({ statePanelId: 'panel_x', urlPanelId: 'living-room' });
assert.strictEqual(viaUrl('living-room'), true, 'URL panel_id is a valid alias');
assert.strictEqual(viaUrl('panel_x'), true, 'state id still matches alongside URL');

// Alias-only browser: the websocket server may resolve `panel_...` to a
// registered id and deliver a canonical PIN/auth payload. With no registered id
// known locally, auth acts rather than dropping the prompt as foreign. General
// targeted actions still reject the canonical id so non-auth routing remains
// panel-specific.
const aliasOnlyFns = makePanelFns({ statePanelId: 'panel_0e3ko5bl', lsTouch: 'panel_0e3ko5bl' });
assert.strictEqual(aliasOnlyFns.panelMatches('zoe-touch-pi'), false, 'alias-only panel must not match every targeted action');
assert.strictEqual(aliasOnlyFns.panelMatchesAuthTarget('zoe-touch-pi'), true, 'alias-only panel must accept canonical registered auth target');

// Panel with no known identity at all → act rather than silently swallow.
const unknown = makePanelMatches({ statePanelId: '', urlPanelId: null, lsTouch: null, lsPanel: null });
assert.strictEqual(unknown('anything'), true, 'unknown identity → honour the action');
assert.strictEqual(unknown(''), true, 'unknown identity + empty target → honour');

console.log('panelMatches: all assertions passed');

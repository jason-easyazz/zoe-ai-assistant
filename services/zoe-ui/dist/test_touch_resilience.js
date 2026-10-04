#!/usr/bin/env node
/**
 * Estate resilience (UI deep review wave 6, 2026-10-05) — no browser, no network.
 * Empty ≠ failed ≠ unknown (services/zoe-ui/AGENTS.md), and one poll per resource:
 *  - mergeListGroups: every type failing keeps the last good board (never "no lists"
 *    over a deploy restart); a partial failure shows what loaded; auth is reported.
 *  - timersGet: the dock chip, the timer surface and the alarm watcher share ONE
 *    /api/skybridge/timers response inside TIMERS_MEMO_MS; a failure is not memoised.
 *  - loadHA: one in-flight /api/ha/entities load, reused inside HA_MEMO_MS.
 *  - text pins: the dock music chain re-arms on a failed poll; "Music isn't available"
 *    when MA reports available:false; the Sources tab tells auth apart from failure.
 * Run: node services/zoe-ui/dist/test_touch_resilience.js
 */
const fs = require('fs'); const path = require('path'); const assert = require('assert'); const vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');
function fn(name) {
  const m = new RegExp('function\\s+' + name + '\\s*\\(').exec(html); assert(m, 'missing ' + name);
  let i = html.indexOf('{', m.index), d = 0;
  for (; i < html.length; i++) { const c = html[i]; if (c === "'" || c === '"') { const q = c; i++; while (html[i] !== q) { if (html[i] === '\\') i++; i++; } continue; } if (c === '{') d++; else if (c === '}') { d--; if (!d) return html.slice(m.index, i + 1); } }
  throw new Error('unterminated ' + name);
}
let n = 0; const ok = (m) => { n++; console.log('  ok  ' + m); };

// mergeListGroups
{
  const { mergeListGroups } = vm.runInNewContext(fn('mergeListGroups') + '; ({ mergeListGroups })', {});
  const prev = [{ id: 'a', name: 'Shopping' }];
  let m = mergeListGroups([{ ok: false }, { ok: false }, { ok: false }], prev);
  assert(m.allFailed && m.lists === prev && !m.auth); ok('lists: all types failed → keep the last good board (never "no lists" over a 502)');
  m = mergeListGroups([{ ok: true, lists: [{ id: 'b' }] }, { ok: false }], prev);
  assert(!m.allFailed && m.someFailed && m.lists.length === 1 && m.lists[0].id === 'b'); ok('lists: a partial failure shows what loaded and flags the rest');
  m = mergeListGroups([{ ok: false, auth: true }, { ok: false, auth: true }], []);
  assert(m.allFailed && m.auth && m.lists.length === 0); ok('lists: an auth refusal is reported as auth, not as failure');
  assert(/mergeListGroups\(results,_lst\.lists\)/.test(html) && /Couldn’t refresh lists/.test(html) && /tap to retry/.test(html)); ok('lists: wireListsFull routes through mergeListGroups with keep/retry copy');
}
// timersGet memo
{
  let calls = 0; const outcomes = [];
  const ctx = { apiGet: () => { calls++; const o = outcomes.shift(); return o === 'fail' ? Promise.reject(new Error('http 502')) : Promise.resolve({ timers: [] }); } };
  const code = 'var TIMERS_MEMO_MS=4000,_tmMemo={at:0,p:null};\n' + fn('timersGet') + '; ({ timersGet })';
  const { timersGet } = vm.runInNewContext(code, ctx);
  (async () => {
    outcomes.push('ok', 'ok', 'fail', 'ok');
    const t0 = 1000000;
    const a = timersGet(t0), b = timersGet(t0 + 100), c = timersGet(t0 + 3900);
    assert(a === b && b === c && calls === 1); ok('timers: three callers inside 4 s share ONE fetch');
    timersGet(t0 + 4001); assert(calls === 2); ok('timers: after the memo window a new fetch goes out');
    await timersGet(t0 + 9000).catch(() => {}); assert(calls === 3);
    timersGet(t0 + 9100); assert(calls === 4); ok('timers: a FAILED fetch is not memoised — the next caller retries at once');
    assert(/paintDockTimer\(\)\{[\s\S]{0,200}timersGet\(\)/.test(html) && /function paint\(\)\{timersGet\(\)/.test(html) && /function _tAlarmRefresh\(\)\{timersGet\(\)/.test(html)); ok('timers: dock chip, timer surface and alarm watcher all use timersGet');
    // loadHA memo
    {
      let loads = 0; let resolveLoad;
      const hctx = { _ha: { loaded: false }, _loadHA: () => { loads++; return new Promise(r => { resolveLoad = r; }); }, Date };
      const hcode = 'var HA_MEMO_MS=5000,_haAt=0,_haInflight=null;\n' + fn('loadHA') + '; ({ loadHA, state: () => ({_haAt,_haInflight}) })';
      const h = vm.runInNewContext(hcode, hctx);
      let cbs = 0; h.loadHA(() => cbs++); h.loadHA(() => cbs++); h.loadHA(() => cbs++);
      assert(loads === 1); ok('ha: three overlapping callers (dock, rooms, sleep) share ONE in-flight entity load');
      hctx._ha.loaded = true; resolveLoad(); await new Promise(r => setTimeout(r, 10));
      assert(cbs === 3); h.loadHA(() => cbs++); assert(loads === 1 && cbs === 4); ok('ha: a fresh response is reused inside 5 s; every caller still gets its callback');
    }
    // text pins
    assert(/_dockMusicT=setTimeout\(paintDockMusic,5000\);\n    \}\);/.test(html), 'dock music chain must re-arm inside its catch'); ok('dock: a failed now-playing poll re-arms the 5 s chain');
    assert(/var unavailable=!!d&&d\.available===false/.test(html) && /Music isn’t available right now/.test(html)); ok('music: MA available:false is "Music isn’t available", not "Nothing playing"');
    assert(/if\(e&&e\.message==='auth'\)\{\s*host\.innerHTML='<div class="srcnote">Sign in to manage music services\./.test(html) && /Couldn’t load music services/.test(html)); ok('sources: auth refusal and failed load are told apart');
    // viewer mode (touch-ui-executor.js): a non-kiosk, unregistered browser is not a panel
    {
      const execSrc = fs.readFileSync(path.join(__dirname, 'js/touch-ui-executor.js'), 'utf8');
      const m = /function isViewerContext\(/.exec(execSrc); assert(m, 'missing isViewerContext');
      let i = execSrc.indexOf('{', m.index), d = 0; for (; i < execSrc.length; i++) { if (execSrc[i] === '{') d++; else if (execSrc[i] === '}') { d--; if (!d) break; } }
      const { isViewerContext } = vm.runInNewContext(execSrc.slice(m.index, i + 1) + '; ({ isViewerContext })', { URLSearchParams });
      const ls = (o) => ({ getItem: (k) => (k in o ? o[k] : null) });
      assert.strictEqual(isViewerContext('', ls({})), true);
      assert.strictEqual(isViewerContext('', ls({ zoe_touch_panel_id: 'panel_abc12345' })), true);
      assert.strictEqual(isViewerContext('?kiosk=1', ls({})), false);
      assert.strictEqual(isViewerContext('', ls({ zoe_kiosk: '1' })), false);
      assert.strictEqual(isViewerContext('?panel_id=zoe-touch-pi', ls({})), false);
      assert.strictEqual(isViewerContext('', ls({ zoe_panel_id: 'zoe-touch-pi' })), false);
      // Codex (#1861): the session-scoped kiosk flag (auth.js on legacy touch pages) is a panel signal…
      assert.strictEqual(isViewerContext('', ls({}), ls({ zoe_kiosk: '1' })), false);
      // …and a locally generated alias is never a registered id, whether forced in the URL or stored.
      // …and THIS browser's generated alias (the persisted marker) is never a registered id, whether forced or stored —
      // while a registered id that merely LOOKS like one (no marker) is a panel (test_touch_panel_id_precedence.js).
      assert.strictEqual(isViewerContext('?panel_id=panel_abc12345', ls({ zoe_touch_panel_alias_generated: 'panel_abc12345' })), true);
      assert.strictEqual(isViewerContext('', ls({ zoe_panel_id: 'panel_abc12345', zoe_touch_panel_alias_generated: 'panel_abc12345' })), true);
      assert.strictEqual(isViewerContext('?panel_id=weird-alias', ls({ zoe_touch_panel_alias_generated: 'weird-alias' })), true);
      assert.strictEqual(isViewerContext('?panel_id=panel_abcd1234', ls({})), false);
      ok('viewer: a laptop on /touch/home.html is a viewer; kiosk flag (URL, local or session) or a registered panel id makes a panel (a generated alias never does)');
      assert(/if \(state\.viewer\) \{[\s\S]{0,1500}\} else \{[\s\S]{0,300}bindPanel\(\)/.test(execSrc), 'init must gate bind/sync/push/poll on state.viewer');
      assert(/if \(state\.viewer\) \{[\s\S]{0,900}stopServiceWorkerPanelPoll\(\);/.test(execSrc), 'a viewer must STOP a leftover SW panel poll');
      assert(/function stopServiceWorkerPanelPoll\(\)[\s\S]{0,600}STOP_PANEL_POLL/.test(execSrc));
      ok('viewer: init skips panel bind, state sync, action poll, push socket and SW poll — and stops a leftover SW panel poll');
      // Codex (#1861, round 2): js/auth.js on the legacy touch pages must carry the
      // localStorage kiosk flag into sessionStorage BEFORE clearing it, or a kiosk
      // navigating with a bare URL loses every signal before isViewerContext runs.
      const authSrc = fs.readFileSync(path.join(__dirname, 'js/auth.js'), 'utf8');
      const blk = /if \(currentPath\.startsWith\('\/touch\/'\)\) \{([\s\S]{0,400})\} else \{([\s\S]{0,120})\}/.exec(authSrc); assert(blk, 'auth.js kiosk-flag block');
      assert(/sessionStorage\.setItem\('zoe_kiosk', '1'\)/.test(blk[1]) && /localStorage\.setItem\('zoe_kiosk', '1'\)/.test(blk[1]) && !/removeItem\('zoe_kiosk'\)/.test(blk[1]), 'on touch pages auth.js mirrors the kiosk flag and never deletes the estate\'s localStorage copy');
      assert(/localStorage\.removeItem\('zoe_kiosk'\)/.test(blk[2]), 'off touch the stale key is cleared');
      const homeSrc = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');
      assert(/sessionStorage\.getItem\('zoe_kiosk'\)==='1'/.test(homeSrc.slice(0, 6000)), 'the estate bootstrap must honour the session-scoped kiosk flag');
      ok('viewer: the kiosk flag survives bare navigations in both directions (auth.js mirrors, never deletes on touch; the estate bootstrap reads the session copy)');
      // Codex (#1861, round 4): registering the generated alias (touch/settings.html
      // setLocalPanelId after /panels/register) must drop the alias marker, or the
      // now-registered panel reads as a viewer after reload.
      const settingsSrc = fs.readFileSync(path.join(__dirname, 'touch/settings.html'), 'utf8');
      const sl = /function setLocalPanelId\((\w+)\)\s*\{([\s\S]{0,700})/.exec(settingsSrc); assert(sl, 'missing setLocalPanelId');
      assert(/localStorage\.getItem\('zoe_touch_panel_alias_generated'\) === String\(\w+ \|\| ''\)\.trim\(\)\) localStorage\.removeItem\('zoe_touch_panel_alias_generated'\)/.test(sl[2]), 'setLocalPanelId must clear the alias marker when that id becomes registered');
      ok('viewer: registering the generated alias clears the alias marker (touch/settings.html setLocalPanelId)');
      // Codex (#1861, rounds 6+7): a NEW registration must reload so the executor re-classifies
      // this browser as a panel — and it must reload even when the bindings step AFTER the
      // register fails (the id is already persisted), so the reload sits after the catch.
      const sp = /async function savePanelIdentity\(\)[\s\S]*?\n        \}\n/.exec(settingsSrc); assert(sp, 'savePanelIdentity');
      assert(/registeredNow = true;/.test(sp[0]), 'register branch marks registeredNow');
      assert(/catch \(e\) \{[\s\S]*?\n            \}\n[\s\S]{0,900}if \(registeredNow\) \{[\s\S]{0,400}window\.location\.reload\(\)/.test(sp[0]), 'reload must be scheduled AFTER the catch (bindings failure still reloads)');
      assert(!/try \{[\s\S]*?if \(registeredNow\) \{[\s\S]*?\} catch \(e\)/.test(sp[0]), 'reload must not live inside the try');
      ok('viewer: a new registration from Touch Settings reloads so the panel services start (even if bindings fail)');
      // Codex (#1861, round 7): the estate boot mirrors the kiosk flag into sessionStorage too —
      // js/auth.js on a desktop tab removes the shared localStorage copy, and the tab-scoped
      // copy is what keeps the kiosk a kiosk across a bare /touch/*.html navigation.
      assert(/if\(p\.get\('kiosk'\)==='1'\)\{localStorage\.setItem\('zoe_kiosk','1'\);sessionStorage\.setItem\('zoe_kiosk','1'\);\}/.test(homeSrc), 'early inline ?kiosk=1 mirrors to sessionStorage');
      assert(/if\(kiosk\)\{try\{localStorage\.setItem\('zoe_kiosk','1'\);sessionStorage\.setItem\('zoe_kiosk','1'\);\}catch\(e\)\{\}\}/.test(homeSrc), 'boot kiosk=true mirrors to sessionStorage');
      ok('viewer: the estate boot persists the kiosk flag to BOTH storages (another tab cannot declassify the kiosk)');
      // Polish (#1862, Codex round 1): the 48 px finger floor applies to the HIT BOX, not the drawing.
      assert(!/width:\s*44px;\s*height:\s*44px/.test(execSrc), 'executor close buttons must not be 44 px');
      assert((execSrc.match(/width:\s*48px;\s*height:\s*48px;\s*border-radius:\s*50%/g) || []).length >= 2, 'both executor close buttons are 48×48');
      assert(/\.srow2 \.sw::before\{content:'';position:absolute;inset:-4px/.test(homeSrc), 'settings switch hit box is extended to 48 px via ::before');
      ok('polish: executor close buttons are 48×48 and the settings switch hit box is ≥48 px');
      // Polish (#1862, Codex round 2): calendar view pills guarantee 48 px by min-height, and an
      // HA icon outside the local glyph set resolves to its family, never the question mark.
      assert(/\.calviews button\{[^}]*min-height:48px/.test(homeSrc), 'calendar view buttons carry min-height:48px');
      const mm = /  var MDI=\{[\s\S]*?\n  function mdi\(name\)\{[^\n]*\n/.exec(homeSrc); assert(mm, 'MDI map + mdi() not found');
      const { mdi: mdiFn, MDI: mdiMap } = vm.runInNewContext(mm[0] + '; ({ mdi, MDI })', {});
      const q = mdiMap['_'];
      assert(mdiFn('mdi:lamp').includes(mdiMap['mdi:lightbulb-outline']) && !mdiFn('mdi:lamp').includes(q), 'mdi:lamp resolves to the light glyph');
      assert(mdiFn('mdi:power-socket-au').includes(mdiMap['mdi:toggle-switch-outline']), 'an unknown device icon resolves to the switch glyph');
      assert(mdiFn('mdi:fan-speed-3').includes(mdiMap['mdi:fan']) && mdiFn('mdi:tv').includes(mdiMap['mdi:television']), 'fan/tv families resolve');
      assert(mdiFn('').includes(q) && mdiFn(undefined).includes(q), 'only NO icon draws the question mark');
      ok('polish: calendar pills are ≥48 px by min-height; unknown HA icons resolve to a family glyph');
    }
    console.log('estate resilience: ' + n + ' checks passed');
  })().catch((e) => { console.error(e); process.exit(1); });
}

/*
 * Touch home: time/temp alignment contract + the sun/moon that rises and sets beside the temp.
 * No browser, no network: runs the REAL CEL block of touch/home.html (CEL-BEGIN..CEL-END) in a vm sandbox and reads the page's CSS/markup.
 * The measured alignment (ink edges vs the orb) is asserted in a real Chromium by scripts/maintenance/estate_browser_verify.py.
 * Negative control: CEL_SRC=<mutated copy of home.html> (e.g. flip sin to cos, drop the day-shift, move hclock back to left:74px) - the named checks go red.
 * Run: node services/zoe-ui/dist/test_touch_home_sun_moon.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const HOME = fs.readFileSync(process.env.CEL_SRC || path.join(__dirname, 'touch/home.html'), 'utf8');
const m = /\/\/ CEL-BEGIN[\s\S]*?\/\/ CEL-END/.exec(HOME);
assert(m, 'CEL block missing from home.html');
const box = {};
vm.createContext(box);
vm.runInContext(m[0], box);
const pos = (now, sr, ss) => vm.runInContext(`celPos(${now},${sr},${ss})`, box);
const phase = (now) => vm.runInContext(`moonPhase(${now})`, box);
const svg = (f) => vm.runInContext(`moonSvg(${f})`, box);

let n = 0;
function check(name, fn) { fn(); n++; console.log('ok  ' + name); }
const at = (iso) => new Date(iso).getTime();
const SR = at('2026-11-09T05:22:00'), SS = at('2026-11-09T18:41:00');
const near = (a, b, tol, msg) => assert(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);

check('sun: on the horizon at sunrise, highest at solar noon, back on the horizon at sunset', () => {
  const r = pos(SR, SR, SS), noon = pos((SR + SS) / 2, SR, SS), s = pos(SS - 1, SR, SS);
  assert.strictEqual(r.kind, 'sun'); near(r.h, 0, 0.01, 'sunrise h');
  assert.strictEqual(noon.kind, 'sun'); near(noon.h, 1, 0.001, 'noon h');
  near(s.h, 0, 0.01, 'sunset h');
});
check('sun: low in the morning, higher mid-morning, p moves left to right', () => {
  const a = pos(at('2026-11-09T06:30:00'), SR, SS), b = pos(at('2026-11-09T09:30:00'), SR, SS), c = pos(at('2026-11-09T16:30:00'), SR, SS);
  assert(a.h < b.h, 'rising'); assert(c.h < b.h, 'setting'); assert(a.p < b.p && b.p < c.p, 'p increases');
});
check('moon: takes over at sunset, highest at the middle of the night, on the horizon at sunrise', () => {
  const dusk = pos(SS + 60000, SR, SS), mid = pos(SS + ((SR + 86400000) - SS) / 2, SR, SS), pre = pos(at('2026-11-09T05:00:00'), SR, SS);
  assert.strictEqual(dusk.kind, 'moon'); assert(dusk.h < 0.05, 'moon low just after sunset: ' + dusk.h);
  near(mid.h, 1, 0.001, 'moon high at midnight-ish'); assert.strictEqual(mid.kind, 'moon');
  assert.strictEqual(pre.kind, 'moon'); assert(pre.h < 0.2, 'moon low just before sunrise: ' + pre.h);
});
check('the feed may be days stale: sunrise/sunset shift by whole days to bracket now', () => {
  const day = 86400000, now = at('2026-11-12T12:02:00'), p = pos(now, SR, SS);
  assert.strictEqual(p.kind, 'sun'); near(p.h, 1, 0.01, 'noon three days later');
  const q = pos(at('2026-11-12T23:00:00'), SR - 5 * day, SS - 5 * day); assert.strictEqual(q.kind, 'moon');
  const t = pos(at('2026-11-06T12:00:00'), SR, SS); assert.strictEqual(t.kind, 'sun');   // feed from the future
});
check('no feed (0/garbage/inverted): 6am-6pm fallback, never NaN', () => {
  [[0, 0], [NaN, NaN], [SS, SR], [SR, SR + 90000000]].forEach(([a, b]) => {
    const noon = pos(at('2026-11-09T12:00:00'), a, b); assert.strictEqual(noon.kind, 'sun'); near(noon.h, 1, 0.01, 'fallback noon');
    const night = pos(at('2026-11-09T23:30:00'), a, b); assert.strictEqual(night.kind, 'moon');
    assert(!Number.isNaN(noon.p) && !Number.isNaN(night.p));
  });
});
check('h and p always stay in 0..1 (the arc can never leave its band)', () => {
  for (let t = 0; t < 86400000 * 3; t += 600000) { const r = pos(at('2026-11-09T00:00:00') + t, SR, SS); assert(r.h >= 0 && r.h <= 1 && r.p >= 0 && r.p <= 1, 'out of range at +' + t); }
});
check('moon phase from the date alone: new 2026-11-09, full ~2026-11-24, ~29.5 day period', () => {
  const circ = (a, b) => { const d = Math.abs(a - b); return Math.min(d, 1 - d); };
  assert(circ(phase(at('2026-11-09T14:00:00Z')), 0) < 0.03, 'new moon ' + phase(at('2026-11-09T14:00:00Z')));
  assert(circ(phase(at('2026-11-24T14:00:00Z')), 0.5) < 0.04, 'full moon ' + phase(at('2026-11-24T14:00:00Z')));
  assert(circ(phase(at('2026-11-17T00:00:00Z')), 0.25) < 0.05, 'first quarter');
  for (let i = 0; i < 40; i++) { const f = phase(at('2026-01-01T00:00:00Z') + i * 86400000 * 7); assert(f >= 0 && f < 1); }
});
check('moon drawing: new = no lit area, full = whole disc, waxing lit LEFT (southern sky), waning lit RIGHT', () => {
  const waxCrescent = svg(0.1), full = svg(0.5), waneCrescent = svg(0.9), newMoon = svg(0);
  assert(/A10 10 0 0 0 12 22/.test(waxCrescent), 'waxing limb is the left half: ' + waxCrescent);
  assert(/A10 10 0 0 1 12 22/.test(waneCrescent), 'waning limb is the right half: ' + waneCrescent);
  assert(/A10\.00 10 0 0 1 12 2Z/.test(full) || /A10\.00 10/.test(full), 'full: terminator is a full-width half ellipse: ' + full);
  assert(/A10\.00 10 0 0 \d 12 2Z/.test(newMoon), 'new: terminator hugs the limb (zero area): ' + newMoon);
  const quarter = svg(0.25); assert(/A0\.00 10/.test(quarter), 'quarter: straight terminator: ' + quarter);
  assert(svg(0.75) !== svg(0.25));
});
check('sun colour: amber at the horizon, pale gold when high', () => {
  const lo = vm.runInContext('sunColor(0)', box), hi = vm.runInContext('sunColor(1)', box);
  assert.strictEqual(lo, 'rgb(255,138,61)'); assert.strictEqual(hi, 'rgb(255,214,102)');
});

// ---- the page contract (CSS + markup), pinned so a later edit cannot quietly undo the alignment ----
const css = (re, what) => { const x = re.exec(HOME); assert(x, what + ' not found'); return x[1]; };
check('time block starts at the orb\'s left edge: .hclock left == #orb left', () => {
  const orbLeft = css(/#orb\{position:absolute;left:(\d+)px/, '#orb left'), clockLeft = css(/\.hfull \.hclock\{position:absolute;left:(\d+)px/, '.hclock left');
  assert.strictEqual(clockLeft, orbLeft);
});
check('temp mirrors it: .htemp right == .hclock left, and nothing re-overrides it (the old right:120px "home cog" rule is gone)', () => {
  const clockLeft = css(/\.hfull \.hclock\{position:absolute;left:(\d+)px/, '.hclock left');
  const rights = [...HOME.matchAll(/\.hfull \.htemp\{[^}]*?right:(\d+)px/g)].map((x) => x[1]);
  assert.deepStrictEqual(rights, [clockLeft], 'exactly one .htemp right rule: ' + rights);
});
check('the sun/moon sits in the temp row, left of the number, inside a band the icon cannot leave', () => {
  const row = /class="hrow"><div class="hcel" id="hCel"[^>]*><div class="hcelI" id="hCelI"><\/div><\/div><div class="n">/.test(HOME);
  assert(row, 'hcel must precede .n inside .hrow');
  const track = +css(/\.hfull \.hcel\{[^}]*?width:(\d+)px/, 'track width'), icon = +css(/\.hfull \.hcelI\{[^}]*?width:(\d+)px/, 'icon width');
  const travel = +css(/\(pos\.p\*(\d+)\)\.toFixed/, 'x travel');
  assert(travel + icon <= track, `icon (${icon}) + x travel (${travel}) must fit the track (${track})`);
  const trackH = +css(/\.hfull \.hcel\{[^}]*?height:(\d+)px/, 'track height'), iconH = +css(/\.hfull \.hcelI\{[^}]*?height:(\d+)px/, 'icon height');
  const ym = /\((\d+)-(\d+)\*pos\.h\)\.toFixed/.exec(HOME); assert(ym, 'y formula'); const y0 = +ym[1], amp = +ym[2];
  assert(y0 - amp >= 0, 'peak stays inside the band top'); assert(y0 + iconH / 2 <= trackH + 1, 'horizon seat stays on the track');
});
check('motion is subtle and re-seated every few minutes; first seating never animates', () => {
  assert(/\.hcelI\[data-mv\]\{transition:transform \d+s linear/.test(HOME), 'transition only after the first seating');
  assert(!/\.hcelI\{[^}]*transition/.test(HOME), 'the base rule must not animate (a visit would replay the sunrise)');
  assert(/Date\.now\(\)-_cel\.at>=180000/.test(HOME), '3-minute re-seat');
  assert(/prefers-reduced-motion: reduce\)\{[^}]*transition-duration:\.001ms!important/.test(HOME), 'global reduced-motion rule still present');
});
check('home does not also draw the big canvas sun/moon (one sun, not two)', () => {
  assert(/if\(_cur!=='home'\)ctx\.drawImage\(nt\?sp\.moon:sp\.sun/.test(HOME));
});
check('weather feed fields used are the ones the router already returns (sunrise/sunset), no new API', () => {
  assert(/Date\.parse\(w\.sunrise\)/.test(HOME) && /Date\.parse\(w\.sunset\)/.test(HOME));
  assert(!/sunrise-sunset\.org|api\.met\.no|usno\.navy/.test(HOME));
});
console.log(`${n} checks passed`);

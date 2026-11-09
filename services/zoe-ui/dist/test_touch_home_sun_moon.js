/*
 * Touch home: the time/temp edge alignment contract + the BIG sun/moon that crosses the whole panel on the real sunrise/sunset arc.
 * No browser, no network: runs the REAL CEL block of touch/home.html (CEL-BEGIN..CEL-END) in a vm sandbox and reads the page's CSS/markup/canvas code.
 * The measured alignment (ink edges vs the orb) is asserted in a real Chromium by scripts/maintenance/estate_browser_verify.py.
 * Negative control: CEL_SRC=<mutated copy of home.html> (e.g. flip sin to cos, drop the day-shift, shrink the apex, add the small icon back) - the named checks go red.
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
const xy = (p, h) => vm.runInContext(`celXY(${p},${h})`, box);
const phase = (now) => vm.runInContext(`moonPhase(${now})`, box);
const mpath = (f) => vm.runInContext(`moonPath(${f})`, box);

let n = 0;
function check(name, fn) { fn(); n++; console.log('ok  ' + name); }
const at = (iso) => new Date(iso).getTime();
const SR = at('2026-11-09T05:22:00'), SS = at('2026-11-09T18:41:00');
const near = (a, b, tol, msg) => assert(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);
const W = 1280, H = 720;
// The old static sprites (kept verbatim for every other screen): sun sprite 360 px, solid disc to 25% of its radius; moon sprite 240 px.
const SUN_R = 0.25 * 180;

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
check('h and p always stay in 0..1', () => {
  for (let t = 0; t < 86400000 * 3; t += 600000) { const r = pos(at('2026-11-09T00:00:00') + t, SR, SS); assert(r.h >= 0 && r.h <= 1 && r.p >= 0 && r.p <= 1, 'out of range at +' + t); }
});

// ---- the full-screen arc ----
check('arc: rises from BELOW the bottom edge at the LEFT of the panel (sunrise) - disc fully off screen', () => {
  const a = xy(0, 0); assert(a.y - SUN_R >= H, `disc must be below the bottom edge: y=${a.y}`); assert(a.x > 0 && a.x < W * 0.12, 'left side: x=' + a.x);
});
check('arc: sets below the bottom edge at the RIGHT of the panel (sunset)', () => {
  const a = xy(1, 0); assert(a.y - SUN_R >= H, `disc below the bottom edge: y=${a.y}`); assert(a.x < W && a.x > W * 0.88, 'right side: x=' + a.x);
});
check('arc: apex at solar noon is the TOP-MIDDLE, fully on screen, at or above the old static spot (y 170)', () => {
  const a = xy(0.5, 1); near(a.x, W / 2, 20, 'noon x is the middle'); assert(a.y - SUN_R >= 0, 'whole disc on screen: y=' + a.y); assert(a.y <= 170, 'at or above the old sun spot: y=' + a.y);
  assert(a.y <= H * 0.25, 'near the top: ' + a.y);
});
check('arc: x is linear in the day fraction and spans nearly the full width; y is the symmetric sin arc', () => {
  let prev = null;
  for (let i = 0; i <= 20; i++) { const p = i / 20, a = xy(p, Math.sin(Math.PI * p)); if (prev) assert(a.x > prev.x, 'x increases');
    const mirror = xy(1 - p, Math.sin(Math.PI * (1 - p))); near(a.y, mirror.y, 0.5, 'symmetric about noon'); prev = a; }
  const a0 = xy(0, 0), a1 = xy(1, 0); assert(a1.x - a0.x >= W * 0.85, 'spans nearly the full width: ' + (a1.x - a0.x));
  near(xy(0.25, 0).x - a0.x, (a1.x - a0.x) / 4, 0.5, 'linear x');
});
check('the body is drawn at the arc EVERY frame from the real feed, so a first seating cannot replay a sunrise and the dusk handover cannot slide', () => {
  assert(/cp=celPos\(now,_cel\.sr,_cel\.ss\),xy=celXY\(cp\.p,cp\.h\)/.test(HOME));
  assert(/if\(cp\.kind==='sun'&&sunOn\)ctx\.drawImage\(sp\.sun,xy\.x-180,xy\.y-180\)/.test(HOME), 'sun drawn centred on the arc point at its OLD sprite size (360 px)');
  assert(/cp\.kind==='moon'&&\(moonOn\|\|bodyNight\)\)ctx\.drawImage\(moonSprite\(moonPhase\(now\)\),xy\.x-120,xy\.y-120\)/.test(HOME), 'moon drawn centred on the arc point');
});
check('size: the sun sprite is exactly the old one (360 px, same stops) and the moon disc matches the sun disc', () => {
  assert(/sun:mk\(360,360,function\(x\)\{var g=x\.createRadialGradient\(180,180,0,180,180,180\);g\.addColorStop\(0,'rgba\(255,246,208,1\)'\);g\.addColorStop\(\.25,'rgba\(255,240,190,1\)'\);g\.addColorStop\(\.29,'rgba\(255,214,120,\.55\)'\);g\.addColorStop\(\.6,'rgba\(255,200,110,\.16\)'\);g\.addColorStop\(1,'rgba\(255,200,110,0\)'\)/.test(HOME), 'old sun sprite untouched');
  const mr = +/x\.fillStyle='rgba\(150,166,214,\.28\)';x\.beginPath\(\);x\.arc\(120,120,(\d+),0,6\.29\)/.exec(HOME)[1], sc = +/x\.scale\(([\d.]+),/.exec(HOME)[1];
  near(mr, SUN_R, 6, 'moon disc radius vs sun disc radius'); near(10 * sc, SUN_R, 6, 'lit moon radius vs sun disc radius');
});
check('the body is on the #cv canvas (behind every panel element), and the small icon beside the temp is gone', () => {
  assert(/<canvas id="cv"><\/canvas>\s*<div id="glow">/.test(HOME));
  assert(!/hCelI|class="hcel"|id="hCel"|class="hrow"|\.hcel/.test(HOME), 'no small icon element/CSS left');
});

check('white night text keeps a tight dark halo so the moon passing behind it stays readable', () => {
  assert(/#stage:not\(\.wxlight\) \.hfull \.hclock \.ht,[^{]*\{text-shadow:0 0 3px rgba\(4,7,24,\.9\)/.test(HOME));
});

// ---- the moon's phase ----
check('moon phase from the date alone: new 2026-11-09, full ~2026-11-24, ~29.5 day period', () => {
  const circ = (a, b) => { const d = Math.abs(a - b); return Math.min(d, 1 - d); };
  assert(circ(phase(at('2026-11-09T14:00:00Z')), 0) < 0.03, 'new moon ' + phase(at('2026-11-09T14:00:00Z')));
  assert(circ(phase(at('2026-11-24T14:00:00Z')), 0.5) < 0.04, 'full moon ' + phase(at('2026-11-24T14:00:00Z')));
  assert(circ(phase(at('2026-11-17T00:00:00Z')), 0.25) < 0.05, 'first quarter');
  for (let i = 0; i < 40; i++) { const f = phase(at('2026-01-01T00:00:00Z') + i * 86400000 * 7); assert(f >= 0 && f < 1); }
});
// Rasterise the lit path the way SVG does (two half-arcs between (12,2) and (12,22)) and measure the REAL lit area and which side it is on.
function litArea(d) {
  const mm = /^M12 2A10 10 0 0 ([01]) 12 22A([\d.]+) 10 0 0 ([01]) 12 2Z$/.exec(d);
  assert(mm, 'unexpected path shape: ' + d);
  const sl = +mm[1], rx = +mm[2], st = +mm[3], pts = [];
  const side = (sweep, down) => (down ? (sweep ? 1 : -1) : (sweep ? -1 : 1));   // clockwise from the top is the right side; from the bottom, the left
  for (let i = 0; i <= 200; i++) { const u = (Math.PI * i) / 200; pts.push([12 + side(sl, true) * 10 * Math.sin(u), 12 - 10 * Math.cos(u)]); }
  for (let i = 0; i <= 200; i++) { const u = (Math.PI * i) / 200; pts.push([12 + side(st, false) * rx * Math.sin(u), 12 + 10 * Math.cos(u)]); }
  let a = 0, cx = 0; for (let i = 0; i < pts.length; i++) { const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length]; a += x1 * y2 - x2 * y1; cx += (x1 + x2) * (x1 * y2 - x2 * y1); }
  a /= 2; return { area: Math.abs(a), cx: cx / (6 * a) };   // centroid x: < 12 means the lit part is on the LEFT
}
check('moon drawing: the lit AREA is the real illuminated fraction, on the left while waxing (southern sky), right while waning', () => {
  const disc = Math.PI * 100;
  [0, 0.05, 0.1, 0.2, 0.25, 0.35, 0.45, 0.5, 0.55, 0.65, 0.75, 0.85, 0.95].forEach((f) => {
    const want = (1 - Math.cos(2 * Math.PI * f)) / 2, got = litArea(mpath(f)).area / disc;
    near(got, want, 0.01, 'lit fraction at phase ' + f);
  });
  assert(litArea(mpath(0)).area < 1, 'new moon: nothing lit'); near(litArea(mpath(0.5)).area / disc, 1, 0.005, 'full moon: whole disc');
  [0.1, 0.25, 0.4].forEach((f) => assert(litArea(mpath(f)).cx < 12, 'waxing lights the left at ' + f));
  [0.6, 0.75, 0.9].forEach((f) => assert(litArea(mpath(f)).cx > 12, 'waning lights the right at ' + f));
});

// ---- the page contract (CSS + markup), pinned so a later edit cannot quietly undo the alignment ----
const css = (re, what) => { const x = re.exec(HOME); assert(x, what + ' not found'); return x[1]; };
check('time block starts at the orb\'s left edge: .hclock left == #orb left', () => {
  const orbLeft = css(/#orb\{position:absolute;left:(\d+)px/, '#orb left'), clockLeft = css(/\.hfull \.hclock\{position:absolute;left:(\d+)px/, '.hclock left');
  assert.strictEqual(clockLeft, orbLeft);
});
check('temp mirrors it: .htemp right == .hclock left, and nothing re-overrides it', () => {
  const clockLeft = css(/\.hfull \.hclock\{position:absolute;left:(\d+)px/, '.hclock left');
  const rights = [...HOME.matchAll(/\.hfull \.htemp\{[^}]*?right:(\d+)px/g)].map((x) => x[1]);
  assert.deepStrictEqual(rights, [clockLeft], 'exactly one .htemp right rule: ' + rights);
});
check('every other screen keeps the old static sprites; reduced-motion keeps the still-sky repaint', () => {
  assert(/else ctx\.drawImage\(nt\?sp\.moon:sp\.sun,bx-\(nt\?120:180\),by-\(nt\?120:180\)\)/.test(HOME));
  assert(/if\(RM\)\{setTimeout\(function\(\)\{requestAnimationFrame\(frame\);\},1000\);\}/.test(HOME), 'reduced motion repaints once a second');
});
check('weather feed fields used are the ones the router already returns (sunrise/sunset), no new API', () => {
  assert(/Date\.parse\(w\.sunrise\)/.test(HOME) && /Date\.parse\(w\.sunset\)/.test(HOME));
  assert(!/sunrise-sunset\.org|api\.met\.no|usno\.navy/.test(HOME));
});
console.log(`${n} checks passed`);

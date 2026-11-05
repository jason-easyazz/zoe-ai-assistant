/*
 * Touch home sky: day vs night comes from the weather icon suffix (...d / ...n) for EVERY scene.
 * Bug (owner, 7.10am): a daytime partly-cloudy morning ('02d') showed stars on a near-black cloud sky.
 * No browser, no network: runs the REAL wxSky()/owmIcon() block of touch/home.html (WXSKY-BEGIN..END) in a vm sandbox.
 * Negative control: SKY_SRC=<mutated copy of home.html>, e.g. make wxSky treat every code as night - the named checks go red.
 * Run: node services/zoe-ui/dist/test_touch_sky_day_night.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const HOME = fs.readFileSync(process.env.SKY_SRC || path.join(__dirname, 'touch/home.html'), 'utf8');
const m = /\/\/ WXSKY-BEGIN[\s\S]*?\/\/ WXSKY-END/.exec(HOME);
assert(m, 'WXSKY block missing from home.html');
const box = {};
vm.createContext(box);
vm.runInContext("var DUSK='DUSKBG';var OWM_ICON={'01':'sun','02':'cloud','03':'cloud','04':'cloud','09':'rain','10':'rain','11':'rain','13':'cloud','50':'cloud'};" + m[0], box);
const sky = (c) => vm.runInContext('wxSky(' + JSON.stringify(c) + ')', box);
const icon = (c) => vm.runInContext('owmIcon(' + JSON.stringify(c) + ')', box);

let n = 0;
function check(name, fn) { fn(); n++; console.log('ok  ' + name); }
const lum = (bg) => { const hex = (bg.match(/#[0-9a-f]{6}/gi) || []).map((h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16)));
  return hex.map((c) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]).reduce((a, b) => a + b, 0) / hex.length; };

check("'02d' partly cloudy morning: no stars, light overcast bg, wxlight", () => {
  const s = sky('02d');
  assert.strictEqual(s.stars, 0); assert.strictEqual(s.light, true); assert.strictEqual(s.rain, false);
  assert.strictEqual(s.bg, vm.runInContext('WXBG.cloudDay', box));
  assert(lum(s.bg) > 120, 'daytime cloud bg must not be near-black: ' + lum(s.bg));
});
check("'02n' night cloud: dark bg, dimmed stars, not light", () => {
  const s = sky('02n');
  assert(s.stars > 0 && s.stars < 1); assert.strictEqual(s.light, false); assert(lum(s.bg) < 90);
});
check("'03d'/'04d'/'50d' all daytime cloud (no stars)", () => {
  ['03d', '04d', '50d'].forEach((c) => { assert.strictEqual(sky(c).stars, 0, c); assert.strictEqual(sky(c).light, true, c); });
});
check("'10d' rain day: rain drops, no stars, light bg", () => {
  const s = sky('10d');
  assert.strictEqual(s.rain, true); assert.strictEqual(s.stars, 0); assert.strictEqual(s.light, true);
  assert.strictEqual(s.canvas, true); assert(lum(s.bg) > 110, 'rain day bg too dark: ' + lum(s.bg));
});
check("'10n' rain night: drops, no stars, dark bg", () => {
  const s = sky('10n');
  assert.strictEqual(s.rain, true); assert.strictEqual(s.stars, 0); assert.strictEqual(s.light, false); assert(lum(s.bg) < 90);
});
check("'01n' clear night: stars full, canvas on, dark", () => {
  const s = sky('01n');
  assert.strictEqual(s.stars, 1); assert.strictEqual(s.canvas, true); assert.strictEqual(s.light, false); assert.strictEqual(s.rain, false);
});
check("'01d' clear day: no stars, light, plain sun with no cloud", () => {
  const s = sky('01d');
  assert.strictEqual(s.sun, true); assert.strictEqual(s.clouds, 0); assert.strictEqual(s.canvas, true); assert.strictEqual(s.stars, 0); assert.strictEqual(s.light, true);
});
check('unknown/empty code keeps the dusk sky unchanged (stars + glow)', () => {
  ['', null, 'zz', undefined].forEach((c) => { const s = sky(c); assert.strictEqual(s.scene, 'dusk'); assert.strictEqual(s.glow, true); assert.strictEqual(s.stars, 1); assert.strictEqual(s.bg, 'DUSKBG'); });
});
check('no daytime icon ever shows stars; night rain shows none', () => {
  ['01', '02', '03', '04', '09', '10', '11', '13', '50'].forEach((b) => {
    assert.strictEqual(sky(b + 'd').stars, 0, b + 'd');
    if (['09', '10', '11', '13'].includes(b)) assert.strictEqual(sky(b + 'n').stars, 0, b + 'n');
  });
});
check('hourly icons: sun->moon at night; daytime cloud/rain keep their glyph', () => {
  assert.strictEqual(icon('01n'), 'moon'); assert.strictEqual(icon('01d'), 'sun');
  assert.strictEqual(icon('02d'), 'cloud'); assert.strictEqual(icon('10d'), 'rain');
});
check('both surfaces (home + weather) route through wxApplySky; no scene!==day star gate left', () => {
  assert((HOME.match(/wxApplySky\(wxSky\(w\.icon\)\)/g) || []).length === 2);
  assert(!/wxOn=\(scene!==/.test(HOME));
  assert(/#stage\.wxlight\.wxovc \.whours \.hh \.ic/.test(HOME), 'overcast-day hourly icons need a readable colour');
});
check("'02d'/'03d': sun behind 2/3 drifting clouds; no moon, no stars", () => {
  const a = sky('02d'), b = sky('03d');
  assert.strictEqual(a.sun, true); assert.strictEqual(a.clouds, 2); assert.strictEqual(b.sun, true); assert.strictEqual(b.clouds, 3);
  assert.strictEqual(a.moon, false); assert.strictEqual(a.stars, 0); assert.strictEqual(a.canvas, true);
});
check("'04d' overcast / '50d' fog / rain: NO sun", () => {
  ['04d', '50d', '09d', '10d', '11d', '13d', '04n', '50n', '10n'].forEach((c) => { const s = sky(c); assert.strictEqual(s.sun, false, c); assert.strictEqual(s.moon, false, c); assert.strictEqual(s.clouds, 0, c); });
});
check("'02n'/'03n': moon partly behind cloud with dimmed stars, no sun", () => {
  ['02n', '03n'].forEach((c) => { const s = sky(c); assert.strictEqual(s.moon, true, c); assert.strictEqual(s.sun, false, c); assert(s.clouds >= 2, c); assert(s.stars > 0 && s.stars < 1, c); });
});
check('no sun at night, no moon by day, over every code', () => {
  ['01', '02', '03', '04', '09', '10', '11', '13', '50'].forEach((b) => { assert.strictEqual(sky(b + 'n').sun, false, b + 'n'); assert.strictEqual(sky(b + 'd').moon, false, b + 'd'); });
});
check('cloud drift: moves with time, is static under reduced motion', () => {
  const d = (t, rm) => vm.runInContext('wxDrift(' + t + ',0,' + rm + ')', box);
  assert(Math.abs(d(5000, false) - d(0, false)) > 1, 'clouds must drift');
  assert(Math.abs(d(5000, false)) <= 60, 'drift stays small (clear of the clock/temperature)');
  assert.strictEqual(d(5000, true), 0); assert.strictEqual(d(99999, true), 0);
});
check('sprites are pre-rendered once (no per-frame gradients) and the frame honours RM', () => {
  assert(/_skySpr=\{/.test(HOME) && /if\(_skySpr\)return _skySpr/.test(HOME));
  const fr = /function frame\(t\)\{[\s\S]*?requestAnimationFrame\(frame\);\} \}/.exec(HOME);
  assert(fr, 'frame() not found');
  assert(!/createRadialGradient[\s\S]*sunOn/.test(fr[0].split('if(sunOn||moonOn)')[1] || ''), 'sun/moon branch must not build gradients per frame');
  assert(/wxDrift\(t,cc\.ph,RM\)/.test(fr[0]), 'clouds must pass the RM flag');
});
console.log('\n' + n + ' checks passed');

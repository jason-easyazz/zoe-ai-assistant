/*
 * Controlled test for the skybridge.js timer engine. Extracts the REAL timer
 * functions and runs them against a fake clock + DOM/audio shims, so the
 * countdown / fire / alarm / acknowledge / reload-restore logic is verified
 * without a live kiosk.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'touch/js/skybridge.js'), 'utf8');

function extract(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, 'missing function ' + name);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error('unbalanced braces for ' + name);
}

const FN_NAMES = ['_timerSel', '_fmtClock', 'persistTimers', 'ensureTimerTicking',
  'registerTimer', 'removeTimer', 'timerTick', 'fireTimer', 'startAlarm', 'stopAlarm',
  'acknowledgeRingingTimers', 'restoreTimers'];
const body = FN_NAMES.map(extract).join('\n');

// ---- harness scope (names referenced by the extracted functions) ----
let now = 1_000_000_000_000;
const realDateNow = Date.now;
let intervals = [];
function setInterval(fn, ms) { const t = { fn, ms }; intervals.push(t); return t; }
function clearInterval(t) { intervals = intervals.filter(x => x !== t); }

const TIMERS_KEY = 'sky_active_timers';
const activeTimers = new Map();
let timerTickHandle = null;
let audioCtx = null;
let alarmTimer = null;

const store = {};
const localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
const CSS = { escape: s => s };
const window = { CSS, AudioContext: function () {
  return { state: 'running', currentTime: 0, resume() {},
    createOscillator: () => ({ type: '', frequency: { value: 0 }, connect() {}, start() {}, stop() {} }),
    createGain: () => ({ gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }),
    destination: {} };
} };

let addCardCalls = [];
let voiceText = '';
const els = { cards: { _onScreen: false, children: { length: 0 },
  querySelector() { return null; } } };          // default: nothing on screen
function addCard(card) { addCardCalls.push(card); }
function setVoiceLayerText(t) { voiceText = t; }
function clearIdleTimer() {}
function clearCards() {}

global.Date.now = () => now;
try { new Function(body); } catch (e) { throw new Error('extracted timer source did not parse: ' + e.message); }
eval(body);

function reset() {
  now = 1_000_000_000_000; intervals = []; activeTimers.clear();
  timerTickHandle = null; audioCtx = null; alarmTimer = null;
  for (const k of Object.keys(store)) delete store[k];
  addCardCalls = []; voiceText = '';
}

let pass = 0;
function ok(label, cond) { assert(cond, 'FAIL: ' + label); process.stdout.write('  ok  - ' + label + '\n'); pass++; }

process.stdout.write('skybridge timer engine\n');

// 1) register persists + starts ticking
reset();
registerTimer('t1', 'Pasta', now + 5000, 5);
ok('registered timer is tracked', activeTimers.size === 1 && activeTimers.get('t1').label === 'Pasta');
ok('persisted to localStorage', JSON.parse(store['sky_active_timers'])[0].id === 't1');
ok('tick interval armed', timerTickHandle !== null);

// 2) tick before expiry does not fire
reset();
registerTimer('t1', 'Pasta', now + 5000, 5);
now += 3000; timerTick();
ok('not ringing before expiry', activeTimers.get('t1').ringing === false && alarmTimer === null);

// 3) at expiry it fires: rings, alarms, and surfaces a card (none was on screen)
reset();
registerTimer('t1', 'Eggs', now + 5000, 5);
now += 5000; timerTick();
ok('fires at zero (ringing)', activeTimers.get('t1').ringing === true);
ok('alarm started', alarmTimer !== null);
ok('surfaced a ringing card when none was visible', addCardCalls.length === 1 && addCardCalls[0].props.status === 'expired');
ok('announced time is up', /time's up/i.test(voiceText));

// 4) acknowledging a ringing timer stops the alarm and clears it
reset();
registerTimer('t1', 'Eggs', now + 1000, 5);
now += 1000; timerTick();           // ring
const ack = acknowledgeRingingTimers();
ok('acknowledge returns true', ack === true);
ok('alarm stopped on acknowledge', alarmTimer === null);
ok('timer cleared after acknowledge', activeTimers.size === 0);

// 5) restore drops expired and resumes a still-running timer
reset();
store['sky_active_timers'] = JSON.stringify([
  { id: 'live', label: 'Soup', expires: now + 10000, duration: 60 },
  { id: 'dead', label: 'Old', expires: now - 10000, duration: 60 },
]);
restoreTimers();
ok('restore keeps only the live timer', activeTimers.size === 1 && activeTimers.has('live'));
ok('restore re-shows the running card', addCardCalls.some(c => c.props.timer_id === 'live' && c.props.status === 'running'));

global.Date.now = realDateNow;
process.stdout.write('\n' + pass + ' checks passed\n');

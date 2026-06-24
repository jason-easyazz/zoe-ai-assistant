/*
 * Controlled test for skybridge.js turn-resilience (stall watchdog + recovery).
 * Extracts the REAL function bodies from skybridge.js and runs them against a
 * fake clock + DOM/voice shims, so a hung turn / interrupted page-fade is
 * verified to self-heal without touching the live kiosk.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, 'touch/js/skybridge.js'), 'utf8');

// Brace-match a top-level `function name(...) { ... }` out of the source.
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

const body = ['clearStallWatchdog', 'armStallWatchdog', 'recoverToAmbient'].map(extract).join('\n');

// ---- harness scope (these names are referenced by the extracted functions) ----
let now = 0, timers = [];
function setTimeout(fn, ms) { const t = { fn, at: now + ms }; timers.push(t); return t; }
function clearTimeout(t) { timers = timers.filter(x => x !== t); }
function advance(ms) {
  now += ms;
  const due = timers.filter(t => t.at <= now).sort((a, b) => a.at - b.at);
  timers = timers.filter(t => t.at > now);
  due.forEach(t => t.fn());
}
const STALL_WATCHDOG_MS = 25000;
const MAX_STALL_DEFERRALS = 3;
let stallWatchdog = null;
let stallDeferrals = 0;
let orbState = 'ambient';
const console = { warn() {} };
const document = { body: { style: { opacity: '' } } };
let voice = { isRecording: false, speaking: false, serverBusy: false };
let setStateCalls = [];
function setState(s) { setStateCalls.push(s); orbState = s; if (s === 'ambient') { stallDeferrals = 0; clearStallWatchdog(); } else armStallWatchdog(); }

// Fail loudly if extraction captured a broken span (e.g. a future brace-in-string
// edit) instead of silently eval-ing wrong code and passing.
try { new Function(body); } catch (e) { throw new Error('extracted watchdog source did not parse: ' + e.message); }
eval(body); // defines clearStallWatchdog / armStallWatchdog / recoverToAmbient in this scope

function reset() { now = 0; timers = []; stallWatchdog = null; stallDeferrals = 0; orbState = 'ambient'; document.body.style.opacity = ''; voice = { isRecording: false, speaking: false, serverBusy: false }; setStateCalls = []; }

let pass = 0;
function ok(label, cond) { assert(cond, 'FAIL: ' + label); console_log('  ok  - ' + label); pass++; }
function console_log(s) { process.stdout.write(s + '\n'); }

console_log('skybridge resilience (stall watchdog + recovery)');

// 1) hung turn: thinking, then 25s of silence → recovers to ambient
reset();
setState('thinking'); voice.serverBusy = true;
advance(STALL_WATCHDOG_MS + 1);
ok('hung turn recovers to ambient after stall window', orbState === 'ambient' && setStateCalls[setStateCalls.length - 1] === 'ambient');
ok('recovery clears serverBusy', voice.serverBusy === false);

// 2) stuck invisible body (opacity 0 from an aborted nav) is restored on recovery
reset();
setState('thinking'); document.body.style.opacity = '0';
advance(STALL_WATCHDOG_MS + 1);
ok('stuck body opacity:0 is cleared on recovery', document.body.style.opacity === '');

// 3) liveness: an inbound event re-arms, so a slow-but-alive turn is NOT cut off
reset();
setState('thinking');
advance(20000);            // 20s in
armStallWatchdog();        // an inbound event arrives (handleVoiceEvent re-arms)
advance(20000);            // +20s (40s total, but only 20s since last event)
ok('slow-but-alive turn not cut off (re-armed)', orbState === 'thinking');
advance(6000);             // now 26s of silence since the event
ok('recovers once truly silent past the window', orbState === 'ambient');

// 4) active mic/TTS is not a hang — recover defers instead of cutting off
reset();
setState('responding'); voice.speaking = true;
advance(STALL_WATCHDOG_MS + 1);
ok('does not interrupt active TTS playback', orbState === 'responding');
voice.speaking = false;
advance(STALL_WATCHDOG_MS + 1);
ok('recovers once playback finishes and goes silent', orbState === 'ambient');

// 5) reaching ambient normally clears the watchdog (no spurious later fire)
reset();
setState('thinking');
setState('ambient');
ok('ambient clears the watchdog', stallWatchdog === null);
advance(STALL_WATCHDOG_MS + 1);
ok('no spurious recovery after a clean turn', setStateCalls.filter(s => s === 'ambient').length === 1);

// 6) a STUCK voice flag (e.g. socket crash leaving speaking=true) must not block
//    recovery forever — recover unconditionally after MAX_STALL_DEFERRALS.
reset();
setState('thinking'); voice.speaking = true; // never cleared
for (let k = 0; k <= MAX_STALL_DEFERRALS; k++) advance(STALL_WATCHDOG_MS + 1);
ok('stuck voice flag still recovers after max deferrals', orbState === 'ambient');

// ---- voice-error typing-fallback auto-recovery ----
// The panel drops into a typing fallback when voice errors/disconnects. Once the
// socket reconnects (handleVoiceEvent 'ready'), it must return to voice instead of
// latching on "Type here while voice reconnects..." forever.
(function testFallbackRecovery() {
  const recoverSrc = extract('recoverFromVoiceError');
  try { new Function(recoverSrc); } catch (e) { throw new Error('recoverFromVoiceError did not parse: ' + e.message); }

  let voiceErrorFallback = false;
  let commandFallbackOpen = false;
  let micOk = true;
  const classes = new Set();
  const els = { input: { value: '', placeholder: '' } };
  const document = { body: { classList: {
    add: c => classes.add(c), remove: c => classes.delete(c),
    contains: c => classes.has(c), toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); }
  } } };
  function canUseMicrophone() { return micOk; }
  function syncVoiceFallbackState() {}
  function setStatus() {}
  eval(recoverSrc); // defines recoverFromVoiceError closing over the shims above

  function enterErrorFallback() {
    voiceErrorFallback = true; commandFallbackOpen = true;
    classes.add('sky-command-open'); classes.add('sky-typing-fallback');
    els.input.placeholder = 'Type here while voice reconnects...';
  }

  // a) voice back, mic ok, input empty → recover to voice
  classes.clear(); micOk = true; els.input.value = ''; enterErrorFallback();
  recoverFromVoiceError();
  ok('reconnect clears the stale typing fallback',
     !classes.has('sky-typing-fallback') && !classes.has('sky-command-open') && voiceErrorFallback === false);

  // b) user has started typing → don't yank the keyboard away
  classes.clear(); micOk = true; enterErrorFallback(); els.input.value = 'add milk to the list';
  recoverFromVoiceError();
  ok('does not interrupt a half-typed message',
     classes.has('sky-typing-fallback') && voiceErrorFallback === true);

  // b2) deferred while typing, then the user submits (input cleared) → recover.
  //     Mirrors the form-submit handler calling recoverFromVoiceError() after it
  //     clears the input, so the panel doesn't latch in the fallback forever.
  classes.clear(); micOk = true; enterErrorFallback(); els.input.value = 'add milk to the list';
  recoverFromVoiceError();                       // 'ready' fired mid-typing → deferred
  ok('stays in fallback while still typing', classes.has('sky-typing-fallback'));
  els.input.value = '';                          // submit handler clears the input
  recoverFromVoiceError();                       // submit handler retries
  ok('recovers after the message is submitted',
     !classes.has('sky-typing-fallback') && voiceErrorFallback === false);

  // c) mic genuinely unavailable → keep typing as the primary input
  classes.clear(); micOk = false; els.input.value = ''; enterErrorFallback();
  recoverFromVoiceError();
  ok('keeps typing fallback when the mic is unusable',
     classes.has('sky-typing-fallback') && voiceErrorFallback === true);

  // d) a deliberately-opened command bar (not a voice error) is left alone
  classes.clear(); micOk = true; els.input.value = '';
  voiceErrorFallback = false; commandFallbackOpen = true; classes.add('sky-command-open');
  recoverFromVoiceError();
  ok('leaves a user-opened command bar open', classes.has('sky-command-open'));
})();

console_log('\n' + pass + ' checks passed');

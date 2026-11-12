/*
 * The wall panel's home screen (touch/home.html) tells the truth and needs no keyboard - gap audit 2026-11-13, lane A.
 * No browser, no network. Two halves:
 *   1. the REAL ZFETCH block (ZFETCH-BEGIN..ZFETCH-END) runs in a vm sandbox: one deadline on every request, ONE
 *      "Can't reach Zoe" line while requests fail (network error, timeout, proxy 502/503/504), cleared by the next answer;
 *      the plain failure line for a tap; one wording for a screen that could not load (refused read = sign in).
 *   2. checks over the page text, each a function returning its violations, so the negative controls at the end plant
 *      every fault into a copy and expect the check to go red (A1 no typing dialogs, A2/A3 no silent taps, A7 person
 *      arrival, A8 timer done in Zoe's voice, A9 wall-readable sizes, chat session id, tappable agenda rows).
 * Run: node services/zoe-ui/dist/test_touch_home_honest_panel.js   (SRC=<copy of home.html> to point it elsewhere)
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const HOME = fs.readFileSync(process.env.SRC || path.join(__dirname, 'touch/home.html'), 'utf8');
let n = 0;
function check(name, fn) { fn(); n++; console.log('ok  ' + name); }

// ── 1. the ZFETCH block, for real ──
const block = /\/\/ ZFETCH-BEGIN[\s\S]*?\/\/ ZFETCH-END/.exec(HOME);
assert(block, 'ZFETCH block missing from home.html');
function sandbox() {
  const pill = { on: false, classList: { toggle(c, v) { if (c === 'on') pill.on = !!v; } } };
  const box = {
    pill, toasts: [], queue: [], seen: [],
    document: { getElementById: (id) => (id === 'zoeOff' ? pill : null) },
    reqSig: (ms) => ({ deadline: ms }),
    sid: () => 'S',
    toast(t) { box.toasts.push(t); },
    fetch(url, opts) {
      box.seen.push({ url, opts });
      const next = box.queue.shift();
      return next === 'net' ? Promise.reject(new Error('net')) : Promise.resolve({ status: next, ok: next >= 200 && next < 300 });
    },
  };
  vm.createContext(box);
  vm.runInContext(block[0], box);
  return box;
}
const run = (box, code) => vm.runInContext(code, box);

(async () => {
  {
    const b = sandbox();
    b.queue.push(200);
    await run(b, "zfetch('/api/x',{})");
    check('zfetch puts a deadline on a request that carries none', () => assert.deepStrictEqual(b.seen[0].opts.signal, { deadline: 12000 }));
    b.queue.push(200);
    await run(b, "zfetch('/api/x',{},120000)");
    check('zfetch takes a longer deadline when a call asks for one (the chat stream)', () => assert.strictEqual(b.seen[1].opts.signal.deadline, 120000));
    b.queue.push('net');
    let threw = false;
    await run(b, "zfetch('/api/x',{})").catch(() => { threw = true; });
    check('a network failure lights the one offline line and still rejects to the caller', () => { assert(b.pill.on); assert(threw); });
    run(b, "actFail('That scene didn’t run')");
    check('a failed tap while offline says so plainly', () => assert.strictEqual(b.toasts.pop(), 'That scene didn’t run — Zoe can’t be reached right now.'));
    check('a load failure while offline says Zoe cannot be reached', () => assert.strictEqual(run(b, "loadErr('calendar',new Error('http 0'))"), 'Couldn’t load your calendar — Zoe can’t be reached right now.'));
    b.queue.push(404);
    await run(b, "zfetch('/api/x',{})");
    check('any answer from Zoe (even a 404) clears the offline line', () => assert(!b.pill.on));
    b.queue.push(503);
    await run(b, "zfetch('/api/x',{})");
    check('a proxy 503 (zoe-data down behind nginx) lights it again', () => assert(b.pill.on));
    b.queue.push(200);
    await run(b, "zfetch('/api/x',{})");
    run(b, "actFail()");
    check('online again: the line clears and a failed tap reads plainly', () => { assert(!b.pill.on); assert.strictEqual(b.toasts.pop(), 'That didn’t go through.'); });
    check('a refused read (401/403) reads as private, not broken', () => assert.strictEqual(run(b, "loadErr('day',new Error('auth'))"), 'Sign in to see your day.'));
    check('a plain failure reads as one sentence', () => assert.strictEqual(run(b, "loadErr('reminders',new Error('http 500'))"), 'Couldn’t load your reminders.'));
    b.queue.push(500);
    let rejected = false;
    await run(b, "zfetch('/api/x',{}).then(zok)").catch(() => { rejected = true; });
    check('zok turns a non-2xx into a failure, so success is shown only on a 2xx', () => assert(rejected));
  }

  // ── 2. checks over the page text ──
  const mainScript = (t) => { const i = t.indexOf('<script>', t.indexOf('touch-alarms.js')); return t.slice(i, t.indexOf('</script>', i)); };
  const css = (t) => t.slice(t.indexOf('<style>'), t.indexOf('</style>'));
  const near = (t, marker, len) => { const i = t.indexOf(marker); return i < 0 ? '' : t.slice(i, i + len); };

  const checks = {
    bareApiFetch(t) { return (mainScript(t).match(/(?<![\w.])fetch\((?:'\/api\/|path,)/g) || []); },
    helpersSkipZfetch(t) {
      return ['function apiGet(url){return zfetch(', 'function apiJson(method,url,body){return zfetch(', "function haControl(body){return zfetch('/api/ha/control'",
        "function musicPost(body){return zfetch('/api/music/control'"].filter((s) => !t.includes(s));
    },
    typingFields(t) {
      // the desktop-browser Ask bar is the ONE text field, and only in the statement that builds it when this is not the kiosk
      const s = mainScript(t).replace(/^\s*\/\/.*$/gm, '').replace(/^\s*if\(!window\.__kiosk\)cmdbar\.innerHTML=.*$/m, ''), bad = [];
      (s.match(/<input(?![^>]*type="range")[^>]*>/g) || []).forEach((x) => bad.push(x));
      // the STATIC page too: markup outside the scripts is on the kiosk whatever the code does
      const stat = t.replace(/<script[\s\S]*?<\/script>/g, '').replace(/<style[\s\S]*?<\/style>/g, '');
      (stat.match(/<input(?![^>]*type="range")[^>]*>|<textarea[^>]*>|contenteditable/g) || []).forEach((x) => bad.push('static: ' + x));
      if (!/^\s*if\(!window\.__kiosk\)cmdbar\.innerHTML=/m.test(mainScript(t))) bad.push('the Ask bar is not built off-kiosk only');
      // a phone hand-off must land on a page that can do the job: no members page edits a reminder's words, so the reminder dialog has none
      if (/phoneQrHtml\('\/calendar\.html'/.test(near(mainScript(t), 'function editReminder(', 900))) bad.push('reminder edit hands off to a page that cannot edit it');
      if (/createElement\('input'\)/.test(s)) bad.push("createElement('input')");
      return bad;
    },
    silentTaps(t) {
      const s = mainScript(t), bad = [];
      const re = /(haControl\(|musicPost\(|timers\/cancel',\{timer_id:b\.|preferred-player')(?:(?!\.catch\()[\s\S]){0,300}\.catch\(function\(\)\{\}\)/g;
      let m; while ((m = re.exec(s))) bad.push(m[0].slice(0, 80));
      // a scene's flash is the success signal: it must come AFTER the 2xx check, in the dock and on the sleep screen
      ['.pc.scene[data-eid]', '.sc.scene[data-eid]'].forEach((k) => {
        const r = near(s, k, 520);
        if (!(r.indexOf('.then(zok)') >= 0 && r.indexOf("classList.add('fire')") > r.indexOf('.then(zok)'))) bad.push('flash before 2xx: ' + k);
      });
      return bad;
    },
    timerVoice(t) {
      const bad = [];
      // the browser's voice is the FALLBACK only: named once, inside _timerSayBrowser, and timerSay reaches it on every failure path
      if ((t.match(/speechSynthesis\.speak\(/g) || []).length !== 1 || !near(t, 'function _timerSayBrowser(', 220).includes('speechSynthesis.speak(')) bad.push('browser speech outside the fallback');
      const ts = near(t, 'function timerSay(', 1100);
      if (!ts.includes('if(!sid()){fb();return;}')) bad.push('no session = silent');
      // Dismiss must silence a line that is pending or playing: the handler calls timerHush, and every late path checks the generation
      if (!/tmrAlarmX'\)\.onclick=function\(\)\{ov\.classList\.remove\('on'\);timerHush\(\);/.test(t)) bad.push('Dismiss does not hush the timer voice');
      if (!ts.includes('if(gen!==_tSay.gen)return;') || !near(t, 'function _timerSayBrowser(', 80).includes('if(gen!==_tSay.gen)return;')) bad.push('late speech is not generation-checked');
      const th = near(t, 'function timerHush(', 320);
      if (!(th.includes('_tSay.gen++') && th.includes('.pause()') && th.includes('speechSynthesis.cancel()'))) bad.push('timerHush does not stop audio and browser speech');
      if (!ts.includes("if(!j||!j.audio_base64){fb();return;}")) bad.push('no audio = silent');
      if (!ts.includes('.catch(fb)')) bad.push('a refused speak = silent');
      if (!/timerSay\(\(label\|\|'Timer'\)\+' timer is done'\)/.test(near(t, 'function timerAlarm(', 2400))) bad.push('timerAlarm does not speak via timerSay');
      if (!near(t, 'function timerSay(', 300).includes("'/api/voice/speak'")) bad.push('timerSay is not the server TTS');
      return bad;
    },
    personArrival(t) {
      const bad = [];
      if (!/sp\.get\('person'\)/.test(t)) bad.push('arrival ignores ?person=');
      if (!near(t, 'function wireContacts(', 200).includes('var want=_openPerson')) bad.push('wireContacts does not consume the arrival');
      if (!/if\(wi>=0\)openContact\(ps\[wi\],wi\)/.test(t)) bad.push('the person card is not opened');
      return bad;
    },
    chatSession(t) {
      const bad = [];
      if (!/if\(th\)cb\.session_id=th/.test(t)) bad.push('chat sends no session_id');
      if (!/ev\.type==='RUN_STARTED'&&\(ev\.threadId\|\|ev\.thread_id\)/.test(t)) bad.push('thread id never captured');
      return bad;
    },
    agendaTap(t) { return near(t, 'function loadToday(', 2200).includes("show('day');editEvent(e)") ? [] : ['home agenda rows are not tappable']; },
    smallWallText(t) {
      const c = css(t), bad = [];
      ['.hdock .hpc .tl{', '#dock .pc.temp .tl{', '#dock .pc.temp .tpl{', '.cmd .cev{', '.cmd .dn{', '.calmonth .cmh{', '.slp .sc.temp .nm{', '.slp .sc.temp .tpl{'].forEach((sel) => {
        const m = /font-size:([\d.]+)px/.exec(near(c, sel, 200));
        if (!m || +m[1] < 16) bad.push(sel + (m ? m[1] : '?'));
      });
      if (!/\.lderr\{color:var\(--ink-soft\)/.test(c)) bad.push('.lderr not in readable ink');
      (mainScript(t).match(/ink-faint[^'"]*["'][^>]*>[^<]*Couldn’t/g) || []).forEach((x) => bad.push('faint error: ' + x.slice(0, 60)));
      return bad;
    },
  };
  Object.keys(checks).forEach((k) => check('page: ' + k + ' is clean', () => assert.deepStrictEqual(checks[k](HOME), [])));

  // ── negative controls: plant each fault, the check must see it ──
  const plant = (from, to) => { assert(HOME.includes(from), 'control anchor missing: ' + from.slice(0, 60)); return HOME.replace(from, to); };
  const red = (k, text) => assert(checks[k](text).length > 0, 'check ' + k + ' did not see the planted fault');
  check('control: a bare /api fetch is seen', () => red('bareApiFetch', plant("zfetch('/api/chat/'", "fetch('/api/chat/'")));
  check('control: apiJson bypassing zfetch is seen', () => red('helpersSkipZfetch', plant('function apiJson(method,url,body){return zfetch(', 'function apiJson(method,url,body){return fetch(')));
  check('control: a typing field is seen', () => red('typingFields', plant("+pickChips(cands,cands[0])", "+'<input data-f=\"nm\" maxlength=\"12\">'")));
  check('control: a silent scene tap is seen', () => red('silentTaps', plant(".catch(function(){actFail('That scene didn’t run');});};});\n    dbody", ".catch(function(){});};});\n    dbody")));
  check('control: a flash before the 2xx is seen', () => red('silentTaps', plant("haControl({entity_id:el.getAttribute('data-eid'),action:el.getAttribute('data-act')||'turn_on'}).then(zok)\n        .then(function(){el.classList.add('fire');",
    "el.classList.add('fire');haControl({entity_id:el.getAttribute('data-eid'),action:el.getAttribute('data-act')||'turn_on'}).then(zok)\n        .then(function(){")));
  check('control: browser speech for the timer is seen', () => red('timerVoice', plant("timerSay((label||'Timer')+' timer is done');", "speechSynthesis.speak(new SpeechSynthesisUtterance('x'));")));
  check('control: a static text field is seen', () => red('typingFields', plant('<div id="cmdbar"></div>', '<div id="cmdbar"><input id="cmdin" type="text"></div>')));
  check('control: a Dismiss that leaves speech pending is seen', () => red('timerVoice', plant("ov.classList.remove('on');timerHush();", "ov.classList.remove('on');")));
  check('control: a reminder hand-off to a page that cannot edit it is seen', () => red('typingFields', plant("To change the words, delete this one and say the new reminder.", "x'+phoneQrHtml('/calendar.html','scan')+'")));
  check('control: a silent guest timer is seen', () => red('timerVoice', plant('if(!sid()){fb();return;}', 'if(!sid())return;')));
  check('control: an ignored person arrival is seen', () => red('personArrival', plant("if(wi>=0)openContact(ps[wi],wi);", "")));
  check('control: a chat without session id is seen', () => red('chatSession', plant('if(th)cb.session_id=th;', '')));
  check('control: a dead agenda row is seen', () => red('agendaTap', plant("show('day');editEvent(e)", "show('day')")));
  check('control: a 9px dock label is seen', () => red('smallWallText', plant('#dock .pc.temp .tl{font-size:16px', '#dock .pc.temp .tl{font-size:9px')));
  check('control: faint error copy is seen', () => red('smallWallText', plant('<div class="lderr">Couldn’t load rooms.', '<div style="color:var(--ink-faint);font-size:18px">Couldn’t load rooms.')));

  console.log(`\n${n} checks passed`);
})().catch((e) => { console.error(e); process.exit(1); });

/*
 * The wall panel's home screen (touch/home.html) tells the truth and needs no keyboard - gap audit 2026-11-13, lane A.
 * No browser, no network. Two halves:
 *   1. the REAL ZFETCH block (ZFETCH-BEGIN..ZFETCH-END) runs in a vm sandbox: one deadline on every request, ONE
 *      "Can't reach Zoe" line while requests fail (network error, timeout, proxy 502/503/504), cleared by the next answer;
 *      the plain failure line for a tap; one wording for a screen that could not load (refused read = sign in).
 *   2. checks over the page text, each a function returning its violations, so the negative controls at the end plant
 *      every fault into a copy and expect the check to go red (A1 no typing dialogs, A2/A3 no silent taps, A7 person
 *      arrival, A8 timer done in Zoe's voice, A9 wall-readable sizes, chat session id, tappable agenda rows, the Settings screen:
 *      Voice ID / Pendant / Notifications rows through zfetch, no text field, nothing off the estate, a guest sees two sections).
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
      // nor a list item's words (lists.html only adds, ticks and deletes items): the item dialog says what works instead
      const item = near(mainScript(t), "if(a==='edit'){", 600);
      if (/handoffDialog\([^;]*'\/[\w-]+\.html'/.test(item)) bad.push('list-item edit hands off to a page that cannot rename an item');
      if (!item.includes('To change the words, take it off the list and add the new one.')) bad.push('list-item edit does not say what works');
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
      if (!/if\(say\)timerSay\(say\+' timer is done'\)/.test(near(t, 'function timerAlarm(', 2600))) bad.push('timerAlarm does not speak via timerSay');
      if (!near(t, 'function timerSay(', 300).includes("'/api/voice/speak'")) bad.push('timerSay is not the server TTS');
      return bad;
    },
    // a timer the panel daemon speaks itself (spoken_by_panel) is not spoken by the page too: run the REAL poll-learn + tick in a vm
    timerPanelSpoken(t) {
      const s = mainScript(t), bad = [];
      const learn = /\(\(d&&d\.timers\)\|\|\[\]\)\.forEach\(function\(t\)\{[\s\S]*?\}\);/.exec(near(s, 'function _tAlarmRefresh(', 1500));
      const tick = /function _tAlarmTick\(\)\{[\s\S]*?\n {4}if\(due\.length\)[^\n]*\}/.exec(s);
      if (!learn || !tick) return ['timer watcher not found'];
      const run = (timers) => {
        const box = { _tAlarm: { known: {}, fired: {} }, calls: [], timerAlarm(l, ids, say) { box.calls.push({ l, say }); }, _ackSend() {}, Date };
        vm.createContext(box);
        vm.runInContext('var d={timers:' + JSON.stringify(timers) + '},seen={},now=0,acks={},late=[];function _tUnspoken(){return _tAlarm.unspoken||(_tAlarm.unspoken={});}' + learn[0] + tick[0] + '_tAlarmTick();', box);
        return box.calls[0] || {};
      };
      const both = run([{ timer_id: 'a', label: 'Pasta', expires_at_ms: 1, spoken_by_panel: true }, { timer_id: 'b', label: 'Eggs', expires_at_ms: 1 }]);
      if (both.l !== 'Pasta / Eggs' || both.say !== 'Eggs') bad.push('mixed batch: card ' + both.l + ', spoken ' + both.say);
      if (run([{ timer_id: 'a', label: 'Pasta', expires_at_ms: 1, spoken_by_panel: true }]).say !== '') bad.push('the page speaks a timer the panel already speaks');
      if (run([{ timer_id: 'a', label: 'Pasta', expires_at_ms: 1, spoken_by_panel: false }]).say !== 'Pasta') bad.push('an unflagged timer is not spoken');
      if (!near(s, 'function timerAlarm(', 1400).includes("if(say)timerSay(say+' timer is done');")) bad.push('timerAlarm ignores say');
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
    // Settings (W2): Voice ID, Pendant and Notifications rows; no text field, no bare fetch, no route off the estate, a guest sees two sections
    settingsScreen(t) {
      const s = mainScript(t), bad = [];
      const a = s.indexOf('// SETGUEST-BEGIN'), b = s.indexOf('function editContact(');
      if (a < 0 || b < a) return ['settings block not found'];
      const blk = s.slice(a, b);
      if (/<input(?![^>]*type="range")|<textarea|contenteditable|createElement\('input'\)/.test(blk)) bad.push('a text field in Settings');
      if (/(?<![\w.])fetch\(/.test(blk)) bad.push('a fetch in Settings that skips zfetch');
      if (/location\.(href|assign|replace)|window\.open|data-go|\.html['"?#]/.test(blk)) bad.push('Settings routes off the estate');
      ["apiGet('/api/enrollment/state')", "apiGet('/api/omi/my-pendant')", "apiGet('/api/omi/health')", "apiGet('/api/notifications/pending')",
        "zfetch('/sw.js'", "if(a==='pendlink')panelVoice(", "if(a==='notecard')orbPress(", "Pendant sync isn’t switched on.",
        "Say: “Zoe, learn my voice”"].forEach((x) => { if (!blk.includes(x)) bad.push('missing: ' + x); });
      if (!/if\(guest\)\{wireSetSys\(\);return host\.onclick=setClick;\}\s*wireSetVoiceId\(\);/.test(blk)) bad.push('a guest is not stopped before the member rows');
      if (!/var secs=guest\?\[\s*\{t:'This panel'[^\n]*\n\s*\{t:'System'[^\n]*\n\s*\{t:'Everything else'[^\n]*sign in for the rest[^\n]*\n\s*\]:/.test(blk)) bad.push('guest sections are not exactly panel, system, how to get the rest');
      if (!near(s, 'function wirePanelSettings(){', 900).includes('if(_setGuest()){')) bad.push('the panel rows are editable for a guest');
      if (/ink-faint[^'"]*["'][^>]*>[^<]*(Couldn’t|unavailable|Loading)/.test(blk)) bad.push('faint state copy in Settings');
      if (!/function orbPress\(idlePrompt\)[\s\S]{0,400}orb\.addEventListener\('click',function\(\)\{orbPress\(\);\}\)/.test(s)) bad.push('the orb and Settings do not share one press path');
      return bad;
    },
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
  check('control: browser speech for the timer is seen', () => red('timerVoice', plant("if(say)timerSay(say+' timer is done');", "speechSynthesis.speak(new SpeechSynthesisUtterance('x'));")));
  check('control: a static text field is seen', () => red('typingFields', plant('<div id="cmdbar"></div>', '<div id="cmdbar"><input id="cmdin" type="text"></div>')));
  check('control: a Dismiss that leaves speech pending is seen', () => red('timerVoice', plant("ov.classList.remove('on');timerHush();", "ov.classList.remove('on');")));
  check('control: a reminder hand-off to a page that cannot edit it is seen', () => red('typingFields', plant("To change the words, delete this one and say the new reminder.", "x'+phoneQrHtml('/calendar.html','scan')+'")));
  check('control: a list-item hand-off to a page that cannot rename it is seen', () => red('typingFields', plant("handoffDialog('Change “'+cur+'”','',''", "handoffDialog('Change “'+cur+'”','/lists.html','Scan'")));
  check('control: a list-item dialog that hides what works is seen', () => red('typingFields', plant('To change the words, take it off the list and add the new one.', 'Edit it.')));
  check('control: the page speaking a timer the panel already speaks is seen', () => red('timerPanelSpoken', plant('if(!t.panel)say.push(t.label);', 'say.push(t.label);')));
  check('control: a dropped spoken_by_panel flag is seen', () => red('timerPanelSpoken', plant('panel:t.spoken_by_panel===true', 'panel:false')));
  check('control: a silent guest timer is seen', () => red('timerVoice', plant('if(!sid()){fb();return;}', 'if(!sid())return;')));
  check('control: an ignored person arrival is seen', () => red('personArrival', plant("if(wi>=0)openContact(ps[wi],wi);", "")));
  check('control: a chat without session id is seen', () => red('chatSession', plant('if(th)cb.session_id=th;', '')));
  check('control: a dead agenda row is seen', () => red('agendaTap', plant("show('day');editEvent(e)", "show('day')")));
  check('control: a typed field in Settings is seen', () => red('settingsScreen', plant("'<div class=\"srow2\"><span>No pendant linked to you yet</span></div>'", "'<input type=\"text\" id=\"pendCode\">'")));
  check('control: a bare fetch in Settings is seen', () => red('settingsScreen', plant("apiGet('/api/omi/health')", "fetch('/api/omi/health').then(function(r){return r.json();})")));
  check('control: a Settings link to a desktop page is seen', () => red('settingsScreen', plant("if(a==='contacts')show('person');", "if(a==='contacts')location.href='/people.html';")));
  check('control: a typed pendant link instead of the voice path is seen', () => red('settingsScreen', plant("if(a==='pendlink')panelVoice(", "if(a==='pendlink')phoneForm(")));
  check('control: a guest reaching the member rows is seen', () => red('settingsScreen', plant('if(guest){wireSetSys();return host.onclick=setClick;}', '')));
  check('control: editable panel rows for a guest are seen', () => red('settingsScreen', plant('if(_setGuest()){el.innerHTML=', 'if(false){el.innerHTML=')));
  check('control: a silent pendant flag-off is seen', () => red('settingsScreen', plant("_setLine('setPend','<div class=\"srow2\"><span class=\"lderr\">Pendant sync isn’t switched on.</span></div>',s0)", "_setLine('setPend','',s0)")));
  check('control: a second orb press path is seen', () => red('settingsScreen', plant("orb.addEventListener('click',function(){orbPress();});", "orb.addEventListener('click',function(){panelVoice();});")));
  // ── Settings privacy, for real: the MEMBER helper + the whole Settings block in a vm with a fake DOM and a controllable API ──
  // (a) a live GUEST id beside stale member metadata is a guest: no private endpoint, no private row; (b) a private answer that
  // lands after the session changed is dropped; (c) a member -> guest change while Settings is open clears the private rows at once;
  // (d) a genuine member still sees them.
  const PRIV = /\/api\/(enrollment\/state|omi\/my-pendant|omi\/health|notifications\/pending|proactive\/inbox|system\/display\/preferences|voice\/voices|system\/panel\/idle-logout)/;
  function settingsBox(text, live, meta) {
    const mem = /\/\/ MEMBER-BEGIN[\s\S]*?\/\/ SESSCHECK-END/.exec(text), blk = /\/\/ SETGUEST-BEGIN[\s\S]*?(?=\n {2}function editContact\()/.exec(text);
    assert(mem && blk, 'MEMBER / Settings block missing');
    const store = {}; let hostHTML = '';
    const mk = (id) => store[id] || (store[id] = { id, innerHTML: '', textContent: '', style: {}, disabled: false, options: [], selectedIndex: 0,
      classList: { toggle() {}, add() {}, remove() {} }, querySelector: () => null, querySelectorAll: () => [] });
    const host = { onclick: null, get innerHTML() { return hostHTML; }, set innerHTML(v) { hostHTML = v; Object.keys(store).forEach((k) => delete store[k]); } };
    const box = {
      urls: [], pending: {}, store, host, live, meta, _cur: 'settings', JSON, Date, Promise, encodeURIComponent, String, Object, Math, isFinite,
      localStorage: { getItem: (k) => (k === 'zoe_session' && box.meta ? JSON.stringify(box.meta) : null) },
      sid: () => box.live,
      document: { getElementById: (id) => (id === 'setSecs' ? host : hostHTML.includes('id="' + id + '"') ? mk(id) : null), querySelector: () => null },
      esc: (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]),
      loadErr: (w) => 'Couldn’t load your ' + w + '.',
      apiGet(url) { box.urls.push(url); return PRIV.test(url) ? new Promise((res, rej) => { box.pending[url.replace(/\?.*$/, '')] = { res, rej }; }) : new Promise(() => {}); },
      apiJson: () => new Promise(() => {}), zfetch: () => new Promise(() => {}),
      _tmMemo: {}, _lst: {}, _calEvents: {}, show(id) { box.shown.push(id); if (id === 'settings') vm.runInContext('wireSettings()', box); }, shown: [],
      wirePanelSettings() {}, estClose() {}, toast() {}, panelVoice() {}, orbPress() {}, _panelDevId: () => 'p1', _savePrefs() {},
      setInterval: () => 1, clearInterval() {},
    };
    vm.createContext(box);
    vm.runInContext(mem[0] + '\n' + blk[0] + '\nsessionCheck();', box);   // the watch's first look sets who the page is painted for
    return box;
  }
  const flush = () => new Promise((r) => setImmediate(r));
  const MEMBER = { session_id: 'M1', role: 'user', user_id: 'member-a', username: 'member-a' };
  const GUEST = { session_id: 'G1', role: 'guest', user_id: 'guest' };
  const shown = (b) => Object.values(b.store).map((e) => e.innerHTML).join('|') + '|' + b.host.innerHTML;
  async function answerAll(b) {
    const r = (u, v) => { if (b.pending[u]) b.pending[u].res(v); };
    r('/api/enrollment/state', { voice: { enrolled: true } });
    r('/api/omi/my-pendant', { linking: { enabled: true }, pendants: [{ label: 'PendantX', last_sync_at: null }] });
    r('/api/notifications/pending', { notifications: [{ title: 'NoteX', message: 'private words' }] });
    r('/api/proactive/inbox', { notify: 1 });
    r('/api/system/display/preferences', { preferences: { day_brightness: 37, night_start: 'NightX' } });
    r('/api/voice/voices', { voices: [{ id: 'v1', label: 'VoiceX' }], current: 'v1' });
    r('/api/system/panel/idle-logout', { seconds: 7777 });
    await flush(); await flush();
  }
  const privacy = {
    async staleMetadata(text) {      // (a)
      const b = settingsBox(text, 'G1', MEMBER), bad = [];
      if (vm.runInContext('_setGuest()', b) !== true) bad.push('a live guest id beside member metadata reads as a member');
      vm.runInContext('wireSettings()', b);
      if (b.urls.some((u) => PRIV.test(u))) bad.push('private endpoints called for a guest: ' + b.urls.filter((u) => PRIV.test(u)).join(' '));
      if (/id="set(Vid|Pend|Note)"/.test(b.host.innerHTML)) bad.push('private rows painted for a guest');
      return bad;
    },
    async lateAnswer(text) {         // (b)
      const b = settingsBox(text, 'M1', MEMBER), bad = [];
      vm.runInContext('wireSettings()', b);
      b.live = 'G1'; b.meta = GUEST;
      await answerAll(b);
      if (/enrolled|PendantX|NoteX|NightX|VoiceX|130 min/.test(shown(b))) bad.push('an answer for the old session was painted: ' + (shown(b).match(/enrolled|PendantX|NoteX|NightX|VoiceX|130 min/) || [])[0]);
      return bad;
    },
    async downgradeClears(text) {    // (c)
      const b = settingsBox(text, 'M1', MEMBER), bad = [];
      vm.runInContext('wireSettings()', b);
      await answerAll(b);
      if (!/Your voice is enrolled/.test(shown(b))) bad.push('setup: member rows never shown');
      b.live = 'G1'; b.meta = GUEST;
      vm.runInContext('sessionCheck()', b);
      if (/enrolled|PendantX|NoteX|id="set(Vid|Pend|Note)"/.test(shown(b))) bad.push('private rows stay after the member -> guest change');
      if (!/sign in for the rest/.test(b.host.innerHTML)) bad.push('Settings not repainted in its guest form');
      return bad;
    },
    async memberSees(text) {         // (d)
      const b = settingsBox(text, 'M1', MEMBER), bad = [];
      if (vm.runInContext('_setGuest()', b) !== false) bad.push('a genuine member reads as guest');
      vm.runInContext('wireSettings()', b);
      await answerAll(b);
      const s = shown(b);
      ['Your voice is enrolled', 'PendantX', 'NoteX'].forEach((x) => { if (!s.includes(x)) bad.push('member row missing: ' + x); });
      return bad;
    },
  };
  for (const k of Object.keys(privacy)) { const bad = await privacy[k](HOME); check('Settings privacy: ' + k, () => assert.deepStrictEqual(bad, [])); }
  for (const [name, k, from, to] of [
    ['the live-sid match', 'staleMetadata', "if(!live||!u||String(u.session_id||'')!==live)return null;", 'if(!live||!u)return null;'],
    ['a missing role read as member', 'staleMetadata', "return (!role||role==='guest'||!uid||uid==='guest')?null:u;}", "return (role==='guest')?null:u;}"],
    ['the stale-answer drop', 'lateAnswer', 'if(s0!==undefined&&sid()!==s0)return;', ''],
    ['the downgrade repaint', 'downgradeClears', 'if(PRIVATE_BOXES[_cur])show(_cur);}', '}'],
    ['the display-row drop', 'lateAnswer', "if(_cur!=='settings'||!sessLive(s0))return;var pr=", "if(_cur!=='settings')return;var pr="],
    ['the voice-row drop', 'lateAnswer', "        if(_cur!=='settings'||!sessLive(s0))return;\n        var voices=", "        if(_cur!=='settings')return;\n        var voices="],
    ['the idle-logout-row drop', 'lateAnswer', "if(_cur!=='settings'||!sessLive(s0))return;var cur=", "if(_cur!=='settings')return;var cur="],
  ]) {
    let bad;
    if (k === 'staleMetadata' && name.startsWith('a missing role')) {   // a session with no role must never count as a member
      const b = settingsBox(plant(from, to), 'M1', { session_id: 'M1', user_id: 'member-a' });
      bad = vm.runInContext('_setGuest()', b) === true ? [] : ['seen'];
    } else bad = await privacy[k](plant(from, to));
    check('control: removing ' + name + ' is seen', () => assert(bad.length > 0, 'check ' + k + ' did not see the planted fault'));
  }
  check('Settings privacy: a session with no role is a guest', () => assert.strictEqual(vm.runInContext('_setGuest()', settingsBox(HOME, 'M1', { session_id: 'M1', user_id: 'member-a' })), true));
  // Voice ID: an answer that does not say enrolled true/false is a load failure, never "not enrolled"
  {
    const b = settingsBox(HOME, 'M1', MEMBER);
    vm.runInContext('wireSettings()', b);
    b.pending['/api/enrollment/state'].res({ voice: { enrolled: null } });
    await flush(); await flush();
    check('Settings: an unknown enrolment reads as a load failure, not "not enrolled"', () => {
      const s = shown(b); assert(/Couldn’t load your voice ID\./.test(s) && !/isn’t enrolled/.test(s), s.slice(0, 200)); });
  }

  // ── every member-private screen: the session is captured when the request starts, a late answer is dropped ──
  // (the request starts with s0=sid(); each answer and each failure is painted only while sessLive(s0)). One case per screen.
  const fnBody = (text, name) => { const s = mainScript(text), i = s.indexOf('function ' + name + '('); if (i < 0) return '';
    const j = s.indexOf('\n  function ', i + 10); return s.slice(i, j < 0 ? undefined : j); };
  const SCREENS = [
    ['home agenda', 'loadToday', ["if(_cur!=='home'||!sessLive(s0))return;", '}).catch(function(e){if(!sessLive(s0))return;']],
    ['Day', 'wireDay', ["if(_cur!=='day'||!host||!sessLive(s0))return;", "if(host&&_cur==='day'&&sessLive(s0))", "if(_cur!=='day'||!rems||!sessLive(s0))return;", "if(rems&&_cur==='day'&&sessLive(s0))"]],
    ['Calendar', 'paintCal', ["if(_cur!=='calendar'||gen!==_cal.gen||!sessLive(s0))return;\n      var evs", "}).catch(function(e){if(_cur!=='calendar'||gen!==_cal.gen||!sessLive(s0))return;"]],
    ['Reminders', 'wireReminder', ["apiGet('/api/reminders/').then(function(d){if(_cur!=='reminder'||!sessLive(s0))return;", "}).catch(function(e){if(_cur!=='reminder'||!sessLive(s0))return;"]],
    ['Lists', 'wireListsFull', ["if(_cur!=='list'||!sessLive(s0))return;", "if(_cur==='list'&&sessLive(s0))"]],
    ['Contacts', 'wireContacts', ["apiJson('GET','/api/people/').then(function(d){if(_cur!=='person'||!sessLive(s0))return;", '}).catch(function(e){if(!grid||!sessLive(s0))return;']],
    ['Work board', 'wireWork', ["if(_cur!=='work'||!sessLive(s0))return;", "if(_cur==='work'&&sessLive(s0))"]],
    ['Timers', 'wireTimer', ["timersGet().then(function(d){if(_cur!=='timer'||!sessLive(s0))return;", "if(_cur==='timer'&&sessLive(s0)&&rings"]],
  ];
  function lateAnswerGuards(text, name, guards) {
    const body = fnBody(text, name), bad = [];
    if (!body) return ['function ' + name + ' missing'];
    const cap = body.search(/s0=sid\(\)/), call = body.search(/apiGet\(|apiJson\(|timersGet\(\)\.then|Promise\.all\(/);
    if (cap < 0 || call < 0 || cap > call) bad.push(name + ': the session is not captured before the request');
    guards.forEach((g) => { if (!body.includes(g)) bad.push(name + ': a late answer is not dropped at ' + g.slice(0, 50)); });
    return bad;
  }
  SCREENS.forEach(([label, name, guards]) => {
    check('late answer dropped: ' + label, () => assert.deepStrictEqual(lateAnswerGuards(HOME, name, guards), []));
    check('control: ' + label + ' painting a late answer is seen', () =>
      assert(lateAnswerGuards(plant(guards[0], guards[0].replace(/\|\|!sessLive\(s0\)|&&sessLive\(s0\)|if\(!sessLive\(s0\)\)return;/, '')), name, guards).length > 0));
  });

  // the generic part, for real: a session change empties EVERY private container at once (before any request answers),
  // forgets cached private answers, closes an open dialog and repaints ONLY a private open screen; the first change is always seen
  const SC_MEM = /\/\/ MEMBER-BEGIN[\s\S]*?\/\/ MEMBER-END/.exec(HOME)[0], SC_TG = /var TIMERS_MEMO_MS=[\s\S]*?\n  function timersGet\(now\)\{[\s\S]*?\n  \}/.exec(HOME)[0];
  const BOX_IDS = { home: ['hToday'], day: ['dayEvents', 'dayRems', 'dayCommsList'], calendar: ['calBody'], reminder: ['remList', 'remMeta'],
    list: ['lstTabs', 'lstItems', 'lstCount'], person: ['ctGrid', 'ctAZ', 'ctDetail'], work: ['wkBody', 'wkMeta'], timer: ['tmRings', 'tmMore', 'alFull'], settings: ['setSecs'] };
  function scBox(text, cur, opts) {
    opts = opts || {};
    const sc = /\/\/ SESSCHECK-BEGIN[\s\S]*?\/\/ SESSCHECK-END/.exec(text);
    assert(sc, 'SESSCHECK block missing');
    const els = {}, chips = [{ gone: false, remove() { this.gone = true; } }];
    Object.values(BOX_IDS).flat().forEach((id) => { els[id] = { innerHTML: '<b>MemberA private ' + id + '</b>', onclick: () => {} }; });
    const box = { live: 'M1', meta: MEMBER, shown: [], closed: 0, _cur: cur, JSON, String, Object, setInterval: () => 1, calls: 0, els, chips,
      localStorage: { getItem: () => JSON.stringify(box.meta) }, sid: () => box.live, show(id) { box.shown.push(id); },
      document: { getElementById: (id) => els[id] || null }, estClose() { box.closed++; },
      dbody: { querySelectorAll: (q) => (q === '.pc.tchip' ? chips : []) },
      apiGet() { box.calls++; return new Promise(() => {}); }, Date, _lst: { lists: ['A-list'], sel: 'a' }, _calEvents: { k: 1 } };
    vm.createContext(box);
    vm.runInContext(SC_MEM + '\n' + SC_TG, box);
    if (opts.before) opts.before(box);          // a change that happens BEFORE the watcher block runs counts as the start
    vm.runInContext(sc[0], box);
    if (opts.after) opts.after(box);
    return box;
  }
  const leftover = (b) => Object.keys(b.els).filter((id) => /MemberA/.test(b.els[id].innerHTML));
  const scChecks = {
    clearsAllAtOnce(text) {
      const b = scBox(text, 'day'), bad = [];
      b.live = 'G1'; b.meta = GUEST; vm.runInContext('sessionCheck()', b);   // synchronous: nothing has been answered yet
      const left = leftover(b); if (left.length) bad.push('member text left in: ' + left.join(' '));
      if (b.closed !== 1) bad.push('an open dialog is not closed'); if (!b.chips[0].gone) bad.push('dock timer chips stay');
      if (b.shown.join() !== 'day') bad.push('the open private screen is not repainted');
      return bad;
    },
    firstChangeSeen(text) {
      const b = scBox(text, 'person', { after: (x) => { x.live = 'G1'; x.meta = GUEST; } }), bad = [];   // the change lands before the first tick
      vm.runInContext('sessionCheck()', b);
      if (leftover(b).length || b.shown.join() !== 'person') bad.push('a change before the first tick is taken as the starting state');
      return bad;
    },
    // a 401 drops the member session, then a GUEST session is provisioned: that arrival is a change too, or the screen stays blank
    guestArrivalRepaints(text) {
      const b = scBox(text, 'day'), bad = [];
      b.live = ''; b.meta = null; vm.runInContext('sessionCheck()', b);
      if (leftover(b).length || b.shown.join() !== 'day') bad.push('losing the member session does not clear and repaint');
      b.live = 'G1'; b.meta = GUEST; vm.runInContext('sessionCheck()', b);
      if (b.shown.join() !== 'day,day') bad.push('the provisioned guest session does not repaint the open screen');
      b.live = 'G2'; b.meta = Object.assign({}, GUEST, { session_id: 'G2' }); vm.runInContext('sessionCheck()', b);
      if (b.shown.join() !== 'day,day,day') bad.push('a new guest session is not seen as a change');
      vm.runInContext('sessionCheck()', b);
      if (b.shown.length !== 3) bad.push('no change must repaint nothing');
      return bad;
    },
  };
  Object.keys(scChecks).forEach((k) => check('session change: ' + k, () => assert.deepStrictEqual(scChecks[k](HOME), [])));
  check('control: a session change that only repaints (no synchronous clear) is seen', () =>
    assert(scChecks.clearsAllAtOnce(plant('    clearPrivate();\n    if(PRIVATE_BOXES[_cur])show(_cur);}', '    if(PRIVATE_BOXES[_cur])show(_cur);}')).length > 0));
  check('control: a container missing from the clear list is seen', () =>
    assert(scChecks.clearsAllAtOnce(plant("work:['wkBody','wkMeta']", "work:['wkMeta']")).length > 0));
  check('control: a baseline taken lazily at the first tick is seen', () =>
    assert(scChecks.firstChangeSeen(plant('var _paintFor=sessKey();', 'var _paintFor=null;').replace('function sessionCheck(){var k=sessKey();if(k===_paintFor)return;', 'function sessionCheck(){var k=sessKey();if(_paintFor===null||k===_paintFor){_paintFor=k;return;}')).length > 0));
  check('control: a session key that ignores guest sessions is seen', () =>
    assert(scChecks.guestArrivalRepaints(plant("function sessKey(){return String(sid()||'')+(sessionMember()?'|m':'|g');}", "function sessKey(){var m=sessionMember();return m?String(m.session_id):'';}")).length > 0));
  check('the clear list names every private screen and only containers the page really has', () => {
    const m = /var PRIVATE_BOXES=(\{[\s\S]*?\});/.exec(HOME); assert(m, 'PRIVATE_BOXES missing');
    const boxes = vm.runInNewContext('(' + m[1] + ')');
    assert.strictEqual(JSON.stringify(boxes), JSON.stringify(BOX_IDS));
    Object.values(boxes).flat().forEach((id) => assert(HOME.includes('id="' + id + '"'), 'no element ' + id));
  });
  check('a hidden screen keeps no DOM: every private screen is rebuilt from its template on show()', () => {
    assert(/full\.innerHTML=f\.html;/.test(HOME));
    Object.keys(BOX_IDS).filter((s) => s !== 'home').forEach((s) => assert(new RegExp('\\n    ' + s + ':\\{').test(HOME), s + ' is not a FULL screen'));
  });
  {
    const run = (cur) => scBox(HOME, cur);
    check('session change on a private screen: repainted, cached lists / calendar / timers forgotten', () => {
      const b = run('list'); vm.runInContext('timersGet(1)', b);
      b.live = 'G1'; b.meta = GUEST; vm.runInContext('sessionCheck()', b);
      assert.deepStrictEqual(b.shown, ['list']); assert.strictEqual(vm.runInContext('JSON.stringify(_lst.lists)', b), '[]');
      assert.strictEqual(vm.runInContext('JSON.stringify(_calEvents)', b), '{}'); vm.runInContext('timersGet(2)', b); assert.strictEqual(b.calls, 2, 'timers memo reused across sessions');
    });
    check('session change on a non-private screen repaints nothing; no change repaints nothing', () => {
      const b = run('weather'); b.live = 'G1'; b.meta = GUEST; vm.runInContext('sessionCheck()', b); assert.deepStrictEqual(b.shown, []);
      const c = run('day'); vm.runInContext('sessionCheck()', c); assert.deepStrictEqual(c.shown, []); assert.strictEqual(leftover(c).length, Object.values(BOX_IDS).flat().length, 'no change must not clear');
    });
    check('the timers memo is never another session\'s answer', () => {
      const b = run('home'); vm.runInContext('timersGet(1000)', b); b.live = 'M2'; vm.runInContext('timersGet(1001)', b); assert.strictEqual(b.calls, 2);
      vm.runInContext('timersGet(1002)', b); assert.strictEqual(b.calls, 2, 'same session inside the memo window reuses');
    });
  }

  // ── calendar list shape (#2447-tolerant): occurrences keyed by instance_key, placed on occurrence_date, series id for writes ──
  {
    const cs = /\/\/ CALSHAPE-BEGIN[\s\S]*?\/\/ CALSHAPE-END/.exec(HOME);
    const box = { String }; vm.createContext(box); vm.runInContext(cs ? cs[0] : '', box);
    check('calendar: an occurrence is keyed by instance_key (id as fallback), placed on occurrence_date, read_only honoured', () => {
      assert.strictEqual(vm.runInContext("ekey({id:'s1',instance_key:'s1:2026-10-22'})", box), 's1:2026-10-22');
      assert.strictEqual(vm.runInContext("ekey({id:7})", box), '7');
      assert.strictEqual(vm.runInContext("eday({start_date:'2026-10-01',occurrence_date:'2026-10-22'})", box), '2026-10-22');
      assert.strictEqual(vm.runInContext("eday({start_date:'2026-10-01'})", box), '2026-10-01');
      assert.strictEqual(vm.runInContext("eRO({read_only:1})", box), true); assert.strictEqual(vm.runInContext("eRO({read_only:false})", box), false);
    });
    const calText = (text) => { const s = mainScript(text), bad = [];
      if (/data-eid="'\+esc\(String\(e\.id\)\)/.test(s)) bad.push('a calendar row keyed by the series id');
      if (!s.includes('_calEvents[ekey(e)]=e') || !s.includes('var k=eday(e);')) bad.push('calendar map / buckets not by occurrence');
      const day = fnBody(text, 'wireDay');
      if (!day.includes('return ekey(x)===row.getAttribute(\'data-eid\')')) bad.push('Day looks rows up by series id');
      if (!day.includes("apiJson('DELETE','/api/calendar/events/'+encodeURIComponent(e.id))")) bad.push('Day delete does not send the series id');
      if (!day.includes("(eRO(e)?'':'<span class=\"lact\">")) bad.push('a read-only event offers Edit / Delete');
      if (!day.includes("(e.source_label?'<div class=\"es\">From '+esc(e.source_label)")) bad.push('source_label not shown');
      return bad; };
    check('calendar: rows keyed and acted on by the occurrence; writes keep the series; read-only and source honoured', () => assert.deepStrictEqual(calText(HOME), []));
    check('control: a calendar row keyed by series id is seen', () => assert(calText(plant(`return '<div class="cwe" data-eid="'+esc(ekey(e))+'"`, `return '<div class="cwe" data-eid="'+esc(String(e.id))+'"`)).length > 0));
    check('control: a read-only Day row with Edit / Delete is seen', () => assert(calText(plant("(eRO(e)?'':'<span class=\"lact\">", "(false?'':'<span class=\"lact\">")).length > 0));
  }
  check('page: no member-vs-guest decision outside sessionMember', () => {
    const s = mainScript(HOME);
    const reads = (s.match(/JSON\.parse\(localStorage\.getItem\('zoe_session'\)/g) || []).length;
    assert.strictEqual(reads, 2, 'the main script reads stored session metadata outside sid() and sessionMember(): ' + reads);
    assert(!/u\.role|\.role\|\|''\)\.toLowerCase\(\)==='guest'/.test(s.replace(/\/\/ MEMBER-BEGIN[\s\S]*?\/\/ MEMBER-END/, '')), 'a role check outside sessionMember');
    ['kioskAuthFail', 'estateAuthFail', 'convEnsureSession'].forEach((f) => assert(near(s, 'function ' + f + '(', 900).includes('sessionCheck()'), f + ' does not repaint the open private screen'));
  });
  check('control: a 9px dock label is seen', () => red('smallWallText', plant('#dock .pc.temp .tl{font-size:16px', '#dock .pc.temp .tl{font-size:9px')));
  check('control: faint error copy is seen', () => red('smallWallText', plant('<div class="lderr">Couldn’t load rooms.', '<div style="color:var(--ink-faint);font-size:18px">Couldn’t load rooms.')));

  console.log(`\n${n} checks passed`);
})().catch((e) => { console.error(e); process.exit(1); });

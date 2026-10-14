/*
 * Controlled test for the panel's calendar EVENT DETAIL sheet (no browser, no network).
 *
 *   node dist/test_touch_event_detail.js        (CI: test_touch_event_detail_harness.py)
 *
 * Owner feedback (2026-10-14): tapping an event opened a cramped form whose Category
 * <select> was unreadable on the dark kiosk, and the details should be near-full-screen.
 * This pins, against the REAL js/touch-event-detail.js and the real touch/home.html text:
 *   - the sheet renders every field that is present and OMITS every one that is not, for
 *     today's minimal calendar row AND a fully-populated synced-calendar-shaped event
 *     (invented data only — this repo is public);
 *   - categories are tappable chips (no native <select>, no text input anywhere in the
 *     sheet — the panel has no keyboard), every state meeting WCAG AA text contrast;
 *   - editing saves ONLY what changed through the API (never title/location/metadata —
 *     PUT replaces metadata wholesale);
 *   - read-only (externally owned) events hide edit/delete;
 *   - spoken "close" and swipe-down decisions;
 *   - the home.html wiring: script tag + ?v= cache-bust, no select left in the event code.
 * Negative controls: the same check-suite is run against deliberately broken copies of
 * the module and MUST go red for each.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const MOD_PATH = path.join(__dirname, 'js/touch-event-detail.js');
const SRC = fs.readFileSync(MOD_PATH, 'utf8');
const HOME = fs.readFileSync(path.join(__dirname, 'touch/home.html'), 'utf8');

function load(src) {
  const sandbox = { module: { exports: {} }, console };
  sandbox.module.exports = {};
  vm.runInNewContext(src, Object.assign(sandbox, { exports: sandbox.module.exports }), { timeout: 5000 });
  return sandbox.module.exports;
}

// ---- fixtures (invented) ------------------------------------------------------
const OPT = { locale: 'en-US', panelTz: 'Australia/Perth', now: new Date(2026, 9, 14) };
// Exactly what GET /api/calendar/events returns today (routers/calendar.py): the row + parsed metadata.
const MINIMAL = { id: 'ev-min', user_id: 'u1', title: 'Dentist', start_date: '2026-10-20', start_time: '09:00',
  end_date: null, end_time: null, duration: 45, category: 'health', location: null, all_day: false,
  recurring: null, metadata: null, visibility: 'family', deleted: false };
// Today's row with everything the desktop calendar stores in metadata.
const TODAYS_FULL = { id: 'ev-today', title: 'Sam’s school concert', start_date: '2026-10-22', start_time: '18:30', end_time: '20:00',
  category: 'family', location: 'Example Primary School hall', all_day: false, recurring: 'weekly',
  metadata: { description: 'Bring the blue folder.\nDoors open at 6.', attendees: [{ person_id: 'p1', name: 'Sam', role: 'participant' }, { person_id: 'p2', name: 'Riley', role: 'participant' }],
    reminders: [{ offset_minutes: 30, message: 'leave soon' }], travel_time: 15 } };
// The richer shape synced calendars will carry (field names are tolerant; see the module header).
const SYNCED = { id: 'ev-sync', title: 'Quarterly planning', start_date: '2026-10-21', start_time: '10:00', end_time: '11:30',
  category: 'work', location: 'Room 4, Example Tower', all_day: false, status: 'tentative', source: 'google',
  timezone: 'Europe/London', read_only: true, meeting_url: 'https://meet.example.com/abc-defg-hij',
  description: 'Agenda in the shared doc.', organizer: { name: 'Alex Example', email: 'alex@example.com' },
  attendees: [{ name: 'Alex Example', status: 'accepted' }, { name: 'Jo Sample', response_status: 'needsAction' },
    { name: 'Kit Demo', status: 'declined' }, { name: 'Max Test', status: 'tentative' }] };

// Objects built inside the vm context are cross-realm: compare their JSON, not prototypes.
const eq = (a, b) => assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), b);

// The REAL API shape from the calendar data model (alembic 0052, PR #2059): attendees are event_attendees rows
// (display_name/email/partstat/role 'chair'/is_organizer, rsvp is a BOOLEAN "reply requested"), alarms[] rows,
// rrule beats the legacy free-text `recurring`, calendar_name names the calendar, read_only arrives as 1/0.
const API_SHAPED = { id: 'ev-api', title: 'Team sync', start_date: '2026-10-21', start_time: '10:00', end_time: '10:30', category: 'work',
  status: 'confirmed', source: 'google', calendar_name: 'Work', read_only: 1, description: 'Standing agenda.',
  conference_url: 'https://meet.example.com/team', recurring: 'daily', rrule: 'FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;COUNT=10',
  alarms: [{ action: 'display', offset_minutes: 30, related: 'start' }, { action: 'display', offset_minutes: 1440, related: 'start' }],
  attendees: [{ display_name: 'Alex Example', partstat: 'accepted', is_organizer: true, role: 'chair', rsvp: true },
    { display_name: 'Jo Sample', partstat: 'needs-action', role: 'req-participant', rsvp: true, is_organizer: false },
    { display_name: 'Kit Demo', partstat: 'declined', role: 'opt-participant', rsvp: false },
    { display_name: 'Pat Tentative', partstat: 'tentative' }, { name: 'Pat Legacy', role: 'participant', legacy: true }] };
const BUSY = { id: 'ev-busy', title: 'Busy', busy_only: true, start_date: '2026-10-21', start_time: '14:00', end_time: '15:00', status: 'confirmed',
  visibility: 'busy', category: null, location: null, description: null, metadata: null, attendees: [], alarms: [] };

function checkSuite(M) {
  const fails = [];
  const t = (name, fn) => { try { fn(); } catch (e) { fails.push(name + ' :: ' + (e && e.message || e).toString().split('\n')[0]); } };
  const has = (h, s, m) => assert(h.includes(s), (m || 'expected') + ': ' + JSON.stringify(s));
  const lacks = (h, s, m) => assert(!h.includes(s), (m || 'must not contain') + ': ' + JSON.stringify(s));

  t('minimal event: present fields render', () => {
    const h = M.renderDetail(MINIMAL, OPT);
    has(h, '>Dentist<'); has(h, 'October'); has(h, '20'); has(h, '9:00 am – 9:45 am', 'time range from duration');
    has(h, 'Health'); has(h, 'class="evcat"'); has(h, 'data-x="close"');
    has(h, 'evd sparse', 'a headline-only event fills the sheet');
    lacks(M.renderDetail(TODAYS_FULL, OPT), 'evd sparse');
  });
  t('minimal event: empty fields are omitted, not shown blank', () => {
    const h = M.renderDetail(MINIMAL, OPT);
    ['>With<', '>Where<', '>Notes<', '>Meeting<', 'Reminder', 'evtz', 'evbadge src', 'evbadge bad', 'evbadge warn', 'Repeats', 'evro', 'undefined', 'null', '>Organizer<']
      .forEach((s) => lacks(h, s, 'omitted for a minimal event'));
    lacks(h, 'class="evbody"', 'no empty body grid');
  });
  t("today's full event: notes, who, where, repeat, reminder", () => {
    const h = M.renderDetail(TODAYS_FULL, OPT);
    has(h, 'Bring the blue folder.'); has(h, '>Sam<'); has(h, '>Riley<'); has(h, 'Example Primary School hall');
    has(h, 'Repeats every week'); has(h, '30 min before'); has(h, '6:30 pm – 8:00 pm'); has(h, '1 h 30 min');
    lacks(h, 'class="evrsvp', 'plain participants carry no RSVP chip');
    lacks(h, '>Organizer<');
  });
  t('synced event: attendees + RSVP + organizer, status, source, tz, meeting, notes', () => {
    const h = M.renderDetail(SYNCED, OPT);
    has(h, 'Alex Example'); has(h, 'Organizer'); has(h, 'Going'); has(h, 'No reply'); has(h, 'Declined'); has(h, 'Maybe');
    has(h, 'Tentative'); has(h, 'From Google Calendar'); has(h, 'Europe/London'); has(h, 'Agenda in the shared doc.');
    has(h, 'data-url="https://meet.example.com/abc-defg-hij"'); has(h, 'Room 4, Example Tower');
    assert.strictEqual((h.match(/>Organizer</g) || []).length, 1, 'organizer badge once (listed attendee + organizer field are the same person)');
  });
  t('synced event: read-only hides edit/delete and says where to edit', () => {
    const h = M.renderDetail(SYNCED, OPT);
    lacks(h, 'data-x="edit"'); lacks(h, 'data-x="del"'); lacks(h, 'data-x="phone"');
    has(h, 'Managed in Google Calendar — edit it there.'); has(h, 'data-x="close"');
  });
  t('editable events show the edit actions', () => {
    const h = M.renderDetail(MINIMAL, OPT);
    has(h, 'data-x="edit"'); has(h, 'data-x="del"'); has(h, 'data-x="phone"');
  });
  t('timezone line only when it differs from the panel', () => {
    lacks(M.renderDetail(Object.assign({}, SYNCED, { timezone: 'Australia/Perth' }), OPT), 'evtz');
    has(M.renderDetail(SYNCED, OPT), 'Times are in Europe/London');
  });
  t('cancelled events are struck through and badged', () => {
    const h = M.renderDetail(Object.assign({}, SYNCED, { status: 'cancelled' }), OPT);
    has(h, 'st-cancelled'); has(h, 'Cancelled');
    assert(/\.evd\.st-cancelled \.evt\{[^}]*line-through/.test(HOME), 'home.html styles the cancelled title');
  });
  t('all-day and multi-day', () => {
    has(M.renderDetail({ id: 'a', title: 'Fun day', start_date: '2026-10-25', all_day: true }, OPT), 'All day');
    const h = M.renderDetail({ id: 'b', title: 'Camp', start_date: '2026-10-25', end_date: '2026-10-27', all_day: true }, OPT);
    has(h, '→'); has(h, 'All day');
  });
  t('a meeting link that is not http(s) is never rendered; a link-only location becomes the meeting', () => {
    lacks(M.renderDetail(Object.assign({}, MINIMAL, { meeting_url: 'javascript:alert(1)' }), OPT), 'javascript:');
    const h = M.renderDetail(Object.assign({}, MINIMAL, { location: 'https://meet.example.com/x' }), OPT);
    has(h, 'data-x="meeturl"'); lacks(h, '>Where<');
  });
  t('everything user-supplied is escaped', () => {
    const evil = '<img src=x onerror=alert(1)>';
    const h = M.renderDetail({ id: '"><b>', title: evil, start_date: '2026-10-20', start_time: '09:00', category: evil, location: evil,
      description: evil, attendees: [{ name: evil }], organizer: evil }, OPT);
    lacks(h, '<img', 'raw tag'); lacks(h, 'onerror=alert(1)>', 'raw handler'); has(h, '&lt;img');
    const c = M.renderCategoryChips(evil); lacks(c, '<img');
  });
  t('long attendee lists are capped with a "+N more"', () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ name: 'Guest ' + String.fromCharCode(65 + i) }));
    const h = M.renderDetail(Object.assign({}, MINIMAL, { attendees: many }), OPT);
    has(h, '+8 more'); lacks(h, 'Guest T');
  });
  t('NO native select and NO text input/textarea anywhere in the detail or edit sheet', () => {
    const dt = '<div class="dtp"><button type="button" data-s="d,1">▲</button></div>';
    [M.renderDetail(MINIMAL, OPT), M.renderDetail(SYNCED, OPT), M.renderDetail(TODAYS_FULL, OPT),
      M.renderEdit(MINIMAL, Object.assign({ dtHtml: dt }, OPT)), M.renderCategoryChips('work'), M.renderDeleteConfirm(MINIMAL)]
      .forEach((h) => { ['<select', '<option', '<input', '<textarea', 'contenteditable'].forEach((s) => lacks(h, s)); });
  });
  t('category chips: all categories, one selected, obvious state, big', () => {
    const h = M.renderCategoryChips('health');
    M.CATEGORIES.forEach((c) => has(h, 'data-cat="' + c + '"'));
    assert.strictEqual((h.match(/aria-checked="true"/g) || []).length, 1, 'exactly one selected');
    assert.strictEqual((h.match(/role="radio"/g) || []).length, M.CATEGORIES.length);
    has(h, 'class="evchip on"'); has(h, '✓', 'selected chip shows a check, not only a colour');
    assert(/\.evd \.evchip\{[^}]*min-height:(\d+)px/.test(HOME) && +/\.evd \.evchip\{[^}]*min-height:(\d+)px/.exec(HOME)[1] >= 48, 'chips are >= 48px tall');
  });
  t('a category outside the picker list is still shown (and selected)', () => {
    const h = M.renderCategoryChips('sport');
    has(h, 'data-cat="sport"'); has(h, 'class="evchip on"');
  });
  t('every chip state meets WCAG AA text contrast (4.5:1)', () => {
    const cats = Object.keys(M.CAT_HEX);
    cats.forEach((c) => [true, false].forEach((sel) => {
      const k = M.chipColors(c, sel), r = M.contrast(k.fg, k.bg);
      assert(r >= 4.5, c + (sel ? ' selected' : '') + ' contrast ' + r.toFixed(2));
    }));
    // …and what the markup actually paints is what chipColors says.
    const h = M.renderCategoryChips('work'), k = M.chipColors('work', true);
    has(h, 'background:' + k.bg + ';color:' + k.fg);
  });
  t('sheet text tokens are legible on the sheet (no ink-faint labels; ink-soft >= 4.5:1)', () => {
    const rules = HOME.split('\n').filter((l) => /^\s*\.(estmc\.)?evd\b/.test(l) || /^\s*\.evd\b/.test(l));
    assert(rules.length > 20, 'found the event-sheet CSS');
    rules.forEach((l) => assert(!/color:var\(--ink-faint\)/.test(l), 'faint text on the sheet: ' + l.trim().slice(0, 60)));
    const soft = M.mix('#f4f6fa', '#0f121c', 74);
    assert(M.contrast(soft, '#0f121c') >= 4.5 && M.contrast(soft, '#181c2c') >= 4.5, 'ink-soft over the sheet gradient');
  });
  t('palette mirrors the estate CAT map', () => {
    const root = {}; (HOME.match(/--[a-z-]+:#[0-9a-f]{6}/gi) || []).forEach((p) => { const [k, v] = p.split(':'); root[k] = v.toLowerCase(); });
    const cat = /var CAT=\{([\s\S]*?)\};/.exec(HOME)[1];
    let n = 0;
    cat.split(/,(?=\s*[a-z]+:)/).forEach((pair) => {
      const m = /^\s*([a-z]+):'([^']+)'/.exec(pair); if (!m) return;
      const v = /var\((--[a-z-]+)\)/.exec(m[2]); const hex = (v ? root[v[1]] : m[2]).toLowerCase();
      assert.strictEqual(M.CAT_HEX[m[1]], hex, 'category ' + m[1]); n++;
    });
    assert(n >= 14, 'compared the whole map (' + n + ')');
  });

  t('API shape: RSVP from partstat, organizer from is_organizer, boolean rsvp ignored', () => {
    const h = M.renderDetail(API_SHAPED, OPT);
    ['Going', 'No reply', 'Declined', 'Maybe'].forEach((x) => has(h, '>' + x + '<'));
    assert.strictEqual((h.match(/>Organizer</g) || []).length, 1, 'only the is_organizer row');
    lacks(h, 'r-true'); lacks(h, 'r-false'); lacks(h, '>true<'); lacks(h, '>false<');
    has(h, '>Pat Legacy<'); assert.strictEqual((h.match(/class="evrsvp/g) || []).length, 4, 'the legacy attendee has no RSVP chip');
    // a role of "chair" without is_organizer is NOT what marks the organizer
    lacks(M.renderDetail({ id: 'x', title: 'T', start_date: '2026-10-21', attendees: [{ display_name: 'Cy', role: 'chair', partstat: 'accepted' }] }, OPT), '>Organizer<');
    // a non-participant organizer (organises, does not attend) still shows, as organizer
    has(M.renderDetail({ id: 'x', title: 'T', start_date: '2026-10-21', attendees: [{ display_name: 'Dee', role: 'non-participant', is_organizer: true, partstat: 'needs-action' }] }, OPT), '>Organizer<');
  });
  t('API shape: rrule beats the legacy `recurring`, and reads as words', () => {
    const h = M.renderDetail(API_SHAPED, OPT);
    has(h, 'Repeats every 2 weeks on Tue, Thu, 10 times'); lacks(h, 'every day', 'the legacy text must not win over rrule');
    has(M.renderDetail({ id: 'r', title: 'T', start_date: '2026-10-21', rrule: 'RRULE:FREQ=MONTHLY' }, OPT), 'Repeats every month');
    has(M.renderDetail({ id: 'r', title: 'T', start_date: '2026-10-21', recurring: 'weekly' }, OPT), 'Repeats every week');
    lacks(M.renderDetail({ id: 'r', title: 'T', start_date: '2026-10-21', rrule: null, recurring: null }, OPT), 'Repeats');
  });
  t('API shape: alarms[] listed, labelled as stored only (nothing fires them)', () => {
    const h = M.renderDetail(API_SHAPED, OPT);
    has(h, '30 min before, 1 day before'); has(h, 'Saved reminders'); has(h, 'nothing sends them yet');
    lacks(M.renderDetail(MINIMAL, OPT), 'Saved reminders');
    const both = M.renderDetail({ id: 'd', title: 'T', start_date: '2026-10-21', alarms: [{ offset_minutes: 30 }], metadata: { reminders: [{ offset_minutes: 30 }] } }, OPT);
    assert.strictEqual((both.match(/30 min before/g) || []).length, 1, 'the same reminder in alarms and metadata is listed once');
  });
  t('API shape: calendar_name + provider label (incl. Home Assistant), read_only as 1', () => {
    const h = M.renderDetail(API_SHAPED, OPT);
    has(h, 'From Work (Google Calendar)'); has(h, 'Managed in Work (Google Calendar) — edit it there.');
    lacks(h, 'data-x="edit"'); lacks(h, 'data-x="del"');
    has(M.renderDetail({ id: 'h', title: 'Bins', start_date: '2026-10-21', source: 'ha', calendar_name: 'Household' }, OPT), 'From Household (Home Assistant)');
    has(M.renderDetail({ id: 'h', title: 'Bins', start_date: '2026-10-21', source: 'ha' }, OPT), 'From Home Assistant');
    lacks(M.renderDetail({ id: 'l', title: 'T', start_date: '2026-10-21', source: 'local', calendar_name: 'Personal' }, OPT), 'evbadge src');
    assert(M.renderDetail({ id: 'z', title: 'T', start_date: '2026-10-21', read_only: 0 }, OPT).includes('data-x="edit"'), 'read_only 0 is editable');
  });
  t('API shape: conference_url becomes the meeting button', () => { has(M.renderDetail(API_SHAPED, OPT), 'data-url="https://meet.example.com/team"'); });
  t('busy_only renders a time block and NOTHING else (even if the payload leaks detail)', () => {
    const h = M.renderDetail(BUSY, OPT);
    has(h, 'Busy'); has(h, '2:00 pm – 3:00 pm'); has(h, 'Only the time is shared'); has(h, 'class="estmc evd busy');
    ['data-x="edit"', 'data-x="del"', 'data-x="phone"', 'evcat', '>With<', '>Where<', '>Notes<', 'data-x="meeturl"'].forEach((x) => lacks(h, x));
    const leaky = M.renderDetail(Object.assign({}, BUSY, { description: 'SECRET NOTES', location: 'SECRET PLACE', attendees: [{ display_name: 'SECRET PERSON' }],
      conference_url: 'https://meet.example.com/secret', category: 'health', alarms: [{ offset_minutes: 5 }] }), OPT);
    ['SECRET', 'meet.example.com', 'Health', 'Saved reminders'].forEach((x) => lacks(leaky, x, 'a busy block must not show'));
  });

  // ---- saving: the narrowest write -------------------------------------------
  function stub() {
    const calls = [];
    const api = (method, url, body) => { calls.push({ method, url, body }); return Promise.resolve({ id: 'ev-min', ...body }); };
    return { api, calls };
  }
  const saveCases = [];
  saveCases.push(['saving a new category PUTs only the category', async () => {
    const s = stub(); const r = await M.save(s.api, MINIMAL, { category: 'work', date: '2026-10-20', time: '09:00', allDay: false });
    assert.strictEqual(s.calls.length, 1); assert.strictEqual(s.calls[0].method, 'PUT');
    assert.strictEqual(s.calls[0].url, '/api/calendar/events/ev-min');
    eq(s.calls[0].body, { category: 'work' });
    assert.strictEqual(r.event.category, 'work'); assert.strictEqual(r.event.title, 'Dentist');
  }]);
  saveCases.push(['an unchanged edit makes no request', async () => {
    const s = stub(); const r = await M.save(s.api, MINIMAL, { category: 'health', date: '2026-10-20', time: '09:00', allDay: false });
    assert.strictEqual(s.calls.length, 0); assert.strictEqual(r.changed, false);
  }]);
  saveCases.push(['never sends title/location/metadata/visibility', async () => {
    const s = stub(); await M.save(s.api, TODAYS_FULL, { category: 'work', date: '2026-10-23', time: '19:00', allDay: false });
    ['title', 'location', 'metadata', 'visibility', 'recurring'].forEach((k) => assert(!(k in s.calls[0].body), k));
  }]);
  saveCases.push(['moving the start keeps the length (end_time / end_date follow)', async () => {
    const s = stub(); await M.save(s.api, TODAYS_FULL, { category: 'family', date: '2026-10-23', time: '19:00', allDay: false });
    eq(s.calls[0].body, { start_date: '2026-10-23', start_time: '19:00', end_time: '20:30' });
    const s2 = stub(); await M.save(s2.api, { id: 'm', start_date: '2026-10-25', end_date: '2026-10-27', start_time: '08:00', end_time: '09:00', category: 'general' },
      { category: 'general', date: '2026-10-26', time: '08:00', allDay: false });
    eq(s2.calls[0].body, { start_date: '2026-10-26', end_date: '2026-10-28' });
  }]);
  saveCases.push(['a timeless event saved with only a new category is NOT flipped to all-day', async () => {
    const ev = { id: 't', title: 'Pay rent', start_date: '2026-10-30', start_time: null, all_day: false, category: 'general' };
    const s = stub(); await M.save(s.api, ev, { category: 'family', date: '2026-10-30', time: null, allDay: true });
    eq(s.calls[0].body, { category: 'family' });
  }]);
  saveCases.push(['all-day toggle, and giving an all-day event a time', async () => {
    const s = stub(); await M.save(s.api, MINIMAL, { category: 'health', date: '2026-10-20', time: '09:00', allDay: true });
    eq(s.calls[0].body, { all_day: true, start_time: null });
    const ad = { id: 'd', start_date: '2026-10-20', all_day: true, category: 'general' };
    const s2 = stub(); await M.save(s2.api, ad, { category: 'general', date: '2026-10-20', time: '14:00', allDay: false });
    eq(s2.calls[0].body, { all_day: false, start_time: '14:00' });
  }]);
  saveCases.push(['an API failure rejects (the panel toasts) and does not swallow it', async () => {
    let threw = false;
    try { await M.save(() => Promise.reject(new Error('http 500')), MINIMAL, { category: 'work', date: '2026-10-20', time: '09:00', allDay: false }); } catch (e) { threw = true; }
    assert(threw);
  }]);

  // ---- voice / gesture ---------------------------------------------------------
  t('spoken close', () => {
    ['close', 'Close.', 'Hey Zoe, close that', 'okay close it please', 'never mind', "that’s all", 'Zoe dismiss', 'go back', 'done']
      .forEach((u) => assert(M.isCloseUtterance(u), 'should close on: ' + u));
    ['close the garage door', 'what’s on today', 'change the location to the library', 'close my account', '', null]
      .forEach((u) => assert(!M.isCloseUtterance(u), 'must NOT close on: ' + u));
  });
  t('swipe down closes only from the top, far enough, and mostly vertical', () => {
    assert(M.swipeShouldClose({ dy: 200, dx: 10, scrollTop: 0 }));
    assert(!M.swipeShouldClose({ dy: 60, dx: 0, scrollTop: 0 }), 'too short');
    assert(!M.swipeShouldClose({ dy: 200, dx: 0, scrollTop: 140 }), 'the sheet was scrolled: a pull-down is a scroll');
    assert(!M.swipeShouldClose({ dy: 200, dx: 190, scrollTop: 0 }), 'diagonal');
    assert(!M.swipeShouldClose({ dy: -200, dx: 0, scrollTop: 0 }), 'upwards');
  });
  t('phone-edit URL is a plain http(s) calendar link or nothing', () => {
    assert.strictEqual(M.phoneEditUrl('http://192.168.1.20'), 'http://192.168.1.20/calendar.html');
    assert.strictEqual(M.phoneEditUrl('javascript:alert(1)'), ''); assert.strictEqual(M.phoneEditUrl(''), '');
  });
  return { fails, saveCases };
}

// ---- static checks on the real home.html -----------------------------------------
function wiringChecks() {
  const fails = [];
  const t = (name, fn) => { try { fn(); } catch (e) { fails.push(name + ' :: ' + e.message.split('\n')[0]); } };
  const a = HOME.indexOf('// ---- Calendar event sheet'), b = HOME.indexOf('// ---- Day: full-screen');
  const region = HOME.slice(a, b);
  t('event-sheet region found', () => assert(a > 0 && b > a));
  t('the module is loaded once, with a ?v= cache-bust, on every page that uses it', () => {
    assert.strictEqual((HOME.match(/<script src="\/js\/touch-event-detail\.js\?v=[\w.-]+"><\/script>/g) || []).length, 1);
    const users = fs.readdirSync(__dirname, { recursive: true }).filter((f) => /\.html$/.test(f))
      .filter((f) => fs.readFileSync(path.join(__dirname, f), 'utf8').includes('touch-event-detail.js'));
    assert.deepStrictEqual(users.map((u) => u.replace(/\\/g, '/')), ['touch/home.html']);
  });
  t('no native select / text input in the event sheet code', () => {
    ['<select', 'data-f="category"', 'data-f="title"', '<input', '<textarea'].forEach((s) => assert(!region.includes(s), 'region has ' + s));
  });
  t('save goes through ZoeEventDetail.save via apiJson; delete confirms in-sheet', () => {
    assert(/EVD\.save\(apiJson,/.test(region)); assert(/renderDeleteConfirm/.test(region)); assert(/data-x==='delyes'|x==='delyes'/.test(region));
  });
  t('both entry points open the sheet (calendar grid + Day list)', () => {
    assert(/body\.onclick=function\(e\)\{var chip=e\.target\.closest\('\[data-eid\]'\);[^\n]*editEvent\(ev\)/.test(HOME), 'grid tap');
    assert(/host\.onclick=function\(ev\)\{var row=ev\.target\.closest\('\.dev\[data-eid\]'\)[\s\S]{0,700}editEvent\(e\)/.test(HOME), 'day list tap');
  });
  t('spoken "close" is wired into ZoeEstateVoice.transcript', () => {
    assert(/transcript:function\(t\)\{if\(window\.__evdVoiceClose&&window\.__evdVoiceClose\(t\)\)return;/.test(HOME));
  });
  t('swipe-down + tap-outside + idle close are wired', () => {
    assert(/swipeShouldClose/.test(region)); assert(/e\.target===m\)\{evdClose/.test(region)); assert(/EVD_IDLE_MS/.test(region));
  });
  t('every native select on the estate gets a readable dark popup', () => {
    assert(/^\s*select\{color-scheme:dark\}/m.test(HOME)); assert(/^\s*select option\{background:#[0-9a-f]{6};color:var\(--ink\)\}/m.test(HOME));
  });
  t('the sheet is near-full-screen and every control is >= 48px', () => {
    assert(/\.estmc\.evd\{width:min\(1120px,94%\)/.test(HOME));
    ['evx', 'evbtn', 'evchip', 'evsw'].forEach((c) => {
      const m = new RegExp('\\.evd \\.' + c + '\\{[^}]*(?:min-)?height:(\\d+)px').exec(HOME);
      assert(m && +m[1] >= 48, c + ' height');
    });
    assert(/\.evd \.evx\{[^}]*width:(\d+)px/.test(HOME) && +/\.evd \.evx\{[^}]*width:(\d+)px/.exec(HOME)[1] >= 48);
  });
  return fails;
}

(async () => {
  let total = 0;
  const M = load(SRC);
  const real = checkSuite(M);
  const asyncFails = [];
  for (const [name, fn] of real.saveCases) { try { await fn(); } catch (e) { asyncFails.push(name + ' :: ' + String(e.message).split('\n')[0]); } }
  const failures = real.fails.concat(asyncFails, wiringChecks());
  total += 1;
  if (failures.length) { console.error('FAIL:\n  ' + failures.join('\n  ')); process.exit(1); }

  // ---- negative controls: broken copies of the module MUST go red ------------------
  const mutants = {
    'chips fall back to a native <select>': (s) => s.replace(`return '<div class="evcats" role="radiogroup"`, `return '<select class="evcats"></select><div class="evcats" role="radiogroup"`),
    'selected chip loses its contrast (light text on a pale fill)': (s) => s.replace(`selected ? { bg: c, fg: DARK_INK }`, `selected ? { bg: c, fg: '#ffffff' }`),
    'escaping removed (XSS)': (s) => s.replace(/function esc\(s\) \{[\s\S]*?\n  \}\n/, 'function esc(s) { return String(s == null ? "" : s); }\n'),
    'save also sends the title (would blank text the panel cannot edit)': (s) => s.replace(`var body = {};\n    var curCat`, `var body = { title: ev.title };\n    var curCat`),
    'save always PUTs even when nothing changed': (s) => s.replace(`if (!Object.keys(body).length) return`, `if (false) return`),
    'read-only events still offer Delete': (s) => s.replace(`if (n.readOnly) {`, `if (false) {`),
    'empty rows rendered (omission broken)': (s) => s.replace(`if (!n.busy && n.location) rows += row('Where', esc(n.location));`, `rows += row('Where', esc(n.location));`),
    'attendee RSVP dropped': (s) => s.replace(`rsvp: isObj(a) ? rsvpOf(a) : '',\n        organizer: (isObj(a)`, `rsvp: '',\n        organizer: (isObj(a)`),
    'timeless event flipped to all-day on a category save': (s) => s.replace(`var wasAll = !!ev.all_day || !firstStr(ev.start_time)`, `var wasAll = !!ev.all_day`),
    'close word matches too much': (s) => s.replace(`return CLOSE_WORDS.indexOf(s) >= 0;`, `return s.indexOf('close') >= 0;`),
    'RSVP read from the boolean rsvp instead of partstat': (s) => s.replace(`firstStr(a.partstat, a.status,`, `firstStr(a.status,`),
    'busy_only not honoured': (s) => s.replace(`busy: truthy(ev.busy_only)`, `busy: false`),
    'legacy recurring beats rrule': (s) => s.replace(`var rr = firstStr(ev.rrule, meta(ev).rrule);`, `var rr = ev.recurring ? '' : firstStr(ev.rrule, meta(ev).rrule);`),
    'read_only 1 not recognised': (s) => s.replace(`truthy(ev.read_only) ||`, `ev.read_only === true ||`),
    'alarms ignored': (s) => s.replace(`if (Array.isArray(ev.alarms)) raw = raw.concat(ev.alarms);`, ``),
    'swipe ignores scroll position': (s) => s.replace(`(Number(g.scrollTop) || 0) <= 1 &&`, ``),
  };
  let ctl = 0;
  for (const [name, mut] of Object.entries(mutants)) {
    const src = mut(SRC);
    assert.notStrictEqual(src, SRC, 'mutation did not apply (stale negative control): ' + name);
    const MM = load(src);
    const r = checkSuite(MM);
    const af = [];
    for (const [n2, fn] of r.saveCases) { try { await fn(); } catch (e) { af.push(n2); } }
    if (process.env.VERBOSE) console.log('  control "' + name + '" -> red in:', r.fails.concat(af).map((f) => String(f).split(' :: ')[0]).join(' | '));
    if (!r.fails.length && !af.length) { console.error('NEGATIVE CONTROL NOT RED: ' + name); process.exit(1); }
    ctl++;
  }
  console.log(`event-detail: all checks passed (${ctl} negative controls red)`);
})().catch((e) => { console.error(e); process.exit(1); });

/*
 * touch-event-detail.js — the panel's calendar EVENT DETAIL sheet (touch estate only).
 *
 * Pure string-building + small decision helpers, no DOM access, so the whole
 * thing runs in a no-browser node harness (dist/test_touch_event_detail.js).
 * touch/home.html owns the wiring (open/close, QR drawing, the API call).
 *
 * Why this exists: the old tap-an-event path opened a cramped edit form whose
 * category was a native <select> — on the kiosk's dark theme the open dropdown
 * is a light native popup with near-white text, so the categories were
 * unreadable — and whose title/location were text inputs on a keyboard-less
 * panel. Tapping an event now opens a near-full-screen READ view; categories
 * are large tappable chips; typed edits hand off to the phone (QR).
 *
 * Reads defensively: every field is optional. Besides today's calendar row
 * (title, start/end date+time, duration, category, location, all_day,
 * recurring, metadata{description, attendees[{name,role}], reminders[]}) it
 * ALSO understands the richer shape synced calendars will carry (notes,
 * attendees with RSVP + organizer, meeting URL, status, source, timezone,
 * read-only) so the layout needs no change when that lands. A missing field
 * simply does not render.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ZoeEventDetail = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Category → solid hex. MIRRORS `CAT` in touch/home.html (which uses CSS vars);
  // a harness check pins the two together. Hex, not var(), because the chip
  // contrast test has to compute real luminance.
  var CAT_HEX = {
    general: '#7b96ff', work: '#6aa6ff', personal: '#66d6a3', family: '#f5b13c',
    health: '#ff7a90', social: '#66d6a3', appointment: '#ff7a90', birthday: '#f5b13c',
    medical: '#ff7a90', sport: '#5ec8f0', shopping: '#ff9e6a', routine: '#9aa6bd',
    household: '#9aa6bd', bucket: '#66d6a3', education: '#c77bff', travel: '#5ec8f0'
  };
  // What the picker offers (the set the old <select> offered).
  var CATEGORIES = ['general', 'work', 'personal', 'family', 'health', 'social', 'appointment', 'birthday'];
  var ACCENT = '#7b96ff';
  var DARK_INK = '#0b0e17';     // text on a SOLID category colour (selected chip)
  var LIGHT_INK = '#f4f6fa';    // text on the dark sheet / unselected chip
  var PANEL_BG = '#12151f';     // chip base the category colour is mixed into

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function two(n) { return (n < 10 ? '0' : '') + n; }
  function isObj(x) { return x && typeof x === 'object' && !Array.isArray(x); }
  function str(x) { return typeof x === 'string' ? x.trim() : (typeof x === 'number' ? String(x) : ''); }
  function firstStr() {
    for (var i = 0; i < arguments.length; i++) { var s = str(arguments[i]); if (s) return s; }
    return '';
  }
  function titleCase(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  // ---- colour / contrast (WCAG 2.x) ----------------------------------------
  function hexToRgb(h) {
    var m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return [0, 0, 0];
    var n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function luminance(hex) {
    var c = hexToRgb(hex).map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrast(a, b) {
    var la = luminance(a), lb = luminance(b);
    var hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  }
  // `pct`% of colour `fg` over `bg` (what color-mix(in srgb, fg pct%, bg) paints).
  function mix(fg, bg, pct) {
    var f = hexToRgb(fg), b = hexToRgb(bg);
    var o = f.map(function (v, i) { return Math.round(v * pct / 100 + b[i] * (100 - pct) / 100); });
    return '#' + o.map(function (v) { return ('0' + v.toString(16)).slice(-2); }).join('');
  }
  function catColor(cat) { return CAT_HEX[String(cat || '').toLowerCase()] || ACCENT; }
  // The two chip states, as real hex pairs {bg, fg} — the contrast test reads these.
  function chipColors(cat, selected) {
    var c = catColor(cat);
    return selected ? { bg: c, fg: DARK_INK } : { bg: mix(c, PANEL_BG, 22), fg: LIGHT_INK };
  }

  // ---- date / time formatting ----------------------------------------------
  function parseDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function fmt12(hhmm) {
    var m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
    if (!m) return '';
    var h = +m[1], ap = h < 12 ? 'am' : 'pm', hr = h % 12;
    if (hr === 0) hr = 12;
    return hr + ':' + m[2] + ' ' + ap;
  }
  function toMin(hhmm) {
    var m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
    return m ? (+m[1]) * 60 + (+m[2]) : null;
  }
  function fromMin(n) { n = Math.max(0, Math.min(23 * 60 + 59, n)); return two(Math.floor(n / 60)) + ':' + two(n % 60); }
  function dayNum(s) {   // whole days, DST-proof
    var d = parseDate(s);
    return d ? Math.round(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000) : null;
  }
  function addDays(s, n) {
    var d = parseDate(s);
    if (!d) return s;
    d.setDate(d.getDate() + n);
    return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate());
  }
  function longDate(s, o) {
    var d = parseDate(s);
    if (!d) return '';
    var now = (o && o.now) || new Date();
    var opt = { weekday: 'long', day: 'numeric', month: 'long' };
    if (d.getFullYear() !== now.getFullYear()) opt.year = 'numeric';
    return d.toLocaleDateString(o && o.locale, opt);
  }
  function durText(min) {
    if (!(min > 0)) return '';
    var h = Math.floor(min / 60), m = min % 60;
    return (h ? h + ' h' : '') + (h && m ? ' ' : '') + (m ? m + ' min' : '');
  }

  // ---- field readers (today's shape + the synced shape) --------------------
  function meta(ev) { return isObj(ev && ev.metadata) ? ev.metadata : {}; }

  function safeUrl(u) {
    u = str(u);
    return /^https?:\/\/[^\s"'<>]+$/i.test(u) ? u : '';
  }
  function meetingUrl(ev) {
    var m = meta(ev);
    var conf = isObj(ev.conference) ? ev.conference.url : '';
    return safeUrl(firstStr(ev.meeting_url, ev.conference_url, ev.hangout_link, ev.join_url, conf,
      m.meeting_url, m.conference_url, m.hangout_link, m.join_url));
  }
  function notesOf(ev) {
    var m = meta(ev);
    return firstStr(ev.description, ev.notes, m.description, m.notes);
  }
  var SOURCE_LABEL = {
    google: 'Google Calendar', gcal: 'Google Calendar', outlook: 'Outlook', microsoft: 'Outlook',
    office365: 'Outlook', o365: 'Outlook', caldav: 'CalDAV', icloud: 'iCloud', apple: 'iCloud', ics: 'a calendar feed'
  };
  var LOCAL_SOURCE = { '': 1, local: 1, zoe: 1, manual: 1, voice: 1, panel: 1, native: 1 };
  function sourceOf(ev) {
    var m = meta(ev);
    var raw = firstStr(isObj(ev.source) ? ev.source.type || ev.source.name : ev.source,
      ev.provider, ev.source_type, m.source, m.provider).toLowerCase();
    if (LOCAL_SOURCE[raw]) return null;
    return SOURCE_LABEL[raw] || titleCase(raw);
  }
  function readOnlyOf(ev) {
    var m = meta(ev);
    return ev.read_only === true || ev.readonly === true || ev.readOnly === true ||
      ev.editable === false || ev.can_edit === false || ev.canEdit === false ||
      m.read_only === true || m.readonly === true || m.editable === false;
  }
  function statusOf(ev) {
    var s = firstStr(ev.status, meta(ev).status).toLowerCase();
    if (s === 'cancelled' || s === 'canceled') return 'cancelled';
    if (s === 'tentative') return 'tentative';
    return '';
  }
  var RSVP = {
    accepted: 'Going', going: 'Going', yes: 'Going', confirmed: 'Going',
    declined: 'Declined', no: 'Declined',
    tentative: 'Maybe', tentativelyaccepted: 'Maybe', maybe: 'Maybe',
    needsaction: 'No reply', none: 'No reply', pending: 'No reply', notresponded: 'No reply', invited: 'No reply'
  };
  function rsvpOf(a) {
    var raw = firstStr(a.status, a.response_status, a.responseStatus, a.rsvp, a.response).toLowerCase().replace(/[\s_-]/g, '');
    return RSVP[raw] || '';
  }
  function nameOfPerson(a) {
    if (typeof a === 'string') return a.trim();
    if (!isObj(a)) return '';
    var n = firstStr(a.name, a.display_name, a.displayName, a.full_name);
    if (n) return n;
    var e = firstStr(a.email);
    return e ? e : '';
  }
  function organizerName(ev) {
    var o = ev.organizer != null ? ev.organizer : meta(ev).organizer;
    return nameOfPerson(o).toLowerCase();
  }
  function attendeesOf(ev) {
    var raw = ev.attendees != null ? ev.attendees : meta(ev).attendees;
    if (!Array.isArray(raw)) raw = [];
    var orgName = organizerName(ev), out = [], seen = {};
    raw.forEach(function (a) {
      var name = nameOfPerson(a);
      if (!name) return;
      var key = name.toLowerCase();
      if (seen[key]) return;
      seen[key] = 1;
      var role = isObj(a) ? firstStr(a.role, a.type).toLowerCase() : '';
      out.push({
        name: name,
        rsvp: isObj(a) ? rsvpOf(a) : '',
        organizer: (isObj(a) && (a.organizer === true || role === 'organizer')) || (!!orgName && key === orgName)
      });
    });
    // An organizer who is not in the attendee list is still "who it's with".
    var o = ev.organizer != null ? ev.organizer : meta(ev).organizer;
    var on = nameOfPerson(o);
    if (on && !seen[on.toLowerCase()]) out.unshift({ name: on, rsvp: isObj(o) ? rsvpOf(o) : '', organizer: true });
    return out;
  }
  var REPEAT = { daily: 'every day', weekly: 'every week', monthly: 'every month', yearly: 'every year', annually: 'every year',
    weekdays: 'every weekday', fortnightly: 'every two weeks', biweekly: 'every two weeks' };
  function recurrenceOf(ev) {
    var r = firstStr(ev.recurring, ev.recurrence, meta(ev).recurring);
    if (!r) return '';
    var k = r.toLowerCase();
    if (k === 'none' || k === 'no' || k === 'false' || k === '0') return '';
    if (REPEAT[k]) return 'Repeats ' + REPEAT[k];
    var m = /FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)/i.exec(r);
    if (m) return 'Repeats ' + REPEAT[m[1].toLowerCase()];
    return 'Repeats: ' + r;
  }
  function remindersOf(ev) {
    var raw = meta(ev).reminders;
    if (!Array.isArray(raw)) return [];
    var out = [];
    raw.forEach(function (r) {
      var n = isObj(r) ? Number(r.offset_minutes) : NaN;
      if (!isFinite(n)) return;
      if (n === 0) { out.push('At the time'); return; }
      if (n < 0) return;
      var t = n % 1440 === 0 ? (n / 1440) + (n === 1440 ? ' day' : ' days')
        : n % 60 === 0 ? (n / 60) + (n === 60 ? ' hour' : ' hours') : n + ' min';
      out.push(t + ' before');
    });
    return out;
  }
  function timezoneNote(ev, o) {
    var tz = firstStr(ev.timezone, ev.time_zone, ev.tz, meta(ev).timezone);
    var panelTz = (o && o.panelTz) || '';
    if (!panelTz) { try { panelTz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { panelTz = ''; } }
    return tz && tz !== panelTz ? tz.replace(/_/g, ' ') : '';
  }

  // ---- normalise ------------------------------------------------------------
  function normalize(ev, o) {
    ev = isObj(ev) ? ev : {};
    o = o || {};
    var cat = firstStr(ev.category).toLowerCase() || 'general';
    var allDay = !!ev.all_day;
    var sd = firstStr(ev.start_date), ed = firstStr(ev.end_date);
    var st = allDay ? '' : firstStr(ev.start_time), et = allDay ? '' : firstStr(ev.end_time);
    var dur = Number(ev.duration);
    var endMin = null;
    if (!et && st && dur > 0 && toMin(st) != null) endMin = toMin(st) + dur;
    var multiDay = !!(ed && sd && ed !== sd);

    // Date line: one day, or "Mon 5 Oct → Wed 7 Oct".
    var dateLine = longDate(sd, o);
    if (multiDay) {
      var a = parseDate(sd), b = parseDate(ed);
      var sOpt = { weekday: 'short', day: 'numeric', month: 'short' };
      if (a && b) dateLine = a.toLocaleDateString(o.locale, sOpt) + ' → ' + b.toLocaleDateString(o.locale, sOpt);
    }
    // Time line.
    var timeLine = '', durLine = '';
    if (allDay) timeLine = 'All day';
    else if (st) {
      var end = et ? fmt12(et) : (endMin != null && endMin < 24 * 60 ? fmt12(fromMin(endMin)) : '');
      timeLine = fmt12(st) + (end ? ' – ' + end : '');
      var span = et && toMin(et) != null && toMin(st) != null && !multiDay ? toMin(et) - toMin(st) : (dur > 0 ? dur : 0);
      durLine = durText(span);
    }
    var url = meetingUrl(ev);
    var loc = firstStr(ev.location, meta(ev).location);
    // A location that is only a meeting link is a link, not a place.
    if (loc && safeUrl(loc) && !url) { url = safeUrl(loc); loc = ''; }
    else if (loc && safeUrl(loc) && safeUrl(loc) === url) loc = '';
    var src = sourceOf(ev);
    return {
      id: firstStr(ev.id),
      title: firstStr(ev.title) || 'Event',
      category: cat, color: catColor(cat),
      dateLine: dateLine, timeLine: timeLine, durLine: durLine, allDay: allDay,
      location: loc, meetingUrl: url, notes: notesOf(ev),
      attendees: attendeesOf(ev),
      status: statusOf(ev), source: src,
      timezone: timezoneNote(ev, o),
      recurrence: recurrenceOf(ev), reminders: remindersOf(ev),
      readOnly: readOnlyOf(ev)
    };
  }

  // ---- render: detail sheet -------------------------------------------------
  function row(label, valueHtml, wide) {
    return '<div class="evrow' + (wide ? ' wide' : '') + '"><div class="evl">' + label + '</div><div class="evv">' + valueHtml + '</div></div>';
  }
  function attendeeHtml(a) {
    return '<span class="evwho' + (a.organizer ? ' org' : '') + '"><span class="evav" aria-hidden="true">' +
      esc(a.name.charAt(0).toUpperCase()) + '</span><span class="evwn">' + esc(a.name) + '</span>' +
      (a.organizer ? '<span class="evtag">Organizer</span>' : '') +
      (a.rsvp ? '<span class="evrsvp r-' + a.rsvp.toLowerCase().replace(/\s/g, '') + '">' + esc(a.rsvp) + '</span>' : '') + '</span>';
  }
  var MAX_ATTENDEES = 12;

  function renderDetail(ev, o) {
    var n = normalize(ev, o);
    var cls = 'estmc evd' + (n.status ? ' st-' + n.status : '');
    var badges = '<span class="evcat" style="--cc:' + n.color + '"><i></i>' + esc(titleCase(n.category)) + '</span>';
    if (n.status === 'cancelled') badges += '<span class="evbadge bad">Cancelled</span>';
    else if (n.status === 'tentative') badges += '<span class="evbadge warn">Tentative</span>';
    if (n.source) badges += '<span class="evbadge src">From ' + esc(n.source) + '</span>';
    if (n.recurrence) badges += '<span class="evbadge">' + esc(n.recurrence) + '</span>';

    var when = '<div class="evdate">' + esc(n.dateLine || 'No date') + '</div>' +
      (n.timeLine ? '<div class="evtime">' + esc(n.timeLine) + (n.durLine ? '<span class="evdur">' + esc(n.durLine) + '</span>' : '') + '</div>' : '') +
      (n.timezone ? '<div class="evtz">Times are in ' + esc(n.timezone) + '</div>' : '');

    var rows = '';
    if (n.attendees.length) {
      var shown = n.attendees.slice(0, MAX_ATTENDEES).map(attendeeHtml).join('');
      var more = n.attendees.length - MAX_ATTENDEES;
      rows += row('With', '<div class="evwhos">' + shown + (more > 0 ? '<span class="evwho more">+' + more + ' more</span>' : '') + '</div>', true);
    }
    if (n.location) rows += row('Where', esc(n.location));
    if (n.meetingUrl) {
      rows += row('Meeting', '<button type="button" class="evbtn" data-x="meeturl" data-url="' + esc(n.meetingUrl) + '">Join on your phone</button>');
    }
    if (n.reminders.length) rows += row(n.reminders.length > 1 ? 'Reminders' : 'Reminder', esc(n.reminders.join(', ')));
    if (n.notes) rows += row('Notes', '<div class="evnotes">' + esc(n.notes) + '</div>', true);

    var acts;
    if (n.readOnly) {
      acts = '<div class="evro">' + esc(n.source ? 'Managed in ' + n.source + ' — edit it there.' : 'This event can’t be edited here.') + '</div>' +
        '<div class="evacts"><button type="button" class="evbtn" data-x="close">Close</button></div>';
    } else {
      acts = '<div class="evacts"><button type="button" class="evbtn del" data-x="del">Delete</button>' +
        '<span class="evsp"></span>' +
        '<button type="button" class="evbtn" data-x="phone">Edit on your phone</button>' +
        '<button type="button" class="evbtn pri" data-x="edit">Change time or category</button></div>';
    }
    return '<div class="' + cls + '" role="dialog" aria-modal="true" aria-label="' + esc(n.title) + '" style="--c:' + n.color + '">' +
      '<div class="evh"><button type="button" class="evx" data-x="close" aria-label="Close">✕</button>' +
      '<div class="evbadges">' + badges + '</div>' +
      '<h2 class="evt">' + esc(n.title) + '</h2>' + when + '</div>' +
      (rows ? '<div class="evbody">' + rows + '</div>' : '') +
      '<div class="evqr" id="evqr" hidden></div>' +
      '<div class="evfoot" id="evfoot">' + acts + '</div>' +
      '<div class="evhint">Say “close” or swipe down</div></div>';
  }

  function renderQr(kind, url) {
    var cap = kind === 'meeting' ? 'Scan to join on your phone' : 'Scan to open your calendar on your phone';
    return '<div class="evqrbox"><canvas class="evqrc" width="180" height="180" data-qr="' + esc(url) + '"></canvas>' +
      '<div class="evqrcap">' + cap + '</div></div>';
  }
  // Where "Edit on your phone" points. The desktop calendar page is members-only
  // (the phone signs in, then lands on the calendar). A non-secret URL, so the
  // one CLIENT qrcode library is the right tool (see AGENTS.md: authCard rule).
  function phoneEditUrl(origin) {
    origin = str(origin).replace(/\/+$/, '');
    return /^https?:\/\/[^\s"'<>]+$/i.test(origin) ? origin + '/calendar.html' : '';
  }

  function renderDeleteConfirm(ev) {
    var n = normalize(ev);
    return '<div class="evconf"><div class="evcq">Delete “' + esc(n.title) + '”?</div>' +
      '<div class="evacts"><button type="button" class="evbtn" data-x="keep">Keep it</button>' +
      '<button type="button" class="evbtn del" data-x="delyes">Delete</button></div></div>';
  }

  // ---- render: category chips + edit sheet ----------------------------------
  function categoriesFor(current) {
    var c = String(current || '').toLowerCase();
    var list = CATEGORIES.slice();
    if (c && list.indexOf(c) < 0) list.push(c);   // never hide the one it already has
    return list;
  }
  function renderCategoryChips(selected) {
    var sel = String(selected || 'general').toLowerCase();
    return '<div class="evcats" role="radiogroup" aria-label="Category">' + categoriesFor(sel).map(function (c) {
      var on = c === sel, k = chipColors(c, on);
      return '<button type="button" class="evchip' + (on ? ' on' : '') + '" role="radio" aria-checked="' + (on ? 'true' : 'false') +
        '" data-cat="' + esc(c) + '" style="--cc:' + catColor(c) + ';background:' + k.bg + ';color:' + k.fg + '">' +
        '<i class="evck" aria-hidden="true">' + (on ? '✓' : '') + '</i>' + esc(titleCase(c)) + '</button>';
    }).join('') + '</div>';
  }
  // opts.dtHtml = the panel's date/time stepper markup (_dtHtml in home.html).
  function renderEdit(ev, o) {
    var n = normalize(ev, o);
    o = o || {};
    return '<div class="estmc evd eve" role="dialog" aria-modal="true" aria-label="Change ' + esc(n.title) + '" style="--c:' + n.color + '">' +
      '<div class="evh"><button type="button" class="evx" data-x="close" aria-label="Close">✕</button>' +
      '<div class="evbadges"><span class="evbadge">Changing</span></div><h2 class="evt">' + esc(n.title) + '</h2></div>' +
      '<div class="evbody one">' +
      row('Category', renderCategoryChips(n.category), true) +
      row('When', (o.dtHtml || '') +
        '<button type="button" class="evsw" role="switch" data-x="allday" aria-checked="' + (n.allDay || !firstStr(ev.start_time) ? 'true' : 'false') + '"><i></i>All day</button>', true) +
      row('Words', '<div class="evnote">Title, place, notes and who’s coming are typed — do those on your phone.</div>' +
        '<button type="button" class="evbtn" data-x="phone">Edit on your phone</button>', true) +
      '</div>' +
      '<div class="evqr" id="evqr" hidden></div>' +
      '<div class="evfoot"><div class="evacts"><button type="button" class="evbtn" data-x="back">Back</button><span class="evsp"></span>' +
      '<button type="button" class="evbtn pri" data-x="save">Save</button></div></div></div>';
  }

  // ---- save: build ONLY what changed ---------------------------------------
  // state = {category, date, time (HH:MM|null), allDay}. Never title/location/
  // metadata: PUT replaces metadata wholesale and the panel cannot edit text, so
  // the safest write is the narrowest one.
  function buildUpdate(ev, st) {
    ev = ev || {}; st = st || {};
    var body = {};
    var curCat = firstStr(ev.category).toLowerCase() || 'general';
    if (st.category && String(st.category).toLowerCase() !== curCat) body.category = String(st.category).toLowerCase();

    var wasAll = !!ev.all_day || !firstStr(ev.start_time), oldDate = firstStr(ev.start_date),
      oldTime = wasAll ? '' : firstStr(ev.start_time).slice(0, 5);   // a timeless event counts as all-day: a category-only save must not flip it
    var newAll = !!st.allDay || st.time == null;
    var newTime = newAll ? '' : String(st.time).slice(0, 5);
    var dateChanged = !!st.date && st.date !== oldDate;
    if (dateChanged) body.start_date = st.date;
    if (newAll !== wasAll) { body.all_day = newAll; body.start_time = newAll ? null : newTime; }
    else if (!newAll && newTime !== oldTime) body.start_time = newTime;

    // Keep the duration: moving the start moves the end by the same amount.
    if (!newAll && !wasAll) {
      var oldM = toMin(oldTime), newM = toMin(newTime), endM = toMin(ev.end_time);
      if (body.start_time && oldM != null && newM != null && endM != null) body.end_time = fromMin(endM + (newM - oldM));
    }
    if (dateChanged && firstStr(ev.end_date) && oldDate) {
      var delta = dayNum(st.date) - dayNum(oldDate);
      body.end_date = addDays(ev.end_date, delta);
    }
    return body;
  }
  // api(method,url,body) -> Promise<json>. Resolves {changed:false} without a request when nothing changed.
  function save(api, ev, st) {
    var body = buildUpdate(ev, st);
    if (!Object.keys(body).length) return Promise.resolve({ changed: false, event: ev });
    return api('PUT', '/api/calendar/events/' + encodeURIComponent(ev.id), body).then(function (resp) {
      var merged = {};
      Object.keys(ev).forEach(function (k) { merged[k] = ev[k]; });
      var src = isObj(resp) && resp.id ? resp : body;
      Object.keys(src).forEach(function (k) { merged[k] = src[k]; });
      return { changed: true, event: merged, body: body };
    });
  }

  // ---- gestures / voice ----------------------------------------------------
  // A pull-down that starts at the top of the sheet's scroll and is mostly vertical.
  function swipeShouldClose(g) {
    g = g || {};
    var dy = Number(g.dy) || 0, dx = Math.abs(Number(g.dx) || 0);
    return dy >= 120 && (Number(g.scrollTop) || 0) <= 1 && dx < dy * 0.7;
  }
  var CLOSE_WORDS = ['close', 'close it', 'close that', 'close this', 'close the event', 'close the details', 'close the window',
    'close the sheet', 'dismiss', 'dismiss it', 'go back', 'back', 'done', 'all done', 'thats all', 'never mind', 'nevermind', 'exit', 'cancel'];
  function isCloseUtterance(t) {
    var s = String(t || '').toLowerCase().replace(/[’']/g, '').replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim();
    s = s.replace(/^(hey |ok |okay )?(zoe )?(please )?/, '').replace(/( please| zoe| thanks| thank you)+$/, '').trim();
    return CLOSE_WORDS.indexOf(s) >= 0;
  }

  return {
    CATEGORIES: CATEGORIES, CAT_HEX: CAT_HEX, DARK_INK: DARK_INK, LIGHT_INK: LIGHT_INK,
    esc: esc, catColor: catColor, chipColors: chipColors, contrast: contrast, mix: mix,
    normalize: normalize, renderDetail: renderDetail, renderEdit: renderEdit,
    renderCategoryChips: renderCategoryChips, renderQr: renderQr, renderDeleteConfirm: renderDeleteConfirm,
    phoneEditUrl: phoneEditUrl, buildUpdate: buildUpdate, save: save,
    swipeShouldClose: swipeShouldClose, isCloseUtterance: isCloseUtterance
  };
});

/* ═══════════════════════════════════════════════════════════════════════════
   Orbit — Host Dashboard JS
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

// ── State ────────────────────────────────────────────────────────────────────

const HOST = {
  sessionId:    null,
  joinCode:     null,
  ws:           null,
  wsReconnect:  null,
  startTime:    null,
  durationMins: 120,
  lastOrdersMins: 30,
  durationMs:   0,
  lastOrdersAt: 0,
  lastOrdersFired: false,
  endFired: false,
  clockInterval: null,
  countdownInterval: null,
  statsCache:   {},
  zones:        {
    'bar':         'Bar',
    'beer-garden': 'Beer Garden',
    'pool-area':   'Pool Area',
    'outside':     'Outside',
  },
  // Challenge
  challengeActive: false,
  challengeId: null,
  challengeEndsAt: null,
  challengePrize: null,
  challengeCountdownInterval: null,
  // Speed dating
  sdActive: false,
};

// ── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  loadSavedSetup();
  bindSetupUI();
  bindDashboardUI();
  populateClock();

  // QD pattern: restore session on refresh instead of losing everything
  if (!await tryRestoreHostSession()) {
    showScreen('setup');
  }
});

async function tryRestoreHostSession() {
  const savedId   = sessionStorage.getItem('orbit_host_session_id');
  const savedCode = sessionStorage.getItem('orbit_host_join_code');
  if (!savedId) return false;

  // Validate the session is still active
  try {
    const res = await fetch(`/modules/orbit/api/sessions/${savedId}`);
    if (!res.ok) throw new Error();
    const session = await res.json();
    if (!session.active) throw new Error();

    // Restore HOST state
    HOST.sessionId      = savedId;
    HOST.joinCode       = savedCode || '';
    HOST.startTime      = parseInt(sessionStorage.getItem('orbit_host_start_time') || '0', 10) || Date.now();
    HOST.durationMins   = parseInt(sessionStorage.getItem('orbit_host_duration_mins') || '120', 10);
    HOST.lastOrdersMins = parseInt(sessionStorage.getItem('orbit_host_last_orders_mins') || '30', 10);
    HOST.durationMs     = HOST.durationMins > 0 ? HOST.durationMins * 60 * 1000 : 0;
    HOST.lastOrdersAt   = parseInt(sessionStorage.getItem('orbit_host_last_orders_at') || '0', 10);
    HOST.zones          = JSON.parse(sessionStorage.getItem('orbit_host_zones') || 'null') || HOST.zones;

    await launchDashboard(true);
    return true;
  } catch (_) {
    // Session gone — clear saved state
    clearHostSession();
    return false;
  }
}

function saveHostSession() {
  sessionStorage.setItem('orbit_host_session_id',       HOST.sessionId);
  sessionStorage.setItem('orbit_host_join_code',        HOST.joinCode || '');
  sessionStorage.setItem('orbit_host_start_time',       String(HOST.startTime || Date.now()));
  sessionStorage.setItem('orbit_host_duration_mins',    String(HOST.durationMins));
  sessionStorage.setItem('orbit_host_last_orders_mins', String(HOST.lastOrdersMins));
  sessionStorage.setItem('orbit_host_last_orders_at',   String(HOST.lastOrdersAt));
  sessionStorage.setItem('orbit_host_zones',            JSON.stringify(HOST.zones));
}

function clearHostSession() {
  ['orbit_host_session_id','orbit_host_join_code','orbit_host_start_time',
   'orbit_host_duration_mins','orbit_host_last_orders_mins','orbit_host_last_orders_at',
   'orbit_host_zones'].forEach(k => sessionStorage.removeItem(k));
}

// ── Setup screen ──────────────────────────────────────────────────────────────

function loadSavedSetup() {
  const saved = JSON.parse(localStorage.getItem('orbit_host_setup') || '{}');
  if (saved.venue)    document.getElementById('input-venue').value  = saved.venue;
  if (saved.event)    document.getElementById('input-event').value  = saved.event;
  if (saved.duration) selectChip('duration-chips', saved.duration);
  if (saved.lastOrders !== undefined) selectChip('last-orders-chips', saved.lastOrders);
  if (saved.zones) {
    Object.assign(HOST.zones, saved.zones);
    document.querySelectorAll('[data-zone]').forEach(inp => {
      inp.value = HOST.zones[inp.dataset.zone] || inp.value;
    });
  }
}

function bindSetupUI() {
  document.getElementById('btn-start-session').addEventListener('click', startSession);

  // Duration chip group
  document.getElementById('duration-chips').addEventListener('click', e => {
    const chip = e.target.closest('.setup-chip');
    if (chip) selectChip('duration-chips', chip.dataset.value);
    const noExpiry = chip && chip.dataset.value === '0';
    document.getElementById('last-orders-row').style.opacity = noExpiry ? '0.3' : '1';
    document.getElementById('last-orders-row').style.pointerEvents = noExpiry ? 'none' : '';
  });

  document.getElementById('last-orders-chips').addEventListener('click', e => {
    const chip = e.target.closest('.setup-chip');
    if (chip) selectChip('last-orders-chips', chip.dataset.value);
  });
}

function selectChip(groupId, value) {
  const container = document.getElementById(groupId);
  if (!container) return;
  container.querySelectorAll('.setup-chip').forEach(c => {
    c.classList.toggle('selected', String(c.dataset.value) === String(value));
  });
}

function getChipValue(groupId) {
  const chip = document.querySelector(`#${groupId} .setup-chip.selected`);
  return chip ? Number(chip.dataset.value) : null;
}

function readZones() {
  const zones = {};
  document.querySelectorAll('[data-zone]').forEach(inp => {
    zones[inp.dataset.zone] = inp.value.trim() || inp.placeholder;
  });
  return zones;
}

async function startSession() {
  const venue = document.getElementById('input-venue').value.trim();
  if (!venue) { showSetupError('Please enter a venue name.'); return; }

  const duration   = getChipValue('duration-chips');
  const lastOrders = getChipValue('last-orders-chips');
  const zones      = readZones();

  // Persist setup
  localStorage.setItem('orbit_host_setup', JSON.stringify({
    venue, event: document.getElementById('input-event').value.trim(),
    duration, lastOrders, zones,
  }));

  const btn = document.getElementById('btn-start-session');
  btn.disabled    = true;
  btn.textContent = 'Creating…';

  try {
    const res = await fetch('/modules/orbit/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ venue_name: venue }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    HOST.sessionId  = data.id;
    HOST.joinCode   = data.join_code || '';

    HOST.durationMins   = duration;
    HOST.lastOrdersMins = lastOrders;
    HOST.zones          = zones;
    HOST.startTime      = Date.now();
    HOST.durationMs     = duration > 0 ? duration * 60 * 1000 : 0;
    HOST.lastOrdersAt   = (duration > 0 && lastOrders > 0)
      ? HOST.startTime + (duration - lastOrders) * 60 * 1000
      : 0;
    HOST.lastOrdersFired = false;

    saveHostSession();
    await launchDashboard();
  } catch (err) {
    showSetupError('Could not start session. Check your connection.');
    btn.disabled    = false;
    btn.textContent = 'Start Orbit ✦';
  }
}

function showSetupError(msg) {
  const el = document.getElementById('setup-error');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 5000);
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

async function launchDashboard(isRestore = false) {
  showScreen('dashboard');

  // Event name header
  const savedSetup = JSON.parse(localStorage.getItem('orbit_host_setup') || '{}');
  const eventName  = savedSetup.event || savedSetup.venue || 'Orbit Night';
  document.getElementById('dash-event-name').textContent = eventName;

  // QR code + join code (only generate QR once — avoid duplicate on restore)
  const joinUrl = `${window.location.origin}/modules/orbit/checkin?s=${HOST.sessionId}`;
  document.getElementById('join-url-display').textContent = joinUrl;
  document.getElementById('btn-copy-url').onclick = () => {
    navigator.clipboard.writeText(joinUrl).then(() => showToast('URL copied!'));
  };
  const qrContainer = document.getElementById('host-qr');
  if (!qrContainer.querySelector('canvas') && !qrContainer.querySelector('img')) {
    new QRCode(qrContainer, {
      text: joinUrl,
      width: 120, height: 120,
      colorDark: '#0d0d1a',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M,
    });
  }
  // Show join code badge
  const codeEl = document.getElementById('join-code-display');
  if (codeEl && HOST.joinCode) {
    codeEl.textContent = HOST.joinCode;
    codeEl.closest('.join-code-badge') && (codeEl.closest('.join-code-badge').style.display = 'flex');
  }

  // Timers
  HOST.clockInterval    = setInterval(populateClock,  1000);
  HOST.countdownInterval = setInterval(tickCountdown, 1000);
  tickCountdown();

  // Solar system
  const canvas = document.getElementById('solar-canvas');
  canvas.width  = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;

  let attendees = [];
  try {
    const res = await fetch(`/modules/orbit/api/sessions/${HOST.sessionId}/attendees`);
    if (res.ok) {
      const data = await res.json();
      attendees = data.attendees || [];
    }
  } catch (_) {}
  SolarSystem.init(canvas, attendees);

  // Initial stats
  fetchStats();

  // WebSocket
  connectWS();
}

function bindDashboardUI() {
  document.getElementById('btn-last-orders').addEventListener('click', triggerLastOrders);
  document.getElementById('btn-end-session').addEventListener('click', confirmEndSession);
  document.getElementById('btn-close-reports').addEventListener('click', () => {
    document.getElementById('reports-panel').style.display = 'none';
  });

  document.getElementById('btn-show-zones').addEventListener('click', openZoneEditor);
  document.getElementById('btn-cancel-zones').addEventListener('click', () => {
    document.getElementById('zone-editor').style.display = 'none';
  });
  document.getElementById('btn-save-zones').addEventListener('click', saveZoneEditor);

  // Challenge
  document.getElementById('btn-challenge').addEventListener('click', () => {
    if (HOST.challengeActive) return; // already running
    document.getElementById('challenge-modal').style.display = 'flex';
  });
  document.getElementById('challenge-duration-chips').addEventListener('click', e => {
    const chip = e.target.closest('.setup-chip');
    if (chip) selectChip('challenge-duration-chips', chip.dataset.value);
  });
  document.getElementById('btn-cancel-challenge').addEventListener('click', () => {
    document.getElementById('challenge-modal').style.display = 'none';
  });
  document.getElementById('btn-start-challenge').addEventListener('click', startChallenge);
  document.getElementById('btn-dismiss-winner').addEventListener('click', () => {
    document.getElementById('winner-overlay').style.display = 'none';
  });

  // Speed dating
  document.getElementById('btn-speed-dating').addEventListener('click', () => {
    if (HOST.sdActive) {
      endSpeedDating();
    } else {
      document.getElementById('sd-modal').style.display = 'flex';
    }
  });
  document.getElementById('sd-duration-chips').addEventListener('click', e => {
    const chip = e.target.closest('.setup-chip');
    if (chip) selectChip('sd-duration-chips', chip.dataset.value);
  });
  document.getElementById('btn-cancel-sd').addEventListener('click', () => {
    document.getElementById('sd-modal').style.display = 'none';
  });
  document.getElementById('btn-start-sd').addEventListener('click', startSpeedDating);
}

function openZoneEditor() {
  const container = document.getElementById('zone-edit-rows');
  container.innerHTML = '';
  Object.entries(HOST.zones).forEach(([key, val]) => {
    const row = document.createElement('div');
    row.className = 'zone-edit-row';
    row.innerHTML = `<span class="zone-key">${key}</span>
      <input type="text" data-zone="${key}" value="${escHtml(val)}" maxlength="24">`;
    container.appendChild(row);
  });
  document.getElementById('zone-editor').style.display = 'flex';
}

function saveZoneEditor() {
  document.querySelectorAll('#zone-edit-rows [data-zone]').forEach(inp => {
    HOST.zones[inp.dataset.zone] = inp.value.trim() || inp.dataset.zone;
  });
  document.getElementById('zone-editor').style.display = 'none';
  showToast('Zone names saved');
}

// ── Clocks ────────────────────────────────────────────────────────────────────

function populateClock() {
  const el = document.getElementById('dash-clock');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function tickCountdown() {
  if (!HOST.durationMs) {
    document.getElementById('countdown-label').textContent = 'Open session';
    document.getElementById('countdown-value').textContent = '∞';
    return;
  }

  const elapsed = Date.now() - HOST.startTime;
  const left    = HOST.durationMs - elapsed;

  // Fire last orders
  if (HOST.lastOrdersAt && !HOST.lastOrdersFired && Date.now() >= HOST.lastOrdersAt) {
    triggerLastOrders(true);
  }

  if (left <= 0) {
    document.getElementById('countdown-value').textContent = '00:00';
    if (!HOST.endFired) {
      HOST.endFired = true;
      autoEndSession();
    }
    return;
  }

  const mins = Math.floor(left / 60000);
  const secs = Math.floor((left % 60000) / 1000);
  document.getElementById('countdown-value').textContent =
    `${String(mins).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;

  // Warning colour
  const countdownEl = document.getElementById('countdown-display');
  if (mins < 5)       countdownEl.classList.add('danger');
  else if (mins < 15) countdownEl.classList.add('warning');
}

// ── Last Orders ───────────────────────────────────────────────────────────────

function triggerLastOrders(auto = false) {
  if (HOST.lastOrdersFired) return;
  HOST.lastOrdersFired = true;

  fetch(`/modules/orbit/api/sessions/${HOST.sessionId}/last-orders`, { method: 'POST' })
    .catch(() => {});

  const overlay = document.getElementById('last-orders-overlay');
  overlay.style.display = 'flex';
  SolarSystem.onEvent('last_orders_triggered', {});
  addFeedItem('🔔', 'Last Orders — guests are exchanging contacts', 'last-orders');
  setTimeout(() => { overlay.style.display = 'none'; }, 6000);
}

// ── End session ───────────────────────────────────────────────────────────────

function confirmEndSession() {
  if (!confirm('End the Orbit session? This will notify all guests.')) return;
  autoEndSession();
}

async function autoEndSession() {
  clearInterval(HOST.clockInterval);
  clearInterval(HOST.countdownInterval);
  SolarSystem.onEvent('session_ended', {});

  try {
    await fetch(`/modules/orbit/api/sessions/${HOST.sessionId}/end`, { method: 'POST' });
  } catch (_) {}

  clearHostSession();  // QD pattern: wipe saved state so refresh shows setup
  addFeedItem('✦', 'Orbit session ended', 'session-end');
  showToast('Session ended');
  if (HOST.ws) HOST.ws.close();
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWS() {
  if (HOST.ws) HOST.ws.close();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  HOST.ws = new WebSocket(
    `${proto}://${location.host}/modules/orbit/ws/host/${HOST.sessionId}`
  );

  HOST.ws.onopen = () => {
    clearTimeout(HOST.wsReconnect);
    showToast('Connected live ✓');
  };

  HOST.ws.onmessage = e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch(_) { return; }
    handleWS(msg.type, msg.data || {});
  };

  HOST.ws.onclose = () => {
    HOST.wsReconnect = setTimeout(connectWS, 3000);
  };
}

function handleWS(type, data) {
  // Forward every event to solar system
  SolarSystem.onEvent(type, data);

  switch (type) {

    case 'stats_update':
      applyStats(data);
      break;

    case 'new_checkin':
      bumpStat('stat-total');
      addFeedItem(
        intentEmoji(data.intent),
        `${escHtml(data.display_name || 'Someone')} joined orbit — ${escHtml(data.zone || '')}`,
        'checkin'
      );
      break;

    case 'meet_confirmed':
      bumpStat('stat-meets');
      addFeedItem('✨', 'A meet was confirmed', 'meet');
      break;

    case 'interaction':
      addFeedItem('👋', `${escHtml(data.sender_name || '?')} → ${escHtml(data.receiver_name || '?')}: ${escHtml(data.type || '')}`, 'interaction');
      break;

    case 'safety_report':
      bumpStat('stat-reports');
      addFeedItem('⚠', `Safety report received (${escHtml(data.reason || 'unspecified')})`, 'report');
      showReportsPanel(data);
      break;

    case 'last_orders_triggered':
      if (!HOST.lastOrdersFired) triggerLastOrders(true);
      break;

    case 'challenge_started':
      HOST.challengeActive = true;
      HOST.challengeId = data.challenge_id;
      HOST.challengeEndsAt = data.ends_at;
      HOST.challengePrize = data.prize_text || null;
      document.getElementById('challenge-modal').style.display = 'none';
      document.getElementById('challenge-overlay').style.display = 'block';
      document.getElementById('btn-challenge').classList.add('active');
      startChallengeCountdown(data.ends_at);
      addFeedItem('🎯', `Scan Challenge started! ${data.duration_seconds}s${data.prize_text ? ' — Prize: ' + data.prize_text : ''}`, 'challenge');
      break;

    case 'leaderboard_update':
      updateChallengeLeaderboard(data.top || []);
      break;

    case 'challenge_ended':
      HOST.challengeActive = false;
      if (HOST.challengeCountdownInterval) clearInterval(HOST.challengeCountdownInterval);
      document.getElementById('challenge-overlay').style.display = 'none';
      document.getElementById('btn-challenge').classList.remove('active');
      if (data.winner) {
        showWinner(data.winner);
        addFeedItem('🏆', `Challenge won by ${escHtml(data.winner.display_name)} with ${data.winner.points} point(s)`, 'challenge');
      }
      break;

    case 'speed_dating_started':
      HOST.sdActive = true;
      document.getElementById('btn-speed-dating').textContent = '⏱ End Speed Dating';
      document.getElementById('btn-speed-dating').classList.add('active');
      addFeedItem('⏱', `Speed Dating activated — ${Math.floor(data.duration_seconds/60)} min rounds`, 'sd');
      break;

    case 'speed_dating_paired':
      addFeedItem('💫', `Speed date pair: scanning started`, 'sd');
      break;

    case 'speed_dating_ended':
      HOST.sdActive = false;
      document.getElementById('btn-speed-dating').textContent = '⏱ Speed Dating';
      document.getElementById('btn-speed-dating').classList.remove('active');
      addFeedItem('✓', 'Speed Dating mode ended', 'sd');
      break;

    case 'speed_dating_match':
      bumpStat('stat-connections');
      addFeedItem('✦', `Speed date match made!`, 'meet');
      break;
  }
}

// ── Stats ─────────────────────────────────────────────────────────────────────

async function fetchStats() {
  try {
    const res = await fetch(`/modules/orbit/api/sessions/${HOST.sessionId}/stats`);
    if (res.ok) applyStats(await res.json());
  } catch (_) {}
}

function applyStats(data) {
  HOST.statsCache = data;
  SolarSystem.onEvent('stats_update', data);

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el && el.textContent !== String(val)) {
      el.textContent = val;
      el.closest('.stat-bubble')?.classList.add('bump');
      setTimeout(() => el.closest('.stat-bubble')?.classList.remove('bump'), 500);
    }
  };
  setEl('stat-total',       data.total       || 0);
  setEl('stat-meets',       data.confirmed_meets || 0);
  setEl('stat-connections', data.connections  || 0);
  setEl('stat-reports',     data.safety_reports || 0);
}

function bumpStat(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const n = (parseInt(el.textContent) || 0) + 1;
  el.textContent = n;
  el.closest('.stat-bubble')?.classList.add('bump');
  setTimeout(() => el.closest('.stat-bubble')?.classList.remove('bump'), 500);
}

// ── Feed ticker ───────────────────────────────────────────────────────────────

function addFeedItem(icon, text, type) {
  const ticker = document.getElementById('feed-ticker');
  if (!ticker) return;

  const item = document.createElement('div');
  item.className = `feed-item feed-${type}`;
  const time = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  item.innerHTML = `<span class="feed-icon">${icon}</span>
    <span class="feed-text">${escHtml(text)}</span>
    <span class="feed-time">${time}</span>`;
  ticker.prepend(item);

  while (ticker.children.length > 30) ticker.lastChild.remove();
}

// ── Safety reports panel ──────────────────────────────────────────────────────

function showReportsPanel(data) {
  const panel = document.getElementById('reports-panel');
  panel.style.display = 'flex';
  const list = document.getElementById('reports-list');
  const item = document.createElement('div');
  item.className = 'report-item';
  item.innerHTML = `<strong>${escHtml(data.reporter_id || 'anonymous')}</strong> → 
    ${escHtml(data.reported_id || 'unknown')}: ${escHtml(data.reason || 'No reason given')}
    <span class="report-time">${new Date().toLocaleTimeString('en-GB')}</span>`;
  list.prepend(item);
  const badge = document.getElementById('reports-badge');
  if (badge) badge.textContent = list.children.length;
}

// ── Screen transition ─────────────────────────────────────────────────────────

function showScreen(name) {
  document.querySelectorAll('.host-screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById(`screen-${name}`);
  if (el) el.classList.add('active');
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  c.appendChild(t);
  requestAnimationFrame(() => t.classList.add('visible'));
  setTimeout(() => {
    t.classList.remove('visible');
    setTimeout(() => t.remove(), 400);
  }, 2600);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function intentEmoji(intent) {
  return { romantic: '💫', activity: '🎯', social: '✨' }[intent] || '✦';
}

function escHtml(str) {
  return String(str || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

// ── Scan Challenge ─────────────────────────────────────────────────────────────

async function startChallenge() {
  const duration = getChipValue('challenge-duration-chips') || 90;
  const prize = document.getElementById('challenge-prize').value.trim() || null;
  try {
    const res = await fetch(`/modules/orbit/api/challenges`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        session_id: HOST.sessionId,
        duration_seconds: duration,
        prize_text: prize,
      }),
    });
    if (!res.ok) throw new Error();
    // WS broadcast will handle UI update
  } catch(_) {
    showToast('Could not start challenge. Please try again.');
  }
}

function startChallengeCountdown(endsAt) {
  if (HOST.challengeCountdownInterval) clearInterval(HOST.challengeCountdownInterval);
  const tick = () => {
    const rem = Math.max(0, endsAt - Date.now()/1000);
    const mins = Math.floor(rem/60), secs = Math.floor(rem%60);
    const el = document.getElementById('challenge-countdown');
    if (el) el.textContent = `${mins}:${String(secs).padStart(2,'0')}`;
  };
  tick();
  HOST.challengeCountdownInterval = setInterval(tick, 500);
}

function updateChallengeLeaderboard(top) {
  const el = document.getElementById('challenge-leaderboard');
  if (!el) return;
  const medals = ['🥇','🥈','🥉'];
  el.innerHTML = top.slice(0,5).map((row,i) =>
    `<div class="challenge-lb-row">
      <span class="challenge-lb-rank">${medals[i]||'·'}</span>
      <span class="challenge-lb-name">${escHtml(row.display_name)}</span>
      <span class="challenge-lb-pts">${row.points}pt</span>
    </div>`
  ).join('');
}

function showWinner(winner) {
  document.getElementById('winner-name').textContent = winner.display_name;
  document.getElementById('winner-pts').textContent = `${winner.points} point(s)`;
  document.getElementById('winner-prize').textContent = HOST.challengePrize || '';
  document.getElementById('winner-overlay').style.display = 'flex';
}

// ── Speed Dating ──────────────────────────────────────────────────────────────

async function startSpeedDating() {
  const duration = getChipValue('sd-duration-chips') || 240;
  try {
    const res = await fetch(`/modules/orbit/api/speed-dating/start`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        session_id: HOST.sessionId,
        round_duration_seconds: duration,
      }),
    });
    if (!res.ok) throw new Error();
    document.getElementById('sd-modal').style.display = 'none';
    // WS broadcast handles UI
  } catch(_) {
    showToast('Could not start speed dating. Please try again.');
  }
}

async function endSpeedDating() {
  if (!confirm('End Speed Dating mode?')) return;
  try {
    await fetch(`/modules/orbit/api/speed-dating/end?session_id=${HOST.sessionId}`, {
      method: 'POST',
    });
  } catch(_) {}
}

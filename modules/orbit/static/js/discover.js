(function () {
  'use strict';

  const params = new URLSearchParams(location.search);
  const sessionId = params.get('s') || sessionStorage.getItem('orbit_session_id') || localStorage.getItem('orbit_last_session_id');
  const checkinId = params.get('c') || sessionStorage.getItem('orbit_checkin_id');

  if (!sessionId || !checkinId) {
    // No session/checkin — go back to checkin screen (QD pattern)
    window.location.href = '/modules/orbit/checkin';
    return;
  }

  sessionStorage.setItem('orbit_session_id', sessionId);
  sessionStorage.setItem('orbit_checkin_id', checkinId);

  const INTENT_LABELS = {
    social:   { label: 'Social', cls: 'badge-social' },
    activity: { label: 'Up for something', cls: 'badge-activity' },
    romantic: { label: 'Open to more', cls: 'badge-romantic' },
  };

  let ws = null;
  let pendingConnectionCount = 0;
  let pendingConnections = [];
  let metPeople = [];            // ids I've confirmed met
  let pendingContactTo = null;   // checkin_id we're about to share contact with

  // ── WebSocket ──────────────────────────────────────────────────────────────

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/modules/orbit/ws/presence/${sessionId}/${checkinId}`);

    ws.onopen = () => {
      startPing();
      loadMatches();
      loadPendingConnections();
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      handleWS(msg);
    };

    ws.onclose = () => {
      setTimeout(connectWS, 3000);
    };
  }

  let pingTimer = null;
  function startPing() {
    clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 25000);
  }

  function handleWS(msg) {
    switch (msg.type) {
      case 'matches_updated':
        renderMatches(msg.data.matches || []);
        break;

      case 'interaction_received':
        showToast(`${msg.data.from_name} ${msg.data.label}${msg.data.zone ? ' · ' + msg.data.zone : ''}`, false);
        break;

      case 'connection_request':
        pendingConnectionCount++;
        loadPendingConnections();
        updateConnectionsBadge();
        break;

      case 'connection_accepted':
        showToast(`✓ ${msg.data.name} accepted your connection — "${msg.data.icebreaker}"`, false);
        loadPendingConnections();
        break;

      case 'mutual_scan':
        showToast(`✦ It's mutual — you both scanned each other! "${msg.data.icebreaker}"`, true);
        loadPendingConnections();
        break;

      case 'mutual_met':
        showToast(`✓ Mutual meet confirmed! Check your connections to share details.`, false);
        loadMatches();
        renderMetSection();
        break;

      case 'contact_received':
        showContactReceivedModal(msg.data);
        break;

      case 'last_orders':
        showToast(`🔔 ${msg.data.message}`, false);
        renderMetSection();
        break;

      case 'session_ended':
        showToast('The Orbit session has ended. Thanks for coming out!', false);
        setTimeout(() => window.location.href = '/modules/orbit/', 3000);
        break;

      case 'removed_by_host':
        alert(msg.data.message);
        window.location.href = '/';
        break;

      case 'auto_checkout':
        window.location.href = '/';
        break;

      case 'challenge_started':
        showToast(`🎯 Scan Challenge is live! Scan people and answer questions to win.`, false);
        break;

      case 'challenge_ended':
        if (msg.data.winner) {
          showToast(`🏆 Challenge over! Winner: ${msg.data.winner.display_name} with ${msg.data.winner.points} point(s)`, true);
        } else {
          showToast('🏁 Scan Challenge has ended!', false);
        }
        break;

      case 'speed_dating_started':
        showSpeedDatingBanner(msg.data.duration_seconds);
        break;

      case 'speed_dating_ended':
        hideSpeedDatingBanner();
        hideSDTimerOverlay();
        break;

      case 'speed_dating_paired':
        // We were scanned — show timer overlay so our screen also shows the countdown
        showSDTimerOverlay(msg.data.partner_name, msg.data.ends_at, msg.data.duration_seconds);
        break;

      case 'speed_dating_match':
        showToast(`✦ Speed date match! You and ${msg.data.name} both said yes.`, true);
        break;

      case 'leaderboard_update':
        updateMiniLeaderboard(msg.data.top || []);
        break;
    }
  }

  // ── Speed dating banner ────────────────────────────────────────────────────

  function showSpeedDatingBanner(durationSecs) {
    let banner = document.getElementById('sd-banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'sd-banner';
      banner.style.cssText = `
        position:fixed;top:0;left:0;right:0;z-index:200;
        background:linear-gradient(135deg,rgba(251,191,36,0.2),rgba(251,191,36,0.08));
        border-bottom:1px solid rgba(251,191,36,0.3);
        padding:10px 16px;text-align:center;
        font-size:0.875rem;font-weight:600;color:#fbbf24;
        backdrop-filter:blur(10px);
      `;
      document.body.prepend(banner);
    }
    const mins = Math.floor(durationSecs / 60);
    banner.innerHTML = `⏱ Speed Dating is live — rounds are ${mins} min. <strong>Scan someone to start your timer!</strong>`;
  }

  function hideSpeedDatingBanner() {
    const banner = document.getElementById('sd-banner');
    if (banner) banner.remove();
    const overlay = document.getElementById('sd-timer-overlay');
    if (overlay) overlay.remove();
  }

  let sdOverlayInterval = null;

  function showSDTimerOverlay(partnerName, endsAt, durationSecs) {
    let overlay = document.getElementById('sd-timer-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'sd-timer-overlay';
      overlay.style.cssText = `
        position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:300;
        background:rgba(7,7,14,0.9);border:1px solid rgba(251,191,36,0.4);
        border-radius:16px;padding:16px 24px;text-align:center;
        min-width:180px;
        backdrop-filter:blur(16px);
      `;
      document.body.appendChild(overlay);
    }
    if (sdOverlayInterval) clearInterval(sdOverlayInterval);
    const render = () => {
      const rem = Math.max(0, endsAt - Date.now()/1000);
      const mins = Math.floor(rem/60), secs = Math.floor(rem%60);
      overlay.innerHTML = `
        <div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:.08em;color:rgba(251,191,36,0.7);margin-bottom:4px;">⏱ Speed date with</div>
        <div style="font-weight:700;font-size:1rem;color:#fde68a;margin-bottom:8px;">${escHtml(partnerName)}</div>
        <div style="font-size:2.5rem;font-weight:800;font-family:'Space Grotesk',sans-serif;color:#fbbf24;line-height:1;">${mins}:${String(secs).padStart(2,'0')}</div>
      `;
      if (rem <= 0) {
        clearInterval(sdOverlayInterval);
        overlay.remove();
        if ('vibrate' in navigator) navigator.vibrate([200,100,200]);
        showToast('Time\'s up! Check the other person\'s screen if you want to connect.', false);
      }
    };
    render();
    sdOverlayInterval = setInterval(render, 500);
  }

  function hideSDTimerOverlay() {
    if (sdOverlayInterval) clearInterval(sdOverlayInterval);
    const overlay = document.getElementById('sd-timer-overlay');
    if (overlay) overlay.remove();
  }

  // ── Mini challenge leaderboard ─────────────────────────────────────────────

  function updateMiniLeaderboard(top) {
    let lb = document.getElementById('mini-leaderboard');
    if (!top.length) { if (lb) lb.remove(); return; }
    if (!lb) {
      lb = document.createElement('div');
      lb.id = 'mini-leaderboard';
      lb.style.cssText = `
        position:fixed;top:60px;right:12px;z-index:250;
        background:rgba(7,7,14,0.88);border:1px solid rgba(251,191,36,0.3);
        border-radius:12px;padding:10px 14px;min-width:160px;
        backdrop-filter:blur(12px);
      `;
      document.body.appendChild(lb);
    }
    lb.innerHTML = `<div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:.07em;color:rgba(251,191,36,0.7);margin-bottom:6px;">🎯 Challenge</div>` +
      top.map((r,i)=>`<div style="display:flex;justify-content:space-between;gap:12px;font-size:0.85rem;margin:2px 0;">
        <span style="color:rgba(226,232,240,0.8)">${['🥇','🥈','🥉'][i]||'·'} ${escHtml(r.display_name)}</span>
        <span style="color:rgba(251,191,36,0.8);font-weight:700">${r.points}pt</span>
      </div>`).join('');
  }

  // ── Load matches ───────────────────────────────────────────────────────────

  async function loadMatches() {
    try {
      const res = await fetch(`/api/checkins/${checkinId}/matches`);
      if (!res.ok) return;
      const data = await res.json();
      renderMatches(data.matches || []);
    } catch (_) {}
  }

  function renderMatches(matches) {
    const container = document.getElementById('match-cards');
    const empty = document.getElementById('matches-empty');

    // Clear existing match cards (keep empty state)
    container.querySelectorAll('.match-card').forEach(c => c.remove());

    if (!matches.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    matches.forEach(m => {
      const card = buildMatchCard(m);
      container.appendChild(card);
    });
  }

  function buildMatchCard(m) {
    const p = m.person;
    const intentInfo = INTENT_LABELS[p.intent] || { label: p.intent, cls: 'badge-social' };
    const alreadyMet = m.met_by_me && m.met_by_them;
    const iMetThem = m.met_by_me;

    const card = document.createElement('div');
    card.className = 'match-card pop-in';
    card.dataset.matchId = m.match_id;
    card.dataset.personId = p.id;
    card.dataset.intent = p.intent || 'social';

    const sharedInterests = p.interests.slice(0, 4)
      .map(i => {
        const intense = p.interest_intensity && p.interest_intensity[i] === 2;
        return `<span class="chip" style="padding:4px 10px;font-size:0.78rem;${intense ? 'border-color:var(--accent);color:var(--accent)' : ''}">${i}${intense ? ' ★' : ''}</span>`;
      }).join('');

    const zoneHtml = p.zone
      ? `<span class="zone-hint">📍 ${p.zone.replace(/-/g, ' ')}</span>`
      : '';

    card.innerHTML = `
      <div class="match-header">
        <span class="match-name">${escHtml(p.display_name)}</span>
        ${zoneHtml}
      </div>
      <div class="match-meta">
        <span class="badge ${intentInfo.cls}">${intentInfo.label}</span>
        ${p.group_size > 1 ? `<span class="badge" style="background:var(--bg-elevated);color:var(--text-muted)">Group of ${p.group_size}</span>` : ''}
      </div>
      ${sharedInterests ? `<div class="chip-grid" style="margin:8px 0;">${sharedInterests}</div>` : ''}
      <div class="icebreaker">${escHtml(m.icebreaker)}</div>
      ${alreadyMet ? `<div class="met-confirmed">✓ You both confirmed this meet</div>` : ''}
      ${!alreadyMet ? buildActionButtons(m, p) : ''}
    `;

    if (!alreadyMet) {
      attachCardActions(card, m, p);
    }

    return card;
  }

  function buildActionButtons(m, p) {
    const iMetThem = m.met_by_me;
    return `
      <div class="match-actions">
        <button class="btn btn-secondary btn-sm" data-action="say_hey">👋 Say hey</button>
        <button class="btn btn-secondary btn-sm" data-action="join_drink">🍻 Join us</button>
        ${p.activities && p.activities.length
          ? `<button class="btn btn-secondary btn-sm" data-action="join_activity">🎯 ${escHtml(p.activities[0])}</button>`
          : ''}
      </div>
      <div class="match-actions" style="margin-top:6px;">
        <button class="btn ${iMetThem ? 'btn-primary' : 'btn-ghost'} btn-sm" data-action="met">
          ${iMetThem ? '✓ We met' : 'We met'}
        </button>
      </div>
    `;
  }

  function attachCardActions(card, m, p) {
    card.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = btn.dataset.action;
        if (action === 'met') {
          await confirmMet(p.id, card);
        } else {
          await sendInteraction(p.id, action, p, card);
        }
      });
    });
  }

  async function sendInteraction(toId, type, person, card) {
    const body = {
      session_id: sessionId,
      sender_id: checkinId,
      receiver_id: toId,
      type: type,
      payload: type === 'join_activity' && person.activities
        ? { activity: person.activities[0] }
        : null,
    };
    try {
      await fetch('/api/interactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      // Briefly dim the button
      const btn = card.querySelector(`[data-action="${type}"]`);
      if (btn) { btn.textContent = '✓ Sent'; btn.disabled = true; }
    } catch (_) {}
  }

  async function confirmMet(otherId, card) {
    try {
      await fetch(`/api/matches/confirm-met?checkin_id=${checkinId}&other_id=${otherId}`, {
        method: 'POST',
      });
      if (!metPeople.includes(otherId)) metPeople.push(otherId);
      // Reload to reflect met state
      loadMatches();
      renderMetSection();
    } catch (_) {}
  }

  // ── QR code ────────────────────────────────────────────────────────────────

  document.getElementById('btn-show-qr').addEventListener('click', showQRModal);
  document.getElementById('btn-close-qr').addEventListener('click', () => {
    document.getElementById('qr-modal').classList.add('hidden');
  });

  function showQRModal() {
    const modal = document.getElementById('qr-modal');
    modal.classList.remove('hidden');

    const canvas = document.getElementById('qr-canvas');
    canvas.innerHTML = '';

    const url = `${location.protocol}//${location.host}/modules/orbit/connect/${checkinId}?s=${sessionId}`;
    if (typeof QRCode !== 'undefined') {
      new QRCode(canvas, {
        text: url,
        width: 200,
        height: 200,
        colorDark: '#000',
        colorLight: '#fff',
        correctLevel: QRCode.CorrectLevel.M,
      });
    } else {
      canvas.textContent = url;
    }
  }

  // ── Connections (QR scan) ──────────────────────────────────────────────────

  async function loadPendingConnections() {
    try {
      const res = await fetch(`/api/connections/${checkinId}/pending`);
      if (!res.ok) return;
      const data = await res.json();
      pendingConnections = data.connections || [];
      updateConnectionsBadge();
      renderConnectionCards();
    } catch (_) {}
  }

  function updateConnectionsBadge() {
    const badge = document.getElementById('connections-badge');
    badge.style.display = pendingConnections.length ? 'inline' : 'none';
    badge.textContent = pendingConnections.length || '';
  }

  function renderConnectionCards() {
    const section = document.getElementById('connections-section');
    const container = document.getElementById('connection-cards');
    container.innerHTML = '';

    if (!pendingConnections.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = 'block';

    pendingConnections.forEach(conn => {
      const card = document.createElement('div');
      card.className = `connection-card${conn.is_mutual ? ' mutual' : ''}`;
      card.innerHTML = `
        <div class="conn-info">
          <div class="conn-name">
            ${conn.is_mutual ? '<span class="badge badge-mutual">✦ Mutual</span> ' : ''}
            ${escHtml(conn.display_name)}
          </div>
          <div class="conn-sub">${conn.intent || ''} ${conn.zone ? '· ' + conn.zone : ''}</div>
        </div>
        <div style="display:flex; gap:6px;">
          ${conn.is_mutual ? '' : `
            <button class="btn btn-secondary btn-sm" data-conn-decline="${conn.id}">Nope</button>
            <button class="btn btn-primary btn-sm" data-conn-accept="${conn.id}">Connect</button>
          `}
        </div>
      `;

      if (!conn.is_mutual) {
        card.querySelector(`[data-conn-accept]`).addEventListener('click', () =>
          respondConnection(conn.id, 'accept')
        );
        card.querySelector(`[data-conn-decline]`).addEventListener('click', () =>
          respondConnection(conn.id, 'decline')
        );
      }

      container.appendChild(card);
    });
  }

  async function respondConnection(connId, action) {
    try {
      await fetch(`/api/connections/${connId}?checkin_id=${checkinId}&action=${action}`, {
        method: 'PATCH',
      });
      pendingConnections = pendingConnections.filter(c => c.id !== connId);
      updateConnectionsBadge();
      renderConnectionCards();
    } catch (_) {}
  }

  // ── Confirmed meets + contact exchange ────────────────────────────────────

  async function renderMetSection() {
    if (!metPeople.length) return;
    const section = document.getElementById('met-section');
    const container = document.getElementById('met-cards');
    section.style.display = 'block';
    container.innerHTML = '';

    // For simplicity we show share button for all confirmed meets
    metPeople.forEach(otherId => {
      const card = document.createElement('div');
      card.className = 'connection-card';
      card.innerHTML = `
        <div class="conn-info">
          <div class="conn-name">Someone you met ✓</div>
          <div class="conn-sub">Share details to stay in touch</div>
        </div>
        <button class="btn btn-primary btn-sm" data-met-share="${otherId}">Share details</button>
      `;
      card.querySelector('[data-met-share]').addEventListener('click', () => {
        pendingContactTo = otherId;
        document.getElementById('contact-modal').classList.remove('hidden');
      });
      container.appendChild(card);
    });
  }

  // ── Contact exchange modal ────────────────────────────────────────────────

  document.getElementById('btn-contact-cancel').addEventListener('click', () => {
    document.getElementById('contact-modal').classList.add('hidden');
    pendingContactTo = null;
  });

  document.getElementById('btn-contact-send').addEventListener('click', async () => {
    if (!pendingContactTo) return;
    const contactData = {
      phone:     document.getElementById('contact-phone').value.trim() || null,
      instagram: document.getElementById('contact-instagram').value.trim() || null,
      whatsapp:  document.getElementById('contact-whatsapp').value.trim() || null,
      other:     document.getElementById('contact-other').value.trim() || null,
    };
    try {
      await fetch('/api/contact-exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          from_id: checkinId,
          to_id: pendingContactTo,
          contact_data: contactData,
        }),
      });
      document.getElementById('contact-modal').classList.add('hidden');
      showToast('Details shared ✓', false);
      pendingContactTo = null;
    } catch (_) {}
  });

  function showContactReceivedModal(data) {
    const modal = document.getElementById('received-contact-modal');
    document.getElementById('received-contact-from').textContent = `${data.from_name} shared their details with you.`;

    // Fetch the actual contact data
    fetch(`/api/contact-exchange/${data.exchange_id}?checkin_id=${checkinId}`)
      .then(r => r.json())
      .then(d => {
        const cd = d.contact_data || {};
        const lines = [];
        if (cd.phone)     lines.push(`📱 ${cd.phone}`);
        if (cd.whatsapp)  lines.push(`💬 WhatsApp: ${cd.whatsapp}`);
        if (cd.instagram) lines.push(`📸 @${cd.instagram}`);
        if (cd.other)     lines.push(`🔗 ${cd.other}`);
        document.getElementById('received-contact-data').innerHTML =
          lines.map(l => `<p style="margin:6px 0;font-size:0.95rem;">${escHtml(l)}</p>`).join('');
        modal.classList.remove('hidden');
      })
      .catch(() => {});
  }

  document.getElementById('btn-close-received').addEventListener('click', () => {
    document.getElementById('received-contact-modal').classList.add('hidden');
  });

  // ── Leave ──────────────────────────────────────────────────────────────────

  document.getElementById('btn-leave').addEventListener('click', async () => {
    if (!confirm('Leave Orbit? You\'ll disappear from everyone\'s matches.')) return;
    try {
      await fetch(`/api/checkins/${checkinId}/checkout`, { method: 'POST' });
    } catch (_) {}
    sessionStorage.removeItem('orbit_checkin_id');
    sessionStorage.removeItem('orbit_session_id');
    window.location.href = '/';
  });

  // ── Toast ──────────────────────────────────────────────────────────────────

  function showToast(message, isMutual) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast${isMutual ? ' toast-mutual' : ''}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function escHtml(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Boot ───────────────────────────────────────────────────────────────────

  connectWS();

  // Refresh matches every 90 seconds as people come and go
  setInterval(loadMatches, 90000);

})();

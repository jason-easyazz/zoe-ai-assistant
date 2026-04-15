(function () {
  'use strict';

  // ── Persistent state (survives page navigation) ────────────────────────────
  // Mirrors QD's approach: store session + checkin IDs in sessionStorage so
  // they survive navigate-back without relying on URL params at all.

  const state = {
    sessionId:  sessionStorage.getItem('orbit_session_id')  || localStorage.getItem('orbit_last_session_id') || null,
    checkinId:  sessionStorage.getItem('orbit_checkin_id')  || null,
    sessionName: sessionStorage.getItem('orbit_session_name') || '',
    selectedIntents: [],
    selectedZone: null,
    selectedActivities: [],
  };

  // Also accept session ID from URL param (QR code scan)
  const params = new URLSearchParams(location.search);
  const urlSessionId = params.get('s') || params.get('session');
  if (urlSessionId) {
    state.sessionId = urlSessionId;
    sessionStorage.setItem('orbit_session_id', urlSessionId);
    localStorage.setItem('orbit_last_session_id', urlSessionId);
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function loadProfile() {
    try {
      const raw = localStorage.getItem('orbit_profile');
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }

  function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const s = document.getElementById(id);
    if (s) s.classList.add('active');
  }

  function updateZoneLabels(names) {
    document.querySelectorAll('#zone-grid .zone-btn').forEach(btn => {
      const zone = btn.dataset.zone;
      if (names[zone]) btn.textContent = names[zone];
    });
  }

  // ── Auto-reconnect (QD pattern) ────────────────────────────────────────────
  // If we already have a checkin ID in sessionStorage, skip straight to discover.

  function tryReconnectOnLoad() {
    if (state.sessionId && state.checkinId) {
      window.location.href = `/modules/orbit/discover?s=${state.sessionId}&c=${state.checkinId}`;
      return true;
    }
    return false;
  }

  // ── Boot ───────────────────────────────────────────────────────────────────

  async function boot() {
    // If already checked in this tab/session, go straight to discover
    if (tryReconnectOnLoad()) return;

    const profile = loadProfile();

    // If we have a session ID already (from URL or localStorage), validate it
    // and skip the code-entry screen
    if (state.sessionId) {
      const session = await validateSession(state.sessionId);
      if (session) {
        enterWelcomeScreen(profile, session);
        return;
      }
      // Session invalid — clear it and fall through to join screen
      state.sessionId = null;
      sessionStorage.removeItem('orbit_session_id');
      localStorage.removeItem('orbit_last_session_id');
    }

    // No session yet — show join screen (QD style)
    showJoinScreen(profile);
  }

  async function validateSession(sessionId) {
    try {
      const res = await fetch(`/modules/orbit/api/sessions/${sessionId}`);
      if (!res.ok) return null;
      const s = await res.json();
      return s.active ? s : null;
    } catch (_) { return null; }
  }

  function showJoinScreen(profile) {
    // Pre-fill name from profile if available
    if (profile && profile.displayName) {
      document.getElementById('join-name').value = profile.displayName;
      document.getElementById('name-hint').textContent = '(from your profile)';
    }
    validateJoinForm();
    showScreen('screen-join');
  }

  function enterWelcomeScreen(profile, session) {
    // Cache session info
    state.sessionId = session.id;
    sessionStorage.setItem('orbit_session_id', session.id);
    sessionStorage.setItem('orbit_session_name', session.venue_name || '');
    localStorage.setItem('orbit_last_session_id', session.id);

    if (session.zone_names) updateZoneLabels(session.zone_names);

    if (!profile) {
      // No profile yet — redirect to profile setup, then come back
      window.location.href = `/modules/orbit/profile?return=checkin&s=${session.id}`;
      return;
    }

    document.getElementById('welcome-name').textContent = profile.displayName;

    // Pre-select default intents from profile
    const defaultIntents = profile.intents || (profile.defaultIntent ? [profile.defaultIntent] : ['social']);
    state.selectedIntents = [...defaultIntents];
    state.selectedIntents.forEach(i => {
      const chip = document.querySelector(`#intent-chips [data-intent="${i}"]`);
      if (chip) chip.classList.add('selected');
    });

    showScreen('screen-welcome');
  }

  // ── Join screen logic (QD-style code input) ────────────────────────────────

  const joinCodeInput = document.getElementById('join-code');
  const joinNameInput = document.getElementById('join-name');
  const btnJoin = document.getElementById('btn-join');
  const codeError = document.getElementById('code-error');

  function validateJoinForm() {
    const code = joinCodeInput.value.trim();
    const name = joinNameInput.value.trim();
    btnJoin.disabled = !(code.length === 4 && name.length > 0);
  }

  joinCodeInput.addEventListener('input', () => {
    joinCodeInput.value = joinCodeInput.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    codeError.style.display = 'none';
    validateJoinForm();
  });

  joinNameInput.addEventListener('input', () => {
    validateJoinForm();
  });

  joinCodeInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') joinNameInput.focus();
  });

  joinNameInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !btnJoin.disabled) joinSession();
  });

  // Pre-fill code from URL param ?code=XXXX (host can share direct link)
  const urlCode = params.get('code');
  if (urlCode) {
    joinCodeInput.value = urlCode.toUpperCase().slice(0, 4);
    validateJoinForm();
  }

  let joinInProgress = false;

  async function joinSession() {
    if (joinInProgress || btnJoin.disabled) return;
    joinInProgress = true;
    btnJoin.disabled = true;
    codeError.style.display = 'none';

    const code = joinCodeInput.value.trim().toUpperCase();
    const nameFromInput = joinNameInput.value.trim();

    try {
      const res = await fetch(`/modules/orbit/api/sessions/by-code/${code}`);
      if (!res.ok) {
        codeError.style.display = 'block';
        btnJoin.disabled = false;
        joinInProgress = false;
        return;
      }
      const session = await res.json();

      // Save name to profile if user typed a new one
      const profile = loadProfile();
      if (!profile || profile.displayName !== nameFromInput) {
        const updated = {
          ...(profile || {}),
          displayName: nameFromInput,
          createdAt: (profile && profile.createdAt) || new Date().toISOString(),
        };
        localStorage.setItem('orbit_profile', JSON.stringify(updated));
      }

      enterWelcomeScreen(loadProfile(), session);
    } catch (_) {
      codeError.style.display = 'block';
      btnJoin.disabled = false;
    }
    joinInProgress = false;
  }

  btnJoin.addEventListener('click', joinSession);

  document.getElementById('btn-setup-profile').addEventListener('click', () => {
    const code = joinCodeInput.value.trim();
    const qs = code.length === 4 ? `?return=checkin&code=${code}` : '';
    window.location.href = `/modules/orbit/profile${qs}`;
  });

  // ── Intent selection (multi-select) ───────────────────────────────────────

  document.querySelectorAll('#intent-chips [data-intent]').forEach(chip => {
    chip.addEventListener('click', () => {
      const key = chip.dataset.intent;
      if (state.selectedIntents.includes(key)) {
        state.selectedIntents = state.selectedIntents.filter(i => i !== key);
        chip.classList.remove('selected');
      } else {
        state.selectedIntents.push(key);
        chip.classList.add('selected');
      }
    });
  });

  // ── Zone selection ─────────────────────────────────────────────────────────

  document.querySelectorAll('.zone-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.zone-btn').forEach(b => b.classList.remove('selected'));
      if (state.selectedZone === btn.dataset.zone) {
        state.selectedZone = null;
      } else {
        state.selectedZone = btn.dataset.zone;
        btn.classList.add('selected');
      }
    });
  });

  // ── Enter Orbit button ─────────────────────────────────────────────────────

  document.getElementById('btn-checkin').addEventListener('click', () => {
    if (state.selectedIntents.includes('activities')) {
      showScreen('screen-activities');
    } else {
      doCheckin([]);
    }
  });

  // ── Activities screen ──────────────────────────────────────────────────────

  document.querySelectorAll('#activity-chips .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const act = chip.dataset.activity;
      chip.classList.toggle('selected');
      if (chip.classList.contains('selected')) {
        state.selectedActivities.push(act);
      } else {
        state.selectedActivities = state.selectedActivities.filter(a => a !== act);
      }
    });
  });

  document.getElementById('btn-activity-confirm').addEventListener('click', () => {
    doCheckin(state.selectedActivities);
  });

  document.getElementById('btn-back-intent').addEventListener('click', () => {
    showScreen('screen-welcome');
  });

  // ── Edit profile ───────────────────────────────────────────────────────────

  document.getElementById('btn-edit-profile').addEventListener('click', () => {
    window.location.href = `/modules/orbit/profile?return=checkin&s=${state.sessionId}`;
  });

  // ── Do the check-in ────────────────────────────────────────────────────────

  async function doCheckin(activities) {
    showScreen('screen-entering');

    const profile = loadProfile();
    if (!profile) {
      window.location.href = `/modules/orbit/profile?return=checkin&s=${state.sessionId}`;
      return;
    }

    const intents = state.selectedIntents.length
      ? state.selectedIntents
      : (profile.intents || ['social']);

    const body = {
      session_id: state.sessionId,
      display_name: profile.displayName,
      intent: intents[0] || 'social',
      intents,
      desires: profile.desires || [],
      visibility: profile.defaultVisibility || 'public',
      interests: profile.interests || [],
      interest_intensity: profile.interestIntensity || {},
      values: profile.values || [],
      personality: profile.personality || null,
      activities,
      group_size: 1,
      zone: state.selectedZone,
    };

    try {
      const res = await fetch('/modules/orbit/api/checkins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('Check-in failed');
      const data = await res.json();

      // Store checkin ID — both sessionStorage (tab) and the redirect URL
      sessionStorage.setItem('orbit_checkin_id', data.id);
      sessionStorage.setItem('orbit_session_id', state.sessionId);

      window.location.href = `/modules/orbit/discover?s=${state.sessionId}&c=${data.id}`;
    } catch (_) {
      showScreen('screen-welcome');
      alert('Could not check in. Please try again.');
    }
  }

  // ── Init ───────────────────────────────────────────────────────────────────

  boot();

})();

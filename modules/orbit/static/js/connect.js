(function () {
  'use strict';

  const pathParts = location.pathname.split('/');
  const scannedId = pathParts[pathParts.length - 1];
  const params = new URLSearchParams(location.search);
  const sessionId = params.get('s');

  const INTENT_LABELS = {
    social:     { label: 'Just socialising',   emoji: '🟢' },
    activities: { label: 'Up for activities',  emoji: '🟡' },
    friends:    { label: 'New friends',        emoji: '🔵' },
    romantic:   { label: 'Looking for a date', emoji: '🔴' },
  };

  const DESIRE_LABELS = {
    casual:    'Something casual',
    real:      'Something real',
    adventure: 'Open to adventure',
    wild:      'A bit wild 🔥',
    seewhere:  "Let's see where it goes",
  };

  let person = null;
  let activeChallengeId = null;
  let sdTimerInterval = null;
  let sdEndsAt = null;
  let quizAnswered = false;
  let sdActive = false;

  function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  }

  function escHtml(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function getMyCheckinId() {
    return sessionStorage.getItem('orbit_checkin_id');
  }

  function loadMyProfile() {
    try { return JSON.parse(localStorage.getItem('orbit_profile') || 'null'); } catch(_){ return null; }
  }

  // ── Load ────────────────────────────────────────────────────────────────────

  async function load() {
    if (!scannedId || !sessionId) { showScreen('screen-error'); return; }

    try {
      const myId = getMyCheckinId();
      const qs = myId ? `s=${sessionId}&scanner_id=${myId}` : `s=${sessionId}`;
      const res = await fetch(`/modules/orbit/api/connect/${scannedId}?${qs}`);
      if (!res.ok) { showScreen('screen-error'); return; }
      person = await res.json();
      renderProfile(person);

      // Check if I'm checked in
      const mySessId = sessionStorage.getItem('orbit_session_id');
      if (!myId || mySessId !== sessionId) {
        document.getElementById('send-section').style.display = 'none';
        document.getElementById('not-in-orbit').style.display = 'block';
        document.getElementById('btn-go-checkin').addEventListener('click', () => {
          window.location.href = `/modules/orbit/checkin?s=${sessionId}`;
        });
      }

      showScreen('screen-profile');

      // Check active challenge
      if (myId) {
        const cRes = await fetch(`/modules/orbit/api/challenges/${sessionId}/current`);
        if (cRes.ok) {
          const cData = await cRes.json();
          if (cData.challenge) {
            activeChallengeId = cData.challenge.id;
            await loadQuiz(myId, cData.challenge);
          }
        }

        // Check speed dating
        const sdRes = await fetch(`/modules/orbit/api/speed-dating/${sessionId}/active`);
        if (sdRes.ok) {
          const sdData = await sdRes.json();
          if (sdData.active) {
            sdActive = true;
            const endsAt = Date.now()/1000 + sdData.active.round_duration_seconds;
            startSDTimer(endsAt);
          }
        }
      }

    } catch (_) { showScreen('screen-error'); }
  }

  // ── Speed dating timer injection from connection response ────────────────────
  window._orbitSDInject = function(speedDating) {
    if (!speedDating) return;
    sdActive = true;
    startSDTimer(speedDating.ends_at);
  };

  // ── Render profile ──────────────────────────────────────────────────────────

  function renderProfile(p) {
    // Avatar
    document.getElementById('connect-avatar').textContent = (p.display_name || '?')[0].toUpperCase();
    document.getElementById('connect-name').textContent = p.display_name;

    // Intents row
    const intentsRow = document.getElementById('connect-intents-row');
    const intents = p.intents && p.intents.length ? p.intents : [p.intent];
    intents.forEach(key => {
      const info = INTENT_LABELS[key] || { label: key, emoji: '•' };
      const badge = document.createElement('span');
      badge.className = 'badge-intent';
      badge.textContent = `${info.emoji} ${info.label}`;
      intentsRow.appendChild(badge);
    });

    // Zone
    if (p.zone) {
      document.getElementById('connect-zone').textContent = `📍 ${p.zone.replace(/-/g,' ')}`;
    }

    // Compatibility
    if (p.compatibility) {
      renderCompatibility(p.compatibility);
    }

    // Interests
    const interests = p.interests || [];
    const intContainer = document.getElementById('connect-interests');
    interests.slice(0, 8).forEach(tag => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.style.pointerEvents = 'none';
      chip.textContent = tag;
      intContainer.appendChild(chip);
    });

    // Values
    const vals = p.values || [];
    if (vals.length) {
      document.getElementById('connect-values-wrap').style.display = 'block';
      const vc = document.getElementById('connect-values');
      vals.forEach(v => {
        const chip = document.createElement('span');
        chip.className = 'chip selected';
        chip.style.pointerEvents = 'none';
        chip.textContent = v.charAt(0).toUpperCase() + v.slice(1);
        vc.appendChild(chip);
      });
    }
  }

  function renderCompatibility(compat) {
    const section = document.getElementById('compat-section');
    section.style.display = 'block';
    document.getElementById('compat-pct').textContent = `${compat.score}%`;

    setTimeout(() => {
      document.getElementById('compat-bar').style.width = `${compat.score}%`;
    }, 120);

    const bulletsEl = document.getElementById('compat-bullets');
    (compat.bullets || []).forEach(b => {
      const li = document.createElement('li');
      li.textContent = b;
      bulletsEl.appendChild(li);
    });

    if (compat.personality_note) {
      const pn = document.getElementById('personality-note');
      pn.textContent = `✦ ${compat.personality_note}`;
      pn.style.display = 'block';
    }

    // Private match
    const sharedDesires = compat.shared_desires || [];
    if (sharedDesires.length) {
      const card = document.getElementById('private-match-card');
      card.style.display = 'block';
      const items = document.getElementById('private-match-items');
      sharedDesires.forEach(key => {
        const chip = document.createElement('span');
        chip.className = 'pm-chip';
        chip.textContent = DESIRE_LABELS[key] || key;
        items.appendChild(chip);
      });
    }
  }

  // ── Quiz ────────────────────────────────────────────────────────────────────

  async function loadQuiz(scannerId, challenge) {
    try {
      const res = await fetch(`/modules/orbit/api/connect/${scannedId}/quiz?s=${sessionId}&scanner_id=${scannerId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.question) return;
      renderQuiz(data, scannerId);
    } catch(_) {}
  }

  function renderQuiz(data, scannerId) {
    const card = document.getElementById('quiz-card');
    card.style.display = 'block';
    document.getElementById('quiz-question-text').textContent = data.question;
    const optsEl = document.getElementById('quiz-options');
    optsEl.innerHTML = '';
    (data.options || []).forEach((opt, i) => {
      const btn = document.createElement('button');
      btn.className = 'quiz-opt';
      btn.textContent = opt;
      btn.addEventListener('click', () => submitQuizAnswer(i, data, scannerId, optsEl, btn));
      optsEl.appendChild(btn);
    });
  }

  async function submitQuizAnswer(idx, data, scannerId, optsEl, btn) {
    if (quizAnswered) return;
    quizAnswered = true;
    optsEl.querySelectorAll('.quiz-opt').forEach(b => b.disabled = true);
    btn.classList.add('selected');
    try {
      const res = await fetch('/modules/orbit/api/challenge-answers', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          challenge_id: data.challenge_id,
          scanner_id: scannerId,
          scanned_id: scannedId,
          answer_index: idx,
        }),
      });
      if (!res.ok) throw new Error();
      const result = await res.json();
      const resultEl = document.getElementById('quiz-result');
      if (result.correct) {
        btn.classList.add('correct');
        resultEl.textContent = '✓ Correct! +1 point';
        resultEl.style.color = '#34d399';
      } else {
        btn.classList.add('wrong');
        resultEl.textContent = '✗ Better luck next scan!';
        resultEl.style.color = '#f87171';
      }
      resultEl.style.display = 'block';
    } catch(_) {}
  }

  // ── Speed dating timer ──────────────────────────────────────────────────────

  function startSDTimer(endsAt) {
    sdEndsAt = endsAt;
    document.getElementById('sd-timer-section').style.display = 'block';
    document.getElementById('send-section').style.display = 'none';
    updateSDDisplay();
    sdTimerInterval = setInterval(() => {
      const rem = Math.max(0, sdEndsAt - Date.now()/1000);
      updateSDDisplay(rem);
      if (rem <= 0) {
        clearInterval(sdTimerInterval);
        sdTimerExpired();
      }
    }, 500);
  }

  function updateSDDisplay(rem) {
    const total = sdEndsAt ? Math.max(0, sdEndsAt - Date.now()/1000) : 0;
    const secsLeft = rem !== undefined ? rem : total;
    const mins = Math.floor(secsLeft / 60);
    const secs = Math.floor(secsLeft % 60);
    document.getElementById('sd-timer-display').textContent = `${mins}:${String(secs).padStart(2,'0')}`;
  }

  function sdTimerExpired() {
    document.getElementById('sd-timer-section').style.display = 'none';
    document.getElementById('sd-reaction-section').style.display = 'block';
    // Vibrate
    if ('vibrate' in navigator) navigator.vibrate([200, 100, 200]);
  }

  document.getElementById('btn-sd-yes').addEventListener('click', () => sendSDReaction(true));
  document.getElementById('btn-sd-no').addEventListener('click', () => sendSDReaction(false));

  async function sendSDReaction(thumbsUp) {
    const myId = getMyCheckinId();
    if (!myId) return;
    try {
      await fetch('/modules/orbit/api/speed-dating/react', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          session_id: sessionId,
          from_id: myId,
          to_id: scannedId,
          thumbs_up: thumbsUp,
        }),
      });
    } catch(_) {}
    goDiscover();
  }

  // ── Send connection ─────────────────────────────────────────────────────────

  document.getElementById('btn-connect').addEventListener('click', sendConnection);
  document.getElementById('btn-no').addEventListener('click', () => history.back());

  async function sendConnection() {
    const myId = getMyCheckinId();
    if (!myId) return;
    try {
      document.getElementById('btn-connect').disabled = true;
      const res = await fetch('/modules/orbit/api/connections', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          session_id: sessionId,
          scanner_id: myId,
          scanned_id: scannedId,
        }),
      });
      if (!res.ok) throw new Error();
      const data = await res.json();

      // If speed dating just started via this connection
      if (data.speed_dating && !sdActive) {
        window._orbitSDInject(data.speed_dating);
        return; // stay on screen, show timer
      }

      if (data.is_mutual) {
        document.getElementById('mutual-msg').textContent =
          'You both scanned each other tonight. You\'re now connected.';
        showScreen('screen-mutual');
      } else {
        showScreen('screen-sent');
      }
    } catch(_) {
      document.getElementById('btn-connect').disabled = false;
      alert('Could not send connection. Please try again.');
    }
  }

  function goDiscover() {
    const sessId = sessionStorage.getItem('orbit_session_id');
    const cId = sessionStorage.getItem('orbit_checkin_id');
    if (sessId && cId) {
      window.location.href = `/modules/orbit/discover?s=${sessId}&c=${cId}`;
    } else {
      window.location.href = '/modules/orbit/';
    }
  }

  document.getElementById('btn-back-discover').addEventListener('click', goDiscover);
  document.getElementById('btn-mutual-discover').addEventListener('click', goDiscover);

  load();

})();

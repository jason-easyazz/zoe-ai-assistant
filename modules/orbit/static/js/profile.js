(function () {
  'use strict';

  const INTERESTS = [
    'Live music', 'Travel', 'Sport', 'Food', 'Dogs', 'Gaming',
    'Festivals', 'Cocktails', 'Hiking', 'Film', 'Books', 'Gym',
    'Comedy', 'Photography', 'Art', 'Cooking', 'Cycling', 'Yoga',
    'Poker', 'Trivia', 'Dancing', 'Coffee', 'Wine', 'Pool',
  ];

  const VALUES = [
    'Adventure', 'Loyalty', 'Ambition', 'Creativity', 'Family',
    'Freedom', 'Humour', 'Honesty', 'Security', 'Authenticity',
    'Community', 'Spontaneity',
  ];

  const QUIZ = [
    { text: 'I enjoy trying things I\'ve never done before', trait: 'O' },
    { text: 'I\'d rather have a deep conversation than small talk', trait: 'O' },
    { text: 'I\'m usually organised and on time', trait: 'C' },
    { text: 'I finish what I start', trait: 'C' },
    { text: 'I feel energised being around lots of people', trait: 'E' },
    { text: 'I\'m usually the one to start conversations with strangers', trait: 'E' },
    { text: 'I find it easy to forgive people', trait: 'A' },
    { text: 'I genuinely enjoy helping others', trait: 'A' },
    { text: 'I stay calm when things go wrong', trait: 'N', reverse: true },
    { text: 'I tend to overthink things', trait: 'N' },
  ];

  // ── State ──────────────────────────────────────────────────────────────────

  let selectedInterests = {}; // tag -> 1|2
  let selectedValues = [];
  let quizAnswers = {};       // index -> 1-5
  let currentQuizQ = 0;
  let selectedIntents = [];   // Tier 1 public (multi-select)
  let selectedDesires = [];   // Tier 2 private (multi-select)
  let visibilityLowKey = false;

  // Load existing profile if editing
  const existing = loadProfile();
  if (existing) {
    selectedInterests = existing.interestIntensity || {};
    selectedValues = existing.values || [];
    quizAnswers = existing.quizAnswers || {};
    selectedIntents = existing.intents || (existing.defaultIntent ? [existing.defaultIntent] : []);
    selectedDesires = existing.desires || [];
    visibilityLowKey = existing.defaultVisibility === 'low-key';
  }

  // ── Screens ────────────────────────────────────────────────────────────────

  function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const s = document.getElementById(id);
    if (s) s.classList.add('active');
  }

  // ── Screen 1: Name ─────────────────────────────────────────────────────────

  const nameInput = document.getElementById('input-name');
  if (existing && existing.displayName) nameInput.value = existing.displayName;

  nameInput.addEventListener('keydown', e => { if (e.key === 'Enter') nextFromName(); });
  document.getElementById('btn-name-next').addEventListener('click', nextFromName);

  function nextFromName() {
    const name = nameInput.value.trim();
    if (!name) { nameInput.focus(); return; }
    buildInterestChips();
    showScreen('screen-interests');
  }

  // ── Screen 2: Interests ────────────────────────────────────────────────────

  function buildInterestChips() {
    const container = document.getElementById('interest-chips');
    container.innerHTML = '';
    INTERESTS.forEach(tag => {
      const el = document.createElement('div');
      el.className = 'chip';
      const key = tag.toLowerCase();
      const intensity = selectedInterests[key] || 0;
      if (intensity === 1) el.classList.add('selected');
      if (intensity === 2) { el.classList.add('selected', 'intense'); }
      el.textContent = tag;
      el.addEventListener('click', () => toggleInterest(key, el, tag));
      container.appendChild(el);
    });
  }

  function toggleInterest(key, el, label) {
    const current = selectedInterests[key] || 0;
    if (current === 0) {
      selectedInterests[key] = 1;
      el.classList.add('selected');
      el.classList.remove('intense');
      el.textContent = label;
    } else if (current === 1) {
      selectedInterests[key] = 2;
      el.classList.add('intense');
      el.textContent = label + ' ★';
    } else {
      delete selectedInterests[key];
      el.classList.remove('selected', 'intense');
      el.textContent = label;
    }
  }

  document.getElementById('btn-interests-next').addEventListener('click', () => {
    buildValueChips();
    showScreen('screen-values');
  });
  document.getElementById('btn-back-name').addEventListener('click', () => showScreen('screen-name'));

  // ── Screen 3: Values ───────────────────────────────────────────────────────

  function buildValueChips() {
    const container = document.getElementById('value-chips');
    container.innerHTML = '';
    VALUES.forEach(v => {
      const key = v.toLowerCase();
      const el = document.createElement('div');
      el.className = 'chip' + (selectedValues.includes(key) ? ' selected' : '');
      el.textContent = v;
      el.addEventListener('click', () => toggleValue(key, el));
      container.appendChild(el);
    });
    updateValuesHint();
  }

  function toggleValue(key, el) {
    if (selectedValues.includes(key)) {
      selectedValues = selectedValues.filter(v => v !== key);
      el.classList.remove('selected');
    } else if (selectedValues.length < 3) {
      selectedValues.push(key);
      el.classList.add('selected');
    }
    updateValuesHint();
  }

  function updateValuesHint() {
    const hint = document.getElementById('values-hint');
    const btn = document.getElementById('btn-values-next');
    const n = selectedValues.length;
    hint.textContent = n === 3 ? '✓ Perfect' : `Select ${3 - n} more`;
    btn.disabled = n < 3;
  }

  document.getElementById('btn-values-next').addEventListener('click', () => {
    initQuiz();
    showScreen('screen-quiz');
  });
  document.getElementById('btn-back-interests').addEventListener('click', () => {
    buildInterestChips();
    showScreen('screen-interests');
  });

  // ── Screen 4: Quiz ─────────────────────────────────────────────────────────

  function initQuiz() {
    currentQuizQ = 0;
    renderQuizQuestion();
  }

  function renderQuizQuestion() {
    const q = QUIZ[currentQuizQ];
    document.getElementById('q-text').textContent = `"${q.text}"`;
    document.getElementById('quiz-q-count').textContent = `Question ${currentQuizQ + 1} of ${QUIZ.length}`;
    document.getElementById('quiz-progress').style.width = `${(currentQuizQ / QUIZ.length) * 100}%`;

    const row = document.getElementById('scale-row');
    row.innerHTML = '';
    for (let v = 1; v <= 5; v++) {
      const btn = document.createElement('button');
      btn.className = 'scale-btn' + (quizAnswers[currentQuizQ] === v ? ' chosen' : '');
      btn.textContent = v;
      btn.addEventListener('click', () => {
        quizAnswers[currentQuizQ] = v;
        row.querySelectorAll('.scale-btn').forEach(b => b.classList.remove('chosen'));
        btn.classList.add('chosen');
        document.getElementById('btn-quiz-next').disabled = false;
      });
      row.appendChild(btn);
    }
    document.getElementById('btn-quiz-next').disabled = !(currentQuizQ in quizAnswers);
  }

  document.getElementById('btn-quiz-next').addEventListener('click', () => {
    if (currentQuizQ < QUIZ.length - 1) {
      currentQuizQ++;
      renderQuizQuestion();
    } else {
      showScreen('screen-defaults');
    }
  });

  document.getElementById('btn-quiz-skip').addEventListener('click', () => {
    quizAnswers = {};
    showScreen('screen-defaults');
  });

  document.getElementById('btn-back-quiz').addEventListener('click', () => showScreen('screen-quiz'));

  // ── Screen 5: Defaults ─────────────────────────────────────────────────────

  // Tier-1 public intents (multi-select chips)
  document.querySelectorAll('#intents-public [data-intent]').forEach(chip => {
    const key = chip.dataset.intent;
    if (selectedIntents.includes(key)) chip.classList.add('selected');
    chip.addEventListener('click', () => {
      if (selectedIntents.includes(key)) {
        selectedIntents = selectedIntents.filter(i => i !== key);
        chip.classList.remove('selected');
      } else {
        selectedIntents.push(key);
        chip.classList.add('selected');
      }
    });
  });

  // Tier-2 private desires (multi-select chips)
  document.querySelectorAll('#intents-private [data-desire]').forEach(chip => {
    const key = chip.dataset.desire;
    if (selectedDesires.includes(key)) chip.classList.add('selected');
    chip.addEventListener('click', () => {
      if (selectedDesires.includes(key)) {
        selectedDesires = selectedDesires.filter(d => d !== key);
        chip.classList.remove('selected');
      } else {
        selectedDesires.push(key);
        chip.classList.add('selected');
      }
    });
  });

  // Visibility toggle
  const visToggle = document.getElementById('visibility-toggle');
  if (visibilityLowKey) visToggle.classList.add('on');
  visToggle.addEventListener('click', () => {
    visibilityLowKey = !visibilityLowKey;
    visToggle.classList.toggle('on', visibilityLowKey);
  });

  document.getElementById('btn-save').addEventListener('click', saveProfile);

  function computePersonality() {
    if (Object.keys(quizAnswers).length < QUIZ.length) return null;
    const sums = { O: 0, C: 0, E: 0, A: 0, N: 0 };
    const counts = { O: 0, C: 0, E: 0, A: 0, N: 0 };
    QUIZ.forEach((q, i) => {
      let val = quizAnswers[i];
      if (q.reverse) val = 6 - val;
      sums[q.trait] += val;
      counts[q.trait]++;
    });
    const result = {};
    Object.keys(sums).forEach(t => {
      result[t] = counts[t] ? sums[t] / counts[t] : 3;
    });
    return result;
  }

  function saveProfile() {
    const name = document.getElementById('input-name').value.trim();
    if (!name) { showScreen('screen-name'); return; }

    const primaryIntent = selectedIntents[0] || 'social';
    const profile = {
      displayName: name,
      interests: Object.keys(selectedInterests),
      interestIntensity: selectedInterests,
      values: selectedValues,
      personality: computePersonality(),
      defaultIntent: primaryIntent,
      intents: selectedIntents.length ? selectedIntents : [primaryIntent],
      desires: selectedDesires,
      defaultVisibility: visibilityLowKey ? 'low-key' : 'public',
      createdAt: existing ? existing.createdAt : new Date().toISOString(),
    };

    localStorage.setItem('orbit_profile', JSON.stringify(profile));
    showScreen('screen-done');

    // Redirect back to check-in, preserving session ID or join code
    const qp = new URLSearchParams(location.search);
    const sessionId = qp.get('s');
    const joinCode  = qp.get('code');

    let checkinUrl = '/modules/orbit/checkin';
    if (sessionId) checkinUrl += `?s=${sessionId}`;
    else if (joinCode) checkinUrl += `?code=${joinCode}`;

    document.getElementById('redirect-msg').textContent = 'Profile saved! Ready to join.';
    document.getElementById('btn-done').addEventListener('click', () => {
      window.location.href = checkinUrl;
    });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function loadProfile() {
    try {
      const raw = localStorage.getItem('orbit_profile');
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

})();

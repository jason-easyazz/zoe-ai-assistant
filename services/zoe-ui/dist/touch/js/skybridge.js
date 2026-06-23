/*
 * Skybridge app orchestration.
 */
(function () {
    const els = {};
    let voice = null;
    let mode = new URLSearchParams(location.search).get('mode') || localStorage.getItem('skybridge_voice_mode') || 'local';
    let orbState = 'ambient';
    let phase = 0;
    let animationFrame = null;
    let cardSequence = 0;
    let currentUtterance = '';
    let voiceStartedByUser = false;
    let commandFallbackOpen = false;
    let skybridgeContext = {};
    let authProfiles = [];
    let authHydrationSequence = 0;
    let idleTimer = null;
    let clockTicker = null;

    const queryParams = new URLSearchParams(location.search);
    const configuredIdleMs = Number(queryParams.get('idle_ms') || localStorage.getItem('skybridge_idle_return_ms') || 75000);
    const CARD_IDLE_MS = Number.isFinite(configuredIdleMs) ? Math.max(15000, configuredIdleMs) : 75000;

    const colors = {
        ambient: ['#5fc6ff', '#66d19e'],
        listening: ['#5fc6ff', '#d8f3ff'],
        thinking: ['#f7bf5f', '#66d19e'],
        responding: ['#8c7dff', '#ff7b7b']
    };

    function init() {
        cacheEls();
        bindEvents();
        startOrb();
        startClockTicker();
        renderHome();
        loadBackendStatus();
        setMode(mode);
        syncVoiceFallbackState();
        if (typeof TouchMenu !== 'undefined') TouchMenu.init({ page: 'skybridge' });
        const initialQuery = new URLSearchParams(location.search).get('q');
        if (initialQuery) {
            setTimeout(() => submitCommand(initialQuery), 120);
        }
    }

    async function loadBackendStatus() {
        try {
            const resp = await fetch('/api/skybridge/status', { headers: { 'Accept': 'application/json' } });
            if (!resp.ok) return;
            const data = await resp.json();
            if (data && data.status === 'ready') setStatus('Skybridge runtime ready');
        } catch (_) {
            // The interface can still render local cards if the API is offline.
        }
    }

    function cacheEls() {
        els.cards = document.getElementById('skyCards');
        els.statusText = document.getElementById('skyStatusText');
        els.statusDot = document.getElementById('skyStatusDot');
        els.contextTitle = document.getElementById('skyContextTitle');
        els.contextDetail = document.getElementById('skyContextDetail');
        els.transcript = document.getElementById('skyTranscript');
        els.copy = document.getElementById('skyListeningCopy');
        els.form = document.getElementById('skyCommandForm');
        els.commandCopy = document.getElementById('skyCommandCopy');
        els.input = document.getElementById('skyCommandInput');
        els.mic = document.getElementById('skyMicBtn');
        els.home = document.getElementById('skyHomeBtn');
        els.orbButton = document.getElementById('skyOrbButton');
        els.orbButtonText = document.getElementById('skyOrbButtonText');
        els.voiceHint = document.getElementById('skyVoiceHint');
        els.voiceTitle = document.getElementById('skyVoiceTitle');
        els.voiceDetail = document.getElementById('skyVoiceDetail');
        els.voiceAction = document.getElementById('skyVoiceAction');
        els.ambientClock = document.getElementById('skyAmbientClock');
        els.ambientClockHour = document.getElementById('skyAmbientClockHour');
        els.ambientClockMinute = document.getElementById('skyAmbientClockMinute');
        els.ambientClockMeridiem = document.getElementById('skyAmbientClockMeridiem');
        els.ambientClockDate = document.getElementById('skyAmbientClockDate');
        els.canvas = document.getElementById('skyOrb');
        els.ctx = els.canvas.getContext('2d');
    }

    function bindEvents() {
        document.querySelectorAll('[data-mode]').forEach(btn => {
            btn.addEventListener('click', () => setMode(btn.dataset.mode));
        });
        els.form.addEventListener('submit', event => {
            event.preventDefault();
            submitCommand(els.input.value);
            els.input.value = '';
        });
        els.input.addEventListener('focus', () => {
            if (!commandFallbackOpen) {
                openCommandFallback('Type anything Zoe should show.');
            }
        });
        els.input.addEventListener('input', () => {
            commandFallbackOpen = true;
            document.body.classList.add('sky-command-open');
        });
        els.mic.addEventListener('click', toggleVoiceCapture);
        els.orbButton.addEventListener('click', toggleVoiceCapture);
        els.voiceAction.addEventListener('click', toggleVoiceCapture);
        els.home.addEventListener('click', () => renderHome({ showCards: true }));
        ['pointerdown', 'keydown', 'touchstart'].forEach(type => {
            document.addEventListener(type, noteUserActivity, { passive: true });
        });
        els.cards.addEventListener('click', event => {
            const btn = event.target.closest('button[data-sky-action]');
            if (!btn) return;
            let route = btn.dataset.route;
            const query = btn.dataset.query;
            if (btn.dataset.skyAction === 'auth') {
                route = prepareAuthRoute(btn, route);
            }
            if (route && route.startsWith('/')) {
                location.href = route;
            } else if (route) {
                showError('Unsupported card route');
            } else if (query) {
                submitCommand(query);
            }
        });
    }

    function currentPanelId(storedChallenge) {
        return new URLSearchParams(location.search).get('panel_id')
            || localStorage.getItem('zoe_touch_panel_id')
            || localStorage.getItem('zoe_panel_id')
            || (storedChallenge && storedChallenge.panel_id)
            || '';
    }

    function buildLoginRoute(panelId, selectedUserId) {
        const params = new URLSearchParams();
        if (panelId) params.set('panel_id', panelId);
        if (selectedUserId) params.set('user_id', selectedUserId);
        if (selectedUserId) params.set('auth', 'skybridge');
        const query = params.toString();
        return '/touch/index.html' + (query ? '?' + query : '');
    }

    function prepareAuthRoute(btn, route) {
        let storedChallenge = {};
        try { storedChallenge = JSON.parse(sessionStorage.getItem('zoe_panel_auth_challenge') || '{}') || {}; } catch (_) {}
        const panelId = currentPanelId(storedChallenge);
        const challengeId = btn.dataset.challengeId || storedChallenge.challenge_id || '';
        const actionContext = btn.dataset.actionContext || storedChallenge.action_context || 'Enter PIN';
        const selectedUserId = btn.dataset.userId || storedChallenge.selected_user_id || '';
        const selectedUsername = btn.dataset.userName || storedChallenge.selected_username || '';
        const selectedAvatar = btn.dataset.userAvatar || storedChallenge.selected_avatar || '';
        const authState = Object.assign({}, storedChallenge, {
            challenge_id: challengeId,
            panel_id: panelId,
            action_context: actionContext,
            selected_user_id: selectedUserId,
            selected_username: selectedUsername,
            selected_avatar: selectedAvatar
        });
        try {
            sessionStorage.setItem('zoe_panel_auth_challenge', JSON.stringify(authState));
            sessionStorage.setItem('zoe_redirect_after_login', location.pathname + location.search);
        } catch (_) {
            // Route still preserves panel id even when storage is unavailable.
        }
        return route || buildLoginRoute(panelId, selectedUserId);
    }

    function setMode(nextMode) {
        mode = nextMode || 'local';
        localStorage.setItem('skybridge_voice_mode', mode);
        document.querySelectorAll('[data-mode]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        if (voice) voice.stop();
        voice = new window.SkybridgeVoice({ mode, onEvent: handleVoiceEvent });
        voice.start().catch(err => {
            showError(err.message || 'Voice failed to start');
            openCommandFallback('Voice is still connecting. Type here and Zoe will still render cards.');
        });
        setStatus('Connecting ' + mode);
    }

    function handleVoiceEvent(event) {
        if (event.type === 'ready') {
            setStatus('Ready on ' + event.mode);
        } else if (event.type === 'state') {
            setState(event.state || 'ambient');
        } else if (event.type === 'transcript') {
            if (event.role === 'user' && trySelectAuthProfile(event.text)) return;
            addTranscript(event.role, event.text);
        } else if (event.type === 'card') {
            addCard(event.card, true);
            scheduleIdleReturn();
        } else if (event.type === 'cards') {
            renderSkybridgeResult(event.result || event);
        } else if (event.type === 'skybridge_context') {
            skybridgeContext = event.context || skybridgeContext || {};
        } else if (event.type === 'error') {
            showError(event.message);
            if (/voice disconnected|transport unavailable|livekit unavailable|websocket|microphone|permission/i.test(event.message || '')) {
                openCommandFallback('Type here while voice reconnects...');
            }
        } else if (event.type === 'done') {
            setState('ambient');
        }
    }

    async function submitCommand(text) {
        const query = String(text || '').trim();
        if (!query) return;
        if (trySelectAuthProfile(query)) {
            els.input.value = '';
            return;
        }
        currentUtterance = 'Heard: ' + query;
        setVoiceLayerText(currentUtterance);
        addTranscript('user', query);
        setState('thinking');
        const resolved = await resolveCommand(query);
        if (!resolved) {
            projectCards(query);
            if (voice) voice.sendText(query);
        }
        setState('ambient');
    }

    async function resolveCommand(query) {
        try {
            let resp = await fetch('/api/skybridge/resolve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ message: query, context: skybridgeContext })
            });
            if (resp.status === 401 || resp.status === 503) {
                try { localStorage.removeItem('zoe_session'); } catch (_) {}
                resp = await fetch('/api/skybridge/resolve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ message: query, context: skybridgeContext })
                });
            }
            if (!resp.ok) throw new Error('Skybridge resolver unavailable');
            const data = await resp.json();
            if (!data || !data.handled) return false;
            renderSkybridgeResult(data);
            retireRenderedVoiceCards(data);
            return true;
        } catch (err) {
            if (isDataQuery(query)) {
                clearCards();
                addCard({
                    component: 'status',
                    props: {
                        title: 'Live data unavailable',
                        body: err.message || 'Skybridge could not reach the data resolver.',
                        status: 'Data'
                    }
                }, true);
                return true;
            }
            return false;
        }
    }

    function contextLabelFor(intent) {
        const domain = intent && intent.domain;
        if (domain === 'calendar') return 'Calendar';
        if (domain === 'weather') return 'Weather';
        if (domain === 'lists') return 'Lists';
        if (domain === 'people') return 'People';
        return 'Skybridge';
    }

    function renderSkybridgeResult(data) {
        if (!data) return;
        clearCards();
        const intent = data.intent || {};
        const cards = Array.isArray(data.cards) ? data.cards : [];
        skybridgeContext = data.skybridge_context || skybridgeContext || {};
        setContext(contextLabelFor(intent), data.spoken_summary || 'Showing live data.');
        currentUtterance = data.spoken_summary || currentUtterance;
        setVoiceLayerText(currentUtterance);
        cards.forEach((card, index) => addCard(card, false, index * 90));
        if (!cards.length) {
            addCard({
                component: 'status',
                props: {
                    title: 'No card returned',
                    body: data.spoken_summary || 'Zoe understood the request but did not return a display card.',
                    status: 'Resolver'
                }
            }, true);
        }
        scheduleIdleReturn();
    }

    async function retireRenderedVoiceCards(data) {
        if (!data || !data.handled) return;
        const panelId = new URLSearchParams(location.search).get('panel_id');
        if (!panelId) return;
        try {
            const pending = await fetch(`/api/ui/actions/pending?panel_id=${encodeURIComponent(panelId)}&limit=20`, {
                headers: { 'Accept': 'application/json' }
            });
            if (!pending.ok) return;
            const payload = await pending.json();
            const actions = Array.isArray(payload.actions) ? payload.actions : [];
            await Promise.allSettled(actions
                .filter(action => action && action.action_type === 'show_card')
                .filter(action => action.payload && action.payload.source === 'voice:skybridge')
                .map(action => fetch(`/api/ui/actions/${encodeURIComponent(action.id)}/ack`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        status: 'success',
                        panel_id: panelId,
                        ui_context: { page: location.pathname, source: 'skybridge-direct-render' }
                    }),
                    keepalive: true
                })));
        } catch (_) {
            // Non-fatal: the queued card will be superseded by the next voice turn.
        }
    }

    function isDataQuery(query) {
        return /\b(calendar|schedule|events|appointments|agenda|weather|forecast|temperature|rain|windy|wind|list|shopping|groceries|grocery|tasks|todos|people|contacts|person|profile|remember|change|move|reschedule|add|remove|take|delete|drop|edit)\b/i.test(query || '');
    }

    function projectCards(query) {
        const matches = window.SkybridgeCapabilities.find(query);
        if (!matches.length) {
            clearCards();
            addCard({
                component: 'status',
                props: {
                    title: 'I can work from here',
                    kicker: 'No exact page match',
                    body: 'I will keep the conversation here. If this needs a page or setting, Skybridge can add a new capability mapping for it.',
                    status: 'General',
                    tone: 'hero'
                }
            }, true);
            return;
        }
        clearCards();
        scheduleIdleReturn();
        const top = matches[0];
        if (top.kind === 'setting') {
            setContext('Settings', top.title + ' is active for follow-up commands.');
            addCard({ component: 'setting', props: top }, true);
        } else {
            setContext(top.title, top.summary);
            addCard({ component: 'page', props: top }, true);
        }
        if (matches.length > 1) {
            addCard({
                component: 'list',
                props: {
                    title: 'Related matches',
                    kicker: 'Skybridge context',
                    items: matches.slice(1, 5).map(item => item.title + ' - ' + item.summary),
                    actions: matches.slice(1, 3).map(item => ({ label: item.title, query: item.title }))
                }
            }, false, 120);
        }
    }

    function renderHome(options) {
        const showCards = options && options.showCards;
        clearIdleTimer();
        document.body.classList.add('sky-empty');
        document.body.classList.add('sky-ambient-clock');
        commandFallbackOpen = false;
        document.body.classList.remove('sky-command-open');
        document.body.classList.remove('sky-typing-fallback');
        syncVoiceFallbackState();
        currentUtterance = '';
        skybridgeContext = {};
        setContext('Listening', 'The surface will build itself when Zoe understands what you need.');
        clearCards();
        setVoiceLayerText('Listening. Ask Zoe for anything.');
        updateAllClocks();
        if (showCards && window.SkybridgeCapabilities && typeof window.SkybridgeCapabilities.getHomeCards === 'function') {
            setContext('Skybridge home', 'Start with voice, then keep the useful cards in reach.');
            window.SkybridgeCapabilities.getHomeCards().forEach((card, index) => {
                addCard(card, false, index * 90);
            });
        }
        updateVoiceHint('Touch the orb to speak', getMicGuidance(), 'Start mic');
        requestAnimationFrame(resizeOrb);
    }

    function clearCards() {
        cardSequence = 0;
        els.cards.innerHTML = '';
        document.body.classList.add('sky-empty');
        document.body.classList.add('sky-ambient-clock');
        requestAnimationFrame(resizeOrb);
    }

    function addCard(card, prepend, delayMs) {
        document.body.classList.remove('sky-empty');
        document.body.classList.remove('sky-ambient-clock');
        requestAnimationFrame(resizeOrb);
        const wrapper = document.createElement('div');
        wrapper.innerHTML = window.SkybridgeRenderer.render(card);
        const node = wrapper.firstElementChild;
        if (!node) return;
        node.style.animationDelay = ((delayMs == null ? cardSequence * 90 : delayMs) / 1000) + 's';
        cardSequence += 1;
        if (prepend && els.cards.firstChild) {
            els.cards.insertBefore(node, els.cards.firstChild);
        } else {
            els.cards.appendChild(node);
        }
        hydrateAuthCard(node, card);
        updateAllClocks();
        scheduleIdleReturn();
        while (els.cards.children.length > 8) {
            els.cards.removeChild(els.cards.lastElementChild);
        }
    }

    function clearIdleTimer() {
        if (idleTimer) {
            clearTimeout(idleTimer);
            idleTimer = null;
        }
    }

    function noteUserActivity() {
        if (!document.body.classList.contains('sky-empty')) {
            scheduleIdleReturn();
        }
    }

    function scheduleIdleReturn() {
        clearIdleTimer();
        if (document.body.classList.contains('sky-empty')) return;
        idleTimer = setTimeout(returnToAmbientClock, CARD_IDLE_MS);
    }

    function returnToAmbientClock() {
        idleTimer = null;
        const voiceBusy = voice && (voice.isRecording || voice.speaking || voice.serverBusy);
        if (voiceBusy || orbState === 'listening' || orbState === 'thinking' || orbState === 'responding') {
            scheduleIdleReturn();
            return;
        }
        renderHome({ idle: true });
        setStatus('Ambient');
    }

    function startClockTicker() {
        updateAllClocks();
        clockTicker = setInterval(updateAllClocks, 1000);
    }

    function clockParts(timezone) {
        const options = timezone ? { timeZone: timezone } : {};
        const now = new Date();
        const timeParts = new Intl.DateTimeFormat(undefined, Object.assign({
            hour: 'numeric',
            minute: '2-digit',
            hour12: true
        }, options)).formatToParts(now);
        const dateLabel = new Intl.DateTimeFormat(undefined, Object.assign({
            weekday: 'long',
            day: 'numeric',
            month: 'long'
        }, options)).format(now);
        return {
            hour: (timeParts.find(part => part.type === 'hour') || {}).value || '',
            minute: (timeParts.find(part => part.type === 'minute') || {}).value || '',
            meridiem: (timeParts.find(part => part.type === 'dayPeriod') || {}).value || '',
            date: dateLabel
        };
    }

    function updateClockElement(root, timezone) {
        if (!root) return;
        const parts = clockParts(timezone || root.dataset.timezone || '');
        const hour = root.querySelector('[data-clock-hour]');
        const minute = root.querySelector('[data-clock-minute]');
        const meridiem = root.querySelector('[data-clock-meridiem]');
        const dateLabel = root.querySelector('[data-clock-date]');
        if (hour) hour.textContent = parts.hour;
        if (minute) minute.textContent = parts.minute;
        if (meridiem) meridiem.textContent = parts.meridiem;
        if (dateLabel) dateLabel.textContent = parts.date;
    }

    function updateAllClocks() {
        if (els.ambientClock) updateClockElement(els.ambientClock);
        document.querySelectorAll('.sky-live-clock').forEach(node => updateClockElement(node));
    }

    async function hydrateAuthCard(node, card) {
        if (!node || !node.classList || !node.classList.contains('auth-challenge')) return;
        const target = node.querySelector('[data-auth-profiles]');
        if (!target) return;
        const hydrationId = String(++authHydrationSequence);
        node.dataset.authHydrationId = hydrationId;
        const props = (card && card.props) || {};
        const panelId = currentPanelId({});
        let profiles = Array.isArray(props.profiles) ? props.profiles : [];
        let defaultUserId = props.default_user_id || '';
        const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timeoutId = controller ? setTimeout(() => controller.abort(), 8000) : null;
        try {
            const url = panelId ? '/api/auth/profiles?panel_id=' + encodeURIComponent(panelId) : '/api/auth/profiles';
            const resp = await fetch(url, {
                headers: { 'Accept': 'application/json' },
                signal: controller ? controller.signal : undefined
            });
            if (resp.ok) {
                const data = await resp.json();
                if (Array.isArray(data)) {
                    profiles = data;
                } else if (data && Array.isArray(data.profiles)) {
                    profiles = data.profiles;
                    defaultUserId = data.default_user_id || defaultUserId;
                }
            }
        } catch (_) {
            // Keep any profiles already supplied with the card.
        } finally {
            if (timeoutId) clearTimeout(timeoutId);
        }
        if (!node.isConnected || node.dataset.authHydrationId !== hydrationId) return;
        const nextProfiles = profiles.filter(profile => profile && profile.user_id && profile.user_id !== 'guest');
        authProfiles = nextProfiles;
        renderAuthProfiles(target, nextProfiles, defaultUserId, props);
    }

    function authInitials(profile) {
        const raw = String(profile.avatar || profile.username || profile.name || profile.user_id || '?').trim();
        return raw.charAt(0).toUpperCase() || '?';
    }

    function renderAuthProfiles(target, profiles, defaultUserId, props) {
        if (!profiles.length) {
            target.innerHTML = '<div class="sky-auth-empty">No profiles are linked to this panel yet.</div>';
            return;
        }
        const baseAction = props.actions && props.actions[0] ? props.actions[0] : {};
        const panelId = currentPanelId({});
        target.innerHTML = profiles.map(profile => {
            const name = window.SkybridgeRenderer.escapeHtml(profile.username || profile.name || profile.user_id);
            const avatar = window.SkybridgeRenderer.escapeHtml(authInitials(profile));
            const userId = window.SkybridgeRenderer.escapeHtml(profile.user_id);
            const route = window.SkybridgeRenderer.escapeHtml(buildLoginRoute(panelId, profile.user_id));
            const selected = profile.user_id === defaultUserId ? ' is-default' : '';
            const challengeId = window.SkybridgeRenderer.escapeHtml(baseAction.challenge_id || '');
            const actionContext = window.SkybridgeRenderer.escapeHtml(baseAction.action_context || 'Enter PIN');
            return '<button type="button" class="sky-auth-profile' + selected + '" aria-label="Sign in as ' + name + '" data-sky-action="auth" data-route="' + route + '" data-challenge-id="' + challengeId + '" data-action-context="' + actionContext + '" data-user-id="' + userId + '" data-user-name="' + name + '" data-user-avatar="' + avatar + '">' +
                '<span class="sky-auth-person"><strong>' + name + '</strong></span>' +
                '</button>';
        }).join('');
    }

    function normalizeProfileName(value) {
        return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ' ).trim();
    }

    function authNameMatches(wanted, profile) {
        const name = normalizeProfileName(profile.username || profile.name || profile.user_id);
        const userId = normalizeProfileName(profile.user_id);
        if (!name) return false;
        if (wanted === name || wanted === userId) return true;
        const spoken = wanted.replace(/^(i am|i'm|this is|it is|it's|its|select|choose|use|as)\s+/, '').replace(/\s+(please|thanks)$/,'').trim();
        if (spoken === name || spoken === userId) return true;
        const tokens = wanted.split(/\s+/).filter(Boolean);
        if (!name.includes(' ') && name.length >= 2 && tokens.includes(name)) return true;
        return name.includes(' ') && (' ' + wanted + ' ').includes(' ' + name + ' ');
    }

    function trySelectAuthProfile(text) {
        if (!document.querySelector('.sky-card.auth-challenge') || !authProfiles.length) return false;
        const wanted = normalizeProfileName(text);
        if (!wanted) return false;
        const match = authProfiles.find(profile => authNameMatches(wanted, profile));
        if (!match) return false;
        const button = els.cards.querySelector('button.sky-auth-profile[data-user-id="' + CSS.escape(match.user_id) + '"]');
        if (!button) return false;
        button.click();
        return true;
    }

    function setContext(title, detail) {
        els.contextTitle.textContent = title;
        els.contextDetail.textContent = detail;
    }

    function addTranscript(role, text) {
        if (!text) return;
        currentUtterance = (role === 'user' ? 'Heard: ' : 'Zoe: ') + text;
        setVoiceLayerText(currentUtterance);
        const line = document.createElement('div');
        line.className = 'sky-line';
        line.innerHTML = '<strong>' + (role === 'user' ? 'You' : 'Zoe') + ':</strong> ' + window.SkybridgeRenderer.escapeHtml(text);
        els.transcript.appendChild(line);
        els.transcript.scrollTop = els.transcript.scrollHeight;
    }

    function setStatus(text) {
        els.statusText.textContent = text;
    }

    function showError(message) {
        const text = message || 'Something needs attention.';
        setStatus(text);
        if (/microphone|secure|permission|voice upload|transport unavailable/i.test(text)) {
            updateVoiceHint('Voice reconnecting', text, 'Try again');
        }
        const transportNotice = /voice disconnected|transport unavailable|livekit unavailable/i.test(text);
        if (transportNotice && (document.body.classList.contains('sky-empty') || currentUtterance)) return;
        if (!document.body.classList.contains('sky-empty')) {
            const previous = currentUtterance;
            setVoiceLayerText('Notice: ' + text);
            if (previous) {
                setTimeout(() => {
                    if (!document.body.classList.contains('sky-empty') && currentUtterance === previous) {
                        setVoiceLayerText(previous);
                    }
                }, 4500);
            }
        } else {
            setVoiceLayerText(text);
        }
    }

    function setState(state) {
        orbState = state;
        els.mic.classList.toggle('recording', state === 'listening');
        document.body.classList.toggle('sky-state-listening', state === 'listening');
        document.body.classList.toggle('sky-state-thinking', state === 'thinking');
        document.body.classList.toggle('sky-state-responding', state === 'responding');
        const copy = {
            ambient: 'Say what you need. Skybridge will shape the screen around it.',
            listening: 'Listening...',
            thinking: 'Finding the right page, data, or card...',
            responding: 'Zoe is speaking. You can interrupt when needed.'
        };
        if (document.body.classList.contains('sky-empty') || !currentUtterance) {
            setVoiceLayerText(copy[state] || copy.ambient);
        }
        setStatus(state.charAt(0).toUpperCase() + state.slice(1));
        updateVoiceControl(state);
    }

    function canUseMicrophone() {
        return !!(window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1') &&
            !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    function syncVoiceFallbackState() {
        document.body.classList.toggle('sky-voice-fallback', !canUseMicrophone());
    }

    function openCommandFallback(message) {
        commandFallbackOpen = true;
        document.body.classList.add('sky-command-open');
        document.body.classList.add('sky-typing-fallback');
        document.body.classList.toggle('sky-voice-fallback', !canUseMicrophone());
        if (message) els.input.placeholder = message;
        updateVoiceHint('Type to Zoe', message || 'Voice is unavailable here. The same resolver will render cards from typed requests.', 'Type');
        requestAnimationFrame(() => els.input.focus({ preventScroll: true }));
    }

    function setVoiceLayerText(text) {
        const value = text || 'Listening';
        els.copy.textContent = value;
        if (els.commandCopy) els.commandCopy.textContent = value;
    }

    function getMicGuidance() {
        if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
            return 'Open Skybridge over HTTPS on the touch screen so the browser can allow microphone access.';
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            return 'This browser is not exposing a microphone API. Check kiosk permissions and use the HTTPS Zoe URL.';
        }
        return 'The touch screen will ask for microphone access the first time.';
    }

    function updateVoiceHint(title, detail, action) {
        els.voiceTitle.textContent = title;
        els.voiceDetail.textContent = detail;
        els.voiceAction.textContent = action;
        els.orbButtonText.textContent = action === 'Stop' ? 'Stop listening' : 'Tap to talk';
        els.mic.textContent = action === 'Stop' ? 'Stop' : 'Mic';
        els.mic.setAttribute('aria-label', title + '. ' + detail);
        els.mic.setAttribute('aria-pressed', action === 'Stop' ? 'true' : 'false');
        els.orbButton.setAttribute('aria-label', title + '. ' + detail);
    }

    function updateVoiceControl(state) {
        if (state === 'listening') {
            updateVoiceHint('Listening now', 'Speak naturally. Zoe will build the screen from what she hears.', 'Stop');
        } else if (state === 'thinking') {
            updateVoiceHint('Working on it', 'Finding the right data, page, or card surface.', 'Cancel');
        } else if (state === 'responding') {
            updateVoiceHint('Zoe is speaking', 'Tap to interrupt or ask the next thing.', 'Cancel');
        } else if (!voiceStartedByUser) {
            updateVoiceHint('Touch the orb to speak', getMicGuidance(), 'Start mic');
        } else {
            updateVoiceHint('Ready for the next request', 'Tap the orb or mic button to listen again.', 'Start mic');
        }
    }

    function toggleVoiceCapture() {
        if (!voice) {
            openCommandFallback('Voice is still connecting. Type here or tap again in a moment.');
            return;
        }
        if (!canUseMicrophone()) {
            openCommandFallback('Microphone needs HTTPS here. Type a request and Zoe will still render cards.');
            return;
        }
        if (voice.isRecording) {
            voice.stopRecording();
            return;
        }
        if (voice.speaking || voice.serverBusy) {
            voice.cancel().catch(err => showError(err.message || 'Cancel failed'));
            return;
        }
        voiceStartedByUser = true;
        voice.startRecording().catch(err => {
            voiceStartedByUser = false;
            showError(err.message || 'Microphone unavailable');
            openCommandFallback('Microphone is not available here. Type a request and Zoe will still render cards.');
        });
    }

    function resizeOrb() {
        const rect = els.canvas.getBoundingClientRect();
        els.canvas.width = Math.max(1, Math.floor(rect.width * window.devicePixelRatio));
        els.canvas.height = Math.max(1, Math.floor(rect.height * window.devicePixelRatio));
    }

    function startOrb() {
        resizeOrb();
        window.addEventListener('resize', resizeOrb);
        const draw = () => {
            animationFrame = requestAnimationFrame(draw);
            phase += 0.012;
            const ctx = els.ctx;
            const w = els.canvas.width;
            const h = els.canvas.height;
            ctx.clearRect(0, 0, w, h);
            const cx = w / 2;
            const cy = h / 2;
            const empty = document.body.classList.contains('sky-empty');
            const base = Math.min(w, h) * (empty ? 0.26 : 0.24);
            const pulse = 1 + (orbState === 'listening' ? 0.06 : orbState === 'responding' ? 0.08 : 0.025) * Math.sin(phase * 2);
            const r = base * pulse;
            const pair = colors[orbState] || colors.ambient;
            const outer = ctx.createRadialGradient(cx, cy, r * 0.1, cx, cy, r * 2.55);
            outer.addColorStop(0, pair[0] + '42');
            outer.addColorStop(0.35, pair[1] + '22');
            outer.addColorStop(1, 'transparent');
            ctx.fillStyle = outer;
            ctx.beginPath();
            ctx.arc(cx, cy, r * 2.55, 0, Math.PI * 2);
            ctx.fill();

            const glow = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 2.4);
            glow.addColorStop(0, pair[0] + 'aa');
            glow.addColorStop(0.52, pair[1] + '42');
            glow.addColorStop(1, 'transparent');
            ctx.fillStyle = glow;
            ctx.beginPath();
            ctx.arc(cx, cy, r * 2.4, 0, Math.PI * 2);
            ctx.fill();

            ctx.save();
            ctx.globalAlpha = empty ? 0.24 : 0.16;
            ctx.strokeStyle = pair[1] + '66';
            ctx.lineWidth = Math.max(1, r * 0.012);
            for (let i = 0; i < 3; i += 1) {
                const ring = r * (1.26 + i * 0.23 + Math.sin(phase + i) * 0.018);
                ctx.beginPath();
                ctx.arc(cx, cy, ring, 0, Math.PI * 2);
                ctx.stroke();
            }
            ctx.restore();

            const orb = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
            orb.addColorStop(0, '#ffffff');
            orb.addColorStop(0.17, '#c9f8ff');
            orb.addColorStop(0.48, pair[0]);
            orb.addColorStop(1, pair[1]);
            ctx.fillStyle = orb;
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            ctx.fill();

            const sheen = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
            sheen.addColorStop(0, 'rgba(255,255,255,0.24)');
            sheen.addColorStop(0.35, 'rgba(255,255,255,0.05)');
            sheen.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = sheen;
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            ctx.fill();
        };
        draw();
    }

    window.SkybridgeHydrateAuthCard = hydrateAuthCard;

    window.addEventListener('beforeunload', () => {
        if (animationFrame) cancelAnimationFrame(animationFrame);
        if (clockTicker) clearInterval(clockTicker);
        clearIdleTimer();
        if (voice) voice.stop();
    });

    document.addEventListener('DOMContentLoaded', init);
})();

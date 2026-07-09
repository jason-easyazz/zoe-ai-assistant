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
    // Bumped on every deck clear so a pending async append (e.g. the dashboard's
    // weather fetch) can detect the view changed under it and bail.
    let deckToken = 0;
    let currentUtterance = '';
    let voiceStartedByUser = false;
    let commandFallbackOpen = false;
    let voiceErrorFallback = false;
    let skybridgeContext = {};
    let authProfiles = [];
    let authHydrationSequence = 0;
    let idleTimer = null;
    let clockTicker = null;
    const activeTimers = new Map();   // id -> {id,label,expires,duration,ringing}
    let timerTickHandle = null;
    let audioCtx = null;
    let alarmTimer = null;
    let stallWatchdog = null;
    let stallDeferrals = 0;
    // Force-recover the panel if a turn goes silent for this long (re-armed on
    // every inbound event, so it only fires on a genuine stall, not a slow turn).
    const STALL_WATCHDOG_MS = 25000;
    // Cap consecutive recovery deferrals (active mic/TTS) so a stuck voice flag
    // can't permanently block the watchdog.
    const MAX_STALL_DEFERRALS = 3;

    const queryParams = new URLSearchParams(location.search);
    // How long a card stays up with no interaction before returning to the ambient
    // clock. Touch/voice resets this, so it's the quiet-time fade. 75s was too quick
    // to read a result you just asked for; 3 min is comfortable on a kiosk.
    const configuredIdleMs = Number(queryParams.get('idle_ms') || localStorage.getItem('skybridge_idle_return_ms') || 180000);
    const CARD_IDLE_MS = Number.isFinite(configuredIdleMs) ? Math.max(15000, configuredIdleMs) : 180000;
    // Ambient composed briefing: refresh at most every 5 min while resting on
    // the clock (piggybacks on the existing clock ticker — no extra interval).

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
        restoreTimers();   // a reload resumes any still-running countdown
        loadBackendStatus();
        startNowPlayingWatch();
        setMode(mode);
        syncVoiceFallbackState();
        if (typeof TouchMenu !== 'undefined') TouchMenu.init({ page: 'skybridge' });
        const initialQuery = new URLSearchParams(location.search).get('q');
        if (initialQuery) {
            // Strip ?q from the URL before running it, so a reload doesn't re-submit
            // the command — it can be side-effectful (e.g. "set a timer"), which
            // otherwise regenerates on every refresh. One-shot only.
            try {
                const u = new URL(location.href);
                u.searchParams.delete('q');
                history.replaceState(null, '', u.pathname + u.search + u.hash);
            } catch (_) {}
            setTimeout(() => submitCommand(initialQuery), 120);
        }
    }

    async function loadBackendStatus() {
        try {
            const pid = new URLSearchParams(location.search).get('panel_id') || localStorage.getItem('zoe_panel_id') || '';
            const resp = await fetch('/api/skybridge/status' + (pid ? '?panel_id=' + encodeURIComponent(pid) : ''), { headers: { 'Accept': 'application/json' } });
            if (!resp.ok) return;
            const data = await resp.json();
            if (data && data.status === 'ready') setStatus('Skybridge runtime ready');
            // Real auth state for the dashboard's profile chip (the device-session
            // user is server-side; localStorage can't see it). If the dashboard is
            // already up, re-render so "Sign in" corrects to the profile chip.
            if (data && data.user) {
                backendUser = data.user;
                if (document.body.classList.contains('sky-on-dashboard')) renderDashboardSurface(null);
            }
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
        els.navHome = document.getElementById('skyNavHome');
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
        els.nowPlaying = document.getElementById('skyNowPlaying');
        els.npArt = document.getElementById('skyNpArt');
        els.npTitle = document.getElementById('skyNpTitle');
        els.npArtist = document.getElementById('skyNpArtist');
        els.npOutputs = document.getElementById('skyNpOutputs');
        els.npScrubber = document.getElementById('skyNpScrubber');
        els.npSeek = document.getElementById('skyNpSeek');
        els.npElapsed = document.getElementById('skyNpElapsed');
        els.npDuration = document.getElementById('skyNpDuration');
    }

    function bindEvents() {
        document.querySelectorAll('[data-mode]').forEach(btn => {
            btn.addEventListener('click', () => setMode(btn.dataset.mode));
        });
        els.form.addEventListener('submit', event => {
            event.preventDefault();
            submitCommand(els.input.value);
            els.input.value = '';
            // The user has committed their message and the input is empty again,
            // so if we deferred a voice recovery while they were typing (a 'ready'
            // arrived mid-message), do it now — otherwise no further 'ready' fires
            // and the panel stays stuck in the typing fallback.
            recoverFromVoiceError();
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
        if (els.navHome) {
            // Always-visible Home while a card is up → back to the dashboard hub.
            els.navHome.addEventListener('click', () => wakeToDashboard());
        }
        if (els.nowPlaying) {
            // Mini-player: transport/volume + tap-the-track-to-expand + output picker.
            els.nowPlaying.addEventListener('click', event => {
                // Note: no stopPropagation here — the document-level handlers below
                // must still see the click (outside-tap picker-close; the wake-tap
                // handler already excludes .sky-nowplaying), otherwise an open picker
                // couldn't be dismissed by tapping the mini-player's own inert areas.
                const playerBtn = event.target.closest('[data-music-player]');
                if (playerBtn && event.target.closest('[data-music-picker]')) {
                    selectMusicPlayer(playerBtn.dataset.musicPlayer);
                    return;
                }
                const outBtn = event.target.closest('[data-music-output]');
                if (outBtn) { toggleMusicPicker(outBtn, els.npOutputs); return; }
                const btn = event.target.closest('[data-np-action]');
                if (!btn) return;
                npControl(btn.dataset.npAction);
            });
        }
        if (els.npSeek) {
            // Scrubbing: pause the client-side ticker while the user drags (so it
            // doesn't fight the thumb), reflect the fill live, and commit the seek
            // on release. 'change' fires on pointer-up / keyboard commit.
            els.npSeek.addEventListener('pointerdown', () => { npSeeking = true; });
            els.npSeek.addEventListener('input', () => {
                npSeeking = true;
                npRenderScrub(Number(els.npSeek.value) || 0, npDurationS);
            });
            els.npSeek.addEventListener('change', () => {
                npSeeking = false;
                npSeek(Number(els.npSeek.value) || 0);
            });
        }
        // Any outside tap closes an open speaker picker (card or mini-player).
        document.addEventListener('click', event => {
            if (event.target.closest('[data-music-picker], [data-music-output]')) return;
            closeMusicPickers();
        });
        // Touch the resting panel anywhere (not a control) to wake it to the
        // dashboard — the ambient clock should be a door, not a dead end.
        document.addEventListener('click', event => {
            if (!document.body.classList.contains('sky-empty')) return;
            if (event.target.closest('button, a, input, textarea, label, [data-sky-action], .sky-command, .sky-orb-button, .sky-nowplaying')) return;
            wakeToDashboard();
        });
        ['pointerdown', 'keydown', 'touchstart'].forEach(type => {
            document.addEventListener(type, noteUserActivity, { passive: true });
        });
        // Dead album-art URLs (e.g. a radio logo that 404s) must fall back to the
        // gradient placeholder, not the browser's broken-image glyph. `error` does
        // not bubble, so listen in the capture phase; any flagged art <img> that
        // fails to load is removed, revealing the gradient painted beneath it.
        els.cards.addEventListener('error', event => {
            const t = event.target;
            if (t && t.tagName === 'IMG' && t.hasAttribute('data-np-art-fallback')) {
                t.remove();
            }
        }, true);
        els.cards.addEventListener('click', event => {
            // Per-timer Cancel (✕) button → cancel just that one (others keep
            // running). Checked BEFORE the ringing-card tap so the ✕ always closes
            // exactly its own timer, even on a card that's currently ringing.
            const cancelBtn = event.target.closest('[data-timer-cancel]');
            if (cancelBtn) {
                cancelTimerLocal(cancelBtn.dataset.timerCancel);
                return;
            }
            // Tapping elsewhere on a ringing timer silences + dismisses it.
            if (event.target.closest('.sky-card.sky-timer-ringing') && acknowledgeRingingTimers()) {
                return;
            }
            const btn = event.target.closest('button[data-sky-action]');
            if (!btn) return;
            // "+ Add item" and friends: open the composer prefilled so you can type
            // or speak the rest (e.g. "add ⟂ to the shopping list").
            if (btn.dataset.skyAction === 'compose') {
                composeInInput(btn.dataset.compose || '', parseInt(btn.dataset.composeCaret, 10) || 0);
                return;
            }
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
            || localStorage.getItem('zoe_panel_id')
            || localStorage.getItem('zoe_touch_panel_id')
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
        // Any inbound activity during an active turn proves the pipeline is alive
        // — re-arm the stall watchdog so it only fires on real silence.
        if (event && event.type !== 'ready' && orbState !== 'ambient') armStallWatchdog();
        if (event.type === 'ready') {
            setStatus('Ready on ' + event.mode);
            // Voice transport just (re)connected. If we'd dropped into the typing
            // fallback purely because voice errored/disconnected, recover to voice
            // instead of leaving the panel stuck on "Type here while voice
            // reconnects..." after the socket is already back.
            recoverFromVoiceError();
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
        } else if (event.type === 'activity') {
            showActivity(event.phase, event.tool);
        } else if (event.type === 'error') {
            showError(event.message);
            if (/voice disconnected|transport unavailable|livekit unavailable|websocket|microphone|permission/i.test(event.message || '')) {
                voiceErrorFallback = true;
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
        if (data.timer_cancelled_id) removeTimer(data.timer_cancelled_id);
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
        if (showCards) {
            // The awake home surface IS the ambient dashboard (big time/date +
            // shortcut tiles) — the same surface as tapping Home over a card or
            // waking the resting panel. One home, not two.
            wakeToDashboard();
            return;
        }
        clearIdleTimer();
        document.body.classList.add('sky-empty');
        document.body.classList.add('sky-ambient-clock');
        commandFallbackOpen = false;
        voiceErrorFallback = false;
        document.body.classList.remove('sky-command-open');
        document.body.classList.remove('sky-typing-fallback');
        syncVoiceFallbackState();
        currentUtterance = '';
        skybridgeContext = {};
        setContext('Listening', 'The surface will build itself when Zoe understands what you need.');
        clearCards();
        setVoiceLayerText('Listening. Ask Zoe for anything.');
        updateAllClocks();
        updateVoiceHint('Touch the orb to speak', getMicGuidance(), 'Start mic');
        requestAnimationFrame(resizeOrb);
    }

    // Wake from rest into the guest dashboard: the orb stays present (tap/talk),
    // and the stage fills with glance cards anyone can see — time, weather, room
    // controls. Personal cards still ask for sign-in when tapped. Live weather is
    // fetched after the instant cards so the wake feels immediate.
    // Render the condensed guest dashboard (Layout B) as a single surface — clock +
    // weather tile + room/music/sign-in. Sets the stage directly (no clearCards,
    // which would flash the ambient clock back) and bumps the deck token so a
    // pending weather fetch can tell the view changed under it.
    let backendUser = null;   // from /api/skybridge/status — the authoritative panel user

    function panelSignedInName() {
        if (backendUser && !backendUser.guest && backendUser.username &&
            String(backendUser.username).toLowerCase() !== 'guest') {
            return backendUser.username;
        }
        // Same sources the clock card trusts: the panel auth challenge's selected
        // user first (device session), then a non-guest browser session.
        try {
            var c = JSON.parse(sessionStorage.getItem('zoe_panel_auth_challenge') || '{}');
            if (c.selected_username && String(c.selected_username).toLowerCase() !== 'guest') return c.selected_username;
        } catch (e) { /* fall through */ }
        try {
            var sess = JSON.parse(localStorage.getItem('zoe_session') || '{}');
            var role = (sess.role || (sess.user_info && sess.user_info.role) || '').toLowerCase();
            var uname = sess.username || (sess.user_info && sess.user_info.username) || '';
            if (uname && role && role !== 'guest') return uname;
        } catch (e) { /* fall through */ }
        return '';
    }

    let lastDashboardWeather = null;  // survives in-place re-renders (auth arrival)
    let dashboardVisit = 0;           // bumped per wake — NOT by in-place re-renders —
                                      // so late weather from a previous visit is discarded
                                      // while the auth-arrival re-render can't strand it

    function renderDashboardSurface(weather) {
        // Any re-render (e.g. the /status user arriving) keeps the last live
        // weather instead of blanking the tile; a fresh weather payload updates it.
        if (weather) lastDashboardWeather = weather;
        else weather = lastDashboardWeather;
        // Build the surface BEFORE tearing down the ambient clock. This wake used to
        // flip the body classes (drop sky-empty / sky-ambient-clock) and only THEN
        // call the renderer, so any throw — an odd live-weather shape from the async
        // re-render, a missing glyph — left the clock removed and the stage empty:
        // the "bare clock on wake". Render to a string first and bail without touching
        // the surface if it fails, so a failed weather fetch or a renderer error can
        // never blank the panel.
        var markup = '';
        try {
            var name = panelSignedInName();
            var sun = null;
            try { sun = (window.SkybridgeTheme && window.SkybridgeTheme.sunTimes) ? window.SkybridgeTheme.sunTimes() : null; } catch (e) { /* sun is optional */ }
            markup = window.SkybridgeRenderer.render({
                component: 'dashboard',
                props: { guest: !name, user_name: name, sun: sun, weather: weather || null }
            });
        } catch (e) {
            markup = '';
        }
        if (!markup) {
            // Nothing safe to show. If the dashboard is already up (a re-render
            // failed) keep the existing tiles. Otherwise we were waking from the
            // ambient clock or an existing card (Home pill) — fall back to the
            // ambient clock so a failed wake never strands a stale card or a blank
            // stage.
            if (!document.body.classList.contains('sky-on-dashboard')) clearCards();
            return;
        }
        deckToken++;
        cardSequence = 0;
        document.body.classList.remove('sky-empty');
        document.body.classList.remove('sky-ambient-clock');
        document.body.classList.add('sky-has-cards');
        // The dashboard IS home: the floating Home pill hides on this surface
        // (stage css) and returns as soon as any other card takes the stage.
        document.body.classList.add('sky-on-dashboard');
        els.cards.innerHTML = markup;
        requestAnimationFrame(resizeOrb);
    }

    function wakeToDashboard() {
        dashboardVisit++;
        const visit = dashboardVisit;
        renderDashboardSurface(null);
        scheduleIdleReturn();
        // Enrich with live weather (guest-readable); re-render the surface with it,
        // but only if the user hasn't moved on (token still current).
        fetch('/api/skybridge/resolve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ message: 'weather' })
        }).then(res => (res.ok ? res.json() : null)).then(data => {
            // Apply iff this is still the SAME dashboard visit (a late fetch from a
            // previous visit is stale) and the dashboard is still the active surface.
            // The auth-arrival re-render bumps neither, so it can't strand us.
            if (!data || visit !== dashboardVisit || !document.body.classList.contains('sky-on-dashboard')) return;
            // The resolve card carries its data under `content` (props is created
            // later by the renderer's normalization), so read either.
            const wxCard = (Array.isArray(data.cards) ? data.cards : []).find(c => {
                const src = (c && c.content && c.content.source) || (c && c.props && c.props.source) || '';
                return /weather/.test(String(src));
            });
            if (wxCard) renderDashboardSurface(wxCard.content || wxCard.props);
        }).catch(() => { /* weather is best-effort on the dashboard */ });
    }

    function clearCards() {
        cardSequence = 0;
        deckToken++;
        els.cards.innerHTML = '';
        document.body.classList.add('sky-empty');
        document.body.classList.add('sky-ambient-clock');
        document.body.classList.remove('sky-on-dashboard');
        document.body.classList.remove('sky-has-cards');
        requestAnimationFrame(resizeOrb);
    }

    function addCard(card, prepend, delayMs) {
        // A new card changes the deck, so invalidate any pending async render
        // (e.g. the dashboard's weather fetch) that would otherwise overwrite it.
        deckToken++;
        document.body.classList.remove('sky-empty');
        document.body.classList.remove('sky-ambient-clock');
        document.body.classList.remove('sky-on-dashboard');
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
        hydrateNowPlayingQueue(node);
        registerTimerCard(node, card);
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

    // ── Timers ──────────────────────────────────────────────────────────────
    // A real countdown engine: timers live in this JS map (authoritative for
    // firing), tick the on-screen card, persist to localStorage so a reload
    // resumes, and ring an audible alarm at zero — even if another card is up.
    const TIMERS_KEY = 'sky_active_timers';

    function _timerSel(id) {
        const safe = (window.CSS && CSS.escape) ? CSS.escape(id) : id;
        return '.sky-timer[data-timer-id="' + safe + '"]';
    }
    // Every on-screen .sky-timer for this id. A full-deck re-render (a second
    // timer returns the whole running set) layered over cards already on screen
    // can briefly leave two nodes for one timer; ticking/removing must touch ALL
    // of them, or the stale copy freezes and can't be closed.
    function _timerEls(id) {
        return [].slice.call(els.cards.querySelectorAll(_timerSel(id)));
    }
    // Collapse duplicate timer cards (keep the newest node per id) and drop orphan
    // cards whose timer is no longer active. A full-deck re-render layered over
    // cards already on screen can leave a stale twin that freezes and captures the
    // single-node lookups; removing it also restores timer.css's `:only-child`
    // full-width rule for a lone timer (a phantom twin would strand it half-width).
    function syncTimerCards() {
        const seen = new Set();
        // Walk newest → oldest so the freshest node survives.
        [].slice.call(els.cards.querySelectorAll('.sky-timer')).reverse().forEach(el => {
            const id = el.dataset.timerId;
            const card = el.closest('.sky-card');
            if (!id || !activeTimers.has(id) || seen.has(id)) {
                if (card) card.remove();
                return;
            }
            seen.add(id);
        });
    }
    function _fmtClock(secs) {
        secs = Math.max(0, secs);
        return String(Math.floor(secs / 60)).padStart(2, '0') + ':' + String(secs % 60).padStart(2, '0');
    }
    function persistTimers() {
        try {
            localStorage.setItem(TIMERS_KEY, JSON.stringify([...activeTimers.values()]
                .map(t => ({ id: t.id, label: t.label, expires: t.expires, duration: t.duration }))));
        } catch (_) {}
    }
    function ensureTimerTicking() {
        if (timerTickHandle == null && activeTimers.size) timerTickHandle = setInterval(timerTick, 250);
    }
    function registerTimer(id, label, expires, duration) {
        if (!id || !(+expires)) return;
        const existing = activeTimers.get(id);
        activeTimers.set(id, {
            id, label: label || 'Timer', expires: +expires, duration: +duration || 0,
            ringing: existing ? existing.ringing : false
        });
        persistTimers();
        ensureTimerTicking();
    }
    function registerTimerCard(node, card) {
        const type = card && (card.component || card.card_type);
        const props = (card && (card.props || card.content)) || {};
        if (type !== 'timer' && props.source !== 'timer') return;
        const el = node.querySelector('.sky-timer');
        const id = (el && el.dataset.timerId) || props.timer_id || props.id;
        const expires = (el && +el.dataset.timerExpires) || +props.expires_at_ms;
        if (id && expires) registerTimer(id, props.label || props.title, expires, props.duration_seconds);
        // Collapse any duplicate/orphan timer cards so a newly-added timer never
        // freezes a stale twin (or strands a lone timer at half width).
        syncTimerCards();
    }
    function removeTimer(id) {
        if (!id || !activeTimers.has(id)) return;
        const t = activeTimers.get(id);
        activeTimers.delete(id);
        persistTimers();
        if (t && t.ringing) stopAlarm();
        // Remove EVERY card for this id (a stale duplicate must not linger, unclosable).
        _timerEls(id).forEach(el => {
            const card = el.closest('.sky-card');
            if (card) card.remove();
        });
        if (!activeTimers.size && timerTickHandle != null) { clearInterval(timerTickHandle); timerTickHandle = null; }
        if (!els.cards.children.length) clearCards();
    }
    function timerTick() {
        const now = Date.now();
        activeTimers.forEach(t => {
            const remaining = Math.max(0, Math.ceil((t.expires - now) / 1000));
            const els_ = _timerEls(t.id);
            // Update EVERY node for this timer so each card ticks independently and
            // no stale duplicate is left frozen on screen.
            els_.forEach(el => {
                const digits = el.querySelector('.sky-timer-digits');
                const fill = el.querySelector('.sky-timer-ring-fill');
                if (digits && !t.ringing) digits.textContent = _fmtClock(remaining);
                if (fill && t.duration && !t.ringing) {
                    const frac = Math.max(0, Math.min(1, remaining / t.duration));
                    fill.setAttribute('stroke-dashoffset', (100 * (1 - frac)).toFixed(2));
                    el.classList.toggle('is-low', frac <= 0.15);
                }
            });
            if (remaining <= 0 && !t.ringing) fireTimer(t);
        });
        if (!activeTimers.size && timerTickHandle != null) { clearInterval(timerTickHandle); timerTickHandle = null; }
    }
    function fireTimer(t) {
        t.ringing = true;
        persistTimers();
        if (!_timerEls(t.id).length) {
            // Rang while its card wasn't on screen — surface a fresh ringing one.
            addCard({ component: 'timer', props: { timer_id: t.id, label: t.label, title: t.label,
                duration_seconds: t.duration, expires_at_ms: t.expires, status: 'expired' } }, true);
        }
        // Ring EVERY node for this id: if a stale duplicate slipped through, both
        // must flip to the ringing/expired state so neither is left as a dead
        // 00:00 card that ignores tap-to-dismiss.
        _timerEls(t.id).forEach(inner => {
            const card = inner.closest('.sky-card');
            if (card) card.classList.add('sky-timer-ringing');
            inner.dataset.timerStatus = 'expired';
            const digits = inner.querySelector('.sky-timer-digits');
            if (digits) digits.textContent = "Time's up";
        });
        const named = (t.label && t.label.toLowerCase() !== 'timer') ? t.label + ' timer' : 'Timer';
        setVoiceLayerText(named + " — time's up!");
        startAlarm();
        clearIdleTimer();   // keep the ringing card up
    }
    function startAlarm() {
        stopAlarm();
        let count = 0;
        const beep = () => {
            try {
                audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
                if (audioCtx.state === 'suspended') audioCtx.resume();
                const o = audioCtx.createOscillator(), g = audioCtx.createGain();
                o.type = 'sine'; o.frequency.value = 880;
                g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
                g.gain.exponentialRampToValueAtTime(0.4, audioCtx.currentTime + 0.02);
                g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
                o.connect(g); g.connect(audioCtx.destination);
                o.start(); o.stop(audioCtx.currentTime + 0.36);
            } catch (_) {}
            if (++count >= 40) stopAlarm();   // ~30s safety cap
        };
        beep();
        alarmTimer = setInterval(beep, 750);
    }
    function stopAlarm() {
        if (alarmTimer != null) { clearInterval(alarmTimer); alarmTimer = null; }
    }
    function acknowledgeRingingTimers() {
        const ringing = [...activeTimers.values()].filter(t => t.ringing);
        if (!ringing.length) return false;
        stopAlarm();
        ringing.forEach(t => removeTimer(t.id));
        return true;
    }
    function cancelTimerLocal(id) {
        if (!id) return;
        removeTimer(id);   // immediate on the panel (the firing authority)
        try {
            fetch('/api/skybridge/timers/cancel', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ timer_id: id })
            });   // best-effort server sync so spoken "how long left" stays accurate
        } catch (_) {}
    }

    function renderActiveTimers() {
        clearCards();
        [...activeTimers.values()].sort((a, b) => a.expires - b.expires).forEach(t => {
            addCard({ component: 'timer', props: { timer_id: t.id, label: t.label, title: t.label,
                duration_seconds: t.duration, expires_at_ms: t.expires, status: t.ringing ? 'expired' : 'running' } }, false);
        });
    }

    function restoreTimers() {
        let arr = [];
        try { arr = JSON.parse(localStorage.getItem(TIMERS_KEY) || '[]') || []; } catch (_) {}
        const now = Date.now();
        // resume every still-running timer, not just the soonest
        arr.filter(t => t && t.id && +t.expires > now).forEach(t => registerTimer(t.id, t.label, t.expires, t.duration));
        if (activeTimers.size) renderActiveTimers();
    }

    function noteUserActivity() {
        if (!document.body.classList.contains('sky-empty')) {
            scheduleIdleReturn();
        }
    }

    function scheduleIdleReturn() {
        clearIdleTimer();
        if (document.body.classList.contains('sky-empty')) return;
        // Keep the screen up only when the *only* thing showing is running timers —
        // a countdown shouldn't auto-dismiss. Any other card (weather, calendar…)
        // still idles back normally even while a timer runs in the background.
        const cards = [].slice.call(els.cards.children);
        const hasNonTimer = cards.some(function (c) { return !c.querySelector('.sky-timer'); });
        if (cards.length && !hasNonTimer) return;
        idleTimer = setTimeout(returnToAmbientClock, CARD_IDLE_MS);
    }

    function returnToAmbientClock() {
        idleTimer = null;
        const voiceBusy = voice && (voice.isRecording || voice.speaking || voice.serverBusy);
        if (voiceBusy || orbState === 'listening' || orbState === 'thinking' || orbState === 'responding') {
            scheduleIdleReturn();
            return;
        }
        // If timers are still running, fall back to showing them rather than a bare
        // ambient clock, so the countdown returns to view once the other card idles.
        if (activeTimers.size) {
            renderActiveTimers();
            setStatus('Ambient');
            return;
        }
        renderHome({ idle: true });
        setStatus('Ambient');
    }

    // ── Turn resilience ─────────────────────────────────────────────────────
    // A hung brain / dropped-but-open socket / interrupted page-fade could leave
    // the panel stuck — spinning in "thinking" forever, or invisible (body left
    // at opacity 0 by an aborted nav). These guarantee the panel always returns
    // to a usable ambient state and is never left invisible.
    function clearStallWatchdog() {
        if (stallWatchdog) { clearTimeout(stallWatchdog); stallWatchdog = null; }
    }

    function armStallWatchdog() {
        clearStallWatchdog();
        stallWatchdog = setTimeout(function () {
            stallWatchdog = null;
            recoverToAmbient('stall-watchdog');
        }, STALL_WATCHDOG_MS);
    }

    function recoverToAmbient(reason) {
        // Genuine mic/TTS activity isn't a hang — defer. But a stale voice flag
        // (socket crash leaving isRecording/speaking stuck true) must not suppress
        // the last-resort recovery forever: allow a few deferrals, then recover.
        if (stallDeferrals < MAX_STALL_DEFERRALS && voice && (voice.isRecording || voice.speaking)) {
            stallDeferrals++;
            armStallWatchdog();
            return;
        }
        stallDeferrals = 0;
        clearStallWatchdog();
        try { console.warn('[skybridge] recovering to ambient:', reason); } catch (_) {}
        // Never leave the UI invisible from an interrupted page-fade (touch-menu _nav).
        if (document.body.style.opacity !== '') document.body.style.opacity = '';
        if (voice) { try { voice.serverBusy = false; } catch (_) {} }
        if (orbState !== 'ambient') setState('ambient');
    }

    // ── Persistent now-playing mini-player ──────────────────────────────────
    // Polls MA's now-playing snapshot and keeps the floating mini-player in sync.
    // Shown only while something is playing/paused; its controls hit the same
    // /api/music/control endpoint the cards use. Every failure path is silent and
    // just hides the player — it must never disrupt voice or the resting clock.
    const NP_POLL_MS = 5000;
    let npPollHandle = null;
    let npInFlight = false;
    let npPlayerId = '';          // stick to the active player for control calls
    let npLastArt = '';           // avoid reloading identical album art each poll
    let npActive = false;         // true while something is actually playing/paused
    let npDurationS = 0;          // current track length (s), 0 when unknown (radio)
    let npElapsedS = 0;           // last-known elapsed (s); advanced by the ticker
    let npIsPlaying = false;      // drives whether the ticker advances elapsed
    let npSeeking = false;        // true while the user drags the scrubber thumb
    let npScrubTicker = null;     // 1s interval that advances the scrubber between polls
    const MUSIC_PLAYER_KEY = 'zoe_music_player_id';  // persisted preferred speaker

    // The chosen output speaker persists so the whole panel (poll, control, and
    // any play) consistently targets it once the user picks e.g. "Kitchen".
    function getMusicPlayerId() {
        try { return localStorage.getItem(MUSIC_PLAYER_KEY) || ''; } catch (_) { return ''; }
    }
    function setMusicPlayerId(id) {
        try { if (id) localStorage.setItem(MUSIC_PLAYER_KEY, id); } catch (_) {}
    }

    function startNowPlayingWatch() {
        // Require the whole mini-player subtree so applyNowPlaying can trust its
        // element refs (keeps DOM-missing distinct from network failures).
        if (!els.nowPlaying || !els.npTitle || !els.npArtist || !els.npArt) return;
        pollNowPlaying();
        npPollHandle = setInterval(pollNowPlaying, NP_POLL_MS);
    }

    async function pollNowPlaying() {
        if (npInFlight || !els.nowPlaying) return;
        npInFlight = true;
        try {
            const pid = getMusicPlayerId();
            const url = '/api/music/now-playing' + (pid ? '?player_id=' + encodeURIComponent(pid) : '');
            const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
            if (!resp.ok) { hideNowPlaying(); return; }
            const data = await resp.json();
            const np = data && data.available ? (data.now_playing || {}) : null;
            const state = np && String(np.state || '').toLowerCase();
            if (np && (state === 'playing' || state === 'paused')) {
                applyNowPlaying(np, state === 'playing');
            } else {
                hideNowPlaying();
            }
        } catch (_) {
            hideNowPlaying();   // MA unreachable → no mini-player, no noise
        } finally {
            npInFlight = false;
        }
    }

    function applyNowPlaying(np, isPlaying) {
        npPlayerId = np.player_id || '';
        els.npTitle.textContent = np.title || np.player_name || 'Now playing';
        els.npArtist.textContent = np.artist || '';
        // Same-origin / absolute http(s) art only, mirroring the card's guard.
        const art = typeof np.image === 'string' && /^(https?:\/\/|\/)[^"'()\\<>\s]+$/.test(np.image) ? np.image : '';
        if (art !== npLastArt) {
            npLastArt = art;
            const glyph = els.npArt.querySelector('svg');
            const oldImg = els.npArt.querySelector('img');
            if (oldImg) oldImg.remove();
            if (art) {
                const img = document.createElement('img');
                img.alt = '';
                img.src = art;
                img.onerror = () => { img.remove(); els.npArt.classList.remove('has-art'); if (glyph) glyph.style.display = ''; };
                els.npArt.appendChild(img);
                els.npArt.classList.add('has-art');
            } else {
                els.npArt.classList.remove('has-art');
            }
        }
        els.nowPlaying.classList.toggle('is-playing', !!isPlaying);
        els.nowPlaying.hidden = false;
        npActive = true;
        // Scrubber: reflect server progress (unless the user is mid-drag), show it
        // only when the source has a known duration, and (re)arm the smooth ticker.
        npIsPlaying = !!isPlaying;
        const dur = Number(np.duration);
        const el = Number(np.elapsed);
        npDurationS = isFinite(dur) && dur > 0 ? Math.floor(dur) : 0;
        if (!npSeeking) {
            npElapsedS = isFinite(el) && el >= 0 ? Math.min(Math.floor(el), npDurationS || Infinity) : 0;
            npSyncScrubber();
        }
        npEnsureTicker();
    }

    // Show/position the scrubber for the current known progress.
    function npSyncScrubber() {
        if (!els.npScrubber || !els.npSeek) return;
        if (npDurationS > 0) {
            els.npScrubber.hidden = false;
            els.npSeek.max = String(npDurationS);
            if (!npSeeking) els.npSeek.value = String(npElapsedS);
            if (els.npDuration) els.npDuration.textContent = npFormatTime(npDurationS);
            npRenderScrub(npElapsedS, npDurationS);
        } else {
            els.npScrubber.hidden = true;   // radio / durationless → no scrubber
        }
    }

    // Paint the elapsed label + track-fill percentage (no server call).
    function npRenderScrub(elapsed, duration) {
        if (els.npElapsed) els.npElapsed.textContent = npFormatTime(elapsed);
        if (els.npSeek && duration > 0) {
            const pct = Math.max(0, Math.min(100, (elapsed / duration) * 100));
            els.npSeek.style.setProperty('--snp-seek-pct', pct.toFixed(2) + '%');
        }
    }

    function npFormatTime(secs) {
        secs = Math.max(0, Math.floor(Number(secs) || 0));
        const m = Math.floor(secs / 60), s = secs % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    // One shared 1s ticker advances the displayed elapsed while playing so the
    // thumb glides between the 5s polls; polls remain the source of truth.
    function npEnsureTicker() {
        if (npScrubTicker) return;
        npScrubTicker = setInterval(() => {
            if (!npActive || !npIsPlaying || npSeeking || npDurationS <= 0) return;
            if (npElapsedS >= npDurationS) return;
            npElapsedS += 1;
            if (els.npSeek) els.npSeek.value = String(npElapsedS);
            npRenderScrub(npElapsedS, npDurationS);
        }, 1000);
    }

    async function npSeek(positionSeconds) {
        const pos = Math.max(0, Math.floor(Number(positionSeconds) || 0));
        npElapsedS = pos;
        npRenderScrub(pos, npDurationS);
        try {
            const resp = await fetch('/api/music/seek', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ position_seconds: pos, player_id: getMusicPlayerId() || npPlayerId })
            });
            if (!resp.ok) { pollNowPlaying(); return; }
            // The endpoint returns 200 with { ok:false } when MA rejected the seek
            // (no active player, durationless item, MA down). Reconcile immediately
            // so the optimistic thumb snaps back to the real position, don't wait.
            const data = await resp.json().catch(() => null);
            if (!data || data.ok === false) { pollNowPlaying(); return; }
        } catch (_) { pollNowPlaying(); return; }
        setTimeout(pollNowPlaying, 400);
    }

    function hideNowPlaying() {
        npActive = false;
        npIsPlaying = false;
        if (npScrubTicker) { clearInterval(npScrubTicker); npScrubTicker = null; }
        if (els.nowPlaying && !els.nowPlaying.hidden) {
            els.nowPlaying.hidden = true;
            els.nowPlaying.classList.remove('is-playing');
        }
        if (els.npScrubber) els.npScrubber.hidden = true;
    }

    async function npControl(action) {
        if (action === 'expand') { submitCommand("what's playing"); return; }
        // Optimistic: flip the play/pause glyph immediately, then confirm by poll.
        if (action === 'play_pause' && els.nowPlaying) {
            els.nowPlaying.classList.toggle('is-playing');
            npIsPlaying = els.nowPlaying.classList.contains('is-playing');
        }
        try {
            const resp = await fetch('/api/music/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, player_id: getMusicPlayerId() || npPlayerId })
            });
            // On a non-2xx, reconcile immediately so an optimistic glyph flip that
            // didn't actually take doesn't linger until the next scheduled poll.
            if (!resp.ok) { pollNowPlaying(); return; }
        } catch (_) { pollNowPlaying(); return; }  // network fail → reconcile now
        // Re-poll shortly after so state (track change / real play state) catches up.
        setTimeout(pollNowPlaying, 350);
    }

    // ── Speaker / output picker (music hub) ──────────────────────────────────
    // Shared by the now-playing card and the mini-player: fetch MA's speakers,
    // let the user pick one, persist it, and (when playback is live) move it
    // there via /api/music/transfer. All best-effort — never breaks a turn.
    async function fetchMusicPlayers() {
        try {
            const resp = await fetch('/api/music/players', { headers: { 'Accept': 'application/json' } });
            if (!resp.ok) return [];
            const data = await resp.json();
            return (data && Array.isArray(data.players)) ? data.players : [];
        } catch (_) { return []; }
    }

    function musicPlayerName(p) {
        return (p && (p.display_name || p.name)) || 'Speaker';
    }

    function pickerItemsHtml(players, activeId) {
        const esc = window.SkybridgeRenderer.escapeHtml;
        const rows = (players || [])
            .filter(p => p && p.player_id && p.available !== false)
            .map(p => {
                const id = p.player_id;
                const on = id === activeId;
                return '<button type="button" class="mp-opt' + (on ? ' is-active' : '') + '"'
                    + ' role="option" aria-selected="' + (on ? 'true' : 'false') + '"'
                    + ' data-music-player="' + esc(id) + '">'
                    + '<span class="mp-opt-name">' + esc(musicPlayerName(p)) + '</span>'
                    + (on ? '<span class="mp-opt-check" aria-hidden="true">✓</span>' : '')
                    + '</button>';
            }).join('');
        return rows || '<div class="mp-empty">No speakers found</div>';
    }

    function closeMusicPickers() {
        document.querySelectorAll('[data-music-picker]').forEach(el => { el.hidden = true; });
        document.querySelectorAll('[data-music-output]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    }

    async function toggleMusicPicker(btn, container) {
        if (!container) return;
        const wasOpen = container.hidden === false;
        closeMusicPickers();
        if (wasOpen) return;   // second tap on the same button just closes it
        container.innerHTML = '<div class="mp-empty">Finding speakers…</div>';
        container.hidden = false;
        if (btn) btn.setAttribute('aria-expanded', 'true');
        const players = await fetchMusicPlayers();
        if (container.hidden) return;   // closed again while fetching
        container.innerHTML = pickerItemsHtml(players, getMusicPlayerId() || npPlayerId);
    }

    async function selectMusicPlayer(id) {
        if (!id) return;
        const prev = npPlayerId || getMusicPlayerId();
        const wasActive = npActive;
        setMusicPlayerId(id);
        npPlayerId = id;
        closeMusicPickers();
        // Move live playback to the newly chosen speaker; if nothing's playing we
        // only set the preferred target (degrades gracefully, no transfer).
        if (wasActive && prev && prev !== id) {
            try {
                await fetch('/api/music/transfer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_player_id: id, source_player_id: prev })
                });
            } catch (_) { /* best-effort */ }
        }
        // Reflect the new speaker name wherever the card shows it, then reconcile.
        const players = await fetchMusicPlayers();
        const chosen = players.find(p => p && p.player_id === id);
        if (chosen) {
            const name = musicPlayerName(chosen);
            document.querySelectorAll('.np-out-name').forEach(el => { el.textContent = name; });
        }
        setTimeout(pollNowPlaying, 300);
    }

    // ── "Up next" queue on the now-playing canvas ────────────────────────────
    // The card ships an empty [data-music-queue] container (queue payload kept
    // off the server card); we fetch the queue and render the upcoming items.
    function npQueueItemArt(item) {
        const mi = (item && item.media_item) || item || {};
        let src = mi.image;
        if (src && typeof src === 'object') src = src.path || src.url || '';
        const imgs = (mi.metadata && mi.metadata.images) || [];
        if (!src && Array.isArray(imgs) && imgs[0]) src = imgs[0].path || imgs[0].url || '';
        return (typeof src === 'string' && /^(https?:\/\/|\/)[^"'()\\<>\s]+$/.test(src)) ? src : '';
    }
    function npQueueItemTitle(item) {
        // Prefer the underlying media item's name (matches how now_playing derives
        // the current title); fall back to the QueueItem's own name.
        const mi = (item && item.media_item) || {};
        return String((mi && mi.name) || (item && item.name) || '').trim();
    }
    function npQueueItemArtist(item) {
        const mi = (item && item.media_item) || item || {};
        const artists = mi.artists || [];
        if (Array.isArray(artists) && artists.length) {
            return artists.map(a => (a && (a.name || a)) || '').filter(Boolean).join(', ');
        }
        return mi.artist || '';
    }
    async function hydrateNowPlayingQueue(node) {
        const box = node && node.querySelector('[data-music-queue]');
        if (!box) return;
        const queueId = box.dataset.queueId || '';
        if (!queueId) return;
        let items = [];
        try {
            const resp = await fetch('/api/music/queue/' + encodeURIComponent(queueId), { headers: { 'Accept': 'application/json' } });
            if (!resp.ok) return;
            const data = await resp.json();
            items = (data && Array.isArray(data.items)) ? data.items : [];
        } catch (_) { return; }
        if (!box.isConnected) return;
        const esc = window.SkybridgeRenderer.escapeHtml;
        // Drop everything up to and including the current track, so we show only
        // what's genuinely "up next" (MA returns the whole queue from the start).
        const current = (box.dataset.currentTitle || '').trim();
        let start = 0;
        if (current) {
            const idx = items.findIndex(it => npQueueItemTitle(it) === current);
            if (idx >= 0) start = idx + 1;
        }
        const upcoming = items.slice(start, start + 6);
        if (!upcoming.length) { box.hidden = true; return; }
        const noteSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 17V5l10-2v12" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="6" cy="17" r="3"/><circle cx="16" cy="15" r="3"/></svg>';
        const rows = upcoming.map(it => {
            const title = esc(npQueueItemTitle(it) || 'Track');
            const artist = esc(npQueueItemArtist(it));
            const art = npQueueItemArt(it);
            const dur = Number((it && it.duration) || 0);
            const time = dur > 0 ? npFormatTime(dur) : '';
            const artHtml = '<span class="np-qart">' + noteSvg
                + (art ? '<img src="' + esc(art) + '" alt="" loading="lazy" data-np-art-fallback>' : '')
                + '</span>';
            return '<div class="np-qrow">' + artHtml
                + '<span class="np-qmeta"><span class="np-qtitle">' + title + '</span>'
                + '<span class="np-qsub">' + artist + '</span></span>'
                + (time ? '<span class="np-qtime">' + time + '</span>' : '')
                + '</div>';
        }).join('');
        box.innerHTML = '<div class="np-queue-head">Up next</div>' + rows;
        box.hidden = false;
    }

    function startClockTicker() {
        updateAllClocks();
        // One shared 1s ticker for the live clock numerals.
        clockTicker = setInterval(function () {
            updateAllClocks();
        }, 1000);
    }

    // ── Ambient composed briefing ───────────────────────────────────────────
    // While the panel rests on the ambient clock, fetch Zoe's composed briefing
    // card and show it as a modest card under the clock. The clock stays
    // primary; the briefing is best-effort — every failure path is silent and
    // leaves the plain clock exactly as it was. Night-dim is inherited from
    // #skyAmbientClock's brightness filter (no CSS of its own).
    function isRestingOnAmbientClock() {
        return document.body.classList.contains('sky-empty')
            && document.body.classList.contains('sky-ambient-clock');
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
                '<span class="sky-auth-avatar" aria-hidden="true">' + avatar + '</span>' +
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

    // ── Live-activity strip ─────────────────────────────────────────────────
    // "What Zoe is DOING" during brain turns. The server forwards brain tool
    // sentinels as {type:'activity', phase:'start'|'result', tool:'calendar'}
    // frames (name + phase only). A compact one-line strip near the orb pulses
    // with a friendly verb while a tool runs, flashes a ✓ on result, and clears
    // when the turn finishes (done/ambient). Never overlaps cards — it lives
    // inside the orb panel.
    let activityClearTimer = null;

    // Friendly verb per tool (mirrors chat.html's activityToolVerb, tuned for
    // the voice-brain tool set). Prefix match so 'calendar_show' → calendar.
    const ACTIVITY_VERBS = {
        calendar: 'Checking calendar',
        lists: 'Looking at your lists',
        list: 'Looking at your list',
        reminders: 'Checking reminders',
        reminder: 'Checking reminders',
        mempalace_search: 'Checking memory',
        mempalace_add: 'Saving to memory',
        memory: 'Checking memory',
        weather: 'Checking the weather',
        web_search: 'Searching the web',
        web: 'Looking that up',
        search: 'Looking that up',
        browse: 'Reading a page',
        ha_control: 'Adjusting the house',
        bash: 'Running a command',
        show_map: 'Preparing a map',
        show_chart: 'Drawing a chart'
    };

    function activityToolVerb(name) {
        // Local escaper: the tool name arrives over the wire — keep only safe
        // identifier characters before it can ever reach the DOM.
        const key = String(name || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
        if (ACTIVITY_VERBS[key]) return ACTIVITY_VERBS[key];
        for (const k in ACTIVITY_VERBS) {
            if (key.indexOf(k) === 0) return ACTIVITY_VERBS[k];
        }
        return key ? 'Using ' + key.replace(/_/g, ' ') : 'Working';
    }

    function activityStripEl() {
        let el = document.getElementById('skyActivityStrip');
        if (!el) {
            el = document.createElement('div');
            el.id = 'skyActivityStrip';
            el.className = 'sky-activity-strip';
            el.setAttribute('aria-live', 'polite');
            const host = document.querySelector('.sky-orb-panel');
            const anchor = document.getElementById('skyListeningCopy');
            if (host && anchor && anchor.parentElement === host) {
                host.insertBefore(el, anchor);
            } else if (host) {
                host.appendChild(el);
            } else {
                document.body.appendChild(el);
            }
        }
        return el;
    }

    function showActivity(phase, tool) {
        const el = activityStripEl();
        if (activityClearTimer) { clearTimeout(activityClearTimer); activityClearTimer = null; }
        const verb = activityToolVerb(tool);
        if (phase === 'start') {
            // textContent, never innerHTML: wire-derived text must not be parsed.
            el.textContent = verb + '…';
            el.classList.add('is-active', 'is-visible');
            el.classList.remove('is-done');
        } else if (phase === 'result') {
            el.textContent = '✓ ' + verb;
            el.classList.remove('is-active');
            el.classList.add('is-done', 'is-visible');
            activityClearTimer = setTimeout(clearActivity, 1800);
        }
    }

    function clearActivity() {
        if (activityClearTimer) { clearTimeout(activityClearTimer); activityClearTimer = null; }
        const el = document.getElementById('skyActivityStrip');
        if (el) {
            el.classList.remove('is-visible', 'is-active', 'is-done');
            el.textContent = '';
        }
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
        // Arm the watchdog while a server-dependent turn is in flight; clear it
        // once we're back at rest. Ambient also retires the live-activity strip
        // — a finished turn must never leave a stale "Checking calendar…" up.
        if (state === 'ambient') { stallDeferrals = 0; clearStallWatchdog(); clearActivity(); }
        else armStallWatchdog();
    }

    function canUseMicrophone() {
        return !!(window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1') &&
            !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    function syncVoiceFallbackState() {
        document.body.classList.toggle('sky-voice-fallback', !canUseMicrophone());
    }

    // Voice came back after an error-driven typing fallback. Quietly return the
    // panel to voice mode — but only when it's safe: the mic must actually be
    // usable, and we must not be yanking the keyboard away from a user who has
    // started typing a message. Cards/context on screen are left untouched.
    function recoverFromVoiceError() {
        if (!voiceErrorFallback) return;
        if (!canUseMicrophone()) return;
        if (els.input && els.input.value.trim()) return;
        voiceErrorFallback = false;
        commandFallbackOpen = false;
        if (els.input) els.input.placeholder = '';
        document.body.classList.remove('sky-command-open');
        document.body.classList.remove('sky-typing-fallback');
        syncVoiceFallbackState();
        // Leave the status line as the caller set it (e.g. "Ready on local") — it
        // carries the transport mode; don't clobber it with a generic message.
    }

    // Open the typed composer prefilled with `text`, caret at `caret` — the rest of
    // the phrase (the item) goes through the same resolver as voice. Voice users can
    // still just speak; this is the touch path to add without a keyboard hunt.
    function composeInInput(text, caret) {
        if (!els.input) return;
        openCommandFallback('Type the rest, or just speak it.');
        els.input.value = text || '';
        requestAnimationFrame(() => {
            els.input.focus({ preventScroll: true });
            try { els.input.setSelectionRange(caret, caret); } catch (_) {}
        });
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
            // Room-safe resting screen: while idle during the dark hours (after
            // sunset / deep sleep), draw NO orb so the standby screen is genuinely
            // black and doesn't light the room. The canvas is already cleared, so
            // returning here leaves it transparent. The orb returns by day and
            // whenever the panel is active (listening/thinking/responding).
            const resting = document.body.classList.contains('sky-empty') &&
                document.body.classList.contains('sky-ambient-clock');
            if (resting && orbState === 'ambient') {
                const restDim = document.documentElement.getAttribute('data-rest-dim');
                if (restDim === 'deep' || restDim === 'night') return;
            }
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
        if (npPollHandle) clearInterval(npPollHandle);
        clearIdleTimer();
        if (voice) voice.stop();
    });

    document.addEventListener('DOMContentLoaded', init);
})();

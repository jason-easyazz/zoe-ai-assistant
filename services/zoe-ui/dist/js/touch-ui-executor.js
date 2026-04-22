/*
 * Zoe Touch UI Executor
 * Executes backend-issued UI actions with strict allowlist and sends acknowledgements.
 */
(function () {
    const ACTION_TYPES = new Set([
        'navigate',
        'open_panel',
        'focus',
        'fill',
        'submit',
        'create_record',
        'update_record',
        'delete_record',
        'highlight',
        'notify',
        'refresh',
        'click',
        'panel_navigate',
        'panel_navigate_fullscreen',
        'panel_clear',
        'panel_browser_frame',
        'panel_show_fullscreen',
        'panel_announce',
        'panel_request_auth',
        'panel_set_mode',
        'panel_show_smart_home',
        'panel_show_media',
        'panel_show_research_report',
        'panel_open_form',
        'panel_stream_text',
        'panel_dismiss_ambient',
        'show_card',           // Google Home-style intent response card
    ]);

    const state = {
        panelId: null,
        sessionId: null,
        processing: false,
        pollTimer: null,
        syncTimer: null,
        seenActions: new Set(),
        seenAuthChallenges: new Set(),
        pushWs: null,
        autoHomeTimer: null,
        autoHomeArmed: false,
    };
    const AUTH_CHALLENGE_CACHE_KEY = 'zoe_seen_auth_challenges';
    const AUTH_CHALLENGE_TTL_MS = 10 * 60 * 1000;

    const AUTO_HOME_TIMEOUT_S = Number(window.ZOE_AUTO_HOME_TIMEOUT_S || 20);
    const HOME_PATH = '/touch/dashboard.html';

    function isHomePath(path) {
        try {
            const current = String(path || '').split('?')[0];
            const home = String(HOME_PATH || '').split('?')[0];
            return current === home;
        } catch (_) {
            return false;
        }
    }

    function clearAutoHomeTimer(reason) {
        if (state.autoHomeTimer) {
            clearTimeout(state.autoHomeTimer);
            state.autoHomeTimer = null;
        }
    }

    function armAutoHomeTimer(reason) {
        state.autoHomeArmed = true;
        resetAutoHomeTimer(reason || 'voice-nav');
    }

    function disarmAutoHomeTimer(reason) {
        state.autoHomeArmed = false;
        clearAutoHomeTimer(reason || 'disarm');
    }

    function actionIsVoiceTriggered(action, payload) {
        const raw = (
            action?.requested_by
            || action?.source
            || payload?.requested_by
            || payload?.source
            || payload?.origin
            || ''
        );
        const normalized = String(raw).toLowerCase();
        return normalized === 'voice' || normalized.startsWith('voice:') || normalized.startsWith('voice_');
    }

    function getSession() {
        try {
            if (window.zoeAuth && typeof window.zoeAuth.getCurrentSession === 'function') {
                return window.zoeAuth.getCurrentSession();
            }
            const raw = localStorage.getItem('zoe_session');
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function getPanelId() {
        const params = new URLSearchParams(window.location.search);
        const forced = params.get('panel_id');
        if (forced && forced.trim()) {
            localStorage.setItem('zoe_touch_panel_id', forced.trim());
            return forced.trim();
        }
        let panelId = localStorage.getItem('zoe_touch_panel_id');
        if (!panelId) {
            panelId = 'panel_' + Math.random().toString(36).slice(2, 10);
            localStorage.setItem('zoe_touch_panel_id', panelId);
        }
        return panelId;
    }

    function hydrateSeenAuthChallenges() {
        const now = Date.now();
        const next = {};
        try {
            const raw = sessionStorage.getItem(AUTH_CHALLENGE_CACHE_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            if (parsed && typeof parsed === 'object') {
                for (const [challengeId, ts] of Object.entries(parsed)) {
                    const seenAt = Number(ts || 0);
                    if (!challengeId || !Number.isFinite(seenAt)) continue;
                    if ((now - seenAt) <= AUTH_CHALLENGE_TTL_MS) {
                        state.seenAuthChallenges.add(String(challengeId));
                        next[String(challengeId)] = seenAt;
                    }
                }
            }
        } catch (_) {
            // Non-fatal.
        }
        try {
            sessionStorage.setItem(AUTH_CHALLENGE_CACHE_KEY, JSON.stringify(next));
        } catch (_) {
            // Non-fatal.
        }
    }

    function rememberAuthChallenge(challengeId) {
        const id = String(challengeId || '').trim();
        if (!id) return;
        state.seenAuthChallenges.add(id);
        try {
            const raw = sessionStorage.getItem(AUTH_CHALLENGE_CACHE_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            const next = (parsed && typeof parsed === 'object') ? parsed : {};
            next[id] = Date.now();
            sessionStorage.setItem(AUTH_CHALLENGE_CACHE_KEY, JSON.stringify(next));
        } catch (_) {
            // Non-fatal.
        }
    }

    async function api(path, options) {
        const session = getSession();
        const headers = Object.assign({ 'Content-Type': 'application/json' }, options && options.headers ? options.headers : {});
        if (session && session.session_id) {
            headers['X-Session-ID'] = session.session_id;
        }
        return fetch(path, Object.assign({}, options || {}, { headers }));
    }

    function buildContext() {
        return {
            page: location.pathname,
            title: document.title,
            hasModal: !!document.querySelector('[role="dialog"], .modal, .overlay'),
            activeElement: document.activeElement ? document.activeElement.id || document.activeElement.name || document.activeElement.tagName : null,
            timestamp: new Date().toISOString(),
        };
    }

    function resetAutoHomeTimer(reason) {
        if (!state.autoHomeArmed) return;
        if (!AUTO_HOME_TIMEOUT_S || AUTO_HOME_TIMEOUT_S <= 0) return;
        clearAutoHomeTimer(reason || 'reset');
        state.autoHomeTimer = setTimeout(() => {
            try {
                const path = window.location.pathname || '';
                if (path === '/touch/index.html' || path === '/index.html') return;
                if (document.querySelector('[role="dialog"], .modal, #zoePanelPinPad')) return;
                if (isHomePath(path)) {
                    disarmAutoHomeTimer('already-home');
                    return;
                }
                const panelSuffix = state.panelId ? `?panel_id=${encodeURIComponent(state.panelId)}` : '';
                state.autoHomeArmed = false;
                window.location.assign(`${HOME_PATH}${panelSuffix}`);
            } catch (_) {
                // Non-fatal.
            }
        }, AUTO_HOME_TIMEOUT_S * 1000);
    }

    async function bindPanel() {
        await api('/api/ui/panel/bind', {
            method: 'POST',
            body: JSON.stringify({
                panel_id: state.panelId,
                session_id: state.sessionId || null,
                page: location.pathname,
                is_foreground: true,
                ui_context: buildContext(),
            }),
        });
    }

    async function syncState() {
        try {
            await api('/api/ui/state/sync', {
                method: 'POST',
                body: JSON.stringify({
                    panel_id: state.panelId,
                    session_id: state.sessionId || null,
                    page: location.pathname,
                    is_foreground: true,
                    ui_context: buildContext(),
                }),
            });
        } catch (e) {
            // Non-fatal; periodic sync retries.
        }
    }

    function showToast(message) {
        let toast = document.getElementById('zoeTouchActionToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'zoeTouchActionToast';
            toast.style.position = 'fixed';
            toast.style.bottom = '18px';
            toast.style.right = '18px';
            toast.style.zIndex = '9999';
            toast.style.background = 'rgba(20,20,20,0.86)';
            toast.style.color = '#fff';
            toast.style.padding = '10px 12px';
            toast.style.borderRadius = '10px';
            toast.style.fontSize = '13px';
            toast.style.maxWidth = '320px';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.display = 'block';
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(() => {
            toast.style.display = 'none';
        }, 2500);
    }

    function ensurePanelOverlay() {
        let root = document.getElementById('zoePanelOverlay');
        if (!root) {
            root = document.createElement('div');
            root.id = 'zoePanelOverlay';
            root.style.cssText = [
                'display:none', 'position:fixed', 'inset:0', 'z-index:2147483000',
                'background:rgba(0,0,0,0.92)', 'flex-direction:column', 'align-items:center',
                'justify-content:center', 'padding:16px', 'box-sizing:border-box',
            ].join(';');
            document.body.appendChild(root);
        }
        return root;
    }

    function hidePanelOverlay() {
        const root = document.getElementById('zoePanelOverlay');
        if (root) {
            root.style.display = 'none';
            root.innerHTML = '';
        }
    }

    function showPanelFullscreenImage(imageBase64, caption) {
        const root = ensurePanelOverlay();
        root.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.style.cssText = 'max-width:100%;max-height:100%;text-align:center;';
        const img = document.createElement('img');
        img.alt = '';
        img.style.cssText = 'max-width:100%;max-height:85vh;object-fit:contain;border-radius:8px;';
        img.src = 'data:image/png;base64,' + imageBase64.replace(/^data:image\/\w+;base64,/, '');
        wrap.appendChild(img);
        if (caption) {
            const cap = document.createElement('div');
            cap.textContent = caption;
            cap.style.cssText = 'color:#fff;margin-top:12px;font-size:16px;';
            wrap.appendChild(cap);
        }
        const close = document.createElement('button');
        close.type = 'button';
        close.textContent = 'Close';
        close.style.cssText = 'margin-top:16px;padding:12px 24px;font-size:16px;border-radius:10px;border:none;background:#7B61FF;color:#fff;';
        close.onclick = () => hidePanelOverlay();
        wrap.appendChild(close);
        root.appendChild(wrap);
        root.style.display = 'flex';
    }

    // ── Voice Conversation Overlay ─────────────────────────────────────
    // Shows a floating chat bubble near the orb when voice is active.
    const VoiceOverlay = (() => {
        let _el = null;
        let _dismissTimer = null;
        let _isVoiceSession = false;

        function _injectStyles() {
            if (document.getElementById('zvo-styles')) return;
            const s = document.createElement('style');
            s.id = 'zvo-styles';
            s.textContent = `
#zoe-voice-overlay {
    position: fixed;
    bottom: 80px;
    left: 50%;
    width: min(600px, 90vw);
    max-height: 320px;
    background: rgba(12,12,28,0.92);
    border: 1px solid rgba(123,97,255,0.35);
    border-radius: 20px;
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    box-shadow: 0 8px 40px rgba(0,0,0,0.50), 0 0 0 1px rgba(123,97,255,0.15);
    display: none;
    flex-direction: column;
    overflow: hidden;
    z-index: 5000;
    transform: translate(-50%, 12px);
    opacity: 0;
    transition: transform .28s cubic-bezier(.34,1.56,.64,1), opacity .22s ease, left 0s;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
}
#zoe-voice-overlay.zvo-visible {
    display: flex;
    transform: translate(-50%, 0);
    opacity: 1;
}
#zvo-header {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 16px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    flex-shrink: 0;
}
#zvo-orb-dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: #7B61FF;
    box-shadow: 0 0 8px rgba(123,97,255,0.8);
    flex-shrink: 0;
    animation: zvo-pulse 1.4s ease-in-out infinite;
}
#zvo-orb-dot.listening { background: #ff6b6b; box-shadow: 0 0 8px rgba(255,107,107,0.8); }
#zvo-orb-dot.thinking  { background: #ffd166; box-shadow: 0 0 8px rgba(255,209,102,0.8); animation: none; }
#zvo-orb-dot.responding{ background: #06d6a0; box-shadow: 0 0 8px rgba(6,214,160,0.8); }
@keyframes zvo-pulse {
    0%,100%{transform:scale(1);opacity:1}
    50%{transform:scale(1.4);opacity:.7}
}
#zvo-status {
    font-size: 12px; font-weight: 600;
    color: rgba(255,255,255,0.55);
    text-transform: uppercase; letter-spacing: .06em;
    flex: 1;
}
#zvo-close {
    width: 24px; height: 24px; border-radius: 50%;
    border: none; background: rgba(255,255,255,0.08);
    color: rgba(255,255,255,0.45); font-size: 14px;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: background .15s;
    flex-shrink: 0; padding: 0;
}
#zvo-close:hover { background: rgba(255,255,255,0.14); color: rgba(255,255,255,0.8); }
#zvo-messages {
    flex: 1;
    overflow-y: auto;
    padding: 10px 14px 14px;
    display: flex; flex-direction: column; gap: 8px;
    scrollbar-width: none;
}
#zvo-messages::-webkit-scrollbar { display: none; }
.zvo-msg {
    max-width: 88%;
    padding: 8px 12px;
    border-radius: 14px;
    font-size: 14px; line-height: 1.45;
    animation: zvo-msg-in .22s ease;
}
@keyframes zvo-msg-in {
    from { opacity:0; transform: scale(.96) translateY(4px); }
    to   { opacity:1; transform: scale(1) translateY(0); }
}
.zvo-msg.user {
    align-self: flex-end;
    background: rgba(123,97,255,0.25);
    border: 1px solid rgba(123,97,255,0.35);
    color: rgba(255,255,255,0.90);
    border-bottom-right-radius: 4px;
}
.zvo-msg.assistant {
    align-self: flex-start;
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.10);
    color: rgba(255,255,255,0.88);
    border-bottom-left-radius: 4px;
}
.zvo-msg.status {
    align-self: center;
    background: transparent;
    border: none;
    color: rgba(255,255,255,0.38);
    font-size: 12px;
    padding: 2px 4px;
}
.zvo-dots span {
    display: inline-block;
    width: 5px; height: 5px; border-radius: 50%;
    background: currentColor; margin: 0 1.5px;
    animation: zvo-dot .9s ease-in-out infinite;
}
.zvo-dots span:nth-child(2) { animation-delay: .15s; }
.zvo-dots span:nth-child(3) { animation-delay: .30s; }
@keyframes zvo-dot {
    0%,100%{transform:translateY(0);opacity:.4}
    50%{transform:translateY(-4px);opacity:1}
}
/* Light mode */
body.light-mode #zoe-voice-overlay {
    background: rgba(238,241,250,0.94);
    border-color: rgba(123,97,255,0.25);
    box-shadow: 0 8px 40px rgba(0,0,0,0.18), 0 0 0 1px rgba(123,97,255,0.12);
}
body.light-mode #zvo-status { color: rgba(26,26,46,0.52); }
body.light-mode .zvo-msg.user { background: rgba(123,97,255,0.14); border-color: rgba(123,97,255,0.25); color: #1a1a2e; }
body.light-mode .zvo-msg.assistant { background: rgba(255,255,255,0.85); border-color: rgba(0,0,0,0.08); color: #1a1a2e; }
body.light-mode .zvo-msg.status { color: rgba(26,26,46,0.40); }
body.light-mode #zvo-close { background: rgba(0,0,0,0.06); color: rgba(26,26,46,0.45); }
body.light-mode #zvo-header { border-bottom-color: rgba(0,0,0,0.07); }
`;
            document.head.appendChild(s);
        }

        function _build() {
            if (document.getElementById('zoe-voice-overlay')) {
                _el = document.getElementById('zoe-voice-overlay');
                return;
            }
            _injectStyles();
            _el = document.createElement('div');
            _el.id = 'zoe-voice-overlay';
            _el.innerHTML = `
                <div id="zvo-header">
                    <div id="zvo-orb-dot"></div>
                    <span id="zvo-status">Listening…</span>
                    <button id="zvo-close" aria-label="Close" title="Close">✕</button>
                </div>
                <div id="zvo-messages"></div>`;
            document.body.appendChild(_el);
            document.getElementById('zvo-close').addEventListener('click', () => dismiss(true));
            // Trigger visible transition on next frame
        }

        function _setStatus(text, dotClass) {
            const dot = document.getElementById('zvo-orb-dot');
            const statusEl = document.getElementById('zvo-status');
            if (dot) { dot.className = dotClass || ''; }
            if (statusEl) statusEl.textContent = text;
        }

        function show() {
            if (!_el) _build();
            clearTimeout(_dismissTimer);
            _isVoiceSession = true;
            _el.style.display = 'flex';
            // Force reflow then animate in
            requestAnimationFrame(() => requestAnimationFrame(() => _el.classList.add('zvo-visible')));
        }

        function dismiss(manual) {
            if (!_el) return;
            _isVoiceSession = false;
            clearTimeout(_dismissTimer);
            _el.classList.remove('zvo-visible');
            setTimeout(() => { if (_el) _el.style.display = 'none'; }, 300);
        }

        function addMessage(role, text) {
            if (!_el) _build();
            const msgs = document.getElementById('zvo-messages');
            if (!msgs) return null;
            const div = document.createElement('div');
            div.className = `zvo-msg ${role}`;
            div.textContent = text;
            msgs.appendChild(div);
            msgs.scrollTop = msgs.scrollHeight;
            return div;
        }

        function addThinkingDots() {
            if (!_el) _build();
            const msgs = document.getElementById('zvo-messages');
            if (!msgs) return null;
            const div = document.createElement('div');
            div.className = 'zvo-msg assistant zvo-dots';
            div.id = 'zvo-thinking';
            div.innerHTML = '<span></span><span></span><span></span>';
            msgs.appendChild(div);
            msgs.scrollTop = msgs.scrollHeight;
            return div;
        }

        function removeThinkingDots() {
            const el = document.getElementById('zvo-thinking');
            if (el) el.remove();
        }

        function clearMessages() {
            const msgs = document.getElementById('zvo-messages');
            if (msgs) msgs.innerHTML = '';
        }

        // State machine — also dispatches document events so the card overlay (dashboard.html) can react.
        function onListeningStarted() {
            if (!_el) _build();
            // Do NOT clear messages — preserve conversation history across wake words
            _setStatus('Listening…', 'listening');
            addMessage('status', '🎤 Listening…');
            show();
            document.dispatchEvent(new CustomEvent('zoe:voice:wake'));
        }

        function onTranscript(text) {
            const msgs = document.getElementById('zvo-messages');
            if (msgs) {
                const statusMsgs = msgs.querySelectorAll('.zvo-msg.status');
                statusMsgs.forEach(m => m.remove());
            }
            if (text) addMessage('user', text);
        }

        function onThinking() {
            _setStatus('Thinking…', 'thinking');
            removeThinkingDots();
            addThinkingDots();
            document.dispatchEvent(new CustomEvent('zoe:voice:thinking'));
        }

        function onResponding(text) {
            _setStatus('Responding…', 'responding');
            removeThinkingDots();
            if (text) {
                const existing = document.getElementById('zvo-responding');
                if (existing) {
                    existing.textContent = text;
                } else {
                    const div = addMessage('assistant', text);
                    if (div) div.id = 'zvo-responding';
                }
                const msgs = document.getElementById('zvo-messages');
                if (msgs) msgs.scrollTop = msgs.scrollHeight;
            }
            document.dispatchEvent(new CustomEvent('zoe:voice:responding', { detail: { text } }));
        }

        function onDone() {
            _setStatus('Done', '');
            removeThinkingDots();
            clearTimeout(_dismissTimer);
            // No auto-dismiss — user taps ✕ to close, or overlay stays as conversation record
            document.dispatchEvent(new CustomEvent('zoe:voice:done'));
        }

        return { show, dismiss, onListeningStarted, onTranscript, onThinking, onResponding, onDone };
    })();

    // Expose VoiceOverlay globally so dashboard.html inline scripts can access it.
    window.VoiceOverlay = VoiceOverlay;

    function setOrbMode(mode) {
        // Drive the floating orb (orb-loader.js) if present
        const orb = document.getElementById('zoeOrb') || document.querySelector('.zoe-orb');
        if (orb) {
            orb.dataset.zoePanelMode = mode;
            orb.classList.remove('listening', 'thinking', 'responding', 'ambient');
            if (mode !== 'ambient') orb.classList.add(mode);
        }
        // Also drive the nav bar orb dot — visible on every touch page
        const navDot = document.getElementById('ztm-orb-dot');
        if (navDot) {
            navDot.classList.remove('orb-listening', 'orb-thinking', 'orb-responding');
            if (mode !== 'ambient') navDot.classList.add('orb-' + mode);
        }
    }

    // Navigation fallback: parse voice:responding text for navigation intent
    // (until the backend sends proper panel_navigate ui_action events).
    // Path-aware: use /touch/* when the current page is in the touch tree,
    // otherwise use the root desktop paths. This prevents desktop users
    // from being bounced to the touch UI via voice commands.
    function _isTouchContext() {
        try {
            return (window.location.pathname || '').startsWith('/touch/');
        } catch (_) { return false; }
    }
    function _page(name) {
        return (_isTouchContext() ? '/touch/' : '/') + name;
    }
    function _buildPageMap() {
        return {
            'calendar':   _page('calendar.html'),
            'chat':       _page('chat.html'),
            'lists':      _page('lists.html'),
            'notes':      _page('notes.html'),
            'journal':    _page('journal.html'),
            'weather':    _page('weather.html'),
            'music':      _page('music.html'),
            'settings':   _page('settings.html'),
            'smart home': _page('smart-home.html'),
            'smarthome':  _page('smart-home.html'),
            'home':       _page('dashboard.html'),
            'dashboard':  _page('dashboard.html'),
            'people':     _page('people.html'),
            'memories':   _page('memories.html'),
            'cooking':    _page('cooking.html'),
        };
    }

    function _attemptVoiceNavigation(text) {
        if (!text) return;
        const lower = text.toLowerCase();
        // Only navigate if the response sounds like a navigation confirmation
        if (!/navigat|going to|opening|taking you|here.{0,10}(the|your)\s/i.test(lower)) return;
        const map = _buildPageMap();
        for (const [keyword, url] of Object.entries(map)) {
            if (lower.includes(keyword)) {
                setTimeout(() => { window.location.assign(url); }, 1800);
                return;
            }
        }
    }

    function hidePinPad() {
        const el = document.getElementById('zoePanelPinPad');
        if (el) el.remove();
    }

    function redirectToTouchLogin(challenge) {
        const panelId = state.panelId || getPanelId();
        try {
            if (challenge && challenge.challenge_id) {
                sessionStorage.setItem('zoe_panel_auth_challenge', JSON.stringify({
                    challenge_id: challenge.challenge_id,
                    panel_id: panelId,
                    action_context: challenge.action_context || challenge.reason || 'Enter PIN',
                }));
            }
            sessionStorage.setItem('zoe_redirect_after_login', window.location.pathname + window.location.search);
        } catch (_) {
            // Non-fatal.
        }
        const suffix = panelId ? `?panel_id=${encodeURIComponent(panelId)}` : '';
        window.location.assign(`/touch/index.html${suffix}`);
    }

    function showPinPad(challenge) {
        hidePinPad();
        // Inject shake keyframe once.
        if (!document.getElementById('zoePinShakeStyle')) {
            const s = document.createElement('style');
            s.id = 'zoePinShakeStyle';
            s.textContent = '@keyframes zoePinShake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}40%,80%{transform:translateX(8px)}}';
            document.head.appendChild(s);
        }
        const wrap = document.createElement('div');
        wrap.id = 'zoePanelPinPad';
        wrap.style.cssText = [
            'position:fixed', 'inset:0', 'z-index:2147483600', 'background:rgba(0,0,0,0.88)',
            'display:flex', 'flex-direction:column', 'align-items:center', 'justify-content:center',
            'padding:20px', 'box-sizing:border-box',
        ].join(';');
        const title = document.createElement('div');
        title.style.cssText = 'color:#fff;font-size:18px;margin-bottom:12px;text-align:center;max-width:360px;';
        title.textContent = (challenge.action_context && challenge.action_context.kind === 'voice_turn')
            ? 'Voice command needs your identity.\nEnter your PIN to continue.'
            : (challenge.action_context || 'Enter PIN to authorise');
        wrap.appendChild(title);
        const display = document.createElement('div');
        display.style.cssText = 'color:#fff;font-size:28px;letter-spacing:8px;margin-bottom:16px;min-height:36px;';
        display.textContent = '';
        wrap.appendChild(display);
        let selectedUserId = challenge.user_id || null;
        const userHint = document.createElement('div');
        userHint.style.cssText = 'color:#ddd;font-size:13px;margin:4px 0 10px 0;text-align:center;max-width:380px;';
        userHint.textContent = selectedUserId ? `Authenticating as ${selectedUserId}` : 'Select profile, then enter PIN';
        wrap.appendChild(userHint);
        const userRow = document.createElement('div');
        userRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;justify-content:center;max-width:420px;margin-bottom:12px;';
        wrap.appendChild(userRow);
        let buf = '';
        function renderUserButtons(profiles, defaultUserId) {
            if (!Array.isArray(profiles) || !profiles.length) return;
            if (!selectedUserId) selectedUserId = defaultUserId || (profiles[0] && profiles[0].user_id) || null;
            userHint.textContent = selectedUserId ? `Authenticating as ${selectedUserId}` : 'Select profile, then enter PIN';
            userRow.innerHTML = '';
            profiles.forEach((p) => {
                const uid = String(p.user_id || '').trim();
                if (!uid) return;
                const b = document.createElement('button');
                b.type = 'button';
                b.textContent = p.display_name || uid;
                const active = uid === selectedUserId;
                b.style.cssText = [
                    'padding:8px 12px',
                    'border-radius:999px',
                    'border:none',
                    `background:${active ? '#7B61FF' : '#3a3a3a'}`,
                    'color:#fff',
                    'font-size:13px',
                    'cursor:pointer',
                ].join(';');
                b.onclick = () => {
                    selectedUserId = uid;
                    userHint.textContent = `Authenticating as ${selectedUserId}`;
                    renderUserButtons(profiles, defaultUserId);
                };
                userRow.appendChild(b);
            });
        }
        (async () => {
            try {
                const q = state.panelId ? `?panel_id=${encodeURIComponent(state.panelId)}` : '';
                const r = await api(`/api/auth/profiles${q}`, { method: 'GET' });
                if (!r.ok) return;
                const data = await r.json();
                if (Array.isArray(data)) {
                    renderUserButtons(data, null);
                    return;
                }
                renderUserButtons(data && data.profiles, data && data.default_user_id);
            } catch (_) {
                // Non-fatal: PIN can still be submitted without explicit user selection.
            }
        })();
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,72px);gap:10px;';
        function addDigit(d) {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = d;
            b.style.cssText = 'height:56px;font-size:20px;border-radius:12px;border:none;background:#333;color:#fff;';
            b.onclick = () => {
                if (buf.length >= 8) return;
                buf += d;
                display.textContent = '•'.repeat(buf.length);
            };
            grid.appendChild(b);
        }
        for (let i = 1; i <= 9; i++) addDigit(String(i));
        const zero = document.createElement('button');
        zero.type = 'button';
        zero.textContent = '0';
        zero.style.cssText = 'grid-column:2;height:56px;font-size:20px;border-radius:12px;border:none;background:#333;color:#fff;';
        zero.onclick = () => {
            if (buf.length >= 8) return;
            buf += '0';
            display.textContent = '•'.repeat(buf.length);
        };
        grid.appendChild(zero);
        wrap.appendChild(grid);
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:12px;margin-top:16px;';
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.textContent = 'Clear';
        clear.style.cssText = 'padding:12px 20px;border-radius:10px;border:none;background:#555;color:#fff;';
        clear.onclick = () => { buf = ''; display.textContent = ''; };
        const go = document.createElement('button');
        go.type = 'button';
        go.textContent = 'OK';
        go.style.cssText = 'padding:12px 28px;border-radius:10px;border:none;background:#7B61FF;color:#fff;';
        go.onclick = async () => {
            if (!buf) return;
            try {
                const r = await api('/api/panels/auth/pin', {
                    method: 'POST',
                    body: JSON.stringify({
                        challenge_id: challenge.challenge_id,
                        pin: buf,
                        ...((selectedUserId || challenge.user_id) ? { user_id: (selectedUserId || challenge.user_id) } : {}),
                    }),
                });
                if (!r.ok) {
                    display.style.animation = 'zoePinShake 0.4s ease';
                    setTimeout(() => { display.style.animation = ''; }, 400);
                    showToast('Incorrect PIN');
                    buf = '';
                    display.textContent = '';
                    return;
                }
                showToast('Authorised');
                hidePinPad();
            } catch (e) {
                showToast('PIN request failed');
            }
        };
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.textContent = 'Cancel';
        cancel.style.cssText = 'padding:12px 20px;border-radius:10px;border:none;background:#444;color:#fff;';
        cancel.onclick = () => hidePinPad();
        const fullLogin = document.createElement('button');
        fullLogin.type = 'button';
        fullLogin.textContent = 'Use login page';
        fullLogin.style.cssText = 'padding:12px 16px;border-radius:10px;border:none;background:#2f2f2f;color:#fff;';
        fullLogin.onclick = () => {
            try {
                sessionStorage.setItem('zoe_redirect_after_login', window.location.pathname + window.location.search);
            } catch (_) {
                // ignore
            }
            const pid = state.panelId ? `?panel_id=${encodeURIComponent(state.panelId)}` : '';
            window.location.assign(`/touch/index.html${pid}`);
        };
        row.appendChild(clear);
        row.appendChild(go);
        row.appendChild(fullLogin);
        row.appendChild(cancel);
        wrap.appendChild(row);
        document.body.appendChild(wrap);
    }

    function connectPushWebSocket() {
        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${window.location.host}/ws/push?channel=all`;
            const ws = new WebSocket(url);
            state.pushWs = ws;
            ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'panel_pin_request' && msg.data) {
                        const d = (msg.data && msg.data.data) ? msg.data.data : msg.data;
                        const actionId = String((d && d.challenge_id) || '').trim() || ('push_pin_' + Date.now());
                        if (!d.panel_id || d.panel_id === state.panelId) {
                            executeAction({
                                id: `push_pin_${actionId}`,
                                action_type: 'panel_request_auth',
                                payload: {
                                    challenge_id: d.challenge_id,
                                    panel_id: d.panel_id,
                                    action_context: d.action_context || d.reason || 'Enter PIN',
                                },
                            }).then((result) => {
                                if (result && result.status && !String(result.status).startsWith('failed')) return;
                                redirectToTouchLogin(d);
                            }).catch(() => {
                                redirectToTouchLogin(d);
                            });
                        }
                    }
                    if (msg.type === 'panel_pin_result' && msg.data) {
                        const d = msg.data;
                        if (d.panel_id && d.panel_id === state.panelId) {
                            hidePinPad();
                            showToast(d.status === 'approved' ? 'Authorised' : 'Not authorised');
                        }
                    }
                    // Back-compat bridge: panel_state events from either legacy daemon or HA ingress.
                    if (msg.type === 'panel_state' && msg.data) {
                        const d = msg.data;
                        if (!d.panel_id || d.panel_id === state.panelId) {
                            if (d.state === 'listening') {
                                setOrbMode('listening');
                                VoiceOverlay.onListeningStarted();
                            }
                        }
                    }
                    // Voice state machine — drive orb + conversation overlay from backend events.
                    // Panel-ID filter: only react to voice events from our own panel (or global ones without panel_id).
                    if (msg.type && msg.type.startsWith('voice:')) {
                        const _vPanel = msg.data && msg.data.panel_id;
                        if (_vPanel && _vPanel !== state.panelId) {
                            // Event belongs to a different panel — ignore entirely.
                        } else {
                            if (msg.type === 'voice:listening_started') {
                                setOrbMode('listening');
                                if (typeof zoeAmbient !== 'undefined' && zoeAmbient.exit) zoeAmbient.exit();
                                document.dispatchEvent(new CustomEvent('zoe:voice:wake'));
                                VoiceOverlay.onListeningStarted();
                            }
                            if (msg.type === 'voice:transcript') {
                                const _tText = (msg.data && msg.data.text) ? msg.data.text : '';
                                VoiceOverlay.onTranscript(_tText);
                                if (_tText) document.dispatchEvent(new CustomEvent('zoe:voice:transcript', { detail: { text: _tText } }));
                            }
                            if (msg.type === 'voice:thinking') {
                                setOrbMode('thinking');
                                VoiceOverlay.onThinking();
                            }
                            if (msg.type === 'voice:responding') {
                                setOrbMode('responding');
                                const text = (msg.data && msg.data.text) ? msg.data.text : '';
                                VoiceOverlay.onResponding(text);
                                if (text && typeof showAmbientStatus === 'function') {
                                    showAmbientStatus(text);
                                }
                                _attemptVoiceNavigation(text);
                            }
                            if (msg.type === 'voice:done') {
                                setOrbMode('ambient');
                                VoiceOverlay.onDone();
                            }
                        }
                    }
                    // Instant UI action delivery.
                    if (msg.type === 'ui_action' && msg.data) {
                        const action = msg.data.action || msg.data;
                        if (action && action.action_type) {
                            if (!action.id) action.id = 'push_' + Date.now();
                            executeAction(action).then(r => { if (action.id && !action.id.startsWith('push_')) ackAction(action.id, r); }).catch(() => {});
                        }
                    }
                } catch (e) {
                    /* ignore */
                }
            };
            ws.onopen = () => {
                try { ws.send('ping'); } catch (e) { /* ignore */ }
            };
        } catch (e) {
            console.warn('Zoe touch: WebSocket push unavailable', e);
        }
    }

    function resolveSelector(selector) {
        if (!selector || typeof selector !== 'string') return null;
        try {
            return document.querySelector(selector);
        } catch (e) {
            return null;
        }
    }

    async function executeAction(action) {
        const actionType = action.action_type;
        const payload = action.payload || {};
        resetAutoHomeTimer(`action:${actionType}`);

        if (!ACTION_TYPES.has(actionType)) {
            return { status: 'blocked', error_code: 'unsupported_action', error_message: `Unsupported action: ${actionType}` };
        }

        try {
            if (actionType === 'navigate') {
                const page = payload.page || payload.path || payload.url;
                if (!page) return { status: 'failed', error_code: 'missing_page', error_message: 'Missing page/path for navigate' };
                if (actionIsVoiceTriggered(action, payload)) armAutoHomeTimer('voice:navigate');
                else disarmAutoHomeTimer('non-voice:navigate');
                showToast(`Navigating to ${page}`);
                setTimeout(() => { window.location.href = page; }, 150);
                return { status: 'success' };
            }

            if (actionType === 'notify') {
                showToast(payload.message || payload.title || 'Action completed');
                return { status: 'success' };
            }

            if (actionType === 'refresh') {
                showToast('Refreshing data');
                setTimeout(() => window.location.reload(), 120);
                return { status: 'success' };
            }

            if (actionType === 'focus') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Focus selector not found' };
                el.focus();
                return { status: 'success' };
            }

            if (actionType === 'fill') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Fill selector not found' };
                el.value = payload.value != null ? String(payload.value) : '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return { status: 'success' };
            }

            if (actionType === 'click' || actionType === 'submit') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Click selector not found' };
                el.click();
                return { status: 'success' };
            }

            if (actionType === 'panel_navigate' || actionType === 'panel_navigate_fullscreen') {
                if (payload.panel_id && payload.panel_id !== state.panelId) {
                    return { status: 'skipped', error_code: 'wrong_panel' };
                }
                let url = payload.url || payload.page || payload.path;
                if (!url) return { status: 'failed', error_code: 'missing_url', error_message: 'Missing url for panel_navigate' };
                // Preserve kiosk and panel_id query params across navigation.
                const cur = new URLSearchParams(window.location.search);
                const dest = new URL(url, window.location.origin);
                if (cur.has('kiosk') && !dest.searchParams.has('kiosk')) dest.searchParams.set('kiosk', cur.get('kiosk'));
                if (cur.has('panel_id') && !dest.searchParams.has('panel_id')) dest.searchParams.set('panel_id', cur.get('panel_id'));
                url = dest.pathname + dest.search;
                const current = new URL(window.location.href);
                const samePath = current.pathname === dest.pathname;
                const sameQuery = current.searchParams.toString() === dest.searchParams.toString();
                // No-op when already on the target page to avoid unnecessary reload flicker.
                if (samePath && sameQuery) {
                    return { status: 'skipped', error_code: 'already_on_page' };
                }
                if (actionIsVoiceTriggered(action, payload)) armAutoHomeTimer(`voice:${actionType}`);
                else disarmAutoHomeTimer(`non-voice:${actionType}`);
                const label = payload.label || url;
                showToast(String(label).slice(0, 120));
                setTimeout(() => { window.location.assign(url); }, 200);
                return { status: 'success' };
            }

            if (actionType === 'panel_clear') {
                hidePanelOverlay();
                setOrbMode('ambient');
                if (payload.return_url) {
                    setTimeout(() => { window.location.assign(payload.return_url); }, 150);
                } else {
                    showToast('Panel cleared');
                }
                return { status: 'success' };
            }

            if (actionType === 'panel_show_fullscreen') {
                const b64 = payload.image_base64;
                if (!b64) return { status: 'failed', error_code: 'missing_image', error_message: 'Missing image_base64' };
                showPanelFullscreenImage(String(b64), payload.caption || '');
                return { status: 'success' };
            }

            if (actionType === 'panel_browser_frame') {
                const url = payload.url;
                if (!url) return { status: 'failed', error_code: 'missing_url', error_message: 'Missing url for browser frame' };
                const root = ensurePanelOverlay();
                root.innerHTML = '';
                const frame = document.createElement('iframe');
                frame.title = 'Zoe browser';
                frame.style.cssText = 'width:96vw;height:88vh;border:none;border-radius:8px;background:#fff;';
                frame.src = url;
                const bar = document.createElement('div');
                bar.style.cssText = 'display:flex;justify-content:flex-end;padding:8px;width:100%;';
                const close = document.createElement('button');
                close.type = 'button';
                close.textContent = 'Close';
                close.style.cssText = 'padding:10px 18px;border-radius:10px;border:none;background:#7B61FF;color:#fff;';
                close.onclick = () => hidePanelOverlay();
                bar.appendChild(close);
                root.appendChild(bar);
                root.appendChild(frame);
                root.style.display = 'flex';
                root.style.flexDirection = 'column';
                return { status: 'success' };
            }

            if (actionType === 'panel_announce') {
                const text = payload.message || payload.text;
                if (!text) return { status: 'failed', error_code: 'missing_message', error_message: 'Missing message for announce' };
                // Always show visual toast — TTS audio is a bonus, not required for success.
                showToast(String(text).slice(0, 160));
                if (payload.mode) setOrbMode(payload.mode === 'listening' ? 'listening' : 'ambient');
                // Attempt TTS audio asynchronously — failure is non-fatal.
                (async () => {
                    try {
                        const r = await api('/api/voice/speak', {
                            method: 'POST',
                            body: JSON.stringify({ text: String(text).slice(0, 1200) }),
                        });
                        if (!r.ok) return;
                        const j = await r.json();
                        const b64 = j.audio_base64;
                        if (!b64) return;
                        const ct = j.content_type || 'audio/wav';
                        const bin = atob(b64);
                        const bytes = new Uint8Array(bin.length);
                        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                        const blob = new Blob([bytes], { type: ct });
                        const audio = new Audio(URL.createObjectURL(blob));
                        audio.play().catch(() => {});
                    } catch (_) { /* TTS optional */ }
                })();
                return { status: 'success' };
            }

            if (actionType === 'panel_set_mode') {
                const mode = (payload.mode || 'ambient').toLowerCase();
                const validModes = ['listening', 'thinking', 'responding', 'ambient'];
                const safeMode = validModes.includes(mode) ? mode : 'ambient';
                setOrbMode(safeMode);
                const toastMap = { listening: 'Listening…', thinking: 'Thinking…', responding: 'Responding…', ambient: 'Ready' };
                showToast(toastMap[safeMode] || 'Ready');
                return { status: 'success' };
            }

            if (actionType === 'panel_request_auth') {
                if (payload.challenge_id) {
                    disarmAutoHomeTimer('auth-required');
                    if (payload.panel_id && payload.panel_id !== state.panelId) {
                        return { status: 'skipped', error_code: 'wrong_panel' };
                    }
                    const challengeId = String(payload.challenge_id);
                    if (state.seenAuthChallenges.has(challengeId)) {
                        return { status: 'skipped', error_code: 'duplicate_challenge' };
                    }
                    rememberAuthChallenge(challengeId);
                    redirectToTouchLogin({
                        challenge_id: challengeId,
                        action_context: payload.action_context || payload.reason || 'Enter PIN'
                    });
                    return { status: 'success' };
                }
                return { status: 'blocked', error_code: 'no_challenge', error_message: 'panel_request_auth needs challenge_id from server' };
            }

            // ── Smart home overlay ─────────────────────────────────────────
            if (actionType === 'panel_show_smart_home') {
                showSmartHomeOverlay(payload);
                return { status: 'success' };
            }

            // ── Media now-playing overlay ──────────────────────────────────
            if (actionType === 'panel_show_media') {
                showMediaOverlay(payload);
                return { status: 'success' };
            }

            if (actionType === 'panel_show_research_report') {
                showResearchReportOverlay(payload.package || {});
                return { status: 'success' };
            }

            // ── Dismiss ambient/screensaver ────────────────────────────────
            if (actionType === 'panel_dismiss_ambient') {
                if (typeof zoeAmbient !== 'undefined' && zoeAmbient.exit) zoeAmbient.exit();
                document.getElementById('zoe-ambient-overlay')?.classList.remove('active');
                return { status: 'success' };
            }

            // ── Google Home-style response card ────────────────────────────
            if (actionType === 'show_card') {
                // Ignore cards targeting a different panel.
                if (payload.panel_id && payload.panel_id !== state.panelId) {
                    return { status: 'skipped', error_code: 'wrong_panel' };
                }
                const cardType = payload.type || 'answer';
                const cardData = payload.data || {};
                const cardActions = payload.actions || [];
                if (window.ZoeVoiceCard && typeof window.ZoeVoiceCard.showCard === 'function') {
                    const html = window.ZoeVoiceCard.renderCard(cardType, cardData);
                    window.ZoeVoiceCard.showCard(html, cardActions);
                    window.ZoeVoiceCard.showBar('');
                } else {
                    showToast((cardData.text || cardData.title || 'Zoe').slice(0, 80));
                }
                return { status: 'success' };
            }

            // ── Open a creation form on the current page ───────────────────
            if (actionType === 'panel_open_form') {
                // Ignore form actions targeting a different panel.
                if (payload.panel_id && payload.panel_id !== state.panelId) {
                    return { status: 'skipped', error_code: 'wrong_panel' };
                }
                const form = payload.form || '';
                const prefill = payload.prefill || {};
                // Dismiss screensaver if active.
                if (typeof zoeAmbient !== 'undefined' && zoeAmbient.exit) zoeAmbient.exit();
                // Delegate to the page's form hook if registered.
                if (window.ZOE_OPEN_FORM && typeof window.ZOE_OPEN_FORM === 'function') {
                    window.ZOE_OPEN_FORM(form, prefill);
                    return { status: 'success' };
                }
                return { status: 'blocked', error_code: 'no_form_hook', error_message: 'Page does not expose ZOE_OPEN_FORM' };
            }

            // ── Stream text into a form element (typewriter effect) ────────
            if (actionType === 'panel_stream_text') {
                const selector = payload.selector;
                const text = String(payload.text || '');
                const append = payload.append !== false;
                const delay = Number(payload.char_delay_ms || 18);
                const el = resolveSelector(selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: `panel_stream_text: ${selector} not found` };
                el.focus();
                let base = append ? (el.value || el.textContent || '') : '';
                let i = 0;
                function typeNext() {
                    if (i >= text.length) {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return;
                    }
                    if ('value' in el) {
                        el.value = base + text.slice(0, i + 1);
                    } else {
                        el.textContent = base + text.slice(0, i + 1);
                    }
                    el.scrollTop = el.scrollHeight;
                    i++;
                    setTimeout(typeNext, delay);
                }
                typeNext();
                return { status: 'success' };
            }

            if (actionType === 'highlight' || actionType === 'open_panel' || actionType === 'create_record' || actionType === 'update_record' || actionType === 'delete_record') {
                // Deterministic/no-risk local behavior only. Unknown commands are blocked.
                if (actionType === 'highlight' && payload.selector) {
                    const el = resolveSelector(payload.selector);
                    if (el) {
                        const prev = el.style.outline;
                        el.style.outline = '2px solid #7B61FF';
                        setTimeout(() => { el.style.outline = prev; }, 1800);
                        return { status: 'success' };
                    }
                }
                return {
                    status: 'blocked',
                    error_code: 'manual_required',
                    error_message: `${actionType} requires module-specific executor mapping`,
                };
            }

            return { status: 'blocked', error_code: 'not_implemented', error_message: `${actionType} not implemented` };
        } catch (e) {
            return { status: 'failed', error_code: 'execution_error', error_message: String(e) };
        }
    }

    async function ackAction(actionId, result) {
        await api(`/api/ui/actions/${actionId}/ack`, {
            method: 'POST',
            body: JSON.stringify({
                status: result.status,
                error_code: result.error_code || null,
                error_message: result.error_message || null,
                ui_context: buildContext(),
            }),
        });
    }

    async function pollActions() {
        if (state.processing) return;
        state.processing = true;
        try {
            const res = await api(`/api/ui/actions/pending?panel_id=${encodeURIComponent(state.panelId)}&limit=10`);
            if (!res.ok) return;
            const data = await res.json();
            const actions = Array.isArray(data.actions) ? data.actions : [];
            for (const action of actions) {
                if (!action || !action.id) continue;
                const attemptKey = `${action.id}:${Number(action.retry_count || 0)}`;
                if (state.seenActions.has(attemptKey)) continue;
                state.seenActions.add(attemptKey);
                const result = await executeAction(action);
                await ackAction(action.id, result);
            }
        } catch (e) {
            // polling retries on next interval
        } finally {
            state.processing = false;
        }
    }

    // Register with the Service Worker so it can drive panel navigation even
    // after this page navigates away (SW persists across all page transitions).
    function registerWithServiceWorker() {
        if (!('serviceWorker' in navigator)) return;
        navigator.serviceWorker.ready.then((reg) => {
            if (reg.active) {
                reg.active.postMessage({
                    type: 'START_PANEL_POLL',
                    panelId: state.panelId,
                    sessionId: state.sessionId,
                });
            }
        }).catch(() => {});

        // Listen for SW-forwarded actions (non-navigation actions that the SW can't handle)
        navigator.serviceWorker.addEventListener('message', (event) => {
            const msg = event.data;
            if (msg && msg.type === 'SW_PANEL_ACTION' && msg.action) {
                const action = msg.action;
                const actionId = action.id || action.action_id;
                if (actionId) {
                    const key = `${actionId}:${Number(action.retry_count || 0)}`;
                    if (state.seenActions.has(key)) {
                        return;
                    }
                    state.seenActions.add(key);
                }
                executeAction(action).then((result) => {
                    if (actionId) ackAction(actionId, result);
                }).catch(() => {});
            }
            // SW requests a hard reload
            if (msg && msg.type === 'SW_RELOAD') {
                window.location.reload();
            }
        });
    }

    // ── Smart Home Overlay ────────────────────────────────────────────────────
    function showSmartHomeOverlay(payload) {
        document.getElementById('zoe-sh-overlay')?.remove();
        const entities = payload.entities || [];
        const dismissMs = (payload.dismiss_after || 30) * 1000;

        const overlay = document.createElement('div');
        overlay.id = 'zoe-sh-overlay';
        overlay.style.cssText = `
            position:fixed;inset:0;z-index:8000;background:rgba(10,10,26,0.95);
            display:flex;flex-direction:column;align-items:center;justify-content:center;
            padding:32px;gap:16px;font-family:inherit;
        `;

        const title = document.createElement('h2');
        title.textContent = payload.title || 'Smart Home';
        title.style.cssText = 'color:#fff;font-weight:300;font-size:28px;margin:0 0 8px;';
        overlay.appendChild(title);

        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;max-width:680px;width:100%;';

        (entities.length ? entities : []).forEach(ent => {
            const btn = document.createElement('button');
            const isOn = (ent.state || '').toLowerCase() === 'on';
            btn.style.cssText = `
                background:${isOn ? 'rgba(103,126,234,0.3)' : 'rgba(255,255,255,0.07)'};
                border:1px solid ${isOn ? 'rgba(103,126,234,0.6)' : 'rgba(255,255,255,0.15)'};
                color:#fff;border-radius:12px;padding:16px 12px;cursor:pointer;
                font-size:14px;text-align:center;transition:all 0.2s;
            `;
            btn.innerHTML = `<div style="font-size:24px;margin-bottom:6px">${ent.icon || (isOn ? '💡' : '⚫')}</div><div>${ent.name || ent.entity_id}</div>`;
            btn.onclick = async () => {
                try {
                    await fetch('/api/ha/control', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ entity_id: ent.entity_id, action: 'toggle' }),
                    });
                    btn.style.background = isOn ? 'rgba(255,255,255,0.07)' : 'rgba(103,126,234,0.3)';
                } catch (_) { showToast('Control failed'); }
            };
            grid.appendChild(btn);
        });

        if (!entities.length) {
            const msg = document.createElement('p');
            msg.textContent = 'No entities available';
            msg.style.color = 'rgba(255,255,255,0.5)';
            grid.appendChild(msg);
        }
        overlay.appendChild(grid);

        const close = document.createElement('button');
        close.textContent = 'Close';
        close.style.cssText = 'margin-top:16px;padding:10px 32px;border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;border-radius:8px;cursor:pointer;font-size:15px;';
        close.onclick = () => overlay.remove();
        overlay.appendChild(close);

        document.body.appendChild(overlay);
        if (dismissMs > 0) setTimeout(() => overlay.remove(), dismissMs);
    }

    // ── Media Now-Playing Overlay ─────────────────────────────────────────────
    function showMediaOverlay(payload) {
        document.getElementById('zoe-media-overlay')?.remove();
        const dismissMs = (payload.dismiss_after || 20) * 1000;

        const overlay = document.createElement('div');
        overlay.id = 'zoe-media-overlay';
        overlay.style.cssText = `
            position:fixed;inset:0;z-index:8000;
            background:rgba(10,10,26,0.95);
            display:flex;flex-direction:column;align-items:center;justify-content:center;
            padding:40px;gap:20px;font-family:inherit;
        `;

        if (payload.album_art) {
            const img = document.createElement('img');
            img.src = payload.album_art;
            img.style.cssText = 'width:180px;height:180px;border-radius:12px;object-fit:cover;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
            overlay.appendChild(img);
        }

        const title = document.createElement('div');
        title.textContent = payload.title || 'Now Playing';
        title.style.cssText = 'color:#fff;font-size:24px;font-weight:300;text-align:center;';
        overlay.appendChild(title);

        if (payload.artist) {
            const artist = document.createElement('div');
            artist.textContent = payload.artist;
            artist.style.cssText = 'color:rgba(255,255,255,0.6);font-size:16px;';
            overlay.appendChild(artist);
        }

        if (payload.entity_id) {
            const controls = document.createElement('div');
            controls.style.cssText = 'display:flex;gap:16px;margin-top:8px;';
            [['⏮', 'previous'], ['⏯', 'toggle'], ['⏭', 'next']].forEach(([icon, action]) => {
                const btn = document.createElement('button');
                btn.textContent = icon;
                btn.style.cssText = 'font-size:28px;background:transparent;border:none;color:#fff;cursor:pointer;padding:8px;';
                btn.onclick = () => fetch('/api/ha/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entity_id: payload.entity_id, action }),
                }).catch(() => {});
                controls.appendChild(btn);
            });
            overlay.appendChild(controls);
        }

        const close = document.createElement('button');
        close.textContent = 'Close';
        close.style.cssText = 'margin-top:8px;padding:8px 28px;border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;border-radius:8px;cursor:pointer;font-size:14px;';
        close.onclick = () => overlay.remove();
        overlay.appendChild(close);

        document.body.appendChild(overlay);
        if (dismissMs > 0) setTimeout(() => overlay.remove(), dismissMs);
    }

    // ── Research Evidence Overlay ─────────────────────────────────────────────
    function showResearchReportOverlay(pkg) {
        document.getElementById('zoe-research-overlay')?.remove();
        const overlay = document.createElement('div');
        overlay.id = 'zoe-research-overlay';
        overlay.style.cssText = `
            position:fixed;inset:0;z-index:8100;background:rgba(10,10,26,0.96);
            color:#fff;overflow:auto;padding:24px;font-family:inherit;
        `;
        const rows = Array.isArray(pkg.results) ? pkg.results : [];
        const shots = Array.isArray(pkg.screenshots) ? pkg.screenshots : [];
        const sources = Array.isArray(pkg.sources) ? pkg.sources : [];
        const top = rows[0] || {};
        const shotHtml = shots.map((s) => {
            if (s.image_base64) {
                return `<img style="max-width:100%;border-radius:10px;margin:10px 0;" src="data:image/png;base64,${String(s.image_base64).replace(/^data:image\/\w+;base64,/, '')}" alt="">`;
            }
            return '';
        }).join('');
        const rowHtml = rows.map((r) => `
            <tr>
                <td style="padding:8px;">${r.rank ?? ''}</td>
                <td style="padding:8px;">${(r.name || '').toString()}</td>
                <td style="padding:8px;">${(r.value || '').toString()}</td>
                <td style="padding:8px;">${(r.location || '').toString()}</td>
            </tr>
        `).join('');
        const sourceHtml = sources.slice(0, 6).map((u) => `<li style="margin:6px 0;word-break:break-all;">${u}</li>`).join('');
        overlay.innerHTML = `
            <div style="max-width:920px;margin:0 auto;">
                <h2 style="font-size:30px;font-weight:400;margin:0 0 10px;">Research Results</h2>
                <div style="font-size:17px;opacity:.9;margin-bottom:14px;">${pkg.query || ''}</div>
                <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);border-radius:12px;padding:14px;margin-bottom:14px;">
                    <div style="font-size:18px;">Top Pick: <strong>${top.name || 'N/A'}</strong> ${(top.value || '')}</div>
                </div>
                <table style="width:100%;border-collapse:collapse;background:rgba(255,255,255,0.05);border-radius:12px;overflow:hidden;">
                    <thead><tr><th style="text-align:left;padding:8px;">#</th><th style="text-align:left;padding:8px;">Option</th><th style="text-align:left;padding:8px;">Value</th><th style="text-align:left;padding:8px;">Location</th></tr></thead>
                    <tbody>${rowHtml || '<tr><td colspan="4" style="padding:8px;">No structured options available.</td></tr>'}</tbody>
                </table>
                <div style="margin-top:14px;">${shotHtml}</div>
                <h3 style="font-size:22px;font-weight:400;margin:16px 0 8px;">Sources</h3>
                <ul style="padding-left:20px;">${sourceHtml || '<li>No sources captured.</li>'}</ul>
                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">
                    <button id="zoe-research-close" style="padding:12px 20px;border:none;border-radius:10px;background:#7B61FF;color:#fff;">Close</button>
                    <button id="zoe-research-save" style="padding:12px 20px;border:none;border-radius:10px;background:#2d8f6f;color:#fff;">Save</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector('#zoe-research-close')?.addEventListener('click', () => overlay.remove());
        overlay.querySelector('#zoe-research-save')?.addEventListener('click', () => showToast('Saved to Zoe'));
    }

    function init() {
        state.panelId = getPanelId();
        const session = getSession();
        state.sessionId = session && session.session_id ? session.session_id : null;
        hydrateSeenAuthChallenges();

        // Export key functions globally so websocket-sync.js can call them
        // for instant action delivery and voice state transitions.
        window._zoeExecuteAction = (action) => {
            if (!action) return;
            const actionId = action.id || action.action_id;
            if (!actionId) return;
            const normalized = action.id ? action : { ...action, id: actionId };
            const key = `${normalized.id}:${Number(normalized.retry_count || 0)}`;
            if (state.seenActions.has(key)) return;
            state.seenActions.add(key);
            executeAction(normalized).then((result) => ackAction(normalized.id, result)).catch(() => {});
        };
        window._zoeSetOrbMode = setOrbMode;
        window._zoeResetAutoHomeTimer = resetAutoHomeTimer;

        bindPanel().catch(() => {});
        syncState().catch(() => {});
        // Prefer the shared push channel from websocket-sync.js to avoid duplicate
        // websocket connections and duplicate action handling.
        if (window.ZoeWebSockets && typeof window.ZoeWebSockets.initPush === 'function') {
            window.ZoeWebSockets.initPush(state.panelId, state.sessionId);
        } else {
            connectPushWebSocket();
        }
        registerWithServiceWorker();

        state.pollTimer = setInterval(pollActions, 2000);
        state.syncTimer = setInterval(syncState, 5000);
        ['pointerdown', 'touchstart', 'keydown'].forEach((evt) => {
            window.addEventListener(evt, () => resetAutoHomeTimer(`user:${evt}`), { passive: true });
        });
        window.addEventListener('beforeunload', () => {
            if (state.pollTimer) clearInterval(state.pollTimer);
            if (state.syncTimer) clearInterval(state.syncTimer);
            clearAutoHomeTimer('beforeunload');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

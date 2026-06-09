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
        renderHome();
        loadBackendStatus();
        setMode(mode);
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
        els.input = document.getElementById('skyCommandInput');
        els.mic = document.getElementById('skyMicBtn');
        els.home = document.getElementById('skyHomeBtn');
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
        els.mic.addEventListener('click', () => {
            if (!voice) return;
            if (voice.isRecording) {
                voice.stopRecording();
            } else if (voice.speaking || voice.serverBusy) {
                voice.cancel().catch(err => showError(err.message || 'Cancel failed'));
            } else {
                voice.startRecording().catch(err => showError(err.message || 'Microphone unavailable'));
            }
        });
        els.home.addEventListener('click', renderHome);
        els.cards.addEventListener('click', event => {
            const btn = event.target.closest('button[data-sky-action]');
            if (!btn) return;
            const route = btn.dataset.route;
            const query = btn.dataset.query;
            if (route && route.startsWith('/')) {
                location.href = route;
            } else if (route) {
                showError('Unsupported card route');
            } else if (query) {
                submitCommand(query);
            }
        });
    }

    function setMode(nextMode) {
        mode = nextMode || 'local';
        localStorage.setItem('skybridge_voice_mode', mode);
        document.querySelectorAll('[data-mode]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        if (voice) voice.stop();
        voice = new window.SkybridgeVoice({ mode, onEvent: handleVoiceEvent });
        voice.start().catch(err => showError(err.message || 'Voice failed to start'));
        setStatus('Connecting ' + mode);
    }

    function handleVoiceEvent(event) {
        if (event.type === 'ready') {
            setStatus('Ready on ' + event.mode);
        } else if (event.type === 'state') {
            setState(event.state || 'ambient');
        } else if (event.type === 'transcript') {
            addTranscript(event.role, event.text);
            if (event.role === 'user') projectCards(event.text);
        } else if (event.type === 'card') {
            addCard(event.card, true);
        } else if (event.type === 'error') {
            showError(event.message);
        } else if (event.type === 'done') {
            setState('ambient');
        }
    }

    function submitCommand(text) {
        const query = String(text || '').trim();
        if (!query) return;
        currentUtterance = 'Heard: ' + query;
        els.copy.textContent = currentUtterance;
        projectCards(query);
        if (voice) voice.sendText(query);
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

    function renderHome() {
        document.body.classList.add('sky-empty');
        currentUtterance = '';
        setContext('Listening', 'The surface will build itself when Zoe understands what you need.');
        clearCards();
        els.copy.textContent = 'Listening. Ask Zoe for anything.';
        requestAnimationFrame(resizeOrb);
    }

    function clearCards() {
        cardSequence = 0;
        els.cards.innerHTML = '';
        document.body.classList.add('sky-empty');
        requestAnimationFrame(resizeOrb);
    }

    function addCard(card, prepend, delayMs) {
        document.body.classList.remove('sky-empty');
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
        while (els.cards.children.length > 8) {
            els.cards.removeChild(els.cards.lastElementChild);
        }
    }

    function setContext(title, detail) {
        els.contextTitle.textContent = title;
        els.contextDetail.textContent = detail;
    }

    function addTranscript(role, text) {
        if (!text) return;
        currentUtterance = (role === 'user' ? 'Heard: ' : 'Zoe: ') + text;
        els.copy.textContent = currentUtterance;
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
        if (!document.body.classList.contains('sky-empty')) {
            const previous = currentUtterance;
            els.copy.textContent = 'Notice: ' + text;
            if (previous) {
                setTimeout(() => {
                    if (!document.body.classList.contains('sky-empty') && currentUtterance === previous) {
                        els.copy.textContent = previous;
                    }
                }, 4500);
            }
        } else {
            els.copy.textContent = text;
        }
    }

    function setState(state) {
        orbState = state;
        els.mic.classList.toggle('recording', state === 'listening');
        const copy = {
            ambient: 'Say what you need. Skybridge will shape the screen around it.',
            listening: 'Listening...',
            thinking: 'Finding the right page, data, or card...',
            responding: 'Zoe is speaking. You can interrupt when needed.'
        };
        if (document.body.classList.contains('sky-empty') || !currentUtterance) {
            els.copy.textContent = copy[state] || copy.ambient;
        }
        setStatus(state.charAt(0).toUpperCase() + state.slice(1));
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

    window.addEventListener('beforeunload', () => {
        if (animationFrame) cancelAnimationFrame(animationFrame);
        if (voice) voice.stop();
    });

    document.addEventListener('DOMContentLoaded', init);
})();

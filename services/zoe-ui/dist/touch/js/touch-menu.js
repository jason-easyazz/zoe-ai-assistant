/**
 * TouchMenu — Universal navigation bar for all Zoe touch pages.
 *
 * Design: text-only labels (like desktop nav), pill active state, orb on left.
 * Auto-theme: light 7am–7pm, dark otherwise, with manual override.
 *
 * Usage:
 *   <script src="/touch/js/touch-menu.js"></script>
 *   (auto-inits via TouchMenu.init() at bottom of file)
 */
'use strict';

const TouchMenu = (() => {
    const NAV_H = 64;

    const PRIMARY = [
        { id: 'dashboard', path: '/touch/dashboard.html',  label: 'Home'     },
        { id: 'calendar',  path: '/touch/calendar.html',   label: 'Calendar' },
        { id: 'lists',     path: '/touch/lists.html',      label: 'Lists'    },
        { id: 'chat',      path: '/touch/chat.html',       label: 'Chat'     },
    ];

    const ALL_PAGES = [
        { id: 'dashboard',  path: '/touch/dashboard.html',  icon: '🏠', label: 'Home'       },
        { id: 'calendar',   path: '/touch/calendar.html',   icon: '📅', label: 'Calendar'   },
        { id: 'lists',      path: '/touch/lists.html',      icon: '☰',  label: 'Lists'      },
        { id: 'notes',      path: '/touch/notes.html',      icon: '📝', label: 'Notes'      },
        { id: 'journal',    path: '/touch/journal.html',    icon: '📔', label: 'Journal'    },
        { id: 'chat',       path: '/touch/chat.html',       icon: '💬', label: 'Chat'       },
        { id: 'music',      path: '/touch/music.html',      icon: '🎵', label: 'Music'      },
        { id: 'smarthome',  path: '/touch/smart-home.html', icon: '🏠', label: 'Smart Home' },
        { id: 'people',     path: '/touch/people.html',     icon: '👥', label: 'People'     },
        { id: 'memories',   path: '/touch/memories.html',   icon: '🧠', label: 'Memories'   },
        { id: 'cooking',    path: '/touch/cooking.html',    icon: '🍳', label: 'Cooking'    },
        { id: 'timers',     path: '/touch/timers.html',     icon: '⏱️', label: 'Timers'     },
        { id: 'weather',    path: '/touch/weather.html',    icon: '🌤️', label: 'Weather'    },
        { id: 'updates',    path: '/touch/updates.html',    icon: '🔄', label: 'Updates'    },
        { id: 'settings',   path: '/touch/settings.html',   icon: '⚙️', label: 'Settings'   },
    ];
    const GUEST_ALLOWED_PAGE_IDS = new Set([
        'dashboard', 'calendar', 'lists', 'chat', 'weather', 'smarthome', 'timers', 'music',
    ]); // Fallback if matrix is unavailable.

    let _pageId = 'dashboard';
    let _pressTimer = null;

    function injectCSS() {
        if (document.getElementById('ztm-styles')) return;
        const s = document.createElement('style');
        s.id = 'ztm-styles';
        s.textContent = `
/* ── Hide legacy chrome ── */
#bottom-nav, nav.bottom-nav, .bottom-nav, .bottom-nav-area { display:none !important; }
#hdr { display:none !important; }

/* ── Universal Nav Bar ─────────────────────────── */
#ztm-nav {
    position: fixed; top: 0; left: 0; right: 0;
    height: ${NAV_H}px;
    background: rgba(255,255,255,0.88);
    backdrop-filter: blur(20px) saturate(160%);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    border-bottom: 1px solid rgba(0,0,0,0.08);
    display: flex; align-items: stretch; z-index: 2000;
    padding: 0; user-select: none; -webkit-user-select: none;
    touch-action: manipulation; -webkit-tap-highlight-color: transparent;
    box-sizing: border-box;
    box-shadow: 0 1px 12px rgba(0,0,0,0.06);
}
/* Normalize nav typography/layout across touch pages. Some pages define broad
   element styles (a, button, active-class styles, etc.) that can otherwise leak in. */
#ztm-nav, #ztm-nav * {
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
}
#ztm-nav a, #ztm-nav button {
    margin: 0;
    line-height: 1;
}
html.dark-mode #ztm-nav {
    background: rgba(6,6,16,0.92);
    border-bottom-color: rgba(255,255,255,0.08);
    box-shadow: none;
}

/* Orb = voice / home button */
#ztm-orb {
    width: ${NAV_H}px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    border-right: 1px solid rgba(0,0,0,0.06);
}
html.dark-mode #ztm-orb { border-right-color: rgba(255,255,255,0.07); }
#ztm-orb-dot {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg, #7B61FF, #5AE0E0);
    box-shadow: 0 0 16px rgba(123,97,255,0.50);
    animation: ztm-breathe 4s ease-in-out infinite;
    transition: transform 0.15s, box-shadow 0.15s; flex-shrink: 0;
}
#ztm-orb:active #ztm-orb-dot { transform: scale(0.84); }
@keyframes ztm-breathe {
    0%,100% { transform: scale(1); }
    50%     { transform: scale(1.06); }
}
/* Voice state reflected on nav orb */
#ztm-orb-dot.orb-listening  { background: linear-gradient(135deg,#5AE0E0,#7B61FF) !important; box-shadow: 0 0 28px rgba(90,224,224,0.9) !important; animation: none !important; }
#ztm-orb-dot.orb-thinking   { background: linear-gradient(135deg,#FFB340,#FF6B6B) !important; box-shadow: 0 0 20px rgba(255,179,64,0.8) !important; animation: none !important; }
#ztm-orb-dot.orb-responding { background: linear-gradient(135deg,#7B61FF,#A855F7) !important; box-shadow: 0 0 28px rgba(123,97,255,0.9) !important; animation: none !important; }

/* Primary tab strip — text only, like desktop nav */
#ztm-tabs {
    flex: 1; display: flex; align-items: center; min-width: 0; padding: 0 8px; gap: 2px;
}
.ztm-tab {
    display: flex; align-items: center; justify-content: center;
    text-decoration: none;
    color: #444;
    font-size: clamp(16px, 1.5vw, 18px); font-weight: 600;
    line-height: 1;
    white-space: nowrap;
    border-radius: 10px;
    transition: color 0.18s, background 0.18s;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    padding: 8px 14px;
    letter-spacing: -0.1px;
}
html.dark-mode .ztm-tab { color: rgba(255,255,255,0.55); }
.ztm-tab:active { background: rgba(123,97,255,0.08); }
.ztm-tab.active {
    color: #7B61FF;
    font-weight: 700;
    background: rgba(123,97,255,0.10);
}
html.dark-mode .ztm-tab.active {
    color: #a991ff;
    background: rgba(123,97,255,0.20);
}

/* More button — same style as tabs */
#ztm-more-btn {
    display: flex; align-items: center; justify-content: center;
    color: #444;
    font-size: clamp(16px, 1.5vw, 18px); font-weight: 600;
    line-height: 1;
    cursor: pointer; border: none; background: transparent;
    border-radius: 10px;
    padding: 8px 14px;
    -webkit-tap-highlight-color: transparent; transition: color 0.18s, background 0.18s;
    font-family: inherit; white-space: nowrap; letter-spacing: -0.1px;
}
html.dark-mode #ztm-more-btn { color: rgba(255,255,255,0.55); }
#ztm-more-btn:active { background: rgba(123,97,255,0.08); }
#ztm-more-btn.active {
    color: #7B61FF;
    font-weight: 700;
    background: rgba(123,97,255,0.10);
}

/* Right actions */
#ztm-right {
    display: flex; align-items: center; padding: 0 14px 0 0; gap: 2px; flex-shrink: 0;
    border-left: 1px solid rgba(0,0,0,0.06);
}
html.dark-mode #ztm-right { border-left-color: rgba(255,255,255,0.07); }
.ztm-btn {
    width: 50px; height: ${NAV_H}px; border-radius: 10px; background: transparent;
    border: none; color: #555; font-size: 24px; line-height: 1;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    position: relative; flex-shrink: 0; -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, color 0.15s;
}
html.dark-mode .ztm-btn { color: rgba(255,255,255,0.50); }
.ztm-btn:active { background: rgba(123,97,255,0.08); }
#ztm-edit.editing { color: #7B61FF; background: rgba(123,97,255,0.10); }

#ztm-bell-badge {
    position: absolute; top: 8px; right: 6px;
    min-width: 20px; height: 20px; padding: 0 6px;
    background: #FF4D6A; color: #fff;
    border-radius: 10px; display: none;
    font-size: 12px; font-weight: 700; line-height: 20px;
    text-align: center; box-sizing: border-box;
    box-shadow: 0 2px 6px rgba(255,77,106,0.45);
    pointer-events: none;
}


/* ── More / Page Picker Sheet ────────────────────── */
#ztm-backdrop {
    display: none; position: fixed; inset: 0; z-index: 2001;
    background: rgba(0,0,0,0.22);
}
#ztm-backdrop.open { display: block; }
html.dark-mode #ztm-backdrop { background: rgba(0,0,0,0.45); }

#ztm-picker {
    display: none; position: fixed;
    top: ${NAV_H}px; left: 0; right: 0;
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid rgba(0,0,0,0.07);
    z-index: 2002; padding: 16px 16px 22px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.08);
}
html.dark-mode #ztm-picker {
    background: rgba(15,15,28,0.98);
    box-shadow: 0 8px 32px rgba(0,0,0,0.50);
}
#ztm-picker.open {
    display: block;
    animation: ztm-drop 0.22s cubic-bezier(0.34,1.2,0.64,1);
}
@keyframes ztm-drop {
    from { opacity:0; transform: translateY(-10px); }
    to   { opacity:1; transform: translateY(0); }
}
#ztm-picker-label {
    font-size: 12px; font-weight: 700; letter-spacing: 0.8px;
    text-transform: uppercase; color: #999; padding: 0 4px 14px;
}
#ztm-picker-grid {
    display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;
}
.ztm-pg-item {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 8px; padding: 16px 8px; border-radius: 16px; text-decoration: none;
    color: #444; font-size: 13px; font-weight: 600;
    cursor: pointer;
    background: rgba(0,0,0,0.03);
    border: 1px solid rgba(0,0,0,0.07);
    transition: background 0.15s; -webkit-tap-highlight-color: transparent; text-align: center;
}
html.dark-mode .ztm-pg-item {
    background: rgba(255,255,255,0.06);
    border-color: rgba(255,255,255,0.10);
    color: rgba(255,255,255,0.80);
}
.ztm-pg-item:active, .ztm-pg-item.active {
    background: rgba(123,97,255,0.12); color: #7B61FF;
    border-color: rgba(123,97,255,0.28);
}
.ztm-pg-icon { font-size: 28px; line-height: 1; }
.ztm-pg-label { line-height: 1.2; }

/* ── Orb context menu ─────────────────────────────── */
#ztm-ctx-menu {
    display: none; position: fixed;
    top: ${NAV_H + 8}px; left: 8px;
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 16px; overflow: hidden; z-index: 2003;
    min-width: 200px; box-shadow: 0 8px 32px rgba(0,0,0,0.12);
}
html.dark-mode #ztm-ctx-menu {
    background: rgba(15,15,28,0.98);
    box-shadow: 0 8px 32px rgba(0,0,0,0.50);
}
#ztm-ctx-menu.open {
    display: flex; flex-direction: column;
    animation: ztm-pop 0.20s cubic-bezier(0.34,1.56,0.64,1);
}
@keyframes ztm-pop {
    from { opacity:0; transform: scale(0.85) translateY(-10px); }
    to   { opacity:1; transform: scale(1) translateY(0); }
}
.ztm-ctx-item {
    padding: 18px 22px; font-size: 16px; font-weight: 600;
    color: #333; cursor: pointer;
    display: flex; align-items: center; gap: 14px;
    border-bottom: 1px solid rgba(0,0,0,0.05);
    -webkit-tap-highlight-color: transparent; transition: background 0.12s;
}
html.dark-mode .ztm-ctx-item { color: rgba(255,255,255,0.88); border-bottom-color: rgba(255,255,255,0.06); }
.ztm-ctx-item:last-child { border-bottom: none; }
.ztm-ctx-item:active { background: rgba(123,97,255,0.10); }
`;
        document.head.appendChild(s);
    }

    function buildNav(pageId) {
        const nav = document.createElement('nav');
        nav.id = 'ztm-nav';

        const orb = document.createElement('div');
        orb.id = 'ztm-orb';
        orb.title = pageId === 'dashboard' ? 'Hold for menu' : 'Home';
        orb.innerHTML = '<div id="ztm-orb-dot"></div>';
        nav.appendChild(orb);

        const tabs = document.createElement('div');
        tabs.id = 'ztm-tabs';
        PRIMARY.forEach(p => {
            const a = document.createElement('a');
            a.className = 'ztm-tab' + (p.id === pageId ? ' active' : '');
            a.href = p.path;
            a.textContent = p.label;
            tabs.appendChild(a);
        });
        const more = document.createElement('button');
        more.id = 'ztm-more-btn';
        more.textContent = 'More';
        if (!PRIMARY.find(p => p.id === pageId)) more.classList.add('active');
        tabs.appendChild(more);
        nav.appendChild(tabs);

        const right = document.createElement('div');
        right.id = 'ztm-right';

        if (pageId === 'dashboard' || pageId === 'lists') {
            const editBtn = document.createElement('button');
            editBtn.className = 'ztm-btn';
            editBtn.id = 'ztm-edit';
            editBtn.title = pageId === 'lists' ? 'Edit lists' : 'Edit / add widgets';
            editBtn.textContent = '✏️';
            right.appendChild(editBtn);
        }

        const themeBtn = document.createElement('button');
        themeBtn.className = 'ztm-btn';
        themeBtn.id = 'ztm-theme';
        themeBtn.title = 'Toggle theme';
        var _initTheme = localStorage.getItem('zoe_theme') || 'auto';
        var _tIcons = { light: '☀️', dark: '🌙', auto: '🔄' };
        themeBtn.textContent = _tIcons[_initTheme] || '🔄';
        right.appendChild(themeBtn);

        const bell = document.createElement('button');
        bell.className = 'ztm-btn';
        bell.id = 'ztm-bell';
        bell.title = 'Notifications';
        bell.innerHTML = '🔔<span id="ztm-bell-badge"></span>';
        right.appendChild(bell);

        nav.appendChild(right);
        return nav;
    }

    function buildPicker(pageId) {
        const backdrop = document.createElement('div');
        backdrop.id = 'ztm-backdrop';

        const picker = document.createElement('div');
        picker.id = 'ztm-picker';
        picker.innerHTML = '<div id="ztm-picker-label">All Pages</div><div id="ztm-picker-grid"></div>';

        const primaryIds = new Set(PRIMARY.map(p => p.id));
        const grid = picker.querySelector('#ztm-picker-grid');
        const morePages = getAvailablePages().filter(p => !primaryIds.has(p.id));
        morePages.forEach(p => {
            const item = document.createElement('a');
            item.className = 'ztm-pg-item' + (p.id === pageId ? ' active' : '');
            item.href = p.path;
            item.innerHTML = `<span class="ztm-pg-icon">${p.icon}</span><span class="ztm-pg-label">${p.label}</span>`;
            grid.appendChild(item);
        });

        if (morePages.length === 0) {
            grid.innerHTML = '<div style="grid-column:1/-1; padding: 10px 6px; color:#999; font-size:13px; text-align:center;">No additional pages</div>';
        }

        return { backdrop, picker };
    }

    function _getSessionRole() {
        try {
            const raw = localStorage.getItem('zoe_session');
            if (!raw) return '';
            const s = JSON.parse(raw);
            return String(s.role || s.user_info?.role || '').toLowerCase();
        } catch (_) {
            return '';
        }
    }

    function _isAuthenticatedNonGuestSession() {
        try {
            const raw = localStorage.getItem('zoe_session');
            if (!raw) return false;
            const s = JSON.parse(raw);
            const userId = String(s.user_id || s.user_info?.user_id || '').trim().toLowerCase();
            const sid = String(s.session_id || '').trim();
            // Treat any non-guest logged-in user as full access, even if role isn't
            // present in the stored session object.
            return Boolean(sid) && Boolean(userId) && userId !== 'guest';
        } catch (_) {
            return false;
        }
    }

    function _getRolePagesFromMatrix() {
        try {
            const raw = localStorage.getItem('zoe_capability_matrix');
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            const pages = parsed?.matrix?.pages;
            return pages && typeof pages === 'object' ? pages : null;
        } catch (_) {
            return null;
        }
    }

    function getAvailablePages() {
        const role = _getSessionRole();
        // Authenticated non-guest users (admin/family) should see the full menu.
        // Capability matrix restrictions here are intended for guest lockdown.
        if (role && role !== 'guest') return ALL_PAGES;
        if (_isAuthenticatedNonGuestSession()) return ALL_PAGES;

        const matrixPages = _getRolePagesFromMatrix();
        if (matrixPages) {
            return ALL_PAGES.filter((p) => !!matrixPages[p.id]);
        }
        if (role !== 'guest') return ALL_PAGES;
        return ALL_PAGES.filter((p) => GUEST_ALLOWED_PAGE_IDS.has(p.id));
    }

    function buildCtxMenu(pageId) {
        const menu = document.createElement('div');
        menu.id = 'ztm-ctx-menu';

        const items = [
            pageId === 'dashboard' ? { icon: '➕', label: 'Add Widget',   action: 'add-widget'  } : null,
            pageId === 'dashboard' ? { icon: '✏️', label: 'Edit Layout',  action: 'edit-layout' } : null,
            { icon: '💬', label: 'Chat with Zoe', action: 'chat'     },
            { icon: '⚙️', label: 'Settings',      action: 'settings'  },
        ].filter(Boolean);

        items.forEach(item => {
            const row = document.createElement('div');
            row.className = 'ztm-ctx-item';
            row.innerHTML = `<span>${item.icon}</span>${item.label}`;
            row.addEventListener('click', () => { closeAll(); handleCtxAction(item.action); });
            menu.appendChild(row);
        });
        return menu;
    }

    function handleCtxAction(action) {
        switch (action) {
            case 'add-widget':  if (typeof touchDash !== 'undefined') touchDash.openLibrary(); break;
            case 'edit-layout': if (typeof touchDash !== 'undefined') touchDash.toggleEdit(); break;
            case 'chat':        _nav('/touch/chat.html'); break;
            case 'settings':    _nav('/touch/settings.html'); break;
        }
    }

    function openPicker() {
        document.getElementById('ztm-picker')?.classList.add('open');
        document.getElementById('ztm-backdrop')?.classList.add('open');
    }

    function openCtx() {
        document.getElementById('ztm-ctx-menu')?.classList.add('open');
        document.getElementById('ztm-backdrop')?.classList.add('open');
    }

    function closeAll() {
        document.getElementById('ztm-picker')?.classList.remove('open');
        document.getElementById('ztm-ctx-menu')?.classList.remove('open');
        document.getElementById('ztm-backdrop')?.classList.remove('open');
    }

    function adjustLayout(pageId) {
        document.documentElement.style.setProperty('--ztm-nav-h', NAV_H + 'px');

        if (pageId === 'dashboard') {
            const app = document.getElementById('app');
            if (app) {
                app.style.top    = NAV_H + 'px';
                app.style.height = `calc(100dvh - ${NAV_H}px)`;
                app.style.bottom = '0';
            }
        }
        // Body padding-top is handled by touch-adapter.css via --ta-nav-h
        // Do NOT set body.style.paddingTop here — it causes double offsets
        const ss = document.getElementById('zoe-ss');
        if (ss) { ss.style.top = '0'; ss.style.height = '100dvh'; }
        const np = document.getElementById('notifications-panel');
        if (np) np.style.top = NAV_H + 'px';
    }

    function removeOldChrome() {
        const bn = document.querySelector('#bottom-nav, nav.bottom-nav, .bottom-nav');
        if (bn) bn.remove();
    }

    function applyAutoTheme() {
        var stored = localStorage.getItem('zoe_theme') || 'auto';
        var dark;
        if (stored === 'dark') dark = true;
        else if (stored === 'light') dark = false;
        else {
            var h = new Date().getHours();
            dark = h < 7 || h >= 19;
        }
        document.documentElement.classList.toggle('dark-mode', dark);
        document.body.classList.remove('dark-mode', 'light-mode');
        if (dark) document.body.classList.add('dark-mode');
        var mc = document.querySelector('meta[name=theme-color]');
        if (mc) mc.content = dark ? '#060610' : '#fafbfc';
    }

    function refreshBadge() {
        const badge = document.getElementById('ztm-bell-badge');
        if (!badge) return;
        try {
            const count = window.zoeNotifications?.getCount?.() || 0;
            if (count > 0) {
                badge.textContent = count > 9 ? '9+' : String(count);
                badge.style.display = 'block';
            } else {
                badge.textContent = '';
                badge.style.display = 'none';
            }
        } catch (_) { badge.style.display = 'none'; }
    }

    function _nav(path) {
        const target = _normalizeTouchPath(path);
        document.body.style.transition = 'opacity 0.22s';
        document.body.style.opacity = '0';
        setTimeout(() => { window.location.href = target; }, 210);
    }

    function _normalizeTouchPath(input) {
        try {
            const u = new URL(String(input || ''), window.location.origin);
            // External targets are never allowed from touch nav.
            if (u.origin !== window.location.origin) return '/touch/dashboard.html';
            const p = u.pathname || '/';
            const base = p.split('/').pop() || '';
            const allowed = new Set([
                'dashboard.html', 'calendar.html', 'lists.html', 'chat.html', 'notes.html',
                'journal.html', 'people.html', 'music.html', 'smart-home.html', 'weather.html',
                'settings.html', 'memories.html', 'cooking.html', 'timers.html', 'updates.html',
                'index.html'
            ]);
            // Already in touch namespace — allow only known touch pages.
            if (p.startsWith('/touch/')) {
                return allowed.has(base) ? (p + (u.search || '') + (u.hash || '')) : '/touch/dashboard.html';
            }

            // Map desktop routes to touch equivalents by basename.
            if (allowed.has(base)) return '/touch/' + base + (u.search || '') + (u.hash || '');

            // Default hard guard.
            return '/touch/dashboard.html';
        } catch (_) {
            return '/touch/dashboard.html';
        }
    }

    function _installTouchNavigationGuard() {
        // Intercept same-origin anchor clicks and force touch namespace.
        document.addEventListener('click', (e) => {
            const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
            if (!a) return;
            const href = a.getAttribute('href') || '';
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            const target = _normalizeTouchPath(href);
            const resolved = new URL(href, window.location.origin);
            const current = resolved.pathname + (resolved.search || '') + (resolved.hash || '');
            if (target === current && target.startsWith('/touch/')) return;
            e.preventDefault();
            _nav(target);
        }, true);

        // Guard programmatic same-origin navigation where possible.
        try {
            const _assign = window.location.assign.bind(window.location);
            const _replace = window.location.replace.bind(window.location);
            window.location.assign = (url) => _assign(_normalizeTouchPath(url));
            window.location.replace = (url) => _replace(_normalizeTouchPath(url));
        } catch (_) { /* ignore platform restrictions */ }
    }

    function _activateVoice() {
        const daemonUrl = 'http://localhost:7777/activate';
        fetch(daemonUrl, { method: 'POST', signal: AbortSignal.timeout(800) })
            .then(() => {})
            .catch(() => {
                if (window.zoePushWs && window.zoePushWs.readyState === 1) {
                    const panelId = localStorage.getItem('zoe_touch_panel_id') || '';
                    window.zoePushWs.send(JSON.stringify({ type: 'voice:orb_tap', data: { panel_id: panelId } }));
                }
                document.dispatchEvent(new CustomEvent('zoe:voice:wake'));
            });
        const orbDot = document.getElementById('ztm-orb-dot');
        if (orbDot) {
            orbDot.style.boxShadow = '0 0 32px rgba(90,224,224,0.9), 0 0 64px rgba(90,224,224,0.5)';
            setTimeout(() => { orbDot.style.boxShadow = ''; }, 2000);
        }
    }

    function wireOrb(pageId) {
        const orb = document.getElementById('ztm-orb');
        if (!orb) return;
        orb.title = 'Switch user / Login';

        let pressed = false;
        const start = () => { pressed = false; _pressTimer = setTimeout(() => { pressed = true; openCtx(); }, 500); };
        const cancel = () => { if (_pressTimer) { clearTimeout(_pressTimer); _pressTimer = null; } };

        orb.addEventListener('touchstart', start,  { passive: true });
        orb.addEventListener('touchend',   cancel);
        orb.addEventListener('touchcancel',cancel);
        orb.addEventListener('mousedown',  start);
        orb.addEventListener('mouseup',    cancel);
        orb.addEventListener('mouseleave', cancel);

        orb.addEventListener('click', () => {
            if (pressed) return;
            _nav('/touch/index.html');
        });
    }

    function loadTimersGlobal() {
        if (window.TouchTimers && window.TouchTimers._installed) return;
        if (document.querySelector('script[data-ztm-timers]')) return;
        const s = document.createElement('script');
        s.src = '/touch/js/timers-global.js';
        s.defer = true;
        s.setAttribute('data-ztm-timers','1');
        document.head.appendChild(s);
    }

    // Fire a "still alive" beacon to the local Pi panel agent whenever a
    // page loads or becomes visible, so the screen is guaranteed to be awake
    // while the user is actively navigating.
    function pokeWake(hold = 8) {
        try {
            fetch('http://127.0.0.1:8765/wake', {
                method: 'POST',
                mode: 'no-cors',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hold_s: hold }),
                keepalive: true,
            }).catch(() => {});
        } catch (_) {}
    }

    function init(opts = {}) {
        const pageId = opts.page || _detectPage();
        _pageId = pageId;

        applyAutoTheme();
        injectCSS();
        loadTimersGlobal();
        pokeWake(8);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') pokeWake(8);
        });

        const run = () => {
            removeOldChrome();
            _installTouchNavigationGuard();
            // Idempotency: some pages include/load this script more than once.
            // Remove any previous touch-menu chrome before re-inserting.
            document.getElementById('ztm-nav')?.remove();
            document.getElementById('ztm-backdrop')?.remove();
            document.getElementById('ztm-picker')?.remove();
            document.getElementById('ztm-ctx-menu')?.remove();

            const nav = buildNav(pageId);
            document.body.insertBefore(nav, document.body.firstChild);

            const { backdrop, picker } = buildPicker(pageId);
            const ctxMenu = buildCtxMenu(pageId);
            document.body.appendChild(backdrop);
            document.body.appendChild(picker);
            document.body.appendChild(ctxMenu);

            adjustLayout(pageId);


            wireOrb(pageId);

            backdrop.addEventListener('click', closeAll);
            document.getElementById('ztm-more-btn')?.addEventListener('click', openPicker);

            const editBtn = document.getElementById('ztm-edit');
            if (editBtn) {
                const updateEditButtonState = () => {
                    const editing = document.body.classList.contains('edit-mode');
                    editBtn.textContent = editing ? '✓ Done' : '✏️';
                    editBtn.style.fontSize = editing ? '15px' : '';
                    editBtn.classList.toggle('editing', editing);
                    editBtn.title = editing ? 'Done' : (pageId === 'lists' ? 'Edit lists' : 'Edit / add widgets');
                };
                updateEditButtonState();
                editBtn.addEventListener('click', () => {
                    if (pageId === 'dashboard') {
                        // Single source of truth: dashboard.js handles GridStack + mode state.
                        if (typeof window.toggleEditMode === 'function') {
                            window.toggleEditMode();
                        }
                    } else if (pageId === 'lists') {
                        // Single source of truth: lists-dashboard.js handles GridStack + save state.
                        if (typeof window.toggleEditMode === 'function') {
                            window.toggleEditMode();
                        } else {
                            // Fallback only if lists-dashboard.js isn't available yet.
                            const editing = !document.body.classList.contains('edit-mode');
                            document.body.classList.toggle('edit-mode', editing);
                            const gs = document.querySelector('.grid-stack');
                            if (gs && gs.gridstack) gs.gridstack.setStatic(!editing);
                        }
                        // Hard guard: ensure touch drag is actually enabled in edit mode.
                        // This protects against occasional state drift between body/edit button
                        // and GridStack static mode on kiosk pages.
                        const isEditing = document.body.classList.contains('edit-mode');
                        const gsEl = document.querySelector('.grid-stack');
                        if (gsEl && gsEl.gridstack) {
                            gsEl.gridstack.setStatic(!isEditing);
                        }
                    }
                    updateEditButtonState();
                });
            }

            document.getElementById('ztm-theme')?.addEventListener('click', () => {
                var stored = localStorage.getItem('zoe_theme') || 'auto';
                var cycle = { light: 'dark', dark: 'auto', auto: 'light' };
                var next = cycle[stored] || 'auto';
                localStorage.setItem('zoe_theme', next);
                applyAutoTheme();
                var btn = document.getElementById('ztm-theme');
                var icons = { light: '☀️', dark: '🌙', auto: '🔄' };
                if (btn) btn.textContent = icons[next] || '🔄';
            });

            document.getElementById('ztm-bell')?.addEventListener('click', () => {
                window.zoeNotifications?.toggle?.();
            });

            refreshBadge();
            setInterval(refreshBadge, 30000);

            window._ztmCloseAll = closeAll;

            // Suppress noisy auth/network error toasts in kiosk mode
            (function suppressKioskAuthToasts() {
                const _orig = window.showNotification;
                window.showNotification = function(msg, type) {
                    // Suppress noisy kiosk toasts
                    const msgStr = String(msg);
                    if (type === 'error' && /unauthorized|not found|401|404|failed to fetch|session expired|network error|connection error|unknown list type|failed to load|websocket|ws:\/\//i.test(msgStr)) return;
                    if (/update available|new version|reload|service worker|sw.*update/i.test(msgStr)) return;
                    if (_orig) _orig.apply(this, arguments);
                };
            })();

            // Auto-auth: kiosk panels load dashboard.html directly (not index.html),
            // so we auto-create a guest session here if none exists.
            (function kioskAutoAuth() {
                if (localStorage.getItem('zoe_kiosk') !== '1') return;
                let session = null;
                try { session = JSON.parse(localStorage.getItem('zoe_session') || 'null'); } catch(_) {}
                if (session && session.session_id && session.expires_at) {
                    const expiresAt = new Date(session.expires_at);
                    if (expiresAt > new Date(Date.now() + 60000)) return; // Valid session — nothing to do
                }
                // No valid session — fetch a guest token
                fetch('/api/auth/guest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device_info: { source: 'touch-kiosk-automenu' } })
                }).then(r => r.ok ? r.json() : Promise.reject(r.status))
                  .then(data => {
                      if (data && data.session_id) {
                          localStorage.setItem('zoe_session', JSON.stringify(data));
                          // Reload so all widgets can re-authenticate
                          setTimeout(() => window.location.reload(), 300);
                      }
                  }).catch(() => {}); // Silent fail — widgets will show degraded state
            })();
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', run);
        } else {
            run();
        }
    }

    function _detectPage() {
        const path = window.location.pathname;
        const p = getAvailablePages().find(pg => path.endsWith(pg.path.replace('/touch/', '')));
        return p ? p.id : 'dashboard';
    }

    return { init, openPicker, closeAll, _nav, _activateVoice };
})();

window.TouchMenu = TouchMenu;

// Auto-init when included — page is detected from URL
TouchMenu.init();

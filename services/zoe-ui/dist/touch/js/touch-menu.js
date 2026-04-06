/**
 * TouchMenu — Universal navigation bar for all Zoe touch pages.
 *
 * Design: 4 primary tabs always visible + "More" button that opens a
 * full-page picker. Larger text and touch targets for 7" screens.
 *
 * Usage:
 *   <script src="/touch/js/touch-menu.js"></script>
 *   <script> TouchMenu.init({ page: 'dashboard' }); </script>
 */
'use strict';

const TouchMenu = (() => {
    const NAV_H = 64; // px — taller nav bar for easier touch targets

    // Primary 4 tabs always visible in the bar
    const PRIMARY = [
        { id: 'dashboard', path: '/touch/dashboard.html',  icon: '⊞',  label: 'Home'     },
        { id: 'calendar',  path: '/touch/calendar.html',   icon: '📅', label: 'Calendar' },
        { id: 'lists',     path: '/touch/lists.html',      icon: '✅', label: 'Lists'    },
        { id: 'chat',      path: '/touch/chat.html',       icon: '💬', label: 'Chat'     },
    ];

    // All pages (including above) for the "More" picker
    const ALL_PAGES = [
        { id: 'dashboard',  path: '/touch/dashboard.html',  icon: '⊞',  label: 'Home'       },
        { id: 'calendar',   path: '/touch/calendar.html',   icon: '📅', label: 'Calendar'   },
        { id: 'lists',      path: '/touch/lists.html',      icon: '✅', label: 'Lists'       },
        { id: 'notes',      path: '/touch/notes.html',      icon: '📝', label: 'Notes'       },
        { id: 'journal',    path: '/touch/journal.html',    icon: '📔', label: 'Journal'     },
        { id: 'chat',       path: '/touch/chat.html',       icon: '💬', label: 'Chat'        },
        { id: 'music',      path: '/touch/music.html',      icon: '🎵', label: 'Music'       },
        { id: 'smarthome',  path: '/touch/smart-home.html', icon: '🏠', label: 'Smart Home'  },
        { id: 'people',     path: '/touch/people.html',     icon: '👥', label: 'People'      },
        { id: 'memories',   path: '/touch/memories.html',   icon: '🧠', label: 'Memories'    },
        { id: 'cooking',    path: '/touch/cooking.html',    icon: '🍳', label: 'Cooking'     },
        { id: 'weather',    path: '/touch/weather.html',    icon: '🌤️', label: 'Weather'     },
        { id: 'settings',   path: '/touch/settings.html',   icon: '⚙️', label: 'Settings'    },
    ];

    let _pageId = 'dashboard';
    let _pressTimer = null;

    // ─── CSS ──────────────────────────────────────────────────────────
    function injectCSS() {
        if (document.getElementById('ztm-styles')) return;
        const s = document.createElement('style');
        s.id = 'ztm-styles';
        s.textContent = `
/* ── Hide old chrome immediately ── */
#bottom-nav, nav.bottom-nav, .bottom-nav, .bottom-nav-area { display:none !important; }

/* ── Universal Nav Bar ─────────────────────────────── */
#ztm-nav {
    position: fixed;
    top: 0; left: 0; right: 0;
    height: ${NAV_H}px;
    background: rgba(6, 6, 18, 0.96);
    backdrop-filter: blur(24px) saturate(160%);
    -webkit-backdrop-filter: blur(24px) saturate(160%);
    border-bottom: 1px solid rgba(255,255,255,0.10);
    display: flex;
    align-items: stretch;
    z-index: 2000;
    padding: 0;
    user-select: none;
    -webkit-user-select: none;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    box-sizing: border-box;
}
body.light-mode #ztm-nav {
    background: rgba(245, 247, 255, 0.98);
    border-bottom-color: rgba(0,0,0,0.10);
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}

/* Orb = Home button */
#ztm-orb {
    width: ${NAV_H}px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    border-right: 1px solid rgba(255,255,255,0.08);
}
body.light-mode #ztm-orb { border-right-color: rgba(0,0,0,0.07); }
#ztm-orb-dot {
    width: 36px; height: 36px;
    border-radius: 50%;
    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
    box-shadow: 0 0 18px rgba(123,97,255,0.50);
    animation: ztm-breathe 4s ease-in-out infinite;
    transition: transform 0.15s;
    flex-shrink: 0;
}
#ztm-orb:active #ztm-orb-dot { transform: scale(0.84); }
@keyframes ztm-breathe {
    0%,100% { transform: scale(1); }
    50%      { transform: scale(1.07); }
}

/* Primary tab strip */
#ztm-tabs {
    flex: 1;
    display: flex;
    align-items: stretch;
    min-width: 0;
}

.ztm-tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    text-decoration: none;
    color: rgba(255,255,255,0.45);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    white-space: nowrap;
    position: relative;
    transition: color 0.15s, background 0.15s;
    cursor: pointer;
    min-width: 64px;
    -webkit-tap-highlight-color: transparent;
    padding: 0 4px;
}
body.light-mode .ztm-tab { color: rgba(26,26,46,0.40); }

.ztm-tab:active { background: rgba(255,255,255,0.07); }
body.light-mode .ztm-tab:active { background: rgba(0,0,0,0.05); }

.ztm-tab.active { color: #7B61FF; }
body.light-mode .ztm-tab.active { color: #5B41DF; }
.ztm-tab.active::after {
    content: '';
    position: absolute;
    bottom: 0; left: 20%; right: 20%;
    height: 3px;
    background: #7B61FF;
    border-radius: 2px 2px 0 0;
}
body.light-mode .ztm-tab.active::after { background: #5B41DF; }

.ztm-tab-icon { font-size: 22px; line-height: 1; }
.ztm-tab-label { font-size: 11px; line-height: 1; }

/* More button */
#ztm-more-btn {
    flex-shrink: 0;
    min-width: 64px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    color: rgba(255,255,255,0.45);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    cursor: pointer;
    border: none;
    background: transparent;
    padding: 0 8px;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.15s;
}
body.light-mode #ztm-more-btn { color: rgba(26,26,46,0.40); }
#ztm-more-btn:active { background: rgba(255,255,255,0.07); }
body.light-mode #ztm-more-btn:active { background: rgba(0,0,0,0.05); }
#ztm-more-btn .ztm-tab-icon { font-size: 22px; }

/* Right actions */
#ztm-right {
    display: flex;
    align-items: center;
    padding: 0 6px 0 0;
    gap: 0;
    flex-shrink: 0;
    border-left: 1px solid rgba(255,255,255,0.08);
}
body.light-mode #ztm-right { border-left-color: rgba(0,0,0,0.07); }

.ztm-btn {
    width: 48px; height: ${NAV_H}px;
    border-radius: 0;
    background: transparent;
    border: none;
    color: rgba(255,255,255,0.55);
    font-size: 18px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    position: relative;
    flex-shrink: 0;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, color 0.15s;
}
body.light-mode .ztm-btn { color: rgba(26,26,46,0.52); }
.ztm-btn:active { background: rgba(255,255,255,0.09); }
body.light-mode .ztm-btn:active { background: rgba(0,0,0,0.06); }

#ztm-edit.editing { color: #7B61FF; background: rgba(123,97,255,0.12); }

#ztm-bell-badge {
    position: absolute;
    top: 12px; right: 10px;
    width: 8px; height: 8px;
    background: #FF4D6A;
    border-radius: 50%;
    display: none;
}

#ztm-clock {
    font-size: 14px;
    font-weight: 500;
    color: rgba(255,255,255,0.55);
    padding: 0 12px 0 4px;
    letter-spacing: 0.3px;
    flex-shrink: 0;
    white-space: nowrap;
}
body.light-mode #ztm-clock { color: rgba(26,26,46,0.55); }

/* ── More / Page Picker Sheet ────────────────────────── */
#ztm-backdrop {
    display: none;
    position: fixed; inset: 0;
    z-index: 2001;
    background: rgba(0,0,0,0.35);
}
#ztm-backdrop.open { display: block; }

#ztm-picker {
    display: none;
    position: fixed;
    top: ${NAV_H}px; left: 0; right: 0;
    background: rgba(10, 10, 24, 0.98);
    border-bottom: 1px solid rgba(255,255,255,0.10);
    z-index: 2002;
    padding: 14px 12px 20px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.50);
}
#ztm-picker.open {
    display: block;
    animation: ztm-drop 0.22s cubic-bezier(0.34,1.2,0.64,1);
}
@keyframes ztm-drop {
    from { opacity:0; transform: translateY(-12px); }
    to   { opacity:1; transform: translateY(0); }
}
body.light-mode #ztm-picker {
    background: rgba(245,248,255,0.99);
    border-bottom-color: rgba(0,0,0,0.10);
    box-shadow: 0 8px 32px rgba(0,0,0,0.12);
}

#ztm-picker-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.35);
    padding: 0 6px 12px;
}
body.light-mode #ztm-picker-label { color: rgba(26,26,46,0.38); }

#ztm-picker-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 8px;
}

.ztm-pg-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 14px 8px;
    border-radius: 14px;
    text-decoration: none;
    color: rgba(255,255,255,0.80);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    cursor: pointer;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    transition: background 0.15s;
    -webkit-tap-highlight-color: transparent;
    text-align: center;
}
body.light-mode .ztm-pg-item {
    color: rgba(26,26,46,0.80);
    background: rgba(255,255,255,0.70);
    border-color: rgba(0,0,0,0.08);
}
.ztm-pg-item:active, .ztm-pg-item.active { background: rgba(123,97,255,0.20); color: #9B8BFF; border-color: rgba(123,97,255,0.30); }
body.light-mode .ztm-pg-item:active, body.light-mode .ztm-pg-item.active { background: rgba(91,65,223,0.12); color: #5B41DF; }
.ztm-pg-icon { font-size: 26px; line-height: 1; }
.ztm-pg-label { line-height: 1.2; }

/* ── Orb long-press context menu ─────────────────────── */
#ztm-ctx-menu {
    display: none;
    position: fixed;
    top: ${NAV_H + 8}px;
    left: 8px;
    background: rgba(10, 10, 22, 0.98);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 16px;
    overflow: hidden;
    z-index: 2003;
    min-width: 180px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.50);
}
#ztm-ctx-menu.open {
    display: flex;
    flex-direction: column;
    animation: ztm-pop 0.20s cubic-bezier(0.34,1.56,0.64,1);
}
@keyframes ztm-pop {
    from { opacity:0; transform: scale(0.85) translateY(-10px); }
    to   { opacity:1; transform: scale(1) translateY(0); }
}
body.light-mode #ztm-ctx-menu {
    background: rgba(248,250,255,0.99);
    border-color: rgba(0,0,0,0.10);
}

.ztm-ctx-item {
    padding: 16px 20px;
    font-size: 15px;
    font-weight: 500;
    color: rgba(255,255,255,0.90);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    -webkit-tap-highlight-color: transparent;
    transition: background 0.12s;
}
body.light-mode .ztm-ctx-item { color: #1a1a2e; border-bottom-color: rgba(0,0,0,0.06); }
.ztm-ctx-item:last-child { border-bottom: none; }
.ztm-ctx-item:active { background: rgba(123,97,255,0.18); }

/* ── Full-page light/dark theme overrides ─────────────── */
/* Override :root variables by targeting html (same element) */
html:has(body.dark-mode) {
    --bg-deep: #060610;
    --bg: #0f0f1a;
    --bg2: #0b0b18;
    --surface: rgba(255,255,255,0.055);
    --surface-hi: rgba(255,255,255,0.09);
    --border: rgba(255,255,255,0.10);
    --border-hi: rgba(255,255,255,0.18);
    --text-1: rgba(255,255,255,0.93);
    --text-2: rgba(255,255,255,0.58);
    --text-3: rgba(255,255,255,0.32);
    --text: rgba(255,255,255,0.93);
    --glass: rgba(255,255,255,0.07);
    --glass-bg: rgba(255,255,255,0.07);
    --glass-border: rgba(255,255,255,0.12);
    --card-bg: rgba(255,255,255,0.055);
    --background-dark: #0f0f1a;
    background: #060610 !important;
}
body.dark-mode {
    background: #060610 !important;
    color: rgba(255,255,255,0.93) !important;
}

html:has(body.light-mode) {
    --bg-deep: #eef1fa;
    --bg: #f4f6ff;
    --bg2: #eaecf8;
    --surface: rgba(255,255,255,0.85);
    --surface-hi: rgba(255,255,255,0.97);
    --border: rgba(0,0,0,0.09);
    --border-hi: rgba(0,0,0,0.14);
    --text-1: #1a1a2e;
    --text-2: rgba(26,26,46,0.62);
    --text-3: rgba(26,26,46,0.38);
    --text: #1a1a2e;
    --glass: rgba(255,255,255,0.78);
    --glass-bg: rgba(255,255,255,0.78);
    --glass-border: rgba(0,0,0,0.09);
    --card-bg: rgba(255,255,255,0.85);
    --background-light: #f4f6ff;
    --background-dark: #eef1fa;
    background: #eef1fa !important;
    color: #1a1a2e;
}
body.light-mode {
    background: #eef1fa !important;
    color: #1a1a2e !important;
}

/* Light-mode: flip all dark glass/surface elements white */
body.light-mode .glass-card,
body.light-mode .card,
body.light-mode .panel,
body.light-mode .settings-section,
body.light-mode .list-card,
body.light-mode .event-card,
body.light-mode .note-card,
body.light-mode .journal-card,
body.light-mode .tc-event,
body.light-mode .tc-list-item,
body.light-mode .section-card {
    background: rgba(255,255,255,0.85) !important;
    border-color: rgba(0,0,0,0.09) !important;
    color: #1a1a2e !important;
}
body.light-mode #hdr,
body.light-mode .section-header,
body.light-mode .page-header,
body.light-mode .tc-header {
    background: rgba(245,247,255,0.95) !important;
    border-bottom-color: rgba(0,0,0,0.09) !important;
    color: #1a1a2e !important;
}
body.light-mode input,
body.light-mode textarea,
body.light-mode select {
    background: rgba(255,255,255,0.95) !important;
    border-color: rgba(0,0,0,0.14) !important;
    color: #1a1a2e !important;
}
body.light-mode input::placeholder,
body.light-mode textarea::placeholder { color: rgba(26,26,46,0.35) !important; }
body.light-mode h1, body.light-mode h2, body.light-mode h3,
body.light-mode h4, body.light-mode h5, body.light-mode h6,
body.light-mode p, body.light-mode li, body.light-mode span,
body.light-mode label, body.light-mode td, body.light-mode th {
    color: inherit;
}
`;
        document.head.appendChild(s);
    }

    // ─── Build nav bar ────────────────────────────────────────────────
    function buildNav(pageId) {
        const nav = document.createElement('nav');
        nav.id = 'ztm-nav';

        // Orb
        const orb = document.createElement('div');
        orb.id = 'ztm-orb';
        orb.title = pageId === 'dashboard' ? 'Hold for menu' : 'Home';
        orb.innerHTML = '<div id="ztm-orb-dot"></div>';
        nav.appendChild(orb);

        // Primary tabs
        const tabs = document.createElement('div');
        tabs.id = 'ztm-tabs';
        PRIMARY.forEach(p => {
            const a = document.createElement('a');
            a.className = 'ztm-tab' + (p.id === pageId ? ' active' : '');
            a.href = p.path;
            a.innerHTML = `<span class="ztm-tab-icon">${p.icon}</span><span class="ztm-tab-label">${p.label}</span>`;
            tabs.appendChild(a);
        });
        // More button
        const more = document.createElement('button');
        more.id = 'ztm-more-btn';
        more.innerHTML = '<span class="ztm-tab-icon">⋯</span><span class="ztm-tab-label">More</span>';
        // Mark as active if current page isn't in primary
        if (!PRIMARY.find(p => p.id === pageId)) more.classList.add('active');
        tabs.appendChild(more);
        nav.appendChild(tabs);

        // Right: edit (dashboard only) + theme + bell + clock
        const right = document.createElement('div');
        right.id = 'ztm-right';

        if (pageId === 'dashboard') {
            const editBtn = document.createElement('button');
            editBtn.className = 'ztm-btn';
            editBtn.id = 'ztm-edit';
            editBtn.title = 'Edit / add widgets';
            editBtn.textContent = '✏️';
            right.appendChild(editBtn);
        }

        const themeMode = localStorage.getItem('zoe_theme') || 'dark';
        const themeBtn = document.createElement('button');
        themeBtn.className = 'ztm-btn';
        themeBtn.id = 'ztm-theme';
        themeBtn.title = 'Toggle theme';
        themeBtn.textContent = themeMode === 'light' ? '☀️' : '🌙';
        right.appendChild(themeBtn);

        const bell = document.createElement('button');
        bell.className = 'ztm-btn';
        bell.id = 'ztm-bell';
        bell.title = 'Notifications';
        bell.innerHTML = '🔔<span id="ztm-bell-badge"></span>';
        right.appendChild(bell);

        const clock = document.createElement('div');
        clock.id = 'ztm-clock';
        right.appendChild(clock);

        nav.appendChild(right);
        return nav;
    }

    // ─── Build page picker (full grid) ───────────────────────────────
    function buildPicker(pageId) {
        const backdrop = document.createElement('div');
        backdrop.id = 'ztm-backdrop';

        const picker = document.createElement('div');
        picker.id = 'ztm-picker';
        picker.innerHTML = '<div id="ztm-picker-label">All Pages</div><div id="ztm-picker-grid"></div>';

        const grid = picker.querySelector('#ztm-picker-grid');
        ALL_PAGES.forEach(p => {
            const item = document.createElement('a');
            item.className = 'ztm-pg-item' + (p.id === pageId ? ' active' : '');
            item.href = p.path;
            item.innerHTML = `<span class="ztm-pg-icon">${p.icon}</span><span class="ztm-pg-label">${p.label}</span>`;
            grid.appendChild(item);
        });

        return { backdrop, picker };
    }

    // ─── Build orb context menu ───────────────────────────────────────
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
            row.addEventListener('click', () => {
                closeAll();
                handleCtxAction(item.action);
            });
            menu.appendChild(row);
        });
        return menu;
    }

    function handleCtxAction(action) {
        switch (action) {
            case 'add-widget':   if (typeof touchDash !== 'undefined') touchDash.openLibrary(); break;
            case 'edit-layout':  if (typeof touchDash !== 'undefined') touchDash.toggleEdit(); break;
            case 'chat':         _nav('/touch/chat.html'); break;
            case 'settings':     _nav('/touch/settings.html'); break;
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

    // ─── Page layout adjustments ──────────────────────────────────────
    function adjustLayout(pageId) {
        if (pageId === 'dashboard') {
            const app = document.getElementById('app');
            if (app) {
                app.style.top    = NAV_H + 'px';
                app.style.height = `calc(100vh - ${NAV_H}px)`;
                app.style.bottom = '0';
            }
        } else {
            document.body.style.paddingTop = NAV_H + 'px';
        }
        const ss = document.getElementById('ss');
        if (ss) { ss.style.top = '0'; ss.style.height = '100vh'; }
        const np = document.getElementById('notifications-panel');
        if (np) np.style.top = NAV_H + 'px';
    }

    // ─── Remove existing per-page chrome ─────────────────────────────
    function removeOldChrome() {
        const bn = document.querySelector('#bottom-nav, nav.bottom-nav, .bottom-nav');
        if (bn) bn.remove();
        const hdr = document.querySelector('#hdr, header.header, div.header, .header-bar');
        if (hdr) hdr.style.display = 'none';
    }

    // ─── Theme ───────────────────────────────────────────────────────
    function applyStoredTheme() {
        const theme = localStorage.getItem('zoe_theme') || 'dark';
        const body  = document.body;
        const html  = document.documentElement;
        body.classList.remove('dark-mode', 'light-mode');
        if (theme === 'light') {
            body.classList.add('light-mode');
            html.style.background = '#eef1fa';
            html.style.color = '#1a1a2e';
        } else {
            body.classList.add('dark-mode');
            html.style.background = '#060610';
            html.style.color = '';
        }
    }

    // ─── Clock ───────────────────────────────────────────────────────
    function tickClock() {
        const el = document.getElementById('ztm-clock');
        if (el) el.textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    // ─── Notification badge ───────────────────────────────────────────
    function refreshBadge() {
        const badge = document.getElementById('ztm-bell-badge');
        if (!badge) return;
        try { badge.style.display = (window.zoeNotifications?.getCount?.() || 0) > 0 ? 'block' : 'none'; }
        catch (_) { badge.style.display = 'none'; }
    }

    // ─── Navigation ──────────────────────────────────────────────────
    function _nav(path) {
        document.body.style.transition = 'opacity 0.22s';
        document.body.style.opacity = '0';
        setTimeout(() => { window.location.href = path; }, 210);
    }

    // ─── Orb long-press ──────────────────────────────────────────────
    function wireOrb(pageId) {
        const orb = document.getElementById('ztm-orb');
        if (!orb) return;

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
            if (!pressed && pageId !== 'dashboard') _nav('/touch/dashboard.html');
        });
    }

    // ─── Public init ──────────────────────────────────────────────────
    function init(opts = {}) {
        const pageId = opts.page || _detectPage();
        _pageId = pageId;

        applyStoredTheme();
        injectCSS();

        const run = () => {
            removeOldChrome();

            const nav = buildNav(pageId);
            document.body.insertBefore(nav, document.body.firstChild);

            const { backdrop, picker } = buildPicker(pageId);
            const ctxMenu = buildCtxMenu(pageId);
            document.body.appendChild(backdrop);
            document.body.appendChild(picker);
            document.body.appendChild(ctxMenu);

            adjustLayout(pageId);

            tickClock();
            setInterval(tickClock, 15000);

            wireOrb(pageId);

            // Backdrop closes everything
            backdrop.addEventListener('click', closeAll);

            // More button
            document.getElementById('ztm-more-btn')?.addEventListener('click', openPicker);

            // Edit button (dashboard)
            const editBtn = document.getElementById('ztm-edit');
            if (editBtn) {
                let editing = false;
                editBtn.addEventListener('click', () => {
                    editing = !editing;
                    editBtn.textContent = editing ? '✓' : '✏️';
                    editBtn.classList.toggle('editing', editing);
                    editBtn.title = editing ? 'Done editing' : 'Edit / add widgets';
                    if (typeof touchDash !== 'undefined') {
                        touchDash.toggleEdit();
                        if (editing) touchDash.openLibrary();
                    }
                });
            }

            // Theme toggle
            document.getElementById('ztm-theme')?.addEventListener('click', () => {
                const isLight = document.body.classList.contains('light-mode');
                const newTheme = isLight ? 'dark' : 'light';
                localStorage.setItem('zoe_theme', newTheme);
                document.body.classList.remove('dark-mode', 'light-mode');
                document.body.classList.add(isLight ? 'dark-mode' : 'light-mode');
                document.documentElement.style.background = isLight ? '#060610' : '#eef1fa';
                document.documentElement.style.color = isLight ? '' : '#1a1a2e';
                const btn = document.getElementById('ztm-theme');
                if (btn) btn.textContent = isLight ? '🌙' : '☀️';
            });

            // Bell
            document.getElementById('ztm-bell')?.addEventListener('click', () => {
                window.zoeNotifications?.toggle?.();
            });

            refreshBadge();
            setInterval(refreshBadge, 30000);

            window._ztmCloseAll = closeAll;
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', run);
        } else {
            run();
        }
    }

    function _detectPage() {
        const path = window.location.pathname;
        const p = ALL_PAGES.find(pg => path.endsWith(pg.path.replace('/touch/', '')));
        return p ? p.id : 'dashboard';
    }

    return { init, openPicker, closeAll, _nav };
})();

window.TouchMenu = TouchMenu;

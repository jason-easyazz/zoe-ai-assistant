/**
 * TouchMenu — Universal navigation bar for all Zoe touch pages.
 *
 * Uses the touch-premium.css design tokens for light/dark theming.
 * Auto-theme: light 7am–7pm, dark otherwise, with manual override.
 *
 * Usage:
 *   <script src="/touch/js/touch-menu.js"></script>
 *   <script> TouchMenu.init({ page: 'dashboard' }); </script>
 */
'use strict';

const TouchMenu = (() => {
    const NAV_H = 72;

    const PRIMARY = [
        { id: 'dashboard', path: '/touch/dashboard.html',  icon: '🏠', label: 'Home'     },
        { id: 'calendar',  path: '/touch/calendar.html',   icon: '📅', label: 'Calendar' },
        { id: 'lists',     path: '/touch/lists.html',      icon: '☰',  label: 'Lists'    },
        { id: 'chat',      path: '/touch/chat.html',       icon: '💬', label: 'Chat'     },
    ];

    const ALL_PAGES = [
        { id: 'dashboard',  path: '/touch/dashboard.html',  icon: '🏠', label: 'Home'       },
        { id: 'calendar',   path: '/touch/calendar.html',   icon: '📅', label: 'Calendar'   },
        { id: 'lists',      path: '/touch/lists.html',      icon: '✅', label: 'Lists'      },
        { id: 'notes',      path: '/touch/notes.html',      icon: '📝', label: 'Notes'      },
        { id: 'journal',    path: '/touch/journal.html',    icon: '📔', label: 'Journal'    },
        { id: 'chat',       path: '/touch/chat.html',       icon: '💬', label: 'Chat'       },
        { id: 'music',      path: '/touch/music.html',      icon: '🎵', label: 'Music'      },
        { id: 'smarthome',  path: '/touch/smart-home.html', icon: '🏠', label: 'Smart Home' },
        { id: 'people',     path: '/touch/people.html',     icon: '👥', label: 'People'     },
        { id: 'memories',   path: '/touch/memories.html',   icon: '🧠', label: 'Memories'   },
        { id: 'cooking',    path: '/touch/cooking.html',    icon: '🍳', label: 'Cooking'    },
        { id: 'weather',    path: '/touch/weather.html',    icon: '🌤️', label: 'Weather'    },
        { id: 'settings',   path: '/touch/settings.html',   icon: '⚙️', label: 'Settings'   },
    ];

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
    background: var(--nav-bg, rgba(255,255,255,0.80));
    backdrop-filter: blur(var(--blur, 20px)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--blur, 20px)) saturate(160%);
    border-bottom: 1px solid var(--nav-border, rgba(255,255,255,0.30));
    display: flex; align-items: stretch; z-index: 2000;
    padding: 0; user-select: none; -webkit-user-select: none;
    touch-action: manipulation; -webkit-tap-highlight-color: transparent;
    box-sizing: border-box;
    box-shadow: 0 1px 8px rgba(0,0,0,0.06);
}
.dark-mode #ztm-nav, html.dark-mode #ztm-nav {
    box-shadow: none;
}

/* Orb = Home / voice button */
#ztm-orb {
    width: ${NAV_H}px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    border-right: 1px solid var(--glass-border, rgba(0,0,0,0.07));
}
#ztm-orb-dot {
    width: 40px; height: 40px; border-radius: 50%;
    background: var(--primary-gradient, linear-gradient(135deg, #7B61FF, #5AE0E0));
    box-shadow: 0 0 18px rgba(123,97,255,0.50);
    animation: ztm-breathe 4s ease-in-out infinite;
    transition: transform 0.15s; flex-shrink: 0;
}
#ztm-orb:active #ztm-orb-dot { transform: scale(0.84); }
@keyframes ztm-breathe {
    0%,100% { transform: scale(1); }
    50%     { transform: scale(1.07); }
}

/* Primary tab strip */
#ztm-tabs {
    flex: 1; display: flex; align-items: center; min-width: 0; padding: 0 4px;
}
.ztm-tab {
    flex: 1;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 4px; text-decoration: none;
    color: var(--text-muted, #aaa);
    font-size: 11px; font-weight: 500; letter-spacing: 0.1px;
    white-space: nowrap;
    border-radius: 14px;
    transition: color 0.18s, background 0.18s;
    cursor: pointer; min-width: 60px;
    -webkit-tap-highlight-color: transparent;
    padding: 8px 6px; margin: 0 2px;
}
.ztm-tab:active { background: rgba(123,97,255,0.08); transform: scale(0.94); }
.ztm-tab.active {
    color: var(--primary-purple, #7B61FF);
    background: rgba(123,97,255,0.13);
}
.dark-mode .ztm-tab.active { background: rgba(123,97,255,0.22); }
.ztm-tab-icon { font-size: 26px; line-height: 1; }
.ztm-tab-label { font-size: 10px; line-height: 1; font-weight: 600; letter-spacing: 0.2px; }

/* More button */
#ztm-more-btn {
    flex-shrink: 0; min-width: 60px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 4px; color: var(--text-muted, #aaa);
    font-size: 11px; font-weight: 500;
    cursor: pointer; border: none; background: transparent;
    border-radius: 14px;
    padding: 8px 6px; margin: 0 2px;
    -webkit-tap-highlight-color: transparent; transition: color 0.18s, background 0.18s;
    font-family: var(--font-family, inherit);
}
#ztm-more-btn:active { background: rgba(123,97,255,0.08); }
#ztm-more-btn.active { color: var(--primary-purple, #7B61FF); background: rgba(123,97,255,0.13); }
#ztm-more-btn .ztm-tab-icon { font-size: 26px; }

/* Right actions */
#ztm-right {
    display: flex; align-items: center; padding: 0 6px 0 0; gap: 0; flex-shrink: 0;
    border-left: 1px solid var(--glass-border, rgba(0,0,0,0.07));
}
.ztm-btn {
    width: 48px; height: ${NAV_H}px; border-radius: 0; background: transparent;
    border: none; color: var(--text-secondary, #666); font-size: 18px;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    position: relative; flex-shrink: 0; -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, color 0.15s;
}
.ztm-btn:active { background: rgba(123,97,255,0.08); }
#ztm-edit.editing { color: var(--primary-purple, #7B61FF); background: rgba(123,97,255,0.10); }

#ztm-bell-badge {
    position: absolute; top: 12px; right: 10px;
    width: 8px; height: 8px; background: #FF4D6A; border-radius: 50%; display: none;
}

#ztm-clock {
    font-size: 14px; font-weight: 500;
    color: var(--text-secondary, #666);
    padding: 0 12px 0 4px; letter-spacing: 0.3px; flex-shrink: 0; white-space: nowrap;
}

/* ── More / Page Picker Sheet ────────────────────── */
#ztm-backdrop {
    display: none; position: fixed; inset: 0; z-index: 2001;
    background: rgba(0,0,0,0.25);
}
#ztm-backdrop.open { display: block; }
.dark-mode #ztm-backdrop { background: rgba(0,0,0,0.45); }

#ztm-picker {
    display: none; position: fixed;
    top: ${NAV_H}px; left: 0; right: 0;
    background: var(--glass-bg, rgba(255,255,255,0.90));
    backdrop-filter: blur(var(--blur, 20px));
    -webkit-backdrop-filter: blur(var(--blur, 20px));
    border-bottom: 1px solid var(--glass-border, rgba(0,0,0,0.08));
    z-index: 2002; padding: 14px 12px 20px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.10);
}
.dark-mode #ztm-picker {
    background: rgba(15,15,28,0.98);
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
#ztm-picker-label {
    font-size: 11px; font-weight: 700; letter-spacing: 0.8px;
    text-transform: uppercase; color: var(--text-muted, #999); padding: 0 6px 12px;
}
#ztm-picker-grid {
    display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px;
}
.ztm-pg-item {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; padding: 14px 8px; border-radius: 14px; text-decoration: none;
    color: var(--text-primary, #333); font-size: 11px; font-weight: 600;
    letter-spacing: 0.3px; text-transform: uppercase; cursor: pointer;
    background: var(--glass-bg-light, rgba(255,255,255,0.60));
    border: 1px solid var(--glass-border, rgba(0,0,0,0.08));
    transition: background 0.15s; -webkit-tap-highlight-color: transparent; text-align: center;
}
.ztm-pg-item:active, .ztm-pg-item.active {
    background: rgba(123,97,255,0.12); color: var(--primary-purple, #7B61FF);
    border-color: rgba(123,97,255,0.30);
}
.ztm-pg-icon { font-size: 26px; line-height: 1; }
.ztm-pg-label { line-height: 1.2; }

/* ── Orb context menu ─────────────────────────────── */
#ztm-ctx-menu {
    display: none; position: fixed;
    top: ${NAV_H + 8}px; left: 8px;
    background: var(--glass-bg, rgba(255,255,255,0.90));
    backdrop-filter: blur(var(--blur, 20px));
    -webkit-backdrop-filter: blur(var(--blur, 20px));
    border: 1px solid var(--glass-border, rgba(0,0,0,0.08));
    border-radius: 16px; overflow: hidden; z-index: 2003;
    min-width: 180px; box-shadow: 0 8px 32px rgba(0,0,0,0.12);
}
.dark-mode #ztm-ctx-menu {
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
    padding: 16px 20px; font-size: 15px; font-weight: 500;
    color: var(--text-primary, #333); cursor: pointer;
    display: flex; align-items: center; gap: 12px;
    border-bottom: 1px solid var(--glass-border-light, rgba(0,0,0,0.05));
    -webkit-tap-highlight-color: transparent; transition: background 0.12s;
}
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
            a.innerHTML = `<span class="ztm-tab-icon">${p.icon}</span><span class="ztm-tab-label">${p.label}</span>`;
            tabs.appendChild(a);
        });
        const more = document.createElement('button');
        more.id = 'ztm-more-btn';
        more.innerHTML = '<span class="ztm-tab-icon">⋯</span><span class="ztm-tab-label">More</span>';
        if (!PRIMARY.find(p => p.id === pageId)) more.classList.add('active');
        tabs.appendChild(more);
        nav.appendChild(tabs);

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

        const clock = document.createElement('div');
        clock.id = 'ztm-clock';
        right.appendChild(clock);

        nav.appendChild(right);
        return nav;
    }

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
        // Set CSS variable so all pages can reference actual nav height
        document.documentElement.style.setProperty('--ztm-nav-h', NAV_H + 'px');

        if (pageId === 'dashboard') {
            const app = document.getElementById('app');
            if (app) {
                app.style.top    = NAV_H + 'px';
                app.style.height = `calc(100dvh - ${NAV_H}px)`;
                app.style.bottom = '0';
            }
        } else {
            document.body.style.paddingTop = NAV_H + 'px';
        }
        // Screensaver always covers full viewport (above nav)
        const ss = document.getElementById('zoe-ss');
        if (ss) { ss.style.top = '0'; ss.style.height = '100dvh'; }
        const np = document.getElementById('notifications-panel');
        if (np) np.style.top = NAV_H + 'px';
    }

    function removeOldChrome() {
        const bn = document.querySelector('#bottom-nav, nav.bottom-nav, .bottom-nav');
        if (bn) bn.remove();
    }

    function _isDark() {
        return document.documentElement.classList.contains('dark-mode');
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

    function tickClock() {
        const el = document.getElementById('ztm-clock');
        if (el) el.textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    function refreshBadge() {
        const badge = document.getElementById('ztm-bell-badge');
        if (!badge) return;
        try { badge.style.display = (window.zoeNotifications?.getCount?.() || 0) > 0 ? 'block' : 'none'; }
        catch (_) { badge.style.display = 'none'; }
    }

    function _nav(path) {
        document.body.style.transition = 'opacity 0.22s';
        document.body.style.opacity = '0';
        setTimeout(() => { window.location.href = path; }, 210);
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
            if (pageId === 'dashboard') {
                _activateVoice();
            } else {
                _nav('/touch/dashboard.html');
            }
        });
    }

    function init(opts = {}) {
        const pageId = opts.page || _detectPage();
        _pageId = pageId;

        applyAutoTheme();
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

            backdrop.addEventListener('click', closeAll);
            document.getElementById('ztm-more-btn')?.addEventListener('click', openPicker);

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

// Auto-init when included — page is detected from URL
TouchMenu.init();

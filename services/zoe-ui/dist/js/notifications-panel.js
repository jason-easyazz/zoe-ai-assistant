/**
 * Shared notification panel - include on every page.
 * Uses .open class for CSS slide animation (touch-friendly).
 * Requires: common.js (apiRequest, showNotification)
 */
(function() {
    'use strict';

    let notificationsOpen = false;
    let notificationsData = [];

    function injectStyles() {
        if (document.getElementById('zoe-notif-styles')) return;
        const style = document.createElement('style');
        style.id = 'zoe-notif-styles';
        style.textContent = `
            #notifBackdrop {
                display: none;
                position: fixed;
                inset: 0;
                z-index: 9998;
                background: rgba(0,0,0,0.4);
                -webkit-tap-highlight-color: transparent;
            }
            #notifBackdrop.open { display: block; }

            #notificationsPanel[data-zoe-np] {
                position: fixed !important;
                top: 0 !important;
                right: -420px !important;
                width: 400px !important;
                max-width: 100vw !important;
                height: 100vh !important;
                background: rgba(255,255,255,0.97) !important;
                backdrop-filter: blur(24px) !important;
                -webkit-backdrop-filter: blur(24px) !important;
                border-left: 1px solid rgba(0,0,0,0.1) !important;
                z-index: 9999 !important;
                box-shadow: -8px 0 40px rgba(0,0,0,0.18) !important;
                overflow-y: auto !important;
                overflow-x: hidden !important;
                transition: right 0.3s cubic-bezier(0.4,0,0.2,1) !important;
                display: block !important;
                overscroll-behavior: contain;
            }
            html.dark-mode #notificationsPanel[data-zoe-np] {
                background: rgba(20,20,36,0.97) !important;
                border-left-color: rgba(255,255,255,0.08) !important;
            }
            #notificationsPanel[data-zoe-np].open {
                right: 0 !important;
            }
            .zoe-np-header {
                position: sticky;
                top: 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 20px 20px 16px;
                background: inherit;
                border-bottom: 1px solid rgba(0,0,0,0.08);
                z-index: 1;
            }
            html.dark-mode .zoe-np-header {
                border-bottom-color: rgba(255,255,255,0.08);
            }
            .zoe-np-title {
                margin: 0;
                font-size: 20px;
                font-weight: 700;
                color: #1a1a2e;
            }
            html.dark-mode .zoe-np-title { color: #f0f0f8; }
            .zoe-np-close {
                width: 44px;
                height: 44px;
                border-radius: 50%;
                border: none;
                background: rgba(0,0,0,0.06);
                color: #555;
                font-size: 20px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                -webkit-tap-highlight-color: transparent;
                flex-shrink: 0;
            }
            html.dark-mode .zoe-np-close {
                background: rgba(255,255,255,0.1);
                color: #ccc;
            }
            .zoe-np-close:active { background: rgba(0,0,0,0.12); }
            .zoe-np-body { padding: 16px; }
            .zoe-notif-item {
                padding: 14px 16px;
                margin-bottom: 10px;
                background: rgba(0,0,0,0.03);
                border-radius: 12px;
                border-left: 4px solid #7B61FF;
                display: flex;
                gap: 12px;
                align-items: flex-start;
                cursor: pointer;
                -webkit-tap-highlight-color: transparent;
            }
            html.dark-mode .zoe-notif-item {
                background: rgba(255,255,255,0.06);
            }
            .zoe-notif-item:active { opacity: 0.75; }
            .zoe-notif-text { flex: 1; min-width: 0; }
            .zoe-notif-title {
                font-weight: 700;
                font-size: 16px;
                color: #1a1a2e;
                margin-bottom: 4px;
            }
            html.dark-mode .zoe-notif-title { color: #f0f0f8; }
            .zoe-notif-msg {
                font-size: 14px;
                color: #555;
                margin-bottom: 4px;
                line-height: 1.4;
            }
            html.dark-mode .zoe-notif-msg { color: #aaa; }
            .zoe-notif-time { font-size: 12px; color: #888; }
            .zoe-notif-dismiss-btn {
                width: 36px;
                height: 36px;
                border-radius: 50%;
                border: none;
                background: rgba(0,0,0,0.06);
                color: #888;
                font-size: 16px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
                -webkit-tap-highlight-color: transparent;
            }
            .zoe-notif-dismiss-btn:active { background: rgba(255,0,0,0.12); color: #e53935; }
            .zoe-np-empty {
                text-align: center;
                padding: 60px 20px;
                color: #888;
                font-size: 16px;
            }
            .zoe-np-empty-icon { font-size: 48px; margin-bottom: 12px; }
            .zoe-np-install-btn {
                background: #7B61FF;
                color: #fff;
                border: none;
                padding: 12px 20px;
                border-radius: 10px;
                cursor: pointer;
                font-size: 16px;
                font-weight: 700;
                margin-top: 12px;
                width: 100%;
                display: block;
                text-align: center;
                -webkit-tap-highlight-color: transparent;
                min-height: 44px;
            }
            .zoe-np-install-btn:active { opacity: 0.8; transform: scale(0.97); }
            .zoe-notif-item.has-action { border-left-width: 6px; }
            .zoe-np-action-btn {
                background: rgba(0,0,0,0.05);
                color: #333;
                border: 1px solid rgba(0,0,0,0.08);
                padding: 10px 16px;
                border-radius: 10px;
                cursor: pointer;
                font-size: 14px;
                font-weight: 600;
                margin-top: 8px;
                width: 100%;
                display: block;
                text-align: center;
                -webkit-tap-highlight-color: transparent;
                min-height: 40px;
            }
            html.dark-mode .zoe-np-action-btn {
                background: rgba(255,255,255,0.08);
                color: #eee;
                border-color: rgba(255,255,255,0.1);
            }
            .zoe-np-action-btn:active { opacity: 0.85; transform: scale(0.97); }
        `;
        document.head.appendChild(style);
    }

    function ensurePanel() {
        injectStyles();

        // Backdrop overlay (tap outside to close)
        if (!document.getElementById('notifBackdrop')) {
            const bd = document.createElement('div');
            bd.id = 'notifBackdrop';
            bd.addEventListener('click', close);
            bd.addEventListener('touchend', function(e) { e.preventDefault(); close(); });
            document.body.appendChild(bd);
        }

        // Replace any pre-existing panel from the page (e.g. inline dashboard.html panel)
        // so our logic fully controls it and doesn't clash with legacy handlers.
        const existing = document.getElementById('notificationsPanel');
        if (existing && !existing.dataset.zoeNp) {
            existing.remove();
        } else if (existing && existing.dataset.zoeNp) {
            return; // Ours already in place
        }

        const panel = document.createElement('div');
        panel.id = 'notificationsPanel';
        panel.className = 'notifications-panel';
        panel.dataset.zoeNp = '1';
        panel.innerHTML = `
            <div class="zoe-np-header">
                <h3 class="zoe-np-title">🔔 Notifications</h3>
                <button class="zoe-np-close" id="notifCloseBtn" aria-label="Close">✕</button>
            </div>
            <div class="zoe-np-body" id="notificationsList"></div>
        `;
        // Prevent swipe-close on the panel itself reaching the backdrop
        panel.addEventListener('click', e => e.stopPropagation());
        document.body.appendChild(panel);

        document.getElementById('notifCloseBtn')?.addEventListener('click', close);
        document.getElementById('notifCloseBtn')?.addEventListener('touchend', function(e) {
            e.preventDefault();
            close();
        });
    }

    function parseNotifData(n) {
        if (n.data_parsed) return n.data_parsed;
        if (n.data) { try { return JSON.parse(n.data); } catch (e) { return null; } }
        return null;
    }

    function maybeToastOpenClawUpdate() {
        const oc = notificationsData.find(function(n) {
            const d = parseNotifData(n);
            return d && d.kind === 'openclaw_update';
        });
        if (!oc || typeof showNotification !== 'function') return;
        const d = parseNotifData(oc);
        const key = 'zoe_oc_toast_' + (d && d.latest ? d.latest : 'x');
        try {
            if (sessionStorage.getItem(key)) return;
            sessionStorage.setItem(key, '1');
        } catch (e) {}
        showNotification('OpenClaw ' + (d.latest || 'update') + ' available. Open notifications to install.', 'info');
    }

    async function loadNotifications() {
        ensurePanel();
        try {
            const result = await apiRequest('/api/notifications/pending');
            notificationsData = result.notifications || [];
            renderNotifications();
            updateBadge();
            maybeToastOpenClawUpdate();
        } catch (e) {
            console.error('Failed to load notifications:', e);
        }
    }

    function getTypeColor(type) {
        return { info: '#4fc3f7', warning: '#ffa726', success: '#66bb6a', reminder: '#ab47bc', error: '#ef5350' }[type] || '#7B61FF';
    }

    function escapeHtmlNotif(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function formatTime(dateStr) {
        if (!dateStr) return '';
        try {
            const d = new Date(dateStr);
            const diff = Date.now() - d;
            if (diff < 60000) return 'just now';
            if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
            if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
            return d.toLocaleDateString();
        } catch (e) { return dateStr; }
    }

    function renderNotifications() {
        const list = document.getElementById('notificationsList');
        if (!list) return;

        if (!notificationsData.length) {
            list.innerHTML = `<div class="zoe-np-empty"><div class="zoe-np-empty-icon">✅</div>You're all caught up!</div>`;
            return;
        }

        list.innerHTML = '';
        notificationsData.forEach(function(n) {
            const dp = parseNotifData(n);
            const item = document.createElement('div');
            const hasInstall = dp && dp.kind === 'openclaw_update';
            item.className = 'zoe-notif-item' + (hasInstall ? ' has-action' : '');
            item.style.borderLeftColor = getTypeColor(n.type);

            item.innerHTML = `
                <div class="zoe-notif-text">
                    <div class="zoe-notif-title">${escapeHtmlNotif(n.title || 'Notification')}</div>
                    <div class="zoe-notif-msg">${escapeHtmlNotif(n.message || '')}</div>
                    ${hasInstall ? `<button class="zoe-np-install-btn" data-nid="${n.id}">⬇️ Install Update</button>` : ''}
                    ${hasInstall ? `<button class="zoe-np-action-btn" data-open-updates="${n.id}">Open Updates Page</button>` : ''}
                    <div class="zoe-notif-time">${formatTime(n.created_at)}</div>
                </div>
                <button class="zoe-notif-dismiss-btn" data-nid="${n.id}" aria-label="Dismiss">✕</button>
            `;

            item.querySelector('.zoe-notif-dismiss-btn')?.addEventListener('click', function(e) {
                e.stopPropagation();
                dismiss(n.id);
            });

            if (hasInstall) {
                const installHandler = function(e) {
                    e.stopPropagation();
                    installOpenClawUpdate(n.id);
                };
                item.querySelector('.zoe-np-install-btn')?.addEventListener('click', installHandler);
                item.querySelector('.zoe-np-install-btn')?.addEventListener('touchend', function(e) {
                    e.preventDefault();
                    installHandler(e);
                });
                const openBtn = item.querySelector('[data-open-updates]');
                if (openBtn) {
                    const openHandler = function(e) {
                        e.stopPropagation();
                        close();
                        location.href = '/touch/updates.html?highlight=openclaw';
                    };
                    openBtn.addEventListener('click', openHandler);
                    openBtn.addEventListener('touchend', function(e) { e.preventDefault(); openHandler(e); });
                }
            }

            list.appendChild(item);
        });
    }

    async function installOpenClawUpdate(notificationId) {
        try {
            if (typeof showNotification === 'function')
                showNotification('Installing OpenClaw update…', 'info');
            await apiRequest('/api/system/updates/install/openclaw', {
                method: 'POST',
                body: JSON.stringify({ confirm: true }),
            });
            if (typeof showNotification === 'function')
                showNotification('OpenClaw updated.', 'success');
            await dismiss(notificationId);
            // Also clear any sibling openclaw_update notifs (cross-device dedup).
            notificationsData.filter(function(n) {
                const d = parseNotifData(n);
                return d && d.kind === 'openclaw_update';
            }).forEach(function(n) { dismiss(n.id); });
        } catch (e) {
            const msg = (e && e.message) ? e.message : 'Upgrade failed.';
            if (typeof showNotification === 'function') showNotification(msg, 'error');
        }
    }

    function updateBadge() {
        const count = notificationsData.length;
        document.querySelectorAll('.notification-badge, #notificationCount').forEach(function(b) {
            b.textContent = count;
            b.style.display = count > 0 ? '' : 'none';
        });
        const touchBadge = document.getElementById('ztm-bell-badge');
        if (touchBadge) {
            touchBadge.textContent = count > 9 ? '9+' : String(count);
            touchBadge.style.display = count > 0 ? 'block' : 'none';
        }
        // Legacy badge
        const nb = document.getElementById('notif-badge');
        if (nb) {
            nb.textContent = count > 9 ? '9+' : String(count);
            nb.style.display = count > 0 ? 'block' : 'none';
        }

        // Desktop nav bell: auto-decorate with red-dot class + count badge
        // so every page gets the same treatment without editing markup.
        try {
            const bellBtns = document.querySelectorAll(
                'button.notifications-btn[onclick*="openNotifications"],' +
                ' button.notifications-btn[onclick*="toggleNotifications"]'
            );
            bellBtns.forEach(function(btn) {
                // Skip the refresh-button neighbour which shares the class
                if (btn.classList.contains('refresh-btn')) return;
                if (btn.classList.contains('theme-nav-btn')) return;
                if (btn.classList.contains('edit-nav-btn')) return;

                btn.classList.toggle('has-notifications', count > 0);
                // Ensure positioning context for the absolute-positioned badge
                const pos = getComputedStyle(btn).position;
                if (pos === 'static') btn.style.position = 'relative';

                let badge = btn.querySelector(':scope > .bell-count-badge');
                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'bell-count-badge';
                    btn.appendChild(badge);
                }
                if (count > 0) {
                    badge.textContent = count > 9 ? '9+' : String(count);
                    badge.style.display = 'block';
                } else {
                    badge.textContent = '';
                    badge.style.display = 'none';
                }
            });
        } catch (_) { /* non-fatal */ }
    }

    function getCount() { return notificationsData.length; }

    async function dismiss(notificationId) {
        try {
            await apiRequest('/api/notifications/' + notificationId + '/read', { method: 'POST' });
            notificationsData = notificationsData.filter(function(n) { return n.id !== notificationId; });
            renderNotifications();
            updateBadge();
        } catch (e) {
            console.error('Failed to dismiss notification:', e);
        }
    }

    function open() {
        ensurePanel();
        const panel = document.getElementById('notificationsPanel');
        const backdrop = document.getElementById('notifBackdrop');
        if (!panel) return;

        // Position panel below nav bar if present
        const nav = document.getElementById('ztm-nav');
        const navH = nav ? nav.offsetHeight : 0;
        panel.style.top = navH + 'px';
        panel.style.height = `calc(100vh - ${navH}px)`;
        if (backdrop) { backdrop.style.top = navH + 'px'; backdrop.style.height = `calc(100vh - ${navH}px)`; }

        notificationsOpen = true;
        // Force a reflow so the transition fires
        panel.offsetWidth;
        panel.classList.add('open');
        if (backdrop) backdrop.classList.add('open');
        loadNotifications();
    }

    function close() {
        const panel = document.getElementById('notificationsPanel');
        const backdrop = document.getElementById('notifBackdrop');
        if (panel) panel.classList.remove('open');
        if (backdrop) backdrop.classList.remove('open');
        notificationsOpen = false;
    }

    function toggle() {
        if (notificationsOpen) {
            close();
        } else {
            open();
        }
    }

    window.zoeNotifications = {
        load: loadNotifications,
        toggle: toggle,
        open: open,
        close: close,
        dismiss: dismiss,
        refresh: loadNotifications,
        getCount: getCount,
        updateBadge: updateBadge,
        installOpenClawUpdate: installOpenClawUpdate,
    };

    // Legacy alias
    window.openNotifications = open;
    window.closeNotifications = close;

    // ── Real-time push via /ws/push ─────────────────────────────────────
    let _ws = null;
    let _wsRetryMs = 2000;
    function connectPush() {
        try {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${proto}//${location.host}/ws/push?channel=all`;
            _ws = new WebSocket(url);
            _ws.addEventListener('open', function() {
                _wsRetryMs = 2000;
            });
            _ws.addEventListener('message', function(ev) {
                if (!ev || !ev.data) return;
                let msg;
                try { msg = JSON.parse(ev.data); } catch (_) { return; }
                if (!msg || !msg.type) return;
                if (msg.type === 'notification_created' || msg.type === 'notifications_changed') {
                    loadNotifications();
                }
            });
            _ws.addEventListener('close', function() {
                _ws = null;
                setTimeout(connectPush, _wsRetryMs);
                _wsRetryMs = Math.min(_wsRetryMs * 2, 30000);
            });
            _ws.addEventListener('error', function() { try { _ws && _ws.close(); } catch (_) {} });
        } catch (e) {
            setTimeout(connectPush, _wsRetryMs);
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(loadNotifications, 2000);
        // 60s fallback poll; WebSocket pushes drive instant updates.
        setInterval(loadNotifications, 60000);
        connectPush();
    });
})();

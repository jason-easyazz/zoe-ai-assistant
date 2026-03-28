/**
 * Shared notification panel - include on every page.
 * Requires: common.js (apiRequest, showNotification)
 */
(function() {
    'use strict';

    let notificationsOpen = false;
    let notificationsData = [];

    function ensurePanel() {
        if (document.getElementById('notificationsPanel')) return;
        const panel = document.createElement('div');
        panel.id = 'notificationsPanel';
        panel.className = 'notifications-panel';
        panel.style.cssText = 'display:none;position:fixed;top:0;right:0;width:380px;max-width:100vw;height:100vh;background:var(--bg-secondary, #1a1a2e);z-index:10000;box-shadow:-4px 0 20px rgba(0,0,0,0.5);overflow-y:auto;transition:transform 0.3s ease;';
        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:20px;border-bottom:1px solid rgba(255,255,255,0.1);">
                <h3 style="margin:0;color:var(--text-primary,#e0e0e0);">Notifications</h3>
                <button onclick="window.zoeNotifications.close()" style="background:none;border:none;color:var(--text-secondary,#aaa);font-size:1.5em;cursor:pointer;">&times;</button>
            </div>
            <div id="notificationsList" style="padding:10px;"></div>
        `;
        document.body.appendChild(panel);
    }

    async function loadNotifications() {
        ensurePanel();
        try {
            const result = await apiRequest('/api/notifications/pending');
            notificationsData = result.notifications || [];
            renderNotifications();
            updateBadge();
        } catch (e) {
            console.error('Failed to load notifications:', e);
        }
    }

    function renderNotifications() {
        const list = document.getElementById('notificationsList');
        if (!list) return;
        if (!notificationsData.length) {
            list.innerHTML = '<p style="color:var(--text-secondary,#888);text-align:center;padding:40px 20px;">No new notifications</p>';
            return;
        }
        list.innerHTML = notificationsData.map(n => `
            <div class="notification-item" style="padding:12px;margin:8px 0;background:rgba(255,255,255,0.05);border-radius:8px;border-left:3px solid ${getTypeColor(n.type)};">
                <div style="display:flex;justify-content:space-between;align-items:start;">
                    <div>
                        <strong style="color:var(--text-primary,#e0e0e0);">${escapeHtmlNotif(n.title || 'Notification')}</strong>
                        <p style="margin:4px 0 0;color:var(--text-secondary,#aaa);font-size:0.9em;">${escapeHtmlNotif(n.message || '')}</p>
                        <small style="color:var(--text-muted,#666);">${formatTime(n.created_at)}</small>
                    </div>
                    <button onclick="window.zoeNotifications.dismiss('${n.id}')" style="background:none;border:none;color:var(--text-secondary,#888);cursor:pointer;padding:4px 8px;" title="Dismiss">&times;</button>
                </div>
            </div>
        `).join('');
    }

    function getTypeColor(type) {
        const colors = {
            info: '#4fc3f7', warning: '#ffa726', success: '#66bb6a',
            reminder: '#ab47bc', error: '#ef5350',
        };
        return colors[type] || colors.info;
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
            const now = new Date();
            const diff = now - d;
            if (diff < 60000) return 'just now';
            if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
            if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
            return d.toLocaleDateString();
        } catch (e) {
            return dateStr;
        }
    }

    function updateBadge() {
        const badges = document.querySelectorAll('.notification-badge, #notificationCount');
        const count = notificationsData.length;
        badges.forEach(badge => {
            badge.textContent = count;
            badge.style.display = count > 0 ? '' : 'none';
        });
    }

    async function dismiss(notificationId) {
        try {
            await apiRequest(`/api/notifications/${notificationId}/read`, { method: 'POST' });
            notificationsData = notificationsData.filter(n => n.id !== notificationId);
            renderNotifications();
            updateBadge();
        } catch (e) {
            console.error('Failed to dismiss notification:', e);
        }
    }

    function toggle() {
        ensurePanel();
        const panel = document.getElementById('notificationsPanel');
        if (!panel) return;
        notificationsOpen = !notificationsOpen;
        panel.style.display = notificationsOpen ? 'block' : 'none';
        if (notificationsOpen) loadNotifications();
    }

    function close() {
        const panel = document.getElementById('notificationsPanel');
        if (panel) panel.style.display = 'none';
        notificationsOpen = false;
    }

    window.zoeNotifications = {
        load: loadNotifications,
        toggle: toggle,
        close: close,
        dismiss: dismiss,
    };

    window.openNotifications = toggle;

    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(loadNotifications, 2000);
        setInterval(loadNotifications, 60000);
    });
})();

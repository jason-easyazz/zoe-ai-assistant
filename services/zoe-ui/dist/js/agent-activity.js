/**
 * Agent Activity — live agent job feed in the chat sidebar
 * Shows background tasks (Hermes, cron jobs, kanban workers) at the top of
 * #sessionsList with animated status icons, mirroring the Cursor agent panel.
 *
 * Dependencies: window.apiRequest (from chat.html), window.zoeAuth
 * Events consumed: push WS background_task_done / background_task_error
 * Events emitted:  zoe:agent_activity_select  (detail: { taskId, task, result, status })
 */

(function () {
    'use strict';

    const POLL_INTERVAL_MS  = 15_000;
    const MAX_VISIBLE       = 5;   // collapsed max
    const TITLE_MAX_CHARS   = 58;

    function _esc(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    function shortTitle(raw) {
        // Use first non-empty line, strip markdown headers and code fences
        const first = (raw || '').split('\n')
            .map(l => l.replace(/^#+\s*/, '').replace(/^```.*/, '').trim())
            .find(l => l.length > 0) || 'Agent task';
        return first.length > TITLE_MAX_CHARS
            ? first.slice(0, TITLE_MAX_CHARS - 1) + '…'
            : first;
    }

    function timeAgo(isoStr) {
        if (!isoStr) return '';
        const diff = Date.now() - new Date(isoStr).getTime();
        const s = Math.floor(diff / 1000);
        if (s < 60)   return `${s}s ago`;
        const m = Math.floor(s / 60);
        if (m < 60)   return `${m}m ago`;
        const h = Math.floor(m / 60);
        if (h < 24)   return `${h}h ago`;
        return `${Math.floor(h / 24)}d ago`;
    }

    function statusMeta(status) {
        switch (status) {
            case 'running':
            case 'pending':
                return { cls: 'aa-running', icon: '<span class="aa-spinner"></span>', label: _esc(status) };
            case 'done':
                return { cls: 'aa-done',    icon: '✓', label: 'done' };
            case 'error':
                return { cls: 'aa-error',   icon: '✗', label: 'error' };
            case 'blocked':
                return { cls: 'aa-blocked', icon: '⊘', label: 'blocked' };
            default:
                return { cls: 'aa-idle',    icon: '·', label: _esc(status || '?') };
        }
    }

    // ── Core class ────────────────────────────────────────────────────────────

    class AgentActivity {
        constructor() {
            this._tasks      = [];
            this._expanded   = false;
            this._pollTimer  = null;
            this._container  = null;  // the injected wrapper div
        }

        // Called once after DOM is ready
        init() {
            this._injectStyles();
            this._ensureContainer();
            if (this._container && !this._container.dataset.aaBound) {
                this._container.dataset.aaBound = '1';
                this._container.addEventListener('click', (e) => {
                    const row = e.target.closest('.aa-item');
                    if (!row) return;
                    const taskId = row.dataset.taskId;
                    if (taskId) this._select(taskId);
                });
                this._container.addEventListener('keydown', (e) => {
                    if (e.key !== 'Enter') return;
                    const row = e.target.closest('.aa-item');
                    if (!row) return;
                    const taskId = row.dataset.taskId;
                    if (taskId) this._select(taskId);
                });
            }
            this._poll();

            // Refresh on push WS events
            window.addEventListener('zoe:push_event', (e) => {
                const type = e.detail?.type;
                if (type === 'background_task_done' || type === 'background_task_error') {
                    this.refresh();
                }
            });

            // Kick off periodic polling
            this._pollTimer = setInterval(() => this._poll(), POLL_INTERVAL_MS);
        }

        async refresh() {
            await this._poll();
        }

        // ── Data ──────────────────────────────────────────────────────────────

        async _poll() {
            try {
                let data;
                if (window.apiRequest) {
                    data = await window.apiRequest('/api/agent/tasks?limit=30');
                } else {
                    const session = window.zoeAuth?.getCurrentSession();
                    const r = await fetch('/api/agent/tasks?limit=30', {
                        headers: { 'X-Session-ID': session?.session_id || '' }
                    });
                    if (!r.ok) return;
                    data = await r.json();
                }
                this._tasks = data.tasks || [];
                this._render();
            } catch (err) {
                console.warn('[agent-activity] poll error:', err);
            }
        }

        // ── Rendering ─────────────────────────────────────────────────────────

        _ensureContainer() {
            const sessionsList = document.getElementById('sessionsList');
            if (!sessionsList) return;

            // Inject our wrapper as the very first child, before the normal sessions
            this._container = document.createElement('div');
            this._container.id = 'agentActivitySection';
            sessionsList.prepend(this._container);
        }

        _render() {
            // `!this._container` is not enough. chat.html's loadSessions() does
            // sessionsList.innerHTML = ... at three sites (chat.html:3758/:3765/
            // :3788) on every sessions load — including one fired milliseconds
            // after boot. That DETACHES our node without nulling this reference,
            // so the old guard passed and we rendered into an orphan forever:
            // the whole feature was invisible despite working perfectly.
            // isConnected is the check that actually catches it.
            if (!this._container || !this._container.isConnected) {
                this._container = null;
                this._ensureContainer();
                if (!this._container) return;
            }

            const tasks = this._tasks;
            if (!tasks.length) {
                this._container.innerHTML = '';
                return;
            }

            // Sort: running/pending first, then by created_at desc
            const sorted = [...tasks].sort((a, b) => {
                const aActive = a.status === 'running' || a.status === 'pending';
                const bActive = b.status === 'running' || b.status === 'pending';
                if (aActive && !bActive) return -1;
                if (!aActive && bActive) return 1;
                return new Date(b.created_at) - new Date(a.created_at);
            });

            const visible = this._expanded ? sorted : sorted.slice(0, MAX_VISIBLE);
            const hasMore = sorted.length > MAX_VISIBLE;

            const rows = visible.map(t => {
                const { cls, icon, label } = statusMeta(t.status);
                const title = shortTitle(t.task);
                const when  = timeAgo(t.created_at);
                return `
                <div class="session-item aa-item ${cls}"
                     data-task-id="${_esc(t.task_id)}"
                     role="button" tabindex="0">
                    <div class="aa-row-top">
                        <span class="aa-status-icon ${cls}">${icon}</span>
                        <span class="aa-title">${_esc(title)}</span>
                    </div>
                    <div class="session-meta">
                        <span class="aa-badge ${cls}">${label}</span>
                        <span>${when}</span>
                    </div>
                </div>`;
            }).join('');

            const toggleBtn = hasMore ? `
                <button class="aa-toggle" onclick="window.agentActivity._toggle()">
                    ${this._expanded
                        ? '↑ Show less'
                        : `↓ Show ${sorted.length - MAX_VISIBLE} more`}
                </button>` : '';

            this._container.innerHTML = `
                <div class="aa-header">
                    <span class="aa-header-label">Agent Activity</span>
                    ${this._liveCount(sorted) > 0
                        ? `<span class="aa-live-dot" title="Tasks running"></span>`
                        : ''}
                </div>
                ${rows}
                ${toggleBtn}
                <div class="aa-divider"></div>`;
        }

        _liveCount(tasks) {
            return tasks.filter(t => t.status === 'running' || t.status === 'pending').length;
        }

        _toggle() {
            this._expanded = !this._expanded;
            this._render();
        }

        async _select(taskId) {
            const summary = this._tasks.find(t => t.task_id === taskId);
            if (!summary) return;
            let task = summary;
            try {
                if (window.apiRequest) {
                    task = await window.apiRequest(`/api/agent/tasks/${taskId}`);
                } else {
                    const session = window.zoeAuth?.getCurrentSession();
                    const r = await fetch(`/api/agent/tasks/${taskId}`, {
                        headers: { 'X-Session-ID': session?.session_id || '' }
                    });
                    if (r.ok) task = await r.json();
                }
            } catch (err) {
                console.warn('[agent-activity] task detail error:', err);
            }
            document.querySelectorAll('.aa-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`.aa-item[data-task-id="${CSS.escape(taskId)}"]`)?.classList.add('active');
            window.dispatchEvent(new CustomEvent('zoe:agent_activity_select', { detail: task }));
        }

        // ── Styles ────────────────────────────────────────────────────────────

        _injectStyles() {
            if (document.getElementById('aa-styles')) return;
            const style = document.createElement('style');
            style.id = 'aa-styles';
            style.textContent = `
                #agentActivitySection { margin-bottom: 2px; }

                .aa-header {
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    padding: 8px 14px 4px;
                }
                .aa-header-label {
                    font-size: 10px;
                    font-weight: 600;
                    letter-spacing: .07em;
                    text-transform: uppercase;
                    color: var(--clr-text-muted, #94a3b8);
                    flex: 1;
                }
                .aa-live-dot {
                    width: 7px; height: 7px;
                    border-radius: 50%;
                    background: #f59e0b;
                    animation: aa-pulse 1.4s ease-in-out infinite;
                }
                @keyframes aa-pulse {
                    0%,100% { opacity: 1; transform: scale(1); }
                    50%     { opacity: .5; transform: scale(.8); }
                }

                .aa-item { cursor: pointer; user-select: none; }
                .aa-row-top {
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    min-width: 0;
                }
                .aa-title {
                    font-size: 12.5px;
                    font-weight: 500;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    flex: 1;
                    min-width: 0;
                }

                /* Status icon */
                .aa-status-icon {
                    flex-shrink: 0;
                    font-size: 13px;
                    width: 16px;
                    text-align: center;
                }
                .aa-status-icon.aa-done    { color: #10b981; }
                .aa-status-icon.aa-error,
                .aa-status-icon.aa-blocked { color: #ef4444; }
                .aa-status-icon.aa-running,
                .aa-status-icon.aa-idle    { color: #f59e0b; }

                /* Spinner */
                .aa-spinner {
                    display: inline-block;
                    width: 11px; height: 11px;
                    border: 2px solid rgba(245,158,11,.25);
                    border-top-color: #f59e0b;
                    border-radius: 50%;
                    animation: aa-spin .8s linear infinite;
                    vertical-align: middle;
                }
                @keyframes aa-spin { to { transform: rotate(360deg); } }

                /* Badge */
                .aa-badge {
                    font-size: 10px;
                    font-weight: 600;
                    padding: 1px 5px;
                    border-radius: 4px;
                    text-transform: uppercase;
                    letter-spacing: .04em;
                }
                .aa-badge.aa-done    { background: rgba(16,185,129,.12); color: #065f46; }
                .aa-badge.aa-error   { background: rgba(239,68,68,.12);  color: #991b1b; }
                .aa-badge.aa-blocked { background: rgba(239,68,68,.10);  color: #991b1b; }
                .aa-badge.aa-running,
                .aa-badge.aa-idle    { background: rgba(245,158,11,.12); color: #92400e; }

                .aa-toggle {
                    width: 100%;
                    background: none;
                    border: none;
                    cursor: pointer;
                    font-size: 11px;
                    color: var(--clr-text-muted, #94a3b8);
                    padding: 4px 14px;
                    text-align: left;
                }
                .aa-toggle:hover { color: var(--clr-primary, #7B61FF); }

                .aa-divider {
                    height: 1px;
                    margin: 6px 10px 8px;
                    background: var(--clr-border, rgba(0,0,0,.08));
                }`;
            document.head.appendChild(style);
        }
    }

    // ── Boot ─────────────────────────────────────────────────────────────────

    window.agentActivity = new AgentActivity();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => window.agentActivity.init());
    } else {
        window.agentActivity.init();
    }

})();

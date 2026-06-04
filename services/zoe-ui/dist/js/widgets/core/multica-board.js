/**
 * Multica Board Widget
 * Shows active work tracked in Multica tickets.
 * Version: 1.0.0
 */

class MulticaBoardWidget extends WidgetModule {
    constructor() {
        super('multica-board', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 120000 // refresh every 2 minutes
        });
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">Multica Tickets</div>
                <div class="widget-badge" id="mulBoardCount">0</div>
            </div>
            <div class="widget-content">
                <div id="mulBoardContent" class="loading-widget">
                    <div class="spinner"></div>
                    Loading tickets...
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);
        this.loadBoard();

        // Refresh on multica_task_done push event
        document.addEventListener('zoe:multica_task_done', () => this.loadBoard());
    }

    update() {
        this.loadBoard();
    }

    async loadBoard() {
        const content = this.element && this.element.querySelector('#mulBoardContent');
        const badge = this.element && this.element.querySelector('#mulBoardCount');
        try {
            const resp = await fetch('/api/agent/board');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            if (content) content.classList.remove('loading-widget');

            if (!data.available) {
                if (content) content.innerHTML = `<div style="text-align:center;color:#888;font-style:italic;font-size:13px;">Tickets unavailable<br><small>${data.reason || ''}</small></div>`;
                if (badge) badge.textContent = '0';
                return;
            }

            const issues = Array.isArray(data.active) ? data.active : [];
            if (badge) badge.textContent = String(issues.length);

            if (!issues.length) {
                if (content) content.innerHTML = '<div style="text-align:center;color:#888;font-style:italic;font-size:13px;">No active tickets</div>';
                return;
            }

            if (content) {
                content.innerHTML = issues.slice(0, 5).map(issue => {
                    const title = (issue.title || issue.name || 'Untitled').substring(0, 60);
                    const status = issue.status || 'in_progress';
                    const statusColor = status === 'done' ? '#22c55e' : status === 'in_progress' ? '#f59e0b' : '#94a3b8';
                    const assignee = issue.assignee || '';
                    return `
                        <div style="padding:10px 12px;border-radius:8px;margin-bottom:6px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);">
                            <div style="font-size:13px;font-weight:500;margin-bottom:4px;line-height:1.3;">${this._esc(title)}</div>
                            <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#888;">
                                <span style="color:${statusColor};font-weight:600;">${this._esc(status.replace('_', ' '))}</span>
                                ${assignee ? `<span>· ${this._esc(assignee)}</span>` : ''}
                            </div>
                        </div>
                    `;
                }).join('');
            }
        } catch (err) {
            console.error('[MulticaBoardWidget] load failed:', err);
            if (content) {
                content.classList.remove('loading-widget');
                content.innerHTML = '<div style="text-align:center;color:#888;font-style:italic;font-size:13px;">Tickets unavailable</div>';
            }
            if (badge) badge.textContent = '0';
        }
    }

    _esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}

window.MulticaBoardWidget = MulticaBoardWidget;

if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('multica-board', new MulticaBoardWidget());
}

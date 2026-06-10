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

        // Refresh on Multica push events
        document.addEventListener('zoe:multica_task_progress', () => this.loadBoard());
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
                if (content) content.innerHTML = `<div style="text-align:center;color:#888;font-style:italic;font-size:13px;">Tickets unavailable<br><small>${this._esc(data.reason || '')}</small></div>`;
                if (badge) badge.textContent = '0';
                return;
            }

            const groups = data.groups || {};
            const order = ['blocked', 'in_progress', 'in_review', 'todo', 'backlog'];
            const issues = order.flatMap(status =>
                (Array.isArray(groups[status]) ? groups[status] : []).map(issue => ({...issue, status}))
            );
            if (badge) badge.textContent = String(issues.length);

            if (!issues.length) {
                if (content) content.innerHTML = '<div style="text-align:center;color:#888;font-style:italic;font-size:13px;">No open tickets</div>';
                return;
            }

            if (content) {
                content.innerHTML = issues.slice(0, 5).map(issue => {
                    const title = (issue.title || issue.name || 'Untitled').substring(0, 60);
                    const status = issue.status || 'in_progress';
                    const statusColors = {
                        blocked: '#ef4444',
                        in_progress: '#f59e0b',
                        in_review: '#38bdf8',
                        todo: '#a78bfa',
                        backlog: '#94a3b8'
                    };
                    const statusColor = statusColors[status] || '#94a3b8';
                    const phase = issue.phase || (issue.chain && issue.chain.pipeline && issue.chain.pipeline.phase) || '';
                    const blocker = issue.blocker || '';
                    const prUrl = this._safeHttpUrl(issue.pr_url);
                    const childCount = Number(issue.child_count || 0);
                    return `
                        <div style="padding:10px 12px;border-radius:8px;margin-bottom:6px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);">
                            <div style="font-size:13px;font-weight:500;margin-bottom:4px;line-height:1.3;">${this._esc(title)}</div>
                            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:#888;">
                                <span style="color:${statusColor};font-weight:600;">${this._esc(status.replace('_', ' '))}</span>
                                ${phase ? `<span>phase: ${this._esc(phase)}</span>` : ''}
                                ${childCount ? `<span>${childCount} child${childCount === 1 ? '' : 'ren'}</span>` : ''}
                                ${prUrl ? `<a href="${this._esc(prUrl)}" target="_blank" rel="noopener">PR</a>` : ''}
                            </div>
                            ${blocker ? `<div style="margin-top:5px;font-size:11px;color:#fca5a5;line-height:1.25;">${this._esc(blocker.substring(0, 100))}</div>` : ''}
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

    _safeHttpUrl(value) {
        if (!value) return '';
        try {
            const url = new URL(String(value || ''), window.location.origin);
            return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
        } catch (_error) {
            return '';
        }
    }
}

window.MulticaBoardWidget = MulticaBoardWidget;

if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('multica-board', new MulticaBoardWidget());
}

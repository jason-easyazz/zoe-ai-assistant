/**
 * Notes Widget
 * Lists the current user's notes and lets them create / delete from the tile.
 * Version: 2.0.0
 *
 * What changed from 1.0.0:
 *  - Removed the hardcoded mock ("Meeting with team" etc.) — it used to stick on fetch error.
 *  - "+ Add" now actually works (inline compose → POST /api/notes/).
 *  - Requests the canonical trailing-slash URL, skipping FastAPI's 307 redirect.
 *  - Reacts to real-time note_created/updated/deleted events on the notes WS channel.
 *  - Escapes title/content before rendering (XSS-safe).
 *  - Supports per-note delete.
 */

class NotesWidget extends WidgetModule {
    constructor() {
        super('notes', {
            version: '2.0.0',
            defaultSize: 'size-small',
            updateInterval: null
        });
        this._loading = false;
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📝 Notes</div>
                <button class="notes-add-btn" style="background: var(--primary-gradient); border: none; border-radius: 6px; color: white; padding: 4px 8px; font-size: 12px; cursor: pointer;">+ Add</button>
            </div>
            <div class="widget-content">
                <div class="notes-compose" style="display:none; padding:8px; border-bottom:1px solid rgba(0,0,0,0.08);">
                    <input class="notes-title" type="text" placeholder="Title (optional)"
                        style="width:100%; box-sizing:border-box; padding:6px 8px; margin-bottom:4px; border:1px solid rgba(0,0,0,0.15); border-radius:6px; font-size:13px;">
                    <textarea class="notes-content" rows="2" placeholder="Write a note…"
                        style="width:100%; box-sizing:border-box; padding:6px 8px; border:1px solid rgba(0,0,0,0.15); border-radius:6px; font-size:13px; resize:vertical;"></textarea>
                    <div style="display:flex; gap:6px; margin-top:6px; justify-content:flex-end;">
                        <button class="notes-cancel" style="padding:4px 10px; border:1px solid rgba(0,0,0,0.15); background:white; border-radius:6px; font-size:12px; cursor:pointer;">Cancel</button>
                        <button class="notes-save" style="padding:4px 10px; border:none; background:var(--primary-gradient); color:white; border-radius:6px; font-size:12px; cursor:pointer;">Save</button>
                    </div>
                </div>
                <div class="notes-list" style="max-height: 300px; overflow-y: auto;">
                    <div class="notes-empty" style="padding:16px; text-align:center; color:#888; font-size:12px;">Loading…</div>
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);

        const addBtn   = element.querySelector('.notes-add-btn');
        const compose  = element.querySelector('.notes-compose');
        const titleIn  = element.querySelector('.notes-title');
        const contentIn = element.querySelector('.notes-content');
        const saveBtn  = element.querySelector('.notes-save');
        const cancelBtn = element.querySelector('.notes-cancel');

        addBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            const visible = compose.style.display !== 'none';
            compose.style.display = visible ? 'none' : '';
            if (!visible) setTimeout(() => titleIn?.focus(), 0);
        });

        cancelBtn?.addEventListener('click', () => {
            if (titleIn) titleIn.value = '';
            if (contentIn) contentIn.value = '';
            compose.style.display = 'none';
        });

        saveBtn?.addEventListener('click', () => this._createNote(titleIn, contentIn, compose));
        contentIn?.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                this._createNote(titleIn, contentIn, compose);
            }
        });

        // Real-time updates: websocket-sync.js opens /api/notes/ws on the page and calls a
        // global loadNotes() on note_created/updated/deleted. Chain on top of any existing
        // handler so we don't clobber callers registered before us.
        const prev = window.loadNotes;
        window.loadNotes = () => {
            try { if (typeof prev === 'function' && prev !== window.loadNotes) prev(); } catch (_) {}
            this.loadNotes();
        };

        this.loadNotes();
    }

    async loadNotes() {
        if (this._loading) return;
        this._loading = true;
        try {
            // Trailing slash is canonical — without it FastAPI issues a 307 on every request.
            const response = await fetch('/api/notes/', { cache: 'no-store' });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            this.renderNotes(Array.isArray(data.notes) ? data.notes : []);
        } catch (err) {
            console.error('Failed to load notes:', err);
            this.renderError();
        } finally {
            this._loading = false;
        }
    }

    renderNotes(notes) {
        const list = this.element?.querySelector('.notes-list');
        if (!list) return;

        if (notes.length === 0) {
            list.innerHTML = '<div class="notes-empty" style="padding:20px; text-align:center; color:#888; font-style:italic; font-size:12px;">No notes yet</div>';
            return;
        }

        list.innerHTML = notes.map(n => this._renderNote(n)).join('');
        list.querySelectorAll('[data-note-delete]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._deleteNote(btn.getAttribute('data-note-delete'));
            });
        });
    }

    renderError() {
        const list = this.element?.querySelector('.notes-list');
        if (!list) return;
        list.innerHTML = '<div style="padding:20px; text-align:center; color:#c0392b; font-size:12px;">Couldn\'t load notes</div>';
    }

    _renderNote(note) {
        const title = note.title ? this._esc(note.title) : '';
        const content = this._esc(note.content || '');
        const when = this._formatTime(note.updated_at || note.created_at);
        const id = this._esc(note.id || '');
        return `
            <div class="notes-item" data-note-id="${id}" style="padding:8px; border-bottom:1px solid rgba(0,0,0,0.08); font-size:13px; position:relative;">
                ${title ? `<div style="font-weight:600; margin-bottom:2px; padding-right:22px;">${title}</div>` : ''}
                <div style="color:#444; font-size:12px; white-space:pre-wrap; word-break:break-word;${title ? '' : ' padding-right:22px;'}">${content}</div>
                ${when ? `<div style="color:#999; font-size:10px; margin-top:3px;">${this._esc(when)}</div>` : ''}
                <button data-note-delete="${id}" title="Delete"
                    style="position:absolute; top:6px; right:6px; border:none; background:transparent; color:#bbb; cursor:pointer; font-size:14px; line-height:1; padding:2px 4px;">×</button>
            </div>`;
    }

    _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    _formatTime(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso);
            if (isNaN(d.getTime())) return '';
            const now = new Date();
            if (d.toDateString() === now.toDateString()) {
                return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
            }
            const diffDays = Math.floor((now - d) / 86400000);
            if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' });
            return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        } catch (_) { return ''; }
    }

    async _createNote(titleIn, contentIn, compose) {
        const title = (titleIn?.value || '').trim();
        const content = (contentIn?.value || '').trim();
        if (!content) { contentIn?.focus(); return; }
        try {
            const response = await fetch('/api/notes/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title || null, content })
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            if (titleIn) titleIn.value = '';
            if (contentIn) contentIn.value = '';
            if (compose) compose.style.display = 'none';
            // The WS broadcaster will also ping us; reload immediately so the user sees
            // their note without waiting for the round trip.
            await this.loadNotes();
        } catch (err) {
            console.error('Failed to create note:', err);
            alert('Could not save note');
        }
    }

    async _deleteNote(id) {
        if (!id) return;
        if (!confirm('Delete this note?')) return;
        try {
            const response = await fetch('/api/notes/' + encodeURIComponent(id), { method: 'DELETE' });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            await this.loadNotes();
        } catch (err) {
            console.error('Failed to delete note:', err);
            alert('Could not delete note');
        }
    }
}

window.NotesWidget = NotesWidget;

// Legacy auto-registration shim — the manifest-driven registerAllWidgets() is what actually
// instantiates and registers this widget on the dashboard, but keep this here so standalone
// harnesses that look for WidgetRegistry still find it.
if (typeof WidgetRegistry !== 'undefined') {
    try { WidgetRegistry.register('notes', new NotesWidget()); } catch (_) {}
}

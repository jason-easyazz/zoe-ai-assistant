/**
 * Notes Widget
 * Displays quick notes and reminders
 * Version: 1.0.0
 */

class NotesWidget extends WidgetModule {
    constructor() {
        super('notes', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: null // No automatic updates
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📝 Notes</div>
                <button onclick="event.stopPropagation(); addQuickNote()" style="background: var(--primary-gradient); border: none; border-radius: 6px; color: white; padding: 4px 8px; font-size: 12px; cursor: pointer;">+ Add</button>
            </div>
            <div class="widget-content">
                <div id="notesList" style="max-height: 300px; overflow-y: auto;">
                    <div style="padding: 8px; border-bottom: 1px solid rgba(0,0,0,0.1); font-size: 14px;">
                        <div style="font-weight: 600; margin-bottom: 4px;">Meeting with team</div>
                        <div style="color: #666; font-size: 12px;">Discuss project timeline and deliverables</div>
                    </div>
                    <div style="padding: 8px; border-bottom: 1px solid rgba(0,0,0,0.1); font-size: 14px;">
                        <div style="font-weight: 600; margin-bottom: 4px;">Buy groceries</div>
                        <div style="color: #666; font-size: 12px;">Milk, bread, eggs, vegetables</div>
                    </div>
                    <div style="padding: 8px; font-size: 14px;">
                        <div style="font-weight: 600; margin-bottom: 4px;">Call dentist</div>
                        <div style="color: #666; font-size: 12px;">Schedule cleaning appointment</div>
                    </div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        // Load notes from storage or API
        this.loadNotes();
    }
    
    async loadNotes() {
        try {
            const response = await fetch('/api/notes');
            if (response.ok) {
                const data = await response.json();
                this.updateNotes(data.notes || []);
            }
        } catch (error) {
            console.error('Failed to load notes:', error);
            // Keep default display
        }
    }
    
    updateNotes(notes) {
        const notesList = this.element.querySelector('#notesList');
        if (!notesList) return;
        
        if (notes.length === 0) {
            notesList.innerHTML = '<div style="padding: 20px; text-align: center; color: #666; font-style: italic;">No notes yet</div>';
            return;
        }
        
        notesList.innerHTML = notes.map(note => `
            <div style="padding: 8px; border-bottom: 1px solid rgba(0,0,0,0.1); font-size: 14px;">
                <div style="font-weight: 600; margin-bottom: 4px;">${note.title}</div>
                <div style="color: #666; font-size: 12px;">${note.content}</div>
            </div>
        `).join('');
    }
}

// Expose to global scope for WidgetManager
window.NotesWidget = NotesWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('notes', new NotesWidget());
}





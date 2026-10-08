/**
 * Journal Widget
 * Quick journal entry creation - add photo, title, and write
 * Version: 1.0.0
 */

class JournalWidget extends WidgetModule {
    constructor() {
        super('journal', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📔 Quick Journal</div>
                <button id="clearJournalBtn" onclick="event.stopPropagation(); journalWidget.clearForm()" 
                    style="background: rgba(0,0,0,0.05); border: none; border-radius: 6px; color: #666; padding: 4px 8px; font-size: 12px; cursor: pointer; display: none;">
                    Clear
                </button>
            </div>
            <div class="widget-content" style="padding: 16px;">
                <!-- photo upload removed 2026-10-09: no media upload route exists -->
                
                <!-- Title Input -->
                <input type="text" id="journalTitle" placeholder="Title your entry..." 
                    style="width: 100%; padding: 12px; border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; font-size: 16px; font-weight: 600; margin-bottom: 12px; font-family: inherit;">
                
                <!-- Content Area -->
                <textarea id="journalContent" placeholder="What's on your mind?" 
                    style="width: 100%; min-height: 180px; padding: 12px; border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; font-size: 14px; line-height: 1.6; resize: vertical; font-family: inherit; margin-bottom: 12px;"></textarea>
                
                <!-- Save Button -->
                <button id="journalSaveBtn" onclick="event.stopPropagation(); journalWidget.saveEntry()" 
                    style="width: 100%; padding: 14px; background: linear-gradient(135deg, #7B61FF, #5AE0E0); border: none; border-radius: 10px; color: white; font-weight: 600; font-size: 15px; cursor: pointer; transition: all 0.3s; opacity: 0.5;" 
                    disabled>
                    Save Entry
                </button>
                
                <!-- Status Message -->
                <div id="journalStatus" style="margin-top: 8px; text-align: center; font-size: 13px; display: none;"></div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        // Store reference globally
        window.journalWidget = this;
        
        // Set up event listeners
        const titleInput = this.element.querySelector('#journalTitle');
        const contentArea = this.element.querySelector('#journalContent');
        const saveBtn = this.element.querySelector('#journalSaveBtn');
        
        // Enable save button when content exists
        const checkContent = () => {
            const hasContent = titleInput.value.trim() || contentArea.value.trim();
            saveBtn.disabled = !hasContent;
            saveBtn.style.opacity = hasContent ? '1' : '0.5';
            saveBtn.style.cursor = hasContent ? 'pointer' : 'not-allowed';
            
            // Show clear button if any content exists
            const clearBtn = this.element.querySelector('#clearJournalBtn');
            clearBtn.style.display = hasContent ? 'block' : 'none';
        };
        
        titleInput?.addEventListener('input', checkContent);
        contentArea?.addEventListener('input', checkContent);
        
        // Focus on content area for quick entry
        setTimeout(() => contentArea?.focus(), 100);
    }
    
    
    
    
    
    async saveEntry() {
        const titleInput = this.element.querySelector('#journalTitle');
        const contentArea = this.element.querySelector('#journalContent');
        const saveBtn = this.element.querySelector('#journalSaveBtn');
        
        const title = titleInput.value.trim() || 'Untitled Entry';
        const content = contentArea.value.trim();
        
        if (!content) {
            this.showStatus('Please write something!', 'error');
            return;
        }
        
        // Disable button during save
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';
        
        const entryData = {
            title,
            content,
            privacy_level: 'private',
            photos: [],
            tags: []
        };
        
        try {
            // Get user_id
            const session = window.zoeAuth?.getCurrentSession?.();
            const userId = session?.user_info?.user_id || session?.user_id;
            const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
            
            const response = await fetch(`/api/journal/entries${qs}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(window.zoeAuth?.getSession?.() 
                        ? { 'X-Session-ID': window.zoeAuth.getSession() } 
                        : {})
                },
                body: JSON.stringify(entryData)
            });
            
            if (response.ok) {
                this.showStatus('Entry saved! ✨', 'success');
                
                // Clear form after 1 second
                setTimeout(() => {
                    this.clearForm();
                }, 1000);
            } else {
                throw new Error('Save failed');
            }
        } catch (error) {
            console.error('Failed to save entry:', error);
            this.showStatus('Failed to save entry', 'error');
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Entry';
        }
    }
    
    clearForm() {
        const titleInput = this.element.querySelector('#journalTitle');
        const contentArea = this.element.querySelector('#journalContent');
        const saveBtn = this.element.querySelector('#journalSaveBtn');
        const clearBtn = this.element.querySelector('#clearJournalBtn');
        
        if (titleInput) titleInput.value = '';
        if (contentArea) contentArea.value = '';
        
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.style.opacity = '0.5';
            saveBtn.textContent = 'Save Entry';
        }
        
        if (clearBtn) {
            clearBtn.style.display = 'none';
        }
        
        this.hideStatus();
    }
    
    showStatus(message, type = 'info') {
        const statusEl = this.element.querySelector('#journalStatus');
        if (!statusEl) return;
        
        const colors = {
            success: '#10b981',
            error: '#ef4444',
            info: '#3b82f6'
        };
        
        statusEl.textContent = message;
        statusEl.style.color = colors[type] || colors.info;
        statusEl.style.display = 'block';
        
        if (type === 'success' || type === 'error') {
            setTimeout(() => this.hideStatus(), 3000);
        }
    }
    
    hideStatus() {
        const statusEl = this.element.querySelector('#journalStatus');
        if (statusEl) {
            statusEl.style.display = 'none';
        }
    }
}

// Expose to global scope for WidgetManager
window.JournalWidget = JournalWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('journal', new JournalWidget());
}


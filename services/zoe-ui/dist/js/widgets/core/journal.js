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
        this.uploadedPhoto = null;
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
                <!-- Photo Upload Area -->
                <div id="journalPhotoArea" style="margin-bottom: 12px;">
                    <input type="file" id="journalPhotoInput" accept="image/*" style="display: none;">
                    <button id="journalPhotoBtn" onclick="event.stopPropagation(); journalWidget.selectPhoto()" 
                        style="width: 100%; padding: 40px; border: 2px dashed rgba(123, 97, 255, 0.3); border-radius: 12px; background: rgba(123, 97, 255, 0.05); cursor: pointer; transition: all 0.3s; display: flex; flex-direction: column; align-items: center; gap: 8px;">
                        <span style="font-size: 32px;">📷</span>
                        <span style="color: #7B61FF; font-weight: 500; font-size: 14px;">Add Photo</span>
                    </button>
                    <div id="journalPhotoPreview" style="display: none; position: relative; border-radius: 12px; overflow: hidden; margin-bottom: 12px;">
                        <img id="journalPhotoImg" style="width: 100%; height: 200px; object-fit: cover;">
                        <button onclick="event.stopPropagation(); journalWidget.removePhoto()" 
                            style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.7); color: white; border: none; border-radius: 50%; width: 28px; height: 28px; cursor: pointer; display: flex; align-items: center; justify-content: center;">
                            ✕
                        </button>
                    </div>
                </div>
                
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
        const photoInput = this.element.querySelector('#journalPhotoInput');
        const saveBtn = this.element.querySelector('#journalSaveBtn');
        
        // Enable save button when content exists
        const checkContent = () => {
            const hasContent = titleInput.value.trim() || contentArea.value.trim();
            saveBtn.disabled = !hasContent;
            saveBtn.style.opacity = hasContent ? '1' : '0.5';
            saveBtn.style.cursor = hasContent ? 'pointer' : 'not-allowed';
            
            // Show clear button if any content exists
            const clearBtn = this.element.querySelector('#clearJournalBtn');
            clearBtn.style.display = (hasContent || this.uploadedPhoto) ? 'block' : 'none';
        };
        
        titleInput?.addEventListener('input', checkContent);
        contentArea?.addEventListener('input', checkContent);
        
        // Handle photo selection
        photoInput?.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                this.handlePhotoUpload(file);
            }
        });
        
        // Focus on content area for quick entry
        setTimeout(() => contentArea?.focus(), 100);
    }
    
    selectPhoto() {
        this.element.querySelector('#journalPhotoInput')?.click();
    }
    
    handlePhotoUpload(file) {
        // Show preview immediately
        const reader = new FileReader();
        reader.onload = (e) => {
            const photoPreview = this.element.querySelector('#journalPhotoPreview');
            const photoBtn = this.element.querySelector('#journalPhotoBtn');
            const photoImg = this.element.querySelector('#journalPhotoImg');
            
            if (photoPreview && photoBtn && photoImg) {
                photoImg.src = e.target.result;
                photoPreview.style.display = 'block';
                photoBtn.style.display = 'none';
            }
        };
        reader.readAsDataURL(file);
        
        // Upload to server
        this.uploadPhotoToServer(file);
    }
    
    async uploadPhotoToServer(file) {
        const formData = new FormData();
        formData.append('files', file);
        
        // Get user_id from session
        const session = window.zoeAuth?.getCurrentSession?.();
        const userId = session?.user_info?.user_id || session?.user_id || 'default';
        formData.append('user_id', userId);
        
        try {
            const response = await fetch('/api/media/upload', {
                method: 'POST',
                body: formData,
                headers: window.zoeAuth?.getSession?.() 
                    ? { 'X-Session-ID': window.zoeAuth.getSession() } 
                    : {}
            });
            
            if (response.ok) {
                const photos = await response.json();
                this.uploadedPhoto = photos[0];
                this.showStatus('Photo uploaded ✓', 'success');
            } else {
                throw new Error('Upload failed');
            }
        } catch (error) {
            console.error('Photo upload failed:', error);
            this.showStatus('Photo upload failed', 'error');
        }
    }
    
    removePhoto() {
        this.uploadedPhoto = null;
        const photoPreview = this.element.querySelector('#journalPhotoPreview');
        const photoBtn = this.element.querySelector('#journalPhotoBtn');
        const photoInput = this.element.querySelector('#journalPhotoInput');
        
        if (photoPreview && photoBtn) {
            photoPreview.style.display = 'none';
            photoBtn.style.display = 'flex';
        }
        
        if (photoInput) {
            photoInput.value = '';
        }
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
            photos: this.uploadedPhoto ? [this.uploadedPhoto.url] : [],
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
        
        this.removePhoto();
        
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


/**
 * Music Queue Widget
 * Current playback queue with reorder/remove
 * Version: 1.0.0
 */

class MusicQueueWidget extends WidgetModule {
    constructor() {
        super('music-queue', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
        
        this.unsubscribers = [];
        this.draggedItem = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-queue-widget">
                <div class="mq-header">
                    <span class="mq-title">Up Next</span>
                    <div class="mq-actions">
                        <button class="mq-action-btn" id="mq-shuffle" title="Shuffle">🔀</button>
                        <button class="mq-action-btn" id="mq-save" title="Save as Playlist">💾</button>
                        <button class="mq-action-btn mq-clear" id="mq-clear" title="Clear Queue">🗑️</button>
                    </div>
                </div>
                
                <div class="mq-queue-list" id="mq-queue-list">
                    <div class="mq-empty-state">
                        <span class="mq-empty-icon">📋</span>
                        <p>Queue is empty</p>
                    </div>
                </div>
            </div>
            
            <style>
                .music-queue-widget {
                    display: flex;
                    flex-direction: column;
                    padding: 12px;
                    background: rgba(255, 255, 255, 0.9);
                    border-radius: 16px;
                    min-height: 200px;
                    max-height: 100%;
                    overflow: hidden;
                }
                
                .mq-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 12px;
                    flex-shrink: 0;
                }
                
                .mq-title {
                    font-size: 14px;
                    font-weight: 600;
                    color: #333;
                }
                
                .mq-actions {
                    display: flex;
                    gap: 6px;
                }
                
                .mq-action-btn {
                    background: rgba(0, 0, 0, 0.04);
                    border: none;
                    border-radius: 8px;
                    width: 32px;
                    height: 32px;
                    font-size: 14px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .mq-action-btn:hover {
                    background: rgba(123, 97, 255, 0.15);
                }
                
                .mq-action-btn.mq-clear:hover {
                    background: rgba(220, 38, 38, 0.15);
                }
                
                .mq-queue-list {
                    flex: 1;
                    overflow-y: auto;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }
                
                /* Queue track item */
                .mq-track-item {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 10px;
                    background: rgba(0, 0, 0, 0.02);
                    border: 1px solid rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                    cursor: grab;
                    transition: all 0.2s ease;
                }
                
                .mq-track-item:hover {
                    background: rgba(123, 97, 255, 0.08);
                    border-color: rgba(123, 97, 255, 0.2);
                }
                
                .mq-track-item.playing {
                    background: rgba(123, 97, 255, 0.1);
                    border-color: #7B61FF;
                }
                
                .mq-track-item.dragging {
                    opacity: 0.5;
                    cursor: grabbing;
                }
                
                .mq-track-item.drag-over {
                    border-top: 2px solid #7B61FF;
                }
                
                .mq-drag-handle {
                    color: #999;
                    font-size: 14px;
                    cursor: grab;
                    padding: 4px;
                }
                
                .mq-track-art {
                    width: 40px;
                    height: 40px;
                    border-radius: 6px;
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 16px;
                    flex-shrink: 0;
                    overflow: hidden;
                }
                
                .mq-track-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .mq-track-info {
                    flex: 1;
                    min-width: 0;
                }
                
                .mq-track-title {
                    font-size: 13px;
                    font-weight: 500;
                    color: #333;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .mq-track-artist {
                    font-size: 11px;
                    color: #666;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .mq-track-actions {
                    display: flex;
                    gap: 4px;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                
                .mq-track-item:hover .mq-track-actions {
                    opacity: 1;
                }
                
                .mq-track-action {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 6px;
                    width: 26px;
                    height: 26px;
                    font-size: 11px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .mq-track-action:hover {
                    background: rgba(123, 97, 255, 0.2);
                }
                
                .mq-track-action.mq-remove:hover {
                    background: rgba(220, 38, 38, 0.2);
                }
                
                .mq-empty-state {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    color: #666;
                    padding: 30px;
                }
                
                .mq-empty-icon {
                    font-size: 36px;
                    margin-bottom: 12px;
                    opacity: 0.5;
                }
                
                .mq-empty-state p {
                    font-size: 13px;
                    margin: 0;
                }
                
                /* Save playlist modal */
                .mq-modal-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background: rgba(0, 0, 0, 0.5);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    z-index: 10000;
                }
                
                .mq-modal {
                    background: white;
                    border-radius: 16px;
                    padding: 20px;
                    width: 90%;
                    max-width: 320px;
                    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                }
                
                .mq-modal-title {
                    font-size: 16px;
                    font-weight: 600;
                    margin-bottom: 16px;
                    color: #333;
                }
                
                .mq-modal-input {
                    width: 100%;
                    padding: 12px;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    border-radius: 10px;
                    font-size: 14px;
                    margin-bottom: 16px;
                    outline: none;
                    box-sizing: border-box;
                }
                
                .mq-modal-input:focus {
                    border-color: #7B61FF;
                }
                
                .mq-modal-actions {
                    display: flex;
                    gap: 10px;
                    justify-content: flex-end;
                }
                
                .mq-modal-btn {
                    padding: 10px 20px;
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: 500;
                    cursor: pointer;
                    border: none;
                }
                
                .mq-modal-btn.cancel {
                    background: rgba(0, 0, 0, 0.05);
                    color: #666;
                }
                
                .mq-modal-btn.save {
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    color: white;
                }
            </style>
        `;
    }
    
    init(element, options = {}) {
        super.init(element, options);
        this.setupEventListeners();
        this.subscribeToState();
        this.loadQueue();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        el.querySelector('#mq-shuffle')?.addEventListener('click', () => this.shuffleQueue());
        el.querySelector('#mq-save')?.addEventListener('click', () => this.showSavePlaylistModal());
        el.querySelector('#mq-clear')?.addEventListener('click', () => this.clearQueue());
    }
    
    subscribeToState() {
        this.unsubscribers.push(
            MusicState.on('queueUpdated', () => this.loadQueue()),
            MusicState.on('trackChanged', () => this.highlightCurrentTrack())
        );
    }
    
    async loadQueue() {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#mq-queue-list');
        if (!container) return;
        
        const data = await MusicState.apiRequest('/api/music/queue');
        const tracks = data?.queue || [];
        
        if (tracks.length === 0) {
            container.innerHTML = `
                <div class="mq-empty-state">
                    <span class="mq-empty-icon">📋</span>
                    <p>Queue is empty</p>
                </div>
            `;
            return;
        }
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        container.innerHTML = tracks.map((track, index) => {
            const trackId = track.videoId || track.id || track.track_id;
            const artist = track.artist || track.artists?.[0]?.name || '';
            const thumbnail = track.thumbnail_url || track.album_art_url || track.thumbnail || '';
            const isPlaying = trackId === currentTrackId;
            
            return `
                <div class="mq-track-item ${isPlaying ? 'playing' : ''}" 
                     data-track-id="${this.escapeAttr(trackId)}"
                     data-index="${index}"
                     draggable="true">
                    <span class="mq-drag-handle">⋮⋮</span>
                    <div class="mq-track-art">
                        ${thumbnail ? `<img src="${this.escapeAttr(thumbnail)}" alt="" onerror="this.parentElement.innerHTML='🎵'">` : '🎵'}
                    </div>
                    <div class="mq-track-info">
                        <div class="mq-track-title">${this.escapeHtml(track.title || 'Unknown')}</div>
                        <div class="mq-track-artist">${this.escapeHtml(artist)}</div>
                    </div>
                    <div class="mq-track-actions">
                        <button class="mq-track-action mq-play" title="Play">▶</button>
                        <button class="mq-track-action mq-remove" title="Remove">×</button>
                    </div>
                </div>
            `;
        }).join('');
        
        this.bindQueueEvents();
    }
    
    bindQueueEvents() {
        const el = this.element;
        if (!el) return;
        
        el.querySelectorAll('.mq-track-item').forEach(item => {
            const trackId = item.dataset.trackId;
            
            // Play on click
            item.addEventListener('click', (e) => {
                if (e.target.closest('.mq-track-actions') || e.target.closest('.mq-drag-handle')) return;
                MusicState.play(trackId);
            });
            
            // Play button
            item.querySelector('.mq-play')?.addEventListener('click', (e) => {
                e.stopPropagation();
                MusicState.play(trackId);
            });
            
            // Remove button - pass position to handle duplicates correctly
            item.querySelector('.mq-remove')?.addEventListener('click', async (e) => {
                e.stopPropagation();
                const position = parseInt(item.dataset.index);
                await this.removeFromQueue(trackId, position);
            });
            
            // Drag and drop
            item.addEventListener('dragstart', (e) => {
                this.draggedItem = item;
                item.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            
            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
                el.querySelectorAll('.mq-track-item').forEach(i => i.classList.remove('drag-over'));
                this.draggedItem = null;
            });
            
            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (this.draggedItem && this.draggedItem !== item) {
                    item.classList.add('drag-over');
                }
            });
            
            item.addEventListener('dragleave', () => {
                item.classList.remove('drag-over');
            });
            
            item.addEventListener('drop', async (e) => {
                e.preventDefault();
                item.classList.remove('drag-over');
                
                if (this.draggedItem && this.draggedItem !== item) {
                    const fromIndex = parseInt(this.draggedItem.dataset.index);
                    const toIndex = parseInt(item.dataset.index);
                    await this.reorderQueue(fromIndex, toIndex);
                }
            });
        });
    }
    
    async removeFromQueue(trackId, position) {
        // Pass position to correctly handle duplicate tracks
        const url = position !== undefined 
            ? `/api/music/queue/${trackId}?position=${position}`
            : `/api/music/queue/${trackId}`;
        await MusicState.apiRequest(url, { method: 'DELETE' });
        this.loadQueue();
        this.showToast('Removed from queue');
    }
    
    async reorderQueue(fromIndex, toIndex) {
        await MusicState.apiRequest('/api/music/queue/reorder', {
            method: 'POST',
            body: { from_position: fromIndex, to_position: toIndex }
        });
        this.loadQueue();
    }
    
    async shuffleQueue() {
        const data = await MusicState.apiRequest('/api/music/queue');
        const tracks = data?.queue || [];
        
        if (tracks.length < 2) return;
        
        // Fisher-Yates shuffle
        for (let i = tracks.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
        }
        
        // Clear and re-add in new order
        await MusicState.apiRequest('/api/music/queue/clear', { method: 'POST' });
        
        for (const track of tracks) {
            const trackId = track.videoId || track.id || track.track_id;
            await MusicState.addToQueue(trackId, track);
        }
        
        this.loadQueue();
        this.showToast('Queue shuffled');
    }
    
    async clearQueue() {
        await MusicState.apiRequest('/api/music/queue/clear', { method: 'POST' });
        this.loadQueue();
        this.showToast('Queue cleared');
    }
    
    showSavePlaylistModal() {
        const overlay = document.createElement('div');
        overlay.className = 'mq-modal-overlay';
        overlay.innerHTML = `
            <div class="mq-modal">
                <div class="mq-modal-title">Save Queue as Playlist</div>
                <input type="text" class="mq-modal-input" placeholder="Playlist name...">
                <div class="mq-modal-actions">
                    <button class="mq-modal-btn cancel">Cancel</button>
                    <button class="mq-modal-btn save">Save</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
        
        const input = overlay.querySelector('.mq-modal-input');
        input.focus();
        
        const close = () => overlay.remove();
        
        overlay.querySelector('.cancel').addEventListener('click', close);
        overlay.querySelector('.save').addEventListener('click', async () => {
            const name = input.value.trim();
            if (name) {
                await this.saveQueueAsPlaylist(name);
            }
            close();
        });
        input.addEventListener('keypress', async (e) => {
            if (e.key === 'Enter') {
                const name = input.value.trim();
                if (name) {
                    await this.saveQueueAsPlaylist(name);
                }
                close();
            }
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });
    }
    
    async saveQueueAsPlaylist(name) {
        const data = await MusicState.apiRequest('/api/music/queue');
        const tracks = data?.queue || [];
        
        if (tracks.length === 0) {
            this.showToast('Queue is empty');
            return;
        }
        
        try {
            let playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            const newPlaylist = {
                id: 'local-' + Date.now(),
                title: name,
                thumbnail: tracks[0]?.thumbnail_url || tracks[0]?.album_art_url || '',
                trackCount: tracks.length,
                tracks: tracks,
                savedAt: Date.now()
            };
            playlists.unshift(newPlaylist);
            localStorage.setItem('zoe_saved_playlists', JSON.stringify(playlists));
            MusicState.emit('playlistSaved', newPlaylist);
            this.showToast('Playlist saved');
        } catch (e) {
            console.error('Failed to save playlist:', e);
            this.showToast('Failed to save');
        }
    }
    
    highlightCurrentTrack() {
        const el = this.element;
        if (!el) return;
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        el.querySelectorAll('.mq-track-item').forEach(item => {
            item.classList.toggle('playing', item.dataset.trackId === currentTrackId);
        });
    }
    
    showToast(message) {
        const existing = document.querySelector('.mq-toast');
        if (existing) existing.remove();
        
        const toast = document.createElement('div');
        toast.className = 'mq-toast';
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px 20px;
            border-radius: 20px;
            font-size: 13px;
            z-index: 10000;
            animation: fadeInOut 2s ease forwards;
        `;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 2000);
    }
    
    escapeHtml(text) {
        if (!text || typeof text !== 'string') return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    escapeAttr(text) {
        if (!text || typeof text !== 'string') return '';
        return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    
    destroy() {
        this.unsubscribers.forEach(unsub => unsub());
        this.unsubscribers = [];
        super.destroy();
    }
}

// Export to window for widget system
window.MusicQueueWidget = MusicQueueWidget;

// Widget is registered via manifest metadata (lazy loading)
// No auto-registration needed

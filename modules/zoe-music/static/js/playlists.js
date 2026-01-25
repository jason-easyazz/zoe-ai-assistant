/**
 * Music Playlists Widget
 * User playlists and YouTube playlists management
 * Version: 1.0.0
 */

class MusicPlaylistsWidget extends WidgetModule {
    constructor() {
        super('music-playlists', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
        
        this.unsubscribers = [];
        this.expandedPlaylists = new Set();
        this.currentTab = 'local'; // 'local' or 'youtube'
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-playlists-widget">
                <div class="mpl-header">
                    <div class="mpl-tabs">
                        <button class="mpl-tab active" data-tab="local">Your Playlists</button>
                        <button class="mpl-tab" data-tab="youtube">YouTube</button>
                    </div>
                    <button class="mpl-create-btn" id="mpl-create" title="New Playlist">+</button>
                </div>
                
                <div class="mpl-content">
                    <!-- Local Playlists Tab -->
                    <div class="mpl-tab-content active" data-tab="local">
                        <div class="mpl-list" id="mpl-local-list">
                            <div class="mpl-empty-state">
                                <span class="mpl-empty-icon">📁</span>
                                <p>No saved playlists</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- YouTube Playlists Tab -->
                    <div class="mpl-tab-content" data-tab="youtube">
                        <div class="mpl-list" id="mpl-youtube-list">
                            <div class="mpl-loading">
                                <div class="mpl-spinner"></div>
                                Loading playlists...
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <style>
                .music-playlists-widget {
                    display: flex;
                    flex-direction: column;
                    padding: 12px;
                    background: rgba(255, 255, 255, 0.9);
                    border-radius: 16px;
                    min-height: 200px;
                    max-height: 100%;
                    overflow: hidden;
                }
                
                .mpl-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 12px;
                    flex-shrink: 0;
                }
                
                .mpl-tabs {
                    display: flex;
                    gap: 4px;
                    padding: 4px;
                    background: rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                }
                
                .mpl-tab {
                    background: transparent;
                    border: none;
                    padding: 8px 14px;
                    font-size: 12px;
                    font-weight: 500;
                    color: #666;
                    cursor: pointer;
                    border-radius: 8px;
                    transition: all 0.2s ease;
                }
                
                .mpl-tab:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .mpl-tab.active {
                    background: white;
                    color: #7B61FF;
                    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
                }
                
                .mpl-create-btn {
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    border: none;
                    color: white;
                    width: 32px;
                    height: 32px;
                    border-radius: 8px;
                    font-size: 18px;
                    font-weight: 500;
                    cursor: pointer;
                    transition: transform 0.2s ease;
                }
                
                .mpl-create-btn:hover {
                    transform: scale(1.1);
                }
                
                .mpl-content {
                    flex: 1;
                    overflow: hidden;
                    display: flex;
                    flex-direction: column;
                }
                
                .mpl-tab-content {
                    display: none;
                    flex: 1;
                    overflow-y: auto;
                    flex-direction: column;
                }
                
                .mpl-tab-content.active {
                    display: flex;
                }
                
                .mpl-list {
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }
                
                /* Playlist item */
                .mpl-item {
                    background: rgba(0, 0, 0, 0.02);
                    border: 1px solid rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                    overflow: hidden;
                }
                
                .mpl-item-header {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 10px;
                    cursor: pointer;
                    transition: background 0.2s ease;
                }
                
                .mpl-item-header:hover {
                    background: rgba(123, 97, 255, 0.08);
                }
                
                .mpl-expand {
                    font-size: 12px;
                    color: #666;
                    transition: transform 0.2s ease;
                }
                
                .mpl-item.expanded .mpl-expand {
                    transform: rotate(90deg);
                }
                
                .mpl-item-art {
                    width: 44px;
                    height: 44px;
                    border-radius: 8px;
                    background: linear-gradient(135deg, #FF6B6B 0%, #FFE66D 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 18px;
                    flex-shrink: 0;
                    overflow: hidden;
                }
                
                .mpl-item-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .mpl-item-info {
                    flex: 1;
                    min-width: 0;
                }
                
                .mpl-item-title {
                    font-size: 13px;
                    font-weight: 500;
                    color: #333;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .mpl-item-meta {
                    font-size: 11px;
                    color: #666;
                }
                
                .mpl-item-actions {
                    display: flex;
                    gap: 4px;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                
                .mpl-item-header:hover .mpl-item-actions {
                    opacity: 1;
                }
                
                .mpl-action {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 10px;
                    cursor: pointer;
                    white-space: nowrap;
                }
                
                .mpl-action:hover {
                    background: rgba(123, 97, 255, 0.2);
                }
                
                .mpl-action.delete:hover {
                    background: rgba(220, 38, 38, 0.2);
                }
                
                /* Expanded tracks */
                .mpl-tracks {
                    display: none;
                    padding: 0 10px 10px;
                    flex-direction: column;
                    gap: 4px;
                    max-height: 200px;
                    overflow-y: auto;
                }
                
                .mpl-item.expanded .mpl-tracks {
                    display: flex;
                }
                
                .mpl-track {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px;
                    background: rgba(255, 255, 255, 0.5);
                    border-radius: 6px;
                    font-size: 12px;
                    cursor: pointer;
                }
                
                .mpl-track:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .mpl-track-icon {
                    font-size: 14px;
                    opacity: 0.6;
                }
                
                .mpl-track-title {
                    flex: 1;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }
                
                .mpl-empty-state {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    color: #666;
                    padding: 30px;
                }
                
                .mpl-empty-icon {
                    font-size: 36px;
                    margin-bottom: 12px;
                    opacity: 0.5;
                }
                
                .mpl-empty-state p {
                    font-size: 13px;
                    margin: 0;
                }
                
                .mpl-loading {
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    padding: 30px;
                    color: #666;
                    font-size: 13px;
                    gap: 12px;
                }
                
                .mpl-spinner {
                    width: 28px;
                    height: 28px;
                    border: 3px solid rgba(0, 0, 0, 0.08);
                    border-top-color: #7B61FF;
                    border-radius: 50%;
                    animation: mpl-spin 1s linear infinite;
                }
                
                @keyframes mpl-spin {
                    to { transform: rotate(360deg); }
                }
                
                /* Modal */
                .mpl-modal-overlay {
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
                
                .mpl-modal {
                    background: white;
                    border-radius: 16px;
                    padding: 20px;
                    width: 90%;
                    max-width: 320px;
                    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                }
                
                .mpl-modal-title {
                    font-size: 16px;
                    font-weight: 600;
                    margin-bottom: 16px;
                    color: #333;
                }
                
                .mpl-modal-input {
                    width: 100%;
                    padding: 12px;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    border-radius: 10px;
                    font-size: 14px;
                    margin-bottom: 16px;
                    outline: none;
                    box-sizing: border-box;
                }
                
                .mpl-modal-input:focus {
                    border-color: #7B61FF;
                }
                
                .mpl-modal-actions {
                    display: flex;
                    gap: 10px;
                    justify-content: flex-end;
                }
                
                .mpl-modal-btn {
                    padding: 10px 20px;
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: 500;
                    cursor: pointer;
                    border: none;
                }
                
                .mpl-modal-btn.cancel {
                    background: rgba(0, 0, 0, 0.05);
                    color: #666;
                }
                
                .mpl-modal-btn.save {
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
        this.loadLocalPlaylists();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        // Tab switching
        el.querySelectorAll('.mpl-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.dataset.tab;
                this.switchTab(tabName);
            });
        });
        
        // Create playlist button
        el.querySelector('#mpl-create')?.addEventListener('click', () => this.showCreateModal());
    }
    
    subscribeToState() {
        this.unsubscribers.push(
            MusicState.on('playlistSaved', () => this.loadLocalPlaylists())
        );
    }
    
    switchTab(tabName) {
        const el = this.element;
        if (!el) return;
        
        this.currentTab = tabName;
        
        // Update tab buttons
        el.querySelectorAll('.mpl-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        
        // Update tab content
        el.querySelectorAll('.mpl-tab-content').forEach(content => {
            content.classList.toggle('active', content.dataset.tab === tabName);
        });
        
        // Load content
        if (tabName === 'youtube') {
            this.loadYouTubePlaylists();
        } else {
            this.loadLocalPlaylists();
        }
    }
    
    loadLocalPlaylists() {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#mpl-local-list');
        if (!container) return;
        
        let playlists = [];
        try {
            playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
        } catch (e) {
            console.error('Failed to load playlists:', e);
        }
        
        if (playlists.length === 0) {
            container.innerHTML = `
                <div class="mpl-empty-state">
                    <span class="mpl-empty-icon">📁</span>
                    <p>No saved playlists</p>
                    <p style="font-size: 11px; color: #999; margin-top: 8px;">Create one or save your queue</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = playlists.map((playlist, index) => {
            const isExpanded = this.expandedPlaylists.has(playlist.id);
            
            return `
                <div class="mpl-item ${isExpanded ? 'expanded' : ''}" 
                     data-playlist-id="${this.escapeAttr(playlist.id)}"
                     data-type="local">
                    <div class="mpl-item-header">
                        <span class="mpl-expand">▶</span>
                        <div class="mpl-item-art">
                            ${playlist.thumbnail ? `<img src="${this.escapeAttr(playlist.thumbnail)}" alt="" onerror="this.parentElement.innerHTML='📋'">` : '📋'}
                        </div>
                        <div class="mpl-item-info">
                            <div class="mpl-item-title">${this.escapeHtml(playlist.title || 'Untitled')}</div>
                            <div class="mpl-item-meta">${playlist.trackCount || 0} tracks</div>
                        </div>
                        <div class="mpl-item-actions">
                            <button class="mpl-action play" title="Play All">▶ Play</button>
                            <button class="mpl-action add" title="Add to Queue">+ Add</button>
                            <button class="mpl-action delete" title="Delete">🗑️</button>
                        </div>
                    </div>
                    <div class="mpl-tracks" id="mpl-tracks-${this.escapeAttr(playlist.id)}">
                        ${this.renderPlaylistTracks(playlist)}
                    </div>
                </div>
            `;
        }).join('');
        
        this.bindPlaylistEvents(container);
    }
    
    async loadYouTubePlaylists() {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#mpl-youtube-list');
        if (!container) return;
        
        container.innerHTML = `
            <div class="mpl-loading">
                <div class="mpl-spinner"></div>
                Loading playlists...
            </div>
        `;
        
        const data = await MusicState.apiRequest('/api/music/playlists');
        const playlists = data?.playlists || [];
        
        if (playlists.length === 0) {
            container.innerHTML = `
                <div class="mpl-empty-state">
                    <span class="mpl-empty-icon">📺</span>
                    <p>No YouTube playlists found</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = playlists.map((playlist) => {
            const playlistId = playlist.playlistId || playlist.id;
            const isExpanded = this.expandedPlaylists.has(playlistId);
            const thumbnail = playlist.thumbnails?.[0]?.url || playlist.thumbnail || '';
            
            return `
                <div class="mpl-item ${isExpanded ? 'expanded' : ''}" 
                     data-playlist-id="${this.escapeAttr(playlistId)}"
                     data-type="youtube">
                    <div class="mpl-item-header">
                        <span class="mpl-expand">▶</span>
                        <div class="mpl-item-art">
                            ${thumbnail ? `<img src="${this.escapeAttr(thumbnail)}" alt="" onerror="this.parentElement.innerHTML='📺'">` : '📺'}
                        </div>
                        <div class="mpl-item-info">
                            <div class="mpl-item-title">${this.escapeHtml(playlist.title || 'Untitled')}</div>
                            <div class="mpl-item-meta">${playlist.count || ''} tracks</div>
                        </div>
                        <div class="mpl-item-actions">
                            <button class="mpl-action play" title="Play All">▶ Play</button>
                            <button class="mpl-action add" title="Add to Queue">+ Add</button>
                            <button class="mpl-action save" title="Save to Library">💾</button>
                        </div>
                    </div>
                    <div class="mpl-tracks" id="mpl-tracks-${this.escapeAttr(playlistId)}">
                        <div class="mpl-loading">Loading tracks...</div>
                    </div>
                </div>
            `;
        }).join('');
        
        this.bindPlaylistEvents(container);
    }
    
    renderPlaylistTracks(playlist) {
        const tracks = playlist.tracks || [];
        if (tracks.length === 0) {
            return '<div class="mpl-loading">No tracks</div>';
        }
        
        return tracks.slice(0, 10).map(track => {
            const trackId = track.videoId || track.id || track.track_id;
            return `
                <div class="mpl-track" data-track-id="${this.escapeAttr(trackId)}">
                    <span class="mpl-track-icon">🎵</span>
                    <span class="mpl-track-title">${this.escapeHtml(track.title || 'Unknown')}</span>
                </div>
            `;
        }).join('') + (tracks.length > 10 ? `<div class="mpl-loading">+${tracks.length - 10} more</div>` : '');
    }
    
    bindPlaylistEvents(container) {
        container.querySelectorAll('.mpl-item').forEach(item => {
            const playlistId = item.dataset.playlistId;
            const type = item.dataset.type;
            const header = item.querySelector('.mpl-item-header');
            
            // Toggle expand
            header?.addEventListener('click', (e) => {
                if (e.target.closest('.mpl-item-actions')) return;
                
                if (this.expandedPlaylists.has(playlistId)) {
                    this.expandedPlaylists.delete(playlistId);
                    item.classList.remove('expanded');
                } else {
                    this.expandedPlaylists.add(playlistId);
                    item.classList.add('expanded');
                    if (type === 'youtube') {
                        this.loadYouTubePlaylistTracks(playlistId, item);
                    }
                }
            });
            
            // Play button
            item.querySelector('.mpl-action.play')?.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.playPlaylist(playlistId, type);
            });
            
            // Add to queue button
            item.querySelector('.mpl-action.add')?.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.addPlaylistToQueue(playlistId, type);
            });
            
            // Delete button (local only)
            item.querySelector('.mpl-action.delete')?.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deletePlaylist(playlistId);
            });
            
            // Save button (youtube only)
            item.querySelector('.mpl-action.save')?.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.saveYouTubePlaylist(playlistId);
            });
        });
        
        // Track click events
        container.querySelectorAll('.mpl-track').forEach(track => {
            track.addEventListener('click', async (e) => {
                const trackId = track.dataset.trackId;
                console.log('🎵 Playlist track clicked:', trackId);
                if (trackId) {
                    try {
                        await MusicState.play(trackId);
                    } catch (error) {
                        console.error('❌ Play failed:', error);
                        alert(`Failed to play track: ${error.message}`);
                    }
                }
            });
        });
    }
    
    async loadYouTubePlaylistTracks(playlistId, item) {
        const tracksContainer = item.querySelector('.mpl-tracks');
        if (!tracksContainer) return;
        
        tracksContainer.innerHTML = '<div class="mpl-loading">Loading tracks...</div>';
        
        const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
        if (!playlist?.tracks) {
            tracksContainer.innerHTML = '<div class="mpl-loading">Could not load tracks</div>';
            return;
        }
        
        // Cache tracks
        playlist.tracks.forEach(track => {
            const id = track.videoId || track.id;
            MusicState.state.trackCache[id] = {
                id,
                title: track.title || 'Unknown',
                artist: track.artist || track.artists?.[0]?.name || '',
                thumbnail: track.thumbnail_url || track.thumbnails?.[0]?.url || ''
            };
        });
        
        tracksContainer.innerHTML = this.renderPlaylistTracks(playlist);
        
        // Bind track click events
        tracksContainer.querySelectorAll('.mpl-track').forEach(track => {
            track.addEventListener('click', async () => {
                const trackId = track.dataset.trackId;
                console.log('🎵 YouTube playlist track clicked:', trackId);
                if (trackId) {
                    try {
                        await MusicState.play(trackId);
                    } catch (error) {
                        console.error('❌ Play failed:', error);
                        alert(`Failed to play track: ${error.message}`);
                    }
                }
            });
        });
    }
    
    async playPlaylist(playlistId, type) {
        let tracks = [];
        
        if (type === 'local') {
            const playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            const playlist = playlists.find(p => p.id === playlistId);
            tracks = playlist?.tracks || [];
        } else {
            const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
            tracks = playlist?.tracks || [];
        }
        
        if (tracks.length === 0) {
            this.showToast('Playlist is empty');
            return;
        }
        
        // Clear queue and add all tracks
        await MusicState.apiRequest('/api/music/queue/clear', { method: 'POST' });
        
        for (const track of tracks) {
            const trackId = track.videoId || track.id || track.track_id;
            await MusicState.addToQueue(trackId, track);
        }
        
        // Play first track
        const firstTrack = tracks[0];
        const firstId = firstTrack.videoId || firstTrack.id || firstTrack.track_id;
        MusicState.play(firstId);
        
        this.showToast('Playlist loaded');
    }
    
    async addPlaylistToQueue(playlistId, type) {
        let tracks = [];
        
        if (type === 'local') {
            const playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            const playlist = playlists.find(p => p.id === playlistId);
            tracks = playlist?.tracks || [];
        } else {
            const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
            tracks = playlist?.tracks || [];
        }
        
        if (tracks.length === 0) {
            this.showToast('Playlist is empty');
            return;
        }
        
        for (const track of tracks) {
            const trackId = track.videoId || track.id || track.track_id;
            await MusicState.addToQueue(trackId, track);
        }
        
        this.showToast(`Added ${tracks.length} tracks to queue`);
    }
    
    deletePlaylist(playlistId) {
        try {
            let playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            playlists = playlists.filter(p => p.id !== playlistId);
            localStorage.setItem('zoe_saved_playlists', JSON.stringify(playlists));
            this.expandedPlaylists.delete(playlistId);
            this.loadLocalPlaylists();
            this.showToast('Playlist deleted');
        } catch (e) {
            console.error('Failed to delete playlist:', e);
        }
    }
    
    async saveYouTubePlaylist(playlistId) {
        const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
        if (!playlist) {
            this.showToast('Could not load playlist');
            return;
        }
        
        try {
            let playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            
            // Check if already saved
            if (playlists.find(p => p.id === playlistId)) {
                this.showToast('Already saved');
                return;
            }
            
            const newPlaylist = {
                id: playlistId,
                title: playlist.title || 'YouTube Playlist',
                thumbnail: playlist.thumbnails?.[0]?.url || '',
                trackCount: playlist.tracks?.length || 0,
                tracks: playlist.tracks || [],
                savedAt: Date.now(),
                source: 'youtube'
            };
            
            playlists.unshift(newPlaylist);
            localStorage.setItem('zoe_saved_playlists', JSON.stringify(playlists));
            MusicState.emit('playlistSaved', newPlaylist);
            this.showToast('Saved to library');
        } catch (e) {
            console.error('Failed to save playlist:', e);
            this.showToast('Failed to save');
        }
    }
    
    showCreateModal() {
        const overlay = document.createElement('div');
        overlay.className = 'mpl-modal-overlay';
        overlay.innerHTML = `
            <div class="mpl-modal">
                <div class="mpl-modal-title">Create New Playlist</div>
                <input type="text" class="mpl-modal-input" placeholder="Playlist name...">
                <div class="mpl-modal-actions">
                    <button class="mpl-modal-btn cancel">Cancel</button>
                    <button class="mpl-modal-btn save">Create</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
        
        const input = overlay.querySelector('.mpl-modal-input');
        input.focus();
        
        const close = () => overlay.remove();
        
        overlay.querySelector('.cancel').addEventListener('click', close);
        overlay.querySelector('.save').addEventListener('click', () => {
            const name = input.value.trim();
            if (name) {
                this.createEmptyPlaylist(name);
            }
            close();
        });
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const name = input.value.trim();
                if (name) {
                    this.createEmptyPlaylist(name);
                }
                close();
            }
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });
    }
    
    createEmptyPlaylist(name) {
        try {
            let playlists = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            const newPlaylist = {
                id: 'local-' + Date.now(),
                title: name,
                thumbnail: '',
                trackCount: 0,
                tracks: [],
                savedAt: Date.now()
            };
            playlists.unshift(newPlaylist);
            localStorage.setItem('zoe_saved_playlists', JSON.stringify(playlists));
            this.loadLocalPlaylists();
            MusicState.emit('playlistSaved', newPlaylist);
            this.showToast('Playlist created');
        } catch (e) {
            console.error('Failed to create playlist:', e);
        }
    }
    
    showToast(message) {
        const existing = document.querySelector('.mpl-toast');
        if (existing) existing.remove();
        
        const toast = document.createElement('div');
        toast.className = 'mpl-toast';
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
window.MusicPlaylistsWidget = MusicPlaylistsWidget;

// Widget is registered via manifest metadata (lazy loading)
// No auto-registration needed



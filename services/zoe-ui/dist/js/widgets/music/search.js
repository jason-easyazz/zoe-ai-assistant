/**
 * Music Search Widget
 * Search songs and playlists with mixed results
 * Version: 1.0.0
 */

class MusicSearchWidget extends WidgetModule {
    constructor() {
        super('music-search', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
        
        this.searchTimeout = null;
        this.unsubscribers = [];
        this.lastQuery = '';
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-search-widget">
                <div class="ms-search-container">
                    <input type="text" class="ms-search-input" id="ms-search-input" 
                           placeholder="Search songs, playlists, artists...">
                    <button class="ms-search-clear" id="ms-search-clear" style="display: none;">×</button>
                </div>
                
                <div class="ms-results" id="ms-results">
                    <div class="ms-empty-state">
                        <span class="ms-empty-icon">🔍</span>
                        <p>Search for music to get started</p>
                    </div>
                </div>
            </div>
            
            <style>
                .music-search-widget {
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    padding: 12px;
                    background: rgba(255, 255, 255, 0.9);
                    border-radius: 16px;
                    min-height: 200px;
                    max-height: 100%;
                    overflow: hidden;
                }
                
                .ms-search-container {
                    position: relative;
                    flex-shrink: 0;
                }
                
                .ms-search-input {
                    width: 100%;
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 10px;
                    padding: 12px 40px 12px 14px;
                    font-size: 14px;
                    outline: none;
                    transition: all 0.2s ease;
                }
                
                .ms-search-input:focus {
                    border-color: #7B61FF;
                    box-shadow: 0 0 0 3px rgba(123, 97, 255, 0.15);
                }
                
                .ms-search-input::placeholder {
                    color: #999;
                }
                
                .ms-search-clear {
                    position: absolute;
                    right: 10px;
                    top: 50%;
                    transform: translateY(-50%);
                    background: rgba(0, 0, 0, 0.1);
                    border: none;
                    border-radius: 50%;
                    width: 22px;
                    height: 22px;
                    font-size: 14px;
                    color: #666;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .ms-search-clear:hover {
                    background: rgba(0, 0, 0, 0.15);
                }
                
                .ms-results {
                    flex: 1;
                    overflow-y: auto;
                    display: flex;
                    flex-direction: column;
                    gap: 16px;
                }
                
                .ms-section {
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }
                
                .ms-section-title {
                    font-size: 11px;
                    font-weight: 600;
                    text-transform: uppercase;
                    color: #999;
                    letter-spacing: 0.5px;
                    padding: 0 4px;
                }
                
                .ms-track-list {
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }
                
                .ms-track-item {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 10px;
                    background: rgba(0, 0, 0, 0.02);
                    border: 1px solid rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .ms-track-item:hover {
                    background: rgba(123, 97, 255, 0.08);
                    border-color: rgba(123, 97, 255, 0.2);
                }
                
                .ms-track-item.playing {
                    background: rgba(123, 97, 255, 0.1);
                    border-color: #7B61FF;
                }
                
                .ms-track-art {
                    width: 44px;
                    height: 44px;
                    border-radius: 6px;
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 18px;
                    flex-shrink: 0;
                    overflow: hidden;
                }
                
                .ms-track-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .ms-track-info {
                    flex: 1;
                    min-width: 0;
                }
                
                .ms-track-title {
                    font-size: 13px;
                    font-weight: 500;
                    color: #333;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .ms-track-artist {
                    font-size: 11px;
                    color: #666;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .ms-track-meta {
                    font-size: 10px;
                    color: #999;
                }
                
                .ms-track-actions {
                    display: flex;
                    gap: 4px;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                
                .ms-track-item:hover .ms-track-actions {
                    opacity: 1;
                }
                
                .ms-action-btn {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 6px;
                    width: 28px;
                    height: 28px;
                    font-size: 12px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    transition: all 0.2s ease;
                }
                
                .ms-action-btn:hover {
                    background: rgba(123, 97, 255, 0.2);
                }
                
                .ms-action-btn.ms-play-btn:hover {
                    background: #7B61FF;
                    color: white;
                }
                
                /* Playlist item styling */
                .ms-playlist-item {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 10px;
                    background: rgba(0, 0, 0, 0.02);
                    border: 1px solid rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .ms-playlist-item:hover {
                    background: rgba(123, 97, 255, 0.08);
                    border-color: rgba(123, 97, 255, 0.2);
                }
                
                .ms-playlist-art {
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
                
                .ms-playlist-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .ms-playlist-actions {
                    display: flex;
                    gap: 4px;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                
                .ms-playlist-item:hover .ms-playlist-actions {
                    opacity: 1;
                }
                
                /* Dropdown menu for add options */
                .ms-add-menu {
                    position: relative;
                }
                
                .ms-add-dropdown {
                    position: absolute;
                    right: 0;
                    top: 100%;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
                    padding: 4px;
                    min-width: 140px;
                    z-index: 100;
                    display: none;
                }
                
                .ms-add-dropdown.show {
                    display: block;
                }
                
                .ms-add-option {
                    display: block;
                    width: 100%;
                    padding: 8px 12px;
                    text-align: left;
                    background: none;
                    border: none;
                    font-size: 12px;
                    color: #333;
                    cursor: pointer;
                    border-radius: 6px;
                }
                
                .ms-add-option:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .ms-empty-state {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    color: #666;
                    padding: 30px;
                }
                
                .ms-empty-icon {
                    font-size: 36px;
                    margin-bottom: 12px;
                    opacity: 0.5;
                }
                
                .ms-empty-state p {
                    font-size: 13px;
                    margin: 0;
                }
                
                .ms-loading {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    color: #666;
                    font-size: 13px;
                }
                
                .ms-spinner {
                    width: 28px;
                    height: 28px;
                    border: 3px solid rgba(0, 0, 0, 0.08);
                    border-top-color: #7B61FF;
                    border-radius: 50%;
                    animation: ms-spin 1s linear infinite;
                    margin-bottom: 12px;
                }
                
                @keyframes ms-spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;
    }
    
    init(element, options = {}) {
        super.init(element, options);
        this.setupEventListeners();
        this.subscribeToState();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        const searchInput = el.querySelector('#ms-search-input');
        const clearBtn = el.querySelector('#ms-search-clear');
        
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                const query = searchInput.value.trim();
                clearBtn.style.display = query ? 'flex' : 'none';
                
                clearTimeout(this.searchTimeout);
                this.searchTimeout = setTimeout(() => {
                    if (query.length > 2) {
                        this.performSearch(query);
                    } else if (query.length === 0) {
                        this.showEmptyState();
                    }
                }, 400);
            });
            
            searchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const query = searchInput.value.trim();
                    if (query) {
                        clearTimeout(this.searchTimeout);
                        this.performSearch(query);
                    }
                }
            });
        }
        
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                searchInput.value = '';
                clearBtn.style.display = 'none';
                this.showEmptyState();
                searchInput.focus();
            });
        }
        
        // Close dropdowns on outside click
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.ms-add-menu')) {
                el.querySelectorAll('.ms-add-dropdown.show').forEach(d => d.classList.remove('show'));
            }
        });
    }
    
    subscribeToState() {
        this.unsubscribers.push(
            MusicState.on('trackChanged', () => this.highlightCurrentTrack())
        );
    }
    
    showEmptyState() {
        const container = this.element?.querySelector('#ms-results');
        if (container) {
            container.innerHTML = `
                <div class="ms-empty-state">
                    <span class="ms-empty-icon">🔍</span>
                    <p>Search for music to get started</p>
                </div>
            `;
        }
    }
    
    showLoading() {
        const container = this.element?.querySelector('#ms-results');
        if (container) {
            container.innerHTML = `
                <div class="ms-loading">
                    <div class="ms-spinner"></div>
                    Searching...
                </div>
            `;
        }
    }
    
    async performSearch(query) {
        if (query === this.lastQuery) return;
        this.lastQuery = query;
        
        this.showLoading();
        
        // Search for both songs and playlists in parallel
        const [songs, playlists] = await Promise.all([
            this.searchSongs(query),
            this.searchPlaylists(query)
        ]);
        
        this.displayResults(songs, playlists);
    }
    
    async searchSongs(query) {
        const data = await MusicState.apiRequest(`/api/music/search?q=${encodeURIComponent(query)}&filter_type=songs&limit=15`);
        if (data?.results) {
            // Cache track info
            data.results.forEach(track => {
                const id = track.videoId || track.id;
                const rawThumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
                const thumbnail = rawThumbnail ? rawThumbnail.replace(/=w\d+-h\d+/, '=w300-h300') : '';
                MusicState.state.trackCache[id] = {
                    id,
                    title: track.title || 'Unknown',
                    artist: track.artist || track.artists?.[0]?.name || 'Unknown',
                    album: track.album || '',
                    duration: track.duration || '',
                    thumbnail
                };
            });
        }
        return data?.results || [];
    }
    
    async searchPlaylists(query) {
        const data = await MusicState.apiRequest(`/api/music/search?q=${encodeURIComponent(query)}&filter_type=playlists&limit=5`);
        return data?.results || [];
    }
    
    displayResults(songs, playlists) {
        const container = this.element?.querySelector('#ms-results');
        if (!container) return;
        
        if (songs.length === 0 && playlists.length === 0) {
            container.innerHTML = `
                <div class="ms-empty-state">
                    <span class="ms-empty-icon">🎵</span>
                    <p>No results found</p>
                </div>
            `;
            return;
        }
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        let html = '';
        
        // Songs section
        if (songs.length > 0) {
            html += `
                <div class="ms-section">
                    <div class="ms-section-title">Songs</div>
                    <div class="ms-track-list">
                        ${songs.map((track, index) => this.renderTrackItem(track, index, currentTrackId)).join('')}
                    </div>
                </div>
            `;
        }
        
        // Playlists section
        if (playlists.length > 0) {
            html += `
                <div class="ms-section">
                    <div class="ms-section-title">Playlists</div>
                    <div class="ms-track-list">
                        ${playlists.map((playlist, index) => this.renderPlaylistItem(playlist, index)).join('')}
                    </div>
                </div>
            `;
        }
        
        container.innerHTML = html;
        this.bindResultEvents();
    }
    
    renderTrackItem(track, index, currentTrackId) {
        const trackId = track.videoId || track.id;
        const artist = track.artist || track.artists?.[0]?.name || 'Unknown';
        const thumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
        const isPlaying = trackId === currentTrackId;
        
        return `
            <div class="ms-track-item ${isPlaying ? 'playing' : ''}" 
                 data-track-id="${this.escapeAttr(trackId)}"
                 data-index="${index}">
                <div class="ms-track-art">
                    ${thumbnail ? `<img src="${this.escapeAttr(thumbnail)}" alt="" onerror="this.parentElement.innerHTML='🎵'">` : '🎵'}
                </div>
                <div class="ms-track-info">
                    <div class="ms-track-title">${this.escapeHtml(track.title || 'Unknown')}</div>
                    <div class="ms-track-artist">${this.escapeHtml(artist)}</div>
                </div>
                <span class="ms-track-meta">${track.duration || ''}</span>
                <div class="ms-track-actions">
                    <button class="ms-action-btn ms-play-btn" title="Play Now">▶</button>
                    <button class="ms-action-btn ms-next-btn" title="Play Next">⏭</button>
                    <button class="ms-action-btn ms-queue-btn" title="Add to Queue">➕</button>
                </div>
            </div>
        `;
    }
    
    renderPlaylistItem(playlist, index) {
        const playlistId = playlist.browseId || playlist.playlistId || playlist.id;
        const thumbnail = playlist.thumbnail_url || playlist.thumbnails?.[0]?.url || '';
        const trackCount = playlist.itemCount || playlist.trackCount || '';
        
        return `
            <div class="ms-playlist-item" 
                 data-playlist-id="${this.escapeAttr(playlistId)}"
                 data-index="${index}">
                <div class="ms-playlist-art">
                    ${thumbnail ? `<img src="${this.escapeAttr(thumbnail)}" alt="" onerror="this.parentElement.innerHTML='📋'">` : '📋'}
                </div>
                <div class="ms-track-info">
                    <div class="ms-track-title">${this.escapeHtml(playlist.title || 'Unknown Playlist')}</div>
                    <div class="ms-track-artist">${this.escapeHtml(playlist.author || '')}</div>
                    ${trackCount ? `<div class="ms-track-meta">${trackCount} tracks</div>` : ''}
                </div>
                <div class="ms-playlist-actions">
                    <div class="ms-add-menu">
                        <button class="ms-action-btn ms-add-queue-btn" title="Add to Queue">➕</button>
                        <div class="ms-add-dropdown">
                            <button class="ms-add-option" data-action="append">Add to end of queue</button>
                            <button class="ms-add-option" data-action="shuffle">Shuffle into queue</button>
                            <button class="ms-add-option" data-action="save">Save to Library</button>
                        </div>
                    </div>
                    <button class="ms-action-btn ms-play-playlist-btn" title="Play All">▶</button>
                </div>
            </div>
        `;
    }
    
    bindResultEvents() {
        const el = this.element;
        if (!el) return;
        
        // Song track click handlers
        el.querySelectorAll('.ms-track-item').forEach(item => {
            // Play on click (not on action buttons)
            item.addEventListener('click', (e) => {
                if (e.target.closest('.ms-track-actions')) return;
                const trackId = item.dataset.trackId;
                MusicState.play(trackId);
            });
            
            // Play Now button
            item.querySelector('.ms-play-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackId = item.dataset.trackId;
                MusicState.play(trackId);
            });
            
            // Play Next button
            item.querySelector('.ms-next-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackId = item.dataset.trackId;
                const trackInfo = MusicState.state.trackCache[trackId];
                this.addToQueueNext(trackId, trackInfo);
            });
            
            // Add to Queue button
            item.querySelector('.ms-queue-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackId = item.dataset.trackId;
                const trackInfo = MusicState.state.trackCache[trackId];
                MusicState.addToQueue(trackId, trackInfo);
                this.showToast('Added to queue');
            });
        });
        
        // Playlist click handlers
        el.querySelectorAll('.ms-playlist-item').forEach(item => {
            const playlistId = item.dataset.playlistId;
            
            // Show add dropdown
            item.querySelector('.ms-add-queue-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const dropdown = item.querySelector('.ms-add-dropdown');
                // Close other dropdowns
                el.querySelectorAll('.ms-add-dropdown.show').forEach(d => {
                    if (d !== dropdown) d.classList.remove('show');
                });
                dropdown?.classList.toggle('show');
            });
            
            // Dropdown options
            item.querySelectorAll('.ms-add-option').forEach(option => {
                option.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const action = option.dataset.action;
                    await this.handlePlaylistAction(playlistId, action);
                    item.querySelector('.ms-add-dropdown')?.classList.remove('show');
                });
            });
            
            // Play All button
            item.querySelector('.ms-play-playlist-btn')?.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.playPlaylist(playlistId);
            });
            
            // Click on playlist to expand/view (future feature)
            item.addEventListener('click', (e) => {
                if (e.target.closest('.ms-playlist-actions')) return;
                // For now, just play the playlist
                this.playPlaylist(playlistId);
            });
        });
    }
    
    async addToQueueNext(trackId, trackInfo) {
        await MusicState.apiRequest('/api/music/queue/next', {
            method: 'POST',
            body: {
                track_id: trackId,
                track_info: trackInfo
            }
        });
        this.showToast('Playing next');
    }
    
    async handlePlaylistAction(playlistId, action) {
        // Fetch playlist tracks
        const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
        if (!playlist?.tracks) {
            this.showToast('Could not load playlist');
            return;
        }
        
        const tracks = playlist.tracks;
        
        switch (action) {
            case 'append':
                // Add all tracks to end of queue
                for (const track of tracks) {
                    const trackId = track.videoId || track.id;
                    await MusicState.addToQueue(trackId, track);
                }
                this.showToast(`Added ${tracks.length} tracks to queue`);
                break;
                
            case 'shuffle':
                // Shuffle tracks into current queue
                const shuffled = [...tracks].sort(() => Math.random() - 0.5);
                for (const track of shuffled) {
                    const trackId = track.videoId || track.id;
                    await MusicState.addToQueue(trackId, track);
                }
                this.showToast(`Shuffled ${tracks.length} tracks into queue`);
                break;
                
            case 'save':
                // Save to library (stored in localStorage for now)
                this.savePlaylistToLibrary(playlistId, playlist);
                this.showToast('Saved to library');
                break;
        }
    }
    
    async playPlaylist(playlistId) {
        const playlist = await MusicState.apiRequest(`/api/music/playlist/${playlistId}`);
        if (!playlist?.tracks || playlist.tracks.length === 0) {
            this.showToast('Playlist is empty');
            return;
        }
        
        // Clear queue and add all tracks
        await MusicState.apiRequest('/api/music/queue/clear', { method: 'POST' });
        
        // Add all tracks to queue
        for (const track of playlist.tracks) {
            const trackId = track.videoId || track.id;
            await MusicState.addToQueue(trackId, track);
        }
        
        // Play first track
        const firstTrack = playlist.tracks[0];
        const firstId = firstTrack.videoId || firstTrack.id;
        MusicState.play(firstId);
    }
    
    savePlaylistToLibrary(playlistId, playlistData) {
        try {
            const saved = JSON.parse(localStorage.getItem('zoe_saved_playlists') || '[]');
            if (!saved.find(p => p.id === playlistId)) {
                saved.push({
                    id: playlistId,
                    title: playlistData.title,
                    thumbnail: playlistData.thumbnail_url || playlistData.thumbnails?.[0]?.url || '',
                    trackCount: playlistData.tracks?.length || 0,
                    savedAt: Date.now()
                });
                localStorage.setItem('zoe_saved_playlists', JSON.stringify(saved));
                MusicState.emit('playlistSaved', { playlistId, playlistData });
            }
        } catch (e) {
            console.error('Failed to save playlist:', e);
        }
    }
    
    highlightCurrentTrack() {
        const el = this.element;
        if (!el) return;
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        el.querySelectorAll('.ms-track-item').forEach(item => {
            item.classList.toggle('playing', item.dataset.trackId === currentTrackId);
        });
    }
    
    showToast(message) {
        // Simple toast notification
        const existing = document.querySelector('.ms-toast');
        if (existing) existing.remove();
        
        const toast = document.createElement('div');
        toast.className = 'ms-toast';
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
        clearTimeout(this.searchTimeout);
        super.destroy();
    }
}

// Export to window for widget system
window.MusicSearchWidget = MusicSearchWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register(new MusicSearchWidget());
}



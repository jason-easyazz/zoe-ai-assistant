/**
 * Music Library Widget
 * Tabbed interface for Search, Queue, and Recommendations
 * Version: 1.0.0
 */

class MusicLibraryWidget extends WidgetModule {
    constructor() {
        super('music-library', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
        
        this.currentTab = 'search';
        this.searchTimeout = null;
        this.unsubscribers = [];
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-library-widget">
                <!-- Tabs -->
                <div class="ml-tabs">
                    <button class="ml-tab active" data-tab="search">🔍 Search</button>
                    <button class="ml-tab" data-tab="queue">📋 Queue</button>
                    <button class="ml-tab" data-tab="foryou">✨ For You</button>
                </div>
                
                <!-- Search Tab -->
                <div class="ml-tab-content active" data-tab="search">
                    <div class="ml-search-container">
                        <input type="text" class="ml-search-input" id="ml-search-input" 
                               placeholder="Search songs, artists, albums...">
                    </div>
                    <div class="ml-track-list" id="ml-search-results">
                        <div class="ml-empty-state">
                            <span class="ml-empty-icon">🎵</span>
                            <p>Search for music to get started</p>
                        </div>
                    </div>
                </div>
                
                <!-- Queue Tab -->
                <div class="ml-tab-content" data-tab="queue">
                    <div class="ml-queue-header">
                        <span class="ml-queue-title">Up Next</span>
                        <button class="ml-clear-queue" id="ml-clear-queue">Clear</button>
                    </div>
                    <div class="ml-track-list" id="ml-queue-list">
                        <div class="ml-empty-state">
                            <span class="ml-empty-icon">📋</span>
                            <p>Queue is empty</p>
                        </div>
                    </div>
                </div>
                
                <!-- For You Tab -->
                <div class="ml-tab-content" data-tab="foryou">
                    <div class="ml-foryou-tabs">
                        <button class="ml-foryou-tab active" data-type="radio">Personal Radio</button>
                        <button class="ml-foryou-tab" data-type="discover">Discover</button>
                        <button class="ml-foryou-tab" data-type="similar">Similar</button>
                    </div>
                    <div class="ml-track-list" id="ml-foryou-list">
                        <div class="ml-loading">
                            <div class="ml-spinner"></div>
                            Loading recommendations...
                        </div>
                    </div>
                </div>
            </div>
            
            <style>
                .music-library-widget {
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    padding: 12px;
                    background: rgba(255, 255, 255, 0.9);
                    border-radius: 16px;
                    min-height: 300px;
                    max-height: 500px;
                }
                
                .ml-tabs {
                    display: flex;
                    gap: 4px;
                    padding: 4px;
                    background: rgba(0, 0, 0, 0.04);
                    border-radius: 10px;
                }
                
                .ml-tab {
                    flex: 1;
                    background: transparent;
                    border: none;
                    padding: 8px 12px;
                    font-size: 12px;
                    color: var(--text-secondary, #666);
                    cursor: pointer;
                    border-radius: 8px;
                    transition: all 0.2s ease;
                    white-space: nowrap;
                }
                
                .ml-tab:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .ml-tab.active {
                    background: white;
                    color: #7B61FF;
                    font-weight: 500;
                    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
                }
                
                .ml-tab-content {
                    display: none;
                    flex-direction: column;
                    gap: 12px;
                    flex: 1;
                    overflow: hidden;
                }
                
                .ml-tab-content.active {
                    display: flex;
                }
                
                .ml-search-container {
                    position: relative;
                }
                
                .ml-search-input {
                    width: 100%;
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 10px;
                    padding: 10px 14px;
                    font-size: 13px;
                    outline: none;
                    transition: all 0.2s ease;
                }
                
                .ml-search-input:focus {
                    border-color: #7B61FF;
                    box-shadow: 0 0 0 3px rgba(123, 97, 255, 0.15);
                }
                
                .ml-search-input::placeholder {
                    color: #999;
                }
                
                .ml-queue-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0 4px;
                }
                
                .ml-queue-title {
                    font-size: 13px;
                    font-weight: 600;
                    color: var(--text-primary, #333);
                }
                
                .ml-clear-queue {
                    background: none;
                    border: none;
                    font-size: 11px;
                    color: #dc2626;
                    cursor: pointer;
                    padding: 4px 8px;
                    border-radius: 6px;
                }
                
                .ml-clear-queue:hover {
                    background: rgba(220, 38, 38, 0.1);
                }
                
                .ml-foryou-tabs {
                    display: flex;
                    gap: 8px;
                    overflow-x: auto;
                    padding-bottom: 4px;
                }
                
                .ml-foryou-tab {
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 16px;
                    padding: 6px 12px;
                    font-size: 11px;
                    color: var(--text-secondary, #666);
                    cursor: pointer;
                    white-space: nowrap;
                    transition: all 0.2s ease;
                }
                
                .ml-foryou-tab:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .ml-foryou-tab.active {
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    color: white;
                    border-color: transparent;
                }
                
                .ml-track-list {
                    flex: 1;
                    overflow-y: auto;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }
                
                .ml-track-item {
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
                
                .ml-track-item:hover {
                    background: rgba(123, 97, 255, 0.08);
                    border-color: rgba(123, 97, 255, 0.2);
                    transform: translateX(2px);
                }
                
                .ml-track-item.playing {
                    background: rgba(123, 97, 255, 0.1);
                    border-color: #7B61FF;
                }
                
                .ml-track-art {
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
                
                .ml-track-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .ml-track-info {
                    flex: 1;
                    min-width: 0;
                }
                
                .ml-track-title {
                    font-size: 13px;
                    font-weight: 500;
                    color: var(--text-primary, #333);
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .ml-track-artist {
                    font-size: 11px;
                    color: var(--text-secondary, #666);
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .ml-track-duration {
                    font-size: 11px;
                    color: var(--text-tertiary, #999);
                    flex-shrink: 0;
                }
                
                .ml-track-actions {
                    display: flex;
                    gap: 4px;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                
                .ml-track-item:hover .ml-track-actions {
                    opacity: 1;
                }
                
                .ml-track-action {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 50%;
                    width: 28px;
                    height: 28px;
                    font-size: 12px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .ml-track-action:hover {
                    background: rgba(123, 97, 255, 0.2);
                }
                
                .ml-empty-state {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    color: var(--text-secondary, #666);
                    padding: 30px;
                }
                
                .ml-empty-icon {
                    font-size: 36px;
                    margin-bottom: 12px;
                    opacity: 0.5;
                }
                
                .ml-empty-state p {
                    font-size: 13px;
                    margin: 0;
                }
                
                .ml-loading {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    color: var(--text-secondary, #666);
                    font-size: 13px;
                }
                
                .ml-spinner {
                    width: 28px;
                    height: 28px;
                    border: 3px solid rgba(0, 0, 0, 0.08);
                    border-top-color: #7B61FF;
                    border-radius: 50%;
                    animation: ml-spin 1s linear infinite;
                    margin-bottom: 12px;
                }
                
                @keyframes ml-spin {
                    to { transform: rotate(360deg); }
                }
                
                /* Compact mode */
                .size-small .music-library-widget .ml-track-art {
                    width: 32px;
                    height: 32px;
                }
                
                .size-small .music-library-widget .ml-track-actions {
                    display: none;
                }
                
                .size-small .music-library-widget .ml-foryou-tabs {
                    display: none;
                }
            </style>
        `;
    }
    
    init(element, options = {}) {
        super.init(element, options);
        
        this.setupEventListeners();
        this.subscribeToState();
        this.loadInitialData();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        // Tab switching
        el.querySelectorAll('.ml-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.dataset.tab;
                this.switchTab(tabName);
            });
        });
        
        // Search input
        const searchInput = el.querySelector('#ml-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(this.searchTimeout);
                this.searchTimeout = setTimeout(() => {
                    const query = searchInput.value.trim();
                    if (query.length > 2) {
                        this.performSearch(query);
                    }
                }, 500);
            });
            
            searchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const query = searchInput.value.trim();
                    if (query) {
                        this.performSearch(query);
                    }
                }
            });
        }
        
        // For You sub-tabs
        el.querySelectorAll('.ml-foryou-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const type = tab.dataset.type;
                el.querySelectorAll('.ml-foryou-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.loadForYou(type);
            });
        });
        
        // Clear queue
        el.querySelector('#ml-clear-queue')?.addEventListener('click', async () => {
            await MusicState.apiRequest('/api/music/queue/clear', { method: 'POST' });
            this.loadQueue();
        });
    }
    
    subscribeToState() {
        this.unsubscribers.push(
            MusicState.on('searchResults', (results) => this.displaySearchResults(results)),
            MusicState.on('recommendationsLoaded', (data) => {
                if (this.currentTab === 'foryou') {
                    this.displayTracks('ml-foryou-list', data.tracks);
                }
            }),
            MusicState.on('queueUpdated', () => this.loadQueue()),
            MusicState.on('trackChanged', () => this.highlightCurrentTrack())
        );
    }
    
    switchTab(tabName) {
        const el = this.element;
        if (!el) return;
        
        this.currentTab = tabName;
        
        // Update tab buttons
        el.querySelectorAll('.ml-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        
        // Update tab content
        el.querySelectorAll('.ml-tab-content').forEach(content => {
            content.classList.toggle('active', content.dataset.tab === tabName);
        });
        
        // Load data for the tab
        if (tabName === 'queue') {
            this.loadQueue();
        } else if (tabName === 'foryou') {
            const activeType = el.querySelector('.ml-foryou-tab.active')?.dataset.type || 'radio';
            this.loadForYou(activeType);
        }
    }
    
    loadInitialData() {
        // Load recommendations by default
        this.loadForYou('radio');
    }
    
    async performSearch(query) {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#ml-search-results');
        container.innerHTML = `
            <div class="ml-loading">
                <div class="ml-spinner"></div>
                Searching...
            </div>
        `;
        
        const results = await MusicState.search(query);
        this.displaySearchResults(results);
    }
    
    displaySearchResults(results) {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#ml-search-results');
        this.displayTracks('ml-search-results', results);
        
        // Update playlist in MusicState
        const trackIds = results.map(t => t.videoId || t.id);
        MusicState.setPlaylist(trackIds);
    }
    
    async loadQueue() {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#ml-queue-list');
        container.innerHTML = `
            <div class="ml-loading">
                <div class="ml-spinner"></div>
                Loading queue...
            </div>
        `;
        
        const data = await MusicState.apiRequest('/api/music/queue');
        const tracks = data?.queue || [];
        this.displayTracks('ml-queue-list', tracks, true);
    }
    
    async loadForYou(type) {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector('#ml-foryou-list');
        container.innerHTML = `
            <div class="ml-loading">
                <div class="ml-spinner"></div>
                Loading recommendations...
            </div>
        `;
        
        let tracks = [];
        
        if (type === 'similar') {
            const trackId = MusicState.state.currentTrack?.id;
            if (trackId) {
                const data = await MusicState.apiRequest(`/api/music/similar/${trackId}?limit=15`);
                tracks = data?.tracks || [];
            } else {
                container.innerHTML = `
                    <div class="ml-empty-state">
                        <span class="ml-empty-icon">🎵</span>
                        <p>Play a track to see similar music</p>
                    </div>
                `;
                return;
            }
        } else {
            tracks = await MusicState.loadRecommendations(type);
        }
        
        this.displayTracks('ml-foryou-list', tracks);
        
        // Update playlist
        const trackIds = tracks.map(t => t.videoId || t.id);
        MusicState.setPlaylist(trackIds);
    }
    
    displayTracks(containerId, tracks, isDraggable = false) {
        const el = this.element;
        if (!el) return;
        
        const container = el.querySelector(`#${containerId}`);
        if (!container) return;
        
        if (!tracks || tracks.length === 0) {
            container.innerHTML = `
                <div class="ml-empty-state">
                    <span class="ml-empty-icon">🎵</span>
                    <p>No tracks found</p>
                </div>
            `;
            return;
        }
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        container.innerHTML = tracks.map((track, index) => {
            const trackId = track.videoId || track.id;
            const artist = track.artist || track.artists?.[0]?.name || 'Unknown';
            const thumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || track.thumbnail || '';
            const isPlaying = trackId === currentTrackId;
            
            return `
                <div class="ml-track-item ${isPlaying ? 'playing' : ''}" 
                     data-track-id="${this.escapeAttr(trackId)}"
                     data-index="${index}">
                    <div class="ml-track-art">
                        ${thumbnail ? `<img src="${this.escapeAttr(thumbnail)}" alt="">` : '🎵'}
                    </div>
                    <div class="ml-track-info">
                        <div class="ml-track-title">${this.escapeHtml(track.title || 'Unknown')}</div>
                        <div class="ml-track-artist">${this.escapeHtml(artist)}</div>
                    </div>
                    <span class="ml-track-duration">${track.duration || ''}</span>
                    <div class="ml-track-actions">
                        <button class="ml-track-action ml-add-queue" title="Add to Queue">➕</button>
                    </div>
                </div>
            `;
        }).join('');
        
        // Add click handlers
        container.querySelectorAll('.ml-track-item').forEach(item => {
            item.addEventListener('click', (e) => {
                // Don't trigger play if clicking action button
                if (e.target.closest('.ml-track-action')) return;
                
                const trackId = item.dataset.trackId;
                const index = parseInt(item.dataset.index);
                MusicState.setState({ playlistIndex: index });
                MusicState.play(trackId);
            });
            
            // Add to queue button
            item.querySelector('.ml-add-queue')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackId = item.dataset.trackId;
                const trackInfo = MusicState.state.trackCache[trackId];
                MusicState.addToQueue(trackId, trackInfo);
            });
        });
    }
    
    highlightCurrentTrack() {
        const el = this.element;
        if (!el) return;
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        el.querySelectorAll('.ml-track-item').forEach(item => {
            item.classList.toggle('playing', item.dataset.trackId === currentTrackId);
        });
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    escapeAttr(text) {
        if (!text) return '';
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
window.MusicLibraryWidget = MusicLibraryWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register(new MusicLibraryWidget());
}


/**
 * Music Suggestions Widget
 * Unified tile-based recommendations with color-coded types
 * Version: 2.1.0 - Compact color indicators, no duplicates
 */

class MusicSuggestionsWidget extends WidgetModule {
    constructor() {
        super('music-suggestions', {
            version: '2.1.0',
            defaultSize: 'size-medium',
            updateInterval: null
        });
        
        this.allTracks = [];
        this.seenTrackIds = new Set(); // Track deduplication
        this.unsubscribers = [];
        
        // Type configurations with colors only (no text labels)
        this.typeConfig = {
            radio: { 
                color: '#7B61FF',  // Purple - For You
                bg: 'rgba(123, 97, 255, 0.12)'
            },
            discover: { 
                color: '#10B981',  // Green - Trending
                bg: 'rgba(16, 185, 129, 0.12)'
            },
            similar: { 
                color: '#F59E0B',  // Orange - Similar
                bg: 'rgba(245, 158, 11, 0.12)'
            }
        };
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-suggestions-widget">
                <div class="msgs-header">
                    <span class="msgs-title">Suggestions</span>
                    <div class="msgs-legend">
                        <span class="msgs-legend-dot" data-type="radio" style="background: #7B61FF" title="For You"></span>
                        <span class="msgs-legend-dot" data-type="discover" style="background: #10B981" title="Trending"></span>
                        <span class="msgs-legend-dot" data-type="similar" style="background: #F59E0B" title="Similar"></span>
                    </div>
                    <button class="msgs-refresh" id="msgs-refresh" title="Refresh">↻</button>
                </div>
                
                <div class="msgs-grid" id="msgs-content">
                    <div class="msgs-loading">
                        <div class="msgs-spinner"></div>
                    </div>
                </div>
            </div>
            
            <style>
                .music-suggestions-widget {
                    display: flex;
                    flex-direction: column;
                    padding: 10px;
                    background: rgba(255, 255, 255, 0.92);
                    border-radius: 14px;
                    min-height: 180px;
                    max-height: 100%;
                    overflow: hidden;
                }
                
                .msgs-header {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    margin-bottom: 10px;
                    flex-shrink: 0;
                }
                
                .msgs-title {
                    font-size: 13px;
                    font-weight: 600;
                    color: #333;
                    flex: 1;
                }
                
                .msgs-legend {
                    display: flex;
                    gap: 6px;
                    align-items: center;
                }
                
                .msgs-legend-dot {
                    width: 8px;
                    height: 8px;
                    border-radius: 50%;
                    cursor: pointer;
                    transition: all 0.2s;
                    opacity: 1;
                }
                
                .msgs-legend-dot:hover {
                    transform: scale(1.3);
                }
                
                .msgs-legend-dot.dimmed {
                    opacity: 0.25;
                }
                
                .msgs-refresh {
                    background: rgba(0, 0, 0, 0.04);
                    border: none;
                    border-radius: 6px;
                    width: 26px;
                    height: 26px;
                    font-size: 14px;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    flex-shrink: 0;
                    color: #666;
                }
                
                .msgs-refresh:hover {
                    background: rgba(123, 97, 255, 0.15);
                    transform: rotate(180deg);
                    color: #7B61FF;
                }
                
                .msgs-grid {
                    flex: 1;
                    overflow-y: auto;
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
                    gap: 6px;
                    align-content: start;
                    padding: 2px;
                }
                
                .msgs-tile {
                    position: relative;
                    background: rgba(0, 0, 0, 0.02);
                    border: 1px solid rgba(0, 0, 0, 0.04);
                    border-radius: 8px;
                    padding: 6px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                    display: flex;
                    gap: 6px;
                    align-items: center;
                    border-left: 3px solid var(--type-color);
                }
                
                .msgs-tile:hover {
                    background: rgba(0, 0, 0, 0.04);
                    border-color: rgba(0, 0, 0, 0.08);
                    border-left-color: var(--type-color);
                    transform: translateY(-1px);
                    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
                }
                
                .msgs-tile.playing {
                    background: rgba(123, 97, 255, 0.08);
                    border-color: rgba(123, 97, 255, 0.3);
                    border-left-color: #7B61FF;
                }
                
                .msgs-tile-art {
                    width: 32px;
                    height: 32px;
                    border-radius: 4px;
                    background: linear-gradient(135deg, #ddd 0%, #eee 100%);
                    flex-shrink: 0;
                    overflow: hidden;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 12px;
                }
                
                .msgs-tile-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .msgs-tile-info {
                    flex: 1;
                    min-width: 0;
                    display: flex;
                    flex-direction: column;
                    gap: 1px;
                }
                
                .msgs-tile-title {
                    font-size: 11px;
                    font-weight: 500;
                    color: #333;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    line-height: 1.2;
                }
                
                .msgs-tile-artist {
                    font-size: 9px;
                    color: #888;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .msgs-tile-actions {
                    position: absolute;
                    top: 50%;
                    right: 4px;
                    transform: translateY(-50%);
                    display: flex;
                    gap: 2px;
                    opacity: 0;
                    transition: opacity 0.15s ease;
                }
                
                .msgs-tile:hover .msgs-tile-actions {
                    opacity: 1;
                }
                
                .msgs-tile-btn {
                    background: rgba(255, 255, 255, 0.9);
                    border: none;
                    border-radius: 4px;
                    width: 20px;
                    height: 20px;
                    font-size: 9px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    transition: all 0.15s;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                }
                
                .msgs-tile-btn:hover {
                    background: #7B61FF;
                    color: white;
                }
                
                .msgs-loading {
                    grid-column: 1 / -1;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 40px;
                }
                
                .msgs-spinner {
                    width: 24px;
                    height: 24px;
                    border: 2px solid rgba(0, 0, 0, 0.08);
                    border-top-color: #7B61FF;
                    border-radius: 50%;
                    animation: msgs-spin 0.8s linear infinite;
                }
                
                @keyframes msgs-spin {
                    to { transform: rotate(360deg); }
                }
                
                .msgs-empty-state {
                    grid-column: 1 / -1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    color: #888;
                    padding: 30px;
                    font-size: 12px;
                }
                
                .msgs-empty-icon {
                    font-size: 28px;
                    margin-bottom: 8px;
                    opacity: 0.5;
                }
            </style>
        `;
    }
    
    init(element, options = {}) {
        super.init(element, options);
        this.setupEventListeners();
        this.subscribeToState();
        this.loadAllSuggestions();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        // Refresh button
        el.querySelector('#msgs-refresh')?.addEventListener('click', () => {
            this.loadAllSuggestions();
        });
        
        // Legend dots for filtering
        el.querySelectorAll('.msgs-legend-dot').forEach(dot => {
            dot.addEventListener('click', () => {
                this.toggleFilter(dot.dataset.type);
            });
        });
    }
    
    subscribeToState() {
        this.unsubscribers.push(
            MusicState.on('trackChanged', () => {
                this.highlightCurrentTrack();
                // Reload similar when track changes
                this.loadSimilarTracks();
            })
        );
    }
    
    toggleFilter(type) {
        const el = this.element;
        if (!el) return;
        
        const legendDot = el.querySelector(`.msgs-legend-dot[data-type="${type}"]`);
        legendDot?.classList.toggle('dimmed');
        
        // Show/hide tiles of this type
        const isDimmed = legendDot?.classList.contains('dimmed');
        el.querySelectorAll(`.msgs-tile[data-type="${type}"]`).forEach(tile => {
            tile.style.display = isDimmed ? 'none' : '';
        });
    }
    
    async loadAllSuggestions() {
        const container = this.element?.querySelector('#msgs-content');
        if (!container) return;
        
        container.innerHTML = `<div class="msgs-loading"><div class="msgs-spinner"></div></div>`;
        
        this.allTracks = [];
        this.seenTrackIds.clear(); // Reset deduplication
        
        try {
            // Load all types in parallel
            const [radioTracks, discoverTracks, similarResult] = await Promise.all([
                this.loadRadio(),
                this.loadDiscover(),
                this.loadSimilar()
            ]);
            
            // Add tracks with deduplication - priority: radio > discover > similar
            this.addTracksWithDedupe(radioTracks, 'radio', 10);
            this.addTracksWithDedupe(discoverTracks, 'discover', 8);
            this.addTracksWithDedupe(similarResult.tracks, 'similar', 8);
            
            // Interleave for variety
            this.allTracks = this.interleaveByType(this.allTracks);
            
        } catch (e) {
            console.error('Failed to load suggestions:', e);
        }
        
        this.displayTiles();
    }
    
    addTracksWithDedupe(tracks, type, maxCount) {
        let added = 0;
        for (const track of tracks) {
            if (added >= maxCount) break;
            
            const trackId = track.videoId || track.id;
            if (!trackId || this.seenTrackIds.has(trackId)) continue;
            
            // Also skip if it's the currently playing track
            const currentId = MusicState.state.currentTrack?.id;
            if (trackId === currentId) continue;
            
            this.seenTrackIds.add(trackId);
            track._suggestionType = type;
            this.allTracks.push(track);
            added++;
        }
    }
    
    async loadSimilarTracks() {
        // Just reload similar tracks without full refresh
        try {
            const result = await this.loadSimilar();
            
            // Remove old similar tracks and their IDs from seen set
            const oldSimilarIds = this.allTracks
                .filter(t => t._suggestionType === 'similar')
                .map(t => t.videoId || t.id);
            
            oldSimilarIds.forEach(id => this.seenTrackIds.delete(id));
            this.allTracks = this.allTracks.filter(t => t._suggestionType !== 'similar');
            
            // Add new similar tracks with dedupe
            this.addTracksWithDedupe(result.tracks, 'similar', 8);
            
            this.displayTiles();
        } catch (e) {
            console.error('Failed to update similar tracks:', e);
        }
    }
    
    interleaveByType(tracks) {
        // Group by type
        const groups = { radio: [], discover: [], similar: [] };
        tracks.forEach(t => {
            if (groups[t._suggestionType]) {
                groups[t._suggestionType].push(t);
            }
        });
        
        // Interleave: take from each group in rotation
        const result = [];
        const maxLen = Math.max(groups.radio.length, groups.discover.length, groups.similar.length);
        
        for (let i = 0; i < maxLen; i++) {
            if (groups.radio[i]) result.push(groups.radio[i]);
            if (groups.discover[i]) result.push(groups.discover[i]);
            if (groups.similar[i]) result.push(groups.similar[i]);
        }
        
        return result;
    }
    
    async loadRadio() {
        try {
            const data = await MusicState.apiRequest('/api/music/radio?limit=12');
            const tracks = data?.tracks || [];
            this.cacheTracks(tracks);
            return tracks;
        } catch (e) {
            console.warn('Failed to load radio:', e);
            return [];
        }
    }
    
    async loadDiscover() {
        try {
            const data = await MusicState.apiRequest('/api/music/discover?limit=10');
            const tracks = data?.tracks || [];
            this.cacheTracks(tracks);
            return tracks;
        } catch (e) {
            console.warn('Failed to load discover:', e);
            return [];
        }
    }
    
    async loadSimilar() {
        const currentTrack = MusicState.state.currentTrack;
        
        if (!currentTrack?.id) {
            return { tracks: [] };
        }
        
        try {
            const data = await MusicState.apiRequest(`/api/music/similar/${currentTrack.id}?limit=10`);
            const tracks = data?.tracks || [];
            this.cacheTracks(tracks);
            return { tracks };
        } catch (e) {
            console.warn('Failed to load similar:', e);
            return { tracks: [] };
        }
    }
    
    cacheTracks(tracks) {
        tracks.forEach(track => {
            const id = track.videoId || track.id;
            const rawThumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
            const thumbnail = rawThumbnail ? rawThumbnail.replace(/=w\d+-h\d+/, '=w120-h120') : '';
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
    
    displayTiles() {
        const container = this.element?.querySelector('#msgs-content');
        if (!container) return;
        
        if (this.allTracks.length === 0) {
            container.innerHTML = `
                <div class="msgs-empty-state">
                    <span class="msgs-empty-icon">🎵</span>
                    <p>No suggestions yet.<br>Play some music to get started!</p>
                </div>
            `;
            return;
        }
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        // Check which types are filtered out
        const legendDots = this.element.querySelectorAll('.msgs-legend-dot');
        const hiddenTypes = new Set();
        legendDots.forEach(dot => {
            if (dot.classList.contains('dimmed')) {
                hiddenTypes.add(dot.dataset.type);
            }
        });
        
        const html = this.allTracks.map((track, index) => {
            const trackId = track.videoId || track.id;
            const artist = track.artist || track.artists?.[0]?.name || 'Unknown';
            const thumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
            const isPlaying = trackId === currentTrackId;
            const type = track._suggestionType || 'radio';
            const config = this.typeConfig[type];
            const isHidden = hiddenTypes.has(type);
            
            return `
                <div class="msgs-tile ${isPlaying ? 'playing' : ''}" 
                     data-track-id="${this.escapeAttr(trackId)}"
                     data-type="${type}"
                     data-index="${index}"
                     style="--type-color: ${config.color}; ${isHidden ? 'display:none;' : ''}">
                    <div class="msgs-tile-art">
                        ${thumbnail 
                            ? `<img src="${this.escapeAttr(thumbnail)}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='🎵'">`
                            : '🎵'}
                    </div>
                    <div class="msgs-tile-info">
                        <div class="msgs-tile-title">${this.escapeHtml(track.title || 'Unknown')}</div>
                        <div class="msgs-tile-artist">${this.escapeHtml(artist)}</div>
                    </div>
                    <div class="msgs-tile-actions">
                        <button class="msgs-tile-btn play" title="Play">▶</button>
                        <button class="msgs-tile-btn queue" title="Queue">+</button>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = html;
        this.bindTileEvents();
    }
    
    bindTileEvents() {
        const el = this.element;
        if (!el) return;
        
        el.querySelectorAll('.msgs-tile').forEach(tile => {
            const trackId = tile.dataset.trackId;
            
            // Play on click
            tile.addEventListener('click', (e) => {
                if (e.target.closest('.msgs-tile-actions')) return;
                MusicState.play(trackId);
            });
            
            // Play button
            tile.querySelector('.msgs-tile-btn.play')?.addEventListener('click', (e) => {
                e.stopPropagation();
                MusicState.play(trackId);
            });
            
            // Add to queue button
            tile.querySelector('.msgs-tile-btn.queue')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackInfo = MusicState.state.trackCache[trackId];
                MusicState.addToQueue(trackId, trackInfo);
                this.showToast('Added to queue');
            });
        });
    }
    
    highlightCurrentTrack() {
        const el = this.element;
        if (!el) return;
        
        const currentTrackId = MusicState.state.currentTrack?.id;
        
        el.querySelectorAll('.msgs-tile').forEach(tile => {
            tile.classList.toggle('playing', tile.dataset.trackId === currentTrackId);
        });
    }
    
    showToast(message) {
        const existing = document.querySelector('.msgs-toast');
        if (existing) existing.remove();
        
        const toast = document.createElement('div');
        toast.className = 'msgs-toast';
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
window.MusicSuggestionsWidget = MusicSuggestionsWidget;

// Widget is registered via manifest metadata (lazy loading)
// No auto-registration needed



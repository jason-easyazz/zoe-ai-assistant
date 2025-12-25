/**
 * Music State Manager
 * Shared state for all music widgets with cross-widget communication
 * Handles zone management for multi-device playback
 * Version: 1.0.0
 */

class MusicStateManager {
    constructor() {
        this.currentStreamUrl = null; // Store current stream URL for persistence
        
        this.state = {
            // Playback state
            isPlaying: false,
            currentTrack: null,
            position: 0,
            duration: 0,
            volume: 80,
            playMode: 'audio', // 'audio' or 'video'
            
            // Playlist/Queue
            queue: [],
            playlist: [],
            playlistIndex: -1,
            
            // Auth & Zones
            isAuthenticated: false,
            currentZone: null,
            availableZones: [],
            availableDevices: [],
            targetDevice: null,  // { id, type } for casting
            
            // Cache
            trackCache: {},
            
            // Connection
            wsConnected: false
        };
        
        this.listeners = new Map();
        this.ws = null;
        this.audioElement = null;
        this.ytPlayer = null;
        this.ytPlayerReady = false;
        
        // Debounce timers
        this._saveTimer = null;
        this._progressTimer = null;
    }
    
    /**
     * Initialize the state manager
     */
    async init() {
        console.log('🎵 MusicState: Initializing...');
        this.initialized = false;
        
        // Setup WebSocket
        this.setupWebSocket();
        
        // Load saved state
        this.loadLocalState();
        
        // Initialize audio element early to pick up shared audio from mini-player
        // Small delay to ensure mini-player has initialized first
        setTimeout(() => {
            this.getAudioElement();
        }, 50);
        
        // Check auth and load zones in parallel for faster init
        await Promise.all([
            this.checkAuth(),
            this.loadZones(),
            this.loadPlaybackState()
        ]);
        
        this.initialized = true;
        console.log('🎵 MusicState: Ready');
    }
    
    /**
     * Event subscription
     */
    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, new Set());
        }
        this.listeners.get(event).add(callback);
        return () => this.off(event, callback);
    }
    
    off(event, callback) {
        const callbacks = this.listeners.get(event);
        if (callbacks) {
            callbacks.delete(callback);
        }
    }
    
    emit(event, data) {
        const callbacks = this.listeners.get(event);
        if (callbacks) {
            callbacks.forEach(cb => {
                try {
                    cb(data);
                } catch (e) {
                    console.error(`MusicState event handler error (${event}):`, e);
                }
            });
        }
    }
    
    /**
     * State updates with automatic event emission
     */
    setState(updates) {
        const oldState = { ...this.state };
        Object.assign(this.state, updates);
        
        // Emit specific events based on what changed
        for (const key of Object.keys(updates)) {
            if (oldState[key] !== updates[key]) {
                this.emit(`${key}Changed`, updates[key]);
            }
        }
        
        // General state change event
        this.emit('stateChanged', this.state);
        
        // Auto-save to localStorage (debounced)
        clearTimeout(this._saveTimer);
        this._saveTimer = setTimeout(() => this.saveLocalState(), 500);
    }
    
    /**
     * WebSocket setup for real-time sync
     */
    setupWebSocket() {
        const userId = window.ZOE_USER_ID || 'default';
        const deviceId = window.ZOE_DEVICE_ID || 'browser-' + Date.now();
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Use /api/ws/device path which is proxied through nginx
        const wsUrl = `${wsProtocol}//${window.location.host}/api/ws/device?user_id=${userId}&device_id=${deviceId}`;
        
        try {
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                this.setState({ wsConnected: true });
                console.log('🎵 MusicState: WebSocket connected');
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (e) {
                    console.error('WebSocket message parse error:', e);
                }
            };
            
            this.ws.onclose = () => {
                this.setState({ wsConnected: false });
                // Reconnect after 5 seconds
                setTimeout(() => this.setupWebSocket(), 5000);
            };
            
            this.ws.onerror = (e) => {
                console.error('WebSocket error:', e);
            };
        } catch (e) {
            console.error('WebSocket setup failed:', e);
        }
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'zone_state':
                // Update zone playback state
                if (data.zone_id === this.state.currentZone?.id) {
                    this.setState({
                        isPlaying: data.state.is_playing,
                        currentTrack: data.state.track_info,
                        position: data.state.position_ms || 0,
                        volume: data.state.volume || 80,
                        queue: data.state.queue || []
                    });
                    this.emit('zoneStateUpdated', data.state);
                }
                break;
                
            case 'zone_list':
                this.setState({ availableZones: data.zones });
                this.emit('zonesUpdated', data.zones);
                break;
                
            case 'device_list':
                this.setState({ availableDevices: data.devices });
                this.emit('devicesUpdated', data.devices);
                break;
                
            case 'media_play':
                this.playUrl(data.url, data.track_info);
                break;
                
            case 'media_pause':
                this.pause();
                break;
                
            case 'media_resume':
                this.resume();
                break;
                
            case 'media_seek':
                this.seekLocal(data.position_ms);
                break;
                
            case 'media_volume':
                this.setVolumeLocal(data.volume);
                break;
        }
    }
    
    /**
     * API helper
     */
    async apiRequest(endpoint, options = {}) {
        const headers = {
            'X-Session-ID': this.getSessionId(),
            ...options.headers
        };
        
        if (options.body && typeof options.body === 'object') {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(options.body);
        }
        
        try {
            const response = await fetch(endpoint, { ...options, headers });
            
            if (response.status === 401) {
                this.emit('authRequired');
                return null;
            }
            
            return await response.json();
        } catch (error) {
            console.error('API request failed:', error);
            return null;
        }
    }
    
    getSessionId() {
        const cookies = document.cookie.split(';');
        for (const cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'zoe_session_id') return value;
        }
        return 'dev-localhost';
    }
    
    /**
     * Auth methods
     */
    async checkAuth() {
        const data = await this.apiRequest('/api/music/auth/status');
        if (data) {
            const isAuthenticated = data.authenticated && data.api_working !== false;
            this.setState({ isAuthenticated });
            this.emit('authStatusChanged', isAuthenticated);
        }
    }
    
    /**
     * Zone methods
     */
    async loadZones() {
        const data = await this.apiRequest('/api/music/zones');
        if (data?.zones) {
            this.setState({ availableZones: data.zones });
            
            // Set default zone if not set
            if (!this.state.currentZone && data.zones.length > 0) {
                this.selectZone(data.zones[0]);
            }
        }
    }
    
    async loadDevices() {
        const data = await this.apiRequest('/api/music/devices');
        if (data) {
            this.setState({ availableDevices: data });
            this.emit('devicesUpdated', data);
        }
    }
    
    selectZone(zone) {
        this.setState({ currentZone: zone });
        this.emit('zoneChanged', zone);
        
        // Join zone via WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'join_zone',
                zone_id: zone.id,
                as: 'both' // player and controller
            }));
        }
        
        // Load zone state
        this.loadZoneState(zone.id);
    }
    
    async loadZoneState(zoneId) {
        const data = await this.apiRequest(`/api/music/zones/${zoneId}/state`);
        if (data) {
            this.setState({
                isPlaying: data.is_playing || false,
                currentTrack: data.track_info,
                position: data.position_ms || 0,
                volume: data.volume || 80,
                queue: data.queue || []
            });
            this.emit('zoneStateUpdated', data);
        }
    }
    
    /**
     * Playback state
     */
    async loadPlaybackState() {
        const data = await this.apiRequest('/api/music/state');
        if (data && data.track_id) {
            this.setState({
                isPlaying: data.is_playing || false,
                currentTrack: {
                    id: data.track_id,
                    title: data.track_title || 'Unknown',
                    artist: data.artist || '',
                    album: data.album || '',
                    thumbnail: data.album_art_url
                },
                position: data.position_ms || 0
            });
            this.emit('trackChanged', this.state.currentTrack);
        }
    }
    
    /**
     * Playback controls
     */
    async play(trackId, options = {}) {
        const trackInfo = this.state.trackCache[trackId] || options.trackInfo || { id: trackId };
        console.log('🎵 play() trackInfo from cache:', trackId, trackInfo);
        
        // Use target device if selected (for casting)
        const targetDevice = options.deviceId || this.state.targetDevice?.id;
        
        const data = await this.apiRequest('/api/music/play', {
            method: 'POST',
            body: {
                track_id: trackId,
                target_device_id: targetDevice,
                mode: this.state.playMode,
                zone_id: this.state.currentZone?.id
            }
        });
        
        if (data?.success) {
            // Update state with track info - try multiple sources for thumbnail
            const thumbnail = trackInfo.thumbnail 
                || trackInfo.thumbnail_url 
                || data.track_info?.thumbnail_url 
                || data.track_info?.album_art_url 
                || '';
            
            const fullTrackInfo = {
                id: trackId,
                title: trackInfo.title || data.track_info?.title || 'Unknown',
                artist: trackInfo.artist || data.track_info?.artist || '',
                album: trackInfo.album || data.track_info?.album || '',
                thumbnail: thumbnail
            };
            
            console.log('🎵 play() fullTrackInfo:', fullTrackInfo);
            
            this.setState({
                isPlaying: true,
                currentTrack: fullTrackInfo
            });
            
            // Actually play the audio if we have a stream URL
            if (data.stream_url) {
                this.playUrl(data.stream_url, fullTrackInfo);
            }
            
            this.emit('trackChanged', this.state.currentTrack);
            this.emit('playStarted', { trackId, streamUrl: data.stream_url });
            
            return data;
        }
        
        return null;
    }
    
    async pause() {
        if (this.audioElement) {
            this.audioElement.pause();
        }
        if (this.ytPlayer && this.ytPlayerReady) {
            try { this.ytPlayer.pauseVideo(); } catch (e) {}
        }
        
        this.setState({ isPlaying: false });
        await this.apiRequest('/api/music/pause', { method: 'POST' });
        this.emit('playPaused');
    }
    
    async resume() {
        if (this.audioElement && this.audioElement.src) {
            this.audioElement.play().catch(e => console.error('Resume failed:', e));
        }
        if (this.ytPlayer && this.ytPlayerReady) {
            try { this.ytPlayer.playVideo(); } catch (e) {}
        }
        
        this.setState({ isPlaying: true });
        await this.apiRequest('/api/music/resume', { method: 'POST' });
        this.emit('playResumed');
    }
    
    async skip() {
        console.log('🎵 skip() called, playSource:', this.state.playSource, 'playlist:', this.state.playlist.length, 'index:', this.state.playlistIndex);
        this.reportPlayEnd();
        
        // Use local playlist if available AND we're in playlist mode
        if (this.state.playSource !== 'queue' && this.state.playlist.length > 0 && this.state.playlistIndex >= 0) {
            const nextIndex = this.state.playlistIndex + 1;
            console.log('🎵 skip() using local playlist, nextIndex:', nextIndex);
            if (nextIndex < this.state.playlist.length) {
                this.setState({ playlistIndex: nextIndex });
                return this.play(this.state.playlist[nextIndex]);
            }
        }
        
        // Fall back to server queue
        console.log('🎵 skip() using server queue');
        const data = await this.apiRequest('/api/music/skip', { method: 'POST' });
        console.log('🎵 skip() server response:', data);
        
        if (data?.success && data?.stream_url) {
            // Server returned next track with stream URL
            this.playUrl(data.stream_url, data.track_info);
            this.emit('trackSkipped', data);
            return data;
        } else if (data?.queue_empty) {
            console.log('🎵 skip() queue is empty');
            this.emit('queueEmpty');
            return null;
        }
        
        this.emit('queueEmpty');
        return null;
    }
    
    async previous() {
        // Use local playlist if available
        if (this.state.playlist.length > 0 && this.state.playlistIndex > 0) {
            const prevIndex = this.state.playlistIndex - 1;
            this.setState({ playlistIndex: prevIndex });
            return this.play(this.state.playlist[prevIndex]);
        }
        
        const data = await this.apiRequest('/api/music/previous', { method: 'POST' });
        if (data?.success) {
            this.emit('trackPrevious', data);
            return data;
        }
        return null;
    }
    
    async seek(positionMs) {
        await this.apiRequest('/api/music/seek', {
            method: 'POST',
            body: { position_ms: positionMs }
        });
        this.seekLocal(positionMs);
    }
    
    seekLocal(positionMs) {
        if (this.audioElement) {
            this.audioElement.currentTime = positionMs / 1000;
        }
        this.setState({ position: positionMs });
        this.emit('seeked', positionMs);
    }
    
    async setVolume(volume) {
        this.setVolumeLocal(volume);
        await this.apiRequest('/api/music/volume', {
            method: 'POST',
            body: { volume }
        });
    }
    
    setVolumeLocal(volume) {
        this.setState({ volume });
        if (this.audioElement) {
            this.audioElement.volume = volume / 100;
        }
        this.emit('volumeChanged', volume);
    }
    
    setPlayMode(mode) {
        this.setState({ playMode: mode });
        this.emit('playModeChanged', mode);
    }
    
    /**
     * Queue management
     */
    async addToQueue(trackId, trackInfo) {
        // If nothing is playing, play this track directly instead of queueing
        if (!this.state.currentTrack || !this.state.isPlaying) {
            return this.play(trackId, { trackInfo: trackInfo || this.state.trackCache[trackId] });
        }
        
        const data = await this.apiRequest('/api/music/queue', {
            method: 'POST',
            body: { track_id: trackId, track_info: trackInfo || this.state.trackCache[trackId] }
        });
        
        if (data?.success) {
            this.emit('queueUpdated', data.queue);
        }
        return data;
    }
    
    setPlaylist(trackIds) {
        this.setState({ playlist: trackIds, playlistIndex: -1, playSource: 'playlist' });
        this.emit('playlistUpdated', trackIds);
    }
    
    useQueueMode() {
        // Clear local playlist to use server queue
        this.setState({ playlist: [], playlistIndex: -1, playSource: 'queue' });
        console.log('🎵 MusicState: Switched to queue mode');
    }
    
    /**
     * Track interactions
     */
    async likeTrack(trackId) {
        trackId = trackId || this.state.currentTrack?.id;
        if (!trackId) return null;
        
        const data = await this.apiRequest(`/api/music/like/${trackId}`, { method: 'POST' });
        if (data?.success) {
            this.emit('trackLiked', trackId);
        }
        return data;
    }
    
    reportPlayEnd() {
        if (!this.state.currentTrack?.id) return;
        
        const position = this.state.position;
        const duration = this.state.duration;
        
        if (duration > 0) {
            const eventType = position / duration > 0.3 ? 'play_end' : 'skip';
            // Ensure values are integers for the API
            this.apiRequest('/api/music/event', {
                method: 'POST',
                body: {
                    track_id: String(this.state.currentTrack.id),
                    event_type: eventType,
                    position_ms: Math.round(position),
                    duration_ms: Math.round(duration)
                }
            }).catch(e => console.warn('Event tracking failed:', e));
        }
    }
    
    /**
     * Search
     */
    async search(query) {
        const data = await this.apiRequest(`/api/music/search?q=${encodeURIComponent(query)}&limit=20`);
        if (data?.results) {
            // Cache track info
            data.results.forEach(track => {
                const id = track.videoId || track.id;
                const rawThumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
                // Upgrade to larger thumbnail for album art display
                const thumbnail = rawThumbnail ? rawThumbnail.replace(/=w\d+-h\d+/, '=w300-h300') : '';
                this.state.trackCache[id] = {
                    id,
                    title: track.title || 'Unknown',
                    artist: track.artist || track.artists?.[0]?.name || 'Unknown',
                    album: track.album || '',
                    duration: track.duration || '',
                    thumbnail
                };
            });
            this.emit('searchResults', data.results);
        }
        return data?.results || [];
    }
    
    /**
     * Recommendations
     */
    async loadRecommendations(type = 'radio') {
        let endpoint = '/api/music/radio';
        if (type === 'discover') endpoint = '/api/music/discover';
        if (type === 'queue') endpoint = '/api/music/queue';
        
        const data = await this.apiRequest(`${endpoint}?limit=20`);
        const tracks = data?.tracks || data?.queue || [];
        
        // Cache track info
        tracks.forEach(track => {
            const id = track.videoId || track.id;
            const rawThumbnail = track.thumbnail_url || track.thumbnails?.[0]?.url || '';
            // Upgrade to larger thumbnail for album art display
            const thumbnail = rawThumbnail ? rawThumbnail.replace(/=w\d+-h\d+/, '=w300-h300') : '';
            this.state.trackCache[id] = {
                id,
                title: track.title || 'Unknown',
                artist: track.artist || track.artists?.[0]?.name || 'Unknown',
                album: track.album || '',
                duration: track.duration || '',
                thumbnail
            };
        });
        
        this.emit('recommendationsLoaded', { type, tracks });
        return tracks;
    }
    
    /**
     * Stats
     */
    async loadStats() {
        const data = await this.apiRequest('/api/music/stats');
        if (data) {
            this.emit('statsLoaded', data);
        }
        return data;
    }
    
    /**
     * Audio element management
     */
    getAudioElement() {
        if (!this.audioElement) {
            // Use shared audio from mini-player if available (for seamless page transitions)
            if (window.ZOE_SHARED_AUDIO) {
                console.log('🎵 MusicState: Using shared audio from mini-player');
                this.audioElement = window.ZOE_SHARED_AUDIO;
                
                // Update our state from the playing audio
                if (!this.audioElement.paused) {
                    this.setState({ isPlaying: true });
                    this.currentStreamUrl = this.audioElement.src;
                }
            } else {
                this.audioElement = new Audio();
            }
            
            // Always attach our listeners for music page UI updates
            this.audioElement.addEventListener('timeupdate', () => {
                const position = this.audioElement.currentTime * 1000;
                const duration = (this.audioElement.duration || 0) * 1000;
                if (!isNaN(duration)) {
                    this.setState({ position, duration });
                    this.emit('progress', { position, duration });
                }
            });
            
            this.audioElement.addEventListener('play', () => {
                this.setState({ isPlaying: true });
            });
            
            this.audioElement.addEventListener('pause', () => {
                this.setState({ isPlaying: false });
            });
            
            this.audioElement.addEventListener('ended', () => {
                this.reportPlayEnd();
                this.skip();
            });
            
            this.audioElement.addEventListener('error', (e) => {
                console.error('Audio error:', e);
                this.emit('playbackError', e);
            });
        }
        return this.audioElement;
    }
    
    playUrl(url, trackInfo) {
        const audio = this.getAudioElement();
        audio.src = url;
        
        // Store stream URL in state for mini-player persistence
        this.currentStreamUrl = url;
        
        audio.play().catch(e => {
            console.error('Play failed:', e);
            this.emit('playbackError', e);
        });
        
        if (trackInfo) {
            const trackId = trackInfo.track_id || trackInfo.videoId || trackInfo.id;
            // Get cached info if available for more complete data
            const cached = this.state.trackCache[trackId] || {};
            
            const newTrack = {
                id: trackId,
                title: trackInfo.title || cached.title || 'Unknown',
                artist: trackInfo.artist || cached.artist || '',
                album: trackInfo.album || cached.album || '',
                thumbnail: trackInfo.thumbnail || trackInfo.thumbnail_url || trackInfo.album_art_url || cached.thumbnail || ''
            };
            
            this.setState({
                currentTrack: newTrack,
                isPlaying: true
            });
            this.emit('trackChanged', this.state.currentTrack);
        }
        
        // Force save to localStorage for mini-player
        this.saveLocalState();
    }
    
    /**
     * Local storage
     */
    saveLocalState() {
        const toSave = {
            volume: this.state.volume,
            playMode: this.state.playMode,
            currentZone: this.state.currentZone
        };
        try {
            localStorage.setItem('zoe_music_state', JSON.stringify(toSave));
            
            // Get current track with enriched info from cache
            let trackToSave = this.state.currentTrack;
            if (trackToSave?.id && this.state.trackCache[trackToSave.id]) {
                const cached = this.state.trackCache[trackToSave.id];
                trackToSave = {
                    ...trackToSave,
                    title: trackToSave.title !== 'Unknown' ? trackToSave.title : cached.title,
                    artist: trackToSave.artist || cached.artist,
                    album: trackToSave.album || cached.album,
                    thumbnail: trackToSave.thumbnail || cached.thumbnail
                };
            }
            
            // Also save playback state for mini player on other pages
            const playbackState = {
                currentTrack: trackToSave,
                isPlaying: this.state.isPlaying,
                volume: this.state.volume,
                position: this.state.position,
                streamUrl: this.currentStreamUrl || this.audioElement?.src || null
            };
            console.log('🎵 MusicState: Saving playback state:', playbackState);
            localStorage.setItem('zoe_music_playback', JSON.stringify(playbackState));
        } catch (e) {
            console.warn('Could not save music state:', e);
        }
    }
    
    loadLocalState() {
        try {
            const saved = localStorage.getItem('zoe_music_state');
            if (saved) {
                const data = JSON.parse(saved);
                this.setState({
                    volume: data.volume ?? 80,
                    playMode: data.playMode ?? 'audio',
                    currentZone: data.currentZone ?? null
                });
            }
            
            // Also restore playback state if available
            const playbackState = localStorage.getItem('zoe_music_playback');
            if (playbackState) {
                const pb = JSON.parse(playbackState);
                console.log('🎵 MusicState: Restoring playback state:', pb);
                
                if (pb.currentTrack) {
                    this.setState({
                        currentTrack: pb.currentTrack,
                        isPlaying: pb.isPlaying || false
                    });
                }
                
                // Store stream URL for resume (mini-player handles actual audio resume)
                if (pb.streamUrl) {
                    this.currentStreamUrl = pb.streamUrl;
                    this.savedPosition = pb.position || 0;
                }
            }
        } catch (e) {
            console.warn('Could not load music state:', e);
        }
    }
    
    /**
     * Resume playback from saved state (call after user interaction)
     */
    resumeFromSavedState() {
        if (this.currentStreamUrl && this.state.currentTrack) {
            console.log('🎵 MusicState: Resuming from saved state');
            const audio = this.getAudioElement();
            audio.src = this.currentStreamUrl;
            
            // Seek to saved position
            if (this.savedPosition > 0) {
                audio.currentTime = this.savedPosition / 1000;
            }
            
            audio.play().catch(e => console.warn('Resume failed:', e));
            this.setState({ isPlaying: true });
            return true;
        }
        return false;
    }
    
    /**
     * Cleanup
     */
    destroy() {
        if (this.ws) {
            this.ws.close();
        }
        if (this.audioElement) {
            this.audioElement.pause();
            this.audioElement.src = '';
        }
        this.listeners.clear();
    }
}

// Singleton instance
const MusicState = new MusicStateManager();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => MusicState.init());
} else {
    MusicState.init();
}

// Expose globally
window.MusicState = MusicState;


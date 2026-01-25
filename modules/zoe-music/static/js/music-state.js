/**
 * Music State Manager - MCP Edition
 * ==================================
 * 
 * Refactored to use MCP (Model Context Protocol) for module-based architecture.
 * Uses MCP tools where available, with REST API fallback for features not yet migrated.
 * 
 * Architecture:
 * - Discovers music capabilities via MCP Client
 * - Calls music module tools directly (no hardcoded endpoints)
 * - Graceful degradation if music module disabled
 * 
 * Version: 2.0.0 (MCP-based)
 */

class MCPMusicStateManager {
    constructor() {
        this.mcp = null;
        this.currentStreamUrl = null;
        
        this.state = {
            // Playback state
            isPlaying: false,
            currentTrack: null,
            position: 0,
            duration: 0,
            volume: 80,
            playMode: 'audio',
            
            // Playlist/Queue
            queue: [],
            playlist: [],
            playlistIndex: -1,
            
            // Auth & Zones (deprecated in MCP, kept for compatibility)
            isAuthenticated: true, // MCP doesn't require explicit auth
            currentZone: null,
            availableZones: [],
            availableDevices: [],
            targetDevice: null,
            
            // Cache
            trackCache: {},
            
            // Connection
            wsConnected: false,
            mcpAvailable: false
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
     * Initialize with MCP client
     */
    async init() {
        console.log('🎵 MCPMusicState: Initializing...');
        this.initialized = false;
        
        try {
            // Initialize MCP client (use relative URL for proper routing)
            this.mcp = new MCPClient({
                mcpServerUrl: '/api/mcp'
            });
            
            await this.mcp.init();
            
            // Check if music module is enabled
            if (this.mcp.isModuleEnabled('music')) {
                console.log('✅ Music module detected via MCP');
                this.setState({ mcpAvailable: true });
            } else {
                console.warn('⚠️ Music module not available');
                throw new Error('Music module not enabled');
            }
            
        } catch (error) {
            console.error('❌ MCP initialization failed:', error);
            console.error('⚠️  Music module requires MCP to function');
            this.setState({ mcpAvailable: false });
            throw new Error('MCP initialization required for music module');
        }
        
        // Setup WebSocket
        this.setupWebSocket();
        
        // Load saved state
        this.loadLocalState();
        
        // Initialize audio element
        setTimeout(() => {
            this.getAudioElement();
        }, 50);
        
        // Load initial state
        await Promise.all([
            this.loadPlaybackState(),
            this.loadOutputDevices()
        ]);
        
        this.initialized = true;
        console.log('✅ MCPMusicState: Ready');
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
                } catch (error) {
                    console.error(`Error in ${event} listener:`, error);
                }
            });
        }
    }
    
    /**
     * State management
     */
    setState(updates) {
        const oldState = { ...this.state };
        this.state = { ...this.state, ...updates };
        this.emit('stateChanged', { old: oldState, new: this.state });
    }
    
    getState() {
        return { ...this.state };
    }
    
    /**
     * WebSocket setup (for real-time updates)
     */
    setupWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws/device?user_id=${this.getSessionId()}&device_id=browser-music-${Date.now()}`;
        
        try {
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('🔌 MCPMusicState: WebSocket connected');
                this.setState({ wsConnected: true });
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    this.handleWebSocketMessage(message);
                } catch (error) {
                    console.error('WebSocket message error:', error);
                }
            };
            
            this.ws.onclose = () => {
                console.log('🔌 MCPMusicState: WebSocket disconnected');
                this.setState({ wsConnected: false });
                
                // Reconnect after 5 seconds
                setTimeout(() => this.setupWebSocket(), 5000);
            };
        } catch (error) {
            console.error('WebSocket setup failed:', error);
        }
    }
    
    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'music_state_update':
                this.setState({
                    isPlaying: message.data.is_playing,
                    currentTrack: message.data.track_info,
                    position: message.data.position_ms
                });
                break;
            
            case 'music_track_changed':
                this.setState({ currentTrack: message.data.track_info });
                this.emit('trackChanged', message.data.track_info);
                break;
            
            case 'music_queue_updated':
                this.setState({ queue: message.data.queue });
                this.emit('queueUpdated', message.data.queue);
                break;
        }
    }
    
    /**
     * Get session ID
     */
    getSessionId() {
        if (window.zoeAuth && window.zoeAuth.getSession) {
            const session = window.zoeAuth.getSession();
            if (session) return session;
        }
        const cookies = document.cookie.split(';');
        for (const cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'zoe_session_id') return value;
        }
        return 'dev-localhost';
    }
    
    /**
     * Local state persistence
     */
    loadLocalState() {
        try {
            const saved = localStorage.getItem('zoe_music_state');
            if (saved) {
                const state = JSON.parse(saved);
                this.setState({
                    volume: state.volume || 80,
                    playMode: state.playMode || 'audio',
                    targetDevice: state.targetDevice || null
                });
            }
        } catch (error) {
            console.error('Failed to load local state:', error);
        }
    }
    
    saveLocalState() {
        clearTimeout(this._saveTimer);
        this._saveTimer = setTimeout(() => {
            try {
                const toSave = {
                    volume: this.state.volume,
                    playMode: this.state.playMode,
                    targetDevice: this.state.targetDevice
                };
                localStorage.setItem('zoe_music_state', JSON.stringify(toSave));
            } catch (error) {
                console.error('Failed to save local state:', error);
            }
        }, 1000);
    }
    
    /**
     * Playback controls - MCP-based
     */
    async play(trackId, options = {}) {
        const trackInfo = this.state.trackCache[trackId] || options.trackInfo || { id: trackId };
        console.log('🎵 MCPMusicState: play()', trackId, trackInfo);
        console.log('🔍 MCP Status:', { 
            mcpAvailable: this.state.mcpAvailable, 
            mcpExists: !!this.mcp,
            initialized: this.initialized 
        });
        
        if (!this.state.mcpAvailable || !this.mcp) {
            const error = `MCP not available - mcpAvailable: ${this.state.mcpAvailable}, mcp exists: ${!!this.mcp}`;
            console.error('❌', error);
            throw new Error(error);
        }
        
        try {
            const result = await this.mcp.callTool('music_play_song', {
                track_id: trackId,
                user_id: this.getSessionId()
            });
            
            if (result.success) {
                const fullTrackInfo = {
                    id: trackId,
                    title: trackInfo.title || result.track_info?.title || 'Unknown',
                    artist: trackInfo.artist || result.track_info?.artist || '',
                    album: trackInfo.album || result.track_info?.album || '',
                    thumbnail: trackInfo.thumbnail || result.track_info?.thumbnail_url || ''
                };
                
                this.setState({
                    isPlaying: true,
                    currentTrack: fullTrackInfo
                });
                
                if (result.stream_url) {
                    this.playUrl(result.stream_url, fullTrackInfo);
                }
                
                this.emit('trackChanged', fullTrackInfo);
                this.emit('playStarted', { trackId, streamUrl: result.stream_url });
                
                return result;
            }
        } catch (error) {
            console.error('❌ MCPMusicState: play() failed:', error);
            throw error;
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
        
        if (this.state.mcpAvailable && this.mcp) {
            await this.mcp.callTool('music_pause', {
                user_id: this.getSessionId()
            });
        }
        
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
        
        if (this.state.mcpAvailable && this.mcp) {
            await this.mcp.callTool('music_resume', {
                user_id: this.getSessionId()
            });
        }
        
        this.emit('playResumed');
    }
    
    async skip() {
        console.log('🎵 MCPMusicState: skip()');
        this.reportPlayEnd();
        
        if (this.state.mcpAvailable && this.mcp) {
            const result = await this.mcp.callTool('music_skip', {
                user_id: this.getSessionId()
            });
            
            if (result.success && result.stream_url) {
                this.playUrl(result.stream_url, result.track_info);
                this.emit('trackSkipped', result);
                return result;
            } else if (result.queue_empty) {
                this.emit('queueEmpty');
                return null;
            }
        }
        
        this.emit('queueEmpty');
        return null;
    }
    
    async setVolume(volume) {
        this.setVolumeLocal(volume);
        
        if (this.state.mcpAvailable && this.mcp) {
            await this.mcp.callTool('music_set_volume', {
                volume,
                user_id: this.getSessionId()
            });
        }
    }
    
    setVolumeLocal(volume) {
        this.setState({ volume });
        if (this.audioElement) {
            this.audioElement.volume = volume / 100;
        }
        this.emit('volumeChanged', volume);
        this.saveLocalState();
    }
    
    /**
     * Queue management - MCP-based
     */
    async addToQueue(trackId, trackInfo) {
        // If nothing is playing, play directly
        if (!this.state.currentTrack || !this.state.isPlaying) {
            return this.play(trackId, { trackInfo });
        }
        
        if (this.state.mcpAvailable && this.mcp) {
            const result = await this.mcp.callTool('music_add_to_queue', {
                track_id: trackId,
                title: trackInfo?.title,
                artist: trackInfo?.artist,
                user_id: this.getSessionId()
            });
            
            if (result.success) {
                this.emit('queueUpdated', result.queue);
            }
            return result;
        }
        
        return null;
    }
    
    async getQueue() {
        if (this.state.mcpAvailable && this.mcp) {
            const result = await this.mcp.callTool('music_get_queue', {
                user_id: this.getSessionId()
            });
            
            if (result.queue) {
                this.setState({ queue: result.queue });
                return result.queue;
            }
        }
        
        return [];
    }
    
    /**
     * Search - MCP-based
     */
    async search(query, filterType = 'songs', limit = 10) {
        if (!this.state.mcpAvailable || !this.mcp) {
            console.warn('MCP not available for search');
            return { results: [], count: 0 };
        }
        
        try {
            const result = await this.mcp.callTool('music_search', {
                query,
                filter_type: filterType,
                limit,
                user_id: this.getSessionId()
            });
            
            // Cache track results
            if (result.results) {
                result.results.forEach(track => {
                    this.state.trackCache[track.id] = track;
                });
            }
            
            return result;
        } catch (error) {
            console.error('Search failed:', error);
            return { results: [], count: 0 };
        }
    }
    
    /**
     * Get recommendations - MCP-based
     */
    async getRecommendations() {
        if (this.state.mcpAvailable && this.mcp) {
            const result = await this.mcp.callTool('music_get_recommendations', {
                user_id: this.getSessionId()
            });
            
            return result.recommendations || [];
        }
        
        return [];
    }
    
    /**
     * Generic API request helper (for REST endpoints)
     * Used by widgets that need direct REST API access
     */
    async apiRequest(url, options = {}) {
        try {
            const response = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error(`API request failed: ${url}`, error);
            throw error;
        }
    }
    
    /**
     * Playback state loading
     */
    async loadPlaybackState() {
        if (this.state.mcpAvailable && this.mcp) {
            try {
                const result = await this.mcp.callTool('music_get_context', {
                    user_id: this.getSessionId()
                });
                
                if (result.raw_context) {
                    const ctx = result.raw_context;
                    if (ctx.current_track) {
                        this.setState({
                            isPlaying: ctx.is_playing || false,
                            currentTrack: {
                                id: ctx.current_track.track_id,
                                title: ctx.current_track.title || 'Unknown',
                                artist: ctx.current_track.artist || '',
                                album: ctx.current_track.album || '',
                                thumbnail: ctx.current_track.album_art_url
                            },
                            position: ctx.position_ms || 0
                        });
                        this.emit('trackChanged', this.state.currentTrack);
                    }
                }
            } catch (error) {
                console.warn('Failed to load playback state:', error);
            }
        }
    }
    
    /**
     * Device management (stubbed for compatibility)
     */
    async loadOutputDevices() {
        // TODO: Add device discovery MCP tool
        const devices = [
            { id: 'browser', name: 'This Device', type: 'Browser', available: true }
        ];
        this.setState({ availableDevices: devices });
        this.emit('devicesUpdated', devices);
    }
    
    selectOutputDevice(device) {
        if (!device || device.id === 'browser') {
            this.setState({ targetDevice: null });
            this.emit('outputDeviceChanged', null);
        } else {
            this.setState({ targetDevice: device });
            this.emit('outputDeviceChanged', device);
        }
        this.saveLocalState();
    }
    
    /**
     * Audio element management
     */
    getAudioElement() {
        // Try to get shared audio element from mini-player
        this.audioElement = document.getElementById('zoe-shared-audio');
        
        if (!this.audioElement) {
            this.audioElement = document.createElement('audio');
            this.audioElement.id = 'zoe-shared-audio';
            this.audioElement.preload = 'auto';
            document.body.appendChild(this.audioElement);
        }
        
        // Setup event listeners
        this.audioElement.addEventListener('timeupdate', () => {
            if (this.audioElement) {
                const position = Math.floor(this.audioElement.currentTime * 1000);
                this.setState({ position });
            }
        });
        
        this.audioElement.addEventListener('ended', () => {
            this.skip();
        });
        
        // Set initial volume
        this.audioElement.volume = this.state.volume / 100;
        
        return this.audioElement;
    }
    
    playUrl(streamUrl, trackInfo) {
        console.log('🎵 MCPMusicState: playUrl()', streamUrl);
        this.currentStreamUrl = streamUrl;
        
        if (!this.audioElement) {
            this.getAudioElement();
        }
        
        this.audioElement.src = streamUrl;
        this.audioElement.play().catch(error => {
            console.error('Playback failed:', error);
            this.emit('playbackError', error);
        });
        
        this.setState({ isPlaying: true, currentTrack: trackInfo });
    }
    
    reportPlayEnd() {
        // TODO: Report analytics via MCP if needed
    }
    
    setPlayMode(mode) {
        this.setState({ playMode: mode });
        this.emit('playModeChanged', mode);
        this.saveLocalState();
    }
    
    setPlaylist(trackIds) {
        this.setState({ playlist: trackIds, playlistIndex: -1 });
        this.emit('playlistUpdated', trackIds);
    }
    
    /**
     * Check if MCP is available
     */
    isMCPAvailable() {
        return this.state.mcpAvailable;
    }
}

// Export
if (typeof window !== 'undefined') {
    window.MCPMusicStateManager = MCPMusicStateManager;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = MCPMusicStateManager;
}

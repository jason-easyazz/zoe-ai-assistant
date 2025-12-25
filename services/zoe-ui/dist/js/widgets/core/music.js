/**
 * Music Player Widget
 * YouTube Music integration with playback controls
 * Version: 1.0.0
 */

class MusicWidget extends WidgetModule {
    constructor() {
        super('music', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: 5000 // Update state every 5 seconds
        });
        
        this.state = {
            isPlaying: false,
            trackId: null,
            title: 'Not Playing',
            artist: '',
            album: '',
            albumArt: null,
            position: 0,
            duration: 0,
            volume: 100,
            queue: [],
            isAuthenticated: false,
            error: null
        };
        
        this.ws = null;
        this.audioElement = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-content">
                <!-- Album art background blur -->
                <div class="music-backdrop" id="music-backdrop"></div>
                
                <!-- Not authenticated state -->
                <div class="music-auth-required" id="music-auth-required" style="display: none;">
                    <div class="auth-icon">🔑</div>
                    <div class="auth-message">Connect YouTube Music</div>
                    <button class="auth-button" id="music-connect-btn">Connect</button>
                </div>
                
                <!-- Main player -->
                <div class="music-player" id="music-player">
                    <!-- Album art -->
                    <div class="music-album-art" id="music-album-art">
                        <div class="album-art-placeholder">🎵</div>
                    </div>
                    
                    <!-- Track info -->
                    <div class="music-track-info">
                        <div class="track-title" id="music-title">Not Playing</div>
                        <div class="track-artist" id="music-artist">--</div>
                    </div>
                    
                    <!-- Progress bar -->
                    <div class="music-progress">
                        <span class="time-current" id="music-time-current">0:00</span>
                        <div class="progress-bar" id="music-progress-bar">
                            <div class="progress-fill" id="music-progress-fill"></div>
                        </div>
                        <span class="time-duration" id="music-time-duration">0:00</span>
                    </div>
                    
                    <!-- Controls -->
                    <div class="music-controls">
                        <button class="control-btn" id="music-prev" title="Previous">⏮️</button>
                        <button class="control-btn control-btn-main" id="music-play-pause" title="Play/Pause">▶️</button>
                        <button class="control-btn" id="music-next" title="Next">⏭️</button>
                    </div>
                    
                    <!-- Volume (expandable) -->
                    <div class="music-volume" id="music-volume-section">
                        <button class="volume-btn" id="music-volume-btn" title="Volume">🔊</button>
                        <input type="range" class="volume-slider" id="music-volume-slider" min="0" max="100" value="100" style="display: none;">
                    </div>
                </div>
                
                <!-- Search overlay -->
                <div class="music-search-overlay" id="music-search-overlay" style="display: none;">
                    <input type="text" class="music-search-input" id="music-search-input" placeholder="Search music...">
                    <div class="music-search-results" id="music-search-results"></div>
                </div>
                
                <!-- Hidden audio element for browser playback -->
                <audio id="music-audio-element" style="display: none;"></audio>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        this.audioElement = element.querySelector('#music-audio-element');
        
        // Setup event listeners
        this.setupEventListeners(element);
        
        // Check authentication and load initial state
        this.checkAuth();
        this.loadState();
        
        // Setup WebSocket for real-time updates
        this.setupWebSocket();
    }
    
    setupEventListeners(element) {
        // Play/Pause button
        const playPauseBtn = element.querySelector('#music-play-pause');
        if (playPauseBtn) {
            playPauseBtn.addEventListener('click', () => this.togglePlayPause());
        }
        
        // Previous track
        const prevBtn = element.querySelector('#music-prev');
        if (prevBtn) {
            prevBtn.addEventListener('click', () => this.previous());
        }
        
        // Next track
        const nextBtn = element.querySelector('#music-next');
        if (nextBtn) {
            nextBtn.addEventListener('click', () => this.skip());
        }
        
        // Volume controls
        const volumeBtn = element.querySelector('#music-volume-btn');
        const volumeSlider = element.querySelector('#music-volume-slider');
        
        if (volumeBtn && volumeSlider) {
            volumeBtn.addEventListener('click', () => {
                volumeSlider.style.display = volumeSlider.style.display === 'none' ? 'inline-block' : 'none';
            });
            
            volumeSlider.addEventListener('input', (e) => {
                this.setVolume(parseInt(e.target.value));
            });
        }
        
        // Progress bar click to seek
        const progressBar = element.querySelector('#music-progress-bar');
        if (progressBar) {
            progressBar.addEventListener('click', (e) => {
                const rect = progressBar.getBoundingClientRect();
                const percent = (e.clientX - rect.left) / rect.width;
                const seekPosition = Math.floor(percent * this.state.duration);
                this.seek(seekPosition);
            });
        }
        
        // Connect button
        const connectBtn = element.querySelector('#music-connect-btn');
        if (connectBtn) {
            connectBtn.addEventListener('click', () => this.showAuthInstructions());
        }
        
        // Audio element events
        if (this.audioElement) {
            this.audioElement.addEventListener('timeupdate', () => {
                this.updateProgress(this.audioElement.currentTime * 1000);
            });
            
            this.audioElement.addEventListener('ended', () => {
                this.skip();
            });
        }
    }
    
    setupWebSocket() {
        const userId = window.ZOE_USER_ID || 'default';
        const deviceId = window.ZOE_DEVICE_ID || 'browser-' + Date.now();
        const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/device?user_id=${userId}&device_id=${deviceId}`;
        
        try {
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            };
            
            this.ws.onclose = () => {
                // Reconnect after 5 seconds
                setTimeout(() => this.setupWebSocket(), 5000);
            };
        } catch (e) {
            console.error('WebSocket connection failed:', e);
        }
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
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
    
    async checkAuth() {
        try {
            const response = await fetch('/api/music/auth/status', {
                headers: this.getAuthHeaders()
            });
            
            if (response.ok) {
                const data = await response.json();
                this.state.isAuthenticated = data.authenticated;
                this.updateAuthUI();
            }
        } catch (e) {
            console.error('Auth check failed:', e);
        }
    }
    
    async loadState() {
        try {
            const response = await fetch('/api/music/state', {
                headers: this.getAuthHeaders()
            });
            
            if (response.ok) {
                const data = await response.json();
                if (data.track_id) {
                    this.state.trackId = data.track_id;
                    this.state.title = data.track_title || 'Unknown';
                    this.state.artist = data.artist || '';
                    this.state.album = data.album || '';
                    this.state.albumArt = data.album_art_url;
                    this.state.isPlaying = data.is_playing || false;
                    this.updateUI();
                }
            }
        } catch (e) {
            console.error('Load state failed:', e);
        }
    }
    
    updateAuthUI() {
        const authRequired = this.element?.querySelector('#music-auth-required');
        const player = this.element?.querySelector('#music-player');
        
        if (this.state.isAuthenticated) {
            if (authRequired) authRequired.style.display = 'none';
            if (player) player.style.display = 'block';
        } else {
            if (authRequired) authRequired.style.display = 'flex';
            if (player) player.style.display = 'none';
        }
    }
    
    updateUI() {
        // Title and artist
        const titleEl = this.element?.querySelector('#music-title');
        const artistEl = this.element?.querySelector('#music-artist');
        
        if (titleEl) titleEl.textContent = this.state.title;
        if (artistEl) artistEl.textContent = this.state.artist;
        
        // Album art
        const albumArtEl = this.element?.querySelector('#music-album-art');
        const backdropEl = this.element?.querySelector('#music-backdrop');
        
        if (this.state.albumArt) {
            if (albumArtEl) {
                albumArtEl.innerHTML = `<img src="${this.state.albumArt}" alt="Album art">`;
            }
            if (backdropEl) {
                backdropEl.style.backgroundImage = `url(${this.state.albumArt})`;
            }
        } else {
            if (albumArtEl) {
                albumArtEl.innerHTML = '<div class="album-art-placeholder">🎵</div>';
            }
        }
        
        // Play/pause button
        const playPauseBtn = this.element?.querySelector('#music-play-pause');
        if (playPauseBtn) {
            playPauseBtn.textContent = this.state.isPlaying ? '⏸️' : '▶️';
        }
    }
    
    updateProgress(positionMs) {
        this.state.position = positionMs;
        
        const currentEl = this.element?.querySelector('#music-time-current');
        const durationEl = this.element?.querySelector('#music-time-duration');
        const progressFill = this.element?.querySelector('#music-progress-fill');
        
        if (currentEl) {
            currentEl.textContent = this.formatTime(positionMs);
        }
        
        if (durationEl && this.state.duration > 0) {
            durationEl.textContent = this.formatTime(this.state.duration);
        }
        
        if (progressFill && this.state.duration > 0) {
            const percent = (positionMs / this.state.duration) * 100;
            progressFill.style.width = `${percent}%`;
        }
    }
    
    formatTime(ms) {
        const totalSeconds = Math.floor(ms / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }
    
    // Playback controls
    async togglePlayPause() {
        if (this.state.isPlaying) {
            await this.pause();
        } else {
            await this.resume();
        }
    }
    
    async pause() {
        this.state.isPlaying = false;
        if (this.audioElement) {
            this.audioElement.pause();
        }
        this.updateUI();
        
        await fetch('/api/music/pause', {
            method: 'POST',
            headers: this.getAuthHeaders()
        });
    }
    
    async resume() {
        this.state.isPlaying = true;
        if (this.audioElement) {
            this.audioElement.play();
        }
        this.updateUI();
        
        await fetch('/api/music/resume', {
            method: 'POST',
            headers: this.getAuthHeaders()
        });
    }
    
    async skip() {
        await fetch('/api/music/skip', {
            method: 'POST',
            headers: this.getAuthHeaders()
        });
        
        // Reload state after skip
        setTimeout(() => this.loadState(), 500);
    }
    
    async previous() {
        await fetch('/api/music/previous', {
            method: 'POST',
            headers: this.getAuthHeaders()
        });
        
        // Reload state after previous
        setTimeout(() => this.loadState(), 500);
    }
    
    async seek(positionMs) {
        await fetch('/api/music/seek', {
            method: 'POST',
            headers: {
                ...this.getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ position_ms: positionMs })
        });
        
        this.seekLocal(positionMs);
    }
    
    seekLocal(positionMs) {
        if (this.audioElement) {
            this.audioElement.currentTime = positionMs / 1000;
        }
        this.updateProgress(positionMs);
    }
    
    async setVolume(volume) {
        this.state.volume = volume;
        this.setVolumeLocal(volume);
        
        await fetch('/api/music/volume', {
            method: 'POST',
            headers: {
                ...this.getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ volume })
        });
    }
    
    setVolumeLocal(volume) {
        this.state.volume = volume;
        if (this.audioElement) {
            this.audioElement.volume = volume / 100;
        }
        
        const volumeSlider = this.element?.querySelector('#music-volume-slider');
        if (volumeSlider) {
            volumeSlider.value = volume;
        }
    }
    
    playUrl(url, trackInfo) {
        if (this.audioElement) {
            this.audioElement.src = url;
            this.audioElement.play();
        }
        
        if (trackInfo) {
            this.state.trackId = trackInfo.track_id || trackInfo.videoId;
            this.state.title = trackInfo.title || 'Unknown';
            this.state.artist = trackInfo.artist || '';
            this.state.albumArt = trackInfo.thumbnail_url || trackInfo.album_art_url;
            this.state.duration = (trackInfo.duration_seconds || 0) * 1000;
        }
        
        this.state.isPlaying = true;
        this.updateUI();
    }
    
    showAuthInstructions() {
        // Show modal or navigate to settings
        window.location.href = '/settings#music';
    }
    
    getAuthHeaders() {
        return {
            'X-Auth-Token': window.ZOE_AUTH_TOKEN || localStorage.getItem('zoe_auth_token') || '',
            'X-Device-Id': window.ZOE_DEVICE_ID || localStorage.getItem('zoe_device_id') || ''
        };
    }
    
    update() {
        // Periodic update - reload state
        this.loadState();
    }
    
    destroy() {
        if (this.ws) {
            this.ws.close();
        }
        if (this.audioElement) {
            this.audioElement.pause();
            this.audioElement.src = '';
        }
        super.destroy();
    }
}

// Export to window for widget system
window.MusicWidget = MusicWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register(new MusicWidget());
}


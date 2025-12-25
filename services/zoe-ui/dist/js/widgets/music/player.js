/**
 * Music Player Widget
 * Now playing display with playback controls
 * Supports multi-zone control and video mode
 * Version: 1.0.0
 */

class MusicPlayerWidget extends WidgetModule {
    constructor() {
        super('music-player', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null // Uses events instead
        });
        
        this.unsubscribers = [];
    }
    
    getTemplate() {
        return `
            <div class="widget-content music-player-widget">
                <!-- Album Art -->
                <div class="mp-album-art" id="mp-album-art">
                    <div class="mp-album-placeholder">🎵</div>
                </div>
                
                <!-- Track Info -->
                <div class="mp-track-info">
                    <div class="mp-title" id="mp-title">Not Playing</div>
                    <div class="mp-artist" id="mp-artist">Search for music to start</div>
                    <div class="mp-album" id="mp-album"></div>
                </div>
                
                <!-- Progress Bar -->
                <div class="mp-progress-section">
                    <div class="mp-progress-bar" id="mp-progress-bar">
                        <div class="mp-progress-fill" id="mp-progress-fill"></div>
                    </div>
                    <div class="mp-times">
                        <span id="mp-time-current">0:00</span>
                        <span id="mp-time-total">0:00</span>
                    </div>
                </div>
                
                <!-- Main Controls -->
                <div class="mp-controls">
                    <button class="mp-control-btn mp-shuffle" id="mp-shuffle" title="Shuffle">🔀</button>
                    <button class="mp-control-btn mp-prev" id="mp-prev" title="Previous">⏮</button>
                    <button class="mp-control-btn mp-play-pause" id="mp-play-pause" title="Play">▶</button>
                    <button class="mp-control-btn mp-next" id="mp-next" title="Next">⏭</button>
                    <button class="mp-control-btn mp-repeat" id="mp-repeat" title="Repeat">🔁</button>
                </div>
                
                <!-- Volume -->
                <div class="mp-volume-section">
                    <span class="mp-volume-icon" id="mp-volume-icon">🔊</span>
                    <input type="range" class="mp-volume-slider" id="mp-volume-slider" 
                           min="0" max="100" value="80">
                </div>
                
                <!-- Mode & Zone -->
                <div class="mp-footer">
                    <div class="mp-mode-toggle">
                        <button class="mp-mode-btn active" id="mp-mode-audio" title="Audio">🎵</button>
                        <button class="mp-mode-btn" id="mp-mode-video" title="Video">🎬</button>
                    </div>
                    <div class="mp-zone-select">
                        <select id="mp-zone-selector" class="mp-zone-dropdown">
                            <option value="">This Device</option>
                        </select>
                    </div>
                </div>
                
                <!-- Quick Actions -->
                <div class="mp-actions">
                    <button class="mp-action-btn" id="mp-like" title="Like">❤️</button>
                    <button class="mp-action-btn" id="mp-add-queue" title="Add to Queue">➕</button>
                    <button class="mp-action-btn" id="mp-fullscreen" title="Fullscreen" style="display:none;">⛶</button>
                </div>
                
                <!-- Video Overlay (hidden by default) -->
                <div class="mp-video-overlay" id="mp-video-overlay" style="display:none;">
                    <div class="mp-video-container" id="mp-video-container"></div>
                    <div class="mp-video-controls">
                        <button class="mp-video-btn" id="mp-video-prev">⏮</button>
                        <button class="mp-video-btn" id="mp-video-play-pause">⏸</button>
                        <button class="mp-video-btn" id="mp-video-next">⏭</button>
                        <button class="mp-video-btn" id="mp-video-close">✕</button>
                        <button class="mp-video-btn" id="mp-video-fullscreen">⛶</button>
                    </div>
                </div>
            </div>
            
            <style>
                .music-player-widget {
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    padding: 16px;
                    background: linear-gradient(135deg, rgba(123, 97, 255, 0.05) 0%, rgba(90, 224, 224, 0.05) 100%);
                    border-radius: 16px;
                    min-height: 280px;
                }
                
                .mp-album-art {
                    width: 100%;
                    aspect-ratio: 1;
                    max-height: 180px;
                    border-radius: 12px;
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: hidden;
                    box-shadow: 0 8px 24px rgba(123, 97, 255, 0.2);
                    margin: 0 auto;
                }
                
                .mp-album-art img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .mp-album-placeholder {
                    font-size: 48px;
                }
                
                .mp-track-info {
                    text-align: center;
                }
                
                .mp-title {
                    font-size: 16px;
                    font-weight: 600;
                    color: var(--text-primary, #333);
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    margin-bottom: 4px;
                }
                
                .mp-artist {
                    font-size: 13px;
                    color: var(--text-secondary, #666);
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                
                .mp-album {
                    font-size: 11px;
                    color: var(--text-tertiary, #999);
                    margin-top: 2px;
                }
                
                .mp-progress-section {
                    width: 100%;
                }
                
                .mp-progress-bar {
                    width: 100%;
                    height: 4px;
                    background: rgba(0, 0, 0, 0.1);
                    border-radius: 2px;
                    cursor: pointer;
                    overflow: hidden;
                }
                
                .mp-progress-fill {
                    height: 100%;
                    background: linear-gradient(90deg, #7B61FF 0%, #5AE0E0 100%);
                    width: 0%;
                    transition: width 0.3s ease;
                }
                
                .mp-times {
                    display: flex;
                    justify-content: space-between;
                    font-size: 10px;
                    color: var(--text-tertiary, #999);
                    margin-top: 4px;
                }
                
                .mp-controls {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                }
                
                .mp-control-btn {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 50%;
                    width: 36px;
                    height: 36px;
                    font-size: 14px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .mp-control-btn:hover {
                    background: rgba(123, 97, 255, 0.15);
                    transform: scale(1.05);
                }
                
                .mp-control-btn.active {
                    background: rgba(123, 97, 255, 0.2);
                    color: #7B61FF;
                }
                
                .mp-control-btn.mp-play-pause {
                    width: 48px;
                    height: 48px;
                    font-size: 18px;
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    color: white;
                    box-shadow: 0 4px 12px rgba(123, 97, 255, 0.3);
                }
                
                .mp-control-btn.mp-play-pause:hover {
                    transform: scale(1.1);
                    box-shadow: 0 6px 16px rgba(123, 97, 255, 0.4);
                }
                
                .mp-volume-section {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    padding: 0 12px;
                }
                
                .mp-volume-icon {
                    font-size: 14px;
                    cursor: pointer;
                }
                
                .mp-volume-slider {
                    flex: 1;
                    height: 4px;
                    -webkit-appearance: none;
                    background: rgba(0, 0, 0, 0.1);
                    border-radius: 2px;
                    outline: none;
                }
                
                .mp-volume-slider::-webkit-slider-thumb {
                    -webkit-appearance: none;
                    width: 12px;
                    height: 12px;
                    background: #7B61FF;
                    border-radius: 50%;
                    cursor: pointer;
                }
                
                .mp-footer {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0 4px;
                }
                
                .mp-mode-toggle {
                    display: flex;
                    gap: 4px;
                }
                
                .mp-mode-btn {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 12px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .mp-mode-btn:hover {
                    background: rgba(123, 97, 255, 0.1);
                }
                
                .mp-mode-btn.active {
                    background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                    color: white;
                }
                
                .mp-zone-dropdown {
                    background: rgba(0, 0, 0, 0.05);
                    border: none;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 11px;
                    cursor: pointer;
                    outline: none;
                    max-width: 120px;
                }
                
                .mp-actions {
                    display: flex;
                    justify-content: center;
                    gap: 8px;
                }
                
                .mp-action-btn {
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-size: 12px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .mp-action-btn:hover {
                    background: rgba(123, 97, 255, 0.1);
                    border-color: rgba(123, 97, 255, 0.3);
                }
                
                .mp-action-btn.active {
                    background: rgba(123, 97, 255, 0.15);
                    border-color: #7B61FF;
                    color: #7B61FF;
                }
                
                /* Video overlay */
                .mp-video-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background: rgba(0, 0, 0, 0.95);
                    z-index: 1000;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                }
                
                .mp-video-container {
                    max-width: 95%;
                    max-height: 85%;
                    width: 900px;
                    aspect-ratio: 16/9;
                    background: #000;
                    border-radius: 12px;
                    overflow: hidden;
                }
                
                .mp-video-controls {
                    display: flex;
                    gap: 12px;
                    margin-top: 16px;
                }
                
                .mp-video-btn {
                    background: rgba(255, 255, 255, 0.15);
                    border: 1px solid rgba(255, 255, 255, 0.3);
                    color: white;
                    padding: 12px 24px;
                    border-radius: 8px;
                    cursor: pointer;
                    font-size: 14px;
                }
                
                .mp-video-btn:hover {
                    background: rgba(255, 255, 255, 0.25);
                }
                
                /* Compact mode for smaller widget sizes */
                .size-small .music-player-widget .mp-album-art {
                    max-height: 100px;
                }
                
                .size-small .music-player-widget .mp-shuffle,
                .size-small .music-player-widget .mp-repeat,
                .size-small .music-player-widget .mp-album,
                .size-small .music-player-widget .mp-actions {
                    display: none;
                }
                
                .size-small .music-player-widget .mp-control-btn {
                    width: 32px;
                    height: 32px;
                }
                
                .size-small .music-player-widget .mp-control-btn.mp-play-pause {
                    width: 40px;
                    height: 40px;
                }
            </style>
        `;
    }
    
    init(element, options = {}) {
        super.init(element, options);
        
        this.setupEventListeners();
        this.subscribeToState();
        this.updateUI();
    }
    
    setupEventListeners() {
        const el = this.element;
        if (!el) return;
        
        // Play/Pause
        el.querySelector('#mp-play-pause')?.addEventListener('click', () => {
            if (MusicState.state.isPlaying) {
                MusicState.pause();
            } else {
                MusicState.resume();
            }
        });
        
        // Previous/Next
        el.querySelector('#mp-prev')?.addEventListener('click', () => MusicState.previous());
        el.querySelector('#mp-next')?.addEventListener('click', () => MusicState.skip());
        
        // Shuffle/Repeat
        el.querySelector('#mp-shuffle')?.addEventListener('click', (e) => {
            e.target.classList.toggle('active');
            // TODO: Implement shuffle
        });
        
        el.querySelector('#mp-repeat')?.addEventListener('click', (e) => {
            e.target.classList.toggle('active');
            // TODO: Implement repeat
        });
        
        // Volume
        const volumeSlider = el.querySelector('#mp-volume-slider');
        volumeSlider?.addEventListener('input', (e) => {
            MusicState.setVolume(parseInt(e.target.value));
        });
        
        el.querySelector('#mp-volume-icon')?.addEventListener('click', () => {
            const slider = el.querySelector('#mp-volume-slider');
            if (slider.value > 0) {
                slider.dataset.prevVolume = slider.value;
                slider.value = 0;
                MusicState.setVolume(0);
            } else {
                slider.value = slider.dataset.prevVolume || 80;
                MusicState.setVolume(parseInt(slider.value));
            }
            this.updateVolumeIcon();
        });
        
        // Progress bar seek
        el.querySelector('#mp-progress-bar')?.addEventListener('click', (e) => {
            const rect = e.target.getBoundingClientRect();
            const percent = (e.clientX - rect.left) / rect.width;
            const seekMs = Math.floor(percent * MusicState.state.duration);
            MusicState.seek(seekMs);
        });
        
        // Mode toggle
        el.querySelector('#mp-mode-audio')?.addEventListener('click', () => {
            MusicState.setPlayMode('audio');
            this.updateModeButtons();
            this.hideVideoOverlay();
        });
        
        el.querySelector('#mp-mode-video')?.addEventListener('click', () => {
            MusicState.setPlayMode('video');
            this.updateModeButtons();
            if (MusicState.state.isPlaying) {
                this.showVideoOverlay();
            }
        });
        
        // Zone selector
        el.querySelector('#mp-zone-selector')?.addEventListener('change', (e) => {
            const zoneId = e.target.value;
            const zone = MusicState.state.availableZones.find(z => z.id === zoneId);
            if (zone) {
                MusicState.selectZone(zone);
            }
        });
        
        // Quick actions
        el.querySelector('#mp-like')?.addEventListener('click', async () => {
            const result = await MusicState.likeTrack();
            if (result?.success) {
                const btn = el.querySelector('#mp-like');
                btn.classList.add('active');
                setTimeout(() => btn.classList.remove('active'), 2000);
            }
        });
        
        el.querySelector('#mp-add-queue')?.addEventListener('click', async () => {
            const track = MusicState.state.currentTrack;
            if (track) {
                await MusicState.addToQueue(track.id, track);
            }
        });
        
        el.querySelector('#mp-fullscreen')?.addEventListener('click', () => {
            this.showVideoOverlay();
        });
        
        // Video overlay controls
        el.querySelector('#mp-video-close')?.addEventListener('click', () => this.hideVideoOverlay());
        el.querySelector('#mp-video-play-pause')?.addEventListener('click', () => {
            if (MusicState.state.isPlaying) {
                MusicState.pause();
            } else {
                MusicState.resume();
            }
        });
        el.querySelector('#mp-video-prev')?.addEventListener('click', () => MusicState.previous());
        el.querySelector('#mp-video-next')?.addEventListener('click', () => MusicState.skip());
        el.querySelector('#mp-video-fullscreen')?.addEventListener('click', () => {
            const container = el.querySelector('#mp-video-container');
            if (document.fullscreenElement) {
                document.exitFullscreen();
            } else if (container?.requestFullscreen) {
                container.requestFullscreen();
            }
        });
    }
    
    subscribeToState() {
        // Subscribe to MusicState events
        this.unsubscribers.push(
            MusicState.on('trackChanged', () => this.updateTrackInfo()),
            MusicState.on('isPlayingChanged', () => this.updatePlayButton()),
            MusicState.on('progress', () => this.updateProgress()),
            MusicState.on('volumeChanged', (vol) => this.updateVolume(vol)),
            MusicState.on('playModeChanged', () => this.updateModeButtons()),
            MusicState.on('zonesUpdated', (zones) => this.updateZoneSelector(zones))
        );
    }
    
    updateUI() {
        this.updateTrackInfo();
        this.updatePlayButton();
        this.updateProgress();
        this.updateVolume(MusicState.state.volume);
        this.updateModeButtons();
        this.updateZoneSelector(MusicState.state.availableZones);
    }
    
    updateTrackInfo() {
        const el = this.element;
        if (!el) return;
        
        const track = MusicState.state.currentTrack;
        
        el.querySelector('#mp-title').textContent = track?.title || 'Not Playing';
        el.querySelector('#mp-artist').textContent = track?.artist || 'Search for music to start';
        el.querySelector('#mp-album').textContent = track?.album || '';
        
        const albumArt = el.querySelector('#mp-album-art');
        if (track?.thumbnail) {
            albumArt.innerHTML = `<img src="${track.thumbnail}" alt="Album art">`;
        } else {
            albumArt.innerHTML = '<div class="mp-album-placeholder">🎵</div>';
        }
    }
    
    updatePlayButton() {
        const el = this.element;
        if (!el) return;
        
        const btn = el.querySelector('#mp-play-pause');
        const videoBtn = el.querySelector('#mp-video-play-pause');
        const isPlaying = MusicState.state.isPlaying;
        
        if (btn) btn.textContent = isPlaying ? '⏸' : '▶';
        if (videoBtn) videoBtn.textContent = isPlaying ? '⏸' : '▶';
        
        // Show fullscreen button in video mode
        const fsBtn = el.querySelector('#mp-fullscreen');
        if (fsBtn) {
            fsBtn.style.display = MusicState.state.playMode === 'video' ? 'inline-block' : 'none';
        }
    }
    
    updateProgress() {
        const el = this.element;
        if (!el) return;
        
        const { position, duration } = MusicState.state;
        
        el.querySelector('#mp-time-current').textContent = this.formatTime(position);
        el.querySelector('#mp-time-total').textContent = this.formatTime(duration);
        
        const percent = duration > 0 ? (position / duration) * 100 : 0;
        const fill = el.querySelector('#mp-progress-fill');
        if (fill) fill.style.width = `${percent}%`;
    }
    
    updateVolume(volume) {
        const el = this.element;
        if (!el) return;
        
        const slider = el.querySelector('#mp-volume-slider');
        if (slider) slider.value = volume;
        
        this.updateVolumeIcon();
    }
    
    updateVolumeIcon() {
        const el = this.element;
        if (!el) return;
        
        const icon = el.querySelector('#mp-volume-icon');
        const slider = el.querySelector('#mp-volume-slider');
        const vol = parseInt(slider?.value || 0);
        
        if (icon) {
            if (vol === 0) icon.textContent = '🔇';
            else if (vol < 50) icon.textContent = '🔉';
            else icon.textContent = '🔊';
        }
    }
    
    updateModeButtons() {
        const el = this.element;
        if (!el) return;
        
        const mode = MusicState.state.playMode;
        el.querySelector('#mp-mode-audio')?.classList.toggle('active', mode === 'audio');
        el.querySelector('#mp-mode-video')?.classList.toggle('active', mode === 'video');
    }
    
    updateZoneSelector(zones) {
        const el = this.element;
        if (!el) return;
        
        const selector = el.querySelector('#mp-zone-selector');
        if (!selector) return;
        
        const currentZoneId = MusicState.state.currentZone?.id || '';
        
        selector.innerHTML = '<option value="">This Device</option>' +
            (zones || []).map(zone => 
                `<option value="${zone.id}" ${zone.id === currentZoneId ? 'selected' : ''}>${zone.name}</option>`
            ).join('');
    }
    
    showVideoOverlay() {
        const overlay = this.element?.querySelector('#mp-video-overlay');
        if (overlay) {
            overlay.style.display = 'flex';
            // Load video if playing
            if (MusicState.state.currentTrack?.id) {
                this.loadVideo(MusicState.state.currentTrack.id);
            }
        }
    }
    
    hideVideoOverlay() {
        const overlay = this.element?.querySelector('#mp-video-overlay');
        if (overlay) {
            overlay.style.display = 'none';
        }
    }
    
    async loadVideo(trackId) {
        // Use the existing video loading logic
        const container = this.element?.querySelector('#mp-video-container');
        if (!container) return;
        
        // Request video stream
        const data = await MusicState.apiRequest('/api/music/play', {
            method: 'POST',
            body: { track_id: trackId, mode: 'video', force_direct: true }
        });
        
        if (data?.stream_url) {
            container.innerHTML = `<video style="width:100%;height:100%;" controls autoplay src="${data.stream_url}"></video>`;
        }
    }
    
    formatTime(ms) {
        if (!ms || isNaN(ms)) return '0:00';
        const totalSeconds = Math.floor(ms / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }
    
    destroy() {
        // Unsubscribe from all events
        this.unsubscribers.forEach(unsub => unsub());
        this.unsubscribers = [];
        
        this.hideVideoOverlay();
        super.destroy();
    }
}

// Export to window for widget system
window.MusicPlayerWidget = MusicPlayerWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register(new MusicPlayerWidget());
}


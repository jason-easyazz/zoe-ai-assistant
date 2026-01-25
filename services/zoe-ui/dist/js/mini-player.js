/**
 * Mini Player - Persistent music player bar for all pages
 * Maintains audio playback across page navigation
 */

(function() {
    'use strict';

    // Check if we're on the music page - still run but stay hidden
    const isOnMusicPage = window.location.pathname.includes('music.html');

    const STORAGE_KEY = 'zoe_music_playback';
    let audioElement = null;
    let isPlaying = false;
    let currentTrack = null;
    let volume = 80;
    let streamUrl = null;
    let savedPosition = 0;

    // Load saved state
    function loadState() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) {
                const state = JSON.parse(saved);
                currentTrack = state.currentTrack;
                isPlaying = state.isPlaying;
                volume = state.volume || 80;
                streamUrl = state.streamUrl || null;
                savedPosition = state.position || 0;
                return state;
            }
        } catch (e) {
            console.warn('Failed to load music state:', e);
        }
        return null;
    }

    // Save state
    function saveState() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({
                currentTrack,
                isPlaying,
                volume,
                position: audioElement ? audioElement.currentTime * 1000 : 0,
                streamUrl: audioElement?.src || streamUrl
            }));
        } catch (e) {
            console.warn('Failed to save music state:', e);
        }
    }

    // Create the mini player HTML
    function createMiniPlayer() {
        const bar = document.createElement('div');
        bar.id = 'zoe-mini-player';
        bar.innerHTML = `
            <div class="zmp-track-info">
                <div class="zmp-art" id="zmp-art">🎵</div>
                <div class="zmp-details">
                    <div class="zmp-title" id="zmp-title">Not Playing</div>
                    <div class="zmp-artist" id="zmp-artist">Open Music to start</div>
                </div>
            </div>
            <div class="zmp-controls">
                <button class="zmp-btn" id="zmp-prev" title="Previous">⏮</button>
                <button class="zmp-btn zmp-play" id="zmp-play" title="Play">▶</button>
                <button class="zmp-btn" id="zmp-next" title="Next">⏭</button>
            </div>
            <div class="zmp-progress">
                <div class="zmp-progress-bar" id="zmp-progress-bar">
                    <div class="zmp-progress-fill" id="zmp-progress-fill"></div>
                </div>
            </div>
            <div class="zmp-right">
                <input type="range" class="zmp-volume" id="zmp-volume" min="0" max="100" value="80">
                <a href="music.html" class="zmp-expand" title="Open Music">🎵</a>
                <button class="zmp-close" id="zmp-close" title="Close">×</button>
            </div>
        `;
        document.body.appendChild(bar);
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            #zoe-mini-player {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                height: 60px;
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(20px);
                border-top: 1px solid rgba(0, 0, 0, 0.1);
                display: flex;
                align-items: center;
                padding: 0 16px;
                gap: 16px;
                z-index: 9999;
                font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
                box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.1);
                transform: translateY(100%);
                transition: transform 0.3s ease;
            }
            #zoe-mini-player.visible {
                transform: translateY(0);
            }
            #zoe-mini-player.hidden {
                display: none;
            }
            .zmp-track-info {
                display: flex;
                align-items: center;
                gap: 12px;
                min-width: 200px;
            }
            .zmp-art {
                width: 44px;
                height: 44px;
                border-radius: 8px;
                background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 20px;
                overflow: hidden;
            }
            .zmp-art img {
                width: 100%;
                height: 100%;
                object-fit: cover;
            }
            .zmp-details {
                display: flex;
                flex-direction: column;
                gap: 2px;
                min-width: 0;
            }
            .zmp-title {
                font-size: 14px;
                font-weight: 500;
                color: #333;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                max-width: 180px;
            }
            .zmp-artist {
                font-size: 12px;
                color: #666;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                max-width: 180px;
            }
            .zmp-controls {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .zmp-btn {
                width: 36px;
                height: 36px;
                border-radius: 50%;
                border: none;
                background: rgba(0, 0, 0, 0.05);
                font-size: 14px;
                cursor: pointer;
                transition: all 0.2s ease;
            }
            .zmp-btn:hover {
                background: rgba(123, 97, 255, 0.15);
            }
            .zmp-btn.zmp-play {
                width: 44px;
                height: 44px;
                font-size: 16px;
                background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
                color: white;
            }
            .zmp-progress {
                flex: 1;
                min-width: 100px;
            }
            .zmp-progress-bar {
                height: 4px;
                background: rgba(0, 0, 0, 0.1);
                border-radius: 2px;
                cursor: pointer;
                overflow: hidden;
            }
            .zmp-progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #7B61FF 0%, #5AE0E0 100%);
                width: 0%;
                transition: width 0.3s ease;
            }
            .zmp-right {
                display: flex;
                align-items: center;
                gap: 12px;
            }
            .zmp-volume {
                width: 80px;
                height: 4px;
                -webkit-appearance: none;
                background: rgba(0, 0, 0, 0.1);
                border-radius: 2px;
                outline: none;
            }
            .zmp-volume::-webkit-slider-thumb {
                -webkit-appearance: none;
                width: 12px;
                height: 12px;
                background: #7B61FF;
                border-radius: 50%;
                cursor: pointer;
            }
            .zmp-expand {
                text-decoration: none;
                font-size: 18px;
                padding: 8px;
                border-radius: 8px;
                transition: background 0.2s;
            }
            .zmp-expand:hover {
                background: rgba(123, 97, 255, 0.1);
            }
            .zmp-close {
                background: none;
                border: none;
                font-size: 20px;
                color: #999;
                cursor: pointer;
                padding: 4px 8px;
            }
            .zmp-close:hover {
                color: #333;
            }
            @media (max-width: 600px) {
                .zmp-progress, .zmp-volume {
                    display: none;
                }
                .zmp-track-info {
                    min-width: auto;
                    flex: 1;
                }
            }
        `;
        document.head.appendChild(style);

        // Bind events
        document.getElementById('zmp-play').addEventListener('click', togglePlay);
        document.getElementById('zmp-prev').addEventListener('click', previous);
        document.getElementById('zmp-next').addEventListener('click', next);
        document.getElementById('zmp-volume').addEventListener('input', (e) => {
            volume = parseInt(e.target.value);
            if (audioElement) audioElement.volume = volume / 100;
            saveState();
        });
        document.getElementById('zmp-progress-bar').addEventListener('click', seek);
        document.getElementById('zmp-close').addEventListener('click', hideMiniPlayer);

        return bar;
    }

    // Get or create audio element
    function getAudio() {
        if (!audioElement) {
            audioElement = new Audio();
            audioElement.volume = volume / 100;
            
            audioElement.addEventListener('timeupdate', () => {
                const progressFill = document.getElementById('zmp-progress-fill');
                if (progressFill) {
                    const progress = audioElement.duration > 0 
                        ? (audioElement.currentTime / audioElement.duration) * 100 
                        : 0;
                    progressFill.style.width = progress + '%';
                }
                saveState();
            });
            
            audioElement.addEventListener('ended', () => {
                next();
            });
            
            audioElement.addEventListener('error', (e) => {
                console.error('Mini player audio error:', e);
            });
        }
        return audioElement;
    }

    // Update UI
    function updateUI() {
        const titleEl = document.getElementById('zmp-title');
        const artistEl = document.getElementById('zmp-artist');
        const artEl = document.getElementById('zmp-art');
        const playBtn = document.getElementById('zmp-play');

        if (currentTrack) {
            titleEl.textContent = currentTrack.title || 'Unknown';
            artistEl.textContent = currentTrack.artist || '';
            
            if (currentTrack.thumbnail) {
                artEl.innerHTML = `<img src="${currentTrack.thumbnail}" alt="">`;
            } else {
                artEl.innerHTML = '🎵';
            }
        } else {
            titleEl.textContent = 'Not Playing';
            artistEl.textContent = 'Open Music to start';
            artEl.innerHTML = '🎵';
        }

        playBtn.textContent = isPlaying ? '⏸' : '▶';
    }

    // Play/Pause toggle
    function togglePlay() {
        const audio = getAudio();
        if (isPlaying) {
            audio.pause();
            isPlaying = false;
        } else {
            if (audio.src) {
                audio.play().catch(e => console.error('Play failed:', e));
                isPlaying = true;
            }
        }
        updateUI();
        saveState();
        
        // Notify other tabs/pages
        broadcastState('toggle');
    }

    // Next track - call server queue API
    async function next() {
        try {
            const response = await fetch('/api/music/queue/next', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: 'default' })
            });
            const data = await response.json();
            if (data.success && data.stream_url) {
                currentTrack = {
                    id: data.track_id,
                    title: data.track_info?.title || 'Unknown',
                    artist: data.track_info?.artist || '',
                    thumbnail: data.track_info?.thumbnail_url || data.track_info?.album_art_url || null
                };
                const audio = getAudio();
                audio.src = data.stream_url;
                audio.play().catch(e => console.error('Play failed:', e));
                isPlaying = true;
                updateUI();
                saveState();
            } else {
                console.log('No more tracks in queue');
            }
        } catch (e) {
            console.error('Next track failed:', e);
        }
        broadcastState('next');
    }

    // Previous track - restart or go back
    function previous() {
        const audio = getAudio();
        if (audio.currentTime > 3) {
            // If more than 3 seconds in, restart current track
            audio.currentTime = 0;
        } else {
            // Just restart for now
            audio.currentTime = 0;
        }
        broadcastState('previous');
    }

    // Seek
    function seek(e) {
        const audio = getAudio();
        if (audio.duration) {
            const rect = e.target.getBoundingClientRect();
            const percent = (e.clientX - rect.left) / rect.width;
            audio.currentTime = percent * audio.duration;
        }
    }

    // Hide mini player
    function hideMiniPlayer() {
        const bar = document.getElementById('zoe-mini-player');
        if (bar) {
            bar.classList.remove('visible');
            localStorage.setItem('zoe_mini_player_hidden', 'true');
        }
    }

    // Show mini player
    function showMiniPlayer() {
        const bar = document.getElementById('zoe-mini-player');
        if (bar) {
            bar.classList.add('visible');
            localStorage.removeItem('zoe_mini_player_hidden');
        }
    }

    // Broadcast state to other tabs
    function broadcastState(action) {
        localStorage.setItem('zoe_music_action', JSON.stringify({
            action,
            timestamp: Date.now()
        }));
    }

    // Listen for state changes from other tabs
    function listenForChanges() {
        window.addEventListener('storage', (e) => {
            if (e.key === STORAGE_KEY) {
                loadState();
                updateUI();
            }
            if (e.key === 'zoe_music_action') {
                // Another tab requested an action
                const data = JSON.parse(e.newValue);
                console.log('Mini player received action:', data.action);
            }
        });
    }

    // Initialize
    function init() {
        console.log('🎵 Mini-player: Initializing...', isOnMusicPage ? '(on music page)' : '');
        const state = loadState();
        console.log('🎵 Mini-player: Loaded state:', state);
        
        // On music page - just restore audio, don't show bar
        if (isOnMusicPage) {
            if (state?.streamUrl && state?.isPlaying) {
                console.log('🎵 Mini-player: On music page, restoring audio only');
                const audio = getAudio();
                audio.src = state.streamUrl;
                if (state.position) {
                    audio.currentTime = state.position / 1000;
                }
                audio.play().catch(e => console.log('Auto-resume failed:', e));
                
                // Expose audio element for MusicState to use
                window.ZOE_SHARED_AUDIO = audio;
            }
            listenForChanges();
            return;
        }
        
        // Don't show if explicitly hidden
        if (localStorage.getItem('zoe_mini_player_hidden') === 'true' && !state?.isPlaying) {
            console.log('🎵 Mini-player: Hidden by user preference');
            return;
        }
        
        // Only show if there's a current track or music is playing
        if (state?.currentTrack || state?.isPlaying) {
            console.log('🎵 Mini-player: Showing bar (track exists or was playing)');
            const bar = createMiniPlayer();
            
            // Restore volume
            document.getElementById('zmp-volume').value = volume;
            
            // Restore audio if we have a stream URL
            if (state?.streamUrl && state?.isPlaying) {
                const audio = getAudio();
                audio.src = state.streamUrl;
                if (state.position) {
                    audio.currentTime = state.position / 1000; // Convert ms to seconds
                }
                audio.play().catch(e => console.log('Auto-resume failed (user interaction required):', e));
            }
            
            // Update UI with saved state
            updateUI();
            
            // Show the bar
            setTimeout(() => bar.classList.add('visible'), 100);
            
            // Listen for changes from other tabs
            listenForChanges();
        } else {
            console.log('🎵 Mini-player: No track to show (currentTrack:', state?.currentTrack, 'isPlaying:', state?.isPlaying, ')');
        }
    }

    // Wait for DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for external control
    window.ZoeMiniPlayer = {
        show: showMiniPlayer,
        hide: hideMiniPlayer,
        play: (track, streamUrl) => {
            currentTrack = track;
            isPlaying = true;
            const audio = getAudio();
            audio.src = streamUrl;
            audio.play().catch(e => console.error('Play failed:', e));
            updateUI();
            saveState();
            showMiniPlayer();
        },
        pause: () => {
            isPlaying = false;
            if (audioElement) audioElement.pause();
            updateUI();
            saveState();
        },
        updateTrack: (track) => {
            currentTrack = track;
            updateUI();
            saveState();
        }
    };
})();


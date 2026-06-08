/*
 * Skybridge voice transport adapters.
 * Both local WebSocket and LiveKit fallback emit the same browser events.
 */
(function () {
    function getSessionId() {
        try {
            const session = JSON.parse(localStorage.getItem('zoe_session') || '{}');
            return session.session_id || '';
        } catch (_) {
            return '';
        }
    }

    class SkybridgeVoice {
        constructor(options) {
            this.mode = options.mode || 'local';
            this.onEvent = options.onEvent;
            this.ws = null;
            this.room = null;
            this.micStream = null;
            this.mediaRecorder = null;
            this.audioChunks = [];
            this.audioCtx = null;
            this.analyser = null;
            this.isRecording = false;
            this.serverBusy = false;
            this.speaking = false;
            this.maxRecordedRms = 0;
            this.silenceTimer = null;
            this.autoListenTimer = null;
            this.reconnectTimer = null;
            this.reconnectDelayMs = 1000;
            this.stopped = true;
            this.silenceThreshold = 0.01;
            this.silenceMs = 1600;
        }

        emit(event) {
            if (typeof this.onEvent === 'function') this.onEvent(event);
        }

        async start() {
            this.stopped = false;
            if (this.mode === 'livekit') {
                await this.connectLiveKit();
            } else {
                this.connectLocal();
            }
        }

        stop() {
            this.stopped = true;
            clearTimeout(this.reconnectTimer);
            clearTimeout(this.autoListenTimer);
            clearTimeout(this.silenceTimer);
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                this.mediaRecorder.onstop = null;
                this.mediaRecorder.stop();
            }
            this.audioChunks = [];
            if (this.ws) this.ws.close();
            if (this.room) this.room.disconnect();
            if (this.micStream) this.micStream.getTracks().forEach(track => track.stop());
            this.ws = null;
            this.room = null;
            this.micStream = null;
            this.mediaRecorder = null;
            this.isRecording = false;
            this.serverBusy = false;
            this.emit({ type: 'state', state: 'ambient' });
        }

        connectLocal() {
            clearTimeout(this.reconnectTimer);
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const wsUrl = proto + '://' + location.host + '/ws/voice/?session_id=' + encodeURIComponent(getSessionId());
            this.ws = new WebSocket(wsUrl);
            this.ws.binaryType = 'arraybuffer';
            this.ws.onopen = () => {
                this.reconnectDelayMs = 1000;
                this.emit({ type: 'ready', mode: 'local' });
            };
            this.ws.onmessage = event => {
                try {
                    const msg = typeof event.data === 'string' ? JSON.parse(event.data) : null;
                    if (msg) this.handleServerEvent(msg);
                } catch (err) {
                    console.warn('Skybridge voice event parse failed', err);
                    this.emit({ type: 'error', message: 'Malformed server event' });
                }
            };
            this.ws.onclose = () => {
                this.ws = null;
                this.serverBusy = false;
                this.emit({ type: 'state', state: 'ambient' });
                this.emit({ type: 'error', message: 'Voice disconnected' });
                this.scheduleReconnect();
            };
        }

        scheduleReconnect() {
            if (this.stopped || this.mode !== 'local') return;
            clearTimeout(this.reconnectTimer);
            const delay = this.reconnectDelayMs;
            this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, 15000);
            this.reconnectTimer = setTimeout(() => {
                if (!this.stopped && this.mode === 'local') this.connectLocal();
            }, delay);
        }

        async connectLiveKit() {
            if (typeof LivekitClient === 'undefined') {
                this.mode = 'local';
                this.connectLocal();
                return;
            }
            try {
                const resp = await fetch('/api/voice/livekit-token', { headers: { 'X-Session-ID': getSessionId() } });
                if (!resp.ok) throw new Error('Token fetch failed');
                const data = await resp.json();
                if (data.livekit_available === false) {
                    this.mode = 'local';
                    this.connectLocal();
                    return;
                }
                const room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });
                room.on(LivekitClient.RoomEvent.DataReceived, payload => {
                    try {
                        const text = new TextDecoder().decode(payload);
                        this.handleServerEvent(JSON.parse(text));
                    } catch (err) {
                        console.warn('Skybridge LiveKit event parse failed', err);
                        this.emit({ type: 'error', message: 'Malformed server event' });
                    }
                });
                await room.connect(data.url, data.token);
                this.room = room;
                this.emit({ type: 'ready', mode: 'livekit' });
            } catch (err) {
                this.emit({ type: 'error', message: 'LiveKit unavailable, using local voice' });
                this.mode = 'local';
                this.connectLocal();
            }
        }

        handleServerEvent(msg) {
            const type = msg.type || msg.event;
            if (type === 'state') {
                this.emit({ type: 'state', state: msg.state || 'ambient' });
            } else if (type === 'transcript') {
                this.emit({ type: 'transcript', role: msg.role || 'zoe', text: msg.text || '' });
            } else if (type === 'audio') {
                this.playAudio(msg.audio_base64, msg.content_type || 'audio/wav');
            } else if (type === 'agui' || type === 'ui_component') {
                this.emit({ type: 'card', card: msg.data || msg });
            } else if (type === 'done') {
                this.serverBusy = false;
                this.emit({ type: 'done' });
            } else if (type === 'text') {
                this.emit({ type: 'transcript', role: 'zoe', text: msg.content || msg.text || '' });
            }
        }

        sendText(message) {
            const text = String(message || '').trim();
            if (!text) return;
            this.emit({ type: 'transcript', role: 'user', text });
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.serverBusy = true;
                this.emit({ type: 'state', state: 'thinking' });
                this.ws.send(JSON.stringify({ type: 'text', message: text }));
            } else {
                this.serverBusy = false;
                this.emit({ type: 'state', state: 'ambient' });
                this.emit({ type: 'error', message: 'Voice transport unavailable' });
            }
        }

        async startRecording() {
            if (this.isRecording || this.speaking || this.serverBusy) return;
            if (!this.micStream) {
                this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }
            this.audioCtx = this.audioCtx || new (window.AudioContext || window.webkitAudioContext)();
            const source = this.audioCtx.createMediaStreamSource(this.micStream);
            this.analyser = this.audioCtx.createAnalyser();
            this.analyser.fftSize = 512;
            source.connect(this.analyser);
            this.audioChunks = [];
            this.maxRecordedRms = 0;
            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
            this.mediaRecorder = new MediaRecorder(this.micStream, { mimeType });
            this.mediaRecorder.ondataavailable = event => {
                if (event.data.size > 0) this.audioChunks.push(event.data);
            };
            this.mediaRecorder.onstop = () => this.sendRecording();
            this.mediaRecorder.start(100);
            this.isRecording = true;
            this.emit({ type: 'state', state: 'listening' });
            this.watchSilence();
        }

        stopRecording() {
            if (!this.isRecording) return;
            this.isRecording = false;
            clearTimeout(this.silenceTimer);
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop();
            this.emit({ type: 'state', state: 'thinking' });
        }

        watchSilence() {
            const buf = new Float32Array(this.analyser.fftSize);
            const check = () => {
                if (!this.isRecording) return;
                this.analyser.getFloatTimeDomainData(buf);
                let sum = 0;
                for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
                const rms = Math.sqrt(sum / buf.length);
                this.maxRecordedRms = Math.max(this.maxRecordedRms, rms);
                if (rms < this.silenceThreshold) {
                    if (!this.silenceTimer) this.silenceTimer = setTimeout(() => this.stopRecording(), this.silenceMs);
                } else {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }
                if (this.isRecording) requestAnimationFrame(check);
            };
            requestAnimationFrame(check);
        }

        async sendRecording() {
            if (!this.audioChunks.length || this.maxRecordedRms < 0.008) {
                this.emit({ type: 'state', state: 'ambient' });
                return;
            }
            const blob = new Blob(this.audioChunks, { type: this.mediaRecorder.mimeType || 'audio/webm' });
            this.audioChunks = [];
            this.serverBusy = true;
            if (this.mode === 'livekit') {
                const form = new FormData();
                form.append('audio', blob, 'skybridge.webm');
                form.append('session_id', getSessionId());
                try {
                    const resp = await fetch('/api/voice/livekit-audio', {
                        method: 'POST',
                        headers: { 'X-Session-ID': getSessionId() },
                        body: form
                    });
                    const data = await resp.json();
                    if (data.transcript) this.emit({ type: 'transcript', role: 'user', text: data.transcript });
                    if (data.response_text) this.emit({ type: 'transcript', role: 'zoe', text: data.response_text });
                    if (data.audio_base64) this.playAudio(data.audio_base64, data.content_type || 'audio/wav');
                    this.serverBusy = false;
                    this.emit({ type: 'done' });
                } catch (err) {
                    this.serverBusy = false;
                    this.emit({ type: 'error', message: 'Voice upload failed' });
                }
            } else if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(await blob.arrayBuffer());
            } else {
                this.serverBusy = false;
                this.emit({ type: 'state', state: 'ambient' });
                this.emit({ type: 'error', message: 'Voice transport unavailable' });
            }
        }

        playAudio(b64, contentType) {
            try {
                const bin = atob(b64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                const url = URL.createObjectURL(new Blob([bytes], { type: contentType }));
                const audio = new Audio(url);
                this.speaking = true;
                this.emit({ type: 'state', state: 'responding' });
                audio.onended = () => {
                    URL.revokeObjectURL(url);
                    this.speaking = false;
                    this.emit({ type: 'state', state: 'ambient' });
                };
                audio.onerror = audio.onended;
                audio.play().catch(audio.onended);
            } catch (_) {
                this.speaking = false;
            }
        }
    }

    window.SkybridgeVoice = SkybridgeVoice;
})();

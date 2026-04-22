/**
 * Music Widget
 *
 * Playback is handled by Music Assistant inside Home Assistant. Zoe talks to
 * that stack over the existing HA bridge (see /api/settings for
 * `homeassistant_url`), so there's no in-app music surface on the desktop
 * dashboard. The widget renders a compact pointer that deep-links into HA and
 * falls back to a sensible default if the URL is not configured yet.
 *
 * Historical note: This file previously embedded a full YouTube Music player
 * (WebSocket + /api/music/* REST endpoints). Those endpoints were retired when
 * music moved to HA; dropping the widget on the dashboard produced unstyled
 * controls that hit 404s, so we render a clean placeholder card instead.
 */

class MusicWidget extends WidgetModule {
    constructor() {
        super('music', {
            version: '2.0.0',
            defaultSize: 'size-medium',
            updateInterval: 0
        });

        this.haUrl = 'http://homeassistant.local:8123';
    }

    getTemplate() {
        return `
            <div class="widget-content music-content music-ha-card">
                <div class="music-ha-icon">🎵</div>
                <div class="music-ha-title">Music lives in Home Assistant</div>
                <div class="music-ha-body">
                    Playback, queues and speakers are handled by
                    <strong>Music Assistant</strong>. Ask Zoe in chat or open HA
                    for full control.
                </div>
                <div class="music-ha-actions">
                    <a class="music-ha-btn music-ha-btn-primary"
                       id="music-ha-open"
                       href="#"
                       target="_blank"
                       rel="noopener">
                        🏠 Open Home Assistant
                    </a>
                    <button class="music-ha-btn music-ha-btn-secondary"
                            id="music-ha-chat">
                        💬 Ask Zoe
                    </button>
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);

        const chatBtn = element.querySelector('#music-ha-chat');
        if (chatBtn) {
            chatBtn.addEventListener('click', (e) => {
                e.preventDefault();
                window.location.href = 'chat.html';
            });
        }

        // Resolve the HA URL from settings; keep the default href in place
        // until the fetch succeeds so the button is usable immediately.
        this.resolveHomeAssistantUrl().then((url) => {
            this.haUrl = url;
            const link = element.querySelector('#music-ha-open');
            if (link) link.href = url;
        });
    }

    async resolveHomeAssistantUrl() {
        try {
            const response = await fetch('/api/settings', {
                headers: {
                    'X-Auth-Token':
                        (typeof window !== 'undefined' && (window.ZOE_AUTH_TOKEN || localStorage.getItem('zoe_auth_token'))) || ''
                }
            });
            if (!response.ok) return this.haUrl;
            const settings = await response.json();
            if (!settings || typeof settings !== 'object') return this.haUrl;
            return (
                settings.homeassistant_url ||
                settings.home_assistant_url ||
                (settings.settings && settings.settings.homeassistant_url) ||
                this.haUrl
            );
        } catch (_) {
            return this.haUrl;
        }
    }

    update() { /* Static pointer card – no periodic refresh needed. */ }

    destroy() {
        super.destroy();
    }
}

window.MusicWidget = MusicWidget;

if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register(new MusicWidget());
}

/*
 * Skybridge capabilities registry.
 * The registry is intentionally data-first so pages can opt into Skybridge
 * without copying rendering or voice transport code.
 */
(function () {
    const pages = [
        page('dashboard', 'Dashboard', '/touch/dashboard.html', ['home', 'overview', 'widgets'], 'Household overview, widgets, ambient panel state.', 'system'),
        page('chat', 'Chat', '/touch/chat.html', ['conversation', 'messages', 'history'], 'Full Zoe conversation history and long-form agent work.', 'chat'),
        page('calendar', 'Calendar', '/touch/calendar.html', ['schedule', 'events', 'planner'], 'Events, schedule views, planning, and calendar sync.', 'calendar'),
        page('lists', 'Lists', '/touch/lists.html', ['shopping', 'todo', 'tasks', 'groceries'], 'Shopping lists, task lists, and custom household lists.', 'list'),
        page('notes', 'Notes', '/touch/notes.html', ['note', 'scratchpad'], 'Quick notes and saved snippets.', 'note'),
        page('journal', 'Journal', '/touch/journal.html', ['diary', 'reflection'], 'Journal entries, dictation, and personal reflection.', 'journal'),
        page('memories', 'Memories', '/touch/memories.html', ['memory', 'remembered', 'facts'], 'Stored memories, facts, and recall controls.', 'memory'),
        page('people', 'People', '/touch/people.html', ['contacts', 'relationships', 'family'], 'People, relationships, birthdays, and CRM-style context.', 'people'),
        page('smart-home', 'Smart Home', '/touch/smart-home.html', ['lights', 'devices', 'home assistant'], 'Home Assistant devices, rooms, scenes, and controls.', 'home'),
        page('music', 'Music', '/touch/music.html', ['media', 'now playing', 'player', 'spotify', 'youtube'], 'Music providers, queues, zones, and playback controls.', 'media'),
        page('weather', 'Weather', '/touch/weather.html', ['forecast', 'temperature', 'rain'], 'Current weather, forecast, location, and weather preferences.', 'weather'),
        page('cooking', 'Cooking', '/touch/cooking.html', ['recipes', 'kitchen', 'meal'], 'Recipes, kitchen workflows, and cooking helpers.', 'cooking'),
        page('games', 'Games', '/touch/games.html', ['play', 'game'], 'Touch-friendly games and playful modes.', 'game'),
        page('updates', 'Updates', '/touch/updates.html', ['system updates', 'release'], 'Zoe updates, system notices, and upgrade state.', 'updates'),
        page('settings', 'Settings', '/touch/settings.html', ['preferences', 'configure', 'configuration', 'setup'], 'All Zoe configuration, integrations, panel options, and security controls.', 'settings')
    ];

    const settings = [
        setting('profile', 'Profile', ['username', 'email', 'role'], 'User name, email, and account role.', 'medium', 'settings'),
        setting('security', 'Security', ['pin', 'session', 'password', 'login'], 'PIN, sessions, authentication, and account protection.', 'high', 'settings'),
        setting('panel-identity', 'Panel Identity', ['panel name', 'location', 'default user', 'guest'], 'Touch panel name, room, default user, allowed users, and guest behavior.', 'high', 'settings'),
        setting('general', 'General', ['theme', 'appearance'], 'Theme and general application preferences.', 'low', 'settings'),
        // "Change setting" emits "change Zoe's Voice", which the skybridge
        // resolver's voice_settings intent handles with the live picker card.
        setting('zoe-voice', "Zoe's Voice", ['voice', 'speaking voice', 'tts', 'kokoro', 'preview voice'], 'Choose the voice Zoe speaks with, and preview each one.', 'low', 'settings'),
        setting('display', 'Display', ['brightness', 'night', 'screen', 'volume', 'dim'], 'Panel brightness, idle dimming, night mode, screen-off timing, and device volume.', 'medium', 'display'),
        setting('api', 'Integrations', ['home assistant', 'token', 'api key'], 'External integration endpoints and secrets.', 'critical', 'api'),
        setting('music', 'Music Settings', ['provider', 'audio quality', 'autoplay', 'output'], 'Music providers, quality, output devices, and playback preferences.', 'medium', 'music'),
        setting('calendar', 'Calendar Settings', ['calendar provider', 'sync', 'work hours', 'view'], 'Calendar view, providers, sync frequency, and work-hour preferences.', 'medium', 'calendar'),
        setting('notifications', 'Notifications', ['alerts', 'quiet hours', 'push', 'reminders'], 'Push notifications, reminder timing, quiet hours, and device subscriptions.', 'medium', 'notifications'),
        setting('productivity', 'Productivity', ['focus', 'break', 'pomodoro', 'tracking'], 'Focus timers, break reminders, and productivity tracking.', 'low', 'productivity'),
        setting('time-location', 'Time and Location', ['timezone', 'language', 'date format', '24 hour'], 'Time format, timezone, date format, and language.', 'low', 'settings'),
        setting('weather', 'Weather Settings', ['weather location', 'celsius', 'fahrenheit', 'unit'], 'Weather location, current-location permission, and temperature unit.', 'medium', 'weather'),
        setting('data', 'Data', ['export', 'import', 'backup'], 'Data export, import, backup, and portability tools.', 'high', 'data'),
        setting('system', 'System', ['status', 'version', 'uptime', 'health'], 'API health, version, uptime, and system diagnostics.', 'medium', 'system'),
        setting('openclaw-brain', 'OpenClaw Brain', ['openclaw', 'brain', 'agent'], 'OpenClaw state and brain integration settings.', 'high', 'agent'),
        setting('ai-training', 'AI Training', ['training', 'model', 'adapter', 'examples'], 'Training schedule, examples, adapters, and model learning controls.', 'high', 'training'),
        setting('intelligence', 'Intelligence', ['proactive', 'learning', 'insights', 'do not disturb'], 'Proactive behavior, learning, insights, and assistant intelligence toggles.', 'medium', 'intelligence'),
        setting('trust-gate', 'Trust Gate', ['allowlist', 'audit', 'approval'], 'Trust-gated actions, allowlists, and audit evidence.', 'critical', 'security'),
        setting('skills', 'Skills', ['skills', 'capabilities'], 'Installed skills and runtime capability availability.', 'high', 'skills'),
        setting('learnings', 'Learnings', ['pending learning', 'confirmed learning'], 'Pending and confirmed learned facts or behavior changes.', 'medium', 'learning'),
        setting('scheduler', 'Scheduler', ['jobs', 'schedule', 'cron'], 'Scheduled jobs and proactive runtime timing.', 'high', 'scheduler'),
        setting('self-creation', 'Self Creation', ['proposal', 'widget', 'evolution'], 'Self-improvement proposals and generated widget ideas.', 'critical', 'evolution')
    ];

    function page(id, title, route, aliases, summary, icon) {
        return {
            id,
            title,
            route,
            aliases,
            summary,
            icon,
            kind: 'page',
            actions: ['show', 'open', 'summarize']
        };
    }

    function setting(id, title, aliases, summary, risk, domain) {
        return {
            id,
            title,
            aliases,
            summary,
            risk,
            domain,
            kind: 'setting',
            route: '/touch/settings.html#' + id,
            actions: risk === 'critical'
                ? ['show', 'open', 'prepare_change', 'confirm_change']
                : ['show', 'open', 'change']
        };
    }

    function normalize(text) {
        return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    }

    function scoreItem(item, query) {
        const q = normalize(query);
        if (!q) return 0;
        const haystack = normalize([
            item.id,
            item.title,
            item.summary,
            (item.aliases || []).join(' ')
        ].join(' '));
        if (haystack.includes(q)) return 100;
        return q.split(/\s+/).reduce((score, word) => {
            if (!word) return score;
            if (normalize(item.title).includes(word)) return score + 18;
            if ((item.aliases || []).some(alias => normalize(alias).includes(word))) return score + 14;
            if (haystack.includes(word)) return score + 8;
            return score;
        }, 0);
    }

    function find(query) {
        const all = pages.concat(settings);
        return all
            .map(item => Object.assign({ score: scoreItem(item, query) }, item))
            .filter(item => item.score > 0)
            .sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
    }

    // The guest dashboard shown on wake: glance cards anyone at the panel can see.
    // Instant (no fetch) cards — time + room controls; live weather is appended by
    // the wake handler. Personal data (calendar/lists/people) still gates on tap.
    function getHomeCards() {
        var now = new Date();
        var h24 = now.getHours();
        var hour12 = ((h24 + 11) % 12) + 1;
        var greeting = h24 < 12 ? 'Good morning' : (h24 < 18 ? 'Good afternoon' : 'Good evening');
        var clockCard = {
            component: 'status',
            props: {
                source: 'clock_show',
                summary: greeting,
                hour: String(hour12),
                minute: ('0' + now.getMinutes()).slice(-2),
                meridiem: h24 < 12 ? 'AM' : 'PM',
                weekday: now.toLocaleDateString(undefined, { weekday: 'long' }),
                date_label: now.toLocaleDateString(undefined, { day: 'numeric', month: 'long' })
            }
        };
        var roomCard = {
            component: 'status',
            props: {
                title: 'Room controls',
                kicker: 'Home',
                body: 'Lights, scenes and devices for this room.',
                status: 'Home',
                wide: true,
                actions: [{ label: 'Open room controls', query: 'smart home' }]
            }
        };
        return [clockCard, roomCard];
    }

    window.SkybridgeCapabilities = {
        pages,
        settings,
        find,
        getHomeCards
    };
})();

/**
 * Zoe PWA Service Worker
 * Powered by Workbox for efficient caching and offline support
 * Version: 1.0.0
 */

// Import Workbox from the LOCAL vendored copy in dist/workbox/.
// Provenance + refresh procedure: services/zoe-ui/AGENTS.md.
// Zoe is local-first: she runs on a LAN box that may be offline, so the service
// worker must not depend on a third-party CDN. Loading workbox-sw from Google
// also pinged Google from every client on every SW boot — a privacy leak.
importScripts('/workbox/workbox-sw.js');

// Zoe UI Version 4.17.3 - public modules (with or without trailing path segment)
const SW_VERSION = '4.63.56'; // workbox served locally (no CDN)
const CACHE_NAME = `zoe-ui-v${SW_VERSION}`;

// Verify Workbox loaded
if (workbox) {
    console.log(`🚀 Zoe Service Worker ${SW_VERSION} - Workbox loaded`);

    // Configure Workbox.
    //
    // modulePathPrefix is LOAD-BEARING for the local-first guarantee: workbox-sw.js
    // is only a lazy LOADER — on first access of workbox.core / workbox.routing /…
    // it importScripts()es each module, and its built-in default base URL is
    // Google's CDN. Vendoring workbox-sw.js alone would NOT remove the CDN
    // dependency; this prefix is what redirects those module loads to
    // /workbox/workbox-<module>.prod.js on our own origin.
    //
    // debug:false pins the 'prod' build variant, which is what dist/workbox/ ships
    // (the .dev.js variants are deliberately not vendored). Flipping debug to true
    // would make Workbox request .dev.js files that do not exist locally.
    //
    // setConfig must run BEFORE any workbox.<module> access — Workbox throws
    // "Config must be set before accessing workbox.* modules" otherwise. The
    // `if (workbox)` guard above does not touch a module namespace, so this is
    // still the first access.
    workbox.setConfig({
        debug: false,
        modulePathPrefix: '/workbox/'
    });

    // Set cache name prefix
    workbox.core.setCacheNameDetails({
        prefix: 'zoe',
        suffix: SW_VERSION
    });

    // Skip waiting and claim clients immediately on update
    workbox.core.skipWaiting();
    workbox.core.clientsClaim();

    // Upgrade genuine REMOTE http:// fetches to https:// when the SW is on HTTPS,
    // so real mixed-content requests aren't blocked by the browser.
    //
    // EXCEPTION (BUG FIX): loopback and private-LAN targets must pass through
    // un-rewritten. The touch UI deliberately calls LOCAL panel daemons over plain
    // http — the voice-activate daemon on http://localhost:7777/activate and the
    // wake beacon on http://127.0.0.1:8765/wake (see touch/js/touch-menu.js). Those
    // daemons don't speak TLS, and browsers already treat loopback as a secure
    // context, so these are NOT mixed-content and must not be upgraded — rewriting
    // them to https breaks voice activation / wake on HTTPS deployments.
    // True only for a syntactically valid IPv4 literal (exactly four 0–255 octets),
    // so a public hostname that merely starts with private-range digits
    // (e.g. "192.168.example.com", "10.example.com") is NOT treated as one.
    function ipv4Octets(h) {
        const parts = h.split('.');
        if (parts.length !== 4) return null;
        const octets = [];
        for (const part of parts) {
            if (!/^\d{1,3}$/.test(part)) return null;
            const n = Number(part);
            if (n > 255) return null;
            octets.push(n);
        }
        return octets;
    }
    function isLocalRequestTarget(url) {
        const h = url.hostname;
        // Explicit non-IP local names: loopback alias + mDNS panel hostnames.
        if (h === 'localhost' || h.endsWith('.localhost')) return true;
        if (h.endsWith('.local')) return true;
        // IPv6 loopback. URL.hostname keeps the brackets for IPv6 literals,
        // so new URL('http://[::1]/').hostname === '[::1]'.
        if (h === '::1' || h === '[::1]') return true;
        // IPv4 loopback / private ranges — ONLY for genuine IPv4 literals, never
        // for arbitrary hostnames. Public remote http must still upgrade to https.
        const o = ipv4Octets(h);
        if (o) {
            if (o[0] === 127) return true;                          // 127.0.0.0/8 loopback
            if (o[0] === 10) return true;                           // 10.0.0.0/8
            if (o[0] === 192 && o[1] === 168) return true;          // 192.168.0.0/16
            if (o[0] === 172 && o[1] >= 16 && o[1] <= 31) return true; // 172.16.0.0/12
            if (o[0] === 0 && o[1] === 0 && o[2] === 0 && o[3] === 0) return true; // 0.0.0.0
        }
        return false;
    }
    self.addEventListener('fetch', (event) => {
        if (self.location.protocol !== 'https:') return; // only apply on HTTPS
        const url = new URL(event.request.url);
        if (url.protocol === 'http:' && !isLocalRequestTarget(url)) {
            const httpsUrl = event.request.url.replace('http://', 'https://');
            event.respondWith(fetch(new Request(httpsUrl, event.request)));
            return;
        }
    });

    // ===== PRECACHING =====
    // Precache critical assets during service worker installation
    workbox.precaching.precacheAndRoute([
        { url: '/', revision: SW_VERSION },
        { url: '/index.html', revision: SW_VERSION },
        { url: '/dashboard.html', revision: SW_VERSION },
        { url: '/chat.html', revision: SW_VERSION },
        { url: '/calendar.html', revision: SW_VERSION },
        { url: '/lists.html', revision: SW_VERSION },
        { url: '/manifest.json', revision: SW_VERSION },
        { url: '/offline.html', revision: SW_VERSION },
        { url: '/js/push-notifications.js', revision: SW_VERSION },
        { url: '/js/sw-registration.js', revision: SW_VERSION },
        { url: '/css/dark-mode-shared.css', revision: SW_VERSION }
    ]);

    // ===== CACHING STRATEGIES =====

    // The estate home is a live voice/data surface. Never serve stale HTML,
    // auth, or runtime JS for it. (The /touch/js/skybridge* prefix still covers
    // the shared design-system theme script loaded by the login page.)
    workbox.routing.registerRoute(
        ({ url }) => url.pathname === '/touch/home.html' || url.pathname === '/js/auth.js' || url.pathname.startsWith('/touch/js/skybridge'),
        new workbox.strategies.NetworkOnly()
    );

    // 1. HTML Pages - Network First (fresh content, fallback to cache)
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'document',
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-html',
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 50,
                    maxAgeSeconds: 7 * 24 * 60 * 60 // 7 days
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // 2. JavaScript - Network First for widgets and widget-system, Cache First for other JS
    workbox.routing.registerRoute(
        ({ request }) => {
            // Check if it's a widget file, widget-system.js, or widget-base.js - always network first
            return request.destination === 'script' && (
                request.url.includes('/widgets/') ||
                request.url.includes('widget-system.js') ||
                request.url.includes('widget-base.js')
            );
        },
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-widgets',
            networkTimeoutSeconds: 3,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 50,
                    maxAgeSeconds: 60 * 60 // 1 hour only
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // Other JavaScript - Network First (ensures auth/executor fixes deploy instantly)
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'script',
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-js',
            networkTimeoutSeconds: 3,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 60,
                    maxAgeSeconds: 24 * 60 * 60 // 1 day
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // 3. CSS - Network First for widget CSS, Cache First for others
    workbox.routing.registerRoute(
        ({ request }) => {
            return request.destination === 'style' && request.url.includes('/widgets/');
        },
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-widgets-css',
            networkTimeoutSeconds: 3,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 30,
                    maxAgeSeconds: 60 * 60 // 1 hour only
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // Other CSS - Network First (ensures style fixes deploy instantly)
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'style',
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-css',
            networkTimeoutSeconds: 3,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 30,
                    maxAgeSeconds: 24 * 60 * 60 // 1 day
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // 4. Images - Cache First with expiration
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'image',
        new workbox.strategies.CacheFirst({
            cacheName: 'zoe-images',
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 100,
                    maxAgeSeconds: 7 * 24 * 60 * 60 // 7 days
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // 5. Auth + Chat + notifications + health + WebSocket - Network Only (never cache)
    workbox.routing.registerRoute(
        ({ url }) => {
            const p = url.pathname;
            return p === '/health' ||
                p.startsWith('/api/auth/') ||
                p.startsWith('/api/ui/actions/pending') ||
                /\/api\/ui\/actions\/[^/]+\/ack$/.test(p) ||
                p.startsWith('/api/ui/panel/') ||
                p.startsWith('/api/ui/state/') ||
                p.startsWith('/api/panels/') ||
                p.startsWith('/api/touch-panel/') ||
                p === '/api/chat' ||
                p.startsWith('/api/chat/') ||
                p.startsWith('/api/admin/') ||
                p.startsWith('/api/notifications') ||
                p.startsWith('/ws/');
        },
        new workbox.strategies.NetworkOnly()
    );

    // 6. Other API Calls — privacy-first, default-DENY caching.
    //
    // BUG FIX: the previous rule cached EVERY remaining /api/ response with
    // NetworkFirst for up to 1 hour. That swept in authenticated, user-scoped
    // personal data (journal, memories, people, notes, lists, reminders,
    // transactions, calendar, dashboard, user profile, …). Persisting those in
    // Cache Storage means (a) stale private data can be served back, and (b) on a
    // SHARED KIOSK, after a user/session switch the next user's NetworkFirst miss
    // (or offline) could serve the PREVIOUS user's cached personal data — a
    // cross-user leak.
    //
    // Fix: invert the default. Cache ONLY an explicit allowlist of endpoints that
    // are provably non-personal AND identical for everyone AND not user-keyed;
    // everything else under /api/ falls through to NetworkOnly and is never
    // written to the cache. Safe-by-default: any new personal route is
    // non-cacheable automatically, without having to be enumerated here.
    //
    // The allowlist is currently EMPTY: every /api endpoint reviewed is
    // authenticated and/or user-keyed. Even seemingly-shared paths are personal —
    // /api/weather/{current,forecast,preferences} read weather_preferences by the
    // authenticated user_id, and /api/system/capability-matrix/me returns the
    // caller's role-specific matrix (its base path is admin-only). Caching any of
    // them would stale or leak per-user data across kiosk sessions. Losing offline
    // cache for /api is acceptable; leaking personal data is not. Entries, if ever
    // added, must be an EXACT pathname (not a prefix — a prefix could also match
    // /me, /preferences, or admin sub-paths) and provably non-personal.
    //
    // (Auth, chat, notifications, ui/panel, admin, health and ws are already
    // NetworkOnly via rule #5 above, which is registered first and wins.)
    const CACHEABLE_API_PATHS = new Set([
        // intentionally empty — default-deny
    ]);
    const isCacheableApi = (pathname) => CACHEABLE_API_PATHS.has(pathname);

    // 6a. Allowlisted non-personal API — Network First (fresh data, short cache
    //     fallback). Registered only when the allowlist is non-empty; while empty,
    //     all /api/ traffic is handled by rule 6b below.
    if (CACHEABLE_API_PATHS.size > 0) {
        workbox.routing.registerRoute(
            ({ url }) => isCacheableApi(url.pathname),
            new workbox.strategies.NetworkFirst({
                cacheName: 'zoe-api',
                networkTimeoutSeconds: 5,
                plugins: [
                    new workbox.expiration.ExpirationPlugin({
                        maxEntries: 100,
                        maxAgeSeconds: 60 * 60
                    }),
                    new workbox.cacheableResponse.CacheableResponsePlugin({
                        statuses: [0, 200]
                    })
                ]
            })
        );
    }

    // 6b. Every other API call — Network Only. Personal/user-scoped data is
    //     never persisted, so it can't go stale or leak across a kiosk session
    //     switch. Offline simply fails (handled by setCatchHandler) rather than
    //     serving someone else's private data.
    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith('/api/'),
        new workbox.strategies.NetworkOnly()
    );

    // 7. Fonts - Cache First (rarely change)
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'font',
        new workbox.strategies.CacheFirst({
            cacheName: 'zoe-fonts',
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 20,
                    maxAgeSeconds: 365 * 24 * 60 * 60 // 1 year
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );

    // ===== SELF-TEST (no-op in production) =====
    // Demonstrates the two invariants this SW must hold. Exposed for a test
    // harness and run only when self.__SW_SELFTEST__ is set, so it never
    // executes on real kiosks. Returns [{name, pass}] and throws on any failure.
    self.__swSelfTest = function () {
        const results = [];
        const assert = (name, cond) => results.push({ name, pass: !!cond });

        // BUG 1 — default-deny: NO /api endpoint is cacheable. Personal/user-scoped
        //         routes and the previously-(wrongly)-allowlisted weather /
        //         capability-matrix paths must all fall through to NetworkOnly.
        assert('journal entries NOT cacheable',  !isCacheableApi('/api/journal/entries'));
        assert('memories people NOT cacheable',  !isCacheableApi('/api/memories/people'));
        assert('calendar NOT cacheable',         !isCacheableApi('/api/calendar/events'));
        assert('dashboard NOT cacheable',        !isCacheableApi('/api/dashboard/layout'));
        assert('user profile NOT cacheable',     !isCacheableApi('/api/user/profile'));
        assert('weather NOT cacheable',          !isCacheableApi('/api/weather'));
        assert('weather/current NOT cacheable',  !isCacheableApi('/api/weather/current'));
        assert('capability-matrix NOT cacheable',!isCacheableApi('/api/system/capability-matrix'));
        assert('capability-matrix/me NOT cache', !isCacheableApi('/api/system/capability-matrix/me'));

        // BUG 2 — loopback / private-LAN daemon URLs must NOT be upgraded to https;
        //         genuine remote http URLs still are.
        assert('localhost:7777 daemon kept http', isLocalRequestTarget(new URL('http://localhost:7777/activate')));
        assert('127.0.0.1:8765 daemon kept http', isLocalRequestTarget(new URL('http://127.0.0.1:8765/wake')));
        assert('IPv4 192.168 literal IS local',   isLocalRequestTarget(new URL('http://192.168.1.50:8765/wake')));
        assert('IPv4 10.x literal IS local',      isLocalRequestTarget(new URL('http://10.0.0.5/wake')));
        assert('IPv4 172.16 literal IS local',    isLocalRequestTarget(new URL('http://172.16.0.9/wake')));
        assert('IPv6 [::1] loopback IS local',    isLocalRequestTarget(new URL('http://[::1]:8765/wake')));
        assert('*.local panel host kept http',    isLocalRequestTarget(new URL('http://panel.local:8765/wake')));
        // Public hostnames that merely START with private-range digits must NOT be
        // treated as local — they are genuine remote http and must still upgrade.
        assert('192.168.example.com IS remote',  !isLocalRequestTarget(new URL('http://192.168.example.com/x')));
        assert('10.example.com IS remote',       !isLocalRequestTarget(new URL('http://10.example.com/x')));
        assert('172.16.example.com IS remote',   !isLocalRequestTarget(new URL('http://172.16.example.com/x')));
        assert('172.32 IPv4 literal IS remote',  !isLocalRequestTarget(new URL('http://172.32.0.1/x')));
        assert('remote http IS upgraded',        !isLocalRequestTarget(new URL('http://example.com/api/x')));

        const failed = results.filter((r) => !r.pass);
        if (failed.length) throw new Error('SW self-test failed: ' + failed.map((r) => r.name).join(', '));
        return results;
    };
    if (self.__SW_SELFTEST__) console.table(self.__swSelfTest());

    // ===== OFFLINE FALLBACK =====
    workbox.routing.setCatchHandler(async ({ event }) => {
        // Return cached response or offline page for navigation requests
        if (event.request.destination === 'document') {
            return caches.match('/offline.html') || Response.error();
        }

        return Response.error();
    });

} else {
    console.error('❌ Workbox failed to load');
}

// ===== PUSH NOTIFICATIONS =====
// Handle push events for notifications
self.addEventListener('push', (event) => {
    console.log('📬 Push notification received');

    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        console.error('Failed to parse push data:', e);
        data = {
            title: 'Zoe',
            body: event.data ? event.data.text() : 'You have a new notification'
        };
    }

    const title = data.title || 'Zoe';
    // Keep options minimal and iOS-safe: no undefined values, no unsupported fields
    const options = {
        body: data.body || 'You have a new notification',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-72.png',
        tag: data.tag || 'zoe-notification',
        data: {
            url: data.url || '/',
            timestamp: Date.now()
        },
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Handle notification clicks — supports proactive deep-linking via ?p= parameter.
self.addEventListener('notificationclick', (event) => {
    console.log('🖱️ Notification clicked:', event.notification.tag);

    event.notification.close();

    const rawUrl = event.notification.data?.url || '/';
    // Resolve to an absolute URL so pathname comparison works cross-origin.
    const targetUrl = new URL(rawUrl, self.location.origin);

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // If a chat tab is already open, postMessage so it can handle
                // the pending claim without a full reload.
                for (const client of clientList) {
                    const clientUrl = new URL(client.url);
                    if (clientUrl.pathname === targetUrl.pathname && 'focus' in client) {
                        client.postMessage({ type: 'proactive_tap', url: targetUrl.href });
                        return client.focus();
                    }
                }
                // No matching tab — open the deep-link directly.
                if (clients.openWindow) {
                    return clients.openWindow(targetUrl.href);
                }
            })
    );
});

// Handle notification close
self.addEventListener('notificationclose', (event) => {
    console.log('🔕 Notification closed:', event.notification.tag);
    // Optional: Send analytics about notification dismissal
});

// ===== BACKGROUND SYNC =====
// Handle background sync for queued actions
self.addEventListener('sync', (event) => {
    console.log('🔄 Background sync triggered:', event.tag);

    if (event.tag === 'sync-queued-actions') {
        event.waitUntil(syncQueuedActions());
    }
});

async function syncQueuedActions() {
    // Placeholder for syncing queued actions when back online
    // This will be implemented in Phase 3
    console.log('Syncing queued actions...');
    return Promise.resolve();
}

// ===== MESSAGE HANDLING =====
// Handle messages from clients
self.addEventListener('message', (event) => {
    console.log('💬 Message received:', event.data);

    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }

    if (event.data && event.data.type === 'CACHE_URLS') {
        event.waitUntil(
            caches.open(CACHE_NAME).then((cache) => {
                return cache.addAll(event.data.urls);
            })
        );
    }

    if (event.data && event.data.type === 'CLEAR_CACHE') {
        // Delete all zoe-* caches and take control
        event.waitUntil((async () => {
            const names = await caches.keys();
            await Promise.all(names.filter(n => n.startsWith('zoe-')).map(n => caches.delete(n)));
            // Ensure new SW takes control and clients reload
            await self.skipWaiting();
            await self.clients.claim();
            const clientList = await clients.matchAll({ type: 'window', includeUncontrolled: true });
            clientList.forEach(client => client.postMessage({ type: 'CACHE_CLEARED', version: SW_VERSION }));
        })());
    }

    if (event.data && event.data.type === 'GET_VERSION') {
        event.ports[0].postMessage({ version: SW_VERSION });
    }

    // Panel executor control — sent by touch-ui-executor.js on init/destroy
    if (event.data && event.data.type === 'START_PANEL_POLL') {
        _startPanelPoll(event.data.panelId, event.data.sessionId);
    }
    if (event.data && event.data.type === 'STOP_PANEL_POLL') {
        _stopPanelPoll();
    }
});

// ===== PANEL EXECUTOR (cross-page navigation) =====
// The service worker persists across page navigations and can drive panel actions
// even when the page-level executor is not running (e.g. after navigating away from dashboard.html).

let _panelPollTimer = null;
let _panelId = null;
let _panelSessionId = null;

async function _panelFetch(path) {
    const headers = { 'Content-Type': 'application/json' };
    if (_panelSessionId) headers['X-Session-ID'] = _panelSessionId;
    const resp = await fetch(path, { headers, credentials: 'include' });
    if (!resp.ok) {
        const err = new Error(`HTTP ${resp.status}`);
        err.status = resp.status;
        throw err;
    }
    return resp.json();
}

async function _panelAck(actionId, status, errorCode, errorMessage) {
    const headers = { 'Content-Type': 'application/json' };
    if (_panelSessionId) headers['X-Session-ID'] = _panelSessionId;
    try {
        await fetch(`/api/ui/actions/${actionId}/ack`, {
            method: 'POST',
            headers,
            credentials: 'include',
            body: JSON.stringify({ status, error_code: errorCode || null, error_message: errorMessage || null }),
        });
    } catch (_) {}
}

async function _panelPoll() {
    if (!_panelId) return;
    try {
        const data = await _panelFetch(`/api/ui/actions/pending?panel_id=${encodeURIComponent(_panelId)}&limit=5`);
        const actions = Array.isArray(data.actions) ? data.actions : [];
        for (const action of actions) {
            if (!action || !action.id) continue;
            const type = action.action_type;
            const payload = action.payload || {};

            // Forward all actions to page clients — they will handle non-navigation actions.
            // The SW handles navigation itself so it works even after a page transition.
            const clientList = await clients.matchAll({ type: 'window', includeUncontrolled: true });
            let handled = false;

            if (type === 'panel_navigate' || type === 'panel_navigate_fullscreen' || type === 'navigate' || type === 'refresh') {
                let url = payload.url || payload.page || payload.path;
                // Preserve kiosk and panel_id params so touch-panel auth bypass survives navigation.
                if (url && _panelId) {
                    try {
                        const dest = new URL(url, self.location.origin);
                        if (!dest.searchParams.has('kiosk')) dest.searchParams.set('kiosk', '1');
                        if (!dest.searchParams.has('panel_id')) dest.searchParams.set('panel_id', _panelId);
                        url = dest.pathname + dest.search;
                    } catch (_) {}
                }
                for (const client of clientList) {
                    if (type === 'refresh') {
                        client.postMessage({ type: 'SW_PANEL_ACTION', action });
                        await _panelAck(action.id, 'success', null, null);
                        handled = true;
                    } else if (url) {
                        try {
                            await client.navigate(url);
                            await _panelAck(action.id, 'success', null, null);
                            handled = true;
                        } catch (navErr) {
                            client.postMessage({ type: 'SW_PANEL_ACTION', action });
                            await _panelAck(action.id, 'success', null, null);
                            handled = true;
                        }
                        break;
                    }
                }
                if (!handled) await _panelAck(action.id, 'blocked', 'no_client', 'No open window to navigate');
            } else {
                // For non-navigation actions, delegate to the page executor.
                for (const client of clientList) {
                    client.postMessage({ type: 'SW_PANEL_ACTION', action });
                }
                // The page executor will ack via its own ackAction; SW optimistically acks here too
                // only if no clients exist to handle it.
                if (clientList.length === 0) {
                    await _panelAck(action.id, 'blocked', 'no_client', 'No page executor available');
                }
                // Don't ack — let the page executor do it to avoid double-ack.
            }
        }
    } catch (err) {
        if (err && (err.status === 401 || err.status === 403)) {
            _stopPanelPoll();
            console.warn(`🎛️ SW panel executor stopped after auth failure: HTTP ${err.status}`);
            return;
        }
        // Silently retry — network may be unavailable.
    }
}

function _startPanelPoll(panelId, sessionId) {
    _panelId = panelId;
    _panelSessionId = sessionId || null;
    if (_panelPollTimer) clearInterval(_panelPollTimer);
    // 5s fallback poll — WS push now handles the fast path for instant delivery
    _panelPollTimer = setInterval(_panelPoll, 5000);
    _panelPoll(); // immediate first poll
    console.log(`🎛️ SW panel executor started for panel=${panelId}`);
}

function _stopPanelPoll() {
    if (_panelPollTimer) { clearInterval(_panelPollTimer); _panelPollTimer = null; }
    _panelId = null;
    _panelSessionId = null;
    console.log('🎛️ SW panel executor stopped');
}

// ===== INSTALLATION & ACTIVATION =====
self.addEventListener('install', (event) => {
    console.log(`📦 Installing Service Worker ${SW_VERSION}`);
});

self.addEventListener('activate', (event) => {
    console.log(`✅ Service Worker ${SW_VERSION} activated`);

    // 1) Clean up OLD version caches only — keep current version's precache
    //    and all runtime caches (zoe-api, zoe-css, …).
    const currentPrecache = `zoe-precache-v2-${SW_VERSION}`;
    const purgeOldPrecaches = caches.keys().then((cacheNames) => {
        return Promise.all(
            cacheNames
                .filter((cacheName) => /^zoe-precache-v\d+-/.test(cacheName) && cacheName !== currentPrecache)
                .map((cacheName) => {
                    console.log(`🗑️ Deleting old cache: ${cacheName}`);
                    return caches.delete(cacheName);
                })
        );
    });

    // 2) Purge stale /modules/qd, /modules/jag-board and /modules/orbit cache
    //    entries from existing clients. These modules were RETIRED (2026-06-24,
    //    see docs/CANONICAL.md) — this one-shot purge drops their now-dead pages
    //    from any service worker that cached them before removal.
    const purgeStaleModules = caches.keys().then((names) =>
        Promise.all(names.map((name) =>
            caches.open(name).then((cache) =>
                cache.keys().then((requests) =>
                    Promise.all(requests
                        .filter((r) => {
                            const p = new URL(r.url).pathname;
                            return (
                                p === '/modules/qd' ||
                                p.startsWith('/modules/qd/') ||
                                p === '/modules/jag-board' ||
                                p.startsWith('/modules/jag-board/') ||
                                p === '/modules/orbit' ||
                                p.startsWith('/modules/orbit/')
                            );
                        })
                        .map((r) => cache.delete(r))
                    )
                )
            )
        ))
    );

    // 3) One-shot purge of the runtime-cached pages/scripts carrying the
    //    local-timezone date fix that are NOT in the Workbox precache:
    //    /touch/calendar.html, /journal.html (zoe-html) and the Today widget
    //    /touch/js/touch-widgets.js (zoe-widgets/zoe-js). These routes are
    //    NetworkFirst, so an online client already revalidates on next load;
    //    dropping any cached copy here also closes the offline-fallback window
    //    so a flaky-network kiosk can't keep serving the pre-fix code.
    const dateFixPaths = new Set([
        '/touch/calendar.html',
        '/journal.html',
        '/touch/js/touch-widgets.js',
    ]);
    const purgeStaleDateFix = caches.keys().then((names) =>
        Promise.all(names.map((name) =>
            caches.open(name).then((cache) =>
                cache.keys().then((requests) =>
                    Promise.all(requests
                        .filter((r) => dateFixPaths.has(new URL(r.url).pathname))
                        .map((r) => cache.delete(r))
                    )
                )
            )
        ))
    );

    event.waitUntil(Promise.all([purgeOldPrecaches, purgeStaleModules, purgeStaleDateFix]));
});

// NOTE: a second copy of 'push', 'notificationclick' and 'activate' handlers
// used to live here. They fired in addition to the richer handlers defined
// above, causing notifications to be shown twice. Removed intentionally.

console.log(`🎯 Zoe Service Worker ${SW_VERSION} loaded successfully`);


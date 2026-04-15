/**
 * Zoe PWA Service Worker
 * Powered by Workbox for efficient caching and offline support
 * Version: 1.0.0
 */

// Import Workbox from CDN (no build step needed)
importScripts('https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js');

// Zoe UI Version 4.17.3 - public modules (with or without trailing path segment)
const SW_VERSION = '4.26.0';
const CACHE_NAME = `zoe-ui-v${SW_VERSION}`;

// Verify Workbox loaded
if (workbox) {
    console.log(`🚀 Zoe Service Worker ${SW_VERSION} - Workbox loaded`);
    
    // Configure Workbox
    workbox.setConfig({
        debug: false
    });
    
    // Set cache name prefix
    workbox.core.setCacheNameDetails({
        prefix: 'zoe',
        suffix: SW_VERSION
    });
    
    // Skip waiting and claim clients immediately on update
    workbox.core.skipWaiting();
    workbox.core.clientsClaim();

    // Force all fetch requests to HTTPS when the SW itself is on HTTPS.
    // This includes local IPs (192.168.x, 127.x) since an HTTPS-served SW cannot
    // make mixed-content HTTP requests — FastAPI redirect responses to http://localhost
    // would be blocked by the browser's mixed-content policy.
    self.addEventListener('fetch', (event) => {
        if (self.location.protocol !== 'https:') return; // only apply on HTTPS
        const url = new URL(event.request.url);
        if (url.protocol === 'http:') {
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
        { url: '/offline.html', revision: SW_VERSION }
    ]);
    
    // ===== CACHING STRATEGIES =====
    
    // Self-hosted module HTML (/modules/qd/, /modules/jag-board/): never cache as documents
    // (avoids storing index.html under a module URL if origin misroutes).
    workbox.routing.registerRoute(
        ({ request, url }) => {
            if (request.destination !== 'document') return false;
            const p = url.pathname;
            return (
                p === '/modules/qd' ||
                p.startsWith('/modules/qd/') ||
                p === '/modules/jag-board' ||
                p.startsWith('/modules/jag-board/') ||
                p === '/modules/orbit' ||
                p.startsWith('/modules/orbit/')
            );
        },
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
                p === '/api/chat' ||
                p.startsWith('/api/chat/') ||
                p.startsWith('/api/admin/') ||
                p.startsWith('/api/notifications') ||
                p.startsWith('/ws/');
        },
        new workbox.strategies.NetworkOnly()
    );

    // 6. Other API Calls - Network First (fresh data, fallback to cache)
    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith('/api/'),
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
    const options = {
        body: data.body || 'You have a new notification',
        icon: data.icon || '/icons/icon-192.png',
        badge: data.badge || '/icons/icon-72.png',
        image: data.image,
        vibrate: data.vibrate || [200, 100, 200],
        tag: data.tag || 'zoe-notification',
        data: {
            url: data.url || '/',
            action: data.action,
            timestamp: Date.now()
        },
        actions: data.actions || [],
        requireInteraction: data.requireInteraction || false,
        silent: data.silent || false
    };
    
    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
    console.log('🖱️ Notification clicked:', event.notification.tag);
    
    event.notification.close();
    
    const urlToOpen = event.notification.data?.url || '/';
    
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Check if there's already a window open
                for (const client of clientList) {
                    if (client.url === urlToOpen && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Open new window if none exists
                if (clients.openWindow) {
                    return clients.openWindow(urlToOpen);
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
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
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
    } catch (_) {
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
    
    // Clean up OLD version caches only — keep current version's precache and all runtime caches.
    // Pattern: delete versioned precache caches from previous SW versions (e.g. zoe-precache-v2-4.18.0)
    // but NOT the current version's precache or named runtime caches (zoe-api, zoe-css, etc.).
    const currentPrecache = `zoe-precache-v2-${SW_VERSION}`;
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((cacheName) => {
                        // Only delete versioned precaches from older SW versions
                        return /^zoe-precache-v\d+-/.test(cacheName) && cacheName !== currentPrecache;
                    })
                    .map((cacheName) => {
                        console.log(`🗑️ Deleting old cache: ${cacheName}`);
                        return caches.delete(cacheName);
                    })
            );
        })
    );
});

// ===== PUSH NOTIFICATIONS =====
self.addEventListener('push', (event) => {
    let data = { title: 'Zoe', body: 'New notification', url: '/' };
    try {
        if (event.data) data = Object.assign(data, event.data.json());
    } catch (e) {
        if (event.data) data.body = event.data.text();
    }
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/icons/icon-192.png',
            badge: '/icons/icon-96.png',
            data: { url: data.url || '/' },
            vibrate: [100, 50, 100],
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = event.notification.data?.url || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    client.navigate(url);
                    return client.focus();
                }
            }
            return clients.openWindow(url);
        })
    );
});

// On activate, purge cached /modules/qd/ and /modules/jag-board/ entries that may have been
// stored as index.html before the nginx proxy was added.
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then(names =>
            Promise.all(names.map(name =>
                caches.open(name).then(cache =>
                    cache.keys().then(requests =>
                        Promise.all(requests
                            .filter(r => {
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
                            .map(r => cache.delete(r))
                        )
                    )
                )
            ))
        )
    );
});

console.log(`🎯 Zoe Service Worker ${SW_VERSION} loaded successfully`);


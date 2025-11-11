/**
 * Zoe PWA Service Worker
 * Powered by Workbox for efficient caching and offline support
 * Version: 1.0.0
 */

// Import Workbox from CDN (no build step needed)
importScripts('https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js');

// Zoe UI Version 4.13.1 - Widget System Fix
const SW_VERSION = '4.13.1';
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
    
    // Other JavaScript - Cache First
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'script',
        new workbox.strategies.CacheFirst({
            cacheName: 'zoe-js',
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 60,
                    maxAgeSeconds: 30 * 24 * 60 * 60 // 30 days
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
    
    // Other CSS - Cache First
    workbox.routing.registerRoute(
        ({ request }) => request.destination === 'style',
        new workbox.strategies.CacheFirst({
            cacheName: 'zoe-css',
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 30,
                    maxAgeSeconds: 30 * 24 * 60 * 60 // 30 days
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
    
    // 5. API Calls - Network First (fresh data, fallback to cache)
    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith('/api/'),
        new workbox.strategies.NetworkFirst({
            cacheName: 'zoe-api',
            networkTimeoutSeconds: 5,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 100,
                    maxAgeSeconds: 60 * 60 // 1 hour
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200]
                })
            ]
        })
    );
    
    // 6. Fonts - Cache First (rarely change)
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
});

// ===== INSTALLATION & ACTIVATION =====
self.addEventListener('install', (event) => {
    console.log(`📦 Installing Service Worker ${SW_VERSION}`);
});

self.addEventListener('activate', (event) => {
    console.log(`✅ Service Worker ${SW_VERSION} activated`);
    
    // Clean up old caches
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((cacheName) => {
                        return cacheName.startsWith('zoe-') && cacheName !== CACHE_NAME;
                    })
                    .map((cacheName) => {
                        console.log(`🗑️ Deleting old cache: ${cacheName}`);
                        return caches.delete(cacheName);
                    })
            );
        })
    );
});

console.log(`🎯 Zoe Service Worker ${SW_VERSION} loaded successfully`);


/**
 * Zoe Push Notifications Handler
 * Manages push notification subscriptions and permissions
 */

(function() {
    'use strict';
    
    const API_BASE = '/api/push';
    let vapidPublicKey = null;
    
    /**
     * Check if push notifications are supported
     */
    function isPushSupported() {
        return 'serviceWorker' in navigator && 
               'PushManager' in window && 
               'Notification' in window;
    }
    
    /**
     * Get current notification permission status
     */
    function getPermissionStatus() {
        if (!isPushSupported()) {
            return 'unsupported';
        }
        return Notification.permission; // 'granted', 'denied', or 'default'
    }
    
    /**
     * Request notification permission from user
     */
    async function requestPermission() {
        if (!isPushSupported()) {
            console.log('❌ Push notifications not supported');
            return false;
        }
        
        if (Notification.permission === 'granted') {
            console.log('✅ Notification permission already granted');
            return true;
        }
        
        if (Notification.permission === 'denied') {
            console.log('❌ Notification permission denied');
            return false;
        }
        
        // Request permission
        const permission = await Notification.requestPermission();
        
        if (permission === 'granted') {
            console.log('✅ Notification permission granted');
            return true;
        } else {
            console.log('❌ Notification permission denied');
            return false;
        }
    }
    
    /**
     * Get VAPID public key from server
     */
    async function getVapidPublicKey() {
        if (vapidPublicKey) {
            return vapidPublicKey;
        }
        
        // A guest / logged-out visitor gets 403 here; its JSON body carries no key,
        // and the old code handed `undefined` to urlBase64ToUint8Array (TypeError
        // on every page load). Resolve null instead and let the caller bail.
        try {
            const response = await fetch(`${API_BASE}/vapid-public-key`);
            if (!response.ok) return null;
            const data = await response.json();
            const key = data && (data.public_key || data.publicKey);
            if (typeof key !== 'string' || !key) return null;
            vapidPublicKey = key;
            return vapidPublicKey;
        } catch (error) {
            console.warn('⚠️ VAPID public key unavailable:', error && error.message);
            return null;
        }
    }
    
    /**
     * Convert base64 string to Uint8Array (required by Push API)
     */
    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');
        
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }
    
    /**
     * Subscribe to push notifications
     */
    async function subscribeToPush() {
        if (!isPushSupported()) {
            throw new Error('Push notifications not supported');
        }
        
        // Check permission
        if (Notification.permission !== 'granted') {
            const granted = await requestPermission();
            if (!granted) {
                throw new Error('Notification permission not granted');
            }
        }
        
        try {
            // Get service worker registration
            const registration = await navigator.serviceWorker.ready;
            
            // Always unsubscribe any existing (possibly stale/expired) subscription
            // before creating a fresh one to guarantee a valid FCM endpoint.
            const existing = await registration.pushManager.getSubscription();
            if (existing) {
                await existing.unsubscribe();
                console.log('🔄 Unsubscribed stale push subscription');
            }
            
            // Get VAPID public key
            const publicKey = await getVapidPublicKey();
            if (!publicKey) throw new Error('VAPID public key unavailable (not signed in?)');
            const applicationServerKey = urlBase64ToUint8Array(publicKey);
            
            // Subscribe to push service. (`subscription` was undeclared under
            // 'use strict' — a ReferenceError that killed every subscribe.)
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true, // Must be true for Chrome
                applicationServerKey: applicationServerKey
            });
            
            console.log('✅ Subscribed to push service');
            
            // Send subscription to backend
            await sendSubscriptionToBackend(subscription);
            
            return subscription;
            
        } catch (error) {
            console.error('❌ Failed to subscribe to push:', error);
            throw error;
        }
    }
    
    /**
     * Send subscription to backend
     */
    async function sendSubscriptionToBackend(subscription) {
        const subscriptionJson = subscription.toJSON();
        
        // Detect device type
        const deviceType = detectDeviceType();
        
        const payload = {
            subscription: {
                endpoint: subscriptionJson.endpoint,
                keys: {
                    p256dh: subscriptionJson.keys.p256dh,
                    auth: subscriptionJson.keys.auth
                }
            },
            user_agent: navigator.userAgent,
            device_type: deviceType
        };
        
        // Identity rides on X-Session-ID, which js/auth.js's fetch interceptor
        // attaches; there is no Bearer access_token in this auth model.
        try {
            const response = await fetch(`${API_BASE}/subscribe`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            console.log('✅ Subscription saved to backend:', data);
            
            // Store subscription ID (never the string "undefined")
            if (data && data.subscription_id != null) {
                localStorage.setItem('zoe_push_subscription_id', String(data.subscription_id));
            }
            
            return data;
            
        } catch (error) {
            console.error('❌ Failed to save subscription:', error);
            throw error;
        }
    }
    
    /**
     * Unsubscribe from push notifications
     */
    async function unsubscribeFromPush() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            
            if (!subscription) {
                console.log('⚠️ Not subscribed to push notifications');
                return true;
            }
            
            // Unsubscribe from push service
            await subscription.unsubscribe();
            console.log('✅ Unsubscribed from push service');
            
            // Remove from backend (DELETE /api/push/subscribe with endpoint in body)
            const subscriptionJson = subscription.toJSON();
            await fetch(`${API_BASE}/subscribe`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    endpoint: subscriptionJson.endpoint
                })
            });
            
            console.log('✅ Removed subscription from backend');
            
            // Clear stored ID
            localStorage.removeItem('zoe_push_subscription_id');
            
            return true;
            
        } catch (error) {
            console.error('❌ Failed to unsubscribe:', error);
            throw error;
        }
    }
    
    /**
     * Check if user is subscribed
     */
    async function isSubscribed() {
        if (!isPushSupported()) {
            return false;
        }
        
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            return subscription !== null;
        } catch (error) {
            console.error('❌ Error checking subscription:', error);
            return false;
        }
    }
    
    /**
     * Detect device type
     */
    function detectDeviceType() {
        const ua = navigator.userAgent.toLowerCase();
        
        if (/mobile|android|iphone|ipod|blackberry|iemobile|opera mini/i.test(ua)) {
            return 'mobile';
        } else if (/ipad|tablet|kindle|playbook|silk/i.test(ua)) {
            return 'tablet';
        } else {
            return 'desktop';
        }
    }
    
    
    /**
     * Auto-subscribe on page load — prompts if permission not yet decided.
     */
    // Only a signed-in MEMBER can subscribe (the VAPID key is 403 otherwise), and
    // only a signed-in member should ever see the permission prompt.
    function shouldAutoSubscribe(auth, notification) {
        if (!auth || typeof auth.isAuthenticatedNonGuestSession !== 'function') return false;
        if (!auth.isAuthenticatedNonGuestSession()) return false;
        if (notification && notification.permission === 'denied') return false;
        return true;
    }

    async function autoSubscribe() {
        if (!isPushSupported()) {
            return;
        }
        if (!shouldAutoSubscribe(window.zoeAuth, window.Notification)) {
            return;
        }

        // Always attempt subscribe (handles both 'default' and 'granted').
        // subscribeToPush() will request permission if needed.
        try {
            await subscribeToPush();
            console.log('✅ Auto-subscribed to push notifications');
        } catch (error) {
            console.log('⚠️ Auto-subscribe failed (non-critical):', error.message);
        }
    }
    
    /**
     * Initialize push notifications
     */
    function init() {
        // Auto-subscribe if conditions are met
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', autoSubscribe);
        } else {
            autoSubscribe();
        }
        
        console.log('🔔 Push Notifications Handler initialized');
        console.log('   Support:', isPushSupported() ? 'Yes' : 'No');
        console.log('   Permission:', getPermissionStatus());
    }
    
    // Export public API
    window.zoePushNotifications = {
        isSupported: isPushSupported,
        getPermissionStatus: getPermissionStatus,
        requestPermission: requestPermission,
        subscribe: subscribeToPush,
        unsubscribe: unsubscribeFromPush,
        isSubscribed: isSubscribed,
    };
    
    // Initialize
    init();
    
    console.log('🚀 Zoe Push Notifications ready');
    
})();


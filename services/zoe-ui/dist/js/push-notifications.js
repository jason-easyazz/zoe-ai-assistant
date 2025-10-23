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
        
        try {
            const response = await fetch(`${API_BASE}/vapid-public-key`);
            const data = await response.json();
            vapidPublicKey = data.publicKey;
            return vapidPublicKey;
        } catch (error) {
            console.error('❌ Failed to get VAPID public key:', error);
            throw error;
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
            
            // Check if already subscribed
            let subscription = await registration.pushManager.getSubscription();
            
            if (subscription) {
                console.log('✅ Already subscribed to push notifications');
                return subscription;
            }
            
            // Get VAPID public key
            const publicKey = await getVapidPublicKey();
            const applicationServerKey = urlBase64ToUint8Array(publicKey);
            
            // Subscribe to push service
            subscription = await registration.pushManager.subscribe({
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
            endpoint: subscriptionJson.endpoint,
            keys: {
                p256dh: subscriptionJson.keys.p256dh,
                auth: subscriptionJson.keys.auth
            },
            user_agent: navigator.userAgent,
            device_type: deviceType
        };
        
        try {
            const response = await fetch(`${API_BASE}/subscribe`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            console.log('✅ Subscription saved to backend:', data);
            
            // Store subscription ID
            localStorage.setItem('zoe_push_subscription_id', data.subscription_id);
            
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
            
            // Remove from backend
            const subscriptionJson = subscription.toJSON();
            await fetch(`${API_BASE}/unsubscribe`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
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
     * Send test notification
     */
    async function sendTestNotification() {
        try {
            const response = await fetch(`${API_BASE}/test`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    user_id: 'current',
                    title: 'Test Notification 🔔',
                    body: 'If you see this, push notifications are working!',
                    url: '/dashboard.html'
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            console.log('✅ Test notification sent:', data);
            return data;
            
        } catch (error) {
            console.error('❌ Failed to send test notification:', error);
            throw error;
        }
    }
    
    /**
     * Auto-subscribe on page load if permission granted
     */
    async function autoSubscribe() {
        // Only auto-subscribe if:
        // 1. Push is supported
        // 2. Permission is already granted
        // 3. Not already subscribed
        
        if (!isPushSupported()) {
            return;
        }
        
        if (Notification.permission !== 'granted') {
            return;
        }
        
        const subscribed = await isSubscribed();
        if (subscribed) {
            console.log('✅ Already subscribed to push notifications');
            return;
        }
        
        // Auto-subscribe
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
        sendTest: sendTestNotification
    };
    
    // Initialize
    init();
    
    console.log('🚀 Zoe Push Notifications ready');
    
})();


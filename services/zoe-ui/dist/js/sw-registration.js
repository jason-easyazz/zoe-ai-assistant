/**
 * Zoe PWA Service Worker Registration
 * Handles service worker registration, updates, and installation prompts
 */

(function() {
    'use strict';
    
    const SW_PATH = '/sw.js';
    let deferredPrompt = null;
    let swRegistration = null;
    
    // Check if service workers are supported
    if ('serviceWorker' in navigator) {
        // Register service worker when page loads
        window.addEventListener('load', () => {
            registerServiceWorker();
        });
    } else {
        console.warn('⚠️ Service Workers not supported in this browser');
    }
    
    /**
     * Register the service worker
     */
    async function registerServiceWorker() {
        try {
            const registration = await navigator.serviceWorker.register(SW_PATH, {
                scope: '/'
            });
            
            swRegistration = registration;
            console.log('✅ Service Worker registered successfully');
            console.log('   Scope:', registration.scope);
            
            // Check for updates immediately on page load
            registration.update();
            
            // Check for updates
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;
                console.log('🔄 Service Worker update found');
                
                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        // New service worker installed, show update prompt
                        showUpdateNotification();
                    } else if (newWorker.state === 'activated') {
                        // New service worker activated, reload to use it
                        console.log('🔄 New service worker activated, reloading...');
                        window.location.reload();
                    }
                });
            });
            
            // Check for updates periodically (every hour)
            setInterval(() => {
                registration.update();
            }, 60 * 60 * 1000);
            
            // Request notification permission if not granted
            requestNotificationPermission();
            
        } catch (error) {
            console.error('❌ Service Worker registration failed:', error);
        }
    }
    
    /**
     * Show notification when update is available
     */
    function showUpdateNotification() {
        // Create update banner
        const banner = document.createElement('div');
        banner.id = 'sw-update-banner';
        banner.style.cssText = `
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            padding: 16px 24px;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(123, 97, 255, 0.4);
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 16px;
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            font-size: 14px;
            max-width: 90%;
            animation: slideUp 0.3s ease;
        `;
        
        banner.innerHTML = `
            <div style="flex: 1;">
                <strong>Update Available!</strong>
                <div style="font-size: 12px; opacity: 0.9; margin-top: 4px;">
                    A new version of Zoe is ready
                </div>
            </div>
            <button id="sw-update-btn" style="
                background: rgba(255, 255, 255, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.3);
                color: white;
                padding: 8px 16px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
                font-size: 13px;
                white-space: nowrap;
            ">
                Reload
            </button>
            <button id="sw-dismiss-btn" style="
                background: transparent;
                border: none;
                color: white;
                cursor: pointer;
                font-size: 20px;
                padding: 0;
                width: 24px;
                height: 24px;
                line-height: 24px;
            ">
                ×
            </button>
        `;
        
        // Add animation
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideUp {
                from { transform: translateX(-50%) translateY(100px); opacity: 0; }
                to { transform: translateX(-50%) translateY(0); opacity: 1; }
            }
        `;
        document.head.appendChild(style);
        
        document.body.appendChild(banner);
        
        // Handle reload button
        document.getElementById('sw-update-btn').addEventListener('click', () => {
            // Tell the new service worker to skip waiting
            if (swRegistration && swRegistration.waiting) {
                swRegistration.waiting.postMessage({ type: 'SKIP_WAITING' });
            }
            // Reload the page
            window.location.reload();
        });
        
        // Handle dismiss button
        document.getElementById('sw-dismiss-btn').addEventListener('click', () => {
            banner.remove();
        });
    }
    
    /**
     * Request notification permission
     */
    async function requestNotificationPermission() {
        if (!('Notification' in window)) {
            console.log('⚠️ Notifications not supported');
            return;
        }
        
        if (Notification.permission === 'default') {
            // Don't ask immediately, let user explore first
            // Will be prompted when they enable notifications in settings
            console.log('📬 Notification permission not yet requested');
        } else if (Notification.permission === 'granted') {
            console.log('✅ Notification permission granted');
            // Subscribe to push notifications (Phase 2)
        } else {
            console.log('❌ Notification permission denied');
        }
    }
    
    // ===== INSTALL PROMPT HANDLING =====
    
    // Capture the install prompt event
    window.addEventListener('beforeinstallprompt', (e) => {
        console.log('📱 Install prompt available');
        
        // Prevent the default prompt
        e.preventDefault();
        
        // Store the event for later use
        deferredPrompt = e;
        
        // Show custom install button/banner
        showInstallPrompt();
        
        // Emit custom event that pages can listen to
        window.dispatchEvent(new CustomEvent('zoe-installable', { 
            detail: { canInstall: true } 
        }));
    });
    
    /**
     * Show install prompt banner
     */
    function showInstallPrompt() {
        // Check if user has dismissed the prompt before
        const dismissedDate = localStorage.getItem('zoe-install-dismissed');
        if (dismissedDate) {
            const daysSinceDismissed = (Date.now() - parseInt(dismissedDate)) / (1000 * 60 * 60 * 24);
            if (daysSinceDismissed < 7) {
                console.log('Install prompt recently dismissed, waiting...');
                return;
            }
        }
        
        // Check if this is user's first visit or second+ visit
        let visitCount = parseInt(localStorage.getItem('zoe-visit-count') || '0');
        visitCount++;
        localStorage.setItem('zoe-visit-count', visitCount.toString());
        
        if (visitCount < 2) {
            console.log('First visit, not showing install prompt yet');
            return;
        }
        
        // Create install banner
        const banner = document.createElement('div');
        banner.id = 'install-banner';
        banner.style.cssText = `
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            padding: 16px 24px;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(123, 97, 255, 0.4);
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 16px;
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            font-size: 14px;
            max-width: 90%;
            animation: slideUp 0.3s ease 2s both;
        `;
        
        banner.innerHTML = `
            <div style="flex: 1;">
                <strong>Install Zoe</strong>
                <div style="font-size: 12px; opacity: 0.9; margin-top: 4px;">
                    Add to home screen for quick access
                </div>
            </div>
            <button id="install-btn" style="
                background: rgba(255, 255, 255, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.3);
                color: white;
                padding: 8px 16px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
                font-size: 13px;
                white-space: nowrap;
            ">
                Install
            </button>
            <button id="install-dismiss-btn" style="
                background: transparent;
                border: none;
                color: white;
                cursor: pointer;
                font-size: 20px;
                padding: 0;
                width: 24px;
                height: 24px;
                line-height: 24px;
            ">
                ×
            </button>
        `;
        
        document.body.appendChild(banner);
        
        // Handle install button
        document.getElementById('install-btn').addEventListener('click', async () => {
            if (!deferredPrompt) {
                console.log('No install prompt available');
                return;
            }
            
            // Show the install prompt
            deferredPrompt.prompt();
            
            // Wait for the user's response
            const { outcome } = await deferredPrompt.userChoice;
            console.log(`Install prompt outcome: ${outcome}`);
            
            if (outcome === 'accepted') {
                console.log('✅ User accepted the install');
            } else {
                console.log('❌ User dismissed the install');
            }
            
            // Clear the deferred prompt
            deferredPrompt = null;
            
            // Remove the banner
            banner.remove();
        });
        
        // Handle dismiss button
        document.getElementById('install-dismiss-btn').addEventListener('click', () => {
            localStorage.setItem('zoe-install-dismissed', Date.now().toString());
            banner.remove();
        });
    }
    
    /**
     * Programmatically trigger install prompt
     * Can be called from a button in settings
     */
    window.zoeInstallApp = async function() {
        if (!deferredPrompt) {
            alert('App is already installed or install prompt is not available.\n\nOn iOS: Tap Share → Add to Home Screen\nOn Android: The app may already be installed.');
            return false;
        }
        
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        console.log(`Manual install outcome: ${outcome}`);
        deferredPrompt = null;
        return outcome === 'accepted';
    };
    
    /**
     * Check if app is installed
     */
    window.zoeIsInstalled = function() {
        return window.matchMedia('(display-mode: standalone)').matches || 
               window.navigator.standalone === true;
    };
    
    // Log installation status
    if (window.zoeIsInstalled()) {
        console.log('✅ Zoe is installed as PWA');
    } else {
        console.log('📱 Zoe running in browser');
    }
    
    // Handle app installed event
    window.addEventListener('appinstalled', () => {
        console.log('🎉 Zoe has been installed!');
        deferredPrompt = null;
        
        // Show welcome message
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('Welcome to Zoe! 🎉', {
                body: 'Zoe is now installed on your device',
                icon: '/icons/icon-192.png'
            });
        }
        
        // Track installation (analytics)
        if (typeof gtag !== 'undefined') {
            gtag('event', 'pwa_installed', {
                event_category: 'engagement',
                event_label: 'PWA Installation'
            });
        }
    });
    
    console.log('🚀 Zoe PWA Registration Script loaded');
    
})();

// Load push notifications handler
(function() {
    const script = document.createElement('script');
    script.src = '/js/push-notifications.js';
    script.defer = true;
    document.head.appendChild(script);
})();


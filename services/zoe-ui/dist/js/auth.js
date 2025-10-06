/**
 * Zoe Authentication System
 * Unified authentication module for all pages
 */

class ZoeAuth {
    constructor() {
        // Use same origin (proxy) to avoid any protocol issues
        this.apiBase = '';  // Same origin - proxy will handle auth requests
        this.currentUser = null;
        this.currentSession = null;
        this.isInitialized = false;
        
        // Bind methods to maintain context
        this.logout = this.logout.bind(this);
        this.returnToWelcome = this.returnToWelcome.bind(this);
    }

    /**
     * Initialize authentication for the current page
     */
    async initialize() {
        if (this.isInitialized) return;
        
        // Skip auth check for welcome and auth pages
        const currentPage = window.location.pathname.split('/').pop();
        if (currentPage === 'index.html' || currentPage === 'auth.html' || currentPage === '') {
            this.isInitialized = true;
            return;
        }

        // Check for redirect loops
        const loopDetected = localStorage.getItem('redirect_loop_detected');
        if (loopDetected) {
            console.warn('Redirect loop detected, staying on current page');
            localStorage.removeItem('zoe_session');
            localStorage.removeItem('redirect_loop_detected');
            this.isInitialized = true;
            return;
        }

        await this.checkSession();
        this.setupEventListeners();
        this.isInitialized = true;
    }

    /**
     * Check if user has valid session
     */
    async checkSession() {
        const stored = localStorage.getItem('zoe_session');
        if (!stored) {
            this.redirectToWelcome();
            return false;
        }

        try {
            this.currentSession = JSON.parse(stored);
            
            // Validate session with server if online
            const isValid = await this.validateSession();
            if (isValid) {
                await this.loadCurrentUser();
                this.updateUserInterface();
                return true;
            } else {
                this.redirectToWelcome();
                return false;
            }
        } catch (error) {
            console.error('Session validation error:', error);
            this.redirectToWelcome();
            return false;
        }
    }

    /**
     * Validate session with server
     */
    async validateSession() {
        if (!this.currentSession) return false;

        try {
            // Use the existing /api/auth/user endpoint to validate session
            const response = await fetch(`${this.apiBase}/api/auth/user`, {
                headers: {
                    'X-Session-ID': this.currentSession.session_id
                }
            });

            if (response.ok) {
                return true;
            } else if (response.status === 401) {
                // Session is invalid, clear it
                localStorage.removeItem('zoe_session');
                return false;
            } else {
                // Server error, fallback to local validation
                return this.isSessionValidLocally();
            }
        } catch (error) {
            // Network error or server offline, check session locally
            console.warn('Auth service offline, using cached session:', error.message);
            return this.isSessionValidLocally();
        }
    }

    /**
     * Check if session is valid locally (offline mode)
     */
    isSessionValidLocally() {
        if (!this.currentSession) return false;
        
        // Check if session has expired
        if (this.currentSession.expires_at) {
            const expiresAt = new Date(this.currentSession.expires_at);
            const now = new Date();
            return now < expiresAt;
        }
        
        // Fallback: allow cached session for limited time
        const sessionAge = Date.now() - new Date(this.currentSession.created_at || Date.now()).getTime();
        const maxOfflineAge = 24 * 60 * 60 * 1000; // 24 hours
        
        return sessionAge < maxOfflineAge;
    }

    /**
     * Load current user information
     */
    async loadCurrentUser() {
        if (!this.currentSession) return;

        try {
            const response = await fetch(`${this.apiBase}/api/auth/user`, {
                headers: {
                    'X-Session-ID': this.currentSession.session_id
                }
            });

            if (response.ok) {
                this.currentUser = await response.json();
            } else {
                // Use cached user data from session
                this.currentUser = {
                    user_id: this.currentSession.user_id,
                    username: this.currentSession.username,
                    role: this.currentSession.role || 'user'
                };
            }
        } catch (error) {
            console.warn('Could not load user from server, using cached data');
            this.currentUser = {
                user_id: this.currentSession.user_id,
                username: this.currentSession.username,
                role: this.currentSession.role || 'user'
            };
        }
    }

    /**
     * Update user interface elements
     */
    updateUserInterface() {
        if (!this.currentUser) return;

        // Update mini-orb in navigation
        const miniOrb = document.querySelector('.mini-orb');
        if (miniOrb) {
            miniOrb.onclick = this.returnToWelcome;
            miniOrb.title = `${this.currentUser.username} - Click to logout`;
            
            // Add breathing animation to mini-orb
            if (!miniOrb.style.animation) {
                miniOrb.style.animation = 'mini-breathe 3s ease-in-out infinite';
            }
        }

        // Update user menu if it exists
        this.updateUserMenu();

        // Update any user-specific content
        this.updateUserContent();
    }

    /**
     * Update user menu dropdown
     */
    updateUserMenu() {
        const userAvatar = document.getElementById('userAvatar');
        const username = document.getElementById('username');
        const userRole = document.getElementById('userRole');
        const sessionBadge = document.getElementById('sessionBadge');

        if (userAvatar) {
            userAvatar.textContent = this.currentUser.username.charAt(0).toUpperCase();
        }

        if (username) {
            username.textContent = this.currentUser.username;
        }

        if (userRole) {
            userRole.textContent = this.currentUser.role || 'user';
        }

        if (sessionBadge) {
            if (this.currentSession?.session_type === 'passcode') {
                sessionBadge.textContent = 'PIN';
                sessionBadge.className = 'session-badge session-passcode';
                
                // Show upgrade option
                const upgradeItem = document.getElementById('upgradeSessionItem');
                if (upgradeItem) upgradeItem.style.display = 'block';
            } else {
                sessionBadge.textContent = 'Full';
                sessionBadge.className = 'session-badge session-standard';
                
                const upgradeItem = document.getElementById('upgradeSessionItem');
                if (upgradeItem) upgradeItem.style.display = 'none';
            }
        }

        // Show admin features for admin users
        if (this.currentUser.role === 'admin') {
            const adminLink = document.getElementById('adminLink');
            const adminDivider = document.getElementById('adminDivider');
            if (adminLink) adminLink.style.display = 'block';
            if (adminDivider) adminDivider.style.display = 'block';
        }
    }

    /**
     * Update user-specific content
     */
    updateUserContent() {
        // Update page title with user name
        const pageTitle = document.title;
        if (!pageTitle.includes(this.currentUser.username)) {
            document.title = `${pageTitle} - ${this.currentUser.username}`;
        }

        // Update any welcome messages
        const welcomeElements = document.querySelectorAll('[data-user-name]');
        welcomeElements.forEach(el => {
            el.textContent = el.textContent.replace('{username}', this.currentUser.username);
        });
    }

    /**
     * Setup event listeners
     */
    setupEventListeners() {
        // Close user dropdown when clicking outside
        document.addEventListener('click', (event) => {
            const userMenu = document.querySelector('.user-menu');
            const dropdown = document.getElementById('userDropdown');
            
            if (userMenu && dropdown && !userMenu.contains(event.target)) {
                dropdown.classList.remove('show');
            }
        });

        // Add keyboard shortcuts
        document.addEventListener('keydown', (event) => {
            // Ctrl+Shift+L for logout
            if (event.ctrlKey && event.shiftKey && event.key === 'L') {
                event.preventDefault();
                this.logout();
            }
        });
    }

    /**
     * Return to welcome page and logout
     */
    returnToWelcome() {
        if (confirm('Return to welcome screen and logout?')) {
            this.logout();
        }
    }

    /**
     * Logout user
     */
    async logout() {
        try {
            // Notify server of logout
            if (this.currentSession) {
                await fetch(`${this.apiBase}/api/auth/logout`, {
                    method: 'POST',
                    headers: {
                        'X-Session-ID': this.currentSession.session_id
                    }
                }).catch(() => {}); // Ignore errors for logout
            }
        } finally {
            // Always clear local data
            localStorage.removeItem('zoe_session');
            sessionStorage.clear();
            
            // Clear current state
            this.currentUser = null;
            this.currentSession = null;
            
            // Redirect to welcome
            this.redirectToWelcome();
        }
    }

    /**
     * Switch to different user
     */
    switchUser() {
        localStorage.removeItem('zoe_session');
        this.redirectToWelcome();
    }

    /**
     * Upgrade current session
     */
    async upgradeSession() {
        const password = prompt('Enter your password to upgrade this session:');
        if (!password) return false;

        try {
            const response = await fetch(`${this.apiBase}/api/auth/escalate`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Session-ID': this.currentSession.session_id
                },
                body: JSON.stringify({ password })
            });

            if (response.ok) {
                const data = await response.json();
                this.currentSession = data;
                localStorage.setItem('zoe_session', JSON.stringify(data));
                this.updateUserInterface();
                this.showNotification('Session upgraded successfully!', 'success');
                return true;
            } else {
                const error = await response.json();
                alert('Session upgrade failed: ' + (error.detail || 'Invalid password'));
                return false;
            }
        } catch (error) {
            console.error('Upgrade error:', error);
            alert('Session upgrade failed');
            return false;
        }
    }

    /**
     * Show notification
     */
    showNotification(message, type = 'info') {
        // Try to use existing notification system
        if (window.showNotification) {
            window.showNotification(message, type);
            return;
        }

        // Fallback notification
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            color: white;
            font-weight: 500;
            z-index: 10000;
            transition: all 0.3s ease;
            max-width: 300px;
        `;

        switch (type) {
            case 'success':
                notification.style.background = '#22c55e';
                break;
            case 'error':
                notification.style.background = '#ef4444';
                break;
            case 'warning':
                notification.style.background = '#f59e0b';
                break;
            default:
                notification.style.background = '#3b82f6';
        }

        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => {
            notification.style.transform = 'translateX(100%)';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    /**
     * Redirect to welcome page
     */
    redirectToWelcome() {
        const currentPath = window.location.pathname;
        const currentPage = currentPath.split('/').pop();
        
        // Prevent redirect loops
        if (currentPage === 'index.html' || currentPath === '/' || currentPath === '') {
            return;
        }
        
        // Clear session data and redirect
        localStorage.removeItem('zoe_session');
        localStorage.removeItem('redirect_loop_detected');
        window.location.href = 'index.html';
    }

    /**
     * Get current user
     */
    getCurrentUser() {
        return this.currentUser;
    }

    /**
     * Get current session
     */
    getCurrentSession() {
        return this.currentSession;
    }

    /**
     * Check if user has permission
     */
    hasPermission(permission) {
        if (!this.currentUser) return false;
        
        // Admin has all permissions
        if (this.currentUser.role === 'admin') return true;
        
        // TODO: Implement proper permission checking with RBAC
        return true; // For now, allow all authenticated users
    }

    /**
     * Require authentication for current page
     */
    requireAuth() {
        if (!this.currentUser || !this.currentSession) {
            this.redirectToWelcome();
            return false;
        }
        return true;
    }
}

// Global authentication instance
window.zoeAuth = new ZoeAuth();

// Global helper functions
window.toggleUserDropdown = function() {
    const dropdown = document.getElementById('userDropdown');
    if (dropdown) {
        dropdown.classList.toggle('show');
    }
};

window.showUserProfile = function() {
    // Navigate to user settings
    window.location.href = 'settings.html#profile';
    document.getElementById('userDropdown').classList.remove('show');
};

window.showSecuritySettings = function() {
    window.location.href = 'settings.html#security';
    document.getElementById('userDropdown').classList.remove('show');
};

window.switchUser = function() {
    window.zoeAuth.switchUser();
};

window.upgradeSession = function() {
    window.zoeAuth.upgradeSession();
    document.getElementById('userDropdown').classList.remove('show');
};

window.logout = function() {
    window.zoeAuth.logout();
};

window.handleLogout = function() {
    if (window.zoeAuth) {
        window.zoeAuth.logout();
    } else {
        // Fallback if auth not initialized
        localStorage.removeItem('zoe_session');
        localStorage.removeItem('zoe_user_profiles');
        window.location.href = 'index.html';
    }
};

// Auto-initialize on DOM load
document.addEventListener('DOMContentLoaded', function() {
    window.zoeAuth.initialize();
});

// Add mini-orb breathing animation styles if not present
if (!document.querySelector('#auth-styles')) {
    const style = document.createElement('style');
    style.id = 'auth-styles';
    style.textContent = `
        @keyframes mini-breathe {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.05); opacity: 0.9; }
        }
        
        .mini-orb {
            position: relative;
            overflow: hidden;
        }
        
        .mini-orb::after {
            content: '';
            position: absolute;
            top: 2px;
            left: 2px;
            right: 2px;
            bottom: 2px;
            border-radius: 50%;
            background: radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.3) 0%, transparent 60%);
            pointer-events: none;
        }
    `;
    document.head.appendChild(style);
}

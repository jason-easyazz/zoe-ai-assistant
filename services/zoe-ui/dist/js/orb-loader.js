/**
 * Universal Zoe Orb loader - include this script on any page to get the floating orb.
 * Handles loading the orb JS, HTML component, and initialization.
 * Preserves conversation context across page navigation via localStorage.
 */
(function() {
    'use strict';

    // Chat page is its own full-screen voice surface — skip the floating orb popup
    if (window.location.pathname.includes('/touch/chat.html')) return;

    if (document.getElementById('zoeOrbContainer')) return;

    var orbScript = document.createElement('script');
    orbScript.src = '/js/zoe-orb.js';
    orbScript.onload = function() {
        fetch('/components/zoe-orb-complete.html')
            .then(function(r) { return r.text(); })
            .then(function(html) {
                var div = document.createElement('div');
                div.id = 'zoeOrbContainer';
                div.innerHTML = html;
                // Remove script tags (JS already loaded separately)
                div.querySelectorAll('script').forEach(function(s) { s.remove(); });
                // Move style tags to <head> so they apply reliably
                div.querySelectorAll('style').forEach(function(s) {
                    s.remove();
                    document.head.appendChild(s);
                });
                while (div.firstChild) {
                    document.body.appendChild(div.firstChild);
                }
                // Explicitly fix orb position in case CSS hasn't applied yet
                var orbEl = document.getElementById('zoeOrb');
                if (orbEl) {
                    orbEl.style.setProperty('position', 'fixed', 'important');
                    orbEl.style.setProperty('bottom', '24px', 'important');
                    orbEl.style.setProperty('right', '24px', 'important');
                    orbEl.style.setProperty('z-index', '1200', 'important');
                }
                if (typeof initOrbChat === 'function') {
                    initOrbChat();
                }
                _restoreOrbState();
            })
            .catch(function(err) {
                console.error('Failed to load Zoe orb:', err);
            });
    };
    document.head.appendChild(orbScript);

    function _restoreOrbState() {
        try {
            var saved = localStorage.getItem('orbChatMessages');
            if (!saved) return;
            var msgs = JSON.parse(saved);
            var container = document.getElementById('orbChatMessages');
            if (!container || !msgs.length) return;
            var welcome = container.querySelector('.orb-chat-message.assistant');
            if (welcome) welcome.remove();
            msgs.forEach(function(m) {
                if (typeof addOrbMessage === 'function') {
                    addOrbMessage(m.text, m.sender);
                }
            });
        } catch (e) { /* ignore */ }
    }

    window.addEventListener('beforeunload', function() {
        try {
            var container = document.getElementById('orbChatMessages');
            if (!container) return;
            var messages = [];
            container.querySelectorAll('.orb-chat-message').forEach(function(el) {
                var sender = el.classList.contains('user') ? 'user' : 'assistant';
                messages.push({ text: el.textContent.trim(), sender: sender });
            });
            if (messages.length > 1) {
                var recent = messages.slice(-20);
                localStorage.setItem('orbChatMessages', JSON.stringify(recent));
            }
        } catch (e) { /* ignore */ }
    });
})();

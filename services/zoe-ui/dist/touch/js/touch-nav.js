/**
 * Touch Navigation — Edge-swipe between pages.
 * Swipe navigation ONLY fires when the gesture starts within 40px of the
 * left or right screen edge, preventing GridStack drag conflicts.
 *
 * Page order (swipe right-edge = previous, swipe left-edge = next):
 * Dashboard → Calendar → Lists → Notes → Journal → Music →
 * Smart Home → People → Memories → Cooking → Weather → Settings
 */
'use strict';

(function () {
    const PAGE_ORDER = [
        '/touch/dashboard.html',
        '/touch/calendar.html',
        '/touch/lists.html',
        '/touch/notes.html',
        '/touch/journal.html',
        '/touch/music.html',
        '/touch/smart-home.html',
        '/touch/people.html',
        '/touch/memories.html',
        '/touch/cooking.html',
        '/touch/weather.html',
        '/touch/settings.html',
    ];

    // Edge zone width (px from left or right edge) where swipes are recognised
    const EDGE_ZONE   = 40;
    const THRESHOLD_X = 60;  // minimum horizontal px to count as swipe
    const MAX_TIME    = 450; // ms
    const MAX_VERT    = 50;  // max vertical drift to stay horizontal

    let startX = 0, startY = 0, startTime = 0, fromEdge = false;

    function currentPageIndex() {
        const path = window.location.pathname;
        return PAGE_ORDER.findIndex(p => path.includes(p.replace('/touch/', '')));
    }

    function navigateTo(path, direction) {
        const el = document.body;
        const dx = direction === 'left' ? '-100%' : '100%';
        el.style.transition = 'transform 0.28s cubic-bezier(0.4,0,0.2,1), opacity 0.28s';
        el.style.transform  = `translateX(${dx})`;
        el.style.opacity    = '0';
        setTimeout(() => { window.location.href = path; }, 270);
    }

    function init() {
        const idx = currentPageIndex();
        if (idx === -1) return;

        const prevPage = PAGE_ORDER[idx - 1] || null;
        const nextPage = PAGE_ORDER[idx + 1] || null;

        // Touch-only (no mouse) — avoids GridStack drag conflicts
        document.addEventListener('touchstart', (e) => {
            const t = e.touches[0];
            startX    = t.clientX;
            startY    = t.clientY;
            startTime = Date.now();
            // Only consider edge zones
            fromEdge  = startX < EDGE_ZONE || startX > (window.innerWidth - EDGE_ZONE);
        }, { passive: true });

        document.addEventListener('touchend', (e) => {
            if (!fromEdge) return;
            const t    = e.changedTouches[0];
            const dx   = t.clientX - startX;
            const adx  = Math.abs(dx);
            const ady  = Math.abs(t.clientY - startY);
            const dt   = Date.now() - startTime;
            if (dt > MAX_TIME || adx < THRESHOLD_X || ady > MAX_VERT) return;

            if (dx < 0 && nextPage) navigateTo(nextPage, 'left');
            if (dx > 0 && prevPage) navigateTo(prevPage, 'right');
        }, { passive: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

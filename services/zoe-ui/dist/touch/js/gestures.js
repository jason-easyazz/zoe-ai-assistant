"use strict";

// Gesture handling: swipe (L/R/U/D), tap, long-press

const TouchGestures = (() => {
  const DEFAULTS = {
    swipeThreshold: 60, // px
    swipeTime: 500, // ms
    tapMaxDistance: 10, // px
    longPressTime: 500, // ms
  };

  function addGestureListeners(target = document, opts = {}) {
    const cfg = { ...DEFAULTS, ...opts };
    let startX = 0;
    let startY = 0;
    let startTime = 0;
    let moved = false;
    let longPressTimer = null;

    function clearLongPress() { if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; } }

    function onTouchStart(ev) {
      const t = ev.touches ? ev.touches[0] : ev;
      startX = t.clientX; startY = t.clientY; startTime = Date.now(); moved = false;
      clearLongPress();
      longPressTimer = setTimeout(() => {
        target.dispatchEvent(new CustomEvent("gesture:longpress", { detail: { x: startX, y: startY, event: ev } }));
      }, cfg.longPressTime);
    }

    function onTouchMove(ev) {
      const t = ev.touches ? ev.touches[0] : ev;
      const dx = t.clientX - startX; const dy = t.clientY - startY;
      if (Math.hypot(dx, dy) > cfg.tapMaxDistance) moved = true;
    }

    function onTouchEnd(ev) {
      clearLongPress();
      const t = ev.changedTouches ? ev.changedTouches[0] : ev;
      const dx = t.clientX - startX; const dy = t.clientY - startY;
      const adx = Math.abs(dx); const ady = Math.abs(dy);
      const dt = Date.now() - startTime;

      if (!moved && adx < cfg.tapMaxDistance && ady < cfg.tapMaxDistance && dt < cfg.longPressTime) {
        target.dispatchEvent(new CustomEvent("gesture:tap", { detail: { x: t.clientX, y: t.clientY, event: ev } }));
        return;
      }

      if (dt <= cfg.swipeTime) {
        if (adx >= cfg.swipeThreshold && adx > ady) {
          target.dispatchEvent(new CustomEvent(dx > 0 ? "gesture:swiperight" : "gesture:swipeleft", { detail: { dx, dy, dt, event: ev } }));
          return;
        }
        if (ady >= cfg.swipeThreshold && ady > adx) {
          target.dispatchEvent(new CustomEvent(dy > 0 ? "gesture:swipedown" : "gesture:swipeup", { detail: { dx, dy, dt, event: ev } }));
          return;
        }
      }
    }

    target.addEventListener("touchstart", onTouchStart, { passive: true });
    target.addEventListener("touchmove", onTouchMove, { passive: true });
    target.addEventListener("touchend", onTouchEnd, { passive: true });
    // Mouse support (useful on desktop for testing)
    target.addEventListener("mousedown", onTouchStart);
    target.addEventListener("mousemove", onTouchMove);
    target.addEventListener("mouseup", onTouchEnd);

    return () => {
      clearLongPress();
      target.removeEventListener("touchstart", onTouchStart);
      target.removeEventListener("touchmove", onTouchMove);
      target.removeEventListener("touchend", onTouchEnd);
      target.removeEventListener("mousedown", onTouchStart);
      target.removeEventListener("mousemove", onTouchMove);
      target.removeEventListener("mouseup", onTouchEnd);
    };
  }

  return { addGestureListeners };
})();

window.TouchGestures = TouchGestures;



/*
 * Zoe phase-2 firmware: the physical pairing gesture, as a pure state machine (host-tested in host-test/).
 *
 * Gesture: TWO double-taps within ZOE_GESTURE_WINDOW_MS (four quick taps). A single double-tap does nothing, so a pocket
 * bump does not open pairing. The stock 3 s hold (power off) and the single tap are untouched.
 */
#ifndef ZOE_GESTURE_H
#define ZOE_GESTURE_H

#include <stdbool.h>
#include <stdint.h>

#define ZOE_GESTURE_WINDOW_MS 2500

struct zoe_gesture {
    bool armed;
    int64_t first_ms;
};

/* Feed one DOUBLE_TAP event at monotonic time now_ms. Returns true exactly when the gesture completes. */
static inline bool zoe_gesture_on_double_tap(struct zoe_gesture *g, int64_t now_ms)
{
    if (g->armed && now_ms >= g->first_ms && (now_ms - g->first_ms) <= ZOE_GESTURE_WINDOW_MS) {
        g->armed = false;
        return true;
    }
    g->armed = true;
    g->first_ms = now_ms;
    return false;
}

/* Pairing-window bookkeeping, also pure: the window is open while now_ms < until_ms. */
static inline bool zoe_window_open(int64_t until_ms, int64_t now_ms)
{
    return until_ms > 0 && now_ms < until_ms;
}

#endif

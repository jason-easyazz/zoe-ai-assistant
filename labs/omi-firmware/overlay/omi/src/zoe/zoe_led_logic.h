/*
 * Zoe phase-2 firmware: what the LED shows, as pure functions (host-tested in host-test/). Owner design 2026-10-19.
 *
 * Priority (highest first) when states overlap:
 *   OFF > FAULT > PAIRING > WIPE > PAIRED > CHARGING/CHARGED > WARNING > SYNCING > NORMAL
 *
 * | state     | when                                                        | colour / pattern (duty %)                       |
 * |-----------|-------------------------------------------------------------|-------------------------------------------------|
 * | OFF       | powered off (3 s hold)                                      | all off                                         |
 * | FAULT     | stock-bond wipe failed, crypto/flash fail-closed, a pairing | RED fast blink, 100 ms on / 100 ms off, 40 %    |
 * |           | refused while the window was open (plus the 2 s buzz)       |                                                 |
 * | PAIRING   | pairing window open (after the four-tap gesture)            | cycle BLUE > GREEN > PURPLE > YELLOW, 250 ms     |
 * |           |                                                             | each, 30 %                                      |
 * | WIPE      | crypto-wipe in progress                                     | WHITE quick flashes, 80 ms on / 120 ms off, 30 % |
 * | PAIRED    | just bonded                                                 | three GREEN blinks (200 / 200 ms), then normal  |
 * | CHARGING  | on the charger, not full                                    | GREEN breathing, 3 s period, 0-40 %             |
 * | CHARGED   | on the charger, >= BATTERY_FULL (98 %)                      | GREEN solid, 20 %                               |
 * | WARNING   | storage >= 90 % full, battery < 15 % (off the charger),     | RED slow blink, 500 ms on / 1500 ms off, 20 %   |
 * |           | or the clock is not set                                     |                                                 |
 * | SYNCING   | an offline-storage transfer to the Pi is running            | BLUE blink, 500 / 500 ms, 15 %                  |
 * | NORMAL    | recording / idle                                            | BLUE (or WHITE, a setting) steady, 15 %         |
 *
 * The 15 % normal duty: LED current scales with PWM duty, so 15 % draws 15 % of that channel's full-on current, against
 * stock's 50 % default dim ratio: about 70 % less LED current in the state the pendant spends almost all its time in.
 */
#ifndef ZOE_LED_LOGIC_H
#define ZOE_LED_LOGIC_H

#include <stdbool.h>
#include <stdint.h>

enum zoe_led_state {
    ZL_NORMAL = 0,
    ZL_SYNCING,
    ZL_WARNING,
    ZL_CHARGING,
    ZL_CHARGED,
    ZL_PAIRED,
    ZL_WIPE,
    ZL_PAIRING,
    ZL_FAULT,
    ZL_OFF,
};

#define ZL_NORMAL_DUTY 15U
#define ZL_LOW_BATTERY_PCT 15U
#define ZL_STORAGE_WARN_PCT 90U
#define ZL_BATTERY_FULL_PCT 98U /* stock's BATTERY_FULL_THRESHOLD_PERCENT */

struct zoe_led_inputs {
    bool off;
    bool fault;
    bool pairing_window;
    bool wiping;
    bool paired_celebration;
    bool charging;
    uint8_t battery_pct;   /* 0 = not read yet (never a low-battery warning) */
    uint8_t storage_pct;
    bool clock_set;
    bool syncing;
};

struct zoe_rgb {
    uint8_t r, g, b;       /* PWM duty, percent */
};

static inline enum zoe_led_state zoe_led_pick(const struct zoe_led_inputs *in)
{
    if (in->off) return ZL_OFF;
    if (in->fault) return ZL_FAULT;
    if (in->pairing_window) return ZL_PAIRING;
    if (in->wiping) return ZL_WIPE;
    if (in->paired_celebration) return ZL_PAIRED;
    if (in->charging) return in->battery_pct >= ZL_BATTERY_FULL_PCT ? ZL_CHARGED : ZL_CHARGING;
    if (!in->clock_set || in->storage_pct >= ZL_STORAGE_WARN_PCT ||
        (in->battery_pct > 0U && in->battery_pct < ZL_LOW_BATTERY_PCT)) {
        return ZL_WARNING;
    }
    if (in->syncing) return ZL_SYNCING;
    return ZL_NORMAL;
}

static inline struct zoe_rgb zl_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    struct zoe_rgb c = {r, g, b};
    return c;
}

/* The colour at time t_ms (any monotonic ms) for a state. normal_white: the persisted NORMAL colour setting. */
static inline struct zoe_rgb zoe_led_frame(enum zoe_led_state s, uint32_t t_ms, bool normal_white)
{
    switch (s) {
    case ZL_OFF:
        return zl_rgb(0, 0, 0);
    case ZL_FAULT:
        return (t_ms % 200U) < 100U ? zl_rgb(40, 0, 0) : zl_rgb(0, 0, 0);
    case ZL_PAIRING: {
        static const uint8_t cyc[4][3] = {{0, 0, 30}, {0, 30, 0}, {30, 0, 30}, {30, 30, 0}};
        const uint8_t *c = cyc[(t_ms / 250U) % 4U];
        return zl_rgb(c[0], c[1], c[2]);
    }
    case ZL_WIPE:
        return (t_ms % 200U) < 80U ? zl_rgb(30, 30, 30) : zl_rgb(0, 0, 0);
    case ZL_PAIRED:
        return (t_ms % 400U) < 200U ? zl_rgb(0, 30, 0) : zl_rgb(0, 0, 0);
    case ZL_CHARGING: {
        uint32_t p = t_ms % 3000U; /* triangle 0 -> 40 -> 0 */
        uint32_t lvl = p < 1500U ? (p * 40U) / 1500U : ((3000U - p) * 40U) / 1500U;
        return zl_rgb(0, (uint8_t) lvl, 0);
    }
    case ZL_CHARGED:
        return zl_rgb(0, 20, 0);
    case ZL_WARNING:
        return (t_ms % 2000U) < 500U ? zl_rgb(20, 0, 0) : zl_rgb(0, 0, 0);
    case ZL_SYNCING:
        return (t_ms % 1000U) < 500U ? zl_rgb(0, 0, ZL_NORMAL_DUTY) : zl_rgb(0, 0, 0);
    case ZL_NORMAL:
    default:
        return normal_white ? zl_rgb(ZL_NORMAL_DUTY, ZL_NORMAL_DUTY, ZL_NORMAL_DUTY) : zl_rgb(0, 0, ZL_NORMAL_DUTY);
    }
}

/* How often the LED must be refreshed in a state (steady states barely wake the CPU). */
static inline uint32_t zoe_led_period_ms(enum zoe_led_state s)
{
    switch (s) {
    case ZL_FAULT:
    case ZL_WIPE:
    case ZL_CHARGING:
        return 50U;
    case ZL_PAIRING:
    case ZL_PAIRED:
    case ZL_WARNING:
    case ZL_SYNCING:
        return 100U;
    default:
        return 1000U;
    }
}

/* The PAIRED celebration: three blinks = 1200 ms. */
#define ZL_PAIRED_MS 1200U

#endif

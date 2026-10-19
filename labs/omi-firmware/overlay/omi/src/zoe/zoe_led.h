/* Zoe phase-2 firmware: the status LED (owner design 2026-10-19). States, priority and patterns: zoe_led_logic.h. */
#ifndef ZOE_LED_H
#define ZOE_LED_H

#include <stdbool.h>
#include <stdint.h>

/* Load the NORMAL-colour setting, register its GATT characteristic (encrypted link only), start the LED tick. */
int zoe_led_init(void);
/* A latched fault (fail-closed state): red fast blink until cleared. */
void zoe_led_set_fault_latched(bool on);
/* A transient fault (e.g. a pairing refused while the window was open): red fast blink for `ms`. */
void zoe_led_raise_fault(uint32_t ms);
/* Just bonded: three green blinks. */
void zoe_led_paired(void);

#endif

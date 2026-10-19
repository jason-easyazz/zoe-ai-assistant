/* Host shim: builds the PURE firmware code (zoe_ring_crypt.c, zoe_gesture.h) into a shared library for test_host.py.
 * The AES backend is a callback supplied by the test (python `cryptography`), so the C under test is byte-for-byte the
 * firmware's; only zrc_backend_* differ. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "zoe_gesture.h"
#include "zoe_led_logic.h"
#include "zoe_ring_crypt.h"

typedef int (*ctr_fn)(const uint8_t *iv, const uint8_t *in, uint8_t *out, size_t len);
static ctr_fn backend;

void host_set_backend(ctr_fn fn) { backend = fn; }

int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len)
{
    return backend ? backend(iv, in, out, len) : -1;
}

int zrc_backend_init(void) { return 0; }

int zrc_backend_new_salt(uint32_t *salt) { *salt = 0x5A5A5A5AU; return 0; }

static struct zoe_gesture g;

void host_gesture_reset(void) { g.armed = false; g.first_ms = 0; }

bool host_gesture_double_tap(int64_t now_ms) { return zoe_gesture_on_double_tap(&g, now_ms); }

bool host_window_open(int64_t until_ms, int64_t now_ms) { return zoe_window_open(until_ms, now_ms); }

/* the pure epoch allocator, driven with a fake persistence that can fail */
static uint32_t persisted = 0;
static int persist_fail = 0;
static int persist_calls = 0;
static int fake_persist(uint32_t v, void *ctx) { (void) ctx; persist_calls++; if (persist_fail) return -5; persisted = v; return 0; }
static struct zrc_epoch ep;
void host_epoch_boot(void) { zrc_epoch_resume(&ep, persisted); }
void host_epoch_set_fail(int f) { persist_fail = f; }
void host_epoch_reset(uint32_t p) { persisted = p; persist_calls = 0; persist_fail = 0; }
uint32_t host_epoch_persisted(void) { return persisted; }
int host_epoch_calls(void) { return persist_calls; }
int host_epoch_take(uint32_t *salt) { return zrc_epoch_take(&ep, fake_persist, 0, salt); }
void host_epoch_force(uint32_t next, uint32_t limit) { ep.next = next; ep.limit = limit; }
void zrc_backend_wipe_done(void) {}
int host_key_decision(int we, bool w, int ke, bool k, int ee, bool e, uint32_t v) { return zrc_key_decision(we, w, ke, k, ee, e, v); }
int host_new_key_epoch(bool found, uint32_t v, uint32_t *out) { return zrc_new_key_epoch(found, v, out); }

/* the LED decisions (zoe_led_logic.h) */
int host_led_pick(const struct zoe_led_inputs *in) { return (int) zoe_led_pick(in); }
void host_led_frame(int s, uint32_t t, bool white, uint8_t *rgb)
{
    struct zoe_rgb c = zoe_led_frame((enum zoe_led_state) s, t, white);
    rgb[0] = c.r; rgb[1] = c.g; rgb[2] = c.b;
}
uint32_t host_led_period(int s) { return zoe_led_period_ms((enum zoe_led_state) s); }

/* Host shim: builds the PURE firmware code (zoe_ring_crypt.c, zoe_gesture.h) into a shared library for test_host.py.
 * The AES backend is a callback supplied by the test (python `cryptography`), so the C under test is byte-for-byte the
 * firmware's; only zrc_backend_* differ. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "zoe_gesture.h"
#include "zoe_ring_crypt.h"

typedef int (*ctr_fn)(const uint8_t *iv, const uint8_t *in, uint8_t *out, size_t len);
static ctr_fn backend;

void host_set_backend(ctr_fn fn) { backend = fn; }

int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len)
{
    return backend ? backend(iv, in, out, len) : -1;
}

int zrc_backend_init(void) { return 0; }

uint32_t zrc_backend_new_salt(void) { return 0x5A5A5A5AU; }

static struct zoe_gesture g;

void host_gesture_reset(void) { g.armed = false; g.first_ms = 0; }

bool host_gesture_double_tap(int64_t now_ms) { return zoe_gesture_on_double_tap(&g, now_ms); }

bool host_window_open(int64_t until_ms, int64_t now_ms) { return zoe_window_open(until_ms, now_ms); }

/* Host stub of the Zephyr pieces zoe_ble_sec.c uses (host-test only; semantics reduced to what the tests drive). */
#ifndef STUB_ZEPHYR_KERNEL_H
#define STUB_ZEPHYR_KERNEL_H
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ARG_UNUSED(x) (void) (x)
struct k_spinlock { int unused; };
typedef int k_spinlock_key_t;
static inline k_spinlock_key_t k_spin_lock(struct k_spinlock *l) { (void) l; return 0; }
static inline void k_spin_unlock(struct k_spinlock *l, k_spinlock_key_t k) { (void) l; (void) k; }

extern int64_t stub_uptime_ms;
static inline int64_t k_uptime_get(void) { return stub_uptime_ms; }

struct k_work { int unused; };
typedef struct { int64_t ms; } k_timeout_t;
struct k_work_delayable { void (*fn)(struct k_work *); };
#define K_WORK_DELAYABLE_DEFINE(name, f) struct k_work_delayable name = {f}
#define K_SECONDS(s) ((k_timeout_t){(int64_t) (s) * 1000})
int k_work_reschedule(struct k_work_delayable *w, k_timeout_t t);
#endif

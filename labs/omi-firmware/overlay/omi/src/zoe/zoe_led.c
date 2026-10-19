/* Zoe phase-2 firmware: the status LED. The decisions are zoe_led_logic.h (pure, host-tested); this file gathers the inputs,
 * drives the three PWM channels (led.c) on a work item whose period follows the pattern, and owns the persisted NORMAL
 * colour setting ("zoe/lc": 0 = blue, 1 = white), which the bonded Pi may change over an ENCRYPTED characteristic. */
#include "zoe_led.h"

#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "led.h"
#include "rtc.h"
#include "storage.h"
#include "zoe_ble_sec.h"
#include "zoe_led_logic.h"
#ifdef CONFIG_ZOE_RING_ENCRYPT
#include "zoe_ring_crypt.h"
#endif

LOG_MODULE_REGISTER(zoe_led, CONFIG_LOG_DEFAULT_LEVEL);

#define ZOE_LED_COLOUR_KEY "zoe/lc"

extern bool is_off;
extern bool is_charging;
#ifdef CONFIG_OMI_ENABLE_BATTERY
extern uint8_t battery_percentage;
#endif
/* sd_card.c (patched): cheap, lock-free reads for the LED only. */
uint8_t sd_ring_fill_pct(void);
bool sd_ring_blocked(void);

static atomic_t fault_latched;
static atomic_t fault_until_ms;
static atomic_t paired_until_ms;
static atomic_t normal_white;
static struct zoe_rgb shown = {255, 255, 255}; /* forces the first write */

void zoe_led_set_fault_latched(bool on)
{
    atomic_set(&fault_latched, on ? 1 : 0);
}

void zoe_led_raise_fault(uint32_t ms)
{
    atomic_set(&fault_until_ms, (atomic_val_t) (k_uptime_get_32() + ms));
}

void zoe_led_paired(void)
{
    atomic_set(&paired_until_ms, (atomic_val_t) (k_uptime_get_32() + ZL_PAIRED_MS));
}

static bool before(atomic_t *until, uint32_t now)
{
    uint32_t u = (uint32_t) atomic_get(until);
    return u != 0U && (int32_t) (u - now) > 0;
}

static void led_tick(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(led_work, led_tick);

static void led_tick(struct k_work *work)
{
    ARG_UNUSED(work);
    uint32_t now = k_uptime_get_32();
    struct zoe_led_inputs in = {
        .off = is_off,
        .fault = atomic_get(&fault_latched) != 0 || before(&fault_until_ms, now) || sd_ring_blocked(),
        .pairing_window = zoe_ble_sec_pairing_open(),
#ifdef CONFIG_ZOE_RING_ENCRYPT
        .wiping = zrc_backend_wipe_in_progress(), /* the persisted intent, held until the ring is cleared */
#endif
        .paired_celebration = before(&paired_until_ms, now),
        .charging = is_charging,
#ifdef CONFIG_OMI_ENABLE_BATTERY
        .battery_pct = battery_percentage,
#endif
        .storage_pct = sd_ring_fill_pct(),
        .clock_set = rtc_is_valid(),
        .syncing = storage_transfer_active(),
    };
    enum zoe_led_state s = zoe_led_pick(&in);
    struct zoe_rgb c = zoe_led_frame(s, now, atomic_get(&normal_white) != 0);
    if (c.r != shown.r || c.g != shown.g || c.b != shown.b) {
        set_led_pwm(LED_RED, c.r);
        set_led_pwm(LED_GREEN, c.g);
        set_led_pwm(LED_BLUE, c.b);
        shown = c;
    }
    if (s != ZL_OFF) {
        k_work_reschedule(&led_work, K_MSEC(zoe_led_period_ms(s)));
    }
}

/* ---- the NORMAL-colour setting: settings + an encrypted-only characteristic ------------------------------------- */

static struct bt_uuid_128 zoe_led_svc_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x3f6d1a40, 0x8c2e, 0x4b7a, 0x9d15, 0x6e2c0b8f7a10));
static struct bt_uuid_128 zoe_led_colour_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x3f6d1a41, 0x8c2e, 0x4b7a, 0x9d15, 0x6e2c0b8f7a10));

static ssize_t colour_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
    uint8_t v = atomic_get(&normal_white) ? 1U : 0U;
    return bt_gatt_attr_read(conn, attr, buf, len, offset, &v, sizeof(v));
}

static ssize_t colour_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len,
                            uint16_t offset, uint8_t flags)
{
    ARG_UNUSED(conn);
    ARG_UNUSED(attr);
    ARG_UNUSED(flags);
    if (offset != 0U || len != 1U) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }
    uint8_t v = ((const uint8_t *) buf)[0];
    if (v > 1U) {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    if (settings_save_one(ZOE_LED_COLOUR_KEY, &v, sizeof(v)) != 0) {
        return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    }
    atomic_set(&normal_white, v);
    k_work_reschedule(&led_work, K_NO_WAIT);
    return len;
}

static struct bt_gatt_attr zoe_led_attrs[] = {
    BT_GATT_PRIMARY_SERVICE(&zoe_led_svc_uuid),
    BT_GATT_CHARACTERISTIC(&zoe_led_colour_uuid.uuid, BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE, ZOE_PERM_RW, colour_read,
                           colour_write, NULL),
};
static struct bt_gatt_service zoe_led_service = BT_GATT_SERVICE(zoe_led_attrs);

static int colour_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    uint8_t v = 0;
    if (len == 1U && read_cb(cb_arg, &v, 1) == 1 && v <= 1U) {
        *(uint8_t *) param = v;
    }
    return 0;
}

int zoe_led_init(void)
{
    uint8_t v = 0; /* blue unless the setting says white; an unreadable setting just means blue */
    (void) settings_load_subtree_direct(ZOE_LED_COLOUR_KEY, colour_cb, &v);
    atomic_set(&normal_white, v);
    int err = bt_gatt_service_register(&zoe_led_service);
    if (err) {
        LOG_WRN("LED colour characteristic not registered (%d)", err);
    }
    k_work_reschedule(&led_work, K_NO_WAIT);
    return 0;
}

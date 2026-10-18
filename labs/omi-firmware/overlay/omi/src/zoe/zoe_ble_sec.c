/* Zoe phase-2 firmware: bonded-only BLE. Contract in zoe_ble_sec.h; design in labs/omi-firmware/README.md. */
#include "zoe_ble_sec.h"

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci_types.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "haptic.h"
#include "zoe_gesture.h"

LOG_MODULE_REGISTER(zoe_ble_sec, CONFIG_LOG_DEFAULT_LEVEL);

#define ZOE_BOND_WIPE_KEY "zoe/bw"

static struct k_spinlock lock;
static int64_t window_until_ms;
static struct zoe_gesture gesture;
static struct bt_conn *watch_conn;

static void kick_fn(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(kick_work, kick_fn);

bool zoe_ble_sec_pairing_open(void)
{
    k_spinlock_key_t key = k_spin_lock(&lock);
    bool open = zoe_window_open(window_until_ms, k_uptime_get());
    k_spin_unlock(&lock, key);
    return open;
}

static void open_window(uint32_t seconds, const char *why)
{
    k_spinlock_key_t key = k_spin_lock(&lock);
    window_until_ms = k_uptime_get() + (int64_t) seconds * 1000;
    k_spin_unlock(&lock, key);
    LOG_INF("pairing window open for %u s (%s)", seconds, why);
    play_haptic_milli(200);
}

static void close_window(void)
{
    k_spinlock_key_t key = k_spin_lock(&lock);
    window_until_ms = 0;
    k_spin_unlock(&lock, key);
}

void zoe_ble_sec_on_double_tap(void)
{
    bool fire;
    k_spinlock_key_t key = k_spin_lock(&lock);
    fire = zoe_gesture_on_double_tap(&gesture, k_uptime_get());
    k_spin_unlock(&lock, key);
    if (fire) {
        open_window(CONFIG_ZOE_PAIRING_WINDOW_S, "gesture");
    }
}

/* ---- pairing gate ---------------------------------------------------------------------------------------------- */

static enum bt_security_err pairing_accept(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat)
{
    ARG_UNUSED(conn);
    ARG_UNUSED(feat);
    if (!zoe_ble_sec_pairing_open()) {
        LOG_WRN("pairing refused: window closed");
        return BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
    }
    return BT_SECURITY_ERR_SUCCESS;
}

static void pairing_cancel(struct bt_conn *conn)
{
    ARG_UNUSED(conn);
}

static const struct bt_conn_auth_cb auth_cb = {
    .pairing_accept = pairing_accept,
    .cancel = pairing_cancel,
};

static void pairing_complete(struct bt_conn *conn, bool bonded)
{
    ARG_UNUSED(conn);
    LOG_INF("pairing complete (bonded=%d)", bonded);
    if (bonded) {
        close_window();
        play_haptic_milli(800);     /* a long buzz = bonded (the window opening is a short one); never sleep in a BT callback */
    }
}

static void pairing_failed(struct bt_conn *conn, enum bt_security_err reason)
{
    ARG_UNUSED(conn);
    LOG_WRN("pairing failed (%d)", reason);
}

static struct bt_conn_auth_info_cb auth_info_cb = {
    .pairing_complete = pairing_complete,
    .pairing_failed = pairing_failed,
};

/* ---- connection watch: ask a bonded central to encrypt; drop one that never does -------------------------------- */

static void count_bond(const struct bt_bond_info *info, void *user_data)
{
    ARG_UNUSED(info);
    (*(int *) user_data)++;
}

static int bond_count(void)
{
    int n = 0;
    bt_foreach_bond(BT_ID_DEFAULT, count_bond, &n);
    return n;
}

static void kick_fn(struct k_work *work)
{
    ARG_UNUSED(work);
    struct bt_conn *conn = NULL;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (watch_conn != NULL) {
        conn = bt_conn_ref(watch_conn);
    }
    k_spin_unlock(&lock, key);
    if (conn == NULL) {
        return;
    }
    if (bt_conn_get_security(conn) < BT_SECURITY_L2) {
        if (zoe_ble_sec_pairing_open()) {
            k_work_reschedule(&kick_work, K_SECONDS(5));   /* someone may be pairing: look again later */
        } else {
            LOG_WRN("dropping a connection that never encrypted");
            (void) bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
        }
    }
    bt_conn_unref(conn);
}

static void on_connected(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        return;
    }
    k_spinlock_key_t key = k_spin_lock(&lock);
    struct bt_conn *old = watch_conn;
    watch_conn = bt_conn_ref(conn);
    k_spin_unlock(&lock, key);
    if (old != NULL) {
        bt_conn_unref(old);
    }
    if (bond_count() > 0) {
        /* Security Request: a central holding our LTK encrypts at once; anyone else is refused pairing. */
        int rc = bt_conn_set_security(conn, BT_SECURITY_L2);
        if (rc) {
            LOG_WRN("security request failed (%d)", rc);
        }
    }
    k_work_reschedule(&kick_work, K_SECONDS(CONFIG_ZOE_UNENCRYPTED_KICK_S));
}

static void on_disconnected(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    struct bt_conn *old = NULL;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (watch_conn == conn) {
        old = watch_conn;
        watch_conn = NULL;
    }
    k_spin_unlock(&lock, key);
    if (old != NULL) {
        bt_conn_unref(old);
    }
}

static void on_security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
    ARG_UNUSED(conn);
    LOG_INF("security level %d (err %d)", level, err);
}

BT_CONN_CB_DEFINE(zoe_conn_cb) = {
    .connected = on_connected,
    .disconnected = on_disconnected,
    .security_changed = on_security_changed,
};

/* ---- first boot of this firmware: forget every stock-era bond ------------------------------------------------- */

static int marker_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    ARG_UNUSED(read_cb);
    ARG_UNUSED(cb_arg);
    if (len > 0) {
        *(bool *) param = true;
    }
    return 0;
}

static int wipe_stock_bonds_once(void)
{
    bool done = false;
    (void) settings_load_subtree_direct(ZOE_BOND_WIPE_KEY, marker_cb, &done);
    if (done) {
        return 0;
    }
    int err = bt_unpair(BT_ID_DEFAULT, NULL);
    if (err) {
        LOG_ERR("could not delete stock-era bonds (%d)", err);
        return err;
    }
    const uint8_t one = 1;
    err = settings_save_one(ZOE_BOND_WIPE_KEY, &one, sizeof(one));
    LOG_INF("stock-era bonds deleted (marker save %d)", err);
    return err;
}

int zoe_ble_sec_init(void)
{
    int err = bt_conn_auth_cb_register(&auth_cb);
    if (err) {
        LOG_ERR("auth callbacks not registered (%d)", err);
        return err;
    }
    err = bt_conn_auth_info_cb_register(&auth_info_cb);
    if (err) {
        LOG_ERR("auth info callbacks not registered (%d)", err);
        return err;
    }
    (void) wipe_stock_bonds_once();
    if (bond_count() == 0) {
        open_window(CONFIG_ZOE_FIRST_SETUP_WINDOW_S, "no bond yet: first setup");
    }
    return 0;
}

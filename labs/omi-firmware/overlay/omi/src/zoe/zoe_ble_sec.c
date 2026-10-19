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
#ifdef CONFIG_ZOE_RING_ENCRYPT
#include "zoe_ring_crypt.h"
#endif
#ifdef CONFIG_ZOE_LED
#include "zoe_led.h"
#endif

LOG_MODULE_REGISTER(zoe_ble_sec, CONFIG_LOG_DEFAULT_LEVEL);

#define ZOE_BOND_WIPE_KEY "zoe/bw"   /* marker: the stock-era bonds were deleted once */
#define ZOE_NEW_BOND_WIPE "zoe/wb"   /* a new bond is being made: every OTHER bond must go (finished at boot if cut off) */
#define ZOE_MAX_BONDS_SEEN 4
#define ZOE_SMP_AUTH_BONDING 0x01 /* AuthReq bit 0, Core Vol 3 Part H 3.5.1 (Zephyr keeps it in a private header) */

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

struct bond_list {
    bt_addr_le_t addr[ZOE_MAX_BONDS_SEEN];
    int n;
};

static void collect_bond(const struct bt_bond_info *info, void *user_data)
{
    struct bond_list *bl = user_data;
    if (bl->n < ZOE_MAX_BONDS_SEEN) {
        bl->addr[bl->n++] = info->addr;
    }
}

/* Delete every bond except `keep` (NULL = all). The peer pairing right now is kept: its keys are overwritten by the new
 * pairing (CONFIG_BT_SMP_ALLOW_UNAUTH_OVERWRITE), and bt_unpair would disconnect it mid-pairing. */
static int delete_bonds_except(const bt_addr_le_t *keep)
{
    struct bond_list bl = {0};
    bt_foreach_bond(BT_ID_DEFAULT, collect_bond, &bl);
    for (int i = 0; i < bl.n; i++) {
        if (keep != NULL && bt_addr_le_eq(&bl.addr[i], keep)) {
            continue;
        }
        int err = bt_unpair(BT_ID_DEFAULT, &bl.addr[i]);
        if (err) {
            return err;
        }
    }
    return 0;
}

/* CRYPTO-WIPE ON NEW BOND (owner decision 2026-10-19: "if it's stolen and someone tries to pair it, wipe it").
 * Runs BEFORE a new or replacement bond is allowed; a reconnect with the existing bond never reaches here (no pairing).
 * Order is the guarantee - a power cut at any step leaves NO usable old key next to a new bond:
 *   1. persist the intents (key wipe, bond wipe): from here on the boot finishes whatever this misses;
 *   2. revoke the at-rest audio key: unusable in RAM at once, erased from flash (the SD worker then destroys it in PSA,
 *      makes a fresh key from the CSPRNG and empties the ring under it, and only then clears the key-wipe intent);
 *   3. delete every previous bond, then clear the bond-wipe intent.
 * Only then may the pairing go on. Any failure refuses the pairing (what was already done stays done). */
static int wipe_for_new_bond(struct bt_conn *conn)
{
    const uint8_t one = 1;
#ifdef CONFIG_ZOE_RING_ENCRYPT
    if (settings_save_one(ZRC_WIPE_SETTING, &one, sizeof(one))) {
        return -EIO;
    }
    zrc_backend_wipe_started(); /* the white LED follows the persisted intent until the ring is cleared */
#endif
    if (settings_save_one(ZOE_NEW_BOND_WIPE, &one, sizeof(one))) {
        return -EIO;
    }
#ifdef CONFIG_ZOE_RING_ENCRYPT
    if (zrc_backend_revoke()) {
        return -EIO;
    }
#endif
    if (delete_bonds_except(bt_conn_get_dst(conn))) {
        return -EIO;
    }
    (void) settings_delete(ZOE_NEW_BOND_WIPE);
    LOG_WRN("new bond: stored audio key destroyed, previous bonds deleted");
    return 0;
}

static enum bt_security_err pairing_accept(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat)
{
    if (!zoe_ble_sec_pairing_open()) {
        LOG_WRN("pairing refused: window closed");
        return BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
    }
    if (!(feat->auth_req & ZOE_SMP_AUTH_BONDING)) {
        /* A session-only pairing would be an encrypted, readable link outside the one-bond model: bond or nothing. */
        LOG_WRN("pairing refused: not a bonding request");
        return BT_SECURITY_ERR_AUTH_REQUIREMENT;
    }
    if (wipe_for_new_bond(conn) != 0) {
        LOG_ERR("pairing refused: the wipe before a new bond did not complete");
        play_haptic_milli(2000);
#ifdef CONFIG_ZOE_LED
        zoe_led_raise_fault(5000); /* a refused pairing while the window was open: red fast blink */
#endif
        return BT_SECURITY_ERR_UNSPECIFIED;
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
    LOG_INF("pairing complete (bonded=%d)", bonded);
    if (!bonded) {
        (void) bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);    /* belt and braces for the check in pairing_accept */
        return;
    }
    close_window();
#ifdef CONFIG_ZOE_LED
    zoe_led_paired(); /* three green blinks */
#endif
    play_haptic_milli(800);         /* a long buzz = bonded (the window opening is a short one); never sleep in a BT callback */
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

struct flag_load {
    bool found;
    int err;
};

/* A flag is one byte. Its contents are READ: a record of the wrong size, or one that cannot be read back, is corrupt -
 * an error (fail closed), never "set" and never "absent". */
static int marker_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    struct flag_load *fl = param;
    uint8_t v = 0;
    fl->found = true;
    if (len != 1U) {
        fl->err = -EINVAL;
        return 0;
    }
    ssize_t n = read_cb(cb_arg, &v, 1);
    if (n != 1) {
        fl->err = n < 0 ? (int) n : -EIO;
    }
    return 0;
}

#define ZOE_WIPE_ATTEMPTS 3

/* Read a flag record. 0 with *found set or clear; <0 = the store could not be read, which is NEVER taken as "not set"
 * (Zephyr returns 0 without a callback for a missing record, and a negative value for a backend failure). */
static int read_flag(const char *name, bool *found)
{
    struct flag_load fl = {false, 0};
    int rc = settings_load_subtree_direct(name, marker_cb, &fl);
    *found = fl.found;
    if (rc < 0) {
        return rc;
    }
    return fl.err;
}

/* A new-bond wipe that a power cut interrupted before its bonds were deleted: delete them all now, before advertising. */
static int finish_new_bond_wipe(void)
{
    bool pending = false;
    int rerr = read_flag(ZOE_NEW_BOND_WIPE, &pending);
    if (rerr) {
        LOG_ERR("new-bond wipe intent unreadable (%d): not advertising", rerr);
        return rerr;
    }
    if (!pending) {
        return 0;
    }
    int err = delete_bonds_except(NULL);
    if (err) {
        LOG_ERR("interrupted new-bond wipe: bonds not deleted (%d)", err);
        return err;
    }
    (void) settings_delete(ZOE_NEW_BOND_WIPE);
    LOG_WRN("interrupted new-bond wipe finished: all bonds deleted");
    return 0;
}

/* 0 = no stock-era bond can remain (already marked, or bt_unpair succeeded). A failed marker save is only logged: the bonds
 * are gone, and the next boot wipes again (fail-safe). A failed bt_unpair is returned: the caller must not advertise. */
static int wipe_stock_bonds_once(void)
{
    bool done = false;
    int rerr = read_flag(ZOE_BOND_WIPE_KEY, &done);
    if (rerr) {
        LOG_ERR("stock-era wipe marker unreadable (%d): not advertising", rerr);
        return rerr;
    }
    if (done) {
        return 0;
    }
    int err = -EIO;
    for (int attempt = 0; attempt < ZOE_WIPE_ATTEMPTS && err; attempt++) {
        err = bt_unpair(BT_ID_DEFAULT, NULL);
    }
    if (err) {
        LOG_ERR("could not delete stock-era bonds (%d)", err);
        return err;
    }
    const uint8_t one = 1;
    int serr = settings_save_one(ZOE_BOND_WIPE_KEY, &one, sizeof(one));
    if (serr) {
        LOG_WRN("stock-era bonds deleted; marker not saved (%d): the next boot deletes again", serr);
    }
    return 0;
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
    err = wipe_stock_bonds_once();
    if (!err) {
        err = finish_new_bond_wipe();
    }
    if (err) {
        /* Fail closed: a stock-era phone bond could still encrypt and read. Do not advertise; a long buzz says why. */
        LOG_ERR("security init failed: NOT advertising");
        play_haptic_milli(2000);
#ifdef CONFIG_ZOE_LED
        zoe_led_set_fault_latched(true);
#endif
        return err;
    }
    /* No automatic window, not even for first setup: pairing ALWAYS needs the physical gesture. */
    return 0;
}

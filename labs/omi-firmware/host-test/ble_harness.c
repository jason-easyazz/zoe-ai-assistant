/* Host harness: the REAL zoe_ble_sec.c (and the pure zoe_ring_crypt.c policy) against fakes of Zephyr's settings store, bond
 * table and connections, with power-loss injection: every persistent operation spends one unit of `budget`, and when the
 * budget runs out the fake "loses power" (longjmp) BEFORE applying it. The SD worker's re-key and the boot-time key policy
 * are modelled here exactly as zoe_ring_crypt_psa.c / sd_card.c order them (intent -> key -> ring -> clear intent). */
#include <setjmp.h>
#include <string.h>

#define CONFIG_ZOE_BLE_BONDED_ONLY 1
#define CONFIG_ZOE_RING_ENCRYPT 1
#define CONFIG_ZOE_PAIRING_WINDOW_S 60
#define CONFIG_ZOE_UNENCRYPTED_KICK_S 30
#define CONFIG_LOG_DEFAULT_LEVEL 0

#include "zoe_ble_sec.c"

/* ---- power-loss injection ----------------------------------------------------------------------------------------- */
static jmp_buf crash_env;
static int budget = -1; /* -1 = no crash */
static int ops_done;

static void persist_op(void)
{
    if (budget == 0) {
        longjmp(crash_env, 1);
    }
    if (budget > 0) {
        budget--;
    }
    ops_done++;
}

/* ---- fake settings store (persistent) ----------------------------------------------------------------------------- */
#define NKEYS 8
static struct { char name[16]; int present; uint32_t value; } store[NKEYS];

static int slot(const char *name, int create)
{
    for (int i = 0; i < NKEYS; i++) {
        if (store[i].present && strcmp(store[i].name, name) == 0) return i;
    }
    if (!create) return -1;
    for (int i = 0; i < NKEYS; i++) {
        if (!store[i].present) { strncpy(store[i].name, name, 15); return i; }
    }
    return -1;
}

int fail_save;  /* make the Nth settings_save_one fail (not a crash): the code must refuse, not proceed */
static int save_calls;

int settings_save_one(const char *name, const void *value, size_t len)
{
    if (fail_save > 0 && ++save_calls == fail_save) return -5;
    persist_op();
    int i = slot(name, 1);
    store[i].present = 1;
    store[i].value = 0;
    memcpy(&store[i].value, value, len < 4 ? len : 4);
    return 0;
}

int settings_delete(const char *name)
{
    persist_op();
    int i = slot(name, 0);
    if (i >= 0) store[i].present = 0;
    return 0;
}

int settings_load_subtree_direct(const char *subtree, settings_load_direct_cb cb, void *param)
{
    int i = slot(subtree, 0);
    if (i >= 0) cb(NULL, 4, NULL, NULL, param);
    return 0;
}

static int has(const char *name) { return slot(name, 0) >= 0; }
static uint32_t val(const char *name) { int i = slot(name, 0); return i >= 0 ? store[i].value : 0; }

static void put(const char *name, uint32_t v)
{
    int i = slot(name, 1);
    store[i].present = 1;
    store[i].value = v;
}

/* ---- fake bond table (persistent) --------------------------------------------------------------------------------- */
#define PI 1
#define THIEF 2
static int bonded[3];
int fail_unpair;

static bt_addr_le_t addr_of(int who) { bt_addr_le_t a = {0}; a.a[0] = (uint8_t) who; return a; }

void bt_foreach_bond(uint8_t id, void (*func)(const struct bt_bond_info *, void *), void *ud)
{
    (void) id;
    for (int w = 1; w <= 2; w++) {
        if (bonded[w]) { struct bt_bond_info bi = {addr_of(w)}; func(&bi, ud); }
    }
}

int bt_unpair(uint8_t id, const bt_addr_le_t *addr)
{
    (void) id;
    if (fail_unpair) return -5;
    persist_op();
    for (int w = 1; w <= 2; w++) {
        if (addr == NULL || addr->a[0] == w) bonded[w] = 0;
    }
    return 0;
}

/* ---- the rest of Zephyr, trivially -------------------------------------------------------------------------------- */
int64_t stub_uptime_ms;
int k_work_reschedule(struct k_work_delayable *w, k_timeout_t t) { (void) w; (void) t; return 0; }
int bt_conn_auth_cb_register(const struct bt_conn_auth_cb *cb) { (void) cb; return 0; }
int bt_conn_auth_info_cb_register(struct bt_conn_auth_info_cb *cb) { (void) cb; return 0; }
struct bt_conn *bt_conn_ref(struct bt_conn *c) { c->refs++; return c; }
void bt_conn_unref(struct bt_conn *c) { c->refs--; }
bt_security_t bt_conn_get_security(const struct bt_conn *c) { return (bt_security_t) c->security; }
int bt_conn_set_security(struct bt_conn *c, bt_security_t s) { (void) c; (void) s; return 0; }
int bt_conn_disconnect(struct bt_conn *c, uint8_t r) { (void) r; c->disconnected = 1; return 0; }
const bt_addr_le_t *bt_conn_get_dst(const struct bt_conn *c) { return &c->dst; }
int last_haptic;
void play_haptic_milli(uint32_t d) { last_haptic = (int) d; }

/* ---- the audio key: RAM + the SD worker / boot policy, ordered as the firmware orders them ----------------------- */
static int ram_key;     /* generation of the key usable in RAM; 0 = none */
static int revoked;
static int next_gen = 1;
static int ring_key;    /* the key generation the ring's content is encrypted under; 0 = empty */

int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len)
{
    (void) iv; (void) in; (void) out; (void) len;
    return -1; /* not used by these scenarios */
}

int zrc_backend_revoke(void)
{
    revoked = 1;                 /* unusable at once */
    return settings_delete("zoe/rk");
}

/* zrc_backend_init + the mount/re-key ring clear + zrc_backend_wipe_done */
static void key_init_and_clear(void)
{
    int wipe = has("zoe/wk");
    if (wipe) settings_delete("zoe/rk");
    int key_found = !wipe && has("zoe/rk");
    if (zrc_needs_new_key(wipe, key_found, has("zoe/re"), val("zoe/re"))) {
        uint32_t ep = val("zoe/re") + 1024;
        settings_save_one("zoe/re", &ep, 4);
        uint32_t g = (uint32_t) ++next_gen;
        settings_save_one("zoe/rk", &g, 4);
        ram_key = (int) g;
        persist_op();            /* clear_ring_internal: metadata reset */
        ring_key = 0;
        settings_delete("zoe/wk");
    } else {
        ram_key = (int) val("zoe/rk");
    }
}

void h_worker(void)              /* the SD worker loop's zrc_backend_rekey_pending() branch */
{
    if (!revoked) return;
    ram_key = 0;                 /* zrc_backend_destroy */
    revoked = 0;
    key_init_and_clear();
}

/* ---- scenario API for test_host.py --------------------------------------------------------------------------------- */
void h_reset(void)
{
    memset(store, 0, sizeof(store));
    memset(bonded, 0, sizeof(bonded));
    put("zoe/bw", 1);            /* stock-era bonds already wiped on an earlier boot */
    put("zoe/rk", 1);            /* the owner's key, generation 1 */
    put("zoe/re", 1025);
    bonded[PI] = 1;
    ram_key = 1; revoked = 0; next_gen = 1; ring_key = 1; /* the ring holds the owner's audio under key 1 */
    budget = -1; ops_done = 0; fail_save = 0; fail_unpair = 0; last_haptic = 0;
    window_until_ms = 0; gesture.armed = false; stub_uptime_ms = 100000;
}

void h_gesture(void)
{
    zoe_ble_sec_on_double_tap();
    stub_uptime_ms += 500;
    zoe_ble_sec_on_double_tap();
}

static struct bt_conn conn;

/* A pairing from `who` (bonding requested), then the bond being stored, then the SD worker's re-key - each step spends
 * budget. Returns 0 = all done, 1 = power lost, 2 = pairing refused. */
int h_pair(int who, int crash_budget)
{
    budget = crash_budget;
    memset(&conn, 0, sizeof(conn));
    conn.dst = addr_of(who);
    struct bt_conn_pairing_feat feat = {.auth_req = 0x01};
    if (setjmp(crash_env)) { budget = -1; return 1; }
    if (auth_cb.pairing_accept(&conn, &feat) != BT_SECURITY_ERR_SUCCESS) { budget = -1; return 2; }
    persist_op();                /* the stack stores the new bond */
    bonded[who] = 1;
    auth_info_cb.pairing_complete(&conn, true);
    h_worker();
    budget = -1;
    return 0;
}

/* Power cycle: RAM is gone; zoe_ble_sec_init (before advertising) and the SD mount's key init run. Returns init's rc. */
int h_boot(void)
{
    ram_key = 0; revoked = 0; window_until_ms = 0; gesture.armed = false;
    budget = -1;
    int rc = zoe_ble_sec_init();
    key_init_and_clear();
    return rc;
}

int h_reconnect(int who)         /* an existing bond reconnecting: connect, encrypt with the stored LTK, no pairing */
{
    int before = ops_done;
    memset(&conn, 0, sizeof(conn));
    conn.dst = addr_of(who);
    zoe_conn_cb.connected(&conn, 0);
    conn.security = BT_SECURITY_L2;
    zoe_conn_cb.security_changed(&conn, BT_SECURITY_L2, BT_SECURITY_ERR_SUCCESS);
    zoe_conn_cb.disconnected(&conn, 0);
    return ops_done - before;
}

int h_accept_now(int bonding)    /* pairing_accept alone, no side effects counted beyond its own */
{
    memset(&conn, 0, sizeof(conn));
    conn.dst = addr_of(THIEF);
    struct bt_conn_pairing_feat feat = {.auth_req = (uint8_t) (bonding ? 1 : 0)};
    budget = -1;
    return auth_cb.pairing_accept(&conn, &feat);
}

int h_bonded(int who) { return bonded[who]; }
int h_ram_key(void) { return ram_key; }
int h_flash_key(void) { return has("zoe/rk") ? (int) val("zoe/rk") : 0; }
int h_ring_key(void) { return ring_key; }
int h_has(const char *n) { return has(n); }
int h_ops(void) { return ops_done; }
int h_haptic(void) { return last_haptic; }
void h_set_fail_unpair(int f) { fail_unpair = f; }
void h_set_fail_save(int f) { fail_save = f; save_calls = 0; }
void h_clear_marker(void) { int i = slot("zoe/bw", 0); if (i >= 0) store[i].present = 0; }
int h_init(void) { return zoe_ble_sec_init(); }
int h_window_open(void) { return zoe_ble_sec_pairing_open(); }

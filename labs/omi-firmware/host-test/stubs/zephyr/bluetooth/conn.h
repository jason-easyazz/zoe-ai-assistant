#ifndef STUB_ZEPHYR_BT_CONN_H
#define STUB_ZEPHYR_BT_CONN_H
#include <stdbool.h>
#include <stdint.h>

#include <zephyr/bluetooth/bluetooth.h>
struct bt_conn { int refs; int security; int disconnected; bt_addr_le_t dst; };
typedef enum { BT_SECURITY_L0, BT_SECURITY_L1, BT_SECURITY_L2, BT_SECURITY_L3, BT_SECURITY_L4 } bt_security_t;
enum bt_security_err {
    BT_SECURITY_ERR_SUCCESS,
    BT_SECURITY_ERR_AUTH_FAIL,
    BT_SECURITY_ERR_PIN_OR_KEY_MISSING,
    BT_SECURITY_ERR_OOB_NOT_AVAILABLE,
    BT_SECURITY_ERR_AUTH_REQUIREMENT,
    BT_SECURITY_ERR_PAIR_NOT_SUPPORTED,
    BT_SECURITY_ERR_PAIR_NOT_ALLOWED,
    BT_SECURITY_ERR_INVALID_PARAM,
    BT_SECURITY_ERR_KEY_REJECTED,
    BT_SECURITY_ERR_UNSPECIFIED,
};
struct bt_conn_pairing_feat {
    uint8_t io_capability, oob_data_flag, auth_req, max_enc_key_size, init_key_dist, resp_key_dist;
};
struct bt_conn_auth_cb {
    enum bt_security_err (*pairing_accept)(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat);
    void (*cancel)(struct bt_conn *conn);
};
struct bt_conn_auth_info_cb {
    void (*pairing_complete)(struct bt_conn *conn, bool bonded);
    void (*pairing_failed)(struct bt_conn *conn, enum bt_security_err reason);
};
struct bt_conn_cb {
    void (*connected)(struct bt_conn *conn, uint8_t err);
    void (*disconnected)(struct bt_conn *conn, uint8_t reason);
    void (*security_changed)(struct bt_conn *conn, bt_security_t level, enum bt_security_err err);
};
#define BT_CONN_CB_DEFINE(name) const struct bt_conn_cb name
int bt_conn_auth_cb_register(const struct bt_conn_auth_cb *cb);
int bt_conn_auth_info_cb_register(struct bt_conn_auth_info_cb *cb);
struct bt_conn *bt_conn_ref(struct bt_conn *conn);
void bt_conn_unref(struct bt_conn *conn);
bt_security_t bt_conn_get_security(const struct bt_conn *conn);
int bt_conn_set_security(struct bt_conn *conn, bt_security_t sec);
int bt_conn_disconnect(struct bt_conn *conn, uint8_t reason);
const bt_addr_le_t *bt_conn_get_dst(const struct bt_conn *conn);
#endif

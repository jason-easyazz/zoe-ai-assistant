/* Zoe phase-2 firmware: the device backend of the ring encryption (PSA Crypto; the nRF5340's CryptoCell where NCS maps it). */
#include "zoe_ring_crypt.h"

#include <psa/crypto.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>

LOG_MODULE_REGISTER(zoe_ring_crypt, CONFIG_LOG_DEFAULT_LEVEL);

#define ZRC_KEY_SETTING "zoe/rk"
#define ZRC_EPOCH_SETTING "zoe/re" /* the salt reservation limit (uint32) bound to the key above */
#define ZRC_KEY_BYTES 16U

static psa_key_id_t key_id;
static bool ready;
static atomic_t revoked; /* set from the pairing path (BT thread): the key is unusable from that instant */
static struct zrc_epoch epoch;

/* One settings record read with its outcome told apart: found or not (rc 0), or an error (<0). Zephyr's
 * settings_load_subtree_direct returns 0 and never calls back for a missing record; a backend failure is a negative
 * return, and a short/failed read inside the callback is caught here. An error is NEVER taken as "absent". */
struct rec_load {
    uint8_t buf[ZRC_KEY_BYTES];
    size_t want; /* exact length the record must have; 0 = any (a flag) */
    bool found;
    int err;
};

static int rec_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    struct rec_load *rl = param;
    rl->found = true;
    if (rl->want == 0U) {
        return 0;
    }
    if (len != rl->want) {
        rl->err = -EINVAL; /* a record of the wrong size is corrupt, not absent */
        return 0;
    }
    ssize_t n = read_cb(cb_arg, rl->buf, rl->want);
    if (n != (ssize_t) rl->want) {
        rl->err = n < 0 ? (int) n : -EIO;
    }
    return 0;
}

static int load_rec(const char *name, struct rec_load *rl)
{
    int rc = settings_load_subtree_direct(name, rec_cb, rl);
    if (rc < 0 && rl->err == 0) {
        rl->err = rc;
    }
    return rl->err;
}

static int persist_epoch(uint32_t new_limit, void *ctx)
{
    ARG_UNUSED(ctx);
    return settings_save_one(ZRC_EPOCH_SETTING, &new_limit, sizeof(new_limit));
}

int zrc_backend_init(void)
{
    if (ready) {
        return 0;
    }
    bool created = false;
    if (psa_crypto_init() != PSA_SUCCESS) {
        LOG_ERR("psa_crypto_init failed");
        return -EIO;
    }

    struct rec_load wipe = {.want = 0};
    struct rec_load ep = {.want = sizeof(uint32_t)};
    struct rec_load kl = {.want = ZRC_KEY_BYTES};
    (void) load_rec(ZRC_WIPE_SETTING, &wipe);
    (void) load_rec(ZRC_EPOCH_SETTING, &ep);
    uint32_t ep_value = 0;
    memcpy(&ep_value, ep.buf, sizeof(ep_value));
    if (!wipe.err && !wipe.found) {
        (void) load_rec(ZRC_KEY_SETTING, &kl); /* the old key is not even read while a wipe is pending */
    }
    int decision = zrc_key_decision(wipe.err, wipe.found, kl.err, kl.found, ep.err, ep.found, ep_value);
    if (decision < 0) {
        memset(&kl, 0, sizeof(kl));
        LOG_ERR("key store unreadable (%d): no key loaded or made, ring stays blocked", decision);
        return decision;
    }
    if (decision == ZRC_KEY_NEW) {
        /* A wipe, no key, or a key without its salt reservation: the old key (if any) is never used again. */
        if (wipe.found) {
            int derr = settings_delete(ZRC_KEY_SETTING);
            if (derr) {
                LOG_ERR("wipe pending but the old key could not be erased (%d): ring stays blocked", derr);
                return derr;
            }
        }
        uint32_t new_limit = 0;
        int err = zrc_new_key_epoch(ep.found, ep_value, &new_limit);
        if (err) {
            LOG_ERR("salt space exhausted: no new key, ring stays blocked");
            return err;
        }
        if (psa_generate_random(kl.buf, ZRC_KEY_BYTES) != PSA_SUCCESS) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("no CSPRNG output: no key, ring writes stay blocked");
            return -EIO;
        }
        /* The reservation is written BEFORE the key: a key never exists without one (a crash in between leaves a record
         * and no key, and the next boot makes a new key again). It only moves up, across keys too. */
        err = persist_epoch(new_limit, NULL);
        if (err) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("salt reservation not saved (%d): ring writes stay blocked", err);
            return err;
        }
        err = settings_save_one(ZRC_KEY_SETTING, kl.buf, ZRC_KEY_BYTES);
        if (err) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("device key not saved (%d): ring writes stay blocked", err);
            return err;
        }
        ep_value = new_limit;
        LOG_INF("ring device key created");
        created = true;
    }

    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_set_key_type(&attr, PSA_KEY_TYPE_AES);
    psa_set_key_bits(&attr, 128);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_ENCRYPT | PSA_KEY_USAGE_DECRYPT);
    psa_set_key_algorithm(&attr, PSA_ALG_CTR);
    psa_set_key_lifetime(&attr, PSA_KEY_LIFETIME_VOLATILE);
    psa_status_t st = psa_import_key(&attr, kl.buf, ZRC_KEY_BYTES, &key_id);
    memset(&kl, 0, sizeof(kl));
    if (st != PSA_SUCCESS) {
        LOG_ERR("key import failed (%d)", (int) st);
        return -EIO;
    }
    zrc_epoch_resume(&epoch, ep_value);
    ready = true;
    return created ? 1 : 0;
}

int zrc_backend_revoke(void)
{
    atomic_set(&revoked, 1);
    return settings_delete(ZRC_KEY_SETTING);
}

bool zrc_backend_rekey_pending(void)
{
    return atomic_get(&revoked) != 0;
}

void zrc_backend_destroy(void)
{
    if (ready) {
        (void) psa_destroy_key(key_id); /* PSA zeroizes the key material it held */
    }
    ready = false;
    key_id = 0;
    atomic_set(&revoked, 0);
}

void zrc_backend_wipe_done(void)
{
    (void) settings_delete(ZRC_WIPE_SETTING);
}

int zrc_backend_new_salt(uint32_t *salt)
{
    if (!ready || atomic_get(&revoked)) {
        return -EACCES; /* no key, no salt: never a weak fallback */
    }
    return zrc_epoch_take(&epoch, persist_epoch, NULL, salt);
}

int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len)
{
    if (!ready || atomic_get(&revoked)) {
        return -EACCES;
    }
    psa_cipher_operation_t op = PSA_CIPHER_OPERATION_INIT;
    size_t n = 0, tail = 0;
    psa_status_t st = psa_cipher_encrypt_setup(&op, key_id, PSA_ALG_CTR);
    if (st == PSA_SUCCESS) {
        st = psa_cipher_set_iv(&op, iv, ZRC_IV_BYTES);
    }
    if (st == PSA_SUCCESS) {
        st = psa_cipher_update(&op, in, len, out, len, &n);
    }
    if (st == PSA_SUCCESS) {
        st = psa_cipher_finish(&op, out + n, len - n, &tail);
    }
    if (st != PSA_SUCCESS) {
        psa_cipher_abort(&op);
        return -EIO;
    }
    return (n + tail == len) ? 0 : -EIO;
}

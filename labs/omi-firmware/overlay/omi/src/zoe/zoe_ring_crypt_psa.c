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

struct u32_load {
    uint32_t value;
    bool found;
};

static int u32_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    struct u32_load *ul = param;
    if (len == sizeof(ul->value) && read_cb(cb_arg, &ul->value, sizeof(ul->value)) == sizeof(ul->value)) {
        ul->found = true;
    }
    return 0;
}

static int u8_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    ARG_UNUSED(read_cb);
    ARG_UNUSED(cb_arg);
    if (len > 0) {
        ((struct u32_load *) param)->found = true;
    }
    return 0;
}

static int persist_epoch(uint32_t new_limit, void *ctx)
{
    ARG_UNUSED(ctx);
    return settings_save_one(ZRC_EPOCH_SETTING, &new_limit, sizeof(new_limit));
}

struct key_load {
    uint8_t key[ZRC_KEY_BYTES];
    bool found;
};

static int key_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param)
{
    ARG_UNUSED(key);
    struct key_load *kl = param;
    if (len == ZRC_KEY_BYTES && read_cb(cb_arg, kl->key, ZRC_KEY_BYTES) == ZRC_KEY_BYTES) {
        kl->found = true;
    }
    return 0;
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

    struct key_load kl = {0};
    struct u32_load el = {0};
    struct u32_load wipe = {0};
    (void) settings_load_subtree_direct(ZRC_WIPE_SETTING, u8_cb, &wipe);
    (void) settings_load_subtree_direct(ZRC_EPOCH_SETTING, u32_cb, &el);
    if (wipe.found) {
        /* A new-bond wipe was started (possibly cut off by a power loss): the old key is never loaded again. */
        int derr = settings_delete(ZRC_KEY_SETTING);
        if (derr) {
            LOG_ERR("wipe pending but the old key could not be erased (%d): ring stays blocked", derr);
            return derr;
        }
    } else {
        (void) settings_load_subtree_direct(ZRC_KEY_SETTING, key_cb, &kl);
    }
    if (zrc_needs_new_key(wipe.found, kl.found, el.found, el.value)) {
        /* No key, a wipe, or a key whose salt reservation is lost: the old key (if any) is never used again. The salt
         * reservation stays MONOTONIC across keys (it only moves up) and is written BEFORE the key, so a key never exists
         * without one (a crash in between leaves a record and no key: the next boot makes a new key again). */
        if (psa_generate_random(kl.key, sizeof(kl.key)) != PSA_SUCCESS) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("no CSPRNG output: no key, ring writes stay blocked");
            return -EIO;
        }
        el.value = (el.found && el.value > 0U && el.value <= UINT32_MAX - ZRC_EPOCH_BLOCK) ? el.value + ZRC_EPOCH_BLOCK : 1U;
        int err = persist_epoch(el.value, NULL);
        if (err) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("salt reservation not saved (%d): ring writes stay blocked", err);
            return err;
        }
        err = settings_save_one(ZRC_KEY_SETTING, kl.key, sizeof(kl.key));
        if (err) {
            memset(&kl, 0, sizeof(kl));
            LOG_ERR("device key not saved (%d): ring writes stay blocked", err);
            return err;
        }
        LOG_INF("ring device key created");
        created = true;
    }

    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_set_key_type(&attr, PSA_KEY_TYPE_AES);
    psa_set_key_bits(&attr, 128);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_ENCRYPT | PSA_KEY_USAGE_DECRYPT);
    psa_set_key_algorithm(&attr, PSA_ALG_CTR);
    psa_set_key_lifetime(&attr, PSA_KEY_LIFETIME_VOLATILE);
    psa_status_t st = psa_import_key(&attr, kl.key, sizeof(kl.key), &key_id);
    memset(&kl, 0, sizeof(kl));
    if (st != PSA_SUCCESS) {
        LOG_ERR("key import failed (%d)", (int) st);
        return -EIO;
    }
    zrc_epoch_resume(&epoch, el.value);
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

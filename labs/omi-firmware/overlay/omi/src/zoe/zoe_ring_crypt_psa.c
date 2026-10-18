/* Zoe phase-2 firmware: the device backend of the ring encryption (PSA Crypto; the nRF5340's CryptoCell where NCS maps it). */
#include "zoe_ring_crypt.h"

#include <psa/crypto.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

LOG_MODULE_REGISTER(zoe_ring_crypt, CONFIG_LOG_DEFAULT_LEVEL);

#define ZRC_KEY_SETTING "zoe/rk"
#define ZRC_KEY_BYTES 16U

static psa_key_id_t key_id;
static bool ready;

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
    (void) settings_load_subtree_direct(ZRC_KEY_SETTING, key_cb, &kl);
    if (!kl.found) {
        if (psa_generate_random(kl.key, sizeof(kl.key)) != PSA_SUCCESS) {
            return -EIO;
        }
        int err = settings_save_one(ZRC_KEY_SETTING, kl.key, sizeof(kl.key));
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
    ready = true;
    return created ? 1 : 0;
}

uint32_t zrc_backend_new_salt(void)
{
    uint32_t salt = 0;
    while (salt == 0U) {
        if (psa_generate_random((uint8_t *) &salt, sizeof(salt)) != PSA_SUCCESS) {
            salt = (uint32_t) k_cycle_get_32() | 1U; /* never reached on a working RNG; still never 0 */
        }
    }
    return salt;
}

int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len)
{
    if (!ready) {
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

/*
 * Zoe phase-2 firmware: the SD ring's audio packets encrypted at rest.
 *
 * AES-128-CTR, one device key (16 random bytes generated on the pendant at first use, kept in the internal-flash settings
 * store, never sent anywhere). Each 444-byte packet is encrypted ONCE, in RAM, when it is appended to the open batch;
 * flushes rewrite the same ciphertext. IV = salt(4) || seq(8) || block(4), big-endian. The salt is NOT random: it is a
 * per-key MONOTONIC epoch counter (one value per batch, stored in the batch header's reserved0), reserved ahead in blocks
 * of ZRC_EPOCH_BLOCK and persisted BEFORE any value of a block is used, so after any crash or reboot the next salt is above
 * every salt ever handed out. A batch re-opened after a reboot is re-keyed to a new salt. So, under one key, a salt is used
 * by exactly one batch and a (salt, seq) pair covers exactly one plaintext - guaranteed, not probable - even after CLEAR, a
 * truncated tail or a torn write. Losing the epoch record forces a NEW key (the old ring is cleared).
 *
 * Batch headers and ring metadata stay plaintext (counts and sequence numbers, no audio). The Pi receives plaintext over
 * the LE Secure Connections link: the BLE protocol is unchanged byte for byte.
 *
 * Split: zoe_ring_crypt.c is pure (host-tested with an OpenSSL backend); zoe_ring_crypt_psa.c is the device backend.
 */
#ifndef ZOE_RING_CRYPT_H
#define ZOE_RING_CRYPT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ZRC_IV_BYTES 16U
#define ZRC_WIPE_SETTING "zoe/wk" /* intent: the audio key must be destroyed and replaced (set before, cleared after) */

/* Backend (device: PSA; host test: OpenSSL). AES-128-CTR over len bytes from the 16-byte initial counter block. */
int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len);

/* Backend: load or create the device key and make it usable. Idempotent. <0 = no key: nothing may be written; 1 = a NEW
 * key was just created, so whatever the ring holds is unreadable and the caller clears it; 0 = the existing key. */
int zrc_backend_init(void);

/* Backend, called from the pairing path BEFORE a new bond: the key is unusable from this call on and erased from flash.
 * The SD worker sees zrc_backend_rekey_pending() and finishes (zrc_backend_destroy, then zrc_backend_init + ring clear +
 * zrc_backend_wipe_done). */
int zrc_backend_revoke(void);
bool zrc_backend_rekey_pending(void);
void zrc_backend_destroy(void);
void zrc_backend_wipe_done(void);
/* The RAM mirror of the persisted wipe intent ("zoe/wk"): set when the intent is written (or found at boot), cleared only
 * when zrc_backend_wipe_done() has erased it - i.e. after the ring was cleared under the new key. Drives the white LED. */
void zrc_backend_wipe_started(void);
bool zrc_backend_wipe_in_progress(void);

/* Pure: what the boot (or a re-init) does with the stored key. Each *_err is the read's result (0 = read cleanly, found or
 * not; <0 = the store could not be read). ZRC_KEY_USE = load the stored key; ZRC_KEY_NEW = make a new one (and empty the
 * ring); <0 = refuse: a read failed, so nothing is guessed - no key is loaded or made, the ring stays blocked. */
#define ZRC_KEY_USE 0
#define ZRC_KEY_NEW 1
int zrc_key_decision(int wipe_err, bool wipe_found, int key_err, bool key_found, int epoch_err, bool epoch_found,
                     uint32_t epoch);
/* Pure: the salt reservation a NEW key starts from - above the old one (monotonic across keys). <0 = exhausted: no key. */
int zrc_new_key_epoch(bool epoch_found, uint32_t epoch, uint32_t *out);

/* Empty the ring under a NEW key, then (and only then) clear the wipe intent. clear() is the ring-metadata reset. A
 * failure leaves the intent set (the next boot repeats the wipe) and is returned: the caller must block the ring. */
typedef int (*zrc_clear_fn)(void *ctx);
int zrc_finish_new_key(zrc_clear_fn clear, void *ctx);

/* Backend: the next batch salt (never 0). <0 = none could be reserved: the caller STOPS ring writes. */
int zrc_backend_new_salt(uint32_t *salt);

/* Pure: the monotonic salt allocator. `limit` is the persisted reservation: every salt below it may have been used. */
#define ZRC_EPOCH_BLOCK 1024U
struct zrc_epoch {
    uint32_t next;
    uint32_t limit;
};
typedef int (*zrc_persist_fn)(uint32_t new_limit, void *ctx);
void zrc_epoch_resume(struct zrc_epoch *e, uint32_t persisted_limit);
/* 0 and *salt set; or <0 (reservation not persisted, or the 32-bit space is exhausted) and *salt untouched. */
int zrc_epoch_take(struct zrc_epoch *e, zrc_persist_fn persist, void *ctx, uint32_t *salt);

/* Pure core. */
void zrc_iv(uint8_t iv[ZRC_IV_BYTES], uint32_t salt, uint64_t seq);
int zrc_xcrypt_packet(uint32_t salt, uint64_t seq, uint8_t *packet, size_t len);
/* Re-key `count` packets starting at `first_seq` (laid out every `stride` bytes from `packets`) from old_salt to new_salt. */
int zrc_rekey(uint8_t *packets, size_t stride, size_t packet_len, uint64_t first_seq, uint32_t count, uint32_t old_salt,
              uint32_t new_salt);

#endif

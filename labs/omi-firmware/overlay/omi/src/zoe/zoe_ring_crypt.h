/*
 * Zoe phase-2 firmware: the SD ring's audio packets encrypted at rest.
 *
 * AES-128-CTR, one device key (16 random bytes generated on the pendant at first use, kept in the internal-flash settings
 * store, never sent anywhere). Each 444-byte packet is encrypted ONCE, in RAM, when it is appended to the open batch;
 * flushes rewrite the same ciphertext. IV = salt(4) || seq(8) || block(4), big-endian, where salt is a fresh random value
 * per batch (stored in the batch header's reserved0) and seq is the packet's ring sequence number. A batch re-opened after
 * a reboot is re-keyed to a new salt (decrypt, re-encrypt), so a (salt, seq) pair never covers two different plaintexts,
 * even after CLEAR, a truncated tail or a torn write.
 *
 * Batch headers and ring metadata stay plaintext (counts and sequence numbers, no audio). The Pi receives plaintext over
 * the LE Secure Connections link: the BLE protocol is unchanged byte for byte.
 *
 * Split: zoe_ring_crypt.c is pure (host-tested with an OpenSSL backend); zoe_ring_crypt_psa.c is the device backend.
 */
#ifndef ZOE_RING_CRYPT_H
#define ZOE_RING_CRYPT_H

#include <stddef.h>
#include <stdint.h>

#define ZRC_IV_BYTES 16U

/* Backend (device: PSA; host test: OpenSSL). AES-128-CTR over len bytes from the 16-byte initial counter block. */
int zrc_backend_ctr(const uint8_t iv[ZRC_IV_BYTES], const uint8_t *in, uint8_t *out, size_t len);

/* Backend: load or create the device key and make it usable. Idempotent. <0 = no key: nothing may be written; 1 = a NEW
 * key was just created, so whatever the ring holds is unreadable and the caller clears it; 0 = the existing key. */
int zrc_backend_init(void);

/* Backend: a fresh random batch salt (never 0, so an all-zero header from stock firmware is never a valid salt). */
uint32_t zrc_backend_new_salt(void);

/* Pure core. */
void zrc_iv(uint8_t iv[ZRC_IV_BYTES], uint32_t salt, uint64_t seq);
int zrc_xcrypt_packet(uint32_t salt, uint64_t seq, uint8_t *packet, size_t len);
/* Re-key `count` packets starting at `first_seq` (laid out every `stride` bytes from `packets`) from old_salt to new_salt. */
int zrc_rekey(uint8_t *packets, size_t stride, size_t packet_len, uint64_t first_seq, uint32_t count, uint32_t old_salt,
              uint32_t new_salt);

#endif

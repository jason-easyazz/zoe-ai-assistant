/* Zoe phase-2 firmware: the pure half of the ring encryption (no Zephyr includes; host-tested). See zoe_ring_crypt.h. */
#include "zoe_ring_crypt.h"

#include <errno.h>
#include <string.h>

#define ZRC_MAX_PACKET 512U

void zrc_iv(uint8_t iv[ZRC_IV_BYTES], uint32_t salt, uint64_t seq)
{
    for (int i = 0; i < 4; i++) {
        iv[i] = (uint8_t) (salt >> (24 - 8 * i));
    }
    for (int i = 0; i < 8; i++) {
        iv[4 + i] = (uint8_t) (seq >> (56 - 8 * i));
    }
    memset(iv + 12, 0, 4); /* block counter: a packet is at most 32 blocks, so it never carries into seq */
}

int zrc_xcrypt_packet(uint32_t salt, uint64_t seq, uint8_t *packet, size_t len)
{
    /* Not re-entrant, on purpose: every caller (append, read, mount/re-key) runs on the single SD worker thread. */
    static uint8_t scratch[ZRC_MAX_PACKET];
    uint8_t iv[ZRC_IV_BYTES];

    if (salt == 0U || len == 0U || len > ZRC_MAX_PACKET) {
        return -1;
    }
    zrc_iv(iv, salt, seq);
    int rc = zrc_backend_ctr(iv, packet, scratch, len);
    if (rc == 0) {
        memcpy(packet, scratch, len);
    }
    memset(scratch, 0, len);
    return rc;
}

int zrc_rekey(uint8_t *packets, size_t stride, size_t packet_len, uint64_t first_seq, uint32_t count, uint32_t old_salt,
              uint32_t new_salt)
{
    if (old_salt == new_salt) {
        return -1;
    }
    for (uint32_t i = 0; i < count; i++) {
        uint8_t *p = packets + (size_t) i * stride;
        int rc = zrc_xcrypt_packet(old_salt, first_seq + i, p, packet_len);
        if (rc == 0) {
            rc = zrc_xcrypt_packet(new_salt, first_seq + i, p, packet_len);
        }
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

bool zrc_needs_new_key(bool wipe_pending, bool key_found, bool epoch_found, uint32_t epoch)
{
    /* A pending wipe ALWAYS wins: the old key is never loaded again. A key without its salt reservation is never used. */
    return wipe_pending || !key_found || !epoch_found || epoch == 0U;
}

void zrc_epoch_resume(struct zrc_epoch *e, uint32_t persisted_limit)
{
    e->next = persisted_limit > 0U ? persisted_limit : 1U; /* 0 is never a salt (stock headers carry 0) */
    e->limit = e->next;                                   /* nothing reserved yet in this boot */
}

int zrc_epoch_take(struct zrc_epoch *e, zrc_persist_fn persist, void *ctx, uint32_t *salt)
{
    if (e->next == 0U) {
        return -EINVAL;
    }
    if (e->next >= e->limit) {
        if (e->limit > UINT32_MAX - ZRC_EPOCH_BLOCK) {
            return -ENOSPC;
        }
        uint32_t reserve = e->limit + ZRC_EPOCH_BLOCK;
        int rc = persist(reserve, ctx); /* persisted BEFORE any value below it is handed out */
        if (rc != 0) {
            return rc < 0 ? rc : -EIO;
        }
        e->limit = reserve;
    }
    *salt = e->next++;
    return 0;
}

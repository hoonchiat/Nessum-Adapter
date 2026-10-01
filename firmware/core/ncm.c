/* ncm.c - NTB16 parsing (OUT) and building (IN) for CDC-NCM 1.0. */
#include "ncm.h"

#include <string.h>

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void wr32(uint8_t *p, uint32_t v) { wr16(p, (uint16_t)v); wr16(p + 2, (uint16_t)(v >> 16)); }

/* Validate (deliver == 0) or deliver (deliver == 1) all datagrams of one NTB. */
static int walk(const uint8_t *ntb, size_t block, ncm_datagram_fn cb, void *ctx, int deliver)
{
    int count = 0;
    uint16_t ndp = rd16(ntb + 10);
    for (int n = 0; ndp != 0; n++) {
        if (n >= NCM_MAX_NDPS || ndp < NCM_NTH16_LEN || (ndp & 3u) || (size_t)ndp + 8 > block)
            return NCM_E_NDP;
        const uint8_t *d = ntb + ndp;
        uint16_t ndplen = rd16(d + 4);
        if (rd32(d) != NCM_NDP16_SIG || ndplen < 16 || (ndplen & 3u) || (size_t)ndp + ndplen > block)
            return NCM_E_NDP;
        for (size_t off = 8; off + 4 <= ndplen; off += 4) {
            uint16_t idx = rd16(d + off), dlen = rd16(d + off + 2);
            if (idx == 0 || dlen == 0)
                break;   /* terminating entry */
            if (idx < NCM_NTH16_LEN || (size_t)idx + dlen > block || dlen < NCM_ETH_MIN || dlen > NCM_ETH_MAX)
                return NCM_E_DATAGRAM;
            if (deliver)
                cb(ctx, ntb + idx, dlen);
            count++;
        }
        ndp = rd16(d + 6);   /* wNextNdpIndex */
    }
    return count;
}

int ncm_parse(const uint8_t *ntb, size_t len, ncm_datagram_fn cb, void *ctx)
{
    if (len < NCM_NTH16_LEN)
        return NCM_E_SHORT;
    if (rd32(ntb) != NCM_NTH16_SIG || rd16(ntb + 4) != NCM_NTH16_LEN)
        return NCM_E_NTH;
    uint16_t block = rd16(ntb + 8);
    if (block < NCM_NTH16_LEN || block > len)
        return NCM_E_SHORT;
    int r = walk(ntb, block, cb, ctx, 0);   /* validate everything first */
    return r < 0 ? r : walk(ntb, block, cb, ctx, 1);
}

void ncm_tx_init(ncm_tx_t *t, uint8_t *buf, size_t cap, uint16_t seq)
{
    memset(t, 0, sizeof *t);
    t->buf = buf;
    t->cap = cap > 0xFFFFu ? 0xFFFFu : cap;
    t->seq = seq;
    t->pos = NCM_NTH16_LEN;
}

static size_t align4(size_t v) { return (v + 3u) & ~(size_t)3u; }

static size_t ndp_size(int datagrams) { return 8u + 4u * (size_t)(datagrams + 1); }

bool ncm_tx_add(ncm_tx_t *t, const uint8_t *frame, size_t len)
{
    if (len < NCM_ETH_MIN || len > NCM_ETH_MAX || t->count >= NCM_TX_MAX_DATAGRAMS)
        return false;
    size_t at = align4(t->pos);
    size_t end = at + len;
    if (align4(end) + ndp_size(t->count + 1) > t->cap)
        return false;
    memcpy(t->buf + at, frame, len);
    t->index[t->count] = (uint16_t)at;
    t->length[t->count] = (uint16_t)len;
    t->count++;
    t->pos = end;
    return true;
}

size_t ncm_tx_finish(ncm_tx_t *t)
{
    if (t->count == 0)
        return 0;
    size_t ndp = align4(t->pos);
    size_t ndplen = ndp_size(t->count);
    size_t total = ndp + ndplen;
    memset(t->buf + t->pos, 0, ndp - t->pos);   /* alignment padding */
    uint8_t *d = t->buf + ndp;
    wr32(d, NCM_NDP16_SIG);
    wr16(d + 4, (uint16_t)ndplen);
    wr16(d + 6, 0);
    for (int i = 0; i < t->count; i++) {
        wr16(d + 8 + 4 * i, t->index[i]);
        wr16(d + 10 + 4 * i, t->length[i]);
    }
    wr16(d + 8 + 4 * t->count, 0);
    wr16(d + 10 + 4 * t->count, 0);
    wr32(t->buf, NCM_NTH16_SIG);
    wr16(t->buf + 4, NCM_NTH16_LEN);
    wr16(t->buf + 6, t->seq);
    wr16(t->buf + 8, (uint16_t)total);
    wr16(t->buf + 10, (uint16_t)ndp);
    return total;
}

size_t ncm_tx_finish_padded(ncm_tx_t *t, uint16_t mps)
{
    size_t total = ncm_tx_finish(t);
    if (total && mps && total % mps == 0) {
        t->buf[total++] = 0;
        wr16(t->buf + 8, (uint16_t)total);
    }
    return total;
}

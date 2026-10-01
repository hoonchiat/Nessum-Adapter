/* fwimage.c - see fwimage.h. */
#include "fwimage.h"

#include <stdio.h>
#include <string.h>

#include "monocypher-ed25519.h"
#include "util.h"

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

enum fwimg_err fwimg_check_header(const uint8_t *hdr, uint32_t max_payload, uint32_t load_addr,
                                  const uint8_t pubkey[32], fwimg_info_t *info)
{
    if (rd32(hdr) != FWIMG_MAGIC)
        return FWIMG_E_MAGIC;
    if (rd16(hdr + 4) != FWIMG_HDR_SIZE || rd16(hdr + 6) != FWIMG_FORMAT)
        return FWIMG_E_FORMAT;
    uint32_t size = rd32(hdr + 12);
    if (size == 0 || size > max_payload)
        return FWIMG_E_SIZE;
    if (rd32(hdr + 16) != load_addr)
        return FWIMG_E_ADDR;
    /* Signature last: everything above is cheap and rejects garbage early. */
    if (crypto_ed25519_check(hdr + 64, pubkey, hdr, FWIMG_SIGNED_LEN) != 0)
        return FWIMG_E_SIG;
    if (info) {
        info->version = rd32(hdr + 8);
        info->size = size;
        info->load_addr = load_addr;
        memcpy(info->sha256, hdr + 32, 32);
    }
    return FWIMG_OK;
}

enum fwimg_err fwimg_verify(const uint8_t *slot, uint32_t slot_size, uint32_t load_addr,
                            const uint8_t pubkey[32], fwimg_info_t *info)
{
    fwimg_info_t i;
    if (slot_size <= FWIMG_HDR_SIZE)
        return FWIMG_E_SIZE;
    enum fwimg_err e = fwimg_check_header(slot, slot_size - FWIMG_HDR_SIZE, load_addr, pubkey, &i);
    if (e != FWIMG_OK)
        return e;
    uint8_t digest[32];
    sha256(slot + FWIMG_HDR_SIZE, i.size, digest);
    if (memcmp(digest, i.sha256, 32) != 0)
        return FWIMG_E_HASH;
    if (info)
        *info = i;
    return FWIMG_OK;
}

const char *fwimg_strerror(enum fwimg_err e)
{
    switch (e) {
    case FWIMG_OK: return "ok";
    case FWIMG_E_MAGIC: return "not a firmware image";
    case FWIMG_E_FORMAT: return "unknown image format";
    case FWIMG_E_SIZE: return "bad image size";
    case FWIMG_E_ADDR: return "image built for another load address";
    case FWIMG_E_SIG: return "bad signature";
    case FWIMG_E_HASH: return "payload does not match its signed hash";
    }
    return "?";
}

void fwimg_version_str(uint32_t v, char out[16])
{
    snprintf(out, 16, "%u.%u.%u", (unsigned)(v >> 16), (unsigned)(v >> 8 & 0xFF), (unsigned)(v & 0xFF));
}

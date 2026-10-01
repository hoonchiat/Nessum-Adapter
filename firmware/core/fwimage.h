/*
 * fwimage.h - signed firmware images (USB DFU update, bootloader).
 *
 * An image is a 1 KB header followed by the payload (the application binary, linked
 * to run from the active slot right after the header). Header, little-endian:
 *
 *   0   magic "NFW1"
 *   4   header size (0x400)          u16
 *   6   header format (1)            u16
 *   8   firmware version             u32  major << 16 | minor << 8 | patch
 *   12  payload size                 u32
 *   16  load address                 u32  where the payload's vector table must be
 *   20  reserved (0)                 12 bytes
 *   32  SHA-256 of the payload       32 bytes
 *   64  Ed25519 signature            64 bytes, over header bytes 0..63
 *   128 0xFF up to 0x400             (not signed, ignored)
 *
 * The signature covers the payload hash, so checking header + hash checks it all.
 * host/tools/fwimage.py builds and signs images.
 */
#ifndef FWIMAGE_H
#define FWIMAGE_H

#include <stdbool.h>
#include <stdint.h>

#define FWIMG_HDR_SIZE 0x400u
#define FWIMG_SIGNED_LEN 64u   /* header bytes covered by the signature */
#define FWIMG_HDR_MIN 128u     /* header bytes that carry information */
#define FWIMG_MAGIC 0x3157464Eu   /* "NFW1" */
#define FWIMG_FORMAT 1u

enum fwimg_err {
    FWIMG_OK = 0,
    FWIMG_E_MAGIC,    /* not an image (or erased flash) */
    FWIMG_E_FORMAT,   /* unknown header size / format */
    FWIMG_E_SIZE,     /* payload empty or larger than the slot */
    FWIMG_E_ADDR,     /* built for another load address */
    FWIMG_E_SIG,      /* bad signature: not signed with our key */
    FWIMG_E_HASH,     /* payload does not match the signed hash */
};

typedef struct {
    uint32_t version;
    uint32_t size;
    uint32_t load_addr;
    uint8_t sha256[32];
} fwimg_info_t;

/* Check the first FWIMG_HDR_MIN bytes of a header: format, size (<= max_payload),
 * load address and signature. Fills *info when not NULL. */
enum fwimg_err fwimg_check_header(const uint8_t *hdr, uint32_t max_payload, uint32_t load_addr,
                                  const uint8_t pubkey[32], fwimg_info_t *info);

/* Header and payload hash of an image at the start of a slot of slot_size bytes. */
enum fwimg_err fwimg_verify(const uint8_t *slot, uint32_t slot_size, uint32_t load_addr,
                            const uint8_t pubkey[32], fwimg_info_t *info);

const char *fwimg_strerror(enum fwimg_err e);
/* "1.2.3" */
void fwimg_version_str(uint32_t version, char out[16]);

#endif

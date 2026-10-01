/* util.h - CRC-16, SHA-256 and secure zeroing for the core. */
#ifndef UTIL_H
#define UTIL_H

#include <stddef.h>
#include <stdint.h>

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final XOR). */
uint16_t crc16_ccitt(const uint8_t *data, size_t len);

typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t buf[64];
    size_t buflen;
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const uint8_t *data, size_t len);
void sha256_final(sha256_ctx *c, uint8_t out[32]);
void sha256(const uint8_t *data, size_t len, uint8_t out[32]);

/* memset that the compiler may not optimise away (for key material). */
void secure_zero(void *p, size_t len);

#endif

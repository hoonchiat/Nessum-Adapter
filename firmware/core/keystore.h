/*
 * keystore.h - the Nessum network key: write-only, sealed with the chip-unique key
 * (docs/MANAGEMENT.md §2). Nothing in this API returns the key itself.
 *
 * Blob in the reserved flash sector (32 bytes):
 *   0  'N' 'K'   magic        2  version (1)     3  key length (16)
 *   4  fp[8]     first 8 bytes of SHA-256(key) - the fingerprint the tools show
 *   12 sealed[16]  key sealed by plat_seal (RT1062: DCP AES-128, OTP master key)
 *   28 reserved[2]             30 crc16 (LE) over bytes 0..29
 */
#ifndef KEYSTORE_H
#define KEYSTORE_H

#include <stdbool.h>
#include <stdint.h>

#define NKEY_LEN 16u
#define KEYBLOB_SIZE 32u

enum key_err {
    KEY_OK = 0,
    KEY_ERR_STORAGE = 4,   /* write / verify / unseal failed */
    KEY_ERR_IC = 5,        /* stored and verified, but the Nessum IC did not take it */
    KEY_ERR_INVALID = 8,   /* wrong length or all-zero */
};

typedef struct {
    bool set;
    uint8_t fp[8];
} keystore_t;

/* Read the blob header (fingerprint only; does not unseal). */
void keystore_load(keystore_t *k);
/* Unseal and hand the key to the Nessum IC (boot). Key material is wiped after use. */
bool keystore_load_into_nessum(const keystore_t *k);
/* Validate, seal, store, verify (unseal + compare), load into the IC, wipe. */
enum key_err keystore_set(keystore_t *k, const uint8_t *key, uint32_t len);
/* Fingerprint as 16 lowercase hex digits, or "none". */
void keystore_fp_hex(const keystore_t *k, char out[17]);
/* Same fingerprint the host tools compute (first 16 hex of SHA-256). */
void key_fingerprint(const uint8_t *key, uint32_t len, uint8_t fp[8]);

#endif

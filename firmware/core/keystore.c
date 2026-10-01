/* keystore.c - sealed, write-only storage of the Nessum network key. */
#include "keystore.h"

#include <string.h>

#include "platform.h"
#include "util.h"

void key_fingerprint(const uint8_t *key, uint32_t len, uint8_t fp[8])
{
    uint8_t h[32];
    sha256(key, len, h);
    memcpy(fp, h, 8);
    secure_zero(h, sizeof h);
}

static bool blob_header_ok(const uint8_t b[KEYBLOB_SIZE])
{
    if (b[0] != 'N' || b[1] != 'K' || b[2] != 1 || b[3] != NKEY_LEN)
        return false;
    return (uint16_t)(b[30] | b[31] << 8) == crc16_ccitt(b, 30);
}

void keystore_load(keystore_t *k)
{
    uint8_t b[KEYBLOB_SIZE];
    memset(k, 0, sizeof *k);
    if (plat_keyblob_read(b, sizeof b) && blob_header_ok(b)) {
        k->set = true;
        memcpy(k->fp, &b[4], 8);
    }
}

static bool unseal_blob(uint8_t key[NKEY_LEN])
{
    uint8_t b[KEYBLOB_SIZE];
    if (!plat_keyblob_read(b, sizeof b) || !blob_header_ok(b))
        return false;
    bool ok = plat_unseal(&b[12], key, NKEY_LEN);
    secure_zero(b, sizeof b);
    return ok;
}

bool keystore_load_into_nessum(const keystore_t *k)
{
    if (!k->set)
        return false;
    uint8_t key[NKEY_LEN];
    bool ok = unseal_blob(key) && plat_nessum_load_key(key, NKEY_LEN);
    secure_zero(key, sizeof key);
    return ok;
}

enum key_err keystore_set(keystore_t *k, const uint8_t *key, uint32_t len)
{
    static const uint8_t zero[NKEY_LEN] = {0};
    if (len != NKEY_LEN || memcmp(key, zero, NKEY_LEN) == 0)
        return KEY_ERR_INVALID;

    uint8_t b[KEYBLOB_SIZE] = {0}, check[NKEY_LEN];
    b[0] = 'N';
    b[1] = 'K';
    b[2] = 1;
    b[3] = NKEY_LEN;
    key_fingerprint(key, len, &b[4]);
    enum key_err e = KEY_ERR_STORAGE;
    if (plat_seal(key, &b[12], NKEY_LEN)) {
        uint16_t crc = crc16_ccitt(b, 30);
        b[30] = (uint8_t)crc;
        b[31] = (uint8_t)(crc >> 8);
        /* Store, then prove the stored blob unseals back to the same key. */
        if (plat_keyblob_write(b, sizeof b) && unseal_blob(check) && memcmp(check, key, NKEY_LEN) == 0) {
            /* Stored: it is the key from now on (and is loaded at every boot), even
             * if the IC does not take it right now. */
            k->set = true;
            memcpy(k->fp, &b[4], 8);
            e = plat_nessum_load_key(key, NKEY_LEN) ? KEY_OK : KEY_ERR_IC;
        }
    }
    secure_zero(b, sizeof b);
    secure_zero(check, sizeof check);
    return e;
}

void keystore_fp_hex(const keystore_t *k, char out[17])
{
    static const char hex[] = "0123456789abcdef";
    if (!k->set) {
        memcpy(out, "none", 5);
        return;
    }
    for (int i = 0; i < 8; i++) {
        out[2 * i] = hex[k->fp[i] >> 4];
        out[2 * i + 1] = hex[k->fp[i] & 0xf];
    }
    out[16] = '\0';
}

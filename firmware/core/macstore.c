/* macstore.c - MAC selection and the duplicated, CRC-protected EEPROM record. */
#include "macstore.h"

#include <string.h>

#include "platform.h"
#include "util.h"

static const uint8_t MAGIC[2] = {'N', 'A'};

void mac_record_encode(uint8_t rec[MAC_REC_SIZE], uint8_t flags, const uint8_t mac[6])
{
    memset(rec, 0, MAC_REC_SIZE);
    rec[0] = MAGIC[0];
    rec[1] = MAGIC[1];
    rec[2] = 1;
    rec[3] = flags;
    if (mac)
        memcpy(&rec[4], mac, 6);
    uint16_t crc = crc16_ccitt(rec, 14);
    rec[14] = (uint8_t)crc;
    rec[15] = (uint8_t)(crc >> 8);
}

bool mac_record_decode(const uint8_t rec[MAC_REC_SIZE], uint8_t *flags, uint8_t mac[6])
{
    if (rec[0] != MAGIC[0] || rec[1] != MAGIC[1] || rec[2] != 1)
        return false;
    uint16_t crc = (uint16_t)(rec[14] | rec[15] << 8);
    if (crc != crc16_ccitt(rec, 14))
        return false;
    *flags = rec[3];
    memcpy(mac, &rec[4], 6);
    return true;
}

bool mac_is_valid_unicast(const uint8_t mac[6])
{
    static const uint8_t zero[6] = {0}, bcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    if (memcmp(mac, zero, 6) == 0 || memcmp(mac, bcast, 6) == 0)
        return false;
    return (mac[0] & 0x01u) == 0;   /* group bit */
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool mac_parse(const char *s, uint8_t out[6])
{
    size_t n = strlen(s);
    char sep = 0;
    if (n == 17) {
        sep = s[2];
        if (sep != ':' && sep != '-')
            return false;
    } else if (n != 12) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        const char *p = sep ? &s[i * 3] : &s[i * 2];
        if (sep && i < 5 && p[2] != sep)
            return false;
        int hi = hexval(p[0]), lo = hexval(p[1]);
        if (hi < 0 || lo < 0)
            return false;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return true;
}

void mac_format(const uint8_t mac[6], char out[18])
{
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 6; i++) {
        out[i * 3] = hex[mac[i] >> 4];
        out[i * 3 + 1] = hex[mac[i] & 0xf];
        out[i * 3 + 2] = i < 5 ? ':' : '\0';
    }
}

void macstore_apply(macstore_t *m)
{
    m->has_runtime = false;
    if (m->has_programmed) {
        memcpy(m->active, m->programmed, 6);
        m->source = MAC_SRC_PROGRAMMED;
    } else {
        memcpy(m->active, m->def, 6);
        m->source = MAC_SRC_DEFAULT;
    }
}

bool macstore_load(macstore_t *m)
{
    memset(m, 0, sizeof *m);
    bool ok = plat_eeprom_read(EEPROM_EUI48_ADDR, m->def, 6);
    uint8_t rec[MAC_REC_SIZE], flags, mac[6];
    for (uint8_t addr = MAC_REC_ADDR_A;; addr = MAC_REC_ADDR_B) {
        if (plat_eeprom_read(addr, rec, sizeof rec) && mac_record_decode(rec, &flags, mac)) {
            m->locked = (flags & MAC_FLAG_LOCKED) != 0;
            m->has_programmed = (flags & MAC_FLAG_PROGRAMMED) != 0 && mac_is_valid_unicast(mac);
            if (m->has_programmed)
                memcpy(m->programmed, mac, 6);
            break;
        }
        if (addr == MAC_REC_ADDR_B)
            break;
    }
    macstore_apply(m);
    return ok;
}

/* Write the record to copy B then copy A, verifying each by read-back. */
static enum mac_err write_record(uint8_t flags, const uint8_t mac[6])
{
    uint8_t rec[MAC_REC_SIZE], back[MAC_REC_SIZE];
    mac_record_encode(rec, flags, mac);
    static const uint8_t order[2] = {MAC_REC_ADDR_B, MAC_REC_ADDR_A};
    for (int i = 0; i < 2; i++) {
        if (!plat_eeprom_write(order[i], rec, sizeof rec) ||
            !plat_eeprom_read(order[i], back, sizeof back) || memcmp(rec, back, sizeof rec) != 0)
            return MAC_ERR_STORAGE;
    }
    return MAC_OK;
}

enum mac_err macstore_program(macstore_t *m, const uint8_t mac[6])
{
    if (m->locked)
        return MAC_ERR_LOCKED;
    if (!mac_is_valid_unicast(mac))
        return MAC_ERR_INVALID;
    enum mac_err e = write_record(MAC_FLAG_PROGRAMMED, mac);
    if (e == MAC_OK) {
        m->has_programmed = true;
        memcpy(m->programmed, mac, 6);   /* active changes at the next enumeration */
    }
    return e;
}

enum mac_err macstore_clear(macstore_t *m)
{
    if (m->locked)
        return MAC_ERR_LOCKED;
    enum mac_err e = write_record(0, NULL);
    if (e == MAC_OK)
        m->has_programmed = false;
    return e;
}

enum mac_err macstore_lock(macstore_t *m)
{
    if (!m->has_programmed)
        return MAC_ERR_NOTHING;
    if (m->locked)
        return MAC_OK;
    enum mac_err e = write_record(MAC_FLAG_PROGRAMMED | MAC_FLAG_LOCKED, m->programmed);
    if (e == MAC_OK) {
        m->locked = true;
        m->has_runtime = false;
    }
    return e;
}

enum mac_err macstore_set_runtime(macstore_t *m, const uint8_t mac[6])
{
    if (m->locked)
        return MAC_ERR_LOCKED;
    if (!mac_is_valid_unicast(mac))
        return MAC_ERR_INVALID;
    m->has_runtime = true;
    memcpy(m->runtime, mac, 6);
    memcpy(m->active, mac, 6);
    m->source = MAC_SRC_RUNTIME;
    return MAC_OK;
}

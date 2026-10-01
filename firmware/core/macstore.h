/*
 * macstore.h - Ethernet MAC selection and its EEPROM record (docs/MANAGEMENT.md §1).
 *
 * Active MAC = runtime (NCM SET_NET_ADDRESS, RAM only, unlocked units only)
 *            > programmed (EEPROM record) > default (the 24AA02E48's EUI-48).
 *
 * EEPROM record (16 bytes), stored twice at 0x00 (copy A) and 0x10 (copy B):
 *   0  'N' 'A'          magic
 *   2  version (1)
 *   3  flags            bit0 = MAC programmed, bit1 = factory-locked
 *   4  mac[6]
 *   10 reserved[4]      0
 *   14 crc16 (LE)       CRC-16/CCITT-FALSE over bytes 0..13
 * Writes go to copy B first, then A, each verified by read-back, so a power cut
 * always leaves at least one valid copy. Reads prefer A, then B.
 */
#ifndef MACSTORE_H
#define MACSTORE_H

#include <stdbool.h>
#include <stdint.h>

#define MAC_REC_SIZE 16u
#define MAC_REC_ADDR_A 0x00u
#define MAC_REC_ADDR_B 0x10u
#define MAC_FLAG_PROGRAMMED 0x01u
#define MAC_FLAG_LOCKED 0x02u

enum mac_source { MAC_SRC_DEFAULT, MAC_SRC_PROGRAMMED, MAC_SRC_RUNTIME };

enum mac_err {
    MAC_OK = 0,
    MAC_ERR_INVALID = 3,   /* multicast, zero or broadcast (protocol error code 3) */
    MAC_ERR_STORAGE = 4,   /* EEPROM write / verify failed */
    MAC_ERR_LOCKED = 6,    /* factory-locked */
    MAC_ERR_NOTHING = 7,   /* cannot lock: nothing programmed */
};

typedef struct {
    uint8_t def[6];          /* EUI-48 from the EEPROM's read-only half */
    bool has_programmed;
    uint8_t programmed[6];
    bool locked;
    bool has_runtime;
    uint8_t runtime[6];
    uint8_t active[6];       /* what the USB host was given at the last enumeration */
    enum mac_source source;
} macstore_t;

/* Load default + record from the EEPROM and select the active MAC (boot). */
bool macstore_load(macstore_t *m);
/* Recompute the active MAC (called at each USB enumeration; drops the runtime MAC). */
void macstore_apply(macstore_t *m);

enum mac_err macstore_program(macstore_t *m, const uint8_t mac[6]);
enum mac_err macstore_clear(macstore_t *m);
enum mac_err macstore_lock(macstore_t *m);       /* caller checks the key is set too */
/* NCM SET_NET_ADDRESS: takes effect immediately, never stored; refused when locked. */
enum mac_err macstore_set_runtime(macstore_t *m, const uint8_t mac[6]);

bool mac_is_valid_unicast(const uint8_t mac[6]);
/* Accepts xx:xx:xx:xx:xx:xx, xx-xx-..., or 12 hex digits (any case). */
bool mac_parse(const char *s, uint8_t out[6]);
void mac_format(const uint8_t mac[6], char out[18]);   /* lowercase, ':' separated */

/* Record encode/decode (exposed for tests). */
void mac_record_encode(uint8_t rec[MAC_REC_SIZE], uint8_t flags, const uint8_t mac[6]);
bool mac_record_decode(const uint8_t rec[MAC_REC_SIZE], uint8_t *flags, uint8_t mac[6]);

#endif

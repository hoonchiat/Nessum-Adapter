/*
 * host_platform.h - the platform.h implementation for the PC build (fwsim, unit tests).
 *
 * In-memory EEPROM / key-blob flash, optionally persisted to a state directory so
 * a "power cycle" (restarting fwsim) keeps the MAC, key and lock.
 * HOST SIMULATION ONLY: plat_seal() here is NOT secure (see host_platform.c).
 */
#ifndef HOST_PLATFORM_H
#define HOST_PLATFORM_H

#include <stdbool.h>
#include <stdint.h>

#include "platform.h"

typedef struct {
    uint8_t eeprom[256];
    uint8_t keyblob[KEYBLOB_MAX];
    bool keyblob_valid;
    uint8_t nessum_key[16];
    bool nessum_has_key;
    int nessum_key_loads;
    bool fail_eeprom_writes;   /* writes are silently dropped -> verify fails */
    bool fail_keyblob_writes;
    bool nessum_down;
    int reenumerate_requests;
    char serial[40];
    char state_dir[512];       /* "" = no persistence */
} hostsim_t;

extern hostsim_t g_sim;

/* Fresh blank device: EEPROM all 0xFF with the given EUI-48 at 0xFA. */
void hostsim_reset(const uint8_t default_mac[6], const char *serial);
/* Load / save eeprom.bin and keyblob.bin in dir (load returns false if absent). */
bool hostsim_load(const char *dir);
bool hostsim_save(void);

#endif

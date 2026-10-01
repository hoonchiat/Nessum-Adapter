/*
 * platform.h - everything the portable core needs from the hardware.
 *
 * Implemented by platform/rt1062/ (the adapter) and platform/host/ (fwsim, the PC
 * build used for tests and tool conformance). The core never touches registers.
 */
#ifndef PLATFORM_H
#define PLATFORM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- 24AA02E48 EEPROM (256 bytes; 0x00-0x7F writable, 0xFA-0xFF = EUI-48) ---- */
#define EEPROM_EUI48_ADDR 0xFAu
bool plat_eeprom_read(uint8_t addr, uint8_t *buf, size_t len);
/* Handles page boundaries and the write cycle time; returns false on a bus error. */
bool plat_eeprom_write(uint8_t addr, const uint8_t *buf, size_t len);

/* ---- Sealed network-key blob (a reserved QSPI flash sector on the RT1062) ---- */
#define KEYBLOB_MAX 64u
bool plat_keyblob_read(uint8_t *buf, size_t len);
bool plat_keyblob_write(const uint8_t *buf, size_t len);
bool plat_keyblob_erase(void);

/* ---- Sealing with the chip-unique key (RT1062: DCP AES-128 with the OTP master key).
 * len is a multiple of 16. A blob sealed on one chip cannot be unsealed on another. */
bool plat_seal(const uint8_t *in, uint8_t *out, size_t len);
bool plat_unseal(const uint8_t *in, uint8_t *out, size_t len);

/* ---- Nessum IC (SC1320A) over its UART ---- */
#define NESSUM_REPLY_MAX 120u
/* Send one command line; fills up to max_lines reply lines. Returns the number of
 * lines, or -1 if the IC did not answer. */
int plat_nessum_command(const char *cmd, char reply[][NESSUM_REPLY_MAX], int max_lines);
/* Load the network key into the IC (at boot and after NKEY SET). */
bool plat_nessum_load_key(const uint8_t *key, size_t len);
/* Current Nessum link state. */
bool plat_nessum_link(uint32_t *rate_mbps, uint32_t *peers);
const char *plat_nessum_version(void);

/* ---- Identity / control ---- */
const char *plat_serial(void);   /* USB iSerial (RT1062 unique ID) */
const char *plat_hw_rev(void);
/* Disconnect from USB and re-enumerate (after the current reply has been sent). */
void plat_request_reenumerate(void);

#endif

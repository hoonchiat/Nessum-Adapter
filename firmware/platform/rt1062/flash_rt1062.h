/*
 * flash_rt1062.h - the QSPI NOR through the boot ROM's FlexSPI API.
 *
 * Shared by the application (key blob, DFU staging) and the bootloader (install).
 * The code runs in place from this same flash, so no code may be fetched from it
 * during an erase or program: each ROM call runs with interrupts off and the ROM
 * waits for the flash itself. Offsets are from the start of the flash.
 */
#ifndef FLASH_RT1062_H
#define FLASH_RT1062_H

#include <stdbool.h>
#include <stdint.h>

bool flash_init(void);
bool flash_erase_sector(uint32_t offset);   /* 4 KB, aligned */
/* Program into erased flash: offset page-aligned, any length (last page 0xFF-padded). */
bool flash_program(uint32_t offset, const uint8_t *data, uint32_t len);
const uint8_t *flash_map(uint32_t offset);  /* memory-mapped (XIP) view */

#endif

/*
 * board.h - adapter board resources (hardware/schematic.svg, option A).
 *
 * Pin multiplexing and clock trees come from MCUXpresso Config Tools (pin_mux.c,
 * clock_config.c) for the final board layout; board.c only calls them. Pins and
 * peripheral instances below must match that layout - CHECK THEM AGAINST THE PCB.
 */
#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>

#include "fsl_device_registers.h"

#define BOARD_HW_REV "A1"

/* 24AA02E48 on LPI2C1 (7-bit address 0x50, 8-byte pages, 5 ms write cycle) */
#define BOARD_EEPROM_I2C LPI2C1
#define BOARD_EEPROM_ADDR 0x50u
#define BOARD_EEPROM_PAGE 8u
#define BOARD_EEPROM_I2C_HZ 400000u

/* SC1320A command UART and control lines */
#define BOARD_NESSUM_UART LPUART3
#define BOARD_NESSUM_UART_IRQn LPUART3_IRQn
#define BOARD_NESSUM_UART_IRQHandler LPUART3_IRQHandler
#define BOARD_NESSUM_BAUD 115200u   /* TBD from the SC1320A datasheet (S4) */
#define BOARD_NESSUM_RST_GPIO GPIO1
#define BOARD_NESSUM_RST_PIN 18u    /* TBD: PCB */

/* QSPI NOR: the sealed key blob has the last 4 KB sector to itself (outside the
 * image; the update tool never writes it). Assumes an 8 MB part. */
#define BOARD_FLEXSPI_INSTANCE 0u
#define BOARD_FLASH_SIZE (8u * 1024u * 1024u)
#define BOARD_KEYBLOB_OFFSET (BOARD_FLASH_SIZE - 4096u)
#define BOARD_FLEXSPI_AMBA_BASE 0x60000000u

void board_init(void);                 /* MPU, clocks, pins, SysTick */
uint32_t board_millis(void);
void board_delay_ms(uint32_t ms);
uint32_t board_lpi2c_clock_hz(void);
uint32_t board_lpuart_clock_hz(void);
void board_nessum_reset(bool asserted);

#endif

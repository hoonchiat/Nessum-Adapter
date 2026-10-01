/*
 * nessum_uart.h - interrupt-driven LPUART to the SC1320A command interface.
 *
 * RX and TX each have a ring buffer filled / drained by the LPUART interrupt, so no
 * received byte depends on the main loop being on time and nothing here blocks.
 * Single producer / single consumer: the ISR on one side, the main loop on the other.
 */
#ifndef NESSUM_UART_H
#define NESSUM_UART_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t rx_bytes, tx_bytes;
    uint32_t rx_ring_overflow;   /* ring full: byte dropped (main loop too slow) */
    uint32_t rx_hw_overrun;      /* LPUART overrun: interrupt latency too high */
    uint32_t rx_errors;          /* framing / noise / parity */
} nessum_uart_stats_t;

void nessum_uart_init(uint32_t baud, uint32_t src_clock_hz);
/* Queue bytes for sending; all or nothing. False if the TX ring lacks room. */
bool nessum_uart_write(const void *data, size_t len);
/* Next received byte, if any. */
bool nessum_uart_getc(uint8_t *c);
/* Discard anything received so far (stale or unsolicited output). */
void nessum_uart_flush_rx(void);
const nessum_uart_stats_t *nessum_uart_stats(void);

#endif

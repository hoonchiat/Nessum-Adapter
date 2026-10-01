/* nessum_uart.c - see nessum_uart.h. */
#include "nessum_uart.h"

#include <string.h>

#include "board.h"
#include "fsl_lpuart.h"

#define RX_SIZE 512u   /* powers of two: indices wrap with a mask */
#define TX_SIZE 256u

static uint8_t s_rx[RX_SIZE], s_tx[TX_SIZE];
static volatile uint32_t s_rx_head, s_rx_tail;   /* head: ISR writes; tail: main reads */
static volatile uint32_t s_tx_head, s_tx_tail;   /* head: main writes; tail: ISR reads */
static nessum_uart_stats_t s_stats;

#define RX_ERR_FLAGS (kLPUART_FramingErrorFlag | kLPUART_NoiseErrorFlag | kLPUART_ParityErrorFlag)

void BOARD_NESSUM_UART_IRQHandler(void)
{
    LPUART_Type *u = BOARD_NESSUM_UART;
    uint32_t st = LPUART_GetStatusFlags(u);

    if (st & kLPUART_RxOverrunFlag) {          /* the receiver stops until OR is cleared */
        s_stats.rx_hw_overrun++;
        LPUART_ClearStatusFlags(u, kLPUART_RxOverrunFlag);
    }
    if (st & RX_ERR_FLAGS) {
        s_stats.rx_errors++;
        LPUART_ClearStatusFlags(u, RX_ERR_FLAGS);
    }
    while (LPUART_GetStatusFlags(u) & kLPUART_RxDataRegFullFlag) {
        uint8_t c = LPUART_ReadByte(u);
        uint32_t h = s_rx_head;
        if (h - s_rx_tail < RX_SIZE) {
            s_rx[h & (RX_SIZE - 1u)] = c;
            __DMB();
            s_rx_head = h + 1u;
            s_stats.rx_bytes++;
        } else {
            s_stats.rx_ring_overflow++;
        }
    }

    if (LPUART_GetEnabledInterrupts(u) & kLPUART_TxDataRegEmptyInterruptEnable) {
        while (LPUART_GetStatusFlags(u) & kLPUART_TxDataRegEmptyFlag) {
            uint32_t t = s_tx_tail;
            if (t == s_tx_head) {
                LPUART_DisableInterrupts(u, kLPUART_TxDataRegEmptyInterruptEnable);
                break;
            }
            LPUART_WriteByte(u, s_tx[t & (TX_SIZE - 1u)]);
            s_tx[t & (TX_SIZE - 1u)] = 0;   /* commands can carry the network key */
            s_tx_tail = t + 1u;
            s_stats.tx_bytes++;
        }
    }
    SDK_ISR_EXIT_BARRIER;
}

void nessum_uart_init(uint32_t baud, uint32_t src_clock_hz)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = baud;
    cfg.enableTx = true;
    cfg.enableRx = true;
    LPUART_Init(BOARD_NESSUM_UART, &cfg, src_clock_hz);
    s_rx_head = s_rx_tail = s_tx_head = s_tx_tail = 0;
    memset(&s_stats, 0, sizeof s_stats);
    LPUART_EnableInterrupts(BOARD_NESSUM_UART, kLPUART_RxDataRegFullInterruptEnable |
                                                   kLPUART_RxOverrunInterruptEnable);
    EnableIRQ(BOARD_NESSUM_UART_IRQn);
}

bool nessum_uart_write(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint32_t h = s_tx_head;
    if (len > TX_SIZE - (h - s_tx_tail))
        return false;
    for (size_t i = 0; i < len; i++)
        s_tx[(h + i) & (TX_SIZE - 1u)] = p[i];
    __DMB();
    s_tx_head = h + (uint32_t)len;
    /* (Re-)arm the TX interrupt; the ISR disables it once the ring is empty. If the ISR
     * runs inside this read-modify-write of CTRL, TIE just ends up set once more and
     * the next interrupt finds the ring empty and clears it. */
    LPUART_EnableInterrupts(BOARD_NESSUM_UART, kLPUART_TxDataRegEmptyInterruptEnable);
    return true;
}

bool nessum_uart_getc(uint8_t *c)
{
    uint32_t t = s_rx_tail;
    if (t == s_rx_head)
        return false;
    __DMB();
    *c = s_rx[t & (RX_SIZE - 1u)];
    s_rx_tail = t + 1u;
    return true;
}

void nessum_uart_flush_rx(void) { s_rx_tail = s_rx_head; }

const nessum_uart_stats_t *nessum_uart_stats(void) { return &s_stats; }

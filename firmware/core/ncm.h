/*
 * ncm.h - CDC-NCM 1.0 NTB16 transfer blocks (USB <-> Ethernet frames).
 *
 * OUT (host -> adapter): ncm_parse() walks NTH16 -> NDP16 chain -> datagrams and
 * hands each Ethernet frame to a callback. Everything is bounds-checked: the input
 * comes from the USB host.
 * IN (adapter -> host): ncm_tx_* packs several frames into one NTB16 with one NDP16
 * at the end, as the Linux cdc_ncm driver expects.
 */
#ifndef NCM_H
#define NCM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define NCM_NTH16_SIG 0x484D434Eu   /* "NCMH" */
#define NCM_NDP16_SIG 0x304D434Eu   /* "NCM0" (no CRC) */
#define NCM_NTH16_LEN 12u
#define NCM_ETH_MIN 14u             /* Ethernet header */
#define NCM_ETH_MAX 1518u           /* wMaxSegmentSize: 1514 + 802.1Q tag */
#define NCM_MAX_NDPS 4               /* NDP chain limit (loop protection) */
#define NCM_TX_MAX_DATAGRAMS 32

enum ncm_err {
    NCM_E_SHORT = -1,     /* shorter than an NTH16 / block length */
    NCM_E_NTH = -2,       /* bad NTH16 signature / header length */
    NCM_E_NDP = -3,       /* bad NDP16 index / signature / length */
    NCM_E_DATAGRAM = -4,  /* datagram outside the block or bad size */
};

typedef void (*ncm_datagram_fn)(void *ctx, const uint8_t *frame, size_t len);

/* Returns the number of datagrams delivered, or a negative ncm_err. Nothing is
 * delivered unless the whole NTB validates. */
int ncm_parse(const uint8_t *ntb, size_t len, ncm_datagram_fn cb, void *ctx);

typedef struct {
    uint8_t *buf;
    size_t cap;
    size_t pos;            /* next free byte for datagram data */
    uint16_t seq;
    int count;
    uint16_t index[NCM_TX_MAX_DATAGRAMS];
    uint16_t length[NCM_TX_MAX_DATAGRAMS];
} ncm_tx_t;

void ncm_tx_init(ncm_tx_t *t, uint8_t *buf, size_t cap, uint16_t seq);
/* Append a frame; false if it doesn't fit (send the NTB, start a new one). */
bool ncm_tx_add(ncm_tx_t *t, const uint8_t *frame, size_t len);
/* Write NTH16 and NDP16; returns the NTB length (0 if empty). */
size_t ncm_tx_finish(ncm_tx_t *t);
/* As ncm_tx_finish(), but the length is never a multiple of mps: a bulk IN transfer
 * ending on a packet boundary would need a zero-length packet, so one pad byte is
 * added (and counted in wBlockLength) instead. The buffer needs cap + 1 bytes. */
size_t ncm_tx_finish_padded(ncm_tx_t *t, uint16_t mps);

#endif

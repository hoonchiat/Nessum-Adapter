/*
 * usb_ncm.h - the CDC-NCM function (Linux cdc_ncm -> eth2).
 *
 * A TinyUSB application class driver. NTB framing is core/ncm.c (unit-tested on the
 * host); this layer does the USB side: class requests, endpoints, notifications.
 */
#ifndef USB_NCM_H
#define USB_NCM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "macstore.h"

#define NCM_NTB_OUT_MAX 8192u   /* dwNtbOutMaxSize: host -> adapter */
#define NCM_NTB_IN_MAX 8192u    /* dwNtbInMaxSize: adapter -> host */

typedef struct {
    uint32_t ntb_out, ntb_out_bad;   /* NTBs from the host, and rejected ones */
    uint32_t frames_out;             /* frames handed to the ENET */
    uint32_t ntb_in, frames_in;      /* NTBs / frames sent to the host */
} ncm_dev_stats_t;

/* Call once before tusb_init(). The MAC store answers GET/SET_NET_ADDRESS. */
void ncm_dev_bind(macstore_t *macs);

/* Nessum link state -> NETWORK_CONNECTION / CONNECTION_SPEED_CHANGE. */
void ncm_dev_set_link(bool up, uint32_t bits_per_second);

/* True while the host has the data interface up (alt 1); otherwise frames from the
 * Nessum side are dropped. */
bool ncm_dev_active(void);

/* A frame (14..1518 bytes) from the Nessum side for the host. Returns false if it
 * can't be taken now (both NTB buffers busy): keep it and retry. */
bool ncm_dev_to_host(const uint8_t *frame, size_t len);

/* Provided by the bridge: a frame from the host for the ENET. Returns false if the
 * frame was dropped. Called from tud_task(). */
bool ncm_dev_from_host(const uint8_t *frame, size_t len);

const ncm_dev_stats_t *ncm_dev_stats(void);

#endif

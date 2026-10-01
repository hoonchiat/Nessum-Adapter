/*
 * usb_ncm.c - CDC-NCM 1.0 function as a TinyUSB application class driver.
 *
 * Interfaces (usb_descriptors.c): communication interface with the notification
 * endpoint, then the data interface, alt 0 (no endpoints) / alt 1 (bulk IN + OUT).
 *
 * OUT: one NTB buffer. Each NTB is validated by ncm_parse() before any frame goes to
 *      the ENET, and the endpoint is re-armed only after its frames are handed over,
 *      so a busy ENET throttles the host (back-pressure, no unbounded queue).
 * IN:  two NTB buffers. A frame is sent at once if the endpoint is idle; while a
 *      transfer is in flight, frames gather in the other buffer (aggregation without
 *      a timer). NTB lengths never fall on a packet boundary (ncm_tx_finish_padded),
 *      so no zero-length packets are needed.
 */
#include <string.h>

#include "tusb.h"
#include "device/usbd_pvt.h"

#include "ncm.h"
#include "usb_ncm.h"

/* CDC-NCM 1.0, table 6-2 */
enum {
    REQ_SET_ETHERNET_PACKET_FILTER = 0x43,
    REQ_GET_NTB_PARAMETERS = 0x80,
    REQ_GET_NET_ADDRESS = 0x81,
    REQ_SET_NET_ADDRESS = 0x82,
};
/* CDC 1.2 notifications */
enum { NOTIF_NETWORK_CONNECTION = 0x00, NOTIF_CONNECTION_SPEED_CHANGE = 0x2A };
enum { PEND_SPEED = 1, PEND_CONN = 2 };

typedef struct TU_ATTR_PACKED {
    uint16_t wLength;
    uint16_t bmNtbFormatsSupported;
    uint32_t dwNtbInMaxSize;
    uint16_t wNdpInDivisor;
    uint16_t wNdpInPayloadRemainder;
    uint16_t wNdpInAlignment;
    uint16_t wReserved;
    uint32_t dwNtbOutMaxSize;
    uint16_t wNdpOutDivisor;
    uint16_t wNdpOutPayloadRemainder;
    uint16_t wNdpOutAlignment;
    uint16_t wNtbOutMaxDatagrams;
} ntb_params_t;
TU_VERIFY_STATIC(sizeof(ntb_params_t) == 28, "NTB parameter structure");

static const ntb_params_t k_ntb_params = {
    .wLength = sizeof(ntb_params_t),
    .bmNtbFormatsSupported = 0x0001,   /* NTB16 only */
    .dwNtbInMaxSize = NCM_NTB_IN_MAX,
    .wNdpInDivisor = 4,
    .wNdpInPayloadRemainder = 0,
    .wNdpInAlignment = 4,
    .dwNtbOutMaxSize = NCM_NTB_OUT_MAX,
    .wNdpOutDivisor = 4,
    .wNdpOutPayloadRemainder = 0,
    .wNdpOutAlignment = 4,
    .wNtbOutMaxDatagrams = 0,          /* no limit */
};

/* USB DMA buffers (non-cacheable section, see tusb_config.h). */
CFG_TUD_MEM_SECTION CFG_TUD_MEM_ALIGN static uint8_t s_out[NCM_NTB_OUT_MAX];
CFG_TUD_MEM_SECTION CFG_TUD_MEM_ALIGN static uint8_t s_in[2][NCM_NTB_IN_MAX + 1];
CFG_TUD_MEM_SECTION CFG_TUD_MEM_ALIGN static uint8_t s_notif[16];
CFG_TUD_MEM_SECTION CFG_TUD_MEM_ALIGN static uint8_t s_ctrl[sizeof(ntb_params_t)];

static struct {
    uint8_t rhport, itf, ep_notif, ep_in, ep_out, alt;
    uint16_t mps;
    bool in_busy;
    uint8_t fill;           /* s_in buffer being filled */
    uint16_t seq;
    ncm_tx_t tx;
    uint8_t pending;        /* notifications still to send */
    uint16_t packet_filter;
} s;

static macstore_t *s_macs;
static bool s_link_up;
static uint32_t s_link_bps;
static ncm_dev_stats_t s_stats;

void ncm_dev_bind(macstore_t *macs) { s_macs = macs; }
bool ncm_dev_active(void) { return s.alt == 1; }
const ncm_dev_stats_t *ncm_dev_stats(void) { return &s_stats; }

/* ---------------------------------------------------------------- notifications */
static void notif_kick(void)
{
    if (s.alt != 1 || !s.pending || usbd_edpt_busy(s.rhport, s.ep_notif))
        return;
    tusb_control_request_t h = {
        .bmRequestType = 0xA1,   /* IN, class, interface */
        .wIndex = s.itf,
    };
    uint16_t len = sizeof h;
    /* Speed first, so the host has it before the link comes up. */
    if ((s.pending & PEND_SPEED) && s_link_up) {
        h.bRequest = NOTIF_CONNECTION_SPEED_CHANGE;
        h.wLength = 8;
        memcpy(s_notif, &h, sizeof h);
        memcpy(s_notif + 8, &s_link_bps, 4);    /* DLBitRate */
        memcpy(s_notif + 12, &s_link_bps, 4);   /* ULBitRate */
        len += 8;
        s.pending &= (uint8_t)~PEND_SPEED;
    } else {
        h.bRequest = NOTIF_NETWORK_CONNECTION;
        h.wValue = s_link_up ? 1 : 0;
        memcpy(s_notif, &h, sizeof h);
        s.pending = 0;
    }
    usbd_edpt_xfer(s.rhport, s.ep_notif, s_notif, len, false);
}

void ncm_dev_set_link(bool up, uint32_t bits_per_second)
{
    if (up == s_link_up && bits_per_second == s_link_bps)
        return;
    s_link_up = up;
    s_link_bps = bits_per_second;
    s.pending |= PEND_SPEED | PEND_CONN;
    notif_kick();
}

/* ---------------------------------------------------------------- data */
static void out_arm(void)
{
    if (s.alt == 1 && !usbd_edpt_busy(s.rhport, s.ep_out))
        usbd_edpt_xfer(s.rhport, s.ep_out, s_out, sizeof s_out, false);
}

static void tx_start_new(void)
{
    /* cap leaves the spare byte ncm_tx_finish_padded() may need */
    ncm_tx_init(&s.tx, s_in[s.fill], NCM_NTB_IN_MAX, s.seq++);
}

static void in_flush(void)
{
    if (s.in_busy || s.tx.count == 0)
        return;
    size_t len = ncm_tx_finish_padded(&s.tx, s.mps);
    s_stats.ntb_in++;
    s_stats.frames_in += (uint32_t)s.tx.count;
    s.in_busy = usbd_edpt_xfer(s.rhport, s.ep_in, s_in[s.fill], (uint16_t)len, false);
    s.fill ^= 1u;
    tx_start_new();
}

bool ncm_dev_to_host(const uint8_t *frame, size_t len)
{
    if (s.alt != 1)
        return false;
    if (!ncm_tx_add(&s.tx, frame, len)) {
        if (s.in_busy || s.tx.count == 0)   /* both buffers busy (or a bad length) */
            return false;
        in_flush();
        if (s.in_busy || !ncm_tx_add(&s.tx, frame, len))
            return false;
    }
    in_flush();   /* no-op while a transfer is in flight: the frame waits for it */
    return true;
}

static void deliver(void *ctx, const uint8_t *frame, size_t len)
{
    (void)ctx;
    if (ncm_dev_from_host(frame, len))
        s_stats.frames_out++;
}

/* ---------------------------------------------------------------- class driver */
static void ncm_init(void)
{
    memset(&s, 0, sizeof s);
}

static bool ncm_deinit(void)
{
    return true;
}

/* Every enumeration starts with a bus reset: re-select the MAC (this drops a runtime
 * one) before the host reads iMACAddress. */
static void ncm_reset(uint8_t rhport)
{
    (void)rhport;
    memset(&s, 0, sizeof s);
    if (s_macs)
        macstore_apply(s_macs);
}

static uint16_t ncm_open(uint8_t rhport, tusb_desc_interface_t const *itf, uint16_t max_len)
{
    /* Only the NCM communication interface; anything else goes to TinyUSB's drivers. */
    if (itf->bInterfaceClass != TUSB_CLASS_CDC || itf->bInterfaceSubClass != CDC_COMM_SUBCLASS_NETWORK_CONTROL_MODEL)
        return 0;
    TU_ASSERT(s.ep_notif == 0, 0);
    s.rhport = rhport;
    s.itf = itf->bInterfaceNumber;

    uint16_t len = sizeof(tusb_desc_interface_t);
    uint8_t const *p = tu_desc_next(itf);
    while (len < max_len && tu_desc_type(p) == TUSB_DESC_CS_INTERFACE) {
        len += tu_desc_len(p);
        p = tu_desc_next(p);
    }
    TU_ASSERT(tu_desc_type(p) == TUSB_DESC_ENDPOINT, 0);
    TU_ASSERT(usbd_edpt_open(rhport, (tusb_desc_endpoint_t const *)p), 0);
    s.ep_notif = ((tusb_desc_endpoint_t const *)p)->bEndpointAddress;
    len += tu_desc_len(p);
    p = tu_desc_next(p);

    /* data interface: alt 0 and alt 1 */
    while (len < max_len && tu_desc_type(p) == TUSB_DESC_INTERFACE) {
        TU_ASSERT(((tusb_desc_interface_t const *)p)->bInterfaceClass == TUSB_CLASS_CDC_DATA, 0);
        len += tu_desc_len(p);
        p = tu_desc_next(p);
    }
    TU_ASSERT(tu_desc_type(p) == TUSB_DESC_ENDPOINT, 0);
    TU_ASSERT(usbd_open_edpt_pair(rhport, p, 2, TUSB_XFER_BULK, &s.ep_out, &s.ep_in), 0);
    s.mps = tu_edpt_packet_size((tusb_desc_endpoint_t const *)p);
    len += 2 * sizeof(tusb_desc_endpoint_t);
    tx_start_new();
    return len;
}

static bool ncm_control(uint8_t rhport, uint8_t stage, tusb_control_request_t const *req)
{
    if (req->bmRequestType_bit.type == TUSB_REQ_TYPE_STANDARD) {
        if (stage != CONTROL_STAGE_SETUP)
            return true;
        if (req->wIndex != s.itf + 1u)
            return false;
        if (req->bRequest == TUSB_REQ_GET_INTERFACE)
            return tud_control_xfer(rhport, req, &s.alt, 1);
        if (req->bRequest != TUSB_REQ_SET_INTERFACE || req->wValue > 1)
            return false;
        s.alt = (uint8_t)req->wValue;
        if (s.alt == 1) {
            s.in_busy = false;
            s.fill = 0;
            tx_start_new();
            out_arm();
            s.pending = PEND_SPEED | PEND_CONN;
            notif_kick();
        }
        return tud_control_status(rhport, req);
    }

    if (req->bmRequestType_bit.type != TUSB_REQ_TYPE_CLASS || req->wIndex != s.itf)
        return false;

    switch (req->bRequest) {
    case REQ_GET_NTB_PARAMETERS:
        if (stage == CONTROL_STAGE_SETUP) {
            memcpy(s_ctrl, &k_ntb_params, sizeof k_ntb_params);
            return tud_control_xfer(rhport, req, s_ctrl, (uint16_t)tu_min16(req->wLength, sizeof k_ntb_params));
        }
        return true;
    case REQ_GET_NET_ADDRESS:
        if (stage == CONTROL_STAGE_SETUP) {
            memcpy(s_ctrl, s_macs->active, 6);
            return tud_control_xfer(rhport, req, s_ctrl, (uint16_t)tu_min16(req->wLength, 6));
        }
        return true;
    case REQ_SET_NET_ADDRESS:
        /* Runtime only (RAM); STALLed on a factory-locked unit. */
        if (stage == CONTROL_STAGE_SETUP)
            return req->wLength == 6 && !s_macs->locked && tud_control_xfer(rhport, req, s_ctrl, 6);
        if (stage == CONTROL_STAGE_DATA)
            return macstore_set_runtime(s_macs, s_ctrl) == MAC_OK;
        return true;
    case REQ_SET_ETHERNET_PACKET_FILTER:
        /* Recorded only: the ENET runs promiscuous and Linux filters. */
        if (stage == CONTROL_STAGE_SETUP) {
            s.packet_filter = req->wValue;
            return tud_control_status(rhport, req);
        }
        return true;
    default:
        return false;   /* STALL: CRC mode, NTB32, multicast filters... not supported */
    }
}

static bool ncm_xfer(uint8_t rhport, uint8_t ep, xfer_result_t result, uint32_t n)
{
    (void)rhport;
    if (ep == s.ep_out) {
        if (result == XFER_RESULT_SUCCESS) {
            s_stats.ntb_out++;
            if (ncm_parse(s_out, n, deliver, NULL) < 0)
                s_stats.ntb_out_bad++;
        }
        out_arm();
    } else if (ep == s.ep_in) {
        s.in_busy = false;
        in_flush();   /* frames gathered meanwhile */
    } else if (ep == s.ep_notif) {
        notif_kick();
    }
    return true;
}

static const usbd_class_driver_t k_ncm_driver = {
    .name = "NCM",
    .init = ncm_init,
    .deinit = ncm_deinit,
    .reset = ncm_reset,
    .open = ncm_open,
    .control_xfer_cb = ncm_control,
    .xfer_cb = ncm_xfer,
    .xfer_isr = NULL,
    .sof = NULL,
};

usbd_class_driver_t const *usbd_app_driver_get_cb(uint8_t *count)
{
    *count = 1;
    return &k_ncm_driver;
}

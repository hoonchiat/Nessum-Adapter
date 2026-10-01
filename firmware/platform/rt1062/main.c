/*
 * main.c - option A adapter firmware: USB (CDC-NCM + CDC-ACM) <-> ENET/RMII <-> SC1320A.
 *
 * Bare-metal superloop. TinyUSB events, the ENET receive ring, the console and the
 * Nessum link poll are all serviced here and none of them blocks. Interrupt context:
 * USB (TinyUSB's event queue), the Nessum LPUART (byte rings) and SysTick.
 */
#include "tusb.h"

#include "board.h"
#include "enet_bridge.h"
#include "keystore.h"
#include "macstore.h"
#include "mgmt.h"
#include "plat_rt1062.h"
#include "platform.h"
#include "usb_descriptors.h"
#include "usb_dfu.h"
#include "usb_ncm.h"

#define CONSOLE_ITF 0
#define CONSOLE_WAIT_MS 100u

static macstore_t s_macs;
static keystore_t s_keys;
static mgmt_t s_mgmt;

void USB_OTG1_IRQHandler(void) { tusb_int_handler(0, true); }

static void console_write(void *ctx, const char *data, size_t len)
{
    (void)ctx;
    uint32_t t0 = board_millis();
    while (len && tud_cdc_n_connected(CONSOLE_ITF) && board_millis() - t0 < CONSOLE_WAIT_MS) {
        uint32_t n = tud_cdc_n_write(CONSOLE_ITF, data, (uint32_t)len);
        data += n;
        len -= n;
        if (len) {
            tud_cdc_n_write_flush(CONSOLE_ITF);
            tud_task();
        }
    }
    tud_cdc_n_write_flush(CONSOLE_ITF);
}

static void console_poll(void)
{
    uint8_t buf[64];
    while (tud_cdc_n_available(CONSOLE_ITF)) {
        uint32_t n = tud_cdc_n_read(CONSOLE_ITF, buf, sizeof buf);
        mgmt_rx(&s_mgmt, buf, n);
    }
}

static void link_poll(void)
{
    bool up;
    uint32_t bps;
    if (plat_rt1062_link_poll(&up, &bps))
        ncm_dev_set_link(up, bps);
}

/* While a synchronous Nessum command waits (console NESSUM, key load): keep the USB
 * stack and the data path running. Not the console: it may be the caller. */
void plat_rt1062_idle(void)
{
    tud_task();
    enet_bridge_poll();
}

/* REBOOT: let the reply go out, then drop off the bus and come back, so Linux
 * re-enumerates and reads the (possibly new) MAC. */
static void reenumerate_poll(void)
{
    if (!plat_rt1062_take_reenumerate())
        return;
    uint32_t t0 = board_millis();
    while (board_millis() - t0 < 50u)
        tud_task();
    tud_disconnect();
    board_delay_ms(200);
    macstore_apply(&s_macs);
    tud_connect();
}

int main(void)
{
    board_init();
    plat_rt1062_init();

    macstore_load(&s_macs);
    keystore_load(&s_keys);
    keystore_load_into_nessum(&s_keys);
    mgmt_init(&s_mgmt, &s_macs, &s_keys, console_write, NULL);

    usb_dfu_init();
    ncm_dev_bind(&s_macs);
    usb_descriptors_bind(s_macs.active);
    enet_bridge_init(s_macs.active);

    const tusb_rhport_init_t dev = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_HIGH};
    tusb_init(0, &dev);

    for (;;) {
        tud_task();
        enet_bridge_poll();
        console_poll();
        link_poll();
        reenumerate_poll();
        usb_dfu_poll();
    }
}

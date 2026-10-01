/* usb_dfu.c - see usb_dfu.h. */
#include "usb_dfu.h"

#include "tusb.h"

#include "board.h"
#include "fwupdate.h"

#define REBOOT_DELAY_MS 300u   /* let the host read the final status first */

extern const uint8_t fw_signing_pubkey[32];   /* generated: host/tools/fwimage.py pubkey-c */

static fwup_t s_up;
static uint32_t s_running;
static bool s_reboot;
static uint32_t s_reboot_at;

void usb_dfu_init(void)
{
    s_running = fw_running_version(fw_signing_pubkey);
    fwup_init(&s_up, fw_signing_pubkey, s_running);
}

uint32_t usb_dfu_running_version(void) { return s_running; }

void usb_dfu_poll(void)
{
    if (s_reboot && board_millis() - s_reboot_at >= REBOOT_DELAY_MS)
        NVIC_SystemReset();   /* the bootloader installs the staged image */
}

static uint8_t dfu_status(enum fwup_err e)
{
    switch (e) {
    case FWUP_OK: return DFU_STATUS_OK;
    case FWUP_E_IMAGE: return s_up.image_err == FWIMG_E_ADDR ? DFU_STATUS_ERR_TARGET : DFU_STATUS_ERR_FILE;
    case FWUP_E_HEADER:
    case FWUP_E_OLD: return DFU_STATUS_ERR_FILE;
    case FWUP_E_ORDER:
    case FWUP_E_SIZE: return DFU_STATUS_ERR_ADDRESS;
    case FWUP_E_FLASH: return DFU_STATUS_ERR_WRITE;
    case FWUP_E_VERIFY: return DFU_STATUS_ERR_VERIFY;
    }
    return DFU_STATUS_ERR_UNKNOWN;
}

/* bwPollTimeout: how long the host waits before asking again. A block costs one
 * sector erase (typ. 45 ms) plus 16 page programs. */
uint32_t tud_dfu_get_timeout_cb(uint8_t alt, uint8_t state)
{
    (void)alt;
    if (state == DFU_DNBUSY)
        return 100;
    if (state == DFU_MANIFEST)
        return 300;   /* hashing up to 1 MB of flash */
    return 0;
}

void tud_dfu_download_cb(uint8_t alt, uint16_t block_num, uint8_t const *data, uint16_t length)
{
    (void)alt;
    uint32_t offset = (uint32_t)block_num * CFG_TUD_DFU_XFER_BUFSIZE;
    tud_dfu_finish_flashing(dfu_status(fwup_write(&s_up, offset, data, length)));
}

void tud_dfu_manifest_cb(uint8_t alt)
{
    (void)alt;
    enum fwup_err e = fwup_finish(&s_up);
    tud_dfu_finish_flashing(dfu_status(e));
    if (e == FWUP_OK) {
        s_reboot = true;
        s_reboot_at = board_millis();
    }
}

void tud_dfu_abort_cb(uint8_t alt)
{
    (void)alt;
    fwup_abort(&s_up);
}

/*
 * usb_dfu.h - USB DFU 1.1 firmware download (interface 4, dfu-util compatible).
 *
 * Signed images only (core/fwupdate.c): the download goes to the staging slot, is
 * verified at the end, and the adapter then resets so the bootloader installs it.
 * Allowed on factory-locked units too: an image must be signed with the production
 * key, which is the protection. No upload (read-back) of the firmware.
 */
#ifndef USB_DFU_H
#define USB_DFU_H

#include <stdint.h>

void usb_dfu_init(void);
/* Main loop: resets the adapter shortly after a successful download. */
void usb_dfu_poll(void);
/* Version of the running (active-slot) image, 0 if unsigned / debugger-loaded. */
uint32_t usb_dfu_running_version(void);

#endif

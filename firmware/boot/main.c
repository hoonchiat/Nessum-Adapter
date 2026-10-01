/*
 * boot/main.c - the bootloader: first 64 KB of the QSPI flash, never updated by DFU.
 *
 * 1. If the staging slot holds a newer signed image (or the active slot none),
 *    copy it over the active slot (core/fwboot.c; power-cut safe: it just repeats).
 * 2. Verify the active image (Ed25519 signature + SHA-256) and start it.
 * 3. Nothing valid: hand over to the boot ROM's serial downloader (USB HID / UART)
 *    so the factory tooling can recover the unit. On a HAB-closed part that only
 *    accepts signed images too.
 *
 * In production this image is itself HAB-signed, so the chain is: ROM (HAB) ->
 * bootloader -> Ed25519-verified application.
 */
#include "board.h"
#include "flash_rt1062.h"
#include "fsl_romapi.h"
#include "fwboot.h"
#include "platform.h"

extern const uint8_t fw_signing_pubkey[32];   /* generated: host/tools/fwimage.py pubkey-c */

#define INSTALL_TRIES 3
#define ROM_SERIAL_DOWNLOADER 0xEB100000u   /* tag 0xEB, boot mode 1 = serial downloader, auto interface */

static void start(uint32_t vtor)
{
    const volatile uint32_t *vt = (const volatile uint32_t *)vtor;
    __disable_irq();
    SysTick->CTRL = 0;
    for (unsigned i = 0; i < sizeof NVIC->ICER / sizeof NVIC->ICER[0]; i++) {
        NVIC->ICER[i] = 0xFFFFFFFFu;
        NVIC->ICPR[i] = 0xFFFFFFFFu;
    }
    SCB_DisableDCache();   /* cleans it; the application's SystemInit sets caches up again */
    SCB->VTOR = vtor;
    __DSB();
    __ISB();
    __set_MSP(vt[0]);
    ((void (*)(void))vt[1])();   /* the application's Reset_Handler (sets VTOR/MSP again) */
    for (;;) {
    }
}

int main(void)
{
    if (flash_init()) {
        for (int i = 0; i < INSTALL_TRIES; i++) {
            fwimg_info_t img;
            enum fwboot_result r = fwboot_run(fw_signing_pubkey, &img);
            if (r == FWBOOT_RUN || r == FWBOOT_INSTALLED)
                start(img.load_addr);
            if (r == FWBOOT_NO_IMAGE)
                break;
            /* FWBOOT_FAILED: flash error during the copy; staging is intact, try again */
        }
    }
    uint32_t arg = ROM_SERIAL_DOWNLOADER;
    ROM_RunBootloader(&arg);
    for (;;) {
    }
}

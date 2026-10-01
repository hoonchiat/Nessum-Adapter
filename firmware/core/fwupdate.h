/*
 * fwupdate.h - receive a firmware image into the staging slot (USB DFU download).
 *
 * Blocks arrive in order. The first one must hold the image header, which is
 * checked at once: signature, load address, size, and a version newer than the
 * running one (no downgrades). Sectors are erased as the data reaches them, and
 * every write is read back. fwup_finish() verifies the whole staged image; the
 * bootloader installs it at the next reset (core/fwboot.h).
 */
#ifndef FWUPDATE_H
#define FWUPDATE_H

#include <stdbool.h>
#include <stdint.h>

#include "fwimage.h"

enum fwup_err {
    FWUP_OK = 0,
    FWUP_E_ORDER,     /* block out of sequence / no transfer in progress */
    FWUP_E_HEADER,    /* first block too short to hold the header */
    FWUP_E_IMAGE,     /* header rejected: see fwup_t.image_err */
    FWUP_E_OLD,       /* version not newer than the running firmware */
    FWUP_E_SIZE,      /* more data than the header announced / incomplete */
    FWUP_E_FLASH,     /* erase / program / read-back failed */
    FWUP_E_VERIFY,    /* staged image fails verification: see image_err */
};

typedef struct {
    const uint8_t *pubkey;
    uint32_t running_version;
    bool active;              /* a transfer is in progress */
    uint32_t written;         /* bytes received so far */
    uint32_t erased_to;       /* staging bytes [0, erased_to) are erased */
    uint32_t total;           /* header + payload, from the header */
    fwimg_info_t info;
    enum fwimg_err image_err;
} fwup_t;

void fwup_init(fwup_t *u, const uint8_t pubkey[32], uint32_t running_version);
/* Data at byte offset `offset` of the image. Any error ends the transfer. */
enum fwup_err fwup_write(fwup_t *u, uint32_t offset, const uint8_t *data, uint32_t len);
/* End of download: the staged image must be complete and verify. */
enum fwup_err fwup_finish(fwup_t *u);
void fwup_abort(fwup_t *u);
const char *fwup_strerror(enum fwup_err e);

/* Version of the image in the active slot, 0 if it has none (e.g. debugger-loaded). */
uint32_t fw_running_version(const uint8_t pubkey[32]);

#endif

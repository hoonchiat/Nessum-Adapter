/*
 * fwboot.h - the bootloader's install step.
 *
 * Install rule: the staging slot holds a valid signed image AND (the active slot has
 * none OR the staged version is newer). Then staging is copied over active and the
 * result verified. No flags are kept: if power fails mid-copy, the active slot no
 * longer verifies while staging still does, so the next boot simply copies again.
 * After a good copy both slots hold the same version and nothing more happens.
 */
#ifndef FWBOOT_H
#define FWBOOT_H

#include <stdint.h>

#include "fwimage.h"

enum fwboot_result {
    FWBOOT_RUN,          /* active slot verified: start it */
    FWBOOT_INSTALLED,    /* staged image copied and verified: start it */
    FWBOOT_NO_IMAGE,     /* nothing valid to run */
    FWBOOT_FAILED,       /* copy failed (flash error); staging intact, retry at next boot */
};

/* On FWBOOT_RUN / FWBOOT_INSTALLED, *active describes the image to start. */
enum fwboot_result fwboot_run(const uint8_t pubkey[32], fwimg_info_t *active);

#endif

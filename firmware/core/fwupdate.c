/* fwupdate.c - see fwupdate.h. */
#include "fwupdate.h"

#include <string.h>

#include "platform.h"

void fwup_init(fwup_t *u, const uint8_t pubkey[32], uint32_t running_version)
{
    memset(u, 0, sizeof *u);
    u->pubkey = pubkey;
    u->running_version = running_version;
}

void fwup_abort(fwup_t *u)
{
    u->active = false;
}

static enum fwup_err fail(fwup_t *u, enum fwup_err e)
{
    u->active = false;
    return e;
}

/* Program [off, off+len) of the staging slot, erasing sectors first as needed. */
static bool stage(fwup_t *u, uint32_t off, const uint8_t *data, uint32_t len)
{
    while (len) {
        while (u->erased_to < off + 1u) {   /* the sector holding off */
            if (!plat_slot_erase(FW_SLOT_STAGING, u->erased_to))
                return false;
            u->erased_to += FW_SECTOR_SIZE;
        }
        /* up to the end of this sector, in one program call */
        uint32_t room = u->erased_to - off;
        uint32_t n = len < room ? len : room;
        if (!plat_slot_program(FW_SLOT_STAGING, off, data, n) ||
            memcmp(plat_slot_map(FW_SLOT_STAGING) + off, data, n) != 0)
            return false;
        off += n;
        data += n;
        len -= n;
    }
    return true;
}

enum fwup_err fwup_write(fwup_t *u, uint32_t offset, const uint8_t *data, uint32_t len)
{
    if (offset == 0) {   /* a new transfer (a restarted one starts over) */
        u->active = false;
        u->written = u->erased_to = u->total = 0;
        if (len < FWIMG_HDR_MIN)
            return FWUP_E_HEADER;
        u->image_err = fwimg_check_header(data, FW_SLOT_SIZE - FWIMG_HDR_SIZE, FW_ACTIVE_LOAD_ADDR, u->pubkey,
                                          &u->info);
        if (u->image_err != FWIMG_OK)
            return FWUP_E_IMAGE;
        if (u->info.version <= u->running_version)
            return FWUP_E_OLD;
        u->total = FWIMG_HDR_SIZE + u->info.size;
        u->active = true;
    }
    if (!u->active || offset != u->written || offset % FW_PAGE_SIZE)
        return fail(u, FWUP_E_ORDER);
    if (len > u->total - u->written)
        return fail(u, FWUP_E_SIZE);
    if (!stage(u, offset, data, len))
        return fail(u, FWUP_E_FLASH);
    u->written += len;
    return FWUP_OK;
}

enum fwup_err fwup_finish(fwup_t *u)
{
    if (!u->active)
        return FWUP_E_ORDER;
    u->active = false;
    if (u->written != u->total)
        return FWUP_E_SIZE;
    u->image_err = fwimg_verify(plat_slot_map(FW_SLOT_STAGING), FW_SLOT_SIZE, FW_ACTIVE_LOAD_ADDR, u->pubkey, NULL);
    return u->image_err == FWIMG_OK ? FWUP_OK : FWUP_E_VERIFY;
}

const char *fwup_strerror(enum fwup_err e)
{
    switch (e) {
    case FWUP_OK: return "ok";
    case FWUP_E_ORDER: return "block out of sequence";
    case FWUP_E_HEADER: return "first block shorter than the image header";
    case FWUP_E_IMAGE: return "image header rejected";
    case FWUP_E_OLD: return "not newer than the running firmware";
    case FWUP_E_SIZE: return "image size mismatch";
    case FWUP_E_FLASH: return "flash write failed";
    case FWUP_E_VERIFY: return "staged image failed verification";
    }
    return "?";
}

uint32_t fw_running_version(const uint8_t pubkey[32])
{
    fwimg_info_t i;
    return fwimg_check_header(plat_slot_map(FW_SLOT_ACTIVE), FW_SLOT_SIZE - FWIMG_HDR_SIZE, FW_ACTIVE_LOAD_ADDR,
                              pubkey, &i) == FWIMG_OK
               ? i.version
               : 0;
}

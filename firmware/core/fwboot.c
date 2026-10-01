/* fwboot.c - see fwboot.h. */
#include "fwboot.h"

#include <string.h>

#include "platform.h"

static enum fwimg_err check(fw_slot_t s, const uint8_t pubkey[32], fwimg_info_t *i)
{
    return fwimg_verify(plat_slot_map(s), FW_SLOT_SIZE, FW_ACTIVE_LOAD_ADDR, pubkey, i);
}

static bool copy_staging(uint32_t len)
{
    const uint8_t *src = plat_slot_map(FW_SLOT_STAGING);
    for (uint32_t off = 0; off < len; off += FW_SECTOR_SIZE) {
        uint32_t n = len - off < FW_SECTOR_SIZE ? len - off : FW_SECTOR_SIZE;
        if (!plat_slot_erase(FW_SLOT_ACTIVE, off) || !plat_slot_program(FW_SLOT_ACTIVE, off, src + off, n) ||
            memcmp(plat_slot_map(FW_SLOT_ACTIVE) + off, src + off, n) != 0)
            return false;
    }
    return true;
}

enum fwboot_result fwboot_run(const uint8_t pubkey[32], fwimg_info_t *active)
{
    fwimg_info_t a, b;
    bool a_ok = check(FW_SLOT_ACTIVE, pubkey, &a) == FWIMG_OK;
    bool b_ok = check(FW_SLOT_STAGING, pubkey, &b) == FWIMG_OK;

    if (b_ok && (!a_ok || b.version > a.version)) {
        if (!copy_staging(FWIMG_HDR_SIZE + b.size) || check(FW_SLOT_ACTIVE, pubkey, active) != FWIMG_OK)
            return FWBOOT_FAILED;
        return FWBOOT_INSTALLED;
    }
    if (!a_ok)
        return FWBOOT_NO_IMAGE;
    *active = a;
    return FWBOOT_RUN;
}

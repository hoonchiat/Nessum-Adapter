/* flash_rt1062.c - see flash_rt1062.h. Also implements the platform.h slot API. */
#include "flash_rt1062.h"

#include <string.h>

#include "board.h"
#include "fsl_cache.h"
#include "fsl_romapi.h"
#include "platform.h"
#include "util.h"

static flexspi_nor_config_t s_nor;
static bool s_ok;

bool flash_init(void)
{
    /* QuadSPI NOR, SFDP-probed, 133 MHz: the option word the boot ROM uses for
     * common QSPI parts. Re-initialises the XIP interface, hence interrupts off. */
    serial_nor_config_option_t opt = {.option0 = {.U = 0xC0000007u}, .option1 = {.U = 0}};
    uint32_t primask = DisableGlobalIRQ();
    s_ok = ROM_FLEXSPI_NorFlash_GetConfig(BOARD_FLEXSPI_INSTANCE, &s_nor, &opt) == kStatus_Success &&
           ROM_FLEXSPI_NorFlash_Init(BOARD_FLEXSPI_INSTANCE, &s_nor) == kStatus_Success;
    EnableGlobalIRQ(primask);
    return s_ok;
}

/* After a change: drop stale lines from the FlexSPI prefetch buffer and the D-cache. */
static void invalidate(uint32_t offset, uint32_t len)
{
    ROM_FLEXSPI_NorFlash_ClearCache(BOARD_FLEXSPI_INSTANCE);
    DCACHE_InvalidateByRange(BOARD_FLEXSPI_AMBA_BASE + offset, len);
}

bool flash_erase_sector(uint32_t offset)
{
    if (!s_ok || offset % FW_SECTOR_SIZE || offset >= BOARD_FLASH_SIZE)
        return false;
    uint32_t primask = DisableGlobalIRQ();
    status_t st = ROM_FLEXSPI_NorFlash_Erase(BOARD_FLEXSPI_INSTANCE, &s_nor, offset, FW_SECTOR_SIZE);
    invalidate(offset, FW_SECTOR_SIZE);
    EnableGlobalIRQ(primask);
    return st == kStatus_Success;
}

bool flash_program(uint32_t offset, const uint8_t *data, uint32_t len)
{
    static uint32_t page[FW_PAGE_SIZE / 4];   /* word-aligned for the ROM */
    if (!s_ok || offset % FW_PAGE_SIZE || offset + len > BOARD_FLASH_SIZE)
        return false;
    bool ok = true;
    for (uint32_t done = 0; ok && done < len; done += FW_PAGE_SIZE) {
        uint32_t n = len - done < FW_PAGE_SIZE ? len - done : FW_PAGE_SIZE;
        memset(page, 0xFF, sizeof page);
        memcpy(page, data + done, n);
        uint32_t primask = DisableGlobalIRQ();   /* one page at a time: short IRQ-off windows */
        ok = ROM_FLEXSPI_NorFlash_ProgramPage(BOARD_FLEXSPI_INSTANCE, &s_nor, offset + done, page) == kStatus_Success;
        invalidate(offset + done, FW_PAGE_SIZE);
        EnableGlobalIRQ(primask);
    }
    secure_zero(page, sizeof page);   /* the key blob passes through here */
    return ok;
}

const uint8_t *flash_map(uint32_t offset) { return (const uint8_t *)(BOARD_FLEXSPI_AMBA_BASE + offset); }

/* ---- platform.h firmware slots ---- */
static uint32_t slot_base(fw_slot_t s) { return s == FW_SLOT_ACTIVE ? BOARD_FW_ACTIVE_OFFSET : BOARD_FW_STAGING_OFFSET; }

const uint8_t *plat_slot_map(fw_slot_t slot) { return flash_map(slot_base(slot)); }

bool plat_slot_erase(fw_slot_t slot, uint32_t off)
{
    return off < FW_SLOT_SIZE && flash_erase_sector(slot_base(slot) + off);
}

bool plat_slot_program(fw_slot_t slot, uint32_t off, const uint8_t *data, uint32_t len)
{
    return len <= FW_SECTOR_SIZE && off + len <= FW_SLOT_SIZE && flash_program(slot_base(slot) + off, data, len);
}

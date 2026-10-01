/*
 * plat_rt1062.c - platform.h on the i.MX RT1062 (option A adapter).
 *
 *   EEPROM    24AA02E48 on LPI2C (page writes + acknowledge polling)
 *   key blob  last QSPI sector, via the boot ROM's FlexSPI NOR API
 *   sealing   DCP AES-128 with the OTP master key (OTPMK) - see "Sealing" below
 *   Nessum    SC1320A command UART (LPUART) and reset line
 *
 * The SC1320A command set is not yet known (docs/SPECIFICATION.md §9, S4). The
 * line protocol below - "<cmd>\r\n", reply lines ending in "OK" or "ERR ..." - and
 * the KEY / STATUS / VERSION commands are PLACEHOLDERS to replace once it is.
 */
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "fsl_cache.h"
#include "fsl_dcp.h"
#include "fsl_iomuxc.h"
#include "fsl_lpi2c.h"
#include "fsl_lpuart.h"
#include "fsl_romapi.h"
#include "platform.h"
#include "plat_rt1062.h"
#include "util.h"

static flexspi_nor_config_t s_nor;
static bool s_nor_ok;
static char s_serial[17];
static char s_nessum_version[32] = "unknown";
static volatile bool s_reenumerate;

/* ---------------------------------------------------------------- EEPROM */
static status_t eeprom_xfer(lpi2c_direction_t dir, uint8_t addr, uint8_t *buf, size_t len)
{
    lpi2c_master_transfer_t x = {
        .flags = kLPI2C_TransferDefaultFlag,
        .slaveAddress = BOARD_EEPROM_ADDR,
        .direction = dir,
        .subaddress = addr,
        .subaddressSize = 1,
        .data = buf,
        .dataSize = len,
    };
    return LPI2C_MasterTransferBlocking(BOARD_EEPROM_I2C, &x);
}

bool plat_eeprom_read(uint8_t addr, uint8_t *buf, size_t len)
{
    if ((size_t)addr + len > 256u)
        return false;
    return eeprom_xfer(kLPI2C_Read, addr, buf, len) == kStatus_Success;
}

/* The internal write cycle (max 5 ms) NAKs the address: poll until it ACKs. */
static bool eeprom_wait_ready(void)
{
    uint32_t t0 = board_millis();
    do {
        lpi2c_master_transfer_t probe = {
            .flags = kLPI2C_TransferDefaultFlag,
            .slaveAddress = BOARD_EEPROM_ADDR,
            .direction = kLPI2C_Write,
        };
        if (LPI2C_MasterTransferBlocking(BOARD_EEPROM_I2C, &probe) == kStatus_Success)
            return true;
    } while (board_millis() - t0 < 10u);
    return false;
}

bool plat_eeprom_write(uint8_t addr, const uint8_t *buf, size_t len)
{
    if ((size_t)addr + len > 0x80u)   /* upper half is the write-protected EUI-48 block */
        return false;
    while (len) {
        size_t n = BOARD_EEPROM_PAGE - (addr % BOARD_EEPROM_PAGE);   /* stay inside the page */
        if (n > len)
            n = len;
        uint8_t page[BOARD_EEPROM_PAGE];
        memcpy(page, buf, n);
        if (eeprom_xfer(kLPI2C_Write, addr, page, n) != kStatus_Success || !eeprom_wait_ready())
            return false;
        addr = (uint8_t)(addr + n);
        buf += n;
        len -= n;
    }
    return true;
}

/* ---------------------------------------------------------------- key blob in QSPI */
/* The image runs XIP from this flash: no code may be fetched from it while it is
 * erased or programmed, so interrupts are off and only ROM code runs meanwhile. */
static bool flash_op(bool erase, const uint32_t *page)
{
    if (!s_nor_ok)
        return false;
    uint32_t primask = DisableGlobalIRQ();
    status_t st = erase ? ROM_FLEXSPI_NorFlash_Erase(BOARD_FLEXSPI_INSTANCE, &s_nor, BOARD_KEYBLOB_OFFSET, 4096u)
                        : ROM_FLEXSPI_NorFlash_ProgramPage(BOARD_FLEXSPI_INSTANCE, &s_nor, BOARD_KEYBLOB_OFFSET, page);
    ROM_FLEXSPI_NorFlash_ClearCache(BOARD_FLEXSPI_INSTANCE);
    EnableGlobalIRQ(primask);
    DCACHE_InvalidateByRange(BOARD_FLEXSPI_AMBA_BASE + BOARD_KEYBLOB_OFFSET, 4096u);
    return st == kStatus_Success;
}

bool plat_keyblob_read(uint8_t *buf, size_t len)
{
    if (len > KEYBLOB_MAX)
        return false;
    memcpy(buf, (const void *)(BOARD_FLEXSPI_AMBA_BASE + BOARD_KEYBLOB_OFFSET), len);
    return true;   /* an erased sector reads 0xFF: keystore rejects it by magic/CRC */
}

bool plat_keyblob_write(const uint8_t *buf, size_t len)
{
    static uint32_t page[256 / 4];   /* one NOR page, word aligned for the ROM */
    if (len > KEYBLOB_MAX)
        return false;
    memset(page, 0xFF, sizeof page);
    memcpy(page, buf, len);
    bool ok = flash_op(true, NULL) && flash_op(false, page);
    secure_zero(page, sizeof page);
    return ok;
}

bool plat_keyblob_erase(void) { return flash_op(true, NULL); }

/* ---------------------------------------------------------------- sealing
 * AES-128 with the DCP's OTP key slot = the low 128 bits of the OTPMK fuses, which
 * software can never read. Factory requirements (docs/MANAGEMENT.md §2):
 *   - burn a random OTPMK per unit and set its read/write locks;
 *   - close HAB (secure boot), so the SNVS releases the OTPMK to the DCP.
 * Until then OTP_KEY_READY stays clear, sealing fails and NKEY SET reports ERR 4:
 * a unit can never store the key under a known or missing key. The blob is one
 * 16-byte AES block, so ECB leaks nothing. */
static bool dcp_crypt(bool encrypt, const uint8_t *in, uint8_t *out, size_t len)
{
    if (len == 0 || len % 16u || len > 32u)
        return false;
    SDK_ALIGN(static uint8_t src[32], 4);
    SDK_ALIGN(static uint8_t dst[32], 4);
    dcp_handle_t h = {.channel = kDCP_Channel0, .keySlot = kDCP_OtpKey, .swapConfig = kDCP_NoSwap};
    IOMUXC_GPR->GPR3 &= ~IOMUXC_GPR_GPR3_DCP_KEY_SEL_MASK;   /* OTPMK bits 127:0 */
    memcpy(src, in, len);
    bool ok = DCP_AES_SetKey(DCP, &h, NULL, 16) == kStatus_Success &&
              (encrypt ? DCP_AES_EncryptEcb(DCP, &h, src, dst, len) : DCP_AES_DecryptEcb(DCP, &h, src, dst, len)) ==
                  kStatus_Success;
    if (ok)
        memcpy(out, dst, len);
    secure_zero(src, sizeof src);
    secure_zero(dst, sizeof dst);
    return ok;
}

bool plat_seal(const uint8_t *in, uint8_t *out, size_t len) { return dcp_crypt(true, in, out, len); }
bool plat_unseal(const uint8_t *in, uint8_t *out, size_t len) { return dcp_crypt(false, in, out, len); }

/* ---------------------------------------------------------------- Nessum IC (PLACEHOLDER protocol) */
static bool uart_getc(uint8_t *c, uint32_t deadline)
{
    while ((int32_t)(deadline - board_millis()) > 0) {
        if (LPUART_GetStatusFlags(BOARD_NESSUM_UART) & kLPUART_RxDataRegFullFlag) {
            *c = LPUART_ReadByte(BOARD_NESSUM_UART);
            return true;
        }
    }
    return false;
}

int plat_nessum_command(const char *cmd, char reply[][NESSUM_REPLY_MAX], int max_lines)
{
    LPUART_ClearStatusFlags(BOARD_NESSUM_UART, kLPUART_RxOverrunFlag);
    if (LPUART_WriteBlocking(BOARD_NESSUM_UART, (const uint8_t *)cmd, strlen(cmd)) != kStatus_Success ||
        LPUART_WriteBlocking(BOARD_NESSUM_UART, (const uint8_t *)"\r\n", 2) != kStatus_Success)
        return -1;
    uint32_t deadline = board_millis() + 500u;
    char line[NESSUM_REPLY_MAX];
    size_t n = 0;
    int lines = 0;
    uint8_t c;
    while (uart_getc(&c, deadline)) {
        if (c == '\r')
            continue;
        if (c != '\n') {
            if (n < sizeof line - 1)
                line[n++] = (char)c;
            continue;
        }
        line[n] = '\0';
        n = 0;
        if (!strcmp(line, "OK"))
            return lines;
        if (lines < max_lines)
            snprintf(reply[lines++], NESSUM_REPLY_MAX, "%s", line);
        if (!strncmp(line, "ERR", 3))
            return lines;
    }
    return -1;   /* no complete answer in time */
}

bool plat_nessum_load_key(const uint8_t *key, size_t len)
{
    static const char hex[] = "0123456789abcdef";
    char cmd[8 + 2 * 32 + 1] = "KEY SET ";
    char reply[1][NESSUM_REPLY_MAX];
    if (len > 32)
        return false;
    for (size_t i = 0; i < len; i++) {
        cmd[8 + 2 * i] = hex[key[i] >> 4];
        cmd[9 + 2 * i] = hex[key[i] & 15];
    }
    cmd[8 + 2 * len] = '\0';
    int r = plat_nessum_command(cmd, reply, 1);
    secure_zero(cmd, sizeof cmd);
    return r == 0;   /* bare "OK" */
}

bool plat_nessum_link(uint32_t *rate_mbps, uint32_t *peers)
{
    char reply[2][NESSUM_REPLY_MAX];
    unsigned rate = 0, n = 0;
    char state[8] = "";
    *rate_mbps = 0;
    *peers = 0;
    if (plat_nessum_command("STATUS", reply, 2) < 1 ||
        sscanf(reply[0], "link=%7s rate=%u peers=%u", state, &rate, &n) != 3)
        return false;
    *rate_mbps = rate;
    *peers = n;
    return !strcmp(state, "up");
}

const char *plat_nessum_version(void) { return s_nessum_version; }

/* ---------------------------------------------------------------- identity / control */
const char *plat_serial(void) { return s_serial; }
const char *plat_hw_rev(void) { return BOARD_HW_REV; }
void plat_request_reenumerate(void) { s_reenumerate = true; }

bool plat_rt1062_take_reenumerate(void)
{
    bool r = s_reenumerate;
    s_reenumerate = false;
    return r;
}

void plat_rt1062_init(void)
{
    /* serial = the 64-bit unique ID fused by NXP */
    snprintf(s_serial, sizeof s_serial, "%08lX%08lX", (unsigned long)OCOTP->CFG1, (unsigned long)OCOTP->CFG0);

    lpi2c_master_config_t i2c;
    LPI2C_MasterGetDefaultConfig(&i2c);
    i2c.baudRate_Hz = BOARD_EEPROM_I2C_HZ;
    LPI2C_MasterInit(BOARD_EEPROM_I2C, &i2c, board_lpi2c_clock_hz());

    lpuart_config_t uart;
    LPUART_GetDefaultConfig(&uart);
    uart.baudRate_Bps = BOARD_NESSUM_BAUD;
    uart.enableTx = true;
    uart.enableRx = true;
    LPUART_Init(BOARD_NESSUM_UART, &uart, board_lpuart_clock_hz());

    dcp_config_t dcp;
    DCP_GetDefaultConfig(&dcp);
    DCP_Init(DCP, &dcp);

    /* FlexSPI NOR parameters for the ROM API: QuadSPI NOR, SFDP-probed, 133 MHz
     * (the same option word the boot ROM uses for common QSPI parts). */
    serial_nor_config_option_t opt = {.option0 = {.U = 0xC0000007u}, .option1 = {.U = 0}};
    uint32_t primask = DisableGlobalIRQ();   /* re-initialises the XIP flash interface */
    s_nor_ok = ROM_FLEXSPI_NorFlash_GetConfig(BOARD_FLEXSPI_INSTANCE, &s_nor, &opt) == kStatus_Success &&
               ROM_FLEXSPI_NorFlash_Init(BOARD_FLEXSPI_INSTANCE, &s_nor) == kStatus_Success;
    EnableGlobalIRQ(primask);

    /* Release the Nessum IC and ask for its version (PLACEHOLDER command). */
    board_nessum_reset(true);
    board_delay_ms(10);
    board_nessum_reset(false);
    board_delay_ms(200);
    char reply[1][NESSUM_REPLY_MAX];
    if (plat_nessum_command("VERSION", reply, 1) == 1) {
        /* one token: it goes into "nessum=<version>" on the console */
        size_t i = 0;
        for (; i < sizeof s_nessum_version - 1 && reply[0][i]; i++)
            s_nessum_version[i] = reply[0][i] == ' ' ? '_' : reply[0][i];
        s_nessum_version[i] = '\0';
    }
}

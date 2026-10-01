/*
 * plat_rt1062.c - platform.h on the i.MX RT1062 (option A adapter).
 *
 *   EEPROM    24AA02E48 on LPI2C (page writes + acknowledge polling)
 *   key blob  last QSPI sector (flash_rt1062.c: the boot ROM's FlexSPI NOR API)
 *   sealing   DCP AES-128 with the OTP master key (OTPMK) - see "Sealing" below
 *   Nessum    SC1320A command UART (nessum_uart.c, interrupt-driven) and reset line
 *
 * The SC1320A command set is not yet known (docs/SPECIFICATION.md §9, S4). The
 * line protocol below - "<cmd>\r\n", reply lines ending in "OK" or "ERR ..." - and
 * the KEY / STATUS / VERSION commands are PLACEHOLDERS to replace once it is.
 */
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "fsl_dcp.h"
#include "fsl_iomuxc.h"
#include "fsl_lpi2c.h"
#include "flash_rt1062.h"
#include "nessum_uart.h"
#include "nline.h"
#include "platform.h"
#include "plat_rt1062.h"
#include "util.h"

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
bool plat_keyblob_read(uint8_t *buf, size_t len)
{
    if (len > KEYBLOB_MAX)
        return false;
    memcpy(buf, flash_map(BOARD_KEYBLOB_OFFSET), len);
    return true;   /* an erased sector reads 0xFF: keystore rejects it by magic/CRC */
}

bool plat_keyblob_write(const uint8_t *buf, size_t len)
{
    return len <= KEYBLOB_MAX && flash_erase_sector(BOARD_KEYBLOB_OFFSET) && flash_program(BOARD_KEYBLOB_OFFSET, buf, len);
}

bool plat_keyblob_erase(void) { return flash_erase_sector(BOARD_KEYBLOB_OFFSET); }

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

/* ---------------------------------------------------------------- Nessum IC (PLACEHOLDER protocol)
 * One transaction at a time on the UART: either the background link poll (driven by
 * plat_rt1062_link_poll from the main loop, never waits) or a synchronous command
 * (console NESSUM / STATUS, key load, version). A synchronous command keeps USB and
 * the ENET serviced through plat_rt1062_idle() while it waits for the reply. */
#define NESSUM_TIMEOUT_MS 500u
#define LINK_POLL_MS 500u

static nline_t s_nl;
static enum { OWNER_NONE, OWNER_LINK, OWNER_SYNC } s_owner;

static bool nessum_begin(const char *cmd)
{
    char line[8 + 2 * 32 + 3];   /* the longest command: KEY SET <64 hex> */
    size_t n = strlen(cmd);
    if (n + 2 > sizeof line)
        return false;
    memcpy(line, cmd, n);
    line[n++] = '\r';
    line[n++] = '\n';
    nessum_uart_flush_rx();   /* drop stale or unsolicited output */
    bool ok = nessum_uart_write(line, n);
    secure_zero(line, sizeof line);
    if (ok)
        nline_start(&s_nl, board_millis(), NESSUM_TIMEOUT_MS);
    return ok;
}

static nl_state_t nessum_pump(void)
{
    uint8_t c;
    while (nessum_uart_getc(&c))
        nline_rx(&s_nl, c);
    return nline_poll(&s_nl, board_millis());
}

int plat_nessum_command(const char *cmd, char reply[][NESSUM_REPLY_MAX], int max_lines)
{
    /* Let a background link poll finish first; its result is dropped (it retries). */
    while (s_owner == OWNER_LINK && nessum_pump() == NL_BUSY)
        plat_rt1062_idle();
    s_owner = OWNER_NONE;
    if (!nessum_begin(cmd))
        return -1;
    s_owner = OWNER_SYNC;
    nl_state_t st;
    while ((st = nessum_pump()) == NL_BUSY)
        plat_rt1062_idle();
    s_owner = OWNER_NONE;

    int lines = -1;   /* no complete answer in time */
    if (st == NL_OK || st == NL_ERR) {
        lines = s_nl.count < max_lines ? s_nl.count : max_lines;
        for (int i = 0; i < lines; i++)
            memcpy(reply[i], s_nl.lines[i], NESSUM_REPLY_MAX);
    }
    nline_reset(&s_nl);
    return lines;
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

/* "link=<up|down> rate=<Mbit/s> peers=<n>" */
static bool parse_status(const char *line, bool *up, uint32_t *rate_mbps, uint32_t *peers)
{
    unsigned rate = 0, n = 0;
    char state[8] = "";
    if (sscanf(line, "link=%7s rate=%u peers=%u", state, &rate, &n) != 3)
        return false;
    *up = !strcmp(state, "up");
    *rate_mbps = rate;
    *peers = n;
    return true;
}

bool plat_nessum_link(uint32_t *rate_mbps, uint32_t *peers)
{
    char reply[2][NESSUM_REPLY_MAX];
    bool up = false;
    *rate_mbps = 0;
    *peers = 0;
    return plat_nessum_command("STATUS", reply, 2) >= 1 && parse_status(reply[0], &up, rate_mbps, peers) && up;
}

bool plat_rt1062_link_poll(bool *up, uint32_t *bps)
{
    static uint32_t last, interval = LINK_POLL_MS;
    if (s_owner == OWNER_LINK) {
        nl_state_t st = nessum_pump();
        if (st == NL_BUSY)
            return false;
        s_owner = OWNER_NONE;
        bool link = false;
        uint32_t rate = 0, peers = 0;
        bool answered = st == NL_OK && s_nl.count >= 1 && parse_status(s_nl.lines[0], &link, &rate, &peers);
        nline_reset(&s_nl);
        interval = answered ? LINK_POLL_MS : 10u * LINK_POLL_MS;   /* back off while the IC is silent */
        *up = answered && link && peers > 0;
        *bps = *up ? rate * 1000000u : 0;
        return true;
    }
    if (s_owner != OWNER_NONE || board_millis() - last < interval)
        return false;
    last = board_millis();
    if (nessum_begin("STATUS"))
        s_owner = OWNER_LINK;
    return false;
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

    nessum_uart_init(BOARD_NESSUM_BAUD, board_lpuart_clock_hz());

    dcp_config_t dcp;
    DCP_GetDefaultConfig(&dcp);
    DCP_Init(DCP, &dcp);

    flash_init();

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

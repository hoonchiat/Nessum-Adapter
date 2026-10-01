/* host_platform.c - platform.h for the PC: simulated EEPROM, flash, sealing and IC. */
#include "host_platform.h"

#include <stdio.h>
#include <string.h>

#include "util.h"

hostsim_t g_sim;
uint8_t g_slots[2][FW_SLOT_SIZE];

void hostsim_reset(const uint8_t default_mac[6], const char *serial)
{
    char dir[sizeof g_sim.state_dir];
    memcpy(dir, g_sim.state_dir, sizeof dir);
    memset(&g_sim, 0, sizeof g_sim);
    memcpy(g_sim.state_dir, dir, sizeof dir);
    memset(g_sim.eeprom, 0xFF, sizeof g_sim.eeprom);
    memcpy(&g_sim.eeprom[EEPROM_EUI48_ADDR], default_mac, 6);
    snprintf(g_sim.serial, sizeof g_sim.serial, "%s", serial);
    g_sim.slot_ops_left = -1;
    memset(g_slots, 0xFF, sizeof g_slots);
}

static bool path_in_state(char *out, size_t n, const char *name)
{
    return g_sim.state_dir[0] && (size_t)snprintf(out, n, "%s/%s", g_sim.state_dir, name) < n;
}

bool hostsim_load(const char *dir)
{
    char p[600];
    snprintf(g_sim.state_dir, sizeof g_sim.state_dir, "%s", dir);
    FILE *f;
    bool any = false;
    if (path_in_state(p, sizeof p, "eeprom.bin") && (f = fopen(p, "rb"))) {
        any = fread(g_sim.eeprom, 1, sizeof g_sim.eeprom, f) == sizeof g_sim.eeprom;
        fclose(f);
    }
    if (path_in_state(p, sizeof p, "keyblob.bin") && (f = fopen(p, "rb"))) {
        g_sim.keyblob_valid = fread(g_sim.keyblob, 1, sizeof g_sim.keyblob, f) == sizeof g_sim.keyblob;
        fclose(f);
    }
    return any;
}

bool hostsim_save(void)
{
    char p[600];
    FILE *f;
    if (!g_sim.state_dir[0])
        return true;
    bool ok = path_in_state(p, sizeof p, "eeprom.bin") && (f = fopen(p, "wb")) &&
              fwrite(g_sim.eeprom, 1, sizeof g_sim.eeprom, f) == sizeof g_sim.eeprom && fclose(f) == 0;
    if (ok && path_in_state(p, sizeof p, "keyblob.bin")) {
        if (g_sim.keyblob_valid) {
            ok = (f = fopen(p, "wb")) && fwrite(g_sim.keyblob, 1, sizeof g_sim.keyblob, f) == sizeof g_sim.keyblob &&
                 fclose(f) == 0;
        } else {
            remove(p);
        }
    }
    return ok;
}

/* ---- EEPROM: the 24AA02E48's upper half (0x80-0xFF) is write-protected ---- */
bool plat_eeprom_read(uint8_t addr, uint8_t *buf, size_t len)
{
    if ((size_t)addr + len > sizeof g_sim.eeprom)
        return false;
    memcpy(buf, &g_sim.eeprom[addr], len);
    return true;
}

bool plat_eeprom_write(uint8_t addr, const uint8_t *buf, size_t len)
{
    if ((size_t)addr + len > 0x80u)
        return false;
    if (!g_sim.fail_eeprom_writes)
        memcpy(&g_sim.eeprom[addr], buf, len);
    return hostsim_save();
}

/* ---- key-blob flash sector ---- */
bool plat_keyblob_read(uint8_t *buf, size_t len)
{
    if (!g_sim.keyblob_valid || len > sizeof g_sim.keyblob)
        return false;
    memcpy(buf, g_sim.keyblob, len);
    return true;
}

bool plat_keyblob_write(const uint8_t *buf, size_t len)
{
    if (len > sizeof g_sim.keyblob)
        return false;
    if (!g_sim.fail_keyblob_writes) {
        memset(g_sim.keyblob, 0xFF, sizeof g_sim.keyblob);
        memcpy(g_sim.keyblob, buf, len);
        g_sim.keyblob_valid = true;
    }
    return hostsim_save();
}

bool plat_keyblob_erase(void)
{
    memset(g_sim.keyblob, 0xFF, sizeof g_sim.keyblob);
    g_sim.keyblob_valid = false;
    return hostsim_save();
}

/* ---- sealing: HOST SIMULATION ONLY - XOR with a keystream derived from the serial.
 * It only models "bound to this device" for tests. The RT1062 uses DCP AES-128 with
 * the OTP master key (platform/rt1062/dcp_seal.c). */
static void sim_stream(uint8_t ks[32])
{
    char seed[64];
    int n = snprintf(seed, sizeof seed, "fwsim-seal:%s", g_sim.serial);
    sha256((const uint8_t *)seed, (size_t)n, ks);
}

bool plat_seal(const uint8_t *in, uint8_t *out, size_t len)
{
    uint8_t ks[32];
    if (len % 16 || len > sizeof ks)
        return false;
    sim_stream(ks);
    for (size_t i = 0; i < len; i++)
        out[i] = in[i] ^ ks[i];
    secure_zero(ks, sizeof ks);
    return true;
}

bool plat_unseal(const uint8_t *in, uint8_t *out, size_t len)
{
    return plat_seal(in, out, len);
}

/* ---- Nessum IC stand-in: same replies as host/tools/fake_adapter.py ---- */
int plat_nessum_command(const char *cmd, char reply[][NESSUM_REPLY_MAX], int max_lines)
{
    if (g_sim.nessum_down)
        return -1;
    if (max_lines < 1)
        return 0;
    size_t j = 0;
    for (size_t i = 0; cmd[i] && j < NESSUM_REPLY_MAX - 1; i++)
        reply[0][j++] = cmd[i] == ' ' ? '_' : cmd[i];
    reply[0][j] = '\0';
    if (j == 0)
        snprintf(reply[0], NESSUM_REPLY_MAX, "empty");
    return 1;
}

bool plat_nessum_load_key(const uint8_t *key, size_t len)
{
    if (g_sim.nessum_down || len != sizeof g_sim.nessum_key)
        return false;
    memcpy(g_sim.nessum_key, key, len);
    g_sim.nessum_has_key = true;
    g_sim.nessum_key_loads++;
    return true;
}

bool plat_nessum_link(uint32_t *rate_mbps, uint32_t *peers)
{
    *rate_mbps = g_sim.nessum_down ? 0 : 240;
    *peers = g_sim.nessum_down ? 0 : 1;
    return !g_sim.nessum_down;
}

const char *plat_nessum_version(void) { return "sim"; }
const char *plat_serial(void) { return g_sim.serial; }
const char *plat_hw_rev(void) { return "SIM"; }
void plat_request_reenumerate(void) { g_sim.reenumerate_requests++; }

/* ---- firmware slots: NOR semantics (erase -> 0xFF, program only clears bits) ---- */
static bool slot_op_allowed(void)
{
    if (g_sim.slot_ops_left == 0)
        return false;   /* "power is off": every later operation fails */
    if (g_sim.slot_ops_left > 0)
        g_sim.slot_ops_left--;
    return true;
}

const uint8_t *plat_slot_map(fw_slot_t slot) { return g_slots[slot]; }

bool plat_slot_erase(fw_slot_t slot, uint32_t off)
{
    if (off % FW_SECTOR_SIZE || off >= FW_SLOT_SIZE || !slot_op_allowed())
        return false;
    memset(&g_slots[slot][off], 0xFF, FW_SECTOR_SIZE);
    return true;
}

bool plat_slot_program(fw_slot_t slot, uint32_t off, const uint8_t *data, uint32_t len)
{
    if (off % FW_PAGE_SIZE || len > FW_SECTOR_SIZE || off + len > FW_SLOT_SIZE)
        return false;
    g_sim.slot_programs++;
    if (!slot_op_allowed()) {
        /* a cut mid-program leaves half the data written */
        for (uint32_t i = 0; i < len / 2; i++)
            g_slots[slot][off + i] &= data[i];
        return false;
    }
    for (uint32_t i = 0; i < len; i++)
        g_slots[slot][off + i] &= data[i];
    return true;
}


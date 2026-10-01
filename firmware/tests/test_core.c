/*
 * test_core.c - unit tests for the portable firmware core, on the host platform.
 *   make -C firmware test
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_platform.h"
#include "keystore.h"
#include "macstore.h"
#include "mgmt.h"
#include "ncm.h"
#include "nline.h"
#include "util.h"

static int g_fail, g_checks;
#define CHECK(c)                                                                  \
    do {                                                                          \
        g_checks++;                                                               \
        if (!(c)) {                                                               \
            g_fail++;                                                             \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); \
        }                                                                         \
    } while (0)
#define CHECK_STR(a, b) CHECK(strcmp((a), (b)) == 0)

static const uint8_t DEF[6] = {0x00, 0x1e, 0xc0, 0x12, 0x34, 0x56};

static void hex(const uint8_t *b, size_t n, char *out)
{
    for (size_t i = 0; i < n; i++)
        sprintf(out + 2 * i, "%02x", b[i]);
}

static void fresh(void)
{
    g_sim.state_dir[0] = '\0';
    hostsim_reset(DEF, "TEST0001");
}

/* ---------------------------------------------------------------- util */
static void test_util(void)
{
    CHECK(crc16_ccitt((const uint8_t *)"123456789", 9) == 0x29B1);   /* CRC-16/CCITT-FALSE check value */
    uint8_t h[32];
    char s[65];
    sha256((const uint8_t *)"abc", 3, h);
    hex(h, 32, s);
    CHECK_STR(s, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    sha256((const uint8_t *)"", 0, h);
    hex(h, 32, s);
    CHECK_STR(s, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    const char *two = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    sha256((const uint8_t *)two, strlen(two), h);
    hex(h, 32, s);
    CHECK_STR(s, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    /* the documented example key and fingerprint (docs/MANAGEMENT.md §4.3) */
    const uint8_t key[16] = {0x0f, 0x1e, 0x2d, 0x3c, 0x4b, 0x5a, 0x69, 0x78,
                             0x87, 0x96, 0xa5, 0xb4, 0xc3, 0xd2, 0xe1, 0xf0};
    uint8_t fp[8];
    key_fingerprint(key, 16, fp);
    hex(fp, 8, s);
    CHECK_STR(s, "4179529caf32c8cc");
}

/* ---------------------------------------------------------------- MAC */
static void test_mac_text(void)
{
    uint8_t m[6];
    char t[18];
    CHECK(mac_parse("00:50:C2:AA:00:07", m));
    mac_format(m, t);
    CHECK_STR(t, "00:50:c2:aa:00:07");
    CHECK(mac_parse("00-50-c2-aa-00-07", m));
    CHECK(mac_parse("0050c2aa0007", m));
    CHECK(!mac_parse("00:50-c2:aa:00:07", m));
    CHECK(!mac_parse("00:50:c2:aa:00", m));
    CHECK(!mac_parse("zz:50:c2:aa:00:07", m));
    CHECK(!mac_parse("", m));
    const uint8_t zero[6] = {0}, bc[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff}, mc[6] = {0x01, 0, 0x5e, 0, 0, 1};
    const uint8_t la[6] = {0x02, 0, 0, 0, 0, 1};
    CHECK(!mac_is_valid_unicast(zero));
    CHECK(!mac_is_valid_unicast(bc));
    CHECK(!mac_is_valid_unicast(mc));
    CHECK(mac_is_valid_unicast(la));
}

static void test_macstore(void)
{
    macstore_t ms;
    uint8_t mac[6] = {0x00, 0x50, 0xc2, 0xaa, 0x00, 0x07}, got[6];
    fresh();
    CHECK(macstore_load(&ms));
    CHECK(!ms.has_programmed && ms.source == MAC_SRC_DEFAULT && !memcmp(ms.active, DEF, 6));

    CHECK(macstore_program(&ms, mac) == MAC_OK);
    CHECK(ms.source == MAC_SRC_DEFAULT);                       /* not active until re-enumeration */
    uint8_t flags;
    CHECK(mac_record_decode(&g_sim.eeprom[MAC_REC_ADDR_A], &flags, got) && !memcmp(got, mac, 6));
    CHECK(mac_record_decode(&g_sim.eeprom[MAC_REC_ADDR_B], &flags, got) && !memcmp(got, mac, 6));
    macstore_apply(&ms);
    CHECK(ms.source == MAC_SRC_PROGRAMMED && !memcmp(ms.active, mac, 6));

    /* power cycle; then a corrupted copy A falls back to copy B */
    CHECK(macstore_load(&ms) && ms.has_programmed && !memcmp(ms.programmed, mac, 6));
    g_sim.eeprom[MAC_REC_ADDR_A + 5] ^= 0x40;
    CHECK(macstore_load(&ms) && ms.has_programmed && !memcmp(ms.programmed, mac, 6));
    g_sim.eeprom[MAC_REC_ADDR_B + 5] ^= 0x40;                  /* both bad: default */
    CHECK(macstore_load(&ms) && !ms.has_programmed && ms.source == MAC_SRC_DEFAULT);

    /* invalid, storage failure */
    fresh();
    macstore_load(&ms);
    const uint8_t mc[6] = {0x01, 0x00, 0x5e, 0, 0, 1};
    CHECK(macstore_program(&ms, mc) == MAC_ERR_INVALID);
    g_sim.fail_eeprom_writes = true;
    CHECK(macstore_program(&ms, mac) == MAC_ERR_STORAGE && !ms.has_programmed);
    g_sim.fail_eeprom_writes = false;

    /* runtime MAC (NCM SET_NET_ADDRESS): RAM only, dropped at re-enumeration */
    const uint8_t rt[6] = {0x02, 0x11, 0x22, 0x33, 0x44, 0x55};
    CHECK(macstore_set_runtime(&ms, rt) == MAC_OK && ms.source == MAC_SRC_RUNTIME);
    macstore_apply(&ms);
    CHECK(ms.source == MAC_SRC_DEFAULT);

    /* lock: needs a programmed MAC; then everything is refused, and it survives a power cycle */
    CHECK(macstore_lock(&ms) == MAC_ERR_NOTHING);
    CHECK(macstore_program(&ms, mac) == MAC_OK);
    CHECK(macstore_lock(&ms) == MAC_OK && ms.locked);
    CHECK(macstore_program(&ms, rt) == MAC_ERR_LOCKED);
    CHECK(macstore_clear(&ms) == MAC_ERR_LOCKED);
    CHECK(macstore_set_runtime(&ms, rt) == MAC_ERR_LOCKED);
    CHECK(macstore_load(&ms) && ms.locked && !memcmp(ms.programmed, mac, 6));
}

/* ---------------------------------------------------------------- key */
static void test_keystore(void)
{
    keystore_t ks;
    const uint8_t key[16] = {0x0f, 0x1e, 0x2d, 0x3c, 0x4b, 0x5a, 0x69, 0x78,
                             0x87, 0x96, 0xa5, 0xb4, 0xc3, 0xd2, 0xe1, 0xf0};
    const uint8_t zero[16] = {0};
    char fp[17];
    fresh();
    keystore_load(&ks);
    CHECK(!ks.set);
    keystore_fp_hex(&ks, fp);
    CHECK_STR(fp, "none");
    CHECK(keystore_set(&ks, key, 15) == KEY_ERR_INVALID);
    CHECK(keystore_set(&ks, zero, 16) == KEY_ERR_INVALID);
    CHECK(keystore_set(&ks, key, 16) == KEY_OK && ks.set);
    keystore_fp_hex(&ks, fp);
    CHECK_STR(fp, "4179529caf32c8cc");
    CHECK(g_sim.nessum_has_key && !memcmp(g_sim.nessum_key, key, 16));
    CHECK(memmem(g_sim.keyblob, sizeof g_sim.keyblob, key, 16) == NULL);   /* stored sealed, not in clear */

    /* power cycle: fingerprint from the blob; key unsealed into the IC */
    memset(g_sim.nessum_key, 0, 16);
    keystore_load(&ks);
    CHECK(ks.set);
    CHECK(keystore_load_into_nessum(&ks) && !memcmp(g_sim.nessum_key, key, 16));

    /* sealed to this device: another serial cannot unseal it */
    snprintf(g_sim.serial, sizeof g_sim.serial, "OTHER");
    memset(g_sim.nessum_key, 0, 16);
    keystore_load_into_nessum(&ks);
    CHECK(memcmp(g_sim.nessum_key, key, 16) != 0);
    snprintf(g_sim.serial, sizeof g_sim.serial, "TEST0001");

    /* tampered blob is rejected; a failed write leaves "not stored" */
    g_sim.keyblob[13] ^= 1;
    keystore_load(&ks);
    CHECK(!ks.set);
    fresh();
    keystore_load(&ks);
    g_sim.fail_keyblob_writes = true;
    CHECK(keystore_set(&ks, key, 16) == KEY_ERR_STORAGE && !ks.set);
    /* IC down: stored and reported as set, but flagged; it loads at the next boot */
    fresh();
    keystore_load(&ks);
    g_sim.nessum_down = true;
    CHECK(keystore_set(&ks, key, 16) == KEY_ERR_IC && ks.set);
    g_sim.nessum_down = false;
    keystore_load(&ks);
    CHECK(ks.set && keystore_load_into_nessum(&ks) && memcmp(g_sim.nessum_key, key, 16) == 0);
}

/* ---------------------------------------------------------------- console protocol */
static char g_out[4096];
static size_t g_outlen;

static void capture(void *ctx, const char *d, size_t n)
{
    (void)ctx;
    if (g_outlen + n < sizeof g_out) {
        memcpy(g_out + g_outlen, d, n);
        g_outlen += n;
        g_out[g_outlen] = '\0';
    }
}

static macstore_t g_ms;
static keystore_t g_ks;
static mgmt_t g_mg;

static const char *req(const char *line)
{
    g_outlen = 0;
    g_out[0] = '\0';
    mgmt_rx(&g_mg, (const uint8_t *)line, strlen(line));
    if (g_sim.reenumerate_requests) {
        g_sim.reenumerate_requests = 0;
        macstore_apply(&g_ms);
    }
    return g_out;
}

static void boot(void)
{
    macstore_load(&g_ms);
    keystore_load(&g_ks);
    mgmt_init(&g_mg, &g_ms, &g_ks, capture, NULL);
}

static void test_mgmt(void)
{
    fresh();
    boot();
    CHECK_STR(req("VERSION\n"), "fw=" FW_VERSION " hw=SIM serial=TEST0001 nessum=sim proto=1\r\nOK\r\n");
    CHECK_STR(req("status\r\n"), "link=up rate=240 peers=1\r\nOK\r\n");          /* case, CRLF */
    CHECK_STR(req("MAC GET\n"), "active=00:1e:c0:12:34:56 source=default default=00:1e:c0:12:34:56 "
                                "programmed=none locked=no\r\nOK\r\n");
    CHECK_STR(req("MAC SET 01:00:5e:00:00:01\n"), "ERR 3 invalid MAC (multicast, zero or broadcast)\r\n");
    CHECK_STR(req("MAC SET nope\n"), "ERR 2 bad MAC syntax\r\n");
    CHECK_STR(req("MAC SET\n"), "ERR 2 usage: MAC SET <mac>\r\n");
    CHECK_STR(req("MAC SET 00-50-C2-AA-00-2A\n"), "programmed=00:50:c2:aa:00:2a apply=reboot\r\nOK\r\n");
    CHECK_STR(req("LOCK\n"), "ERR 7 MAC and network key must both be programmed before locking\r\n");
    CHECK_STR(req("NKEY GET\n"), "set=no fp=none len=16\r\nOK\r\n");
    CHECK_STR(req("NKEY SET 00\n"), "ERR 8 network key must be 16 bytes\r\n");
    CHECK_STR(req("NKEY SET zz\n"), "ERR 2 usage: NKEY SET <hex>\r\n");
    CHECK_STR(req("NKEY SET 00000000000000000000000000000000\n"), "ERR 8 all-zero network key rejected\r\n");
    CHECK_STR(req("NKEY SET 0f1e2d3c4b5a69788796a5b4c3d2e1f0\n"), "fp=4179529caf32c8cc\r\nOK\r\n");
    CHECK(strstr(g_mg.line, "0f1e2d3c") == NULL);                               /* key wiped from the buffer */
    CHECK_STR(req("NKEY GET\n"), "set=yes fp=4179529caf32c8cc len=16\r\nOK\r\n");
    CHECK_STR(req("NESSUM SETKEY x\n"), "reply=SETKEY_x\r\nOK\r\n");
    CHECK_STR(req("LOCK\n"), "locked=yes\r\nOK\r\n");
    CHECK_STR(req("MAC SET 00:50:c2:aa:00:2b\n"), "ERR 6 adapter is factory-locked\r\n");
    CHECK_STR(req("MAC CLEAR\n"), "ERR 6 adapter is factory-locked\r\n");
    CHECK_STR(req("NKEY SET ffeeddccbbaa99887766554433221100\n"), "ERR 6 adapter is factory-locked\r\n");
    CHECK_STR(req("NESSUM SETKEY x\n"), "ERR 6 only read-only Nessum commands are allowed after the factory lock\r\n");
    CHECK_STR(req("NESSUM status\n"), "reply=status\r\nOK\r\n");
    CHECK_STR(req("REBOOT\n"), "OK\r\n");
    CHECK_STR(req("MAC GET\n"), "active=00:50:c2:aa:00:2a source=programmed default=00:1e:c0:12:34:56 "
                                "programmed=00:50:c2:aa:00:2a locked=yes\r\nOK\r\n");
    CHECK_STR(req("FOO bar\n"), "ERR 1 unknown command FOO\r\n");
    CHECK_STR(req("MAC\n"), "ERR 1 unknown command MAC\r\n");
    CHECK_STR(req("\n"), "");                                                   /* empty line: ignored */
    /* split across reads */
    req("VERS");
    CHECK_STR(req("ION\n"), "fw=" FW_VERSION " hw=SIM serial=TEST0001 nessum=sim proto=1\r\nOK\r\n");
    /* overlong line */
    char big[400];
    memset(big, 'A', sizeof big - 2);
    big[sizeof big - 2] = '\n';
    big[sizeof big - 1] = '\0';
    CHECK_STR(req(big), "ERR 2 line too long (max 255 bytes)\r\n");
    CHECK_STR(req("STATUS\n"), "link=up rate=240 peers=1\r\nOK\r\n");          /* recovers */
    /* IC not answering */
    g_sim.nessum_down = true;
    CHECK_STR(req("NESSUM STATUS\n"), "ERR 5 Nessum IC not responding\r\n");
    g_sim.nessum_down = false;
    /* power cycle keeps everything */
    boot();
    CHECK(strstr(req("MAC GET\n"), "locked=yes") != NULL);
    CHECK(strstr(req("NKEY GET\n"), "set=yes fp=4179529caf32c8cc") != NULL);
}

/* ---------------------------------------------------------------- NCM */
static uint8_t g_frames[8][NCM_ETH_MAX];
static size_t g_flen[8];
static int g_nframes;

static void collect(void *ctx, const uint8_t *f, size_t n)
{
    (void)ctx;
    if (g_nframes < 8) {
        memcpy(g_frames[g_nframes], f, n);
        g_flen[g_nframes++] = n;
    }
}

static int parse(const uint8_t *ntb, size_t len)
{
    g_nframes = 0;
    return ncm_parse(ntb, len, collect, NULL);
}

static void test_ncm(void)
{
    uint8_t buf[4096], f1[60], f2[1518], f3[14];
    for (size_t i = 0; i < sizeof f1; i++) f1[i] = (uint8_t)i;
    for (size_t i = 0; i < sizeof f2; i++) f2[i] = (uint8_t)(i * 7);
    memset(f3, 0xAB, sizeof f3);
    ncm_tx_t t;
    ncm_tx_init(&t, buf, sizeof buf, 42);
    CHECK(ncm_tx_add(&t, f1, sizeof f1));
    CHECK(ncm_tx_add(&t, f2, sizeof f2));
    CHECK(ncm_tx_add(&t, f3, sizeof f3));
    CHECK(!ncm_tx_add(&t, f1, 13));                       /* too short for Ethernet */
    size_t n = ncm_tx_finish(&t);
    CHECK(n > 0 && n % 4 == 0);
    CHECK(buf[6] == 42);                                  /* wSequence */
    CHECK(parse(buf, n) == 3);
    CHECK(g_flen[0] == sizeof f1 && !memcmp(g_frames[0], f1, sizeof f1));
    CHECK(g_flen[1] == sizeof f2 && !memcmp(g_frames[1], f2, sizeof f2));
    CHECK(g_flen[2] == sizeof f3 && !memcmp(g_frames[2], f3, sizeof f3));
    for (int i = 0; i < 3; i++)
        CHECK(t.index[i] % 4 == 0);                       /* datagrams 4-byte aligned */

    /* fills up: a small buffer refuses the frame that doesn't fit */
    uint8_t small[128];
    ncm_tx_init(&t, small, sizeof small, 0);
    CHECK(ncm_tx_add(&t, f1, sizeof f1));
    CHECK(!ncm_tx_add(&t, f1, sizeof f1));
    CHECK(ncm_tx_finish(&t) <= sizeof small);
    ncm_tx_init(&t, small, sizeof small, 0);
    CHECK(ncm_tx_finish(&t) == 0);                        /* empty */

    /* never a multiple of the packet size (no ZLP needed): one pad byte, still valid */
    ncm_tx_init(&t, small, sizeof small - 1, 0);
    CHECK(ncm_tx_add(&t, f1, sizeof f1));
    CHECK(ncm_tx_finish_padded(&t, 8) == 89);             /* 88 = 11 * 8 -> 89 */
    CHECK((small[8] | small[9] << 8) == 89);
    CHECK(parse(small, 89) == 1 && !memcmp(g_frames[0], f1, sizeof f1));
    ncm_tx_init(&t, small, sizeof small - 1, 0);
    CHECK(ncm_tx_add(&t, f1, sizeof f1));
    CHECK(ncm_tx_finish_padded(&t, 64) == 88);

    /* malformed input from the host: rejected, nothing delivered */
    uint8_t bad[4096];
    memcpy(bad, buf, n);
    CHECK(parse(bad, 8) == NCM_E_SHORT);
    bad[0] ^= 1;
    CHECK(parse(bad, n) == NCM_E_NTH && g_nframes == 0);
    memcpy(bad, buf, n);
    bad[8] = (uint8_t)(n + 4);                            /* wBlockLength beyond the transfer */
    bad[9] = (uint8_t)((n + 4) >> 8);
    CHECK(parse(bad, n) == NCM_E_SHORT);
    memcpy(bad, buf, n);
    size_t ndp = (size_t)(buf[10] | buf[11] << 8);
    bad[ndp] ^= 1;                                        /* NDP signature */
    CHECK(parse(bad, n) == NCM_E_NDP && g_nframes == 0);
    memcpy(bad, buf, n);
    bad[ndp + 8 + 4 * 2] = 0xF0;                          /* 3rd datagram index far out of range */
    bad[ndp + 9 + 4 * 2] = 0xFF;
    CHECK(parse(bad, n) == NCM_E_DATAGRAM && g_nframes == 0);   /* no partial delivery */
    memcpy(bad, buf, n);
    bad[ndp + 6] = (uint8_t)ndp;                          /* wNextNdpIndex -> itself: loop */
    bad[ndp + 7] = (uint8_t)(ndp >> 8);
    CHECK(parse(bad, n) == NCM_E_NDP && g_nframes == 0);
    memcpy(bad, buf, n);
    bad[10] = 2;                                          /* misaligned NDP index */
    bad[11] = 0;
    CHECK(parse(bad, n) == NCM_E_NDP);
}

/* ---------------------------------------------------------------- Nessum reply parser */
static void feed(nline_t *p, const char *s)
{
    while (*s)
        nline_rx(p, (uint8_t)*s++);
}

static void test_nline(void)
{
    nline_t p;
    nline_reset(&p);
    nline_rx(&p, 'x');                                    /* idle: ignored */
    CHECK(nline_poll(&p, 0) == NL_IDLE);

    nline_start(&p, 1000, 500);
    feed(&p, "link=up rate=240");
    CHECK(nline_poll(&p, 1499) == NL_BUSY);               /* partial line, not yet due */
    feed(&p, " peers=1\r\nOK\r\n");
    CHECK(nline_poll(&p, 1499) == NL_OK && p.count == 1);
    CHECK(!strcmp(p.lines[0], "link=up rate=240 peers=1"));
    feed(&p, "late\n");                                   /* after completion: ignored */
    CHECK(p.count == 1);

    nline_start(&p, 0, 500);
    feed(&p, "ERR 3 no\n");
    CHECK(nline_poll(&p, 1) == NL_ERR && p.count == 1 && !strcmp(p.lines[0], "ERR 3 no"));

    nline_start(&p, 0, 500);
    feed(&p, "OK\n");
    CHECK(nline_poll(&p, 0) == NL_OK && p.count == 0);    /* bare OK */

    nline_start(&p, 0xFFFFFF00u, 0x200);                  /* deadline wraps past 0 */
    CHECK(nline_poll(&p, 0xFFFFFFF0u) == NL_BUSY);
    CHECK(nline_poll(&p, 0x000000FFu) == NL_BUSY);
    CHECK(nline_poll(&p, 0x00000100u) == NL_TIMEOUT);

    nline_start(&p, 0, 500);                              /* excess lines dropped, still ends */
    for (int i = 0; i < NLINE_MAX_LINES + 3; i++)
        feed(&p, "l\n");
    feed(&p, "OK\n");
    CHECK(nline_poll(&p, 0) == NL_OK && p.count == NLINE_MAX_LINES);

    nline_start(&p, 0, 500);                              /* overlong line truncated */
    for (int i = 0; i < 300; i++)
        nline_rx(&p, 'a');
    feed(&p, "\nOK\n");
    CHECK(p.state == NL_OK && strlen(p.lines[0]) == NESSUM_REPLY_MAX - 1);
    nline_reset(&p);
    CHECK(p.state == NL_IDLE && p.lines[0][0] == '\0');
}

int main(void)
{
    test_util();
    test_mac_text();
    test_macstore();
    test_keystore();
    test_mgmt();
    test_ncm();
    test_nline();
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}

/* mgmt.c - console protocol: VERSION STATUS MAC NKEY LOCK REBOOT NESSUM. */
#include "mgmt.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "platform.h"
#include "util.h"

/* Nessum IC commands still allowed through NESSUM after the factory lock (read-only).
 * Placeholder list until the SC1320A command set is known (spec S4). */
static const char *const NESSUM_READONLY[] = {"STATUS", "STATS", "VERSION", "PEERS"};

#define MAX_ARGS 8

void mgmt_init(mgmt_t *m, macstore_t *macs, keystore_t *keys, mgmt_write_fn write, void *ctx)
{
    memset(m, 0, sizeof *m);
    m->macs = macs;
    m->keys = keys;
    m->write = write;
    m->ctx = ctx;
}

bool mgmt_locked(const mgmt_t *m)
{
    return m->macs->locked;
}

static void out(mgmt_t *m, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void out(mgmt_t *m, const char *fmt, ...)
{
    char buf[MGMT_LINE_MAX + 3];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf - 2, fmt, ap);
    va_end(ap);
    if (n < 0)
        return;
    if ((size_t)n > sizeof buf - 3)
        n = (int)(sizeof buf - 3);
    buf[n++] = '\r';
    buf[n++] = '\n';
    m->write(m->ctx, buf, (size_t)n);
}

static void upcase(char *s)
{
    for (; *s; s++)
        *s = (char)toupper((unsigned char)*s);
}

static bool streq_nocase(const char *a, const char *b)
{
    for (; *a && *b; a++, b++)
        if (toupper((unsigned char)*a) != toupper((unsigned char)*b))
            return false;
    return *a == *b;
}

static void cmd_mac_get(mgmt_t *m)
{
    static const char *const src[] = {"default", "programmed", "runtime"};
    char act[18], def[18], prog[18] = "none";
    mac_format(m->macs->active, act);
    mac_format(m->macs->def, def);
    if (m->macs->has_programmed)
        mac_format(m->macs->programmed, prog);
    out(m, "active=%s source=%s default=%s programmed=%s locked=%s", act, src[m->macs->source], def, prog,
        m->macs->locked ? "yes" : "no");
    out(m, "OK");
}

static void cmd_mac_set(mgmt_t *m, int argc, char **argv)
{
    uint8_t mac[6];
    char txt[18];
    if (argc != 1) {
        out(m, "ERR 2 usage: MAC SET <mac>");
        return;
    }
    if (m->macs->locked) {
        out(m, "ERR 6 adapter is factory-locked");
        return;
    }
    if (!mac_parse(argv[0], mac)) {
        out(m, "ERR 2 bad MAC syntax");
        return;
    }
    switch (macstore_program(m->macs, mac)) {
    case MAC_OK:
        mac_format(mac, txt);
        out(m, "programmed=%s apply=reboot", txt);
        out(m, "OK");
        break;
    case MAC_ERR_INVALID:
        out(m, "ERR 3 invalid MAC (multicast, zero or broadcast)");
        break;
    case MAC_ERR_LOCKED:
        out(m, "ERR 6 adapter is factory-locked");
        break;
    default:
        out(m, "ERR 4 EEPROM verify failed");
        break;
    }
}

static void cmd_mac_clear(mgmt_t *m)
{
    switch (macstore_clear(m->macs)) {
    case MAC_OK:
        out(m, "programmed=none apply=reboot");
        out(m, "OK");
        break;
    case MAC_ERR_LOCKED:
        out(m, "ERR 6 adapter is factory-locked");
        break;
    default:
        out(m, "ERR 4 EEPROM verify failed");
        break;
    }
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    c = (char)tolower((unsigned char)c);
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static void cmd_nkey_set(mgmt_t *m, int argc, char **argv)
{
    if (m->macs->locked) {
        out(m, "ERR 6 adapter is factory-locked");
        return;
    }
    size_t n = argc == 1 ? strlen(argv[0]) : 0;
    if (argc != 1 || n == 0 || n % 2) {
        out(m, "ERR 2 usage: NKEY SET <hex>");
        return;
    }
    for (size_t i = 0; i < n; i++) {
        if (hexval(argv[0][i]) < 0) {
            out(m, "ERR 2 usage: NKEY SET <hex>");
            return;
        }
    }
    if (n / 2 != NKEY_LEN) {
        out(m, "ERR 8 network key must be %u bytes", (unsigned)NKEY_LEN);
        return;
    }
    uint8_t key[NKEY_LEN];
    for (size_t i = 0; i < NKEY_LEN; i++)
        key[i] = (uint8_t)(hexval(argv[0][2 * i]) << 4 | hexval(argv[0][2 * i + 1]));
    secure_zero(argv[0], n);   /* don't leave the key in the line buffer */
    enum key_err e = keystore_set(m->keys, key, NKEY_LEN);
    secure_zero(key, sizeof key);
    if (e == KEY_ERR_INVALID) {
        out(m, "ERR 8 all-zero network key rejected");
    } else if (e == KEY_ERR_IC) {
        out(m, "ERR 5 Nessum IC not responding (key stored, loads at next boot)");
    } else if (e != KEY_OK) {
        out(m, "ERR 4 key store verify failed");
    } else {
        char fp[17];
        keystore_fp_hex(m->keys, fp);
        out(m, "fp=%s", fp);
        out(m, "OK");
    }
}

static void cmd_nkey_get(mgmt_t *m)
{
    char fp[17];
    keystore_fp_hex(m->keys, fp);
    out(m, "set=%s fp=%s len=%u", m->keys->set ? "yes" : "no", fp, (unsigned)NKEY_LEN);
    out(m, "OK");
}

static void cmd_lock(mgmt_t *m)
{
    if (!m->macs->has_programmed || !m->keys->set) {
        out(m, "ERR 7 MAC and network key must both be programmed before locking");
        return;
    }
    if (macstore_lock(m->macs) != MAC_OK) {
        out(m, "ERR 4 EEPROM verify failed");
        return;
    }
    out(m, "locked=yes");
    out(m, "OK");
}

static void cmd_nessum(mgmt_t *m, int argc, char **argv)
{
    if (m->macs->locked) {
        bool allowed = false;
        for (size_t i = 0; argc > 0 && i < sizeof NESSUM_READONLY / sizeof *NESSUM_READONLY; i++)
            allowed |= streq_nocase(argv[0], NESSUM_READONLY[i]);
        if (!allowed) {
            out(m, "ERR 6 only read-only Nessum commands are allowed after the factory lock");
            return;
        }
    }
    char cmd[MGMT_LINE_MAX + 1] = "";
    for (int i = 0; i < argc; i++) {
        if (i)
            strncat(cmd, " ", sizeof cmd - strlen(cmd) - 1);
        strncat(cmd, argv[i], sizeof cmd - strlen(cmd) - 1);
    }
    char reply[8][NESSUM_REPLY_MAX];
    int lines = plat_nessum_command(cmd, reply, 8);
    if (lines < 0) {
        out(m, "ERR 5 Nessum IC not responding");
        return;
    }
    for (int i = 0; i < lines; i++)
        out(m, "reply=%s", reply[i]);
    out(m, "OK");
}

void mgmt_execute(mgmt_t *m, char *line)
{
    char *argv[MAX_ARGS + 2];
    int argc = 0;
    for (char *p = line; *p && argc < MAX_ARGS + 2;) {   /* split on spaces, in place */
        while (*p == ' ')
            *p++ = '\0';
        if (!*p)
            break;
        argv[argc++] = p;
        while (*p && *p != ' ')
            p++;
    }
    if (argc == 0)
        return;
    char word[16];
    snprintf(word, sizeof word, "%s", argv[0]);
    upcase(word);

    if (strcmp(word, "MAC") == 0 || strcmp(word, "NKEY") == 0) {
        char sub[16] = "";
        if (argc > 1) {
            snprintf(sub, sizeof sub, "%s", argv[1]);
            upcase(sub);
        }
        int n = argc > 2 ? argc - 2 : 0;
        char **args = &argv[2];
        if (word[0] == 'M' && strcmp(sub, "GET") == 0)
            cmd_mac_get(m);
        else if (word[0] == 'M' && strcmp(sub, "SET") == 0)
            cmd_mac_set(m, n, args);
        else if (word[0] == 'M' && strcmp(sub, "CLEAR") == 0)
            cmd_mac_clear(m);
        else if (word[0] == 'N' && strcmp(sub, "SET") == 0)
            cmd_nkey_set(m, n, args);
        else if (word[0] == 'N' && strcmp(sub, "GET") == 0)
            cmd_nkey_get(m);
        else
            out(m, "ERR 1 unknown command %s", argv[0]);
    } else if (strcmp(word, "VERSION") == 0) {
        out(m, "fw=%s hw=%s serial=%s nessum=%s proto=1", FW_VERSION, plat_hw_rev(), plat_serial(),
            plat_nessum_version());
        out(m, "OK");
    } else if (strcmp(word, "STATUS") == 0) {
        uint32_t rate = 0, peers = 0;
        bool up = plat_nessum_link(&rate, &peers);
        out(m, "link=%s rate=%lu peers=%lu", up ? "up" : "down", (unsigned long)rate, (unsigned long)peers);
        out(m, "OK");
    } else if (strcmp(word, "LOCK") == 0) {
        cmd_lock(m);
    } else if (strcmp(word, "REBOOT") == 0) {
        out(m, "OK");
        plat_request_reenumerate();
    } else if (strcmp(word, "NESSUM") == 0) {
        cmd_nessum(m, argc - 1, &argv[1]);
    } else {
        out(m, "ERR 1 unknown command %s", argv[0]);
    }
}

void mgmt_rx(mgmt_t *m, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        char c = (char)data[i];
        if (c == '\n') {
            if (m->len && m->line[m->len - 1] == '\r')
                m->len--;
            m->line[m->len] = '\0';
            if (m->overflow)
                out(m, "ERR 2 line too long (max %u bytes)", (unsigned)MGMT_LINE_MAX);
            else if (m->len)
                mgmt_execute(m, m->line);
            secure_zero(m->line, sizeof m->line);   /* may have held a key */
            m->len = 0;
            m->overflow = false;
        } else if (m->len < MGMT_LINE_MAX) {
            m->line[m->len++] = c;
        } else {
            m->overflow = true;
        }
    }
}

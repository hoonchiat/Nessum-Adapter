/*
 * fwsim.c - the firmware core on a PC, serving the management console on a
 * pseudo-terminal (as the adapter does on its USB CDC-ACM port).
 *
 *   fwsim [--state DIR] [--serial S] [--default-mac MAC] [--fail-eeprom-writes]
 *         [--fail-keyblob-writes] [--nessum-down]
 *
 * Prints the pty path on the first line, then serves until killed. With --state the
 * EEPROM and key blob persist in DIR, so restarting fwsim is a power cycle. A REBOOT
 * request re-applies the MAC selection, like a USB re-enumeration.
 */
#define _XOPEN_SOURCE 700
#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#include "host_platform.h"
#include "keystore.h"
#include "macstore.h"
#include "mgmt.h"

static int g_master = -1;

static void write_pty(void *ctx, const char *data, size_t len)
{
    (void)ctx;
    while (len) {
        ssize_t n = write(g_master, data, len);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            return;
        data += n;
        len -= (size_t)n;
    }
}

static int usage(void)
{
    fprintf(stderr, "usage: fwsim [--state DIR] [--serial S] [--default-mac MAC] [--fail-eeprom-writes] "
                    "[--fail-keyblob-writes] [--nessum-down]\n");
    return 2;
}

int main(int argc, char **argv)
{
    const char *state = NULL, *serial = "FWSIM0001", *defmac = "00:1e:c0:12:34:56";
    bool fail_ee = false, fail_kb = false, down = false;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--state") && i + 1 < argc) state = argv[++i];
        else if (!strcmp(argv[i], "--serial") && i + 1 < argc) serial = argv[++i];
        else if (!strcmp(argv[i], "--default-mac") && i + 1 < argc) defmac = argv[++i];
        else if (!strcmp(argv[i], "--fail-eeprom-writes")) fail_ee = true;
        else if (!strcmp(argv[i], "--fail-keyblob-writes")) fail_kb = true;
        else if (!strcmp(argv[i], "--nessum-down")) down = true;
        else return usage();
    }
    uint8_t def[6];
    if (!mac_parse(defmac, def))
        return usage();

    hostsim_reset(def, serial);
    if (state && !hostsim_load(state)) {
        hostsim_reset(def, serial);   /* new device: blank EEPROM with this EUI-48 */
        snprintf(g_sim.state_dir, sizeof g_sim.state_dir, "%s", state);
        hostsim_save();
    }
    g_sim.fail_eeprom_writes = fail_ee;
    g_sim.fail_keyblob_writes = fail_kb;
    g_sim.nessum_down = down;

    /* Boot sequence, as on the adapter. */
    macstore_t macs;
    keystore_t keys;
    mgmt_t mgmt;
    macstore_load(&macs);
    keystore_load(&keys);
    keystore_load_into_nessum(&keys);
    mgmt_init(&mgmt, &macs, &keys, write_pty, NULL);

    g_master = posix_openpt(O_RDWR | O_NOCTTY);
    if (g_master < 0 || grantpt(g_master) || unlockpt(g_master)) {
        perror("fwsim: pty");
        return 1;
    }
    const char *slave_name = ptsname(g_master);
    int slave = open(slave_name, O_RDWR | O_NOCTTY);   /* keep a slave fd open: no EIO on idle */
    struct termios t;
    if (slave < 0 || tcgetattr(slave, &t)) {
        perror("fwsim: slave");
        return 1;
    }
    cfmakeraw(&t);
    tcsetattr(slave, TCSANOW, &t);
    printf("%s\n", slave_name);
    fflush(stdout);
    signal(SIGPIPE, SIG_IGN);

    uint8_t buf[256];
    for (;;) {
        ssize_t n = read(g_master, buf, sizeof buf);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            break;
        mgmt_rx(&mgmt, buf, (size_t)n);
        if (g_sim.reenumerate_requests) {   /* REBOOT: the USB host gets the MAC selection anew */
            g_sim.reenumerate_requests = 0;
            macstore_apply(&macs);
        }
    }
    close(slave);
    return 0;
}

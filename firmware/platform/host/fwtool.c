/*
 * fwtool.c - the firmware's own image check on a PC, for host/tools/test_fwimage.py
 * (proves the Python signer and the C verifier agree).
 *
 *   fwtool verify <pubkey hex> <image file>    exit 0 if the image verifies
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fwimage.h"
#include "platform.h"

static int hexval(int c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

int main(int argc, char **argv)
{
    uint8_t pub[32];
    if (argc != 4 || strcmp(argv[1], "verify") || strlen(argv[2]) != 64) {
        fprintf(stderr, "usage: fwtool verify <pubkey hex> <image>\n");
        return 2;
    }
    for (int i = 0; i < 32; i++) {
        int hi = hexval(argv[2][2 * i]), lo = hexval(argv[2][2 * i + 1]);
        if (hi < 0 || lo < 0)
            return 2;
        pub[i] = (uint8_t)(hi << 4 | lo);
    }
    static uint8_t slot[FW_SLOT_SIZE];
    memset(slot, 0xFF, sizeof slot);
    FILE *f = fopen(argv[3], "rb");
    if (!f) {
        perror(argv[3]);
        return 2;
    }
    size_t n = fread(slot, 1, sizeof slot, f);
    fclose(f);
    (void)n;
    fwimg_info_t info;
    enum fwimg_err e = fwimg_verify(slot, FW_SLOT_SIZE, FW_ACTIVE_LOAD_ADDR, pub, &info);
    if (e != FWIMG_OK) {
        printf("%s\n", fwimg_strerror(e));
        return 1;
    }
    char v[16];
    fwimg_version_str(info.version, v);
    printf("ok version=%s size=%u\n", v, (unsigned)info.size);
    return 0;
}

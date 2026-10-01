/*
 * mgmt.h - the management console protocol on the CDC-ACM port (docs/MANAGEMENT.md §4).
 *
 * Feed received bytes with mgmt_rx(); complete lines are executed and the response
 * is written through the write callback. No echo. host/tools/fake_adapter.py is the
 * executable reference, and host/tools/test_firmware.py checks this implementation
 * against it.
 */
#ifndef MGMT_H
#define MGMT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "keystore.h"
#include "macstore.h"

#define MGMT_LINE_MAX 255u
#define FW_VERSION "0.1.0"

typedef void (*mgmt_write_fn)(void *ctx, const char *data, size_t len);

typedef struct {
    char line[MGMT_LINE_MAX + 1];
    size_t len;
    bool overflow;
    mgmt_write_fn write;
    void *ctx;
    macstore_t *macs;
    keystore_t *keys;
} mgmt_t;

void mgmt_init(mgmt_t *m, macstore_t *macs, keystore_t *keys, mgmt_write_fn write, void *ctx);
void mgmt_rx(mgmt_t *m, const uint8_t *data, size_t len);
/* Execute one request line (without line ending). Exposed for tests. */
void mgmt_execute(mgmt_t *m, char *line);

/* Factory lock covers MAC and key: true once LOCK has succeeded. */
bool mgmt_locked(const mgmt_t *m);

#endif

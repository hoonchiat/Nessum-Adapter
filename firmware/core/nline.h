/*
 * nline.h - non-blocking parser for the Nessum IC's command replies.
 *
 * A reply is zero or more lines followed by "OK", or ending in a line that starts
 * with "ERR" (kept as the last reply line). CR is ignored. Feed bytes as they arrive
 * (from the UART interrupt ring) and poll for completion or timeout; nothing blocks.
 * The SC1320A command set is not yet known: this is the placeholder line protocol
 * of platform/rt1062/README.md.
 */
#ifndef NLINE_H
#define NLINE_H

#include <stdbool.h>
#include <stdint.h>

#include "platform.h"   /* NESSUM_REPLY_MAX */

#define NLINE_MAX_LINES 4

typedef enum { NL_IDLE, NL_BUSY, NL_OK, NL_ERR, NL_TIMEOUT } nl_state_t;

typedef struct {
    nl_state_t state;
    uint32_t deadline;                           /* ms, wrap-safe */
    char cur[NESSUM_REPLY_MAX];
    uint32_t n;
    char lines[NLINE_MAX_LINES][NESSUM_REPLY_MAX];
    int count;                                   /* lines kept (excess lines are dropped) */
} nline_t;

/* Begin waiting for a reply (after the command has been queued). */
void nline_start(nline_t *p, uint32_t now_ms, uint32_t timeout_ms);
/* Feed one received byte; ignored unless busy. */
void nline_rx(nline_t *p, uint8_t c);
/* Current state; turns NL_BUSY into NL_TIMEOUT once the deadline passes. */
nl_state_t nline_poll(nline_t *p, uint32_t now_ms);
/* Back to idle, wiping the buffers (replies can carry key material). */
void nline_reset(nline_t *p);

#endif

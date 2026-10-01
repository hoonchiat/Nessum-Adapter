/* nline.c - see nline.h. */
#include "nline.h"

#include <string.h>

#include "util.h"

void nline_reset(nline_t *p)
{
    secure_zero(p, sizeof *p);   /* state = NL_IDLE */
}

void nline_start(nline_t *p, uint32_t now_ms, uint32_t timeout_ms)
{
    nline_reset(p);
    p->state = NL_BUSY;
    p->deadline = now_ms + timeout_ms;
}

void nline_rx(nline_t *p, uint8_t c)
{
    if (p->state != NL_BUSY || c == '\r')
        return;
    if (c != '\n') {
        if (p->n < sizeof p->cur - 1)
            p->cur[p->n++] = (char)c;   /* overlong lines are truncated */
        return;
    }
    p->cur[p->n] = '\0';
    p->n = 0;
    if (!strcmp(p->cur, "OK")) {
        p->state = NL_OK;
        return;
    }
    if (p->count < NLINE_MAX_LINES)
        memcpy(p->lines[p->count++], p->cur, sizeof p->cur);
    if (!strncmp(p->cur, "ERR", 3))
        p->state = NL_ERR;
}

nl_state_t nline_poll(nline_t *p, uint32_t now_ms)
{
    if (p->state == NL_BUSY && (int32_t)(now_ms - p->deadline) >= 0)
        p->state = NL_TIMEOUT;
    return p->state;
}

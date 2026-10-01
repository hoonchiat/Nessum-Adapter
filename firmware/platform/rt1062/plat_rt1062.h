/* plat_rt1062.h - RT1062-only additions to platform.h. */
#ifndef PLAT_RT1062_H
#define PLAT_RT1062_H

#include <stdbool.h>
#include <stdint.h>

/* Peripherals behind platform.h: EEPROM I2C, Nessum UART, DCP, QSPI NOR, IC reset. */
void plat_rt1062_init(void);
/* True once after plat_request_reenumerate() (REBOOT): main re-enumerates. */
bool plat_rt1062_take_reenumerate(void);

/* Background Nessum link poll; call every main-loop pass, never blocks. Returns true
 * when a poll has completed, with the result in *up / *bps (bit/s). */
bool plat_rt1062_link_poll(bool *up, uint32_t *bps);

/* Provided by main: service USB and the ENET while a synchronous Nessum command
 * waits for its reply (must not run the console, which may be the caller). */
void plat_rt1062_idle(void);

#endif

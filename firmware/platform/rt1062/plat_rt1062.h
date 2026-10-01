/* plat_rt1062.h - RT1062-only additions to platform.h. */
#ifndef PLAT_RT1062_H
#define PLAT_RT1062_H

#include <stdbool.h>

/* Peripherals behind platform.h: EEPROM I2C, Nessum UART, DCP, QSPI NOR, IC reset. */
void plat_rt1062_init(void);
/* True once after plat_request_reenumerate() (REBOOT): main re-enumerates. */
bool plat_rt1062_take_reenumerate(void);

#endif

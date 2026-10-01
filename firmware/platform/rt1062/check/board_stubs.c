/*
 * LINK-CHECK STAND-INS ONLY - this image does not boot.
 *
 * The real versions come from MCUXpresso Config Tools for the adapter PCB
 * (pin_mux.c, clock_config.c), the SDK board template (BOARD_ConfigMPU, with the
 * non-cacheable OCRAM region) and the FlexSPI configuration block of the chosen
 * QSPI part. `make rt1062-link` uses these to prove that everything else links.
 */
#include <stdint.h>

#include "clock_config.h"
#include "pin_mux.h"

void BOARD_ConfigMPU(void);

void BOARD_ConfigMPU(void) {}
void BOARD_InitBootPins(void) {}
void BOARD_InitBootClocks(void) {}

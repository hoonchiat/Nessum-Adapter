/*
 * enet_bridge.h - ENET (RMII, MAC-to-MAC to the SC1320A) <-> CDC-NCM.
 *
 * No PHY: the link is fixed 100 Mbit/s full duplex. The ENET runs promiscuous - it
 * forwards whatever the Nessum IC hands up, and Linux filters as on any NIC.
 */
#ifndef ENET_BRIDGE_H
#define ENET_BRIDGE_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t rx_frames, rx_errors, rx_dropped;   /* Nessum -> host */
    uint32_t tx_frames, tx_dropped;              /* host -> Nessum */
} enet_bridge_stats_t;

bool enet_bridge_init(const uint8_t mac[6]);
/* Move frames from the ENET to the host while the NCM side has room. Main loop. */
void enet_bridge_poll(void);
const enet_bridge_stats_t *enet_bridge_stats(void);

#endif

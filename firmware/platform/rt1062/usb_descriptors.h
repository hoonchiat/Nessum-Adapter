/* usb_descriptors.h - USB identity of the adapter. */
#ifndef USB_DESCRIPTORS_H
#define USB_DESCRIPTORS_H

#include <stdint.h>

/* The MAC reported in iMACAddress: pass macstore_t.active (read at each enumeration). */
void usb_descriptors_bind(const uint8_t active_mac[6]);

#endif

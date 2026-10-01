/*
 * usb_descriptors.c - composite device: CDC-NCM (eth2) + CDC-ACM (management console).
 *
 *   itf 0/1  CDC-NCM  notify 0x81, bulk OUT 0x02 / IN 0x82   -> Linux cdc_ncm
 *   itf 2/3  CDC-ACM  notify 0x83, bulk OUT 0x04 / IN 0x84   -> Linux cdc_acm
 *   itf 4    DFU 1.1 (DFU mode, download only, 4 KB blocks)  -> dfu-util (usb_dfu.c)
 *
 * iMACAddress (string 5) is the active MAC chosen at enumeration (core/macstore.c).
 * VID:PID 1209:0000 is a PLACEHOLDER (docs/SPECIFICATION.md): never ship with it.
 */
#include <string.h>

#include "tusb.h"
#include "class/net/net_device.h"   /* NCM_DATA_PROTOCOL_NETWORK_TRANSFER_BLOCK */

#include "platform.h"
#include "usb_descriptors.h"

#define USB_VID 0x1209
#define USB_PID 0x0000
#define USB_BCD_DEVICE 0x0100

enum { ITF_NCM = 0, ITF_NCM_DATA, ITF_ACM, ITF_ACM_DATA, ITF_DFU, ITF_TOTAL };
enum { STR_LANG, STR_MANUFACTURER, STR_PRODUCT, STR_SERIAL, STR_NCM, STR_MAC, STR_ACM, STR_DFU };

/* bmNetworkCapabilities: bit0 SET_ETHERNET_PACKET_FILTER, bit1 GET/SET_NET_ADDRESS */
#define NCM_CAPABILITIES 0x03
#define NCM_MAX_SEGMENT 1518

static const tusb_desc_device_t k_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,   /* IAD composite */
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = USB_BCD_DEVICE,
    .iManufacturer = STR_MANUFACTURER,
    .iProduct = STR_PRODUCT,
    .iSerialNumber = STR_SERIAL,
    .bNumConfigurations = 1,
};

#define CONFIG_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_NCM_DESC_LEN + TUD_CDC_DESC_LEN + TUD_DFU_DESC_LEN(1))

/* High speed (512-byte bulk) and full speed (64-byte bulk) variants. */
#define CONFIG_DESC(_bulk)                                                                         \
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CONFIG_LEN, 0, 100),                                    \
        TUD_CDC_NCM_DESCRIPTOR(ITF_NCM, STR_NCM, STR_MAC, 0x81, 16, 0x02, 0x82, _bulk, NCM_MAX_SEGMENT, \
                               9 /* HS: 2^(9-1) microframes = 32 ms; FS: 9 ms */, NCM_CAPABILITIES), \
        TUD_CDC_DESCRIPTOR(ITF_ACM, STR_ACM, 0x83, 8, 0x04, 0x84, _bulk),                         \
        /* download only; not manifestation-tolerant: the adapter resets to install */   \
        TUD_DFU_DESCRIPTOR(ITF_DFU, 1, STR_DFU, DFU_ATTR_CAN_DOWNLOAD, 1000, CFG_TUD_DFU_XFER_BUFSIZE)

static const uint8_t k_config_hs[] = {CONFIG_DESC(512)};
static const uint8_t k_config_fs[] = {CONFIG_DESC(64)};
TU_VERIFY_STATIC(sizeof k_config_hs == CONFIG_LEN, "configuration length");

static const tusb_desc_device_qualifier_t k_qualifier = {
    .bLength = sizeof(tusb_desc_device_qualifier_t),
    .bDescriptorType = TUSB_DESC_DEVICE_QUALIFIER,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .bNumConfigurations = 1,
};

static const uint8_t *s_mac;   /* macstore_t.active, set by usb_descriptors_bind() */

void usb_descriptors_bind(const uint8_t active_mac[6]) { s_mac = active_mac; }

uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *)&k_device; }

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return tud_speed_get() == TUSB_SPEED_HIGH ? k_config_hs : k_config_fs;
}

uint8_t const *tud_descriptor_device_qualifier_cb(void) { return (uint8_t const *)&k_qualifier; }

uint8_t const *tud_descriptor_other_speed_configuration_cb(uint8_t index)
{
    static uint8_t buf[CONFIG_LEN];
    (void)index;
    memcpy(buf, tud_speed_get() == TUSB_SPEED_HIGH ? k_config_fs : k_config_hs, CONFIG_LEN);
    buf[1] = TUSB_DESC_OTHER_SPEED_CONFIG;
    return buf;
}

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    static uint16_t desc[1 + 40];
    static const char hex[] = "0123456789ABCDEF";
    char tmp[40];
    const char *str;
    (void)langid;

    switch (index) {
    case STR_LANG:
        desc[1] = 0x0409;
        desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | 4);
        return desc;
    case STR_MANUFACTURER: str = "Nessum Adapter Project"; break;
    case STR_PRODUCT: str = "Nessum Adapter"; break;
    case STR_SERIAL: str = plat_serial(); break;
    case STR_NCM: str = "Nessum Ethernet"; break;
    case STR_ACM: str = "Nessum Adapter Mgmt"; break;
    case STR_DFU: str = "Nessum Adapter Firmware"; break;
    case STR_MAC:   /* 12 hex digits, as cdc_ncm / usbnet_get_ethernet_addr() expect */
        for (int i = 0; i < 6; i++) {
            tmp[2 * i] = hex[s_mac[i] >> 4];
            tmp[2 * i + 1] = hex[s_mac[i] & 15];
        }
        tmp[12] = '\0';
        str = tmp;
        break;
    default:
        return NULL;
    }
    size_t n = strlen(str);
    if (n > 40)
        n = 40;
    for (size_t i = 0; i < n; i++)
        desc[1 + i] = (uint8_t)str[i];
    desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * n + 2));
    return desc;
}

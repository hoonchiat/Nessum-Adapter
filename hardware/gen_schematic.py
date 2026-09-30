#!/usr/bin/env python3
"""Generate hardware/schematic.svg - the rev 0 (architecture-level) schematic, option A.

    python3 hardware/gen_schematic.py

Rev 0 shows every IC, connector, protection part and net with signal names.
The SC1320A pin names are placeholders and no pin numbers are shown: they come
from the datasheets, when the rev A schematic is drawn in an EDA tool (KiCad).
Options B and C (no microcontroller) are drawn by gen_options.py.
"""

import os

from schlib import *  # noqa: F401,F403

# ============================================================================ sheet
begin("USB 2.0 to Nessum adapter - schematic rev 0")

section(30, 40, 700, 350, "USB-C · PROTECTION · 3V3 POWER")
section(760, 40, 890, 350, "3V3 DISTRIBUTION · CLOCKS · DECOUPLING")
section(30, 410, 1080, 560, "BRIDGE MCU  ↔  NESSUM IC   (RMII MAC-to-MAC + UART)")
section(1130, 410, 520, 400, "LINE INTERFACE (field side)")
section(30, 990, 1080, 180, "STORAGE · MAC/KEY EEPROM · DEBUG")

# ---------------------------------------------------------------- USB-C & power
j1 = draw_usb_power("USB 2.0 bus-powered: ≤ 500 mA @ 5 V. Estimated load 0.9–1.4 W (spec §5).")

# ---------------------------------------------------------------- 3V3 distribution / clocks
dx = 800
text(dx, 80, "Each IC: 100 nF per supply pin + 10 µF bulk, placed at the pins.", size=11, fill=MUTED)
for i, (ref, who, val) in enumerate([("C10–C19", "U1 RT1062", "10×100nF + 2×10µF"),
                                     ("C20–C27", "U2 SC1320A", "per datasheet"),
                                     ("C30–C31", "U3 QSPI flash", "100nF + 1µF"),
                                     ("C32", "U4 EEPROM", "100nF")]):
    x = dx + 40 + i * 205
    pwr(x, 150, "+3V3")
    a, b = cap(x, 170, ref, val)
    gnd(*b)
    text(x, 225, who, size=11, anchor="middle", weight="bold")
# RT1062 core supply note
box(dx + 10, 250, 250, 118, "RT1062 core supply", ["internal DCDC: DCDC_LP → L2 4.7µH", "→ VDD_SOC_IN (+ bulk caps)",
                                                   "VDD_HIGH_IN / DCDC_IN / NVCC_* = +3V3",
                                                   "per NXP RT1060 HW design guide"])
# 24 MHz crystal
y1a, y1b = xtal(dx + 360, 300, "Y1", "24 MHz")
netlabel(y1a[0], y1a[1], "XTALI", side="left")
netlabel(y1b[0], y1b[1], "XTALO", side="right")
for px in (y1a[0], y1b[0]):
    a, b = cap(px, 336, "", "", vertical=True)
    wire((px, 300), a)
    gnd(*b)
text(dx + 420, 340, "load caps per crystal CL", size=10, fill=MUTED)
# Link / status LED
lx = 1600
netlabel(lx, 110, "LED_LINK", side="left")
wire((lx, 110), (lx, 130))
la, lb = led(lx, 150, "D4", "LINK")
ra, rb = res(lx, 200, "R16", "1k", vertical=True)
wire(lb, ra)
gnd(*rb)
# 50 MHz RMII oscillator (option)
rect(dx + 590, 262, 90, 70, fill="#fff", width=1.6, rx=4, dash="5 4")
text(dx + 635, 285, "Y2", size=12, anchor="middle", weight="bold")
text(dx + 635, 300, "50 MHz osc", size=10, anchor="middle", fill=MUTED)
text(dx + 635, 314, "(DNP option)", size=10, anchor="middle", fill=MUTED)
wire((dx + 680, 297), (dx + 700, 297))
netlabel(dx + 700, 297, "RMII_REF_CLK", side="right")
text(dx + 590, 352, "Fit only if neither the RT1062 nor the SC1320A", size=10, fill=MUTED)
text(dx + 590, 366, "sources the 50 MHz RMII reference clock.", size=10, fill=MUTED)

# ---------------------------------------------------------------- MCU
u1_left = ["USB_OTG1_DP", "USB_OTG1_DN", "USB_OTG1_VBUS", "",
           "FLEXSPI_A_SS0_B", "FLEXSPI_A_SCLK", "FLEXSPI_A_DATA0", "FLEXSPI_A_DATA1",
           "FLEXSPI_A_DATA2", "FLEXSPI_A_DATA3", "",
           "LPI2C1_SCL", "LPI2C1_SDA", "", "SWDIO", "SWCLK", "POR_B", "XTALI", "XTALO"]
u1_right = ["ENET_REF_CLK", "ENET_TX_EN", "ENET_TXD0", "ENET_TXD1", "ENET_RXD0", "ENET_RXD1",
            "ENET_CRS_DV", "ENET_RX_ER", "", "LPUART_TXD", "LPUART_RXD", "",
            "GPIO → NESSUM_RST_N", "GPIO ← NESSUM_INT", "", "GPIO → LED_LINK", "", "BOOT_MODE0/1"]
u1, u1h = ic(270, 470, 240, "U1", "NXP i.MX RT1062 (Cortex-M7)", u1_left, u1_right)
pwr(270 + 240 - 14, 470, "+3V3")
gnd(390, 470 + u1h)
for n, net in (("USB_OTG1_DP", "USB_DP"), ("USB_OTG1_DN", "USB_DN"), ("USB_OTG1_VBUS", "VBUS_F"),
               ("FLEXSPI_A_SS0_B", "QSPI_CS#"), ("FLEXSPI_A_SCLK", "QSPI_CLK"),
               ("FLEXSPI_A_DATA0", "QSPI_IO0"), ("FLEXSPI_A_DATA1", "QSPI_IO1"),
               ("FLEXSPI_A_DATA2", "QSPI_IO2"), ("FLEXSPI_A_DATA3", "QSPI_IO3"),
               ("LPI2C1_SCL", "I2C_SCL"), ("LPI2C1_SDA", "I2C_SDA"),
               ("SWDIO", "SWDIO"), ("SWCLK", "SWCLK"), ("POR_B", "nRESET"),
               ("XTALI", "XTALI"), ("XTALO", "XTALO")):
    netlabel(*u1[n], net, side="left")
netlabel(*u1["GPIO → LED_LINK"], "LED_LINK", side="right")
wire(u1["BOOT_MODE0/1"], (u1["BOOT_MODE0/1"][0] + 14, u1["BOOT_MODE0/1"][1]))
text(u1["BOOT_MODE0/1"][0] + 18, u1["BOOT_MODE0/1"][1] + 4, "straps: internal boot (fuses)", size=10, fill=MUTED)
text(u1["BOOT_MODE0/1"][0] + 18, u1["BOOT_MODE0/1"][1] + 18, "+ test pad for serial download", size=10, fill=MUTED)

# ---------------------------------------------------------------- Nessum IC
u2_left = ["RMII_REF_CLK", "RMII_CRS_DV", "RMII_RXD0", "RMII_RXD1", "RMII_TXD0", "RMII_TXD1",
           "RMII_TX_EN", "", "", "UART_RXD", "UART_TXD", "", "RESET_N", "INT / GPIO"]
u2_right = ["AFE_TXP", "AFE_TXN", "", "AFE_RXP", "AFE_RXN", "", "", "", "", "",
            "XTAL_IN", "XTAL_OUT"]
u2, u2h = ic(760, 470, 200, "U2", "Socionext SC1320A", u2_left, u2_right,
             bottom_note="pin names are placeholders until the SC1320A datasheet")
pwr(760 + 200 - 14, 470, "+3V3")
gnd(860, 470 + u2h)

# RMII / UART / control: straight wires, MAC-to-MAC crossover by name
pairs = [("ENET_REF_CLK", "RMII_REF_CLK"), ("ENET_TX_EN", "RMII_CRS_DV"), ("ENET_TXD0", "RMII_RXD0"),
         ("ENET_TXD1", "RMII_RXD1"), ("ENET_RXD0", "RMII_TXD0"), ("ENET_RXD1", "RMII_TXD1"),
         ("ENET_CRS_DV", "RMII_TX_EN"), ("LPUART_TXD", "UART_RXD"), ("LPUART_RXD", "UART_TXD"),
         ("GPIO → NESSUM_RST_N", "RESET_N"), ("GPIO ← NESSUM_INT", "INT / GPIO")]
for a, b in pairs:
    wire(u1[a], u2[b])
# 22 R series terminations on RMII TX lines (MCU -> IC) and REF_CLK
for i, n in enumerate(("ENET_TX_EN", "ENET_TXD0", "ENET_TXD1")):
    x, y = u1[n]
    rect(x + 60, y - 5, 22, 10, fill="#fff", width=1.3)
text(u1["ENET_TX_EN"][0] + 71, u1["ENET_REF_CLK"][1] - 30, "R10–R12", size=10, anchor="middle", weight="bold")
text(u1["ENET_TX_EN"][0] + 71, u1["ENET_REF_CLK"][1] - 17, "22Ω series", size=10, anchor="middle", fill=MUTED)
# REF_CLK net label tap (Y2 option joins here)
rx, ry = u1["ENET_REF_CLK"]
dot(rx + 120, ry)
wire((rx + 120, ry), (rx + 120, ry - 30))
netlabel(rx + 120, ry - 30, "RMII_REF_CLK", side="right")
# RX_ER pull-down
ex, ey = u1["ENET_RX_ER"]
wire(u1["ENET_RX_ER"], (ex + 40, ey))
ra, rb = res(ex + 40, ey + 26, "R13", "10k", vertical=True)
wire((ex + 40, ey), ra)
gnd(*rb)
text(ex + 64, ey + 4, "RX_ER unused: pull down", size=10, fill=MUTED)
# bus annotation
text(650, 442, "100 Mbit/s · TX→RX crossed · no PHY · no MDIO", size=10, anchor="middle", fill=WIRE)
text(650, u1["LPUART_RXD"][1] + 20, "UART: Nessum IC configuration / key load", size=10, anchor="middle", fill=MUTED)
# Nessum crystal
y3a, y3b = xtal(u2["XTAL_IN"][0] + 40, (u2["XTAL_IN"][1] + u2["XTAL_OUT"][1]) / 2, "Y3", "per datasheet")
wire(u2["XTAL_IN"], (u2["XTAL_IN"][0] + 10, u2["XTAL_IN"][1]), (u2["XTAL_IN"][0] + 10, y3a[1]), y3a)
wire(u2["XTAL_OUT"], (y3b[0] + 6, u2["XTAL_OUT"][1]), (y3b[0] + 6, y3b[1]), y3b)


# ---------------------------------------------------------------- Line interface
draw_line_interface(u2)

# ---------------------------------------------------------------- Storage / debug
u3, _ = ic(430, 1040, 140, "U3", "QSPI NOR 8–16 MB", ["CS#", "CLK", "IO0", "IO1"], ["IO2", "IO3", "", ""])
for n, net in (("CS#", "QSPI_CS#"), ("CLK", "QSPI_CLK"), ("IO0", "QSPI_IO0"), ("IO1", "QSPI_IO1")):
    netlabel(*u3[n], net, side="left")
for n, net in (("IO2", "QSPI_IO2"), ("IO3", "QSPI_IO3")):
    netlabel(*u3[n], net, side="right")
text(600, 1130, "firmware (XIP)", size=10, fill=MUTED)
text(600, 1144, "+ sealed key blob", size=10, fill=MUTED)

u4, _ = ic(790, 1040, 130, "U4", "24AA02E48 EEPROM", ["SCL", "SDA"], [])
netlabel(*u4["SCL"], "I2C_SCL", side="left")
netlabel(*u4["SDA"], "I2C_SDA", side="left")
text(855, 1134, "WP, A0–A2 → GND · R14/R15 4.7k to +3V3", size=10, anchor="middle", fill=MUTED)
text(855, 1149, "MAC ×2 + CRC, lock flag;", size=10, anchor="middle", fill=MUTED)
text(855, 1163, "default EUI-48 (read-only half)", size=10, anchor="middle", fill=MUTED)

j3 = connector(1025, 1026, "J3", "SWD", ["SWDIO", "SWCLK", "nRESET", "+3V3", "GND"], side="left")
for n in ("SWDIO", "SWCLK", "nRESET"):
    netlabel(*j3[n], n, side="left")
text(j3["+3V3"][0] - 4, j3["+3V3"][1] + 4, "+3V3", size=11, anchor="end", weight="bold", fill=PWR)
text(j3["GND"][0] - 4, j3["GND"][1] + 4, "GND", size=11, anchor="end", weight="bold")
text(80, 1146, "SWD (J3): Tag-Connect footprint,", size=10, fill=MUTED)
text(80, 1160, "service only; fuse-locked in production.", size=10, fill=MUTED)

# ---------------------------------------------------------------- notes + title block
draw_notes([
    "1. Rev 0 = architecture schematic: every part and net, no pin numbers.",
    "2. SC1320A pin names and its support parts are placeholders pending the datasheet (NDA).",
    "3. RMII is MAC-to-MAC: MCU TXD/TX_EN → IC RXD/CRS_DV and back. REF_CLK source TBD.",
    "4. Passive values are starting points; confirm against part datasheets / reference designs.",
    "5. Production: HAB secure boot + SWD fuse lock are mandatory (common network key).",
    "6. Next: draw rev A in KiCad from this sheet once the SC1320A datasheet is available.",
])
draw_title_block("Schematic — rev 0 (architecture, pre-datasheet)",
                 "Host: TI AM62x (eth2) · Bridge: i.MX RT1062 · Nessum: SC1320A",
                 "gen_schematic.py")

save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schematic.svg"))

#!/usr/bin/env python3
"""Generate hardware/schematic.svg - the rev 0 (architecture-level) schematic.

    python3 hardware/gen_schematic.py

Rev 0 shows every IC, connector, protection part and net with signal names.
The SC1320A pin names are placeholders and no pin numbers are shown: they come
from the datasheets, when the rev A schematic is drawn in an EDA tool (KiCad).
"""

import os
from xml.sax.saxutils import escape

W, H = 1680, 1188
PITCH = 22
STUB = 22

WIRE = "#1f5f99"
INK = "#1a1a1a"
MUTED = "#5b6470"
IC_FILL = "#fff8dc"
LABEL_FILL = "#eef4fb"
PWR = "#b3261e"
GND = "#1a1a1a"

out = []


def emit(s):
    out.append(s)


def text(x, y, s, size=11, anchor="start", weight="normal", fill=INK, family="DejaVu Sans, Arial, sans-serif",
         italic=False):
    style = ' font-style="italic"' if italic else ""
    emit(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
         f'fill="{fill}" font-family="{family}"{style}>{escape(s)}</text>')


def line(x1, y1, x2, y2, color=WIRE, width=1.6, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    emit(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{d}/>')


def wire(*pts, color=WIRE):
    p = " ".join(f"{x},{y}" for x, y in pts)
    emit(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>')


def dot(x, y):
    emit(f'<circle cx="{x}" cy="{y}" r="3.2" fill="{WIRE}"/>')


def rect(x, y, w, h, fill="none", stroke=INK, width=1.6, rx=0, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    emit(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" '
         f'stroke-width="{width}"{d}/>')


def section(x, y, w, h, title):
    rect(x, y, w, h, stroke="#9aa4b1", width=1.2, rx=8, dash="6 5")
    text(x + 12, y + 18, title, size=12, weight="bold", fill=MUTED)


def ic(x, y, w, ref, name, left, right, top_note=None, bottom_note=None):
    """IC symbol. left/right are lists of pin names ('' = gap). Returns pin endpoints."""
    rows = max(len(left), len(right))
    h = rows * PITCH + 36
    rect(x, y, w, h, fill=IC_FILL, width=2, rx=4)
    text(x + w / 2, y - 22, ref, size=15, anchor="middle", weight="bold")
    text(x + w / 2, y - 7, name, size=12, anchor="middle", fill=MUTED)
    pins = {}
    for i, n in enumerate(left):
        if not n:
            continue
        py = y + 30 + i * PITCH
        line(x - STUB, py, x, py, color=INK, width=1.4)
        text(x + 6, py + 4, n, size=11)
        pins[n] = (x - STUB, py)
    for i, n in enumerate(right):
        if not n:
            continue
        py = y + 30 + i * PITCH
        line(x + w, py, x + w + STUB, py, color=INK, width=1.4)
        text(x + w - 6, py + 4, n, size=11, anchor="end")
        pins[n] = (x + w + STUB, py)
    if bottom_note:
        text(x + w / 2, y + h + 36, bottom_note, size=10, anchor="middle", fill=MUTED)
    return pins, h


def netlabel(x, y, name, side="left"):
    """Net label flag attached at (x, y); side = which way the flag points."""
    w = 7.2 * len(name) + 16
    if side == "left":
        pts = [(x, y), (x - 8, y - 9), (x - w, y - 9), (x - w, y + 9), (x - 8, y + 9)]
        tx, anchor = x - 12, "end"
    else:
        pts = [(x, y), (x + 8, y - 9), (x + w, y - 9), (x + w, y + 9), (x + 8, y + 9)]
        tx, anchor = x + 12, "start"
    p = " ".join(f"{a},{b}" for a, b in pts)
    emit(f'<polygon points="{p}" fill="{LABEL_FILL}" stroke="{WIRE}" stroke-width="1.2"/>')
    text(tx, y + 4, name, size=11, anchor=anchor, fill="#123f6b")


def pwr(x, y, name="+3V3", up=True):
    """Power flag: bar with name, wire going down (up=True) from (x, y)."""
    yy = y - 14 if up else y + 14
    line(x, y, x, yy, color=PWR, width=1.6)
    line(x - 10, yy, x + 10, yy, color=PWR, width=2.4)
    text(x, yy - 6 if up else yy + 16, name, size=11, anchor="middle", weight="bold", fill=PWR)


def gnd(x, y):
    line(x, y, x, y + 10, color=GND, width=1.6)
    line(x - 11, y + 10, x + 11, y + 10, color=GND, width=2)
    line(x - 7, y + 14, x + 7, y + 14, color=GND, width=2)
    line(x - 3, y + 18, x + 3, y + 18, color=GND, width=2)


def res(x, y, ref, val, vertical=False):
    """IEC resistor centred on (x, y); pins at +-20."""
    if vertical:
        rect(x - 6, y - 14, 12, 28, fill="#fff", width=1.6)
        line(x, y - 20, x, y - 14, color=INK)
        line(x, y + 14, x, y + 20, color=INK)
        text(x + 11, y - 2, ref, size=11, weight="bold")
        text(x + 11, y + 11, val, size=10, fill=MUTED)
        return (x, y - 20), (x, y + 20)
    rect(x - 14, y - 6, 28, 12, fill="#fff", width=1.6)
    line(x - 20, y, x - 14, y, color=INK)
    line(x + 14, y, x + 20, y, color=INK)
    text(x, y - 11, f"{ref} {val}", size=10, anchor="middle")
    return (x - 20, y), (x + 20, y)


def cap(x, y, ref, val, vertical=True, label_left=False):
    if vertical:
        line(x, y - 20, x, y - 4, color=INK)
        line(x - 10, y - 4, x + 10, y - 4, color=INK, width=2.2)
        line(x - 10, y + 4, x + 10, y + 4, color=INK, width=2.2)
        line(x, y + 4, x, y + 20, color=INK)
        tx = x - 14 if label_left else x + 14
        anchor = "end" if label_left else "start"
        text(tx, y - 2, ref, size=11, weight="bold", anchor=anchor)
        text(tx, y + 11, val, size=10, fill=MUTED, anchor=anchor)
        return (x, y - 20), (x, y + 20)
    line(x - 20, y, x - 4, y, color=INK)
    line(x - 4, y - 10, x - 4, y + 10, color=INK, width=2.2)
    line(x + 4, y - 10, x + 4, y + 10, color=INK, width=2.2)
    line(x + 4, y, x + 20, y, color=INK)
    text(x, y - 15, ref, size=11, weight="bold", anchor="middle")
    text(x, y + 24, val, size=10, fill=MUTED, anchor="middle")
    return (x - 20, y), (x + 20, y)


def inductor(x, y, ref, val):
    line(x - 24, y, x - 16, y, color=INK)
    for i in range(4):
        cx = x - 12 + i * 8
        emit(f'<path d="M{cx - 4},{y} a4,4 0 0 1 8,0" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    line(x + 16, y, x + 24, y, color=INK)
    text(x, y - 10, f"{ref} {val}", size=10, anchor="middle")
    return (x - 24, y), (x + 24, y)


def ferrite(x, y, ref, val):
    line(x - 20, y, x - 10, y, color=INK)
    rect(x - 10, y - 6, 20, 12, fill=INK, width=1)
    line(x + 10, y, x + 20, y, color=INK)
    text(x, y - 11, f"{ref} {val}", size=10, anchor="middle")
    return (x - 20, y), (x + 20, y)


def ptc(x, y, ref, val):
    line(x - 20, y, x - 12, y, color=INK)
    rect(x - 12, y - 6, 24, 12, fill="#fff", width=1.6)
    line(x - 16, y + 9, x + 16, y - 9, color=INK, width=1.2)
    line(x + 12, y, x + 20, y, color=INK)
    text(x, y - 13, f"{ref} {val}", size=10, anchor="middle")
    return (x - 20, y), (x + 20, y)


def tvs_bidir(x, y, ref, val):
    """Vertical bidirectional TVS between (x, y-24) and (x, y+24)."""
    line(x, y - 24, x, y - 12, color=INK)
    emit(f'<polygon points="{x - 8},{y - 12} {x + 8},{y - 12} {x},{y}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    emit(f'<polygon points="{x - 8},{y + 12} {x + 8},{y + 12} {x},{y}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    line(x - 10, y, x + 10, y, color=INK, width=2)
    line(x, y + 12, x, y + 24, color=INK)
    text(x + 14, y - 2, ref, size=11, weight="bold")
    text(x + 14, y + 11, val, size=10, fill=MUTED)
    return (x, y - 24), (x, y + 24)


def led(x, y, ref, val):
    line(x, y - 20, x, y - 8, color=INK)
    emit(f'<polygon points="{x - 8},{y - 8} {x + 8},{y - 8} {x},{y + 6}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    line(x - 8, y + 6, x + 8, y + 6, color=INK, width=2)
    line(x, y + 6, x, y + 20, color=INK)
    line(x + 10, y - 4, x + 18, y - 12, color=INK, width=1.2)
    line(x + 12, y + 2, x + 20, y - 6, color=INK, width=1.2)
    text(x - 14, y - 2, ref, size=11, weight="bold", anchor="end")
    text(x - 14, y + 11, val, size=10, fill=MUTED, anchor="end")
    return (x, y - 20), (x, y + 20)


def transformer(x, y, ref, val):
    """1:1 signal transformer; primary pins at x-40 (y-30, y+30), secondary at x+40."""
    for side in (-1, 1):
        cx = x + side * 14
        for i in range(4):
            cy = y - 24 + i * 12 + 6
            sweep = 0 if side < 0 else 1
            emit(f'<path d="M{cx},{cy - 6} a6,6 0 0 {sweep} 0,12" fill="none" stroke="{INK}" stroke-width="1.6"/>')
        line(cx, y - 30, cx, y - 24, color=INK)
        line(cx, y + 24, cx, y + 30, color=INK)
        line(cx, y - 30, x + side * 40, y - 30, color=INK)
        line(cx, y + 24 + 6, x + side * 40, y + 30, color=INK)
    line(x - 3, y - 30, x - 3, y + 30, color=INK, width=1.4)
    line(x + 3, y - 30, x + 3, y + 30, color=INK, width=1.4)
    text(x - 10, y - 38, ref, size=12, anchor="end", weight="bold")
    text(x - 10, y + 70, val, size=10, anchor="end", fill=MUTED)
    return {"p1": (x - 40, y - 30), "p2": (x - 40, y + 30), "s1": (x + 40, y - 30), "s2": (x + 40, y + 30)}


def xtal(x, y, ref, val):
    line(x - 20, y, x - 10, y, color=INK)
    line(x - 10, y - 10, x - 10, y + 10, color=INK, width=2)
    rect(x - 6, y - 12, 12, 24, fill="#fff", width=1.6)
    line(x + 10, y - 10, x + 10, y + 10, color=INK, width=2)
    line(x + 10, y, x + 20, y, color=INK)
    text(x, y - 18, f"{ref} {val}", size=10, anchor="middle")
    return (x - 20, y), (x + 20, y)


def connector(x, y, ref, name, pins, side="right"):
    h = len(pins) * PITCH + 12
    rect(x, y, 70, h, fill="#f3f3f3", width=2, rx=3)
    text(x + 35, y - 22, ref, size=15, anchor="middle", weight="bold")
    text(x + 35, y - 7, name, size=11, anchor="middle", fill=MUTED)
    ends = {}
    for i, n in enumerate(pins):
        py = y + 18 + i * PITCH
        if side == "right":
            line(x + 70, py, x + 70 + STUB, py, color=INK, width=1.4)
            text(x + 64, py + 4, n, size=11, anchor="end")
            ends[n] = (x + 70 + STUB, py)
        else:
            line(x - STUB, py, x, py, color=INK, width=1.4)
            text(x + 6, py + 4, n, size=11)
            ends[n] = (x - STUB, py)
    return ends


def box(x, y, w, h, title, lines_, dashed=False):
    rect(x, y, w, h, fill="#fafafa", width=1.6, rx=4, dash="5 4" if dashed else None)
    text(x + w / 2, y + 18, title, size=12, anchor="middle", weight="bold")
    for i, s in enumerate(lines_):
        text(x + w / 2, y + 36 + i * 14, s, size=10, anchor="middle", fill=MUTED)


# ============================================================================ sheet
emit(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
emit('<title>USB 2.0 to Nessum adapter - schematic rev 0</title>')
rect(0, 0, W, H, fill="#ffffff", stroke="none")
rect(12, 12, W - 24, H - 24, stroke=INK, width=2)

section(30, 40, 700, 350, "USB-C · PROTECTION · 3V3 POWER")
section(760, 40, 890, 350, "3V3 DISTRIBUTION · CLOCKS · DECOUPLING")
section(30, 410, 1080, 560, "BRIDGE MCU  ↔  NESSUM IC   (RMII MAC-to-MAC + UART)")
section(1130, 410, 520, 400, "LINE INTERFACE (field side)")
section(30, 990, 1080, 180, "STORAGE · MAC/KEY EEPROM · DEBUG")

# ---------------------------------------------------------------- USB-C & power
j1 = connector(60, 110, "J1", "USB-C receptacle", ["VBUS", "D+", "D-", "CC1", "CC2", "GND", "SHIELD"])
# VBUS path: PTC -> ferrite -> VBUS_F
NODE = 330
f1a, f1b = ptc(190, j1["VBUS"][1], "F1", "PTC 0.5 A")
wire(j1["VBUS"], f1a)
fb1a, fb1b = ferrite(275, j1["VBUS"][1], "FB1", "600R@100M")
wire(f1b, fb1a)
wire(fb1b, (420, j1["VBUS"][1]), (420, 150), (448, 150))
dot(NODE, j1["VBUS"][1])
text(NODE + 20, j1["VBUS"][1] - 6, "VBUS_F", size=11, anchor="middle", fill="#123f6b")
# USB data through ESD array to net labels
esd_x = 250
rect(esd_x - 30, j1["D+"][1] - 12, 60, 38, fill="#fff", width=1.6)
text(esd_x, j1["D+"][1] + 4, "D2", size=11, anchor="middle", weight="bold")
text(esd_x, j1["D+"][1] + 18, "USB ESD", size=9, anchor="middle", fill=MUTED)
text(esd_x, j1["D-"][1] + 32, "e.g. USBLC6-2", size=9, anchor="middle", fill=MUTED)
wire(j1["D+"], (esd_x - 30, j1["D+"][1]))
wire(j1["D-"], (esd_x - 30, j1["D-"][1]))
wire((esd_x + 30, j1["D+"][1]), (NODE, j1["D+"][1]))
wire((esd_x + 30, j1["D-"][1]), (NODE, j1["D-"][1]))
netlabel(NODE, j1["D+"][1], "USB_DP", side="right")
netlabel(NODE, j1["D-"][1], "USB_DN", side="right")
# CC pull-downs (USB-C sink)
for pin, rx, ref in (("CC1", 175, "R1"), ("CC2", 235, "R2")):
    wire(j1[pin], (rx, j1[pin][1]))
    a, b = res(rx, j1[pin][1] + 34, ref, "5.1k", vertical=True)
    wire((rx, j1[pin][1]), a)
    gnd(*b)
gnd(j1["GND"][0] + 8, j1["GND"][1])
wire(j1["GND"], (j1["GND"][0] + 8, j1["GND"][1]))
text(60, 300, "SHIELD → chassis via 1 MΩ ∥ 4.7 nF", size=10, fill=MUTED)

# Buck regulator 5 V -> 3.3 V
u5, u5h = ic(470, 120, 130, "U5", "Buck 5V→3.3V ≥1 A",
             ["VIN", "EN", "", "GND"], ["SW", "", "FB", ""])
wire(u5["EN"], (u5["EN"][0] - 12, u5["EN"][1]), (u5["EN"][0] - 12, u5["VIN"][1]))
dot(u5["EN"][0] - 12, u5["VIN"][1])
gnd(*u5["GND"])
ci_a, ci_b = cap(410, 290, "C1", "10µF", label_left=True)
wire(ci_a, (ci_a[0], ci_a[1] - 16))
netlabel(ci_a[0], ci_a[1] - 16, "VBUS_F", side="left")
gnd(*ci_b)
l1a, l1b = inductor(u5["SW"][0] + 34, u5["SW"][1], "L1", "2.2µH")
wire(u5["SW"], l1a)
out_x = l1b[0] + 20
wire(l1b, (out_x, l1b[1]))
dot(out_x, l1b[1])
pwr(out_x, l1b[1], "+3V3")
co_a, co_b = cap(out_x, l1b[1] + 50, "C2", "2×22µF")
wire((out_x, l1b[1]), co_a)
gnd(*co_b)
# feedback divider
wire(u5["FB"], (u5["FB"][0] + 10, u5["FB"][1]))
text(535, 262, "FB: divider per regulator datasheet", size=10, anchor="middle", fill=MUTED)
text(60, 372, "USB 2.0 bus-powered: ≤ 500 mA @ 5 V. Estimated load 0.9–1.4 W (spec §5).", size=10, fill=MUTED)

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
hx = 1150
box(hx, 470, 120, 118, "TX/RX hybrid", ["+ band filter", "(U6 line driver", " if required)", "per vendor ref."])
for n, yy in (("AFE_TXP", 496), ("AFE_TXN", 510), ("AFE_RXP", 548), ("AFE_RXN", 562)):
    x, y = u2[n]
    wire((x, y), (x + 30, y), (x + 30, yy), (hx, yy))
TX = 1330
t1 = transformer(TX, 529, "T1", "wideband 1:1")
wire((hx + 120, 499), (TX - 40, 499))
wire((hx + 120, 559), (TX - 40, 559))
# isolation barrier
line(TX, 445, TX, 668, color="#b3261e", width=1.6, dash="8 6")
text(TX - 10, 664, "host / USB ground", size=10, anchor="end", fill="#b3261e")
text(TX + 10, 664, "field wiring", size=10, fill="#b3261e")
text(TX, 455, "FUNCTIONAL ISOLATION", size=10, anchor="middle", weight="bold", fill="#b3261e")
# DC-blocking caps
c3a, c3b = cap(1410, t1["s1"][1], "C3", "≥100 V", vertical=False)
c4a, c4b = cap(1410, t1["s2"][1], "C4", "≥100 V", vertical=False)
wire(t1["s1"], c3a)
wire(t1["s2"], c4a)
# TVS across line
DX = 1475
wire(c3b, (DX, t1["s1"][1]))
wire(c4b, (DX, t1["s2"][1]))
ta, tb = tvs_bidir(DX, 529, "", "")
text(DX - 12, 600, "D1 bidir TVS", size=11, weight="bold")
text(DX - 12, 613, "36–40 V standoff", size=10, fill=MUTED)
wire((DX, t1["s1"][1]), ta)
wire((DX, t1["s2"][1]), tb)
dot(DX, t1["s1"][1])
dot(DX, t1["s2"][1])
# Line connector
j2 = connector(1565, 482, "J2", "2-pin 5.08 mm", ["LINE_A", "LINE_B"], side="left")
wire((DX, t1["s1"][1]), (1515, t1["s1"][1]), (1515, j2["LINE_A"][1]), j2["LINE_A"])
wire((DX, t1["s2"][1]), (1525, t1["s2"][1]), (1525, j2["LINE_B"][1]), j2["LINE_B"])
# GDT to chassis (option)
rect(1530, 632, 110, 40, fill="#fff", width=1.6, rx=4, dash="5 4")
text(1585, 650, "D3 GDT / CM TVS", size=10, anchor="middle", weight="bold")
text(1585, 664, "line → chassis (opt.)", size=9, anchor="middle", fill=MUTED)
wire((1525, t1["s2"][1]), (1525, 575), (1585, 575), (1585, 632))
dot(1525, t1["s2"][1])
text(1150, 690, "Medium: existing RS-485 twisted pair  OR  24 V AC/DC cable (SELV only, no mains).", size=10,
     fill=MUTED)
text(1150, 706, "C3/C4 pass 2–28 MHz and block 24 V DC / 50–60 Hz AC.", size=10, fill=MUTED)
text(1150, 722, "D1 must not conduct on 24 V AC peaks (~34 V).", size=10, fill=MUTED)
text(1150, 738, "24 V medium: fit RF chokes at each load and at the supply (spec §6.2).", size=10, fill=MUTED)

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
nx, ny = 1130, 830
rect(nx, ny, 520, 140, fill="#fbfbfb", stroke="#9aa4b1", width=1.2, rx=6)
text(nx + 12, ny + 20, "NOTES", size=12, weight="bold")
for i, s in enumerate([
    "1. Rev 0 = architecture schematic: every part and net, no pin numbers.",
    "2. SC1320A pin names and its support parts are placeholders pending the datasheet (NDA).",
    "3. RMII is MAC-to-MAC: MCU TXD/TX_EN → IC RXD/CRS_DV and back. REF_CLK source TBD.",
    "4. Passive values are starting points; confirm against part datasheets / reference designs.",
    "5. Production: HAB secure boot + SWD fuse lock are mandatory (common network key).",
    "6. Next: draw rev A in KiCad from this sheet once the SC1320A datasheet is available.",
]):
    text(nx + 12, ny + 42 + i * 16, s, size=10, fill=INK)

tx, ty = 1130, 990
rect(tx, ty, 520, 180, fill="#fff", width=2)
line(tx, ty + 60, tx + 520, ty + 60, color=INK, width=1.2)
line(tx, ty + 120, tx + 520, ty + 120, color=INK, width=1.2)
line(tx + 260, ty + 120, tx + 260, ty + 180, color=INK, width=1.2)
text(tx + 14, ty + 26, "USB 2.0 ↔ Nessum Adapter", size=18, weight="bold")
text(tx + 14, ty + 48, "hoonchiat/Nessum-Adapter · Apache-2.0", size=11, fill=MUTED)
text(tx + 14, ty + 84, "Schematic — rev 0 (architecture, pre-datasheet)", size=13, weight="bold")
text(tx + 14, ty + 104, "Host: TI AM62x (eth2) · Bridge: i.MX RT1062 · Nessum: SC1320A", size=11, fill=MUTED)
text(tx + 14, ty + 144, "Date: 2026-09-30", size=11)
text(tx + 14, ty + 164, "Generated by hardware/gen_schematic.py", size=10, fill=MUTED)
text(tx + 274, ty + 144, "Sheet 1 / 1", size=11)
text(tx + 274, ty + 164, "Not for layout", size=11, weight="bold", fill="#b3261e")

emit("</svg>")

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schematic.svg")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print(f"wrote {path}")

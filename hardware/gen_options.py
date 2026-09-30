#!/usr/bin/env python3
"""Generate the rev 0 schematics for the no-microcontroller options B and C.

    python3 hardware/gen_options.py

  B: ASIX AX88772C (USB 2.0 <-> Reverse-RMII) + SC1320A. The Nessum network key
     is loaded through test pads by a factory fixture; no host-side management.
  C: USB 2.0 hub + AX88772C + CP2102N USB-UART, one USB plug. The host gets eth2
     and a serial port to the SC1320A for status and key programming.

Writes hardware/options/schematic-option-b.svg and -c.svg. As on the option A
sheet, pin names are functional (per datasheet at rev A) and no pin numbers are
shown. See docs/ALTERNATIVES.md for the comparison.
"""

import os

from schlib import *  # noqa: F401,F403

HERE = os.path.dirname(os.path.abspath(__file__))


def decoupling(groups):
    dx = 800
    text(dx, 80, "Each IC: 100 nF per supply pin + 10 µF bulk, placed at the pins.", size=11, fill=MUTED)
    step = 760 // len(groups)
    for i, (ref, who, val) in enumerate(groups):
        x = dx + 40 + i * step
        pwr(x, 150, "+3V3")
        a, b = cap(x, 170, ref, val)
        gnd(*b)
        text(x, 225, who, size=11, anchor="middle", weight="bold")


def crystal(x, y, ref, val, left_net, right_net):
    a, b = xtal(x, y, ref, val)
    netlabel(a[0], a[1], left_net, side="left")
    netlabel(b[0], b[1], right_net, side="right")
    for px in (a[0], b[0]):
        ca, cb = cap(px, y + 36, "", "", vertical=True)
        wire((px, y), ca)
        gnd(*cb)


def bridge_and_nessum(option):
    """AX88772C (U1) and SC1320A (U2) with the Reverse-RMII bus between them."""
    usb_p, usb_n = ("USB_DP", "USB_DN") if option == "B" else ("HUB_D1_DP", "HUB_D1_DN")
    u1_left = ["USB_DP", "USB_DM", "", "EECS", "EECK", "EEDI", "EEDO", "", "XTAL_IN", "XTAL_OUT",
               "", "RESET_N", "", "LED (activity)"]
    u1_right = ["RMII_REF_CLK", "RMII_CRS_DV", "RMII_RXD0", "RMII_RXD1", "RMII_TXD0", "RMII_TXD1",
                "RMII_TX_EN", "", "", "", "", "MFA/MFB mode straps"]
    u1, u1h = ic(270, 470, 240, "U1", "ASIX AX88772C", u1_left, u1_right,
                 bottom_note="Reverse-RMII = AX88772C acts as the PHY side (MAC-to-MAC)")
    pwr(270 + 240 - 14, 470, "+3V3")
    gnd(390, 470 + u1h)
    for n, net in (("USB_DP", usb_p), ("USB_DM", usb_n), ("EECS", "EE_CS"), ("EECK", "EE_CLK"),
                   ("EEDI", "EE_DI"), ("EEDO", "EE_DO"), ("XTAL_IN", "AX_XI"), ("XTAL_OUT", "AX_XO"),
                   ("RESET_N", "nRST_BRIDGE"), ("LED (activity)", "LED_ACT")):
        netlabel(*u1[n], net, side="left")
    wire(u1["MFA/MFB mode straps"], (u1["MFA/MFB mode straps"][0] + 14, u1["MFA/MFB mode straps"][1]))
    text(u1["MFA/MFB mode straps"][0] + 18, u1["MFA/MFB mode straps"][1] + 4,
         "→ Reverse-RMII (PHY mode)", size=10, fill=MUTED)

    u2_left = ["RMII_REF_CLK", "RMII_CRS_DV", "RMII_RXD0", "RMII_RXD1", "RMII_TXD0", "RMII_TXD1",
               "RMII_TX_EN", "", "", "UART_RXD", "UART_TXD", "", "RESET_N", "INT / GPIO"]
    u2_right = ["AFE_TXP", "AFE_TXN", "", "AFE_RXP", "AFE_RXN", "", "", "", "", "",
                "XTAL_IN", "XTAL_OUT"]
    u2, u2h = ic(760, 470, 200, "U2", "Socionext SC1320A", u2_left, u2_right,
                 bottom_note="pin names are placeholders until the SC1320A datasheet")
    pwr(760 + 200 - 14, 470, "+3V3")
    gnd(860, 470 + u2h)
    # Reverse-RMII: the bridge is the PHY side, so signals connect by name (no crossover).
    for n in ("RMII_REF_CLK", "RMII_CRS_DV", "RMII_RXD0", "RMII_RXD1", "RMII_TXD0", "RMII_TXD1", "RMII_TX_EN"):
        wire(u1[n], u2[n])
    rx, ry = u1["RMII_REF_CLK"]
    dot(rx + 120, ry)
    wire((rx + 120, ry), (rx + 120, ry - 30))
    netlabel(rx + 120, ry - 30, "RMII_REF_CLK", side="right")
    text(635, 658, "Reverse-RMII 100 Mbit/s, straight by name", size=10, anchor="middle", fill=WIRE)
    text(635, 672, "(bridge = PHY side) · no MDIO", size=10, anchor="middle", fill=MUTED)
    # UART / control of the Nessum IC go to net labels (fixture pads in B, CP2102N in C).
    netlabel(*u2["UART_RXD"], "NESSUM_UART_RX", side="left")
    netlabel(*u2["UART_TXD"], "NESSUM_UART_TX", side="left")
    netlabel(*u2["RESET_N"], "NESSUM_RST_N", side="left")
    netlabel(*u2["INT / GPIO"], "NESSUM_INT", side="left")
    y3a, y3b = xtal(u2["XTAL_IN"][0] + 40, (u2["XTAL_IN"][1] + u2["XTAL_OUT"][1]) / 2, "Y3", "per datasheet")
    wire(u2["XTAL_IN"], (u2["XTAL_IN"][0] + 10, u2["XTAL_IN"][1]), (u2["XTAL_IN"][0] + 10, y3a[1]), y3a)
    wire(u2["XTAL_OUT"], (y3b[0] + 6, u2["XTAL_OUT"][1]), (y3b[0] + 6, y3b[1]), y3b)
    return u1, u2


def eeprom(x, y):
    """AX88772C configuration EEPROM (93C56/93C66, x16). No write-protect pin exists
    on AX88772C-compatible parts, so the MAC cannot be hardware-locked."""
    u4, _ = ic(x, y, 140, "U4", "93LC56C EEPROM (µWire)", ["CS", "CLK", "DI", "DO"], ["ORG", "", "", ""])
    for n, net in (("CS", "EE_CS"), ("CLK", "EE_CLK"), ("DI", "EE_DI"), ("DO", "EE_DO")):
        netlabel(*u4[n], net, side="left")
    ox, oy = u4["ORG"]
    wire(u4["ORG"], (ox + 16, oy))
    text(ox + 20, oy + 4, "+3V3 (x16 mode)", size=10, weight="bold", fill=PWR)
    text(ox + 4, oy + 26, "MAC, VID/PID, strings.", size=10, fill=MUTED)
    text(ox + 4, oy + 40, "No write-protect pin:", size=10, fill="#b3261e")
    text(ox + 4, oy + 54, "MAC is not lockable.", size=10, fill="#b3261e")


def test_pads(x, y, nets, title="J3", name="factory test pads"):
    pads = [f"TP{i + 1}" for i in range(len(nets))]
    j = connector(x, y, title, name, pads, side="left")
    for pad, n in zip(pads, nets):
        if n in ("+3V3", "GND"):
            text(j[pad][0] - 4, j[pad][1] + 4, n, size=11, anchor="end", weight="bold",
                 fill=PWR if n == "+3V3" else INK)
        else:
            netlabel(*j[pad], n, side="left")
    return j


def sheet(option):
    title = {"B": "USB 2.0 to Nessum adapter - option B (AX88772C, key via factory fixture)",
             "C": "USB 2.0 to Nessum adapter - option C (hub + AX88772C + CP2102N)"}[option]
    begin(title)
    section(30, 40, 700, 350, "USB-C · PROTECTION · 3V3 POWER")
    section(760, 40, 890, 350, "3V3 DISTRIBUTION · CLOCKS · DECOUPLING")
    section(30, 410, 1080, 560, "USB-ETHERNET BRIDGE  ↔  NESSUM IC   (Reverse-RMII MAC-to-MAC)")
    section(1130, 410, 520, 400, "LINE INTERFACE (field side)")
    if option == "B":
        section(30, 990, 1080, 180, "EEPROM · FACTORY PADS")
    else:  # title right-aligned: the left of this row is full
        rect(30, 990, 1080, 180, stroke="#9aa4b1", width=1.2, rx=8, dash="6 5")
        text(1098, 1008, "USB HUB · USB-UART · EEPROM", size=12, weight="bold", fill=MUTED, anchor="end")

    draw_usb_power("USB 2.0 bus-powered: ≤ 500 mA @ 5 V. Load estimate TBD from the "
                   + ("AX88772C / SC1320A" if option == "B" else "hub / AX88772C / CP2102N / SC1320A")
                   + " datasheets.")

    # ---- 3V3 distribution / clocks
    groups = [("C10–C15", "U1 AX88772C", "per datasheet"), ("C20–C27", "U2 SC1320A", "per datasheet"),
              ("C32", "U4 EEPROM", "100nF")]
    if option == "C":
        groups += [("C40–C44", "U7 USB hub", "per datasheet"), ("C50–C51", "U8 CP2102N", "100nF + 1µF")]
    decoupling(groups)
    box(810, 250, 250, 118, "Core supplies", ["AX88772C" + (" / hub" if option == "C" else "")
                                              + ": internal regulator", "or external LDO per datasheet",
                                              "SC1320A: single +3V3", "CP2102N: internal oscillator" if option == "C"
                                              else "no MCU, no firmware"])
    crystal(1160, 300, "Y1", "per AX88772C", "AX_XI", "AX_XO")
    rect(1390, 262, 90, 70, fill="#fff", width=1.6, rx=4, dash="5 4")
    text(1435, 285, "Y2", size=12, anchor="middle", weight="bold")
    text(1435, 300, "50 MHz osc", size=10, anchor="middle", fill=MUTED)
    text(1435, 314, "(DNP option)", size=10, anchor="middle", fill=MUTED)
    wire((1480, 297), (1500, 297))
    netlabel(1500, 297, "RMII_REF_CLK", side="right")
    text(1390, 352, "Fit only if neither the AX88772C nor the SC1320A", size=10, fill=MUTED)
    text(1390, 366, "sources the 50 MHz RMII reference clock.", size=10, fill=MUTED)

    # ---- bridge + Nessum IC
    u1, u2 = bridge_and_nessum(option)
    text(650, 900, "Linux: asix driver → eth2. Carrier is always up (no PHY link);",
         size=10, anchor="middle", fill=MUTED)
    text(650, 914, "the Nessum link state is " + ("not visible to the host." if option == "B"
                                                   else "read over the CP2102N serial port."),
         size=10, anchor="middle", fill=MUTED)
    if option == "C":
        crystal(170, 900, "Y4", "per hub", "HUB_XI", "HUB_XO")
    netlabel(700, 950, "nRST_BRIDGE", side="left")
    text(706, 954, "← RC power-on reset (R21 10k / C60 1µF)", size=10, fill=MUTED)

    # ---- line side (identical to option A)
    draw_line_interface(u2)

    # ---- bottom section
    if option == "B":
        eeprom(190, 1040)
        test_pads(1000, 1026, ["NESSUM_UART_TX", "NESSUM_UART_RX", "NESSUM_RST_N", "+3V3", "GND"])
        text(500, 1030, "Fixture (pogo pins + USB-UART on the fixture):", size=10, fill=INK, weight="bold")
        for i, s in enumerate(["• loads the network key into the SC1320A over its UART",
                               "• MAC is written into U4 over USB (ASIX SROM tool)",
                               "• in the field these pads are unconnected:",
                               "  the host has no path to the SC1320A UART",
                               "NESSUM_RST_N: 10k pull-up; NESSUM_INT: n.c.",
                               "LED_ACT → D4 + R16 1k (activity, not Nessum link)"]):
            text(510, 1048 + i * 15, s, size=10, fill=MUTED)
    else:
        eeprom(110, 1040)
        u7, _ = ic(480, 1040, 130, "U7", "USB2422 2-port HS hub", ["UP_DP", "UP_DM", "XTAL_IN", "XTAL_OUT"],
                   ["DN2_DP", "DN2_DM", "DN1_DP", "DN1_DM"])
        for n, net in (("UP_DP", "USB_DP"), ("UP_DM", "USB_DN"), ("XTAL_IN", "HUB_XI"), ("XTAL_OUT", "HUB_XO")):
            netlabel(*u7[n], net, side="left")
        for n, net in (("DN1_DP", "HUB_D1_DP"), ("DN1_DM", "HUB_D1_DN")):
            netlabel(*u7[n], net, side="right")
        u8, _ = ic(780, 1040, 120, "U8", "CP2102N USB-UART", ["USB_DP", "USB_DM", "", ""],
                   ["TXD", "RXD", "GPIO0", "GPIO1"])
        wire(u7["DN2_DP"], u8["USB_DP"])
        wire(u7["DN2_DM"], u8["USB_DM"])
        for n, net in (("TXD", "NESSUM_UART_RX"), ("RXD", "NESSUM_UART_TX"),
                       ("GPIO0", "NESSUM_RST_N"), ("GPIO1", "NESSUM_INT")):
            x, y = u8[n]
            if n in ("TXD", "RXD"):
                rect(x + 4, y - 5, 22, 10, fill="#fff", width=1.3)
                wire((x + 26, y), (x + 34, y))
                netlabel(x + 34, y, net, side="right")
            else:
                netlabel(x, y, net, side="right")
        text(u8["TXD"][0] + 15, u8["TXD"][1] - 10, "R22/R23 0Ω", size=9, anchor="middle", fill=MUTED)

    # ---- notes + title
    common = ["1. Rev 0 = architecture schematic: every part and net, no pin numbers.",
              "2. SC1320A / AX88772C / hub pin names are functional; exact names per datasheets.",
              "3. Reverse-RMII: AX88772C is the PHY side, so RMII nets connect by name.",
              "4. MAC in U4 (93C56/66): no write-protect pin on supported parts → root can change it."]
    if option == "B":
        notes = common + ["5. Key lock depends ENTIRELY on the SC1320A (see docs/ALTERNATIVES.md).",
                          "6. No firmware to write: AX88772C is fixed-function (asix driver)."]
    else:
        notes = common + ["5. Host can reach the SC1320A UART: key lock must be enforced by the SC1320A.",
                          "6. R22/R23 (0Ω): remove to cut host access to the UART if it cannot be locked."]
    draw_notes(notes)
    draw_title_block({"B": "Option B — AX88772C, key via factory fixture (rev 0)",
                      "C": "Option C — USB hub + AX88772C + CP2102N (rev 0)"}[option],
                     "Host: TI AM62x (eth2) · no MCU · Nessum: SC1320A · see docs/ALTERNATIVES.md",
                     "gen_options.py")
    os.makedirs(os.path.join(HERE, "options"), exist_ok=True)
    save(os.path.join(HERE, "options", f"schematic-option-{option.lower()}.svg"))


if __name__ == "__main__":
    sheet("B")
    sheet("C")

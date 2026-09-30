# Alternative designs: options B and C (no microcontroller)

**Status:** Draft v0.2. **Option C is selected** (2026-09-30). Option **A** (i.MX RT1062
bridge MCU) is the fallback if the SC1320A cannot lock its key. Option B stays on
record. Option C's go/no-go is the Socionext questions in [§6](#6-decision-questions-for-socionext).

| | Schematic (rev 0) | BOM |
|---|---|---|
| **A**: RT1062 MCU (baseline) | [`hardware/schematic.svg`](../hardware/schematic.svg) | [`hardware/bom.csv`](../hardware/bom.csv) |
| **B**: AX88772C, key loaded by a factory fixture | [`hardware/options/schematic-option-b.svg`](../hardware/options/schematic-option-b.svg) | [`hardware/options/bom-option-b.csv`](../hardware/options/bom-option-b.csv) |
| **C**: USB hub + AX88772C + CP2102N | [`hardware/options/schematic-option-c.svg`](../hardware/options/schematic-option-c.svg) | [`hardware/options/bom-option-c.csv`](../hardware/options/bom-option-c.csv) |

---

## 1. The three options

```
A  AM62x ─USB─► RT1062 MCU ──RMII──► SC1320A        eth2 (cdc_ncm) + /dev/ttyACM (MCU console)
               (firmware: NCM bridge, MAC/key store, lock)

B  AM62x ─USB─► AX88772C ──Reverse-RMII──► SC1320A  eth2 (asix)
                                   UART ◄── test pads ◄── factory fixture only

C  AM62x ─USB─► USB2422 hub ─┬─► AX88772C ──Reverse-RMII──► SC1320A   eth2 (asix)
                             └─► CP2102N ───────UART──────► SC1320A   /dev/ttyUSB (cp210x)
```

**How the parts connect**

- **AX88772C Reverse-RMII:** the AX88772C's MFA/MFB bus can be strapped as a
  "Reverse-RMII" port. The AX88772C then acts as the *PHY side*, which gives a glueless
  MAC-to-MAC link to the SC1320A's RMII MAC. Because the bridge is the PHY side, the
  RMII nets connect straight by name (RXD to RXD, and so on). Option A needs crossed
  TX/RX wiring instead.
- **EEPROM:** the AX88772C reads its MAC, VID/PID and USB strings from a 93C56/93C66
  Microwire EEPROM in x16 mode.
- **Hub (option C):** any USB 2.0 hub has a transaction translator, so the full-speed
  CP2102N behind the hub doesn't slow the high-speed AX88772C.

---

## 2. Comparison

| | **A** RT1062 MCU | **B** AX88772C + fixture | **C** hub + AX88772C + CP2102N |
|---|---|---|---|
| Firmware to write | Yes: USB NCM bridge, management, key sealing | **None** | **None** |
| Linux driver / name | `cdc_ncm` → `eth2` | `asix` → `eth2` | `asix` → `eth2`, `cp210x` → `/dev/nessum-mgmt` |
| Carrier / link state in Linux | **Real** Nessum link and PHY rate | Always up, 100 Mbit/s | Always up; real state readable over the serial port |
| Status / management in the field | Yes, over USB | **None** | Yes, over USB (raw SC1320A commands) |
| **Network-key lock** | **Enforced by the MCU** (write-only, sealed, locked) | Depends **entirely** on the SC1320A. The host has no path to its UART. | Depends **entirely** on the SC1320A, and the host *can* reach its UART |
| **MAC lock** | Enforced by the MCU (factory lock) | **Not possible:** no write-protect pin on AX88772C-compatible EEPROMs, so root can change it | Same as B |
| Key programming at the factory | Over USB (`factory_program.py`, UI) | Pogo-pin fixture + USB-UART on the fixture | Over USB via the CP2102N |
| MAC programming at the factory | Over USB | Over USB to the AX88772C EEPROM (ASIX SROM tool or `ethtool -E`) | Same as B |
| Chips (excl. SC1320A, power, line side) | RT1062, QSPI flash, EEPROM, 24 MHz crystal | AX88772C, EEPROM, crystal | USB2422, AX88772C, CP2102N, EEPROM, 2 crystals |
| Design / schedule risk | Firmware (largest task in the project) | Lowest | Low |
| Existing host tools | Work as-is | Need a new backend (§4) | Need a new backend (§4) |

**The trade-off in one sentence:** options B and C remove the firmware work, but the
factory lock then depends on the SC1320A. The MAC lock is lost entirely. The Nessum link
state disappears from Linux's view (B), or moves to a side channel (C).

---

## 3. Security with one common network key

All units share one Nessum key ([MANAGEMENT.md §2](MANAGEMENT.md#2-nessum-network-key)),
so a key read from any single unit exposes every installation.

- **Option A:** the MCU holds the key sealed with its chip-unique key, never reveals it
  over USB, and refuses changes after `LOCK`. With secure boot and the SWD port locked,
  this does not depend on the SC1320A's features.
- **Option B:** the host has no route to the SC1320A UART. The pads are only reached
  by the factory fixture. The key must still be stored somewhere, which means in the
  SC1320A or its own flash. Someone with the unit open could probe the pads or that
  flash, unless the SC1320A protects its key storage. *Acceptable only if the SC1320A
  stores the key non-readably.*
- **Option C:** the same, plus any root process on the AM62x can talk to the SC1320A
  command interface through `/dev/nessum-mgmt`. *Acceptable only if the SC1320A can
  permanently lock key read-back and key changes on its UART.* If it can't, removing
  the 0 Ω links R22/R23 after programming cuts the host's access. But that also removes
  the in-field status that is the reason to choose C over B.
- **MAC (B and C):** the AX88772C accepts EEPROM writes from the host (the Linux `asix`
  driver supports `ethtool -E`). The 93C56/93C66 parts the AX88772C supports have no
  program-enable pin (only the larger 93xx76C/86C do), so there is no hardware lock. The
  MAC can only be protected by host policy (root only).

---

## 4. Factory programming changes (B and C)

The browser UI and the run/log logic (`factory_ui.py`, `factory_program.py`: runs,
blocks, quantities, duplicate-free allocation, the common-key check) are shared by all
options. The **option C backend is implemented** in
[`host/tools/optionc.py`](../host/tools/optionc.py) and is the default (`--backend c`):

| Step | Option A | **Option C** (implemented) |
|---|---|---|
| Find the unit | `/dev/nessum-mgmt` (MCU console) | **By USB topology:** a non-root hub carrying both an AX88772C (`0b95:772b`) and a CP2102N (`10c4:ea60`). Works on blank units. Exactly one must be plugged in. |
| Read serial number | `VERSION` | CP2102N's factory-unique USB serial (sysfs) |
| Program MAC | `MAC SET` | Written to the AX88772C EEPROM through the kernel's ethtool EEPROM ioctls (`asix` driver, standard library only, needs root), then **read back** |
| Program network key | `NKEY SET` (MCU seals it) | `NessumIc.set_key` over the CP2102N → **pending the SC1320A command set (S4)** |
| Verify key | Fingerprint from `NKEY GET` | `NessumIc.key_matches`: a fingerprint/check command, or a functional test against a golden node. Never by reading the key back. |
| Lock | `LOCK` (MAC + key + pass-through) | `NessumIc.lock`, then `is_locked` checked → **pending S4**. No MAC lock. |
| Apply / verify MAC | MCU `REBOOT` | The AX88772C loads its EEPROM at power-up, so the operator **re-plugs** the unit. The station compares the MAC the kernel then reports with the programmed one and logs `verified` or `verify-failed`. This also catches a wrong EEPROM layout. |
| udev naming on the target | USB descriptors in firmware | **By topology** too ([`nessum-udev-id`](../host/linux/options-bc/nessum-udev-id)), so no custom USB strings need programming |

**What is still open before production:**

1. **SC1320A command set (S4).** `optionc.Sc1320aUart` is the only class to fill in
   (`version`, `is_locked`, `set_key`, `key_matches`, `lock`). Until then it stops with
   a clear message *before* any MAC is reserved, so no addresses are wasted.
2. **AX88772C EEPROM layout.** The MAC offset (`optionc.LAYOUT`, currently 0x08 from
   the AX88772-family map) and byte order must be confirmed against the AX88772C
   datasheet, including whether a checksum must be updated. Until
   `optionc.LAYOUT_VERIFIED` is set, the tools refuse to run unless `--engineering` is
   given. The station shows this as a warning.
3. **First real-hardware run.** The ethtool EEPROM ioctls, discovery and udev helper
   are tested against a simulated `/sys` and EEPROM, not yet on a real AX88772C.

**Labelling rule (enforced):** a unit's MAC goes on the label only after it is
**VERIFIED** in hardware. The station enforces this on the server, not just on the page:

- **No MAC before VERIFIED:** after programming, the MAC is not sent to the browser at
  all. The tile shows **RE-PLUG TO VERIFY**, and the unit list shows "hidden until
  verified". It appears only on **VERIFIED**.
- **One unit at a time:** while a unit waits for its re-plug, the station refuses to
  program any other unit (the Program button is disabled, and the API refuses too). A
  different adapter plugged in shows **RE-PLUG FIRST**, naming the waiting unit. The run
  can't be closed until that unit is verified.
- **Mark for rework:** if the unit can't be re-plugged, this button logs it as
  `verify-failed` ("marked for rework"). Its MAC is never shown, the unit is set aside,
  and the station is free again.
- **Failed verification:** the MAC is never shown, including in the error message.
- **Command line:** `factory_program.py` prints "MAC hidden until verified". After the
  re-plug, `factory_program.py --verify --log …` checks the unit and prints the MAC only
  when it is verified. A failed verification stays failed.

Operator flow: plug in → **RE-PLUG TO VERIFY** → unplug and re-plug the same unit →
**VERIFIED** (MAC shown) → **print the label** → next unit.

**Label printing** ([`host/tools/labels.py`](../host/tools/labels.py)):

- **Content:** a 50 × 25 mm label (`--label-size`) with title, date, `MAC xx:xx:…`, a
  **Code 128** barcode of the 12 hex digits, serial and run.
  - The encoder was cross-checked against the python-barcode library's Code 128
    tables, and a rendered label decodes correctly with zxing-cpp.
  - CI keeps golden symbols and a round-trip decoder test.
- **Printers** (`--printer`):
  - `browser` (default): prints from the page, to any printer the station PC has; add
    Chrome's `--kiosk-printing` for no dialog.
  - `zpl:tcp://HOST[:9100]`: Zebra-compatible thermal printer on the network.
  - `zpl:/dev/usb/lp0`: USB thermal printer.
  - `file:DIR`: ZPL + SVG files, for testing.
  - Use `--dpmm 12` for 300 dpi printers.
- **Only for units whose MAC may be shown:** the station server refuses to print or
  preview a label before VERIFIED, or after a failed verification.
- **Printing:** **Print label** (shortcut `P`) under the VERIFIED tile, with a preview.
  **Print label automatically** prints once per verified unit.
- **Traceability:** every print is logged as `label-printed`, and reprints as
  "reprint n". **Reprint** is in the unit list (with a confirmation). The run panel
  counts labelled units.
- **Command line:** `factory_program.py --verify --log … --printer zpl:tcp://…` prints
  the label right after a successful verification.

![Option C: verified, label printed](images/factory-ui-c-label.png)
![Sample label (50 × 25 mm)](images/label-sample.png)

![Option C: programmed, MAC hidden until re-plug](images/factory-ui-c-pass.png)
![Option C: wrong unit plugged in while one is waiting](images/factory-ui-c-wrong-unit.png)
![Option C: verified after re-plug, MAC shown for the label](images/factory-ui-c-verified.png)

Host files for B/C: [`host/linux/options-bc/`](../host/linux/options-bc/).

---

## 5. Option D (not drawn): LAN9512 + MegaChips

Microchip's LAN9512 is a USB 2.0 hub **and** 10/100 Ethernet controller in one chip,
with an external **MII** port intended for HomePlug-type chips. It cannot connect to the
SC1320A, which is RMII only. It could replace the hub and the AX88772C together
with the MegaChips **MLKHN1501AM**, which has MII, plus a CP2102N on the second hub port.
That requires MegaChips to confirm an MII MAC-to-MAC connection. It also brings the
MLKHN1501AM's higher power, 1.2 V rail, 238-ball BGA and older Nessum generation.
[Spec §4.1](SPECIFICATION.md#41-nessum-ic-selection) has the chip comparison.

---

## 6. Decision questions for Socionext

1. Can the SC1320A **store the network key in its own non-volatile storage** so that it
   cannot be read back, over the UART, over the RMII side, or by probing its flash?
2. Can key changes, and read-back, from the UART and from the Ethernet/RMII side be
   **permanently disabled** after factory programming?
3. Does the SC1320A signal **Nessum link state** on a pin or through a status command?
   (This matters for option C's in-field status.)
4. What is the **UART command set** for configuration and key loading? Is it documented
   and licensed for our own tools?

**Decision rule**

| Socionext answers | Choose |
|---|---|
| 1 and 2 both "yes" and you want in-field status over USB | **C** |
| 1 and 2 both "yes", no in-field status needed | **B** (cheapest) |
| 1 or 2 "no" | **A**: the MCU is the only thing that enforces the key lock |

In every option, the MAC lock is only real in **A**.

---

Sources: [ASIX AX88772C](https://www.asix.com.tw/en/product/USBEthernet/High-Speed_USB_Ethernet/AX88772C) ·
[AX88772C datasheet (Reverse-RMII, EEPROM)](https://www.es.co.th/Schemetic/PDF/AX88772C.PDF) ·
[Microchip 93AA56/93LC56/93C56 datasheet](https://ww1.microchip.com/downloads/aemDocuments/documents/MPD/ProductDocuments/DataSheets/93AA56X-93LC56X-93C56X-2-Kbit-Microwire-Compatible-Serial-EEPROM-Data-Sheet.pdf) ·
[Microchip 1K–16K Microwire EEPROMs (PE pin on 76C/86C)](https://ww1.microchip.com/downloads/en/devicedoc/21929d.pdf) ·
[Microchip LAN9512](https://www.microchip.com/en-us/product/LAN9512)

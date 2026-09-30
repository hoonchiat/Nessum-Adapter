# USB 2.0 ↔ Nessum Adapter — Specification

**Document status:** Draft v0.6 (**option C selected**: USB hub + AX88772C + CP2102N, no microcontroller)
**Last updated:** 2026-09-30

---

## 1. Purpose

Give an embedded Linux system built on an Arm Cortex-A processor a Nessum wired-network
port through its USB 2.0 host port, without writing or maintaining a custom kernel driver.

### 1.1 Goals

1. **Driverless on Linux.** Use chips with in-kernel drivers (`asix` for the AX88772C,
   `cp210x` for the CP2102N), so no out-of-tree driver is needed on any CPU architecture.
2. **Saturate the Nessum link.** The USB side must never be the bottleneck. Nessum
   application throughput sits well below 100 Mbit/s, which a USB 2.0 HS to 10/100
   Ethernet bridge carries comfortably.
3. **Manageable.** Expose the Nessum IC's configuration and status (link state,
   statistics, key programming at the factory) to Linux over a serial port on the same USB
   plug.
4. **Robust line interface.** Functional isolation between the USB/host ground and the
   field wiring, with surge protection for long runs of low-voltage cable.
5. **Small and bus-powered** where the power budget allows.

### 1.2 Non-goals (v1)

- Host drivers for Windows or macOS. ASIX and Silicon Labs ship drivers for both, but they
  are not validated.
- Firmware of our own: option C has no microcontroller.
- USB 3.x SuperSpeed.
- Being a Nessum *coordinator* for large multi-hop networks. v1 acts as a terminal/end node.
  Coordinator support depends on the chosen IC firmware.

---

## 2. Assumptions & clarifications

| Request term | Interpreted as | Note |
|---|---|---|
| **Host CPU** | **TI AM62x** (confirmed; Cortex-A53), TI Processor SDK Linux (arm64) | The adapter uses a standard USB class and the host driver is architecture-independent. The only SoC-specific part is the USB host controller: DWC3 with the AM62 glue (§8). |
| **"Standard Ethernet port"** (requirement) | Linux sees an ordinary Ethernet netdev (`ARPHRD_ETHER`, MAC, MTU 1500, VLAN, multicast), so existing software runs unchanged | Met by the AX88772C + the in-kernel `asix` driver. **Difference:** carrier is always up at 100 Mbit/s full duplex (Reverse-RMII has no PHY link). The real Nessum link state is read over the serial port. |
| **Interface name** (confirmed) | `eth2` | Next after the AM62x CPSW's `eth0`/`eth1`. Alternative name `nessum0`. |
| **MAC address** (confirmed) | Programmed **at the factory** from the address block you define **for each production run**, into the AX88772C's EEPROM | Duplicate-free allocation per run (`factory_program.py` run/log logic). **Accepted trade-off of option C:** the MAC is **not hardware-lockable**. The AX88772C-compatible 93C56/93C66 EEPROMs have no write-protect pin, so root on the host can change it. See [ALTERNATIVES.md §3](ALTERNATIVES.md#3-security-with-one-common-network-key). |
| **Nessum network key** (confirmed) | **One common key** for all units, programmed **at the factory** into the SC1320A over the CP2102N serial port | **Blocking dependency of option C:** the SC1320A itself must store the key non-readably and lock it against later changes. The host can reach its UART. Confirm with Socionext (§9, S1–S2). If it can't, fall back to option A, or cut the UART after programming (R22/R23), which loses in-field status. |
| **USB 2.0** | USB 2.0 **High-Speed** (480 Mbit/s) device | Full-Speed (12 Mbit/s) would bottleneck the link. |
| **Nessum** | Nessum (formerly HD-PLC), IEEE 1901-2020 wavelet-OFDM | — |
| **Medium** (confirmed) | **(a)** an existing **twisted pair currently carrying RS-485**, or **(b)** an existing **low-power 24 V AC or 24 V DC** cable | Both are SELV / low-voltage media, **not AC mains** (§6). Assumption: Nessum **replaces** RS-485 signalling on the pair. Sharing the pair with live RS-485 traffic is not planned (§6.1). |

---

## 3. System architecture

### 3.1 Options considered

| # | Architecture | Pros | Cons | Verdict |
|---|---|---|---|---|
| A | USB-HS MCU (RT1062) + RMII MAC-to-MAC to Nessum IC, MCU runs CDC-NCM | Real link state in Linux. MAC and key lock enforced by the MCU. | Firmware for the bridge MCU has to be written. | **Fallback**: if the SC1320A cannot lock its key |
| B | ASIX AX88772C (Reverse-RMII, MAC-to-MAC) + Nessum IC; key loaded by a factory fixture | No firmware to write. In-kernel `asix` driver. | Key lock depends entirely on the SC1320A. MAC not lockable. Carrier always up. No in-field management. | Alternative |
| C | **USB hub + AX88772C + CP2102N USB-UART + Nessum IC** | No firmware. In-kernel drivers. In-field status and factory programming over the one USB plug. | Key lock depends on the SC1320A (host can reach its UART). MAC not hardware-lockable. Carrier always up. | **Selected** |
| D | LAN9512 (hub + Ethernet, MII) + MegaChips MLKHN1501AM | One chip for hub + Ethernet | SC1320A is RMII only, so this needs the MegaChips IC. MII MAC-to-MAC unconfirmed. | Noted in [ALTERNATIVES.md §5](ALTERNATIVES.md#5-option-d-not-drawn-lan9512--megachips) |
| E | Nessum IC UART/SPI straight to a USB-UART bridge | Simplest hardware | Throughput is limited by UART/SPI. Needs a custom host protocol or driver. | Rejected |
| F | Put the Nessum IC on the Cortex-A board over RMII/SPI | Lowest BOM | Not a USB adapter. Needs board changes and a custom device-tree/driver. | Out of scope |

### 3.2 Selected datapath (option C)

```
Linux ─USB HS─► [USB2422 hub] ─┬─► [AX88772C] ─Reverse-RMII─► [SC1320A MAC] ─► Nessum PHY/AFE ─► medium
                               └─► [CP2102N]  ────UART──────► [SC1320A command interface]
```

- **Data:** the AX88772C is a USB 2.0 HS to 10/100 Ethernet bridge. Its MFA/MFB bus is
  strapped as **Reverse-RMII**: the AX88772C acts as the PHY side, giving a glueless
  MAC-to-MAC link to the SC1320A. RMII nets connect straight by name. Linux binds the
  in-kernel `asix` driver and names the interface `eth2`.
- **Management:** the CP2102N is a USB-UART bridge on the hub's second port, wired to
  the SC1320A's UART. Linux binds `cp210x` and the udev rule names it
  `/dev/nessum-mgmt`. It carries the SC1320A's own command set (TBD, §9 S4): link
  state, statistics, and key programming at the factory. CP2102N GPIOs drive the
  SC1320A's reset and read its interrupt.
- **Hub:** a 2-port USB 2.0 High-Speed hub (Microchip USB2422). Its transaction translator
  keeps the full-speed CP2102N from slowing the HS AX88772C.
- **No firmware** anywhere on the adapter. Every chip is fixed-function or configured
  by its EEPROM/OTP settings at the factory.

Option A (the RT1062 design) is kept as the fallback. Its schematic, BOM, firmware plan
and management protocol stay in the repo ([ALTERNATIVES.md](ALTERNATIVES.md)).

### 3.3 Throughput budget

| Segment | Raw | Expected usable |
|---|---|---|
| USB 2.0 HS (hub → AX88772C) | 480 Mbit/s | far above 100 Mbit/s |
| RMII | 100 Mbit/s | ~95 Mbit/s |
| Nessum link | PHY rate depends on IC/generation | Tens of Mbit/s application throughput, depending on line conditions |

The **line** is the bottleneck by a wide margin, as intended. The AX88772C's internal
buffering absorbs bursts.

---

## 4. Major components (proposed)

| Function | Part | Why | To verify |
|---|---|---|---|
| USB hub | **Microchip USB2422** (2-port USB 2.0 HS) | Smallest HS hub for two on-board devices | Crystal, strap/config options, availability |
| USB-Ethernet bridge | **ASIX AX88772C** | USB 2.0 HS to 10/100 MAC with **Reverse-RMII** for glueless MAC-to-MAC. In-kernel `asix` driver. | Reverse-RMII REF_CLK direction, strap settings, EEPROM layout |
| USB-UART bridge | **Silicon Labs CP2102N** | In-kernel `cp210x` driver, internal oscillator, customisable product string/serial, GPIOs for Nessum reset/interrupt | — |
| Bridge EEPROM | 93LC56C (93C56/93C66 class, x16) | Holds the AX88772C's MAC, VID/PID and strings | No write-protect pin exists (MAC not lockable) |
| Nessum IC | **Socionext SC1320A** (**selected**, §4.1) | 4th-generation Nessum (HD-PLC4, IEEE 1901-2020). RMII MAC + UART/SPI host interfaces. Single 3.3 V supply. ~200 mW. 7×7 mm QFN. | Price quote, datasheet/NDA, line-driver needs, eval kit |
| Nessum IC (fallback) | **MegaChips MLKHN1500AM** (single-hop) / **MLKHN1501AM** (multi-hop) | Earlier generation (HD-PLC3). MII/RMII + UART. Stocked at distributors with published prices. | Only if the SC1320A quote or support falls through |
| Clocking | Crystals per AX88772C, USB2422 and SC1320A datasheets. 50 MHz RMII ref from the AX88772C, the SC1320A, or an oscillator (Y2 DNP option). | — | Which side sources REF_CLK |
| Line coupling | Wideband coupling transformer + DC-blocking capacitors + TVS | Per the IC vendor reference design, adapted for twisted pair / 24 V (§6) | — |
| Power | 5 V USB → buck 3.3 V (+ internal/LDO core rails per bridge datasheets) | The SC1320A needs only 3.3 V | Power budget (§5) |

The rev 0 schematic of option C is
[`../hardware/options/schematic-option-c.svg`](../hardware/options/schematic-option-c.svg), with
its BOM [`../hardware/options/bom-option-c.csv`](../hardware/options/bom-option-c.csv).
The option A (fallback) sheet is [`../hardware/schematic.svg`](../hardware/schematic.svg).

### 4.1 Nessum IC selection

Two Nessum ICs are publicly offered with an RMII host interface:

| | **Socionext SC1320A** | **MegaChips MLKHN1500AM / 1501AM** |
|---|---|---|
| Generation / standard | HD-PLC4, IEEE 1901-2020 (newest) | HD-PLC3 (earlier generation) |
| Host interfaces | RMII MAC, UART/SPI | MII/RMII, UART |
| Supply | **Single 3.3 V** | 3.3 V + 1.2 V |
| Power (typical) | **~0.2 W** | ~0.57 W active, 0.12 W standby |
| Package | **7×7 mm QFN**: 4-layer board, easy assembly | 238-ball LBGA, 18×15 mm: finer PCB rules, X-ray inspection |
| Integrated memory / AFE | AFE integrated. External line driver: TBD from datasheet. | 128 Mb SDRAM + AFE integrated |
| Reach | Up to 10 km with multi-hop (vendor figure) | 1500AM single-hop, 1501AM multi-hop |
| Band | 2–28 MHz; runs over twisted pair and DC lines | 2–28 MHz |
| Price (Sept 2026) | **Not published.** Quote needed from Socionext or its distributor. | **Published:** ~US$8–9 in 1k from the MegaChips store, ~US$14–18 for single units at DigiKey |

**Selected: Socionext SC1320A.** For these media and a USB-powered dongle it wins on
every technical point:

- **Power.** About a third of the MLKHN1500AM's draw, which leaves plenty of margin in
  the 2.5 W USB budget (§5).
- **Board cost.** One supply rail and a QFN instead of a 238-ball BGA mean fewer regulators,
  a cheaper 4-layer PCB, and simpler assembly and inspection. That offsets a chip price
  somewhat above the MegaChips part.
- **Longevity.** It implements the current generation of the standard, where the
  MLKHN150x is the previous one. That matters for a new design.
- **Reach.** Multi-hop and long reach suit long RS-485 runs and 24 V building wiring.

**Condition:** SC1320A pricing is not public. Get a quote at the expected volume, and
confirm Socionext will support low-volume customers, before committing the layout. If the
quote lands well above ~US$10 in volume, or support is not available, switch to the
**MLKHN1501AM** (multi-hop variant). The datapath does not change, because both use RMII
plus UART. What changes is the second supply rail, the BGA footprint and ~0.4 W more in the
power budget.

**Interoperability:** every node on the same cable must speak the same Nessum generation.
Confirm with the vendor whether HD-PLC4 and HD-PLC3 nodes interoperate before mixing
chips, and pick one IC for all nodes.

---

## 5. Power budget (preliminary)

| Load | Estimate |
|---|---|
| SC1320A | ~0.2 W typical (vendor figure) |
| External line driver (if the datasheet requires one) | **TBD**, allow ~0.3–0.5 W |
| USB2422 hub + AX88772C + CP2102N | **TBD** from datasheets (low-power fixed-function parts) |
| Regulator losses | ~15 % |
| **Estimated total** | **TBD**, expected well inside the budget |
| **USB 2.0 budget** | **2.5 W** (500 mA @ 5 V after enumeration) |

The adapter should be **USB bus-powered**, with margin. It never draws power from the
twisted pair or the 24 V cable, so it adds no load to the existing installation.
(With the MLKHN1500AM fallback, add ~0.4 W. That still fits.)

---

## 6. Line interface & safety

Both target media are low-voltage (SELV), so the mains-grade safety parts (X2/Y caps,
fuses, reinforced creepage) are **not needed**. One adapter design with a single
2-pin line connector covers both media. Only the coupling-capacitor voltage rating and the
TVS are chosen for the worst case (24 V AC peak ≈ 34 V, plus transients).

Common line-side circuit:

- **Wideband coupling transformer** (per the vendor reference design): keeps the host/USB
  ground **functionally isolated** from the field wiring. This breaks the ground loops that
  are common on long RS-485 and 24 V runs.
- **DC-blocking capacitors** in series on both legs, rated ≥ 100 V: they pass 2–28 MHz and
  block 24 V DC and 50/60 Hz AC.
- **Bidirectional TVS** across the line (≈ 36–40 V standoff, so 24 V AC peaks don't
  trigger it) plus common-mode surge protection. Field cables pick up surges.
- **Plug-in 2-pin terminal block** (5.08 mm) for the pair.

### 6.1 Medium (a): existing RS-485 twisted pair

- Nessum **replaces** the RS-485 traffic. Remove or disconnect the RS-485 transceivers
  from the pair. Their input capacitance and the stubs to them load and reflect the MHz
  signal. RS-485 at high baud rates also overlaps the Nessum band.
- **120 Ω end terminations:** leave them in for a first test. Nessum usually copes with a
  resistive load, but check the vendor's guidance on termination for twisted pair.
- If RS-485 must keep running in parallel on the same pair, a coupling filter (high-pass
  to Nessum, low-pass to RS-485) is needed on every node. That is out of scope for v1.
- **Best case of the two media.** A twisted pair has controlled impedance and few
  branches, so expect the highest throughput and longest reach here.

### 6.2 Medium (b): existing 24 V AC / DC power cable

- The adapter only **injects signal** onto the pair and does not take power from it.
- **Impedance problem:** every load on the 24 V line (power supply input capacitors,
  transformers, relays) shorts the high-frequency signal. Plan an **RF choke / ferrite
  filter at each load and at the 24 V source** (a low-pass that passes 24 V and blocks
  2–28 MHz). This is the main field-installation cost of this medium.
- 24 V AC from a transformer is noisy. Switching supplies on 24 V DC are worse. Nessum's
  wavelet-OFDM with error correction is designed for this, but expect lower throughput
  than on the twisted pair.

### 6.3 EMC

Signalling at 2–28 MHz on unshielded field wiring radiates. Use the IC's band-plan and
notch settings to stay out of amateur and broadcast bands for the target region, and
budget for pre-compliance testing (conducted and radiated emissions for the region, e.g.
FCC Part 15 or CISPR 32).

---

## 7. USB device definition

| Item | Value |
|---|---|
| Speed | High-Speed (480 Mbit/s) |
| VID:PID | **TBD.** Use a vendor-owned VID or apply for a free PID (e.g. pid.codes for open hardware). Never ship with a borrowed ID. |
| Configuration | 1 config, bus-powered, `bMaxPower` set from §5 |
| Topology | USB2422 hub; port 1: AX88772C, port 2: CP2102N. Two USB devices behind one plug. |
| AX88772C | ASIX default VID:PID `0b95:772b`, so the in-kernel `asix` driver binds. Product string **`Nessum Adapter`**, unique serial, and MAC from the EEPROM, all written at the factory. |
| CP2102N | Silicon Labs default VID:PID `10c4:ea60` (in-kernel `cp210x`). Product string **`Nessum Adapter Mgmt`** and serial customised at the factory. |
| Hub | Default IDs, or custom ones through its configuration straps/EEPROM |
| Why default VID:PIDs | The `asix` and `cp210x` drivers only bind to IDs in their tables. Own IDs would need a small kernel patch. udev tells our adapter apart by the product strings. |
| MAC address | From the AX88772C EEPROM. Changeable by root (`ethtool -E`, or `ip link set address` at runtime). No hardware lock in option C. |
| Link status | Carrier always up at 100 Mbit/s (Reverse-RMII). Real Nessum link state is available over `/dev/nessum-mgmt`. |

---

## 8. Host-side (Linux) integration

Target: TI Processor SDK Linux on an **AM62x**. **No custom kernel driver.**

- **Kernel:** `asix` + `cp210x`, plus the AM62x USB host controller (DWC3 +
  `USB_DWC3_AM62`, xHCI). The USB port's device-tree node (`&usb0`/`&usb1`) must be in
  host or OTG mode. See [`../host/linux/options-bc/kernel.config`](../host/linux/options-bc/kernel.config).
- **Interface name `eth2`** and **`/dev/nessum-mgmt`:**
  [`../host/linux/options-bc/70-nessum-options-bc.rules`](../host/linux/options-bc/70-nessum-options-bc.rules)
  matches on the vendors' VID:PIDs plus our product strings. The serial port is
  restricted to root and the `netdev` group.
- **Network config** (systemd-networkd; the TI SDK's Arago rootfs uses systemd):
  [`../host/linux/20-nessum.network`](../host/linux/20-nessum.network). This is unchanged.
- **Bring-up check:** [`../host/linux/check-adapter.sh`](../host/linux/check-adapter.sh)
  (handles `asix` and `cdc_ncm`).
- **Factory tools:** `factory_program.py` / `factory_ui.py` with the **option C backend**
  ([`../host/tools/optionc.py`](../host/tools/optionc.py), default `--backend c`). It
  finds the unit by USB topology, writes and reads back the MAC in the AX88772C EEPROM,
  and verifies the MAC in hardware after a re-plug. Two parts are still pending: the
  SC1320A key/lock commands (§9 S4) and confirmation of the EEPROM layout. See
  [ALTERNATIVES.md §4](ALTERNATIVES.md#4-factory-programming-changes-b-and-c).
- **udev helper:** [`../host/linux/options-bc/nessum-udev-id`](../host/linux/options-bc/nessum-udev-id)
  recognises the adapter by topology for the `eth2` / `/dev/nessum-mgmt` rules.

**Alternative if the host board can be changed:** the AM62x CPSW Ethernet switch
supports RMII. Wiring the SC1320A straight to a spare CPSW port (fixed-link, no USB and
no bridge MCU) gives a native `am65-cpsw-nuss` Ethernet port with fewer parts. The MAC then
comes from the SoC's eFuse or device tree. This is only an option for new host board
revisions. The USB adapter remains the plan for existing boards.

---

## 9. Open questions

| # | Question | Impact |
|---|---|---|
| Q1 | ~~Host~~ Resolved: AM62x, interface `eth2`, MAC + network key factory-programmed and locked. Still open: TI SDK version, and which AM62x USB port (`usb0`/`usb1`) the adapter plugs into. | Device-tree `dr_mode` |
| Q1b | ~~Key scope / MAC block~~ Resolved: **one common network key** for all units. MAC block and quantity are defined **per production run** (`factory_program.py --run/--block/--quantity`). | [MANAGEMENT.md §2–3](MANAGEMENT.md#2-nessum-network-key) |
| Q1c | Key size (AES-128 assumed) | Tool key format |
| **S1** | **Blocking.** Can the SC1320A store the network key in its own non-volatile storage so it cannot be read back (UART, RMII side, or probing its flash)? | Option C viability; "no" → option A |
| **S2** | **Blocking.** Can key read-back and key changes be permanently disabled after factory programming, on the UART and the RMII side? | Option C viability; "no" → option A, or cut R22/R23 after programming |
| S3 | Does the SC1320A report Nessum link state (pin or status command)? | In-field status over `/dev/nessum-mgmt` |
| S4 | UART command set for configuration and key loading: documented and licensed for our tools? | Factory tool backend for option C |
| Q2 | ~~Medium~~ Resolved: RS-485 twisted pair or 24 V AC/DC cable. Still open: region, and cable lengths / number of nodes per cable. | EMC band plan, multi-hop need |
| Q3 | ~~IC~~ Resolved: SC1320A selected (§4.1). Still open: Socionext quote at our volume, datasheet/NDA/SDK access, eval kit. | Final go/no-go on SC1320A vs MLKHN1501AM |
| Q3a | Does RS-485 traffic need to keep running on the same pair during migration? | Adds a filter per node (§6.1) |
| Q4 | Node role: end node only, or also coordinator / multi-hop relay? | IC variant, firmware |
| Q5 | Throughput and latency targets? | Buffer sizing, IC generation |
| Q6 | Form factor: dongle with USB-A/C plug, or board with cable? Enclosure? | Mechanical |
| Q7 | ~~VID/PID~~ Option C uses the chip vendors' default IDs + our product strings (§7). Own IDs would need driver-table patches. | udev matching |
| Q8 | Volume and cost target? | Hub/bridge part choices |
| Q9 | Is a root-changeable MAC acceptable in the field? (Option C cannot lock it in hardware. Recorded as an accepted trade-off.) | If not: option A |

---

## 10. Plan

1. **Phase 0, go/no-go and evaluation.** Get Socionext's answers to S1–S4. If S1 or S2
   is "no", switch to option A. Otherwise build an eval set-up: SC1320A eval board (RMII
   exposed) + an AX88772C board in Reverse-RMII mode + a CP2102N module on the SC1320A
   UART. Verify `asix` → `eth2` on the AM62x and `iperf3` across a Nessum link.
2. **Phase 1, schematic/layout rev A (option C).** Hub + AX88772C + CP2102N + SC1320A +
   AFE per vendor reference. Isolation and EMC review.
3. **Phase 2, factory tooling.** The option C backend is implemented (discovery, EEPROM
   MAC write + read-back, re-plug verification, UI). Remaining: implement
   `Sc1320aUart` from the S4 command set, confirm the AX88772C EEPROM layout and set
   `LAYOUT_VERIFIED`, and do a first run on real hardware.
4. **Phase 3, validation.** Throughput/latency, hot-plug and suspend/resume on the host,
   key-lock verification (attempt read-back and change from the host), conducted
   emissions, safety pre-compliance.

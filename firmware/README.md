# Bridge-MCU firmware

> **Option A (fallback) only.** The selected option C (USB hub + AX88772C + CP2102N) has
> no microcontroller and no firmware. See [`../docs/ALTERNATIVES.md`](../docs/ALTERNATIVES.md).

Target: NXP i.MX RT1062 (Cortex-M7), bare-metal, MCUXpresso SDK drivers + TinyUSB.

## Status

| Part | State |
|---|---|
| `core/` – console protocol, MAC store, key store, NTB16 framing, Nessum reply parser, CRC/SHA-256 | Written and unit-tested on the host (`make test`). Compiles for Cortex-M7 (`make arm-check`). |
| `platform/host/` + `fwsim` | The core on a PC, serving the console on a pty. `host/tools/test_firmware.py` checks it against `fake_adapter.py` (same replies), runs `nessumctl` and `factory_program` against it, and covers power cycles and storage / IC failures. |
| `platform/rt1062/` | USB (NCM + ACM), ENET bridge, EEPROM, flash, DCP and interrupt-driven UART drivers. **Builds and links (`make rt1062-link`), never run.** The board files are stand-ins and the SC1320A protocol is a placeholder. See [`platform/rt1062/README.md`](platform/rt1062/README.md). |
| USB DFU update + bootloader (`boot/`, `core/fwimage.c`, `core/fwupdate.c`, `core/fwboot.c`) | Ed25519-signed images, power-cut-safe install. Unit-tested on the host (incl. a power cut at every step of an install). The bootloader and the signed application image build and link. Never run. See [`../docs/MANAGEMENT.md` §6](../docs/MANAGEMENT.md#6-firmware-update-usb-dfu). |
| SWD service unlock, drop counters on the console | Not started. |

```
core/                 portable logic, no hardware access (platform.h is its only dependency)
boot/                 the bootloader (first 64 KB of flash): install + verify + start
platform/host/        PC implementation of platform.h (simulated EEPROM/flash/seal/IC) + fwsim, fwtool
platform/rt1062/      the adapter
third_party/          Monocypher 4.0.2 (Ed25519), vendored unchanged
tests/test_core.c     host unit tests
```

```sh
make -C firmware test          # unit tests
make -C firmware fwsim         # then: firmware/build/fwsim --state /tmp/unit1  (prints the pty)
make -C firmware arm-check     # core for Cortex-M7, sizes
make -C firmware rt1062-link   # bootloader + application + signed .nfw (fetches pinned deps)
```

`fwsim` options: `--serial S`, `--default-mac MAC`, `--fail-eeprom-writes`,
`--fail-keyblob-writes`, `--nessum-down`. With `--state DIR`, restarting it is a power cycle.

**Host seal is not secure.** The host build's `plat_seal()` is an XOR stand-in that only
models "bound to this device". Only the RT1062 DCP/OTPMK implementation protects the key.

## Components (design)

| Component | Responsibility |
|---|---|
| `usb/` | USB HS device stack (TinyUSB). Composite descriptors: CDC-NCM + CDC-ACM + DFU. |
| `ncm/` | NTB16 parse (host→device) and build (device→host). A frame goes out at once when the IN endpoint is idle. While a transfer is in flight, frames gather in a second NTB, so there is no aggregation timer and no added latency. |
| `enet/` | ENET MAC in RMII mode, fixed 100 Mbit/s full-duplex, no MDIO/PHY polling. Zero-copy DMA descriptor rings shared with the NCM layer where alignment allows. |
| `nessum/` | Nessum IC control over an interrupt-driven UART: reset/boot sequencing, configuration, and a background link-state poll (non-blocking; `core/nline.c` parses replies). Link state drives the NCM `NETWORK_CONNECTION` notification. |
| `mgmt/` | CDC-ACM console protocol per [`../docs/MANAGEMENT.md`](../docs/MANAGEMENT.md): `VERSION`, `STATUS`, `MAC GET/SET/CLEAR`, `NKEY GET/SET`, `LOCK`, `REBOOT`, `NESSUM` pass-through. [`../host/tools/fake_adapter.py`](../host/tools/fake_adapter.py) is the executable reference for its behaviour. |
| `macaddr/` | MAC selection (runtime → programmed → default). Reads the default EUI-48 from the 24AA02E48 (0xFA–0xFF). The programmed address is stored as two CRC-16-protected copies in the EEPROM's writable lower half, and each write is read back to verify. Rejects multicast, zero and broadcast. Handles NCM `SET/GET_NET_ADDRESS`: runtime only, and `SET` STALLs once factory-locked. |
| `nkey/` | Nessum network key: `NKEY SET` seals it with the DCP using the chip-unique OTP key and stores the blob in QSPI. `NKEY GET` returns only SHA-256 fingerprint and length. The key is unsealed at boot and loaded into the SC1320A over UART. Zeroise RAM copies after use. |
| `lock/` | Factory-lock flag, which requires the MAC and key to be programmed. When set: reject `MAC SET/CLEAR` and `NKEY SET`, STALL `SET_NET_ADDRESS`, and restrict `NESSUM` pass-through to a read-only allowlist. No USB unlock. The SWD service routine clears the flag **and erases the key**. |
| `board/` | Clocks, pin mux, RMII REF_CLK direction, EEPROM I²C. |

## "Standard Ethernet port" obligations

These are what the Linux `cdc_ncm` driver needs to present an ordinary Ethernet
interface (see [`../docs/MANAGEMENT.md §5`](../docs/MANAGEMENT.md#5-what-makes-it-look-like-a-standard-ethernet-port-to-linux)):

- `iMACAddress` string = the active MAC.
- `bmNetworkCapabilities` advertises the net-address and packet-filter requests.
- `wMaxSegmentSize` = 1518 (MTU 1500 plus a VLAN tag).
- `NETWORK_CONNECTION` and `CONNECTION_SPEED_CHANGE` notifications follow the Nessum link.
- `REBOOT` performs a USB soft-disconnect and re-enumeration, so Linux re-reads
  `iMACAddress` after a MAC change.

## Security

- Production units run with **HAB secure boot** enabled: the ROM checks the
  bootloader's HAB signature, and the bootloader checks the application's Ed25519
  signature, both at every boot. DFU accepts only signed images. Otherwise new firmware
  could bypass the lock or read out the network key.
- **Firmware signing key:** `fwimage.py keygen` creates it. Keep the seed offline; it is
  as sensitive as the network key. Builds without `FW_SIGNING_PUB` trust a development
  key generated in `build/keys`: never ship those.
- **Mandatory** (all units share one network key, so one compromised unit exposes every
  installation): disable or permanently lock the SWD debug port with the RT1062 fuses in
  production, or restrict it to the authenticated service routine. Pick one once the RMA
  process is defined.

## Datapath rules

- Never drop frames silently. Count drops per direction and expose the counters over `mgmt`.
- Apply back-pressure: stop taking OUT transfers while the ENET TX ring is full rather
  than queueing without bound.
- Keep frame buffers in the OCRAM/DTCM region that the ENET and USB DMA can reach. Handle
  cache maintenance explicitly (the Cortex-M7 D-cache is on).

## Bring-up milestones

1. USB enumerates as CDC-NCM on the Linux host. A loopback mode (OUT frames echoed to IN)
   passes `ping` to a static neighbour entry.
2. ENET RMII link to the Nessum eval board. Frames go across the power line to a second
   Nessum node. Measure with `iperf3`.
3. Management console reaches the Nessum IC command interface.
4. Link-state notifications, counters, USB DFU update (bootloader first, via the debugger).

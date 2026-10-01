# RT1062 platform (option A adapter firmware)

> **Status: builds, never run.** `make rt1062-link` builds the bootloader
> (`boot.elf`/`boot.bin`) and the application (`adapter.elf`/`adapter.bin`). It then
> signs the application into `adapter-<version>.nfw`. It uses this directory, the core,
> TinyUSB and the NXP SDK drivers at pinned upstream revisions. Board-specific files are
> **stand-ins** (`check/`), so the images do not boot. Nothing here has run on hardware
> yet.

| File | What it does |
|---|---|
| `main.c` | Bare-metal superloop: TinyUSB, ENET receive, console, Nessum link poll, re-enumeration after `REBOOT`. Nothing in it blocks. |
| `usb_ncm.c/h` | CDC-NCM as a TinyUSB **application class driver**, built on `core/ncm.c`. Handles `GET_NTB_PARAMETERS` (NTB16, 8 KB each way); `GET/SET_NET_ADDRESS` (SET is STALLed on a locked unit); and `SET_ETHERNET_PACKET_FILTER`. Sends `CONNECTION_SPEED_CHANGE` and `NETWORK_CONNECTION` when the Nessum link changes. The MAC is re-selected at every bus reset. |
| `usb_descriptors.c/h` | Composite IAD device: NCM (itf 0/1) + ACM console (itf 2/3). `iMACAddress` = active MAC, `wMaxSegmentSize` 1518. Both HS and FS configurations are provided. **VID:PID `1209:0000` is a placeholder.** |
| `enet_bridge.c/h` | ENET in RMII, 100 Mbit/s full duplex, promiscuous, with no PHY. The 50 MHz REF_CLK is driven out from the ENET PLL. The bridge applies back-pressure in both directions and counts drops. |
| `plat_rt1062.c/h` | `platform.h`: 24AA02E48 over LPI2C (page writes, ACK polling); key blob in the last QSPI sector via the ROM FlexSPI API; DCP AES-128 sealing with the OTPMK; SC1320A command transactions; serial from the OCOTP unique ID. |
| `nessum_uart.c/h` | Interrupt-driven LPUART to the SC1320A: RX ring (512 B) and TX ring (256 B), filled and drained in the ISR. It counts overruns, ring overflows and line errors, and zeroes TX bytes once sent (commands can carry the key). |
| `board.c/h` | Pins, clocks, SysTick, USB PHY. Pin and peripheral assignments are **TBD against the PCB**. |
| `usb_dfu.c/h` | DFU 1.1 download interface (TinyUSB DFU class, 4 KB blocks) feeding `core/fwupdate.c`; resets after a verified download. |
| `flash_rt1062.c/h` | QSPI erase/program through the ROM FlexSPI API (interrupts off per operation), plus the `platform.h` firmware-slot API. Shared with the bootloader. |
| `gen_ld.py` | Derives the bootloader and application linker scripts from the SDK's. The application runs from the active slot: vectors at `0x60010400`, after the 1 KB image header. |
| `../../boot/main.c` | Bootloader: install a newer staged image (`core/fwboot.c`), verify, jump. With nothing valid it falls back to the ROM serial downloader. |
| `tusb_config.h` | TinyUSB configuration. TinyUSB's own NCM driver is off. |
| `check/` | Link-check stand-ins (pin mux, clocks, MPU) and `fetch-deps.sh` (pinned TinyUSB / MCUXpresso SDK / CMSIS). |

## Before this can run on a board

1. **Board files.** Generate `pin_mux.c` / `clock_config.c` with MCUXpresso Config
   Tools for the PCB.
   - Take `BOARD_ConfigMPU()` from the SDK board template. It must keep the
     `NonCacheable` region uncached.
   - Add the FlexSPI configuration block (FCB) for the chosen QSPI part, and set
     `XIP_BOOT_HEADER_ENABLE=1`.
2. **SC1320A command set** (spec §9, S4). `plat_nessum_command`, `plat_nessum_load_key`
   (`KEY SET`), `plat_nessum_link` (`STATUS`) and `VERSION` use a **placeholder** line
   protocol. Replace them once the command set is known.
3. **Key sealing needs fused OTPMK + closed HAB.**
   - The factory must burn a random OTPMK per unit, set its locks and close HAB.
   - Until then the DCP reports the OTP key as not ready, and `NKEY SET` fails with ERR 4.
     This is deliberate: a key is never stored under a missing or known key.
4. **Flash from XIP.** The key-blob, DFU-staging and bootloader-install writes run
   ROM code with interrupts off, while the CPU executes in place from the same flash.
   - Verify this on hardware. If the ROM API misbehaves while executing in place,
     move the callers to ITCM.
   - A sector erase keeps interrupts off for about 45 ms (typical part), so ENET
     frames can drop during a DFU download.
5. **Nessum UART timing.** Reception is interrupt-driven and the link poll runs as a
   background transaction, so the main loop never waits on the IC.
   - Synchronous commands (console `NESSUM`/`STATUS`, key load, version) still wait
     for their reply, up to 500 ms, but keep USB and the ENET serviced meanwhile.
   - Check the ISR latency at the final baud rate (`rx_hw_overrun` must stay 0).
6. **Bootloader boot header and HAB.** The production bootloader build needs:
   - the FlexSPI configuration block of the QSPI part;
   - `fsl_flexspi_nor_boot.c` with `XIP_BOOT_HEADER_ENABLE=1`;
   - a HAB signature.
   The link check builds it without them.
7. **First programming at the factory** (debugger or ROM serial downloader):
   - write `boot.bin` at flash offset 0;
   - write the signed `adapter-<version>.nfw` (header included) at `0x10000`.
   From then on, updates go over USB DFU.
8. **Signing key.**
   - `make` trusts a per-checkout development key (`build/keys`).
   - Production: `make rt1062-link FW_SIGNING_PUB=prod.pub FW_SIGNING_KEY=`, then
     `fwimage.py sign` on the offline machine.
9. **Image size.** The DMA buffers sit in `.ncache`. The SDK linker script gives that
   section a load image, which adds about 53 KB of zeros to the flash image, and so to
   every DFU download. Mark it `NOLOAD` in `gen_ld.py`.

Bring-up order: [`../../README.md`](../../README.md#bring-up-milestones).

## Build

```sh
make -C firmware rt1062-link     # needs arm-none-eabi-gcc and git; fetches deps once
```

# RT1062 platform (option A adapter firmware)

> **Status: builds, never run.** `make rt1062-link` compiles this directory, the core,
> TinyUSB and the NXP SDK drivers and links them, using pinned upstream revisions.
> Board-specific files are **stand-ins** (`check/`), so the image does not boot.
> Nothing here has run on hardware yet.

| File | What it does |
|---|---|
| `main.c` | Bare-metal superloop: TinyUSB, ENET receive, console, Nessum link poll, re-enumeration after `REBOOT`. |
| `usb_ncm.c/h` | CDC-NCM as a TinyUSB **application class driver**, built on `core/ncm.c`. Handles `GET_NTB_PARAMETERS` (NTB16, 8 KB each way); `GET/SET_NET_ADDRESS` (SET is STALLed on a locked unit); and `SET_ETHERNET_PACKET_FILTER`. Sends `CONNECTION_SPEED_CHANGE` and `NETWORK_CONNECTION` when the Nessum link changes. The MAC is re-selected at every bus reset. |
| `usb_descriptors.c/h` | Composite IAD device: NCM (itf 0/1) + ACM console (itf 2/3). `iMACAddress` = active MAC, `wMaxSegmentSize` 1518. Both HS and FS configurations are provided. **VID:PID `1209:0000` is a placeholder.** |
| `enet_bridge.c/h` | ENET in RMII, 100 Mbit/s full duplex, promiscuous, with no PHY. The 50 MHz REF_CLK is driven out from the ENET PLL. The bridge applies back-pressure in both directions and counts drops. |
| `plat_rt1062.c/h` | `platform.h`: 24AA02E48 over LPI2C (page writes, ACK polling); key blob in the last QSPI sector via the ROM FlexSPI API; DCP AES-128 sealing with the OTPMK; SC1320A UART; serial from the OCOTP unique ID. |
| `board.c/h` | Pins, clocks, SysTick, USB PHY. Pin and peripheral assignments are **TBD against the PCB**. |
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
4. **Flash from XIP.** The key-blob erase/program runs ROM code with interrupts off.
   Verify this on hardware: if the ROM API misbehaves while executing in place, move
   the caller to ITCM.
5. **Blocking UART.**
   - A Nessum exchange blocks the loop until the reply, or for up to 500 ms when the
     IC is silent (the link poll then backs off to 5 s).
   - Make the UART interrupt-driven before measuring throughput.
6. **Image size.** The DMA buffers sit in `.ncache`. The SDK linker script gives that
   section a load image, which adds about 53 KB of zeros to the flash image. Mark it
   `NOLOAD` in the production linker script.

Bring-up order: [`../../README.md`](../../README.md#bring-up-milestones).

## Build

```sh
make -C firmware rt1062-link     # needs arm-none-eabi-gcc and git; fetches deps once
```

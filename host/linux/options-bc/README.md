# Host integration for options B and C

Option C is the selected design, so these are the host files to install.

Options B and C replace the i.MX RT1062 with fixed-function chips (see
[`../../../docs/ALTERNATIVES.md`](../../../docs/ALTERNATIVES.md)). The host side changes:

| File | Purpose |
|---|---|
| [`kernel.config`](kernel.config) | `asix` (AX88772C) and, for option C, `cp210x` (CP2102N) + the AM62x USB host |
| [`70-nessum-options-bc.rules`](70-nessum-options-bc.rules) | Names the AX88772C `eth2`; option C: `/dev/nessum-mgmt` for the CP2102N |

The option A files still apply unchanged: [`../20-nessum.network`](../20-nessum.network)
(networkd config for `eth2`). [`../10-nessum.link`](../10-nessum.link) and
[`../70-nessum.rules`](../70-nessum.rules) are option A only (they match the `cdc_ncm`
driver and our own VID:PID).

Differences the host software will see:

- `ethtool -i eth2` reports driver `asix`.
- **Carrier is always up at 100 Mbit/s full duplex.** Reverse-RMII has no PHY link, so
  Linux cannot see whether the adapter has joined a Nessum network. Option C can read
  the real state over `/dev/nessum-mgmt` (SC1320A command set TBD). Option B cannot.
- The MAC can be changed by root (`ethtool -E` on the asix EEPROM, or `ip link set
  address` at runtime). There is no hardware lock in options B and C.

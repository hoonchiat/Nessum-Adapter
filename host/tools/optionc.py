"""Option C device backend: USB2422 hub + AX88772C + CP2102N + SC1320A.

Used by factory_program.py and factory_ui.py with ``--backend c``. Pieces:

* find_adapters(): locate adapters by USB topology. An adapter is a (non-root) hub
  with an AX88772C (0b95:772b) and a CP2102N (10c4:ea60) behind it, which works
  on blank, never-programmed units. The CP2102N's factory-unique USB serial number
  identifies the unit.
* EthtoolEeprom: reads and writes the AX88772C's EEPROM through the kernel's
  ethtool EEPROM ioctls (the asix driver implements them). Standard library only,
  no ethtool binary needed. Writing needs root / CAP_NET_ADMIN.
* Ax88772cEeprom: where the MAC lives in that EEPROM.
  **LAYOUT_VERIFIED is False**: the offsets must be confirmed against the AX88772C
  datasheet before production. Until then the factory tools refuse to run unless
  ``--engineering`` is given.
* NessumIc / Sc1320aUart: the SC1320A key and lock operations over the CP2102N.
  **The SC1320A UART command set is not available yet** (spec §9 S4), so
  Sc1320aUart raises a clear error before any address is reserved.
* OptionCUnit: the per-unit operations factory_program.program_unit() drives.
"""

import ctypes
import fcntl
import glob
import os
import socket
import struct

import nessumctl

AX88772C_ID = ("0b95", "772b")
CP2102N_ID = ("10c4", "ea60")

# ---------------------------------------------------------------- TBD from datasheets
# AX88772C EEPROM (93C56/93C66, x16): byte offset of the 6-byte node ID (MAC).
# Placeholder from the AX88772-family EEPROM map (words 04h-06h) - VERIFY against the
# AX88772C datasheet, including byte order within each 16-bit word and whether a
# checksum word must be updated. The post-replug check in the station (the MAC the
# kernel reports after re-enumeration) catches a wrong layout either way.
LAYOUT = {"mac_offset": 0x08, "mac_byte_order": "as-is"}
LAYOUT_VERIFIED = False

SC1320A_PENDING = ("SC1320A UART command set not available yet (spec §9 S4): implement "
                   "Sc1320aUart in host/tools/optionc.py once Socionext provides it")


class NotReady(RuntimeError):
    """The backend cannot program units yet (checked before any address is reserved)."""


class UnitError(RuntimeError):
    """A programming or verification step failed on this unit."""


# ---------------------------------------------------------------- discovery
class Adapter:
    def __init__(self, hub, ax, cp, iface, tty, serial):
        self.hub, self.ax, self.cp = hub, ax, cp      # sysfs paths of the USB devices
        self.iface, self.tty, self.serial = iface, tty, serial

    def describe(self):
        return f"hub {os.path.basename(self.hub)}: {self.iface or '?'} + {self.tty or '?'} (serial {self.serial})"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _usb_id(path):
    return _read(os.path.join(path, "idVendor")), _read(os.path.join(path, "idProduct"))


def find_adapters(sysfs="/sys"):
    """All option C adapters currently plugged in, found by USB topology."""
    base = os.path.join(sysfs, "bus", "usb", "devices")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    children = {}
    for name in names:
        if ":" in name:  # interfaces, not devices
            continue
        real = os.path.realpath(os.path.join(base, name))
        if _usb_id(real)[0] is None:
            continue
        parent = os.path.dirname(real)
        if os.path.basename(parent).startswith("usb") or _usb_id(parent)[0] is None:
            continue  # root-hub ports or no parent hub: not an on-board adapter hub
        children.setdefault(parent, []).append(real)
    adapters = []
    for hub, devs in sorted(children.items()):
        ax = [d for d in devs if _usb_id(d) == AX88772C_ID]
        cp = [d for d in devs if _usb_id(d) == CP2102N_ID]
        if len(ax) != 1 or len(cp) != 1:
            continue
        nets = sorted(glob.glob(os.path.join(ax[0], "*:*", "net", "*")))
        ttys = sorted(glob.glob(os.path.join(cp[0], "*:*", "ttyUSB*")))
        adapters.append(Adapter(
            hub, ax[0], cp[0],
            os.path.basename(nets[0]) if nets else None,
            "/dev/" + os.path.basename(ttys[0]) if ttys else None,
            _read(os.path.join(cp[0], "serial")) or _read(os.path.join(ax[0], "serial"))))
    return adapters


def applied_mac(adapter):
    """The MAC the kernel reports for the adapter's interface (after re-enumeration)."""
    for path in sorted(glob.glob(os.path.join(adapter.ax, "*:*", "net", "*", "address"))):
        return (_read(path) or "").lower() or None
    return None


# ---------------------------------------------------------------- AX88772C EEPROM
SIOCETHTOOL = 0x8946
ETHTOOL_GEEPROM = 0x0B
ETHTOOL_SEEPROM = 0x0C
IFREQ_SIZE = 40  # sizeof(struct ifreq) on 64-bit Linux (arm64, x86-64)


def pack_ifreq(iface, data_addr):
    """struct ifreq with ifr_name and ifr_data (pointer to struct ethtool_eeprom)."""
    name = iface.encode()
    if len(name) >= 16:
        raise ValueError(f"interface name too long: {iface}")
    raw = struct.pack("16sP", name, data_addr)
    return bytearray(raw + bytes(max(0, IFREQ_SIZE - len(raw))))


class EthtoolEeprom:
    """EEPROM access via SIOCETHTOOL (ETHTOOL_GEEPROM / ETHTOOL_SEEPROM)."""

    def __init__(self, iface):
        self.iface = iface
        self.magic = None

    def _ioctl(self, cmd, offset, length, data=None):
        buf = ctypes.create_string_buffer(16 + length)
        struct.pack_into("IIII", buf, 0, cmd, self.magic or 0, offset, length)
        if data is not None:
            ctypes.memmove(ctypes.addressof(buf) + 16, bytes(data), length)
        ifr = pack_ifreq(self.iface, ctypes.addressof(buf))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            fcntl.ioctl(s.fileno(), SIOCETHTOOL, ifr)
        magic = struct.unpack_from("I", buf, 4)[0]
        return buf.raw[16:16 + length], magic

    def read(self, offset, length):
        data, magic = self._ioctl(ETHTOOL_GEEPROM, offset, length)
        self.magic = magic  # the driver reports the magic SEEPROM expects
        return data

    def write(self, offset, data):
        if self.magic is None:
            self.read(0, 2)
        try:
            self._ioctl(ETHTOOL_SEEPROM, offset, len(data), data)
        except PermissionError:
            raise UnitError("EEPROM write needs root (CAP_NET_ADMIN)") from None


def _mac_bytes(mac):
    return bytes.fromhex(mac.replace(":", ""))


class Ax88772cEeprom:
    def __init__(self, eeprom, layout=None):
        self.eeprom = eeprom
        self.layout = layout or LAYOUT

    def _order(self, b):
        if self.layout.get("mac_byte_order") == "word-swapped":
            return bytes(x for i in range(0, 6, 2) for x in (b[i + 1], b[i]))
        return bytes(b)

    def read_mac(self):
        """The MAC stored in the EEPROM, or None for a blank/invalid entry."""
        b = self._order(self.eeprom.read(self.layout["mac_offset"], 6))
        mac = ":".join(f"{x:02x}" for x in b)
        try:
            return nessumctl.parse_mac(mac)
        except nessumctl.MacError:
            return None  # blank (ff..ff / 00..00) or not a unicast address

    def write_mac(self, mac):
        want = self._order(_mac_bytes(mac))
        self.eeprom.write(self.layout["mac_offset"], want)
        got = self.eeprom.read(self.layout["mac_offset"], 6)
        if bytes(got) != want:
            raise UnitError(f"EEPROM read-back mismatch at 0x{self.layout['mac_offset']:02x}: "
                            f"{bytes(got).hex()} != {want.hex()}")


# ---------------------------------------------------------------- SC1320A
class NessumIc:
    """Operations the factory needs from the Nessum IC. The key is never read back.

    key_matches() must verify the key WITHOUT reading it back: a fingerprint or
    challenge command if the SC1320A offers one, or otherwise a functional test (for
    example the unit joining a golden node on the station that holds the common key).
    """

    def version(self):
        raise NotImplementedError

    def is_locked(self):
        raise NotImplementedError

    def set_key(self, key):
        raise NotImplementedError

    def key_matches(self, key):
        raise NotImplementedError

    def lock(self):
        raise NotImplementedError

    def close(self):
        pass


class Sc1320aUart(NessumIc):
    """SC1320A over /dev/ttyUSB* (CP2102N). Waiting for the command set (spec S4)."""

    def __init__(self, tty):
        self.tty = tty

    def _pending(self, *args):
        raise NotReady(SC1320A_PENDING)

    version = is_locked = set_key = key_matches = lock = _pending


# ---------------------------------------------------------------- per-unit operations
class OptionCUnit:
    """One plugged-in option C adapter, driven by factory_program.program_unit()."""

    def __init__(self, adapter, eeprom, nessum, engineering=False):
        self.adapter = adapter
        self.ax = Ax88772cEeprom(eeprom)
        self.nessum = nessum
        self.engineering = engineering

    def identify(self):
        if not LAYOUT_VERIFIED and not self.engineering:
            raise NotReady("AX88772C EEPROM layout not verified against the datasheet "
                           "(optionc.LAYOUT_VERIFIED); use --engineering for engineering units only")
        if not self.adapter.iface:
            raise NotReady("AX88772C has no network interface (asix driver not bound?)")
        self.nessum.version()  # fails early (before reserving a MAC) if the IC is unusable
        return {"serial": self.adapter.serial or "?", "programmed": self.ax.read_mac(),
                "locked": bool(self.nessum.is_locked())}

    def write_mac(self, mac):
        self.ax.write_mac(mac)

    def write_key(self, key):
        self.nessum.set_key(key)
        if not self.nessum.key_matches(key):
            raise UnitError("network key verification failed after write")

    def lock(self):
        self.nessum.lock()
        if not self.nessum.is_locked():
            raise UnitError("SC1320A lock did not take effect")

    def finish(self):
        return "unplug and replug the adapter to apply and verify the MAC"

    def close(self):
        self.nessum.close()


class BackendC:
    """Station backend for option C (used by factory_program.py and factory_ui.py)."""

    name = "c"

    def __init__(self, sysfs="/sys", engineering=False, eeprom_factory=EthtoolEeprom,
                 nessum_factory=Sc1320aUart):
        self.sysfs = sysfs
        self.engineering = engineering
        self.eeprom_factory = eeprom_factory
        self.nessum_factory = nessum_factory

    def warnings(self):
        w = []
        if not LAYOUT_VERIFIED:
            w.append("OPTION C: AX88772C EEPROM layout unverified"
                     + (" (engineering mode)" if self.engineering else " - production blocked"))
        return w

    def describe(self):
        found = find_adapters(self.sysfs)
        if len(found) == 1:
            return found[0].describe()
        return f"{len(found)} option C adapters found"

    def present(self):
        return bool(find_adapters(self.sysfs))

    def peek_serial(self):
        found = find_adapters(self.sysfs)
        return found[0].serial if len(found) == 1 else None

    def open_unit(self):
        found = find_adapters(self.sysfs)
        if len(found) != 1:
            raise NotReady(f"plug in exactly one adapter (found {len(found)})")
        a = found[0]
        if not a.tty:
            raise NotReady("CP2102N has no serial port (cp210x driver not bound?)")
        return OptionCUnit(a, self.eeprom_factory(a.iface) if a.iface else None,
                           self.nessum_factory(a.tty), self.engineering)

    def applied_mac(self, serial):
        for a in find_adapters(self.sysfs):
            if a.serial == serial:
                return applied_mac(a)
        return None

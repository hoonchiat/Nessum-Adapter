"""Test doubles for the option C backend (optionc.py).

FakeSysfs builds a /sys-like tree (USB devices under a hub, the asix net interface,
the cp210x tty) in a temporary directory. FakeEeprom is the AX88772C's 93C56.
FakeNessumIc stands in for the SC1320A until its command set is known.
"""

import os
import shutil

import optionc

HUB_ID = ("0424", "2422")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text + "\n")


class FakeSysfs:
    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "bus", "usb", "devices"), exist_ok=True)
        os.makedirs(os.path.join(root, "devices"), exist_ok=True)

    def add_device(self, rel, vid, pid, serial=None, net=None, tty=None, address="00:00:00:00:00:00"):
        real = os.path.join(self.root, "devices", rel)
        name = os.path.basename(rel)
        _write(os.path.join(real, "idVendor"), vid)
        _write(os.path.join(real, "idProduct"), pid)
        if serial:
            _write(os.path.join(real, "serial"), serial)
        if net:
            _write(os.path.join(real, f"{name}:1.0", "net", net, "address"), address)
        if tty:
            os.makedirs(os.path.join(real, f"{name}:1.0", tty), exist_ok=True)
        link = os.path.join(self.root, "bus", "usb", "devices", name)
        if not os.path.islink(link):
            os.symlink(real, link)
        return real

    def add_root_hub(self, bus="usb1"):
        return self.add_device(bus, "1d6b", "0002")

    def add_adapter(self, port="1-1", serial="CP0001", iface="eth2", tty="ttyUSB0", bus="usb1"):
        if not os.path.isdir(os.path.join(self.root, "devices", bus)):
            self.add_root_hub(bus)
        self.add_device(f"{bus}/{port}", *HUB_ID)
        self.add_device(f"{bus}/{port}/{port}.1", *optionc.AX88772C_ID, net=iface)
        self.add_device(f"{bus}/{port}/{port}.2", *optionc.CP2102N_ID, serial=serial, tty=tty)
        return port

    def unplug(self, port="1-1", bus="usb1"):
        for name in (port, f"{port}.1", f"{port}.2"):
            link = os.path.join(self.root, "bus", "usb", "devices", name)
            if os.path.islink(link):
                os.unlink(link)
        shutil.rmtree(os.path.join(self.root, "devices", bus, port), ignore_errors=True)

    def set_address(self, port, iface, mac, bus="usb1"):
        ax = os.path.join(self.root, "devices", bus, port, f"{port}.1")
        _write(os.path.join(ax, f"{port}.1:1.0", "net", iface, "address"), mac)


class FakeEeprom:
    """93C56 behind the AX88772C: 256 bytes, blank = 0xff."""

    def __init__(self, size=256):
        self.data = bytearray(b"\xff" * size)
        self.drop_writes = False

    def read(self, offset, length):
        return bytes(self.data[offset:offset + length])

    def write(self, offset, data):
        if not self.drop_writes:
            self.data[offset:offset + len(data)] = data


class FakeNessumIc(optionc.NessumIc):
    """SC1320A stand-in; `chip` is a dict so state survives re-opening the port."""

    def __init__(self, chip):
        self.chip = chip
        chip.setdefault("key", None)
        chip.setdefault("locked", False)

    def version(self):
        return "sim"

    def is_locked(self):
        return self.chip["locked"]

    def set_key(self, key):
        if self.chip["locked"]:
            raise optionc.UnitError("SC1320A is locked")
        self.chip["key"] = key if not self.chip.get("corrupt_key") else b"\x00" * len(key)

    def key_matches(self, key):
        return self.chip["key"] == key

    def lock(self):
        self.chip["locked"] = True


def replug(sysfs, port, iface, eeprom, bus="usb1"):
    """Simulate a power cycle: the AX88772C loads its MAC from the EEPROM."""
    mac = optionc.Ax88772cEeprom(eeprom).read_mac() or "00:00:00:00:00:00"
    sysfs.set_address(port, iface, mac, bus=bus)

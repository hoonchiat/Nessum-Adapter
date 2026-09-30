#!/usr/bin/env python3
"""Tests for the option C backend (optionc.py) with the fakes in fake_optionc.py."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_program as fp  # noqa: E402
import factory_ui  # noqa: E402
import fake_optionc as fk  # noqa: E402
import nessumctl  # noqa: E402
import optionc  # noqa: E402

KEY = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")
BLOCK = "00:50:c2:aa:00:00-00:50:c2:aa:00:09"
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "linux", "options-bc", "nessum-udev-id")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sys = fk.FakeSysfs(os.path.join(tmp.name, "sys"))
        self.log = os.path.join(tmp.name, "log.csv")
        self.eeproms, self.chips = {}, {}

    def backend(self, engineering=True, nessum_factory=None):
        return optionc.BackendC(
            sysfs=self.sys.root, engineering=engineering,
            eeprom_factory=lambda iface: self.eeproms.setdefault(iface, fk.FakeEeprom()),
            nessum_factory=nessum_factory or (lambda tty: fk.FakeNessumIc(self.chips.setdefault(tty, {}))))

    def program(self, backend=None, quantity=3, **kw):
        backend = backend or self.backend()
        first, last = fp.parse_block(BLOCK)
        res = {}
        unit = backend.open_unit()
        try:
            rc, msg = fp.program_unit(unit, self.log, "R1", first, last, quantity, KEY, result=res, **kw)
        finally:
            unit.close()
        return rc, msg, res

    def unit_rows(self):
        return [r for r in fp.read_log(self.log) if r["status"] != "run-open"]


class DiscoveryTest(Base):
    def test_finds_adapter_by_topology(self):
        self.sys.add_adapter(serial="CP0001")
        found = optionc.find_adapters(self.sys.root)
        self.assertEqual(len(found), 1)
        a = found[0]
        self.assertEqual((a.iface, a.tty, a.serial), ("eth2", "/dev/ttyUSB0", "CP0001"))

    def test_ignores_other_devices(self):
        self.sys.add_root_hub("usb1")
        # A stray ASIX dongle and a CP2102N gadget on root ports are not an adapter.
        self.sys.add_device("usb1/1-3", *optionc.AX88772C_ID, net="eth9")
        self.sys.add_device("usb1/1-4", *optionc.CP2102N_ID, serial="X", tty="ttyUSB5")
        self.assertEqual(optionc.find_adapters(self.sys.root), [])

    def test_two_adapters(self):
        self.sys.add_adapter("1-1", serial="A", iface="eth2", tty="ttyUSB0")
        self.sys.add_adapter("1-2", serial="B", iface="eth3", tty="ttyUSB1")
        self.assertEqual(sorted(a.serial for a in optionc.find_adapters(self.sys.root)), ["A", "B"])
        with self.assertRaises(optionc.NotReady):
            self.backend().open_unit()

    def test_none(self):
        self.assertFalse(self.backend().present())
        with self.assertRaisesRegex(optionc.NotReady, "exactly one"):
            self.backend().open_unit()

    def test_udev_helper(self):
        self.sys.add_adapter()
        self.sys.add_root_hub("usb2")
        self.sys.add_device("usb2/2-1", *optionc.AX88772C_ID, net="eth9")
        env = dict(os.environ, NESSUM_SYSFS=self.sys.root)
        for devpath, expect in (("/devices/usb1/1-1/1-1.1/1-1.1:1.0/net/eth2", True),
                                ("/devices/usb1/1-1/1-1.2/1-1.2:1.0/ttyUSB0", True),
                                ("/devices/usb2/2-1/2-1:1.0/net/eth9", False)):
            with self.subTest(devpath=devpath):
                out = subprocess.run(["sh", HELPER, devpath], env=env, capture_output=True, text=True,
                                     check=True).stdout
                self.assertEqual("NESSUM_ADAPTER=1" in out, expect)


class EepromTest(unittest.TestCase):
    def test_blank_and_roundtrip(self):
        e = fk.FakeEeprom()
        ax = optionc.Ax88772cEeprom(e)
        self.assertIsNone(ax.read_mac())
        ax.write_mac("00:50:c2:aa:00:07")
        self.assertEqual(ax.read_mac(), "00:50:c2:aa:00:07")
        self.assertEqual(e.data[optionc.LAYOUT["mac_offset"]:optionc.LAYOUT["mac_offset"] + 6].hex(), "0050c2aa0007")

    def test_word_swapped_layout(self):
        e = fk.FakeEeprom()
        ax = optionc.Ax88772cEeprom(e, {"mac_offset": 4, "mac_byte_order": "word-swapped"})
        ax.write_mac("00:50:c2:aa:00:07")
        self.assertEqual(e.data[4:10].hex(), "5000aac20700")
        self.assertEqual(ax.read_mac(), "00:50:c2:aa:00:07")

    def test_readback_mismatch(self):
        e = fk.FakeEeprom()
        e.drop_writes = True
        with self.assertRaises(optionc.UnitError):
            optionc.Ax88772cEeprom(e).write_mac("00:50:c2:aa:00:07")

    def test_ifreq_packing(self):
        b = optionc.pack_ifreq("eth2", 0x1234)
        self.assertEqual(len(b), optionc.IFREQ_SIZE)
        self.assertEqual(bytes(b[:5]), b"eth2\x00")
        with self.assertRaises(ValueError):
            optionc.pack_ifreq("x" * 16, 0)


class ProgramTest(Base):
    def setUp(self):
        super().setUp()
        self.sys.add_adapter(serial="CP0001")

    def test_ok(self):
        rc, msg, res = self.program()
        self.assertEqual(rc, 0, msg)
        self.assertEqual(res["outcome"], "ok")
        self.assertIn("replug", msg)
        self.assertEqual(optionc.Ax88772cEeprom(self.eeproms["eth2"]).read_mac(), "00:50:c2:aa:00:00")
        self.assertEqual(self.chips["/dev/ttyUSB0"], {"key": KEY, "locked": True})
        self.assertEqual([(r["status"], r["serial"]) for r in self.unit_rows()],
                         [("reserved", "CP0001"), ("locked", "CP0001")])

    def test_already_locked(self):
        self.program()
        rc, msg, res = self.program()
        self.assertEqual((rc, res["outcome"]), (1, "already"))
        self.assertEqual(res["mac"], "00:50:c2:aa:00:00")

    def test_unverified_layout_blocks_production(self):
        with mock.patch.object(optionc, "LAYOUT_VERIFIED", False):
            rc, msg, res = self.program(backend=self.backend(engineering=False))
        self.assertEqual((rc, res["outcome"]), (2, "error"))
        self.assertIn("not verified", msg)
        self.assertEqual(self.unit_rows(), [])  # nothing reserved

    def test_sc1320a_pending_fails_before_reserving(self):
        rc, msg, res = self.program(backend=self.backend(nessum_factory=optionc.Sc1320aUart))
        self.assertEqual((rc, res["outcome"]), (2, "error"))
        self.assertIn("S4", msg)
        self.assertEqual(self.unit_rows(), [])

    def test_key_verify_failure_retires_address(self):
        self.chips["/dev/ttyUSB0"] = {"corrupt_key": True}
        rc, msg, res = self.program()
        self.assertEqual((rc, res["outcome"]), (1, "failed"))
        self.assertIn("key verification failed", msg)
        self.assertEqual(self.unit_rows()[-1]["status"], "failed")
        self.assertFalse(self.chips["/dev/ttyUSB0"]["locked"])

    def test_eeprom_failure_retires_address(self):
        self.eeproms["eth2"] = e = fk.FakeEeprom()
        e.drop_writes = True
        rc, msg, res = self.program()
        self.assertEqual(res["outcome"], "failed")
        self.assertIn("read-back mismatch", msg)


class CliVerifyTest(Base):
    """factory_program.py for option C: no MAC printed until --verify confirms it."""

    def setUp(self):
        super().setUp()
        self.sys.add_adapter(serial="CP0001")
        self.backend_ = self.backend()

    def test_program_hides_mac_then_verify_reveals(self):
        rc, msg, res = self.program(backend=self.backend_)
        self.assertEqual(rc, 0)
        self.assertNotIn(res["mac"], msg)
        self.assertIn("MAC hidden until verified", msg)
        rc, msg = fp.verify_unit(self.backend_, self.log)   # not re-plugged yet
        self.assertEqual(rc, 1)
        self.assertNotIn(res["mac"], msg)
        # That attempt is on record as failed; a correct re-plug later cannot overturn it.
        self.sys.unplug()
        self.sys.add_adapter(serial="CP0001")
        fk.replug(self.sys, "1-1", "eth2", self.eeproms["eth2"])
        rc, msg = fp.verify_unit(self.backend_, self.log)
        self.assertEqual(rc, 1)
        self.assertIn("already FAILED", msg)

    def test_verify_after_replug(self):
        _, _, res = self.program(backend=self.backend_)
        self.sys.unplug()
        self.sys.add_adapter(serial="CP0001")
        fk.replug(self.sys, "1-1", "eth2", self.eeproms["eth2"])
        rc, msg = fp.verify_unit(self.backend_, self.log)
        self.assertEqual(rc, 0, msg)
        self.assertIn(f"VERIFIED {res['mac']}", msg)
        self.assertEqual(self.unit_rows()[-1]["status"], "verified")
        rc, msg = fp.verify_unit(self.backend_, self.log)    # idempotent re-check, no new row
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.unit_rows()), 3)

    def test_verify_unknown_unit(self):
        rc, msg = fp.verify_unit(self.backend_, self.log)
        self.assertEqual(rc, 2)
        self.assertIn("no programmed record", msg)


class StationTest(Base):
    """The browser-UI station with the option C backend, including the re-plug check."""

    def setUp(self):
        super().setUp()
        self.sys.add_adapter(serial="CP0001")
        self.st = factory_ui.Station(self.backend(), KEY, self.log)
        self.st.set_run("R1", BLOCK, 3)

    def replug(self, correct=True):
        self.sys.unplug()
        self.assertIsNone(self.st.auto_step())          # absent: arms the check
        self.sys.add_adapter(serial="CP0001")
        if correct:
            fk.replug(self.sys, "1-1", "eth2", self.eeproms["eth2"])
        else:
            self.sys.set_address("1-1", "eth2", "00:11:22:33:44:55")
        return self.st.auto_step()

    def test_program_then_verify_after_replug(self):
        res = self.st.program()
        self.assertEqual(res["outcome"], "ok")
        self.assertTrue(self.st.state()["station"]["awaiting_replug"])
        res = self.replug()
        self.assertEqual(res["outcome"], "ok")
        self.assertTrue(res["verified"])
        self.assertEqual(self.unit_rows()[-1]["status"], "verified")
        st = self.st.state()
        self.assertFalse(st["station"]["awaiting_replug"])
        self.assertEqual(st["run"]["done"], 1)
        self.assertEqual(st["recent"][0]["status"], "verified")

    def test_no_verify_before_replug(self):
        # Regression: the check must wait for the unit to go away and come back.
        self.assertEqual(self.st.program()["outcome"], "ok")
        for _ in range(3):
            self.assertIsNone(self.st.auto_step())
        self.assertTrue(self.st.state()["station"]["awaiting_replug"])
        self.assertEqual(self.unit_rows()[-1]["status"], "locked")

    def test_verify_failure(self):
        self.st.program()
        res = self.replug(correct=False)
        self.assertEqual(res["outcome"], "failed")
        self.assertIn("mismatch", res["message"])
        self.assertEqual(self.unit_rows()[-1]["status"], "verify-failed")
        self.assertEqual(self.st.state()["run"]["verify_failed"], 1)

    def test_auto_mode_programs_new_units_once(self):
        self.st.auto = True
        self.assertEqual(self.st.auto_step()["outcome"], "ok")
        # Same unit re-plugged: verified, not programmed again.
        self.assertTrue(self.replug()["verified"])
        # A new unit is programmed with the next address.
        self.sys.unplug()
        self.st.auto_step()
        self.sys.add_adapter(serial="CP0002")
        self.eeproms["eth2"] = fk.FakeEeprom()
        self.chips["/dev/ttyUSB0"] = {}
        res = self.st.auto_step()
        self.assertEqual((res["outcome"], res["serial"], res["mac"]), ("ok", "CP0002", "00:50:c2:aa:00:01"))

    # --- labelling rule: no MAC before VERIFIED ---------------------------------
    def assert_no_mac_in_state(self, mac):
        """The unit's MAC appears nowhere in the page data. The run's block string is
        excluded: it is run configuration, and its first address is the first unit's."""
        import json
        st = self.st.state()
        for r in st["runs"] + ([st["run"]] if st["run"] else []):
            r.pop("block", None)
        self.assertNotIn(mac, json.dumps(st))

    def test_mac_not_revealed_before_verified(self):
        res = self.st.program()
        mac = res["mac"]
        self.assert_no_mac_in_state(mac)          # tile, unit list: nowhere in the page data
        st = self.st.state()
        self.assertIsNone(st["last"]["mac"])
        self.assertTrue(st["recent"][0]["awaiting"])
        self.assertEqual(st["recent"][0]["detail"], "awaiting re-plug verification")
        self.replug()
        st = self.st.state()
        self.assertEqual(st["last"]["mac"], mac)  # revealed once VERIFIED
        self.assertEqual({r["mac"] for r in st["recent"]}, {mac})

    def test_mac_never_revealed_when_verify_fails(self):
        mac = self.st.program()["mac"]
        self.replug(correct=False)
        self.assert_no_mac_in_state(mac)

    def test_no_other_unit_while_waiting(self):
        self.st.program()
        again = self.st.program()                  # manual Program: refused
        self.assertEqual(again["outcome"], "blocked")
        self.assertEqual(len([r for r in self.unit_rows() if r["status"] == "reserved"]), 1)
        # A different unit plugged in: refused, the waiting unit is named.
        self.sys.unplug()
        self.st.auto_step()
        self.sys.add_adapter(serial="CP0002")
        self.st.auto = True
        res = self.st.auto_step()
        self.assertEqual(res["outcome"], "blocked")
        self.assertIn("CP0001", res["message"])
        self.assertEqual(len([r for r in self.unit_rows() if r["status"] == "reserved"]), 1)
        with self.assertRaises(ValueError):
            self.st.clear_run()

    def test_mark_for_rework(self):
        mac = self.st.program()["mac"]
        self.st.abandon_verification()
        self.assertEqual(self.unit_rows()[-1]["status"], "verify-failed")
        self.assertIn("rework", self.unit_rows()[-1]["detail"])
        self.assert_no_mac_in_state(mac)
        self.assertFalse(self.st.state()["station"]["awaiting_replug"])
        with self.assertRaises(ValueError):
            self.st.abandon_verification()        # nothing waiting any more

    def test_state_shows_backend_and_warnings(self):
        st = self.st.state()["station"]
        self.assertEqual(st["backend"], "c")
        self.assertIn("eth2", st["device"])
        with mock.patch.object(optionc, "LAYOUT_VERIFIED", False):
            self.assertTrue(any("unverified" in w for w in self.st.state()["station"]["warnings"]))


if __name__ == "__main__":
    unittest.main()

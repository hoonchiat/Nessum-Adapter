#!/usr/bin/env python3
"""Tests for factory_program.py against the fake adapter. Run: python3 -m unittest -v"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_program as fp  # noqa: E402
import fake_adapter  # noqa: E402
import nessumctl  # noqa: E402

BLOCK = "00:50:c2:aa:00:00-00:50:c2:aa:00:02"  # 3 addresses
KEY = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")


class BlockTest(unittest.TestCase):
    def test_range(self):
        first, last = fp.parse_block(BLOCK)
        self.assertEqual(fp.int_to_mac(first), "00:50:c2:aa:00:00")
        self.assertEqual(last - first, 2)

    def test_prefix(self):
        first, last = fp.parse_block("00:50:c2:aa:b0:00/36")
        self.assertEqual(fp.int_to_mac(first), "00:50:c2:aa:b0:00")
        self.assertEqual(fp.int_to_mac(last), "00:50:c2:aa:bf:ff")

    def test_rejects(self):
        for bad in ("00:50:c2:aa:00:02-00:50:c2:aa:00:00", "01:00:5e:00:00:00/40",
                    "00:50:c2:aa:00:00/8", "00:50:c2:aa:00:00", "junk"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, nessumctl.MacError)):
                fp.parse_block(bad)


class ProgramTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "macs.csv")
        self.first, self.last = fp.parse_block(BLOCK)

    def program(self, adapter=None, run="R1", block=BLOCK, quantity=3, key=KEY, **kw):
        adapter, path, stop = fake_adapter.start(adapter)
        self.addCleanup(stop)
        first, last = fp.parse_block(block)
        with nessumctl.Console(path, 2) as con:
            rc, msg = fp.program_one(con, self.log, run, first, last, quantity, key, **kw)
        return adapter, rc, msg

    def unit_rows(self):
        return [r for r in fp.read_log(self.log) if r["status"] != "run-open"]

    def test_programs_locks_and_logs(self):
        a, rc, msg = self.program()
        self.assertEqual(rc, 0, msg)
        self.assertEqual(a.programmed, "00:50:c2:aa:00:00")
        self.assertEqual(a.nkey, KEY)
        self.assertTrue(a.locked)
        self.assertEqual(a.active, "00:50:c2:aa:00:00")  # rebooted
        rows = self.unit_rows()
        statuses = [(r["status"], r["mac"], r["serial"]) for r in rows]
        self.assertEqual(statuses, [("reserved", "00:50:c2:aa:00:00", "SIM0001"),
                                    ("locked", "00:50:c2:aa:00:00", "SIM0001")])
        self.assertTrue(all(r["key_fp"] == nessumctl.key_fingerprint(KEY) for r in rows))
        self.assertTrue(all(r["run"] == "R1" for r in fp.read_log(self.log)))
        self.assertIn("unit 1/3", msg)
        with open(self.log) as f:
            self.assertNotIn(KEY.hex(), f.read())  # the key itself is never logged

    def test_sequential_units_get_unique_addresses(self):
        macs = []
        for n in range(3):
            a, rc, msg = self.program(fake_adapter.FakeAdapter(serial=f"U{n}"))
            self.assertEqual(rc, 0, msg)
            macs.append(a.programmed)
        self.assertEqual(len(set(macs)), 3)
        _, rc, msg = self.program(fake_adapter.FakeAdapter(serial="U3"))
        self.assertEqual(rc, 1)
        self.assertIn("complete (3/3", msg)

    def test_stops_at_quantity_before_block_is_used_up(self):
        for n in range(2):
            self.assertEqual(self.program(fake_adapter.FakeAdapter(serial=f"U{n}"), quantity=2)[1], 0)
        a, rc, msg = self.program(fake_adapter.FakeAdapter(serial="U2"), quantity=2)
        self.assertEqual(rc, 1)
        self.assertIn("complete", msg)
        self.assertIsNone(a.programmed)  # the unit was not touched

    def test_failures_can_exhaust_block(self):
        bad = fake_adapter.FakeAdapter(serial="BAD")
        bad.fail_storage = True
        self.program(bad, quantity=2, block="00:50:c2:aa:00:00-00:50:c2:aa:00:01")
        self.assertEqual(self.program(quantity=2, block="00:50:c2:aa:00:00-00:50:c2:aa:00:01")[1], 0)
        _, rc, msg = self.program(fake_adapter.FakeAdapter(serial="X"), quantity=2,
                                  block="00:50:c2:aa:00:00-00:50:c2:aa:00:01")
        self.assertEqual(rc, 1)
        self.assertIn("exhausted after 1/2", msg)

    def test_run_parameters_fixed_by_first_unit(self):
        self.program()
        for kw in ({"quantity": 2}, {"block": "00:50:c2:aa:00:00-00:50:c2:aa:00:09"}):
            with self.subTest(kw=kw):
                a, rc, msg = self.program(fake_adapter.FakeAdapter(serial="X"), **kw)
                self.assertEqual(rc, 2)
                self.assertIn("refusing different values mid-run", msg)
                self.assertIsNone(a.programmed)

    def test_block_smaller_than_quantity(self):
        _, rc, msg = self.program(quantity=4)
        self.assertEqual(rc, 2)
        self.assertIn("fewer than --quantity 4", msg)

    def test_runs_cannot_overlap(self):
        self.program(run="R1")
        a, rc, msg = self.program(fake_adapter.FakeAdapter(serial="X"), run="R2",
                                  block="00:50:c2:aa:00:02-00:50:c2:aa:00:05")
        self.assertEqual(rc, 2)
        self.assertIn("overlaps run R1", msg)
        a, rc, msg = self.program(fake_adapter.FakeAdapter(serial="Y"), run="R2",
                                  block="00:50:c2:aa:00:03-00:50:c2:aa:00:05")
        self.assertEqual(rc, 0, msg)
        self.assertEqual(a.programmed, "00:50:c2:aa:00:03")

    def test_common_key_enforced(self):
        self.program()
        other = bytes.fromhex("aa" * 16)
        a, rc, msg = self.program(fake_adapter.FakeAdapter(serial="X"), key=other)
        self.assertEqual(rc, 2)
        self.assertIn("differs from earlier units", msg)
        self.assertIsNone(a.programmed)
        a, rc, _ = self.program(fake_adapter.FakeAdapter(serial="Y"), key=other, new_key=True)
        self.assertEqual(rc, 0)
        self.assertEqual(a.nkey, other)

    def test_failed_write_retires_address(self):
        bad = fake_adapter.FakeAdapter(serial="BAD")
        bad.fail_storage = True
        _, rc, msg = self.program(bad)
        self.assertEqual(rc, 1)
        self.assertIn("retired", msg)
        a, rc, _ = self.program(fake_adapter.FakeAdapter(serial="GOOD"))
        self.assertEqual(rc, 0)
        self.assertEqual(a.programmed, "00:50:c2:aa:00:01")  # :00 never reused

    def test_refuses_locked_or_programmed_units(self):
        locked = fake_adapter.FakeAdapter()
        locked.programmed, locked.locked = "00:50:c2:aa:99:99", True
        _, rc, msg = self.program(locked, force=True)
        self.assertEqual(rc, 1)
        self.assertIn("already locked", msg)

        prog = fake_adapter.FakeAdapter()
        prog.programmed = "00:50:c2:aa:99:99"
        _, rc, msg = self.program(prog)
        self.assertEqual(rc, 1)
        self.assertIn("--force", msg)
        self.assertEqual(self.unit_rows(), [])  # nothing reserved

    def test_no_lock(self):
        a, rc, _ = self.program(lock=False)
        self.assertEqual(rc, 0)
        self.assertFalse(a.locked)
        self.assertEqual(self.unit_rows()[-1]["status"], "programmed")


if __name__ == "__main__":
    unittest.main()

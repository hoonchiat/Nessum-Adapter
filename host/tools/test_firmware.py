#!/usr/bin/env python3
"""Conformance of the option A firmware core with the host tools.

Builds firmware/build/fwsim (the C core on a pseudo-terminal) and checks that:
  * it answers exactly like fake_adapter.FakeAdapter, the protocol reference;
  * nessumctl and factory_program work against it unchanged;
  * MAC, key and lock survive a power cycle (restart with the same state dir);
  * storage failures are reported as ERR 4.

Skipped if there is no C compiler / make. Run: python3 -m unittest -v test_firmware
"""

import contextlib
import io
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_program as fp  # noqa: E402
import fake_adapter  # noqa: E402
import nessumctl  # noqa: E402

FIRMWARE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "firmware"))
FWSIM = os.path.join(FIRMWARE, "build", "fwsim")
KEY = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")
BLOCK = "00:50:c2:aa:00:00-00:50:c2:aa:00:02"


def build_fwsim():
    if not shutil.which("make") or not (shutil.which("cc") or shutil.which("gcc")):
        return "no C compiler / make"
    r = subprocess.run(["make", "-C", FIRMWARE, "fwsim"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("fwsim build failed:\n" + r.stdout + r.stderr)
    return None


SKIP = build_fwsim()


class FwSim:
    """One fwsim process = one powered-on adapter."""

    def __init__(self, state, *flags, serial="SIM0001"):
        self.proc = subprocess.Popen([FWSIM, "--state", state, "--serial", serial, *flags],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.path = self.proc.stdout.readline().strip()
        if not self.path.startswith("/dev/"):
            self.stop()
            raise RuntimeError("fwsim did not start: " + self.proc.stderr.read())

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(5)
        self.proc.stdout.close()
        self.proc.stderr.close()


class Raw:
    """Request/response on a console without nessumctl's error handling."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        self.buf = b""

    def close(self):
        os.close(self.fd)

    def ask(self, line, timeout=2.0):
        os.write(self.fd, line.encode("ascii") + b"\r\n")
        out, deadline = [], time.monotonic() + timeout
        while True:
            while b"\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n", 1)
                text = raw.rstrip(b"\r").decode("ascii")
                out.append(text)
                if text == "OK" or text.startswith("ERR "):
                    return out
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise TimeoutError(f"no reply to {line!r} (got {out})")
            self.buf += os.read(self.fd, 512)


# Each script runs on a fresh adapter; every response must match the reference.
SCRIPTS = [
    ["STATUS", "MAC GET", "NKEY GET", "bogus", "mac get", "MAC SET", "MAC SET a b",
     "MAC SET zz:00:00:00:00:00", "MAC SET 01:00:5e:00:00:01", "MAC SET 00:00:00:00:00:00",
     "MAC SET ff:ff:ff:ff:ff:ff", "MAC SET 00:50:c2:aa:bb", "MAC SET 00:50-c2:aa:bb:cc",
     "MAC SET 00-50-C2-AA-BB-CC", "MAC GET", "REBOOT", "MAC GET", "MAC CLEAR", "REBOOT", "MAC GET"],
    ["MAC SET 0050c2aabbcc", "LOCK", "NKEY SET", "NKEY SET 0g", "NKEY SET abc", "NKEY SET 00112233",
     "NKEY SET " + "00" * 16, "NKEY SET " + "00" * 17, "NKEY SET " + KEY.hex().upper(), "NKEY GET",
     "NESSUM", "NESSUM SET CHANNEL 3", "NESSUM stats", "LOCK", "MAC GET", "MAC SET 00:50:c2:aa:bb:cd",
     "MAC CLEAR", "NKEY SET " + KEY.hex(), "NESSUM SET CHANNEL 3", "NESSUM", "NESSUM peers",
     "NESSUM STATUS x", "REBOOT", "MAC GET", "NKEY GET"],
    ["NKEY SET " + KEY.hex(), "LOCK", "MAC SET 02:00:00:00:00:01", "LOCK", "LOCK", "MAC GET"],
]


@unittest.skipIf(SKIP, SKIP)
class FirmwareTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name

    def boot(self, *flags):
        sim = FwSim(self.state, *flags)
        self.addCleanup(sim.stop)
        return sim

    def raw(self, sim):
        r = Raw(sim.path)
        self.addCleanup(r.close)
        return r

    def test_matches_reference(self):
        for n, script in enumerate(SCRIPTS):
            with self.subTest(script=n), tempfile.TemporaryDirectory() as state:
                sim = FwSim(state)
                try:
                    con, ref = Raw(sim.path), fake_adapter.FakeAdapter()
                    for line in script:
                        self.assertEqual(con.ask(line), ref.handle(line), line)
                    con.close()
                finally:
                    sim.stop()

    def test_version(self):
        reply = self.raw(self.boot()).ask("VERSION")
        self.assertEqual(reply[-1], "OK")
        kv = nessumctl.parse_kv(reply[0])
        self.assertEqual((kv["serial"], kv["proto"], kv["hw"]), ("SIM0001", "1", "SIM"))
        self.assertRegex(kv["fw"], r"^\d+\.\d+\.\d+$")

    def test_long_line_and_recovery(self):
        con = self.raw(self.boot())
        self.assertTrue(con.ask("X" * 300)[0].startswith("ERR 2 line too long"))
        self.assertEqual(con.ask("STATUS")[-1], "OK")

    def test_nessumctl(self):
        sim = self.boot()

        def cli(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = nessumctl.main(["-d", sim.path, "-t", "2", *argv])
            return rc, out.getvalue(), err.getvalue()

        self.assertEqual(cli("mac", "get", "-q")[1].strip(), fake_adapter.DEFAULT_MAC)
        rc, out, err = cli("mac", "set", "00:50:c2:aa:bb:cc", "--apply")
        self.assertEqual(rc, 0, err)
        rc, out, _ = cli("mac", "get")
        self.assertIn("active     00:50:c2:aa:bb:cc", out)
        self.assertIn("source     programmed", out)
        self.assertEqual(cli("version")[0], 0)
        self.assertEqual(cli("status")[0], 0)

    def test_factory_program_then_power_cycle(self):
        log = os.path.join(self.state, "macs.csv")
        first, last = fp.parse_block(BLOCK)
        sim = self.boot()
        with nessumctl.Console(sim.path, 2) as con:
            rc, msg = fp.program_one(con, log, "R1", first, last, 3, KEY)
        self.assertEqual(rc, 0, msg)
        sim.stop()

        con = self.raw(self.boot())   # power cycle: same EEPROM and key blob
        mac = nessumctl.parse_kv(con.ask("MAC GET")[0])
        self.assertEqual((mac["active"], mac["source"], mac["locked"]), ("00:50:c2:aa:00:00", "programmed", "yes"))
        self.assertEqual(con.ask("NKEY GET")[0], f"set=yes fp={nessumctl.key_fingerprint(KEY)} len=16")
        self.assertEqual(con.ask("MAC CLEAR"), ["ERR 6 adapter is factory-locked"])
        with open(os.path.join(self.state, "keyblob.bin"), "rb") as f:
            self.assertNotIn(KEY, f.read())   # stored sealed, never in clear

        # A second programming attempt on the locked unit is refused.
        with nessumctl.Console(self.boot().path, 2) as c2:
            rc, msg = fp.program_one(c2, log, "R1", first, last, 3, KEY)
        self.assertEqual(rc, 1)
        self.assertIn("already locked", msg)

    def test_eeprom_failure(self):
        con = self.raw(self.boot("--fail-eeprom-writes"))
        reply = con.ask("MAC SET 00:50:c2:aa:bb:cc")
        self.assertEqual(len(reply), 1)
        self.assertTrue(reply[0].startswith("ERR 4 "), reply)
        self.assertIn("programmed=none", con.ask("MAC GET")[0])

    def test_keyblob_failure(self):
        reply = self.raw(self.boot("--fail-keyblob-writes")).ask("NKEY SET " + KEY.hex())
        self.assertTrue(reply[0].startswith("ERR 4 "), reply)

    def test_nessum_down(self):
        con = self.raw(self.boot("--nessum-down"))
        self.assertTrue(con.ask("NESSUM STATUS")[0].startswith("ERR 5 "))
        self.assertTrue(con.ask("NKEY SET " + KEY.hex())[0].startswith("ERR 5 "))
        # Stored even so: it is reported as set and is loaded at the next boot.
        self.assertTrue(con.ask("NKEY GET")[0].startswith("set=yes "))


if __name__ == "__main__":
    unittest.main()

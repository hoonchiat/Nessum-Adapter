#!/usr/bin/env python3
"""Tests for labels.py and label printing at the station (factory_ui / factory_program)."""

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import xml.dom.minidom

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_program as fp  # noqa: E402
import factory_ui  # noqa: E402
import fake_adapter  # noqa: E402
import fake_optionc as fk  # noqa: E402
import labels  # noqa: E402
import optionc  # noqa: E402

KEY = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")
BLOCK = "00:50:c2:aa:00:00-00:50:c2:aa:00:09"

# Golden symbols, cross-checked against the python-barcode library's Code 128 tables
# (code set B, including start, checksum and stop) when this encoder was written.
GOLDEN = {
    "0050C2AA0007": "1101001000010011101100100111011001101110010010011101100100010001101100111001010100"
                    "0110001010001100010011101100100111011001001110110011101101110111100010101100011101011",
    "A1B2C3D4E5F6": "1101001000010100011000100111001101000101100011001110010100010001101100101110010110"
                    "0010001100100111010001101000110111001001000110001011001110100100010011001100011101011",
}


def decode(bars):
    """Minimal Code 128-B decoder (tests the encoder from the other direction)."""
    table = {p: v for v, p in enumerate(labels._PATTERNS)}
    widths, i = [], 0
    while i < len(bars):
        j = i
        while j < len(bars) and bars[j] == bars[i]:
            j += 1
        widths.append(j - i)
        i = j
    syms = ["".join(map(str, widths[k:k + 6])) for k in range(0, len(widths) - 7, 6)]
    vals = [table[s] for s in syms]
    assert vals[0] == labels.START_B
    data, check = vals[1:-1], vals[-1]
    assert check == (labels.START_B + sum(i * v for i, v in enumerate(data, 1))) % 103
    assert "".join(map(str, widths[-7:])) == labels._PATTERNS[labels.STOP]
    return "".join(chr(v + 32) for v in data)


class Code128Test(unittest.TestCase):
    def test_table(self):
        self.assertEqual(len(labels._PATTERNS), 107)
        self.assertEqual(len(set(labels._PATTERNS)), 107)
        self.assertTrue(all(sum(map(int, p)) == 11 for p in labels._PATTERNS[:106]))
        self.assertEqual(sum(map(int, labels._PATTERNS[106])), 13)

    def test_golden(self):
        for data, bars in GOLDEN.items():
            with self.subTest(data=data):
                self.assertEqual(labels.code128_bars(data), bars)

    def test_roundtrip(self):
        for data in ("0050C2AA0007", "FFFFFFFFFFFE", "Hello, World!", "~ {}|"):
            with self.subTest(data=data):
                self.assertEqual(decode(labels.code128_bars(data)), data)

    def test_rejects_non_ascii(self):
        for bad in ("", "é", "tab\t"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                labels.code128_values(bad)


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.f = labels.label_fields({"mac": "00:50:C2:AA:00:07", "serial": "CP2102N-0001", "run": "R2026-10"},
                                     date="2026-09-30")

    def test_fields(self):
        self.assertEqual(self.f["mac"], "00:50:c2:aa:00:07")
        self.assertEqual(self.f["barcode"], "0050C2AA0007")
        bad = labels.label_fields({"mac": "00:50:c2:aa:00:07", "serial": "X^XZ~JA"})
        self.assertNotIn("^", bad["serial"])
        self.assertNotIn("~", bad["serial"])

    def test_zpl(self):
        z = labels.zpl_label(self.f)
        self.assertTrue(z.startswith("^XA") and z.strip().endswith("^XZ"))
        self.assertIn("^PW400", z)                  # 50 mm at 8 dots/mm
        self.assertIn("^BY2", z)                    # 2-dot modules fit with quiet zones
        self.assertIn("^FD0050C2AA0007^FS", z)
        self.assertIn("^FDMAC 00:50:c2:aa:00:07^FS", z)
        self.assertIn("^FDS/N CP2102N-0001^FS", z)
        self.assertEqual(z.count("^XA"), 1)          # sanitised data can't inject a label
        # barcode + quiet zones fit inside the label
        bx = int(z.split("^FO")[4].split(",")[0])
        self.assertGreaterEqual(bx, 2 * labels.QUIET)
        self.assertLessEqual(bx + 2 * len(labels.code128_bars("0050C2AA0007")) + 2 * labels.QUIET, 400)

    def test_svg(self):
        svg = labels.svg_label(self.f, (50, 25))
        xml.dom.minidom.parseString(svg)
        self.assertIn('width="50mm"', svg)
        self.assertIn("MAC 00:50:c2:aa:00:07", svg)
        self.assertIn("S/N CP2102N-0001", svg)

    def test_parse_size_and_printers(self):
        self.assertEqual(labels.parse_size("50x25"), (50.0, 25.0))
        self.assertEqual(labels.parse_size(" 62 x 29 mm "), (62.0, 29.0))
        for bad in ("50", "5x5", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                labels.parse_size(bad)
        self.assertEqual(labels.make_printer("browser").mode, "browser")
        p = labels.make_printer("zpl:tcp://10.0.0.5")
        self.assertEqual((p.host, p.port), ("10.0.0.5", 9100))
        self.assertEqual(labels.make_printer("zpl:tcp://h:6101").port, 6101)
        self.assertEqual(labels.make_printer("zpl:/dev/usb/lp0").path, "/dev/usb/lp0")
        with self.assertRaises(ValueError):
            labels.make_printer("lpr:foo")

    def test_tcp_printer(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        got = []

        def accept():
            c, _ = srv.accept()
            with c:
                data = b""
                while chunk := c.recv(4096):
                    data += chunk
                got.append(data)
        t = threading.Thread(target=accept)
        t.start()
        labels.ZplTcpPrinter("127.0.0.1", srv.getsockname()[1]).send(self.f, (50, 25))
        t.join(5)
        srv.close()
        self.assertIn(b"^FD0050C2AA0007^FS", got[0])

    def test_tcp_printer_down(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(labels.PrintError):
            labels.ZplTcpPrinter("127.0.0.1", port, timeout=1).send(self.f, (50, 25))


class StationLabelTest(unittest.TestCase):
    """Labels at the option C station: only after VERIFIED, every print logged."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.log = os.path.join(tmp.name, "log.csv")
        self.sys = fk.FakeSysfs(os.path.join(tmp.name, "sys"))
        self.sys.add_adapter(serial="CP0001")
        self.eeproms, self.chips = {}, {}
        backend = optionc.BackendC(
            sysfs=self.sys.root, engineering=True,
            eeprom_factory=lambda i: self.eeproms.setdefault(i, fk.FakeEeprom()),
            nessum_factory=lambda t: fk.FakeNessumIc(self.chips.setdefault(t, {})))
        self.printer = labels.FilePrinter(os.path.join(tmp.name, "labels"))
        self.st = factory_ui.Station(backend, KEY, self.log, printer=self.printer)
        self.st.set_run("R1", BLOCK, 3)

    def replug(self, correct=True):
        self.sys.unplug()
        self.st.auto_step()
        self.sys.add_adapter(serial="CP0001")
        if correct:
            fk.replug(self.sys, "1-1", "eth2", self.eeproms["eth2"])
        else:
            self.sys.set_address("1-1", "eth2", "00:11:22:33:44:55")
        return self.st.auto_step()

    def printed_rows(self):
        return [r for r in fp.read_log(self.log) if r["status"] == "label-printed"]

    def test_no_label_before_verified(self):
        self.st.program()
        with self.assertRaisesRegex(ValueError, "not VERIFIED"):
            self.st.print_label("CP0001")
        with self.assertRaises(ValueError):
            self.st.label_svg("CP0001")
        self.assertFalse(self.st.state()["last"].get("printable"))
        self.assertEqual(self.printed_rows(), [])

    def test_print_after_verified_and_reprint(self):
        mac = self.st.program()["mac"]
        self.replug()
        last = self.st.state()["last"]
        self.assertTrue(last["printable"])
        self.assertEqual(last["labels"], 0)
        out = self.st.print_label("CP0001")
        self.assertEqual((out["mode"], out["count"]), ("file", 1))
        with open(out["sent_to"]) as f:
            self.assertIn("^FD" + mac.replace(":", "").upper() + "^FS", f.read())
        self.st.print_label("CP0001")
        rows = self.printed_rows()
        self.assertEqual(len(rows), 2)
        self.assertIn("reprint 1", rows[1]["detail"])
        st = self.st.state()
        self.assertEqual(st["last"]["labels"], 2)
        self.assertEqual(st["run"]["labelled"], 1)
        verified_row = next(r for r in st["recent"] if r["status"] == "verified")
        self.assertTrue(verified_row["printable"])
        self.assertEqual(verified_row["labels"], 2)
        # label rows don't disturb counting or MAC allocation
        self.assertEqual(st["run"]["done"], 1)
        self.assertIn(mac, self.st.label_svg("CP0001"))

    def test_no_label_after_failed_verification(self):
        self.st.program()
        self.replug(correct=False)
        with self.assertRaisesRegex(ValueError, "verification failed"):
            self.st.print_label("CP0001")

    def test_http(self):
        server = factory_ui.make_server(self.st, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            h = {"Content-Type": "application/json", "X-Station": "1"} if method == "POST" else {}
            c.request(method, path, json.dumps(body) if body is not None else None, h)
            r = c.getresponse()
            data = r.read()
            c.close()
            return r.status, r.getheader("Content-Type"), data

        self.st.program()
        self.assertEqual(req("GET", "/api/label.svg?serial=CP0001")[0], 403)
        self.assertEqual(req("POST", "/api/label/print", {"serial": "CP0001"})[0], 400)
        self.replug()
        status, ctype, data = req("GET", "/api/label.svg?serial=CP0001")
        self.assertEqual((status, ctype), (200, "image/svg+xml"))
        status, _, data = req("POST", "/api/label/print", {"serial": "CP0001"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["print"]["count"], 1)
        status, _, data = req("POST", "/api/label/auto", {"on": True})
        self.assertTrue(json.loads(data)["station"]["auto_print"])


class OptionALabelTest(unittest.TestCase):
    def test_option_a_label_after_programming(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = os.path.join(tmp.name, "log.csv")
        adapter, path, stop = fake_adapter.start()
        self.addCleanup(stop)
        st = factory_ui.Station(path, KEY, log, printer=labels.FilePrinter(os.path.join(tmp.name, "l")))
        st.set_run("R1", BLOCK, 2)
        self.assertEqual(st.program()["outcome"], "ok")
        self.assertTrue(st.state()["last"]["printable"])   # option A: the MCU verified it
        self.assertEqual(st.print_label("SIM0001")["count"], 1)


class CliLabelTest(unittest.TestCase):
    def test_verify_then_print(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sysfs = fk.FakeSysfs(os.path.join(tmp.name, "sys"))
        sysfs.add_adapter(serial="CP0001")
        log = os.path.join(tmp.name, "log.csv")
        eeprom = fk.FakeEeprom()
        be = optionc.BackendC(sysfs=sysfs.root, engineering=True, eeprom_factory=lambda i: eeprom,
                              nessum_factory=lambda t: fk.FakeNessumIc({}))
        first, last = fp.parse_block(BLOCK)
        unit = be.open_unit()
        fp.program_unit(unit, log, "R1", first, last, 2, KEY)
        sysfs.unplug()
        sysfs.add_adapter(serial="CP0001")
        fk.replug(sysfs, "1-1", "eth2", eeprom)
        outdir = os.path.join(tmp.name, "labels")
        rc = fp.main(["--verify", "--log", log, "--sysfs", sysfs.root, "--engineering",
                      "--printer", f"file:{outdir}"])
        self.assertEqual(rc, 0)
        self.assertEqual(len([f for f in os.listdir(outdir) if f.endswith(".zpl")]), 1)
        rc = fp.main(["--verify", "--log", log, "--sysfs", sysfs.root, "--engineering", "--printer", "browser"])
        self.assertEqual(rc, 3)   # the browser printer only exists in the UI


if __name__ == "__main__":
    unittest.main()

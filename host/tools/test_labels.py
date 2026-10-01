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
import hashlib  # noqa: E402

import labels  # noqa: E402
import optionc  # noqa: E402
import qr  # noqa: E402

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


# SHA-256 (first 16 hex) of the version 3-M symbol for QR_TEXT with each mask 0..7.
# Each symbol was checked module-for-module against the 'qrcode' library when written.
QR_TEXT = "0050C2AA0007;CP2102N-0001;R2026-10"
QR_GOLDEN = {0: "fcb822ff712b7850", 1: "6eb74ab4dc92d2bf", 2: "aed1885709547bcf", 3: "122b1f8b0807ae3c",
             4: "e8195d5ec95c738a", 5: "363008e78bb471dd", 6: "7bf30f70ee7fc68a", 7: "5682da4331931cc0"}


def bits(m):
    return "".join("1" if v else "0" for row in m for v in row)


class QrTest(unittest.TestCase):
    def test_golden_all_masks(self):
        self.assertEqual(qr.choose_version(len(QR_TEXT), "M"), 3)
        for mask, h in QR_GOLDEN.items():
            with self.subTest(mask=mask):
                m = qr.encode(QR_TEXT, "M", 3, mask)
                self.assertEqual(hashlib.sha256(bits(m).encode()).hexdigest()[:16], h)

    def test_structure(self):
        m = qr.encode(QR_TEXT)
        n = len(m)
        self.assertEqual(n, 29)
        finder = [[max(abs(dx), abs(dy)) not in (2, 4) for dx in range(-3, 4)] for dy in range(-3, 4)]
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            self.assertEqual([row[cx - 3:cx + 4] for row in m[cy - 3:cy + 4]], finder)
        self.assertEqual([m[6][x] for x in range(8, n - 8)], [x % 2 == 0 for x in range(8, n - 8)])  # timing
        self.assertTrue(m[n - 8][8])                                                       # dark module

    def test_format_info_decodes(self):
        """The 15 format bits around the top-left finder decode to (M, chosen mask)."""
        for mask in range(8):
            m = qr.encode(QR_TEXT, "M", 3, mask)
            got = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
            word = sum(1 << i for i, v in enumerate(got) if v) ^ 0x5412
            self.assertEqual((word >> 10) >> 3, qr.ECC_LEVELS["M"])
            self.assertEqual((word >> 10) & 7, mask)

    def test_versions_and_capacity(self):
        self.assertEqual(qr.data_codewords(1, "M"), 16)
        self.assertEqual(qr.data_codewords(3, "M"), 44)
        self.assertEqual(qr.data_codewords(7, "M"), 124)
        self.assertEqual(len(qr.encode("x" * 110)), 4 * qr.choose_version(110) + 17)
        self.assertEqual(qr.choose_version(110), 7)                 # exercises version information
        with self.assertRaises(ValueError):
            qr.encode("x" * 300)


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
        self.assertIn("^FD00:50:c2:aa:00:07^FS", z)
        self.assertIn("^FDS/N CP2102N-0001^FS", z)
        self.assertIn("^BQN,2,3^FDMA,0050C2AA0007;CP2102N-0001;R2026-10^FS", z)
        self.assertEqual(z.count("^XA"), 1)          # sanitised data can't inject a label
        bc = next(l for l in z.splitlines() if "^BC" in l)
        bx, by = (int(v) for v in bc[3:].split("^")[0].split(","))
        self.assertGreaterEqual(bx, 2 * labels.QUIET)  # barcode + quiet zones fit inside the label
        self.assertLessEqual(bx + 2 * len(labels.code128_bars("0050C2AA0007")) + 2 * labels.QUIET, 400)
        # QR (3 dots/module, version 3 = 29 modules) ends above the barcode
        self.assertLess(20 + 3 * 29, by)

    def test_svg(self):
        svg = labels.svg_label(self.f, (50, 25))
        xml.dom.minidom.parseString(svg)
        self.assertIn('width="50mm"', svg)
        self.assertIn("00:50:c2:aa:00:07", svg)
        self.assertIn("S/N CP2102N-0001", svg)
        self.assertIn('shape-rendering="crispEdges"', svg)

    def test_qr_template(self):
        self.assertEqual(self.f["qr"], "0050C2AA0007;CP2102N-0001;R2026-10")
        rec = {"mac": "00:50:c2:aa:00:07", "serial": "CP1", "run": "R1"}
        f = labels.label_fields(rec, qr_template="MAC={mac} SN={serial}")
        self.assertEqual(f["qr"], "MAC=00:50:c2:aa:00:07 SN=CP1")
        self.assertEqual(labels.label_fields(rec, qr_template="{barcode}^XZ~")["qr"], "0050C2AA0007?XZ?")
        for bad in ("{nope}", "{", "x" * 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                labels.label_fields(rec, qr_template=bad)

    def test_long_serial_fits(self):
        f = labels.label_fields({"mac": "00:50:c2:aa:00:08", "serial": "a4a8a10a7f9bec11b2fb8c3a0d2c2ff9",
                                 "run": "R2026-10"})
        svg = labels.svg_label(f)
        self.assertIn('lengthAdjust="spacingAndGlyphs"', svg)   # squeezed into the text column
        z = labels.zpl_label(f)
        self.assertIn("^BQN,2,2^", z)                            # version 4 needs a smaller module

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

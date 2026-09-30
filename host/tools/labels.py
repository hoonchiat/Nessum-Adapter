"""Unit labels for the production station: layout, Code 128 barcode, printers.

Label (default 50 x 25 mm): title, the MAC in text and as a Code 128 barcode (12 hex
digits, no colons), serial, run and date. Two renderings of the same layout:

* SVG, for the browser print path (any printer the station PC can print to) and
  for the on-screen preview. The barcode is drawn here (Code 128, code set B).
* ZPL, sent straight to a Zebra-compatible thermal printer, which draws the
  barcode itself (^BC).

Printers (``--printer``):
  browser                  the page prints the SVG label (window.print; silent with
                           Chrome's --kiosk-printing)
  zpl:tcp://HOST[:PORT]    raw ZPL over TCP (port 9100 by default)
  zpl:/dev/usb/lp0         raw ZPL to a device file (USB printer)
  file:DIR                 write <serial>_<mac>.zpl and .svg into DIR (testing)

Standard library only, like the other tools.
"""

import datetime
import os
import re
import socket
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- Code 128
# Bar/space module widths for values 0..106 (106 = stop, 7 elements, 13 modules).
_PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B, STOP = 104, 106
QUIET = 10  # quiet zone, modules each side


def code128_values(data):
    """Symbol values (start B, data, checksum, stop) for printable ASCII data."""
    if not data or any(not 32 <= ord(c) <= 126 for c in data):
        raise ValueError("Code 128-B encodes printable ASCII only")
    vals = [ord(c) - 32 for c in data]
    check = (START_B + sum(i * v for i, v in enumerate(vals, 1))) % 103
    return [START_B] + vals + [check, STOP]


def code128_modules(data):
    """Alternating bar/space widths in modules, starting with a bar."""
    return [int(w) for v in code128_values(data) for w in _PATTERNS[v]]


def code128_bars(data):
    """The symbol as a string of '1' (bar) and '0' (space) modules, no quiet zone."""
    out = []
    for i, w in enumerate(code128_modules(data)):
        out.append(("1" if i % 2 == 0 else "0") * w)
    return "".join(out)


# ---------------------------------------------------------------- label content
SAFE = re.compile(r"[^A-Za-z0-9 ._:/-]")


def clean(text, limit=40):
    """Printable, ZPL-safe text (no ^ or ~ control characters)."""
    return SAFE.sub("?", str(text))[:limit]


def label_fields(rec, title="Nessum Adapter", date=None):
    mac = rec["mac"].lower()
    return {
        "title": clean(title, 32),
        "mac": mac,
        "barcode": mac.replace(":", "").upper(),
        "serial": clean(rec.get("serial", "?"), 32),
        "run": clean(rec.get("run", ""), 24),
        "date": date or datetime.date.today().isoformat(),
    }


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(mm)?\s*", text or "")
    if not m:
        raise ValueError("label size must look like 50x25 (millimetres)")
    w, h = float(m.group(1)), float(m.group(2))
    if not (20 <= w <= 120 and 10 <= h <= 80):
        raise ValueError("label size out of range (20-120 x 10-80 mm)")
    return w, h


# ---------------------------------------------------------------- SVG (browser / preview)
def svg_label(fields, size=(50, 25)):
    w, h = size
    m = 2.0                                     # margin, mm
    bars = code128_bars(fields["barcode"])
    module = w / (len(bars) + 2 * QUIET)         # mm per module; quiet zones may use the margin
    bar_top, bar_h = h * 0.40, h * 0.34
    x0 = (w - len(bars) * module) / 2
    rects, i = [], 0
    while i < len(bars):
        if bars[i] == "1":
            j = i
            while j < len(bars) and bars[j] == "1":
                j += 1
            rects.append(f'<rect x="{x0 + i * module:.3f}" y="{bar_top:.3f}" '
                         f'width="{(j - i) * module:.3f}" height="{bar_h:.3f}"/>')
            i = j
        else:
            i += 1
    f = {k: escape(v) for k, v in fields.items()}
    t = h / 25.0                                 # text scale relative to a 25 mm label
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}" '
        f'font-family="DejaVu Sans, Arial, sans-serif">'
        f'<rect width="{w}" height="{h}" fill="#fff"/>'
        f'<text x="{m}" y="{m + 3.2 * t:.2f}" font-size="{3.2 * t:.2f}" font-weight="bold">{f["title"]}</text>'
        f'<text x="{w - m}" y="{m + 3.2 * t:.2f}" font-size="{2.4 * t:.2f}" text-anchor="end">{f["date"]}</text>'
        f'<text x="{m}" y="{m + 7.2 * t:.2f}" font-size="{3.4 * t:.2f}" font-family="DejaVu Sans Mono, monospace" '
        f'font-weight="bold">MAC {f["mac"]}</text>'
        f'<g fill="#000">{"".join(rects)}</g>'
        f'<text x="{m}" y="{h - m:.2f}" font-size="{2.6 * t:.2f}">S/N {f["serial"]}</text>'
        f'<text x="{w - m}" y="{h - m:.2f}" font-size="{2.6 * t:.2f}" text-anchor="end">Run {f["run"]}</text>'
        f'</svg>')


# ---------------------------------------------------------------- ZPL (thermal printers)
def zpl_label(fields, size=(50, 25), dpmm=8):
    """ZPL II for a Zebra-compatible printer (dpmm 8 = 203 dpi, 12 = 300 dpi)."""
    w, h = (round(v * dpmm) for v in size)
    d = lambda mm: round(mm * dpmm)  # noqa: E731
    symbol = len(code128_bars(fields["barcode"]))
    bw = max(1, min(3, w // (symbol + 2 * QUIET)))  # widest module (dots) that keeps the quiet zones
    bx = (w - symbol * bw) // 2
    return "\n".join([
        "^XA", "^CI28", f"^PW{w}", f"^LL{h}", "^LH0,0",
        f"^FO{d(2)},{d(1.5)}^A0N,{d(3.2)},{d(3.2)}^FD{fields['title']}^FS",
        f"^FO{d(2)},{d(1.5)}^FB{w - d(4)},1,0,R^A0N,{d(2.4)},{d(2.4)}^FD{fields['date']}^FS",
        f"^FO{d(2)},{d(5.2)}^A0N,{d(3.4)},{d(3.4)}^FDMAC {fields['mac']}^FS",
        f"^FO{bx},{d(10)}^BY{bw}^BCN,{d(8.5)},N,N,N^FD{fields['barcode']}^FS",
        f"^FO{d(2)},{h - d(4.6)}^A0N,{d(2.6)},{d(2.6)}^FDS/N {fields['serial']}^FS",
        f"^FO{d(2)},{h - d(4.6)}^FB{w - d(4)},1,0,R^A0N,{d(2.6)},{d(2.6)}^FDRun {fields['run']}^FS",
        "^PQ1", "^XZ", ""])


# ---------------------------------------------------------------- printers
class PrintError(RuntimeError):
    pass


class BrowserPrinter:
    """The page prints the SVG label itself (window.print)."""
    mode = "browser"

    def describe(self):
        return "browser print dialog"

    def send(self, fields, size):
        return {"mode": "browser", "svg": svg_label(fields, size)}


class ZplTcpPrinter:
    mode = "zpl"

    def __init__(self, host, port=9100, dpmm=8, timeout=5):
        self.host, self.port, self.dpmm, self.timeout = host, port, dpmm, timeout

    def describe(self):
        return f"ZPL to {self.host}:{self.port}"

    def send(self, fields, size):
        data = zpl_label(fields, size, self.dpmm).encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(data)
        except OSError as e:
            raise PrintError(f"printer {self.host}:{self.port} not reachable: {e}") from None
        return {"mode": "zpl", "sent_to": self.describe()}


class ZplDevicePrinter:
    mode = "zpl"

    def __init__(self, path, dpmm=8):
        self.path, self.dpmm = path, dpmm

    def describe(self):
        return f"ZPL to {self.path}"

    def send(self, fields, size):
        try:
            with open(self.path, "wb") as f:
                f.write(zpl_label(fields, size, self.dpmm).encode("utf-8"))
        except OSError as e:
            raise PrintError(f"printer {self.path}: {e.strerror}") from None
        return {"mode": "zpl", "sent_to": self.describe()}


class FilePrinter:
    """Writes the ZPL and SVG of each label into a directory (testing, or batch printing)."""
    mode = "file"

    def __init__(self, directory, dpmm=8):
        self.directory, self.dpmm = directory, dpmm

    def describe(self):
        return f"files in {self.directory}"

    def send(self, fields, size):
        os.makedirs(self.directory, exist_ok=True)
        base = os.path.join(self.directory, f"{fields['serial']}_{fields['barcode']}")
        with open(base + ".zpl", "w") as f:
            f.write(zpl_label(fields, size, self.dpmm))
        with open(base + ".svg", "w") as f:
            f.write(svg_label(fields, size))
        return {"mode": "file", "sent_to": base + ".zpl"}


def make_printer(spec, dpmm=8):
    spec = spec or "browser"
    if spec == "browser":
        return BrowserPrinter()
    if spec.startswith("zpl:tcp://"):
        hostport = spec[len("zpl:tcp://"):]
        host, _, port = hostport.partition(":")
        if not host:
            raise ValueError("zpl:tcp://HOST[:PORT] needs a host")
        return ZplTcpPrinter(host, int(port or 9100), dpmm)
    if spec.startswith("zpl:/"):
        return ZplDevicePrinter(spec[len("zpl:"):], dpmm)
    if spec.startswith("file:"):
        return FilePrinter(spec[len("file:"):], dpmm)
    raise ValueError(f"unknown printer '{spec}' (browser | zpl:tcp://HOST[:PORT] | zpl:/dev/... | file:DIR)")

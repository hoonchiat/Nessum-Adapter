"""Minimal QR Code encoder (ISO/IEC 18004): byte mode, versions 1-10, ECC level L/M/Q/H.

Standard library only. Used by labels.py for the SVG label; ZPL printers draw the
QR code themselves (^BQ). Structure follows the well-known reference algorithm:
data bits -> Reed-Solomon blocks -> interleave -> function patterns -> zigzag
placement -> mask with the lowest penalty -> format/version information.

    m = qr.encode("0050C2AA0007;CP2102N-0001;R2026-10")   # list of rows, True = dark
"""

ECC_LEVELS = {"L": 1, "M": 0, "Q": 3, "H": 2}           # format-information bits
_ORDER = "LMQH"
# Per version 1..10 (index 0 unused), per level L, M, Q, H.
ECC_PER_BLOCK = {
    "L": (None, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    "M": (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
    "Q": (None, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),
    "H": (None, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),
}
NUM_BLOCKS = {
    "L": (None, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    "M": (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
    "Q": (None, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),
    "H": (None, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),
}
MAX_VERSION = 10


# ---------------------------------------------------------------- Reed-Solomon over GF(256)
def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---------------------------------------------------------------- capacity
def _raw_data_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def data_codewords(ver, ecl):
    return _raw_data_modules(ver) // 8 - ECC_PER_BLOCK[ecl][ver] * NUM_BLOCKS[ecl][ver]


def _count_bits(ver):
    return 8 if ver <= 9 else 16


def choose_version(nbytes, ecl="M"):
    for ver in range(1, MAX_VERSION + 1):
        if 4 + _count_bits(ver) + 8 * nbytes <= data_codewords(ver, ecl) * 8:
            return ver
    raise ValueError(f"{nbytes} bytes do not fit in a version {MAX_VERSION}-{ecl} QR code")


# ---------------------------------------------------------------- encoding
def _data_codewords(payload, ver, ecl):
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in reversed(range(n)))

    put(0b0100, 4)                                     # byte mode
    put(len(payload), _count_bits(ver))
    for b in payload:
        put(b, 8)
    capacity = data_codewords(ver, ecl) * 8
    put(0, min(4, capacity - len(bits)))               # terminator
    put(0, (-len(bits)) % 8)                           # byte align
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]


def _add_ecc_and_interleave(data, ver, ecl):
    numblocks = NUM_BLOCKS[ecl][ver]
    blockecclen = ECC_PER_BLOCK[ecl][ver]
    rawcodewords = _raw_data_modules(ver) // 8
    numshort = numblocks - rawcodewords % numblocks
    shortlen = rawcodewords // numblocks
    divisor = _rs_divisor(blockecclen)
    blocks, k = [], 0
    for i in range(numblocks):
        dat = data[k:k + shortlen - blockecclen + (0 if i < numshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < numshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - blockecclen or j >= numshort:
                out.append(blk[i])
    return out


def _alignment_positions(ver, size):
    if ver == 1:
        return []
    numalign = ver // 7 + 2
    step = (ver * 4 + numalign * 2 + 1) // (numalign * 2 - 2) * 2
    result = [size - 7 - i * step for i in range(numalign - 1)] + [6]
    return list(reversed(result))


class _Matrix:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]

    def set_func(self, x, y, dark):
        self.mod[y][x] = dark
        self.func[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_func(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, n)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_func(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format_bits("M", 0)   # reserve the areas; real bits drawn after masking
        self.draw_version()

    def draw_format_bits(self, ecl, mask):
        data = ECC_LEVELS[ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 != 0  # noqa: E731
        n = self.size
        for i in range(0, 6):
            self.set_func(8, i, bit(i))
        self.set_func(8, 7, bit(6))
        self.set_func(8, 8, bit(7))
        self.set_func(7, 8, bit(8))
        for i in range(9, 15):
            self.set_func(14 - i, 8, bit(i))
        for i in range(0, 8):
            self.set_func(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_func(8, n - 15 + i, bit(i))
        self.set_func(8, n - 8, True)          # dark module

    def draw_version(self):
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a, b = self.size - 11 + i % 3, i // 3
            self.set_func(a, b, dark)
            self.set_func(b, a, dark)

    def draw_codewords(self, data):
        n, i = self.size, 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.func[y][x] and i < len(data) * 8:
                        self.mod[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        f = MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.func[y][x] and f(x, y):
                    self.mod[y][x] = not self.mod[y][x]


MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def penalty(mod):
    """Mask penalty score (rules N1-N4)."""
    n = len(mod)
    score = 0
    lines = [row for row in mod] + [[mod[y][x] for y in range(n)] for x in range(n)]
    finder_a = [True, False, True, True, True, False, True, False, False, False, False]
    finder_b = list(reversed(finder_a))
    for line in lines:
        run, prev = 0, None
        for v in line:                                  # N1: runs of 5+
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, v
        if run >= 5:
            score += 3 + run - 5
        for i in range(n - 10):                         # N3: finder-like patterns
            seg = line[i:i + 11]
            if seg == finder_a or seg == finder_b:
                score += 40
    for y in range(n - 1):                              # N2: 2x2 blocks
        for x in range(n - 1):
            c = mod[y][x]
            if c == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in mod)                     # N4: balance
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(0, k) * 10
    return score


def encode(text, ecl="M", version=None, mask=None):
    """QR matrix (rows of booleans, True = dark) for `text` (UTF-8, byte mode)."""
    if ecl not in ECC_LEVELS:
        raise ValueError("ecl must be L, M, Q or H")
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    ver = version or choose_version(len(payload), ecl)
    if 4 + _count_bits(ver) + 8 * len(payload) > data_codewords(ver, ecl) * 8:
        raise ValueError(f"data does not fit in version {ver}-{ecl}")
    codewords = _add_ecc_and_interleave(_data_codewords(payload, ver, ecl), ver, ecl)
    best = None
    for m in ([mask] if mask is not None else range(8)):
        mx = _Matrix(ver)
        mx.draw_function_patterns()
        mx.draw_codewords(codewords)
        mx.apply_mask(m)
        mx.draw_format_bits(ecl, m)
        p = penalty(mx.mod)
        if best is None or p < best[0]:
            best = (p, mx.mod)
    return best[1]

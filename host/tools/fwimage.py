#!/usr/bin/env python3
"""Build and sign option A firmware images for USB DFU (format: firmware/core/fwimage.h).

    fwimage.py keygen signing.seed                    # signing.seed (secret) + signing.pub
    fwimage.py sign --key signing.seed --version 0.2.0 adapter.bin adapter-0.2.0.nfw
    fwimage.py info [--pubkey signing.pub] adapter-0.2.0.nfw
    fwimage.py pubkey-c signing.pub fw_pubkey.c       # the key the firmware trusts

Then: dfu-util -d 1209:0000 -a 0 -D adapter-0.2.0.nfw   (see docs/MANAGEMENT.md §6)

Keep the production signing seed offline: anyone holding it can make firmware that
every adapter accepts, which can read out the common network key.
Ed25519 is implemented here (RFC 8032) so the tool needs only the standard library.
"""

import argparse
import hashlib
import os
import re
import struct
import sys

HDR_SIZE = 0x400
SIGNED_LEN = 64
MAGIC = 0x3157464E  # "NFW1"
FORMAT = 1
LOAD_ADDR = 0x60010400  # FW_ACTIVE_LOAD_ADDR in firmware/core/platform.h
SLOT_SIZE = 0x100000


class ImageError(ValueError):
    pass


# ---------------------------------------------------------------- Ed25519 (RFC 8032 §5.1)
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _compress(pt):
    zinv = pow(pt[2], _P - 2, _P)
    x, y = pt[0] * zinv % _P, pt[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(b):
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h(*parts):
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(seed):
    if len(seed) != 32:
        raise ImageError("signing seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed):
    a, _ = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed, msg):
    a, prefix = _expand(seed)
    A = _compress(_mul(a, _G))
    r = _h(prefix, msg) % _L
    R = _compress(_mul(r, _G))
    s = (r + _h(R, A, msg) * a) % _L
    return R + int.to_bytes(s, 32, "little")


def verify(pub, msg, sig):
    if len(pub) != 32 or len(sig) != 64:
        return False
    A, R = _decompress(pub), _decompress(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if A is None or R is None or s >= _L:
        return False
    k = _h(sig[:32], pub, msg) % _L
    return _equal(_mul(s, _G), _add(R, _mul(k, A)))


# ---------------------------------------------------------------- images
def parse_version(text):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ImageError(f"version must be MAJOR.MINOR.PATCH, not {text!r}")
    major, minor, patch = (int(g) for g in m.groups())
    if major > 0xFFFF or minor > 0xFF or patch > 0xFF:
        raise ImageError("version out of range (major <= 65535, minor/patch <= 255)")
    return major << 16 | minor << 8 | patch


def version_str(v):
    return f"{v >> 16}.{v >> 8 & 0xFF}.{v & 0xFF}"


def build(payload, seed, version, load_addr=LOAD_ADDR):
    if not payload or len(payload) > SLOT_SIZE - HDR_SIZE:
        raise ImageError(f"payload must be 1..{SLOT_SIZE - HDR_SIZE} bytes, not {len(payload)}")
    head = struct.pack("<IHHIII12s32s", MAGIC, HDR_SIZE, FORMAT, version, len(payload), load_addr,
                       bytes(12), hashlib.sha256(payload).digest())
    assert len(head) == SIGNED_LEN
    hdr = head + sign(seed, head)
    return hdr + b"\xff" * (HDR_SIZE - len(hdr)) + payload


def inspect(image, pub=None, load_addr=LOAD_ADDR):
    """Return the header fields; with `pub`, also check signature and hash (ImageError)."""
    if len(image) < HDR_SIZE:
        raise ImageError("shorter than the image header")
    magic, hsize, fmt, version, size, addr, _, digest = struct.unpack_from("<IHHIII12s32s", image)
    if magic != MAGIC:
        raise ImageError("not a firmware image")
    if hsize != HDR_SIZE or fmt != FORMAT:
        raise ImageError("unknown image format")
    info = {"version": version_str(version), "size": size, "load_addr": addr, "sha256": digest.hex()}
    if pub is not None:
        if len(image) != HDR_SIZE + size:
            raise ImageError(f"file is {len(image)} bytes, header says {HDR_SIZE + size}")
        if addr != load_addr:
            raise ImageError(f"built for load address {addr:#x}, not {load_addr:#x}")
        if not verify(pub, image[:SIGNED_LEN], image[SIGNED_LEN:2 * SIGNED_LEN]):
            raise ImageError("bad signature (not signed with this key)")
        if hashlib.sha256(image[HDR_SIZE:]).digest() != digest:
            raise ImageError("payload does not match its signed hash")
        info["verified"] = True
    return info


# ---------------------------------------------------------------- key files (hex text)
def read_hex(path, nbytes, what):
    with open(path) as f:
        text = f.read().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{%d}" % (2 * nbytes), text):
        raise ImageError(f"{path}: expected {nbytes} bytes of hex ({what})")
    return bytes.fromhex(text)


def keygen(seed_path):
    seed = os.urandom(32)
    fd = os.open(seed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(seed.hex() + "\n")
    pub_path = os.path.splitext(seed_path)[0] + ".pub"
    with open(pub_path, "w") as f:
        f.write(public_key(seed).hex() + "\n")
    return pub_path


def pubkey_c(pub):
    body = ", ".join(f"0x{b:02x}" for b in pub)
    return ("/* Generated by host/tools/fwimage.py pubkey-c: the Ed25519 key firmware images\n"
            " * must be signed with. */\n#include <stdint.h>\n\n"
            f"const uint8_t fw_signing_pubkey[32] = {{{body}}};\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="new signing key: SEED (secret, mode 600) and SEED.pub")
    k.add_argument("seed")
    s = sub.add_parser("sign", help="wrap a binary into a signed image")
    s.add_argument("--key", required=True, help="signing seed file (hex)")
    s.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH, newer than the units' firmware")
    s.add_argument("--load-addr", type=lambda v: int(v, 0), default=LOAD_ADDR, help=argparse.SUPPRESS)
    s.add_argument("input")
    s.add_argument("output")
    i = sub.add_parser("info", help="show (and with --pubkey verify) an image")
    i.add_argument("--pubkey", help="public key file (hex) to verify against")
    i.add_argument("image")
    c = sub.add_parser("pubkey-c", help="C source with the public key, for the firmware build")
    c.add_argument("pubkey")
    c.add_argument("output")
    a = p.parse_args(argv)
    try:
        if a.cmd == "keygen":
            print(f"secret seed: {a.seed} (keep offline)\npublic key:  {keygen(a.seed)}")
        elif a.cmd == "sign":
            seed = read_hex(a.key, 32, "signing seed")
            with open(a.input, "rb") as f:
                image = build(f.read(), seed, parse_version(a.version), a.load_addr)
            with open(a.output, "wb") as f:
                f.write(image)
            print(f"{a.output}: version {a.version}, payload {len(image) - HDR_SIZE} bytes, "
                  f"key {public_key(seed).hex()[:16]}")
        elif a.cmd == "info":
            pub = read_hex(a.pubkey, 32, "public key") if a.pubkey else None
            with open(a.image, "rb") as f:
                info = inspect(f.read(), pub)
            for key, value in info.items():
                print(f"{key:10} {value:#x}" if key == "load_addr" else f"{key:10} {value}")
        elif a.cmd == "pubkey-c":
            pub = read_hex(a.pubkey, 32, "public key")
            with open(a.output, "w") as f:
                f.write(pubkey_c(pub))
    except (ImageError, OSError) as e:
        print(f"fwimage: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

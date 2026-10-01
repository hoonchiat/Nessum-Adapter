#!/usr/bin/env python3
"""Tests for fwimage.py (signed DFU images). Run: python3 -m unittest -v test_fwimage

Ed25519 is checked against RFC 8032 vectors, against OpenSSL (when the `openssl`
command is available), and against the firmware's own C verifier (firmware/build/fwtool).
"""

import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fwimage  # noqa: E402

FIRMWARE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "firmware"))
FWTOOL = os.path.join(FIRMWARE, "build", "fwtool")
SEED = bytes(range(32))

# RFC 8032 §7.1, tests 1 and 2: (secret, public, message, signature)
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


def build_fwtool():
    if not shutil.which("make") or not (shutil.which("cc") or shutil.which("gcc")):
        return "no C compiler / make"
    r = subprocess.run(["make", "-C", FIRMWARE, "fwtool"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("fwtool build failed:\n" + r.stdout + r.stderr)
    return None


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for sk, pk, msg, sig in VECTORS:
            seed, msg, sig = bytes.fromhex(sk), bytes.fromhex(msg), bytes.fromhex(sig)
            self.assertEqual(fwimage.public_key(seed).hex(), pk)
            self.assertEqual(fwimage.sign(seed, msg), sig)
            self.assertTrue(fwimage.verify(bytes.fromhex(pk), msg, sig))
            self.assertFalse(fwimage.verify(bytes.fromhex(pk), msg + b"x", sig))

    def test_against_openssl(self):
        """An independent implementation: OpenSSL signs with the same seed."""
        if not shutil.which("openssl"):
            self.skipTest("openssl not installed")
        pkcs8_prefix = bytes.fromhex("302e020100300506032b657004220420")   # Ed25519 private key, DER
        with tempfile.TemporaryDirectory() as d:
            for n in range(3):
                seed, msg = os.urandom(32), os.urandom(64)
                key, data = os.path.join(d, "k.der"), os.path.join(d, "m")
                with open(key, "wb") as f:
                    f.write(pkcs8_prefix + seed)
                with open(data, "wb") as f:
                    f.write(msg)
                r = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER", "-inkey", key,
                                    "-in", data], capture_output=True)
                if r.returncode:
                    self.skipTest("openssl without raw Ed25519 signing: " + r.stderr.decode(errors="replace"))
                self.assertEqual(fwimage.sign(seed, msg), r.stdout)
                self.assertTrue(fwimage.verify(fwimage.public_key(seed), msg, r.stdout))


class ImageTest(unittest.TestCase):
    def setUp(self):
        self.pub = fwimage.public_key(SEED)
        self.payload = os.urandom(5000)
        self.image = fwimage.build(self.payload, SEED, fwimage.parse_version("0.2.0"))

    def test_roundtrip(self):
        info = fwimage.inspect(self.image, self.pub)
        self.assertEqual((info["version"], info["size"], info["load_addr"]), ("0.2.0", 5000, fwimage.LOAD_ADDR))
        self.assertTrue(info["verified"])
        self.assertEqual(len(self.image), fwimage.HDR_SIZE + 5000)

    def test_rejections(self):
        bad = bytearray(self.image)
        bad[-1] ^= 1
        with self.assertRaisesRegex(fwimage.ImageError, "hash"):
            fwimage.inspect(bytes(bad), self.pub)
        bad = bytearray(self.image)
        bad[8] ^= 1   # version
        with self.assertRaisesRegex(fwimage.ImageError, "signature"):
            fwimage.inspect(bytes(bad), self.pub)
        with self.assertRaisesRegex(fwimage.ImageError, "signature"):
            fwimage.inspect(self.image, fwimage.public_key(bytes(32)))
        with self.assertRaisesRegex(fwimage.ImageError, "header says"):
            fwimage.inspect(self.image + b"\0", self.pub)
        with self.assertRaisesRegex(fwimage.ImageError, "not a firmware image"):
            fwimage.inspect(b"\xff" * 2048)
        for v in ("1.2", "1.2.3.4", "x.y.z", "1.256.0", "65536.0.0"):
            with self.subTest(v=v), self.assertRaises(fwimage.ImageError):
                fwimage.parse_version(v)
        with self.assertRaises(fwimage.ImageError):
            fwimage.build(b"", SEED, 1)
        with self.assertRaises(fwimage.ImageError):
            fwimage.build(b"x" * (fwimage.SLOT_SIZE - fwimage.HDR_SIZE + 1), SEED, 1)


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, name):
        return os.path.join(self.dir, name)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fwimage.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_keygen_sign_info(self):
        seed = self.path("prod.seed")
        self.assertEqual(self.cli("keygen", seed)[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(seed).st_mode), 0o600)
        self.assertNotEqual(self.cli("keygen", seed)[0], 0)   # never overwrites a key
        with open(self.path("app.bin"), "wb") as f:
            f.write(os.urandom(3000))
        rc, out, err = self.cli("sign", "--key", seed, "--version", "1.4.2", self.path("app.bin"), self.path("a.nfw"))
        self.assertEqual(rc, 0, err)
        rc, out, _ = self.cli("info", "--pubkey", self.path("prod.pub"), self.path("a.nfw"))
        self.assertEqual(rc, 0)
        self.assertIn("version    1.4.2", out)
        self.assertIn("verified   True", out)
        self.assertEqual(self.cli("pubkey-c", self.path("prod.pub"), self.path("k.c"))[0], 0)
        with open(self.path("k.c")) as f:
            self.assertIn("const uint8_t fw_signing_pubkey[32] = {0x", f.read())
        other = self.path("other.seed")
        self.cli("keygen", other)
        rc, _, err = self.cli("info", "--pubkey", self.path("other.pub"), self.path("a.nfw"))
        self.assertEqual(rc, 1)
        self.assertIn("bad signature", err)


SKIP_C = build_fwtool()


@unittest.skipIf(SKIP_C, SKIP_C)
class FirmwareVerifierTest(unittest.TestCase):
    """The firmware's C verifier accepts exactly what this tool signs."""

    def run_fwtool(self, image, pub):
        with tempfile.NamedTemporaryFile(suffix=".nfw") as f:
            f.write(image)
            f.flush()
            return subprocess.run([FWTOOL, "verify", pub.hex(), f.name], capture_output=True, text=True)

    def test_interop(self):
        for size in (1, 4096, 70001):
            seed = os.urandom(32)
            image = fwimage.build(os.urandom(size), seed, fwimage.parse_version("3.1.4"))
            r = self.run_fwtool(image, fwimage.public_key(seed))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip(), f"ok version=3.1.4 size={size}")
            bad = bytearray(image)
            bad[-1] ^= 0x80
            r = self.run_fwtool(bytes(bad), fwimage.public_key(seed))
            self.assertEqual((r.returncode, r.stdout.strip()), (1, "payload does not match its signed hash"))
            r = self.run_fwtool(image, fwimage.public_key(os.urandom(32)))
            self.assertEqual((r.returncode, r.stdout.strip()), (1, "bad signature"))


if __name__ == "__main__":
    unittest.main()

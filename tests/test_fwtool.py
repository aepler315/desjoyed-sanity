#!/usr/bin/env python3
"""Tests for fwtool.py: firmware zip inspection and modification."""
import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fwtool


def make_test_zip(entries: dict[str, bytes],
                  updater_script: bytes | None = None) -> bytes:
    """Create a zip in memory with the given entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        if updater_script is not None:
            zf.writestr(
                "META-INF/com/google/android/updater-script",
                updater_script)
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sign_zip_fake(data: bytes) -> bytes:
    """Append a fake Android v1 whole-file signature to a zip.

    This creates a structurally valid signature block (the format the
    parser looks for) but with a dummy PKCS#7 blob.
    """
    eocd_pos = fwtool.find_eocd(data)
    assert eocd_pos is not None

    fake_p7 = b"\x30\x00" * 20

    comment = fwtool.SIGNEDBY + fake_p7
    footer = struct.pack("<H", len(fake_p7))
    footer += struct.pack("<I", eocd_pos)
    comment += footer
    comment_len = len(comment)

    new_data = bytearray(data)
    old_comment_len_pos = eocd_pos + 20
    struct.pack_into("<H", new_data, old_comment_len_pos, comment_len)
    new_data += comment

    return bytes(new_data)


UPDATER_SCRIPT = b"""\
assert(getprop("ro.product.device") == "msmnile" ||
       getprop("ro.build.product") == "msmnile");
assert(less_than_int("1700000000", getprop("ro.build.date.utc")));
show_progress(0.100000, 0);
"""


class TestFindEocd(unittest.TestCase):

    def test_finds_eocd_in_valid_zip(self):
        data = make_test_zip({"test.txt": b"hello"})
        pos = fwtool.find_eocd(data)
        self.assertIsNotNone(pos)
        self.assertEqual(data[pos:pos + 4], fwtool.EOCD_MAGIC)

    def test_none_for_garbage(self):
        pos = fwtool.find_eocd(b"not a zip at all")
        self.assertIsNone(pos)


class TestParseSignature(unittest.TestCase):

    def test_unsigned(self):
        data = make_test_zip({"test.txt": b"hello"})
        sig = fwtool.parse_signature(data)
        self.assertFalse(sig["signed"])

    def test_signed(self):
        data = make_test_zip({"test.txt": b"hello"})
        signed = sign_zip_fake(data)
        sig = fwtool.parse_signature(signed)
        self.assertTrue(sig["signed"])
        self.assertEqual(sig["signer"], "SignApk")
        self.assertEqual(sig["p7_size"], 40)


class TestCheckMetadata(unittest.TestCase):

    def test_with_updater_script(self):
        data = make_test_zip({}, updater_script=UPDATER_SCRIPT)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(data)
            f.flush()
            zf = zipfile.ZipFile(f.name)
            meta = fwtool.check_metadata(zf)
            zf.close()
            os.unlink(f.name)
        self.assertTrue(meta["has_updater_script"])
        self.assertEqual(meta["post_timestamp"], 1700000000)
        self.assertEqual(meta["pre_device"], "msmnile")

    def test_without_updater_script(self):
        data = make_test_zip({"boot.img": b"\x00" * 100})
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(data)
            f.flush()
            zf = zipfile.ZipFile(f.name)
            meta = fwtool.check_metadata(zf)
            zf.close()
            os.unlink(f.name)
        self.assertFalse(meta["has_updater_script"])


class TestInspect(unittest.TestCase):

    def test_inspect_basic(self):
        entries = {
            "owner_apk/Bloat.apk": b"PK\x03\x04fake",
            "config_file/policy.db": b"sqlite3",
            "boot.img": b"ANDROID!" + b"\x00" * 100,
        }
        data = make_test_zip(entries, updater_script=UPDATER_SCRIPT)
        signed = sign_zip_fake(data)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(signed)
            f.flush()
            args = type("Args", (), {
                "zip": f.name, "list_all": False
            })()
            fwtool.cmd_inspect(args)
            os.unlink(f.name)

    def test_inspect_list_all(self):
        data = make_test_zip({"a.txt": b"a", "b.txt": b"b"})
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(data)
            f.flush()
            args = type("Args", (), {
                "zip": f.name, "list_all": True
            })()
            fwtool.cmd_inspect(args)
            os.unlink(f.name)


class TestStrip(unittest.TestCase):

    def test_strip_removes_entries(self):
        entries = {
            "owner_apk/Bloat.apk": b"bloat",
            "owner_apk/Good.apk": b"good",
            "boot.img": b"ANDROID!",
        }
        data = make_test_zip(entries)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as fin:
            fin.write(data)
            fin.flush()
            with tempfile.NamedTemporaryFile(
                    suffix=".zip", delete=False) as fout:
                args = type("Args", (), {
                    "zip": fin.name,
                    "output": fout.name,
                    "remove": [r"owner_apk/Bloat"],
                })()
                fwtool.cmd_strip(args)
                zf = zipfile.ZipFile(fout.name)
                names = zf.namelist()
                zf.close()
                os.unlink(fout.name)
            os.unlink(fin.name)

        self.assertNotIn("owner_apk/Bloat.apk", names)
        self.assertIn("owner_apk/Good.apk", names)
        self.assertIn("boot.img", names)

    def test_strip_all_owner_apk(self):
        entries = {
            "owner_apk/A.apk": b"a",
            "owner_apk/B.apk": b"b",
            "system.img": b"sys",
        }
        data = make_test_zip(entries)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as fin:
            fin.write(data)
            fin.flush()
            with tempfile.NamedTemporaryFile(
                    suffix=".zip", delete=False) as fout:
                args = type("Args", (), {
                    "zip": fin.name,
                    "output": fout.name,
                    "remove": [r"owner_apk/.*"],
                })()
                fwtool.cmd_strip(args)
                zf = zipfile.ZipFile(fout.name)
                names = zf.namelist()
                zf.close()
                os.unlink(fout.name)
            os.unlink(fin.name)

        owner_apks = [n for n in names if n.startswith("owner_apk/")]
        self.assertEqual(len(owner_apks), 0)
        self.assertIn("system.img", names)


class TestSigcheck(unittest.TestCase):

    def test_sigcheck_unsigned(self):
        data = make_test_zip({"test.txt": b"hello"})
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(data)
            f.flush()
            args = type("Args", (), {"zip": f.name})()
            with self.assertRaises(SystemExit):
                fwtool.cmd_sigcheck(args)
            os.unlink(f.name)

    def test_sigcheck_signed(self):
        data = make_test_zip({"test.txt": b"hello"})
        signed = sign_zip_fake(data)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(signed)
            f.flush()
            args = type("Args", (), {"zip": f.name})()
            fwtool.cmd_sigcheck(args)
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()

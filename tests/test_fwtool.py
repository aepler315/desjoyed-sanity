#!/usr/bin/env python3
"""Tests for firmware inspection, signature detection, and safe ZIP rewriting."""
import contextlib
import io
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

import fwtool


def make_test_zip(entries: dict[str, bytes], updater_script=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        if updater_script is not None:
            zf.writestr("META-INF/com/google/android/updater-script", updater_script)
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sign_zip_fake(data: bytes) -> bytes:
    """AOSP footer layout with fake signature bytes; deliberately NOT verified."""
    eocd_pos = data.rfind(b"PK\x05\x06")
    fake_p7 = b"\x30\x00" * 20
    comment = b"signed by SignApk\x00" + fake_p7
    # Signature start is its distance from EOF, including the six-byte footer.
    comment += struct.pack("<H2sH", 46, b"\xff\xff", len(comment) + 6)
    new_data = bytearray(data)
    struct.pack_into("<H", new_data, eocd_pos + 20, len(comment))
    return bytes(new_data) + comment


UPDATER_SCRIPT = b'''assert(getprop("ro.product.device") == "msmnile");
assert(less_than_int("1700000000", getprop("ro.build.date.utc")));
'''


class TempCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def write_zip(self, data, name="input.zip"):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def command(self, fn, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            fn(SimpleNamespace(**kwargs))
        return output.getvalue()


class TestFindEocd(unittest.TestCase):
    def test_finds_eocd_in_valid_zip(self):
        data = make_test_zip({"test.txt": b"hello"})
        self.assertEqual(data[fwtool.find_eocd(data):][:4], b"PK\x05\x06")

    def test_none_for_garbage(self):
        self.assertIsNone(fwtool.find_eocd(b"not a zip at all"))


class TestParseSignature(unittest.TestCase):
    def test_unsigned(self):
        self.assertFalse(fwtool.parse_signature(make_test_zip({}))["signed"])

    def test_signed_structure_never_means_verified(self):
        sig = fwtool.parse_signature(sign_zip_fake(make_test_zip({})))
        self.assertTrue(sig["signed"])
        self.assertEqual(sig["p7_size"], 40)
        self.assertFalse(sig["verified"])
        self.assertEqual(sig["cert_sha256"], "")

    def test_footer_magic_length_and_offset_required(self):
        original = sign_zip_fake(make_test_zip({}))
        for offset, replacement in ((-4, b"\x00\x00"), (-2, b"\x01\x00"),
                                    (-6, b"\xff\xff"), (-6, b"\x06\x00")):
            data = bytearray(original)
            data[len(data) + offset:len(data) + offset + 2] = replacement
            with self.subTest(offset=offset, replacement=replacement):
                self.assertFalse(fwtool.parse_signature(data)["signed"])

    def test_comment_marker_alone_is_not_a_signature(self):
        buf = io.BytesIO(make_test_zip({}))
        with zipfile.ZipFile(buf, "a") as zf:
            zf.comment = b"signed by SignApk\x00"
        self.assertFalse(fwtool.parse_signature(buf.getvalue())["signed"])


class TestCheckMetadata(TempCase):
    def test_with_updater_script(self):
        path = self.write_zip(make_test_zip({}, UPDATER_SCRIPT))
        with zipfile.ZipFile(path) as zf:
            meta = fwtool.check_metadata(zf)
        self.assertTrue(meta["has_updater_script"])
        self.assertEqual(meta["post_timestamp"], 1700000000)
        self.assertEqual(meta["pre_device"], "msmnile")

    def test_without_updater_script(self):
        path = self.write_zip(make_test_zip({"boot.img": bytes(100)}))
        with zipfile.ZipFile(path) as zf:
            self.assertFalse(fwtool.check_metadata(zf)["has_updater_script"])


class TestInspect(TempCase):
    def test_inspect_basic(self):
        data = make_test_zip({"owner_apk/Bloat.apk": b"fake",
                              "config_file/policy.db": b"sqlite3",
                              "boot.img": b"ANDROID!"}, UPDATER_SCRIPT)
        path = self.write_zip(sign_zip_fake(data))
        output = self.command(fwtool.cmd_inspect, zip=path, list_all=False)
        self.assertIn("owner_apk/Bloat.apk", output)
        self.assertIn("not verified", output.lower())
        self.assertNotIn("[OK] Signed by", output)

    def test_inspect_list_all(self):
        path = self.write_zip(make_test_zip({"a.txt": b"a", "b.txt": b"b"}))
        output = self.command(fwtool.cmd_inspect, zip=path, list_all=True)
        self.assertIn("a.txt", output)
        self.assertIn("b.txt", output)


class TestStrip(TempCase):
    def test_strip_removes_entries_preserves_source(self):
        data = make_test_zip({"owner_apk/Bloat.apk": b"bloat",
                              "owner_apk/Good.apk": b"good", "boot.img": b"ANDROID!"})
        source = self.write_zip(data)
        output = self.folder / "output.zip"
        self.command(fwtool.cmd_strip, zip=source, output=output,
                     remove=[r"owner_apk/Bloat"])
        self.assertEqual(source.read_bytes(), data)
        with zipfile.ZipFile(output) as zf:
            self.assertEqual(set(zf.namelist()), {"owner_apk/Good.apk", "boot.img"})
            self.assertEqual(zf.read("boot.img"), b"ANDROID!")

    def test_strip_all_owner_apk(self):
        source = self.write_zip(make_test_zip({"owner_apk/A.apk": b"a",
                                               "owner_apk/B.apk": b"b", "system.img": b"sys"}))
        output = self.folder / "output.zip"
        self.command(fwtool.cmd_strip, zip=source, output=output, remove=[r"owner_apk/.*"])
        with zipfile.ZipFile(output) as zf:
            self.assertEqual(zf.namelist(), ["system.img"])

    def assert_refused_preserves_source(self, source, output):
        before = source.read_bytes()
        with self.assertRaises(SystemExit) as error:
            self.command(fwtool.cmd_strip, zip=source, output=output, remove=["bloat"])
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(source.read_bytes(), before)

    def test_same_file_refused_without_truncation(self):
        source = self.write_zip(make_test_zip({"bloat": b"x", "boot.img": b"kernel"}))
        self.assert_refused_preserves_source(source, source)

    def test_hardlink_alias_refused_without_truncation(self):
        source = self.write_zip(make_test_zip({"bloat": b"x", "boot.img": b"kernel"}))
        alias = self.folder / "alias.zip"
        os.link(source, alias)
        self.assert_refused_preserves_source(source, alias)

    def test_existing_output_refused_and_preserved(self):
        source = self.write_zip(make_test_zip({"bloat": b"x", "boot.img": b"kernel"}))
        output = self.write_zip(b"irreplaceable", "output.zip")
        self.assert_refused_preserves_source(source, output)
        self.assertEqual(output.read_bytes(), b"irreplaceable")


class TestSigcheck(TempCase):
    def test_sigcheck_unsigned(self):
        path = self.write_zip(make_test_zip({}))
        with self.assertRaises(SystemExit):
            self.command(fwtool.cmd_sigcheck, zip=path)

    def test_sigcheck_detected_signature_reports_unverified(self):
        path = self.write_zip(sign_zip_fake(make_test_zip({})))
        output = self.command(fwtool.cmd_sigcheck, zip=path)
        self.assertIn("not verified", output.lower())
        self.assertNotIn("Signed by:", output)


if __name__ == "__main__":
    unittest.main()

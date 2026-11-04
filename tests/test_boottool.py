"""Regression tests for the read-only bootloader workbench."""
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import boottool
from sanity import Adb, AdbError
from tests.test_bootimage import boot_fixture, vendor_fixture


PROPS = """[ro.product.model]: [Example Head Unit]
[ro.product.device]: [example]
[ro.build.fingerprint]: [vendor/example/build:13/test:userdebug/test-keys]
[ro.boot.flash.locked]: [0]
[ro.boot.vbmeta.device_state]: [unlocked]
[ro.boot.verifiedbootstate]: [orange]
[ro.boot.slot_suffix]: [_a]
"""


def stock_image():
    data = bytearray(12288)
    data[:8] = b"ANDROID!"
    struct.pack_into("<III", data, 8, 4, 4, 0)
    struct.pack_into("<I", data, 20, 1580)
    struct.pack_into("<I", data, 40, 3)
    data[4096:4100] = b"KERN"
    data[8192:8196] = b"RAMD"
    return bytes(data)


def device_report():
    return {
        "schema_version": 1, "kind": "device_probe", "adb_serial": "TEST001",
        "captured_at": "2026-11-03T12:00:00+00:00",
        "properties": {"ro.product.device": "example", "ro.boot.flash.locked": "0"},
        "partitions": ["boot_a", "boot_b", "vbmeta_a", "vbmeta_b"],
        "errors": [],
    }


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.devices = "List of devices attached\nTEST001 device product:example\n"
        self.props = PROPS
        self.fail_partitions = False

    def run_adb(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd[-2:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(cmd, 0, self.devices, "")
        self.assertEqual(cmd[:4], ["adb", "-s", "TEST001", "shell"])
        responses = {
            "getprop": self.props,
            "ls -1 /dev/block/by-name": "boot_a\nboot_b\nvbmeta_a\nvbmeta_b\n",
            "ls -1 /dev/block/bootdevice/by-name": "boot_a\nboot_b\n",
        }
        self.assertIn(cmd[4], responses, "Probe emitted an unexpected device command")
        if self.fail_partitions and cmd[4].startswith("ls "):
            return subprocess.CompletedProcess(cmd, 1, "", "Permission denied")
        return subprocess.CompletedProcess(cmd, 0, responses[cmd[4]], "")

    def probe(self, serial=None):
        with patch("sanity.subprocess.run", side_effect=self.run_adb):
            return boottool.collect_device(Adb(serial=serial, binary="adb"))

    def test_probe_pins_serial_and_uses_only_read_commands(self):
        result = self.probe()
        self.assertEqual(result["adb_serial"], "TEST001")
        self.assertEqual(result["unlock_state"], "reported_unlocked")
        self.assertEqual(result["active_slot"], "a")
        self.assertEqual(result["partitions"], ["boot_a", "boot_b", "vbmeta_a", "vbmeta_b"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.commands), 3)

    def test_ambiguous_devices_refused_before_shell(self):
        self.devices += "TEST002 device\n"
        with self.assertRaisesRegex(AdbError, "serial"):
            self.probe()
        self.assertEqual(len(self.commands), 1)

    def test_explicit_serial_selects_from_multiple(self):
        self.devices += "TEST002 device\n"
        self.assertEqual(self.probe("TEST001")["adb_serial"], "TEST001")

    def test_no_device_offline_and_unauthorized_fail(self):
        for line in ["", "TEST001 offline\n", "TEST001 unauthorized\n"]:
            with self.subTest(line=line):
                self.devices = "List of devices attached\n" + line
                with self.assertRaises(AdbError):
                    self.probe()

    def test_conflicting_unlock_properties_are_not_confirmed(self):
        self.props = PROPS.replace("[ro.boot.flash.locked]: [0]", "[ro.boot.flash.locked]: [1]")
        self.assertEqual(self.probe()["unlock_state"], "conflicting")

    def test_missing_properties_and_partition_denial_are_explicit(self):
        self.props = ""
        self.fail_partitions = True
        result = self.probe()
        self.assertEqual(result["unlock_state"], "unknown")
        self.assertIsNone(result["active_slot"])
        self.assertEqual(result["partitions"], [])
        self.assertTrue(result["errors"])

    def test_partition_fallback_preserves_alternate_layout(self):
        original = self.run_adb
        def fallback(cmd, **kwargs):
            if cmd[-1] == "ls -1 /dev/block/by-name":
                self.commands.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "No such file")
            return original(cmd, **kwargs)
        with patch("sanity.subprocess.run", side_effect=fallback):
            result = boottool.collect_device(Adb(binary="adb"))
        self.assertEqual(result["partitions"], ["boot_a", "boot_b"])
        self.assertEqual(result["errors"], [])


class TestPrepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "stock.img"
        self.source.write_bytes(stock_image())
        self.output = self.root / "prepared"

    def prepare(self, **kwargs):
        return boottool.prepare_workspace(kwargs.get("device", device_report()),
                                          self.source, kwargs.get("partition", "boot"),
                                          self.output)

    def test_preserves_exact_stock_bytes_and_records_unknowns(self):
        result = self.prepare()
        copied = self.output / "stock" / "boot.img"
        self.assertEqual(copied.read_bytes(), stock_image())
        self.assertEqual(self.source.read_bytes(), stock_image())
        self.assertEqual(result["image"]["sha256"], hashlib.sha256(stock_image()).hexdigest())
        self.assertEqual(result["image"]["relative_path"], "stock/boot.img")
        self.assertEqual(result["status"], "needs_device_verification")
        self.assertIn("firmware_match_unverified", result["blockers"])
        self.assertIn("recovery_path_unverified", result["blockers"])
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), result)
        self.assertTrue((self.output / "NEXT_STEPS.md").is_file())

    def test_refuses_existing_directory_without_changing_it(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_rejects_invalid_partition_before_writing(self):
        for name in ["boot_a", "../boot", "boot; reboot", "vbmeta", ""]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(partition=name)
            self.assertFalse(self.output.exists())

    def test_wrong_image_kind_for_vendor_boot_refused(self):
        with self.assertRaisesRegex(ValueError, "vendor_boot"):
            self.prepare(partition="vendor_boot")
        self.assertFalse(self.output.exists())

    def test_init_boot_with_kernel_is_refused(self):
        with self.assertRaisesRegex(ValueError, "init_boot"):
            self.prepare(partition="init_boot")

    def test_unknown_partition_presence_stays_blocked(self):
        report = device_report()
        report["partitions"] = []
        self.assertIn("partition_presence_unverified", self.prepare(device=report)["blockers"])

    def test_invalid_report_and_malformed_image_leave_no_output(self):
        with self.assertRaises(ValueError):
            self.prepare(device={})
        self.source.write_bytes(b"not a boot image")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_failed_copy_leaves_no_published_workspace(self):
        with patch("boottool.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_preserves_kernel_less_init_boot(self):
        data = boot_fixture(4, kernel=b"")
        self.source.write_bytes(data)
        result = self.prepare(partition="init_boot")
        self.assertEqual(result["image"]["kernel_size"], 0)
        self.assertEqual((self.output / "stock/init_boot.img").read_bytes(), data)

    def test_preserves_vendor_boot_without_treating_it_as_generic_boot(self):
        data = vendor_fixture()
        self.source.write_bytes(data)
        result = self.prepare(partition="vendor_boot")
        self.assertEqual(result["image"]["kind"], "vendor_boot")
        self.assertEqual((self.output / "stock/vendor_boot.img").read_bytes(), data)

    def test_source_change_during_copy_rejects_workspace(self):
        def changed_copy(source, target):
            data = bytearray(source.read_bytes())
            data[4096:4100] = b"DIFF"
            Path(target).write_bytes(data)
        with patch("boottool.shutil.copyfile", side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.prepare()
        self.assertFalse(self.output.exists())


class TestCLI(unittest.TestCase):
    def test_probe_json_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "device.json"
            out.write_text("existing")
            with patch("boottool.collect_device", return_value=device_report()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(boottool.main(["probe", "-o", str(out)]), 1)
            self.assertEqual(out.read_text(), "existing")

    def test_partial_probe_writes_report_but_returns_nonzero(self):
        report = device_report()
        report["errors"] = [{"section": "partitions", "error": "permission denied"}]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "device.json"
            with patch("boottool.collect_device", return_value=report), patch("sys.stdout", io.StringIO()):
                self.assertEqual(boottool.main(["probe", "-o", str(out)]), 2)
            self.assertEqual(json.loads(out.read_text())["errors"], report["errors"])

    def test_image_json_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "boot.img"
            source.write_bytes(stock_image())
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(boottool.main(["image", str(source), "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue())["ramdisk_size"], 4)


if __name__ == "__main__":
    unittest.main()

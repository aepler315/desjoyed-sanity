#!/usr/bin/env python3
"""Tests for sanity.py: list parsing, classification, ADB integration."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sanity

FAKE_ADB = str(ROOT / "tests" / "fake_adb.py")
LISTS_DIR = ROOT / "lists"


def make_adb(**kwargs):
    return sanity.Adb(binary=sys.executable, serial=None,
                      **kwargs)


class FakeAdb(sanity.Adb):
    """Adb subclass that routes through fake_adb.py."""

    def __init__(self):
        super().__init__(binary=sys.executable, serial=None)
        self._base = [sys.executable, FAKE_ADB]


class TestListParsing(unittest.TestCase):

    def test_never_loads(self):
        entries = sanity.parse_regex_list(LISTS_DIR / "never.txt")
        self.assertTrue(len(entries) > 10)
        patterns = [p.pattern for p, _ in entries]
        self.assertIn("^android$", patterns)

    def test_guard_loads(self):
        entries = sanity.parse_regex_list(LISTS_DIR / "guard.txt")
        self.assertTrue(len(entries) > 5)

    def test_tier_list_known_bad(self):
        tl = sanity.parse_tier_list(LISTS_DIR / "known-bad.txt")
        self.assertEqual(tl.tier, "known-bad")
        self.assertEqual(tl.default, "on")
        names = [e.package for e in tl.entries]
        self.assertIn("com.adups.fota", names)
        self.assertIn("com.tw.jar1", names)

    def test_tier_list_google(self):
        tl = sanity.parse_tier_list(LISTS_DIR / "google.txt")
        self.assertEqual(tl.tier, "google")
        self.assertEqual(tl.default, "off")
        names = [e.package for e in tl.entries]
        self.assertIn("com.google.android.gms", names)

    def test_all_lists_load(self):
        lists = sanity.load_all_lists(LISTS_DIR)
        self.assertIn("never", lists)
        self.assertIn("guard", lists)
        self.assertIn("tiers", lists)
        self.assertIn("known-bad", lists["tiers"])
        self.assertIn("aosp", lists["tiers"])
        self.assertIn("google", lists["tiers"])


class TestClassification(unittest.TestCase):

    def setUp(self):
        self.lists = sanity.load_all_lists(LISTS_DIR)

    def test_android_is_never(self):
        r = sanity.classify("android", self.lists)
        self.assertEqual(r["action"], "never")

    def test_systemui_is_never(self):
        r = sanity.classify("com.android.systemui", self.lists)
        self.assertEqual(r["action"], "never")

    def test_syu_ms_is_never(self):
        r = sanity.classify("com.syu.ms", self.lists)
        self.assertEqual(r["action"], "never")

    def test_syu_air_is_never(self):
        r = sanity.classify("com.syu.air", self.lists)
        self.assertEqual(r["action"], "never")

    def test_known_bad_is_disable(self):
        r = sanity.classify("com.adups.fota", self.lists)
        self.assertEqual(r["action"], "disable")
        self.assertEqual(r["tier"], "known-bad")

    def test_aosp_junk_is_disable(self):
        r = sanity.classify("com.android.egg", self.lists)
        self.assertEqual(r["action"], "disable")
        self.assertEqual(r["tier"], "aosp")

    def test_google_junk_is_disable(self):
        r = sanity.classify("com.google.android.feedback", self.lists)
        self.assertEqual(r["action"], "disable")
        self.assertEqual(r["tier"], "google-junk")

    def test_google_is_skip(self):
        r = sanity.classify("com.google.android.gms", self.lists)
        self.assertEqual(r["action"], "skip")
        self.assertEqual(r["tier"], "google")

    def test_projection_is_skip(self):
        r = sanity.classify("com.syu.carlink", self.lists)
        self.assertEqual(r["action"], "skip")
        self.assertEqual(r["tier"], "projection")

    def test_unknown_syu_is_guard(self):
        r = sanity.classify("com.syu.somethingelse", self.lists)
        self.assertEqual(r["action"], "guard")

    def test_random_app_is_unknown(self):
        r = sanity.classify("org.fdroid.fdroid", self.lists)
        self.assertEqual(r["action"], "unknown")

    def test_never_overrides_tier(self):
        r = sanity.classify("com.android.settings", self.lists)
        self.assertEqual(r["action"], "never")

    def test_tier_overrides_guard(self):
        r = sanity.classify("com.syu.radio", self.lists)
        self.assertNotEqual(r["action"], "guard")
        self.assertEqual(r["tier"], "vendor")

    def test_mcu_pattern_catches_variants(self):
        for name in ["com.example.mcu", "com.example.mcu.service",
                      "com.example.mcuservice", "mcu.test"]:
            r = sanity.classify(name, self.lists)
            self.assertEqual(r["action"], "never",
                             f"{name} should be 'never' but got {r}")

    def test_qzd_mcu_is_never(self):
        for name in ["com.car.service", "com.car.service.platform",
                      "com.car.uiservice"]:
            r = sanity.classify(name, self.lists)
            self.assertEqual(r["action"], "never",
                             f"{name} should be 'never' but got {r}")

    def test_qzd_vendor_tier(self):
        r = sanity.classify("com.car.radio", self.lists)
        self.assertEqual(r["tier"], "vendor")

    def test_qzd_vehicle_tier(self):
        r = sanity.classify("com.car.canbus", self.lists)
        self.assertEqual(r["tier"], "vehicle")

    def test_unknown_qzd_is_guard(self):
        r = sanity.classify("com.car.somethingelse", self.lists)
        self.assertEqual(r["action"], "guard")


class TestAdbWrapper(unittest.TestCase):

    def setUp(self):
        self.adb = FakeAdb()

    def test_getprop(self):
        props = self.adb.getprop()
        self.assertEqual(props["ro.product.model"], "JY-UTS18N12")
        self.assertEqual(props["ro.boot.verifiedbootstate"], "orange")
        self.assertEqual(props["ro.build.tags"], "test-keys")

    def test_pm_list(self):
        pkgs = self.adb.pm_list()
        names = [p["name"] for p in pkgs]
        self.assertIn("com.android.egg", names)
        self.assertIn("com.syu.ms", names)
        self.assertIn("com.google.android.gms", names)

    def test_pm_disabled(self):
        disabled = self.adb.pm_disabled()
        self.assertIn("com.android.egg", disabled)

    def test_device_id(self):
        dev_id = self.adb.device_id()
        self.assertEqual(len(dev_id), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in dev_id))

    def test_pm_disable(self):
        result = self.adb.pm_disable("com.android.egg")
        self.assertIn("disabled", result)

    def test_pm_enable(self):
        result = self.adb.pm_enable("com.android.egg")
        self.assertIn("enabled", result)


class TestRecon(unittest.TestCase):

    def test_recon_runs(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        args = type("Args", (), {"json": False})()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_recon(adb, lists, args)

    def test_recon_json(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        args = type("Args", (), {"json": True})()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_recon(adb, lists, args)


class TestTriage(unittest.TestCase):

    def test_triage_generates_plan(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = os.path.join(tmpdir, "plan.txt")
            args = type("Args", (), {
                "output": plan_path, "quiet": False
            })()
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_triage(adb, lists, args)
            self.assertTrue(os.path.isfile(plan_path))
            content = Path(plan_path).read_text()
            self.assertIn("known-bad", content)
            self.assertIn("com.adups.fota", content)
            self.assertNotIn("com.android.systemui", content)
            self.assertNotIn("com.syu.ms", content)


class TestApply(unittest.TestCase):

    def test_apply_refuses_never(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = os.path.join(tmpdir, "plan.txt")
            Path(plan_path).write_text(
                "com.android.egg\ncom.android.systemui\n")
            args = type("Args", (), {
                "plan": plan_path, "uninstall": False
            })()
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, lists, args)
            journal = sanity.journal_read(adb.device_id())
            actions = [e["action"] for e in journal
                       if e.get("package") == "com.android.systemui"]
            self.assertTrue(len(actions) == 0)

    def test_apply_processes_normal(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = os.path.join(tmpdir, "plan.txt")
            Path(plan_path).write_text("com.android.egg\n")
            args = type("Args", (), {
                "plan": plan_path, "uninstall": False
            })()
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, lists, args)
                journal = sanity.journal_read(adb.device_id())
            pkgs = [e["package"] for e in journal
                    if e.get("action") == "disable-user"]
            self.assertIn("com.android.egg", pkgs)

    def test_apply_force_guarded(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = os.path.join(tmpdir, "plan.txt")
            Path(plan_path).write_text(
                "# force: com.syu.somethingelse  # test\n")
            args = type("Args", (), {
                "plan": plan_path, "uninstall": False
            })()
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, lists, args)
                journal = sanity.journal_read(adb.device_id())
            pkgs = [e["package"] for e in journal
                    if e.get("action") == "disable-user"]
            self.assertIn("com.syu.somethingelse", pkgs)


class TestRestore(unittest.TestCase):

    def test_restore_last(self):
        adb = FakeAdb()
        lists = sanity.load_all_lists(LISTS_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.journal_append(adb.device_id(), {
                    "action": "disable-user", "package": "com.android.egg",
                })
                args = type("Args", (), {
                    "package": None, "last": 1, "all": False
                })()
                sanity.cmd_restore(adb, lists, args)
                journal = sanity.journal_read(adb.device_id())
            restores = [e for e in journal if e.get("action") == "restore"]
            self.assertEqual(len(restores), 1)
            self.assertEqual(restores[0]["package"], "com.android.egg")


class TestJournal(unittest.TestCase):

    def test_journal_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.journal_append("test123", {
                    "action": "test", "data": "hello"})
                entries = sanity.journal_read("test123")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "test")
        self.assertEqual(entries[0]["data"], "hello")
        self.assertIn("ts", entries[0])


if __name__ == "__main__":
    unittest.main()

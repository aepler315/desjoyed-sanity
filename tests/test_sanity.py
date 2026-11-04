#!/usr/bin/env python3
"""Tests for sanity.py: list parsing, classification, ADB integration."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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


class RecordingAdb(FakeAdb):
    """Record the real wrapper's commands while isolating the physical device."""

    def __init__(self):
        super().__init__()
        self.commands = []
        self.failures = set()

    def shell(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd in self.failures:
            raise sanity.AdbError("simulated package manager failure")
        return super().shell(cmd, **kwargs)

    @property
    def mutations(self):
        return [cmd for cmd in self.commands if cmd.startswith((
            "pm disable-user ", "pm uninstall ", "pm enable ",
            "pm install-existing "))]


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

    def test_regex_reason_accepts_one_space_without_breaking_alternation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "guard.txt"
            path.write_text(r"(^|\.)(audio|dsp)([._]|$) | Audio path" + "\n")
            entries = sanity.parse_regex_list(path)
        pattern, reason = entries[0]
        self.assertIsNotNone(pattern.search("org.example.dsp"))
        self.assertIsNone(pattern.search("org.example.safe"))
        self.assertEqual(reason, "Audio path")


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


class TestAdbWrapper(unittest.TestCase):

    def test_android_utf8_properties_ignore_host_locale(self):
        adb = sanity.Adb(binary=sys.executable)
        adb._base += ["-c", "import sys; sys.stdout.buffer.write("
                      "b'[ro.product.model]: [\\xe5\\x8d\\x95]\\n')"]
        self.assertEqual(adb.getprop()["ro.product.model"], "\u5355")

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
            self.assertIn("com.android.egg", [e.get("package") for e in journal])
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
                "force: com.syu.somethingelse  # test\n")
            args = type("Args", (), {
                "plan": plan_path, "uninstall": False
            })()
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, lists, args)
                journal = sanity.journal_read(adb.device_id())
            pkgs = [e["package"] for e in journal
                    if e.get("action") == "disable-user"]
            self.assertIn("com.syu.somethingelse", pkgs)

    def test_comments_never_apply_even_with_force_prefix(self):
        adb = RecordingAdb()
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = Path(tmpdir) / "plan.txt"
            plan.write_text("com.android.egg\n# force: com.syu.somethingelse\n"
                            "  # force: com.syu.another\n# com.adups.fota\n")
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                 SimpleNamespace(plan=str(plan), uninstall=False))
        self.assertEqual(adb.mutations,
                         ["pm disable-user --user 0 com.android.egg"])

    def test_guarded_package_requires_explicit_force(self):
        adb = RecordingAdb()
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = Path(tmpdir) / "plan.txt"
            plan.write_text("com.syu.somethingelse\norg.example.dsp\n")
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                 SimpleNamespace(plan=str(plan), uninstall=False))
        self.assertEqual(adb.mutations, [])

    def test_force_never_overrides_hard_deny(self):
        adb = RecordingAdb()
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = Path(tmpdir) / "plan.txt"
            plan.write_text("force: com.android.systemui\nforce: com.syu.ms\n")
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                 SimpleNamespace(plan=str(plan), uninstall=False))
                self.assertEqual(sanity.journal_read(adb.device_id()), [])
        self.assertEqual(adb.mutations, [])

    def test_invalid_plan_aborts_before_any_mutation(self):
        for invalid in ("com.example.app; reboot", "com.example.app && reboot",
                        "com.example.$(reboot)", "--user 10", "com..example",
                        "com.example app", "force:", "force: com.example/app"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmpdir:
                adb = RecordingAdb()
                plan = Path(tmpdir) / "plan.txt"
                plan.write_text(f"com.android.egg\n{invalid}\n")
                with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                    with self.assertRaises(SystemExit) as caught:
                        sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                         SimpleNamespace(plan=str(plan), uninstall=False))
                    self.assertNotEqual(caught.exception.code, 0)
                self.assertEqual(adb.mutations, [])

    def test_missing_explicit_plan_does_not_apply_default(self):
        adb = RecordingAdb()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                default = sanity.state_dir_for(adb.device_id()) / "plan.txt"
                default.write_text("com.android.egg\n")
                with self.assertRaises(SystemExit) as caught:
                    sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                     SimpleNamespace(plan=str(Path(tmpdir) / "missing.txt"),
                                                     uninstall=False))
                self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(adb.mutations, [])

    def test_failed_apply_returns_nonzero(self):
        adb = RecordingAdb()
        adb.failures.add("pm disable-user --user 0 com.android.egg")
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = Path(tmpdir) / "plan.txt"
            plan.write_text("com.android.egg\n")
            with patch.object(sanity, "STATE_DIR", Path(tmpdir)):
                with self.assertRaises(SystemExit) as caught:
                    sanity.cmd_apply(adb, sanity.load_all_lists(LISTS_DIR),
                                     SimpleNamespace(plan=str(plan), uninstall=False))
                self.assertNotEqual(caught.exception.code, 0)
                self.assertFalse(any(e["action"] == "disable-user"
                                     for e in sanity.journal_read(adb.device_id())))


class TestRestore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_patch = patch.object(sanity, "STATE_DIR", Path(self.tmp.name))
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.adb = RecordingAdb()
        self.dev_id = self.adb.device_id()
        self.lists = sanity.load_all_lists(LISTS_DIR)

    def append(self, action, package="com.android.egg", **extra):
        sanity.journal_append(self.dev_id, {"action": action, "package": package,
                                           **extra})

    def restore(self, **kwargs):
        args = {"package": None, "last": None, "all": False}
        args.update(kwargs)
        sanity.cmd_restore(self.adb, self.lists, SimpleNamespace(**args))

    def test_restore_uses_inverse_of_recorded_action(self):
        self.append("disable-user")
        self.append("uninstall-user", "com.adups.fota")
        self.restore(all=True)
        self.assertCountEqual(self.adb.mutations, [
            "pm enable com.android.egg",
            "pm install-existing --user 0 com.adups.fota",
        ])

    def test_repeated_last_restores_next_pending_package(self):
        self.append("disable-user", "com.adups.fota")
        self.append("disable-user")
        self.restore(last=1)
        self.restore(last=1)
        self.assertEqual(self.adb.mutations, [
            "pm enable com.android.egg", "pm enable com.adups.fota",
        ])

    def test_failed_restore_stays_pending_and_retry_succeeds(self):
        self.append("disable-user")
        self.adb.failures.add("pm enable com.android.egg")
        with self.assertRaises(SystemExit) as caught:
            self.restore(all=True)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertFalse(any(e["action"] == "restore"
                             for e in sanity.journal_read(self.dev_id)))
        self.adb.failures.clear()
        self.restore(all=True)
        self.assertEqual(self.adb.mutations, [
            "pm enable com.android.egg", "pm enable com.android.egg",
        ])
        self.assertEqual(len([e for e in sanity.journal_read(self.dev_id)
                              if e["action"] == "restore"]), 1)

    def test_later_disable_after_restore_is_pending_again(self):
        self.append("disable-user")
        self.append("restore")
        self.append("disable-user")
        self.restore(last=1)
        self.assertEqual(self.adb.mutations, ["pm enable com.android.egg"])

    def test_disable_then_uninstall_restores_both_in_reverse_order(self):
        self.append("disable-user")
        self.append("uninstall-user")
        self.restore(last=1)
        self.assertEqual(self.adb.mutations, [
            "pm install-existing --user 0 com.android.egg",
            "pm enable com.android.egg",
        ])
        self.adb.commands.clear()
        with self.assertRaises(SystemExit):
            self.restore(all=True)
        self.assertEqual(self.adb.mutations, [])

    def test_partial_restore_keeps_failed_inverse_pending(self):
        self.append("disable-user")
        self.append("uninstall-user")
        self.adb.failures.add("pm enable com.android.egg")
        with self.assertRaises(SystemExit) as caught:
            self.restore(all=True)
        self.assertNotEqual(caught.exception.code, 0)
        self.adb.failures.clear()
        self.adb.commands.clear()
        self.restore(all=True)
        self.assertEqual(self.adb.mutations, ["pm enable com.android.egg"])

    def test_failed_reinstall_does_not_attempt_earlier_enable(self):
        self.append("disable-user")
        self.append("uninstall-user")
        self.adb.failures.add("pm install-existing --user 0 com.android.egg")
        with self.assertRaises(SystemExit):
            self.restore(all=True)
        self.assertEqual(self.adb.mutations,
                         ["pm install-existing --user 0 com.android.egg"])

    def test_restore_does_not_guess_method_without_pending_action(self):
        with self.assertRaises(SystemExit) as caught:
            self.restore(package="com.android.egg")
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(self.adb.mutations, [])

    def test_invalid_journal_package_aborts_before_any_mutation(self):
        self.append("disable-user", "com.adups.fota")
        self.append("disable-user", "com.example.app; reboot")
        with self.assertRaises(SystemExit) as caught:
            self.restore(all=True)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(self.adb.mutations, [])

    def test_last_count_must_be_positive(self):
        self.append("disable-user")
        for count in (0, -1):
            with self.subTest(count=count):
                with self.assertRaises(SystemExit) as caught:
                    self.restore(last=count)
                self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(self.adb.mutations, [])

    def test_restore_options_are_mutually_exclusive(self):
        self.append("disable-user")
        with self.assertRaises(SystemExit) as caught:
            self.restore(last=1, all=True)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(self.adb.mutations, [])

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

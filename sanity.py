#!/usr/bin/env python3
"""
desjoyed-sanity: reversible debloat toolkit for FYT-lineage Android head units.

Every action is reversible:
  pm disable-user --user 0  -> pm enable
  pm uninstall -k --user 0  -> pm install-existing --user 0

Nothing touches the system partition. Nothing needs root (but Magisk helps
with things like /etc/hosts and boot scripts).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LISTS_DIR = Path(__file__).resolve().parent / "lists"
STATE_DIR = Path(__file__).resolve().parent / "state"

# ---------------------------------------------------------------------------
# ADB wrapper
# ---------------------------------------------------------------------------

class AdbError(Exception):
    pass


class Adb:
    def __init__(self, serial: Optional[str] = None,
                 binary: Optional[str] = None):
        self.binary = binary or os.environ.get("SANITY_ADB", "adb")
        self.serial = serial
        self._base = [self.binary]
        if self.serial:
            self._base += ["-s", self.serial]

    def run(self, *args: str, check: bool = True,
            timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = self._base + list(args)
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise AdbError(f"adb binary not found: {self.binary}")
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb timed out after {timeout}s: {' '.join(cmd)}")
        if check and r.returncode != 0:
            raise AdbError(
                f"adb returned {r.returncode}: {r.stderr.strip()}")
        return r

    def shell(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run("shell", cmd, **kwargs)

    def getprop(self) -> dict[str, str]:
        r = self.shell("getprop")
        props = {}
        for line in r.stdout.splitlines():
            m = re.match(r"^\[(.+?)\]: \[(.*)\]$", line)
            if m:
                props[m.group(1)] = m.group(2)
        return props

    def pm_list(self) -> list[dict]:
        r = self.shell("pm list packages -f --show-versioncode -U")
        pkgs = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            rest = line[len("package:"):]
            last_eq = rest.rfind("=")
            if last_eq < 0:
                continue
            apk_path = rest[:last_eq]
            name = rest[last_eq + 1:].split()[0]
            vc_match = re.search(r"versionCode:(\d+)", line)
            uid_match = re.search(r"uid:(\d+)", line)
            ver_code = vc_match.group(1) if vc_match else ""
            uid = uid_match.group(1) if uid_match else ""
            pkgs.append({
                "name": name, "path": apk_path,
                "versionCode": ver_code, "uid": uid,
            })
        return pkgs

    def pm_disabled(self) -> set[str]:
        r = self.shell("pm list packages -d")
        out = set()
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                out.add(line[len("package:"):])
        return out

    def pm_disable(self, pkg: str) -> str:
        r = self.shell(f"pm disable-user --user 0 {pkg}")
        return r.stdout.strip()

    def pm_enable(self, pkg: str) -> str:
        r = self.shell(f"pm enable {pkg}")
        return r.stdout.strip()

    def pm_uninstall_user(self, pkg: str) -> str:
        r = self.shell(f"pm uninstall -k --user 0 {pkg}")
        return r.stdout.strip()

    def pm_install_existing(self, pkg: str) -> str:
        r = self.shell(f"pm install-existing --user 0 {pkg}")
        return r.stdout.strip()

    def dumpsys_package(self, pkg: str) -> dict:
        r = self.shell(f"dumpsys package {pkg}", timeout=15)
        info: dict = {"name": pkg, "flags": "", "sharedUser": "",
                      "signatures": [], "permissions": [],
                      "installed": True, "enabled": True}
        in_sigs = False
        for line in r.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("pkgFlags="):
                info["flags"] = stripped
            elif stripped.startswith("sharedUserId="):
                info["sharedUser"] = stripped.split("=", 1)[1]
            elif stripped.startswith("Signatures:") or stripped.startswith("signatures:"):
                in_sigs = True
            elif in_sigs and re.match(r"^[0-9a-f]{4,}", stripped):
                info["signatures"].append(stripped.split()[0])
                in_sigs = False
            elif stripped.startswith("User 0:"):
                if "installed=false" in stripped:
                    info["installed"] = False
                if "enabled=2" in stripped or "enabled=3" in stripped:
                    info["enabled"] = False
            elif stripped.startswith("requested permissions:"):
                pass
            elif stripped.startswith("android.permission.") and ":" not in stripped:
                info["permissions"].append(stripped)
        return info

    def device_id(self) -> str:
        props = self.getprop()
        serial = props.get("ro.serialno", props.get("ro.boot.serialno", ""))
        model = props.get("ro.product.model", "")
        key = f"{serial}:{model}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# List loading
# ---------------------------------------------------------------------------

@dataclass
class ListEntry:
    package: str
    why: str = ""
    risk: str = ""


@dataclass
class TierList:
    tier: str = ""
    default: str = "off"
    about: str = ""
    entries: list[ListEntry] = field(default_factory=list)


def parse_tier_list(path: Path) -> TierList:
    tl = TierList()
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# tier:"):
                tl.tier = line.split(":", 1)[1].strip()
            elif line.startswith("# default:"):
                tl.default = line.split(":", 1)[1].strip()
            elif line.startswith("# about:"):
                tl.about = line.split(":", 1)[1].strip()
            elif line.startswith("#") or not line.strip():
                continue
            else:
                parts = [p.strip() for p in line.split("|")]
                tl.entries.append(ListEntry(
                    package=parts[0],
                    why=parts[1] if len(parts) > 1 else "",
                    risk=parts[2] if len(parts) > 2 else "",
                ))
    return tl


def parse_regex_list(path: Path) -> list[tuple[re.Pattern, str]]:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            m = re.match(r"^(.+)\s+\|\s+(.*)$", line)
            if m:
                pattern = m.group(1).strip()
                why = m.group(2).strip()
            else:
                pattern = line.strip()
                why = ""
            entries.append((re.compile(pattern), why))
    return entries


def load_all_lists(lists_dir: Path) -> dict:
    never = parse_regex_list(lists_dir / "never.txt")
    guard = parse_regex_list(lists_dir / "guard.txt")
    tiers = {}
    for f in sorted(lists_dir.glob("*.txt")):
        if f.name in ("never.txt", "guard.txt"):
            continue
        tl = parse_tier_list(f)
        if tl.tier:
            tiers[tl.tier] = tl
    return {"never": never, "guard": guard, "tiers": tiers}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(pkg_name: str, lists: dict) -> dict:
    for pat, why in lists["never"]:
        if pat.search(pkg_name):
            return {"action": "never", "reason": why}

    for tier_name, tl in lists["tiers"].items():
        for entry in tl.entries:
            if entry.package == pkg_name:
                act = "disable" if tl.default == "on" else "skip"
                return {
                    "action": act, "tier": tier_name,
                    "why": entry.why, "risk": entry.risk,
                    "default": tl.default,
                }

    for pat, why in lists["guard"]:
        if pat.search(pkg_name):
            return {"action": "guard", "reason": why}

    return {"action": "unknown"}


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def state_dir_for(device_id: str) -> Path:
    d = STATE_DIR / device_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def journal_path(device_id: str) -> Path:
    return state_dir_for(device_id) / "journal.jsonl"


def journal_append(device_id: str, entry: dict):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(journal_path(device_id), "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def journal_read(device_id: str) -> list[dict]:
    p = journal_path(device_id)
    if not p.exists():
        return []
    entries = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_recon(adb: Adb, lists: dict, args):
    print("=== RECON ===")
    print()

    props = adb.getprop()
    boot_state = props.get("ro.boot.verifiedbootstate", "?")
    flash_locked = props.get("ro.boot.flash.locked", "?")
    vbmeta_state = props.get("ro.boot.vbmeta.device_state", "?")
    build_type = props.get("ro.build.type", "?")
    build_keys = props.get("ro.build.tags", "?")
    model = props.get("ro.product.model", "?")
    brand = props.get("ro.product.brand", "?")
    device = props.get("ro.product.device", "?")
    android_ver = props.get("ro.build.version.release", "?")
    sdk = props.get("ro.build.version.sdk", "?")
    soc = props.get("ro.hardware", props.get("ro.board.platform", "?"))
    serial = props.get("ro.serialno", "?")
    adb_secure = props.get("ro.adb.secure", "?")
    build_desc = props.get("ro.build.display.id", "?")

    print(f"  Model:        {brand} {model} ({device})")
    print(f"  Android:      {android_ver} (SDK {sdk})")
    print(f"  SoC:          {soc}")
    print(f"  Build:        {build_desc}")
    print(f"  Build type:   {build_type}")
    print(f"  Build keys:   {build_keys}")
    print(f"  Serial:       {serial}")
    print()

    if boot_state == "orange":
        print("  [OK] Bootloader:  UNLOCKED (verifiedbootstate=orange)")
    else:
        print(f"  [!!] Bootloader:  verifiedbootstate={boot_state}")

    if flash_locked == "0":
        print("  [OK] Flash lock:  UNLOCKED (flash.locked=0)")
    else:
        print(f"  [!!] Flash lock:  flash.locked={flash_locked}")

    if vbmeta_state == "unlocked":
        print("  [OK] VBMeta:      device_state=unlocked")
    else:
        print(f"  [!!] VBMeta:      device_state={vbmeta_state}")

    if "test-keys" in build_keys:
        print("  [!!] Signed with AOSP test keys (no real security boundary)")
    if adb_secure == "0":
        print("  [!!] ro.adb.secure=0: ADB is open to anyone on the network")

    print()
    print(f"  Device hash:  {adb.device_id()}")
    print()

    pkgs = adb.pm_list()
    disabled = adb.pm_disabled()

    print(f"  Installed packages:  {len(pkgs)}")
    print(f"  Already disabled:    {len(disabled)}")
    print()

    stats = {"never": 0, "disable": 0, "skip": 0,
             "guard": 0, "unknown": 0, "known-bad": 0}
    bad_found = []

    for p in pkgs:
        cl = classify(p["name"], lists)
        act = cl["action"]
        if act in stats:
            stats[act] += 1
        if cl.get("tier") == "known-bad":
            bad_found.append(p["name"])
            stats["known-bad"] += 1

    if bad_found:
        print("  *** KNOWN MALWARE / TELEMETRY ***")
        for b in bad_found:
            print(f"  [!!] {b}")
        print()

    print(f"  Protected (never):    {stats['never']}")
    print(f"  Auto-disable (on):    {stats['disable']}")
    print(f"  Optional (off):       {stats['skip']}")
    print(f"  Guarded:              {stats['guard']}")
    print(f"  Unknown:              {stats['unknown']}")
    print()

    dev_id = adb.device_id()
    recon_data = {
        "action": "recon", "device_id": dev_id,
        "model": f"{brand} {model}",
        "android": android_ver, "sdk": sdk, "soc": soc,
        "bootloader": boot_state, "flash_locked": flash_locked,
        "build_keys": build_keys, "adb_secure": adb_secure,
        "total_packages": len(pkgs), "already_disabled": len(disabled),
        "known_bad": bad_found,
    }
    journal_append(dev_id, recon_data)

    if args.json:
        recon_data["packages"] = pkgs
        print(json.dumps(recon_data, indent=2))


def cmd_triage(adb: Adb, lists: dict, args):
    print("=== TRIAGE ===")
    print()

    pkgs = adb.pm_list()
    disabled = adb.pm_disabled()

    plan_lines = []
    plan_lines.append("# desjoyed-sanity triage plan")
    plan_lines.append(f"# generated: {datetime.now(timezone.utc).isoformat()}")
    plan_lines.append(f"# device: {adb.device_id()}")
    plan_lines.append(f"# packages scanned: {len(pkgs)}")
    plan_lines.append("")

    by_tier: dict[str, list] = {}
    guarded = []
    unknown = []

    for p in sorted(pkgs, key=lambda x: x["name"]):
        name = p["name"]
        cl = classify(name, lists)

        if cl["action"] == "never":
            continue

        if name in disabled:
            continue

        if cl["action"] == "disable":
            tier = cl.get("tier", "?")
            by_tier.setdefault(tier, []).append((name, cl))
        elif cl["action"] == "skip":
            tier = cl.get("tier", "?")
            by_tier.setdefault(tier, []).append((name, cl))
        elif cl["action"] == "guard":
            guarded.append((name, cl))
        else:
            unknown.append((name, cl))

    tier_order = ["known-bad", "google-junk", "aosp", "aosp-apps",
                  "google", "projection", "vehicle", "vendor", "qualcomm"]

    for tier_name in tier_order:
        items = by_tier.pop(tier_name, [])
        if not items:
            continue
        tl = lists["tiers"].get(tier_name)
        default_str = tl.default if tl else "?"
        plan_lines.append(f"## {tier_name} (default: {default_str})")
        if tl and tl.about:
            plan_lines.append(f"# {tl.about}")
        plan_lines.append("")
        for name, cl in items:
            prefix = "" if cl["action"] == "disable" else "# "
            why = cl.get("why", "")
            risk = cl.get("risk", "")
            line = f"{prefix}{name}"
            if why:
                line += f"  # {why}"
            if risk:
                line += f" | RISK: {risk}"
            plan_lines.append(line)
        plan_lines.append("")

    for tier_name, items in sorted(by_tier.items()):
        if not items:
            continue
        plan_lines.append(f"## {tier_name}")
        plan_lines.append("")
        for name, cl in items:
            prefix = "" if cl["action"] == "disable" else "# "
            plan_lines.append(f"{prefix}{name}")
        plan_lines.append("")

    if guarded:
        plan_lines.append("## GUARDED (uncomment with force: prefix to apply)")
        plan_lines.append("# These matched vendor/vehicle/audio patterns.")
        plan_lines.append("# Do them one at a time. Reboot and test after each.")
        plan_lines.append("")
        for name, cl in guarded:
            reason = cl.get("reason", "")
            plan_lines.append(f"# force: {name}  # {reason}")
        plan_lines.append("")

    if unknown and not args.quiet:
        plan_lines.append("## UNKNOWN (not in any list)")
        plan_lines.append("")
        for name, cl in unknown:
            plan_lines.append(f"# {name}")
        plan_lines.append("")

    plan_text = "\n".join(plan_lines)

    out = args.output
    if out:
        Path(out).write_text(plan_text)
        print(f"  Plan written to: {out}")
    else:
        dev_id = adb.device_id()
        out = str(state_dir_for(dev_id) / "plan.txt")
        Path(out).write_text(plan_text)
        print(f"  Plan written to: {out}")

    active = sum(1 for line in plan_lines
                 if line and not line.startswith("#")
                 and not line.startswith("##"))
    commented = sum(1 for line in plan_lines
                    if line.startswith("# force:"))

    print(f"  Active (will disable):   {active}")
    print(f"  Guarded (needs force:):  {commented}")
    print()
    print("  Edit the plan, then run:  sanity.py apply <plan-file>")

    journal_append(adb.device_id(), {
        "action": "triage", "plan_file": out,
        "active": active, "guarded": commented,
    })


def cmd_apply(adb: Adb, lists: dict, args):
    plan_file = args.plan
    if not os.path.isfile(plan_file):
        dev_id = adb.device_id()
        alt = state_dir_for(dev_id) / "plan.txt"
        if alt.exists():
            plan_file = str(alt)
        else:
            print(f"[!!] Plan file not found: {plan_file}")
            sys.exit(1)

    with open(plan_file) as f:
        lines = f.readlines()

    targets = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("# force:"):
            pkg = line[len("# force:"):].split("#")[0].strip()
            targets.append(("force", pkg))
        elif line.startswith("#"):
            continue
        else:
            pkg = line.split("#")[0].strip()
            if pkg:
                targets.append(("normal", pkg))

    if not targets:
        print("[!!] No packages to apply. Is the plan empty?")
        sys.exit(1)

    print(f"=== APPLY ({len(targets)} packages) ===")
    print()

    dev_id = adb.device_id()
    never_patterns = lists["never"]

    ok = 0
    skipped = 0
    failed = 0

    for mode, pkg in targets:
        for pat, why in never_patterns:
            if pat.search(pkg):
                print(f"  [!!] REFUSED {pkg} -- protected: {why}")
                skipped += 1
                break
        else:
            if mode == "force":
                cl = classify(pkg, lists)
                if cl["action"] == "guard":
                    print(f"  [**] FORCE  {pkg} (guarded: {cl.get('reason', '')})")
                else:
                    print(f"  [**] FORCE  {pkg}")

            try:
                if args.uninstall:
                    result = adb.pm_uninstall_user(pkg)
                else:
                    result = adb.pm_disable(pkg)
                method = "uninstall-user" if args.uninstall else "disable-user"
                print(f"  [OK] {pkg}: {result}")
                journal_append(dev_id, {
                    "action": method, "package": pkg,
                    "mode": mode, "result": result,
                })
                ok += 1
            except AdbError as e:
                print(f"  [!!] {pkg}: {e}")
                journal_append(dev_id, {
                    "action": "error", "package": pkg,
                    "mode": mode, "error": str(e),
                })
                failed += 1

    print()
    print(f"  Done: {ok} ok, {skipped} refused, {failed} failed")


def cmd_restore(adb: Adb, lists: dict, args):
    dev_id = adb.device_id()
    journal = journal_read(dev_id)

    if args.package:
        targets = [args.package]
    elif args.last:
        disabled = [
            e["package"] for e in reversed(journal)
            if e.get("action") in ("disable-user", "uninstall-user")
        ]
        seen = set()
        targets = []
        for p in disabled:
            if p not in seen:
                seen.add(p)
                targets.append(p)
            if len(targets) >= args.last:
                break
    elif args.all:
        disabled = set()
        for e in journal:
            if e.get("action") in ("disable-user", "uninstall-user"):
                disabled.add(e["package"])
            elif e.get("action") in ("restore",):
                disabled.discard(e.get("package"))
        targets = sorted(disabled)
    else:
        print("[!!] Specify --package, --last N, or --all")
        sys.exit(1)

    if not targets:
        print("[!!] Nothing to restore")
        sys.exit(1)

    print(f"=== RESTORE ({len(targets)} packages) ===")
    print()

    for pkg in targets:
        try:
            r1 = adb.pm_enable(pkg)
            print(f"  [OK] enable  {pkg}: {r1}")
        except AdbError:
            pass
        try:
            r2 = adb.pm_install_existing(pkg)
            print(f"  [OK] reinstall {pkg}: {r2}")
        except AdbError:
            pass
        journal_append(dev_id, {"action": "restore", "package": pkg})

    print()
    print("  Restored. Reboot to be sure.")


def cmd_home(adb: Adb, lists: dict, args):
    launcher = args.launcher
    if not launcher:
        print("[!!] --launcher <package> required")
        print("     Example: sanity.py home --launcher net.nicbell.lawnchair")
        sys.exit(1)

    print(f"=== SET HOME: {launcher} ===")
    print()

    try:
        r = adb.shell(
            f"cmd package set-home-activity {launcher}/.DefaultActivity",
            check=False)
        if r.returncode == 0:
            print(f"  [OK] {r.stdout.strip()}")
        else:
            print(f"  Trying resolve-activity fallback...")
            r2 = adb.shell(
                f"cmd package resolve-activity -c android.intent.category.HOME {launcher}",
                check=False)
            activity = ""
            for line in r2.stdout.splitlines():
                if "name=" in line:
                    activity = line.split("name=")[1].strip()
                    break
            if activity:
                r3 = adb.shell(f"cmd package set-home-activity {activity}")
                print(f"  [OK] {r3.stdout.strip()}")
            else:
                print(f"  [!!] Could not resolve home activity for {launcher}")
                print("  Set it manually: Settings > Apps > Default apps > Home")
    except AdbError as e:
        print(f"  [!!] {e}")
        print("  Set it manually: Settings > Apps > Default apps > Home")


def cmd_dns(adb: Adb, lists: dict, args):
    server = args.server or "dns.quad9.net"
    print(f"=== PRIVATE DNS: {server} ===")
    print()
    try:
        adb.shell(f"settings put global private_dns_mode hostname")
        adb.shell(f"settings put global private_dns_specifier {server}")
        verify = adb.shell("settings get global private_dns_specifier")
        print(f"  [OK] private_dns_specifier = {verify.stdout.strip()}")
    except AdbError as e:
        print(f"  [!!] {e}")

    journal_append(adb.device_id(), {
        "action": "dns", "server": server,
    })


def cmd_journal(adb: Adb, lists: dict, args):
    dev_id = args.device or adb.device_id()
    entries = journal_read(dev_id)
    if not entries:
        print("  No journal entries.")
        return
    for e in entries:
        ts = e.get("ts", "?")[:19]
        act = e.get("action", "?")
        pkg = e.get("package", "")
        extra = ""
        if pkg:
            extra = f" {pkg}"
        elif e.get("model"):
            extra = f" {e['model']}"
        print(f"  {ts}  {act}{extra}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="sanity.py",
        description="Reversible debloat for FYT-lineage Android head units.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            workflow:
              1. sanity.py recon          -- see what you have
              2. sanity.py triage         -- generate a plan
              3. edit the plan            -- comment/uncomment lines
              4. sanity.py apply <plan>   -- disable the uncommented packages
              5. reboot and test
              6. sanity.py restore --last 1  -- undo one if something broke
        """))
    parser.add_argument(
        "-s", "--serial", help="ADB device serial (or set ANDROID_SERIAL)")
    parser.add_argument(
        "--adb", help="Path to adb binary (or set SANITY_ADB)")
    parser.add_argument(
        "--lists", help="Path to lists directory",
        default=str(LISTS_DIR))

    sub = parser.add_subparsers(dest="command")

    p_recon = sub.add_parser("recon", help="Scan the device")
    p_recon.add_argument("--json", action="store_true",
                         help="Also dump full JSON")

    p_triage = sub.add_parser("triage", help="Generate a disable plan")
    p_triage.add_argument("-o", "--output", help="Plan output file")
    p_triage.add_argument("-q", "--quiet", action="store_true",
                          help="Omit unknown packages from plan")

    p_apply = sub.add_parser("apply", help="Apply a plan")
    p_apply.add_argument("plan", nargs="?", default="",
                         help="Plan file (default: state/<device>/plan.txt)")
    p_apply.add_argument("--uninstall", action="store_true",
                         help="Use pm uninstall -k --user 0 instead of disable")

    p_restore = sub.add_parser("restore", help="Re-enable packages")
    p_restore.add_argument("--package", help="Restore one package by name")
    p_restore.add_argument("--last", type=int, help="Restore the last N")
    p_restore.add_argument("--all", action="store_true",
                           help="Restore everything from the journal")

    p_home = sub.add_parser("home", help="Set the default launcher")
    p_home.add_argument("--launcher", required=True,
                        help="Package name of the new launcher")

    p_dns = sub.add_parser("dns", help="Set private DNS")
    p_dns.add_argument("--server", default="dns.quad9.net",
                        help="DNS-over-TLS hostname (default: dns.quad9.net)")

    p_journal = sub.add_parser("journal", help="Show action journal")
    p_journal.add_argument("--device", help="Device hash (default: connected)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    lists_path = Path(args.lists)
    all_lists = load_all_lists(lists_path)

    adb = Adb(serial=args.serial or os.environ.get("ANDROID_SERIAL"),
              binary=args.adb)

    commands = {
        "recon": cmd_recon,
        "triage": cmd_triage,
        "apply": cmd_apply,
        "restore": cmd_restore,
        "home": cmd_home,
        "dns": cmd_dns,
        "journal": cmd_journal,
    }

    fn = commands[args.command]
    fn(adb, all_lists, args)


if __name__ == "__main__":
    main()

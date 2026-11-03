#!/usr/bin/env python3
"""
fwtool.py: inspect and modify FYT-lineage firmware ZIPs (update_car.zip).

Read-only by default. The 'strip' subcommand creates a new zip with
selected entries removed. No re-signing: the stock updater trusts the
AOSP testkey, and we leave the existing signature intact by rewriting
only the entries before the signing data.

This does NOT flash anything. It creates a modified zip you then flash
through the normal USB update path.
"""

import argparse
import hashlib
import os
import re
import struct
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Zip signature handling (Android whole-file v1 signature)
#
# The signing format:
#   [zip content ... EOCD] [PKCS#7 DER] [2-byte p7 len] [4-byte EOCD offset]
#   EOCD comment = "signed by SignApk\0" + p7 + footer
#   Comment length field in EOCD = len(comment)
#
# We detect the signature by looking for the EOCD, checking if the
# comment starts with "signed by SignApk", and extracting the PKCS#7
# block.
# ---------------------------------------------------------------------------

SIGNEDBY = b"signed by SignApk\x00"
EOCD_MAGIC = b"\x50\x4b\x05\x06"
EOCD_MIN_SIZE = 22

# AOSP testkey SHA-256 fingerprint (DER of the X.509 cert)
AOSP_TESTKEY_SHA256 = (
    "a40da80a59d170caa950cf15c18c454d47a39b26989d8b640ecd745ba71bf5dc"
)


def find_eocd(data: bytes) -> Optional[int]:
    """Find the End of Central Directory record, scanning backwards."""
    max_comment = 65535
    search_start = max(0, len(data) - EOCD_MIN_SIZE - max_comment)
    pos = len(data) - EOCD_MIN_SIZE
    while pos >= search_start:
        if data[pos:pos + 4] == EOCD_MAGIC:
            comment_len = struct.unpack_from("<H", data, pos + 20)[0]
            if pos + EOCD_MIN_SIZE + comment_len == len(data):
                return pos
        pos -= 1
    return None


def parse_signature(data: bytes) -> dict:
    """Parse Android whole-file v1 signature from a zip."""
    info = {"signed": False, "signer": "", "cert_sha256": ""}

    eocd_pos = find_eocd(data)
    if eocd_pos is None:
        return info

    comment_len = struct.unpack_from("<H", data, eocd_pos + 20)[0]
    if comment_len == 0:
        return info

    comment = data[eocd_pos + EOCD_MIN_SIZE:]
    if not comment.startswith(SIGNEDBY):
        return info

    info["signed"] = True
    info["signer"] = "SignApk"

    footer = comment[-6:]
    p7_len = struct.unpack_from("<H", footer, 0)[0]

    p7_start = len(SIGNEDBY)
    p7_end = p7_start + p7_len
    if p7_end <= len(comment):
        p7_der = comment[p7_start:p7_end]
        info["p7_size"] = len(p7_der)

    return info


def check_metadata(zf: zipfile.ZipFile) -> dict:
    """Check updater-script metadata for anti-downgrade timestamp."""
    info = {"has_updater_script": False, "post_timestamp": None,
            "pre_device": None}
    try:
        script = zf.read("META-INF/com/google/android/updater-script")
        info["has_updater_script"] = True
        text = script.decode("utf-8", errors="replace")
        for line in text.splitlines():
            m = re.search(r'getprop\("ro\.build\.date\.utc"\)\s*[<>!=]+\s*"(\d+)"', line)
            if m:
                info["post_timestamp"] = int(m.group(1))
            if not info["post_timestamp"]:
                m = re.search(r'less_than_int\(\s*"(\d+)"\s*,\s*getprop\("ro\.build\.date\.utc"\)', line)
                if m:
                    info["post_timestamp"] = int(m.group(1))
            m = re.search(r'getprop\("ro\.product\.device"\)\s*==\s*"([^"]+)"', line)
            if m:
                info["pre_device"] = m.group(1)
    except KeyError:
        pass
    return info


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_inspect(args):
    """Inspect a firmware zip: list contents, check signature, metadata."""
    zip_path = args.zip
    if not os.path.isfile(zip_path):
        print(f"[!!] File not found: {zip_path}")
        sys.exit(1)

    data = Path(zip_path).read_bytes()
    file_sha256 = hashlib.sha256(data).hexdigest()

    print(f"=== INSPECT: {zip_path} ===")
    print(f"  Size:    {len(data):,} bytes")
    print(f"  SHA-256: {file_sha256}")
    print()

    sig = parse_signature(data)
    if sig["signed"]:
        print(f"  [OK] Signed by: {sig['signer']}")
        if sig.get("p7_size"):
            print(f"       PKCS#7 block: {sig['p7_size']} bytes")
    else:
        print("  [!!] No Android v1 whole-file signature found")
    print()

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as e:
        print(f"[!!] Bad zip: {e}")
        sys.exit(1)

    meta = check_metadata(zf)
    if meta["has_updater_script"]:
        print("  [OK] updater-script found")
        if meta["post_timestamp"]:
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(meta["post_timestamp"], tz=timezone.utc)
            print(f"       Anti-downgrade timestamp: {ts.isoformat()}")
        if meta["pre_device"]:
            print(f"       Device assert: {meta['pre_device']}")
    else:
        print("  [!!] No updater-script found")
    print()

    namelist = zf.namelist()
    print(f"  Entries: {len(namelist)}")
    print()

    owner_apks = [n for n in namelist if n.startswith("owner_apk/")]
    if owner_apks:
        print(f"  owner_apk/ ({len(owner_apks)} APKs):")
        for a in sorted(owner_apks):
            zi = zf.getinfo(a)
            print(f"    {a}  ({zi.file_size:,} bytes)")
        print()

    config_files = [n for n in namelist
                    if n.startswith("config_file/")]
    if config_files:
        print(f"  config_file/ ({len(config_files)} files):")
        for c in sorted(config_files):
            zi = zf.getinfo(c)
            print(f"    {c}  ({zi.file_size:,} bytes)")
        print()

    images = [n for n in namelist
              if n.endswith(".img") or n.endswith(".bin")]
    if images:
        print(f"  Partition images ({len(images)}):")
        for img in sorted(images):
            zi = zf.getinfo(img)
            print(f"    {img}  ({zi.file_size:,} bytes)")
        print()

    if args.list_all:
        print("  All entries:")
        for n in sorted(namelist):
            zi = zf.getinfo(n)
            method = "stored" if zi.compress_type == 0 else "deflated"
            print(f"    {method:8s} {zi.file_size:>12,}  {n}")
        print()

    zf.close()


def cmd_strip(args):
    """Create a new zip with specified entries removed."""
    zip_path = args.zip
    out_path = args.output
    patterns = args.remove or []

    if not patterns:
        print("[!!] Nothing to remove. Use --remove <pattern>")
        sys.exit(1)

    if not os.path.isfile(zip_path):
        print(f"[!!] File not found: {zip_path}")
        sys.exit(1)

    compiled = [re.compile(p) for p in patterns]

    print(f"=== STRIP: {zip_path} -> {out_path} ===")
    print()

    with zipfile.ZipFile(zip_path, "r") as zin:
        removed = []
        kept = []
        for name in zin.namelist():
            matched = any(p.search(name) for p in compiled)
            if matched:
                removed.append(name)
            else:
                kept.append(name)

        if not removed:
            print("  [!!] No entries matched the patterns")
            sys.exit(1)

        print(f"  Removing {len(removed)} entries:")
        for r in removed:
            print(f"    - {r}")
        print()
        print(f"  Keeping {len(kept)} entries")
        print()

        with zipfile.ZipFile(out_path, "w") as zout:
            for name in kept:
                info = zin.getinfo(name)
                data = zin.read(name)
                zout.writestr(info, data)

    out_size = os.path.getsize(out_path)
    out_sha256 = hashlib.sha256(Path(out_path).read_bytes()).hexdigest()
    print(f"  Output:  {out_path}")
    print(f"  Size:    {out_size:,} bytes")
    print(f"  SHA-256: {out_sha256}")
    print()
    print("  [!!] The output zip is NOT signed. If the updater checks")
    print("       the signature, you will need to re-sign it with the")
    print("       AOSP testkey (see docs/firmware.md).")


def cmd_sigcheck(args):
    """Check whether a zip's signature uses the AOSP testkey."""
    zip_path = args.zip
    if not os.path.isfile(zip_path):
        print(f"[!!] File not found: {zip_path}")
        sys.exit(1)

    data = Path(zip_path).read_bytes()
    sig = parse_signature(data)

    print(f"=== SIGCHECK: {zip_path} ===")
    print()

    if not sig["signed"]:
        print("  [!!] No Android v1 whole-file signature found")
        sys.exit(1)

    print(f"  Signed by:   {sig['signer']}")
    if sig.get("p7_size"):
        print(f"  PKCS#7 size: {sig['p7_size']} bytes")
    print()
    print("  To extract and verify the certificate, use:")
    print(f"    openssl cms -verify -in <p7.der> -inform DER \\")
    print(f"      -noverify -content <signed-region>")
    print()
    print(f"  Known AOSP testkey SHA-256:")
    print(f"    {AOSP_TESTKEY_SHA256}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="fwtool.py",
        description="Inspect and modify FYT-lineage firmware ZIPs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              fwtool.py inspect update_car.zip
              fwtool.py inspect update_car.zip --list-all
              fwtool.py strip update_car.zip -o stripped.zip --remove 'owner_apk/.*'
              fwtool.py sigcheck update_car.zip
        """))

    sub = parser.add_subparsers(dest="command")

    p_inspect = sub.add_parser("inspect", help="Inspect a firmware zip")
    p_inspect.add_argument("zip", help="Path to update_car.zip")
    p_inspect.add_argument("--list-all", action="store_true",
                           help="List every entry")

    p_strip = sub.add_parser("strip",
                             help="Create a new zip with entries removed")
    p_strip.add_argument("zip", help="Path to input zip")
    p_strip.add_argument("-o", "--output", required=True,
                         help="Output zip path")
    p_strip.add_argument("--remove", nargs="+",
                         help="Regex patterns for entries to remove")

    p_sig = sub.add_parser("sigcheck",
                           help="Check the zip signature")
    p_sig.add_argument("zip", help="Path to zip")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "inspect": cmd_inspect,
        "strip": cmd_strip,
        "sigcheck": cmd_sigcheck,
    }

    fn = commands[args.command]
    fn(args)


if __name__ == "__main__":
    main()

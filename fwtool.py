#!/usr/bin/env python3
"""
fwtool.py: inspect and modify FYT-lineage firmware ZIPs (update_car.zip).

Read-only by default. The 'strip' subcommand creates a new unsigned zip
with selected entries removed and refuses existing output paths.
Signature detection is structural only: no cryptographic verification or
device/updater compatibility is established. This tool does not flash.
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
#   EOCD comment = message + PKCS#7 DER + 6-byte footer
#   footer = uint16 signature_start_from_EOF, 0xff 0xff, uint16 comment_size
# Source: AOSP build/tools/signapk/src/com/android/signapk/SignApk.java,
# signWholeFile. This only recognizes a container; it cannot authenticate it.
# ---------------------------------------------------------------------------

SIGNEDBY = b"signed by SignApk\x00"
EOCD_MAGIC = b"\x50\x4b\x05\x06"
EOCD_MIN_SIZE = 22

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
    """Detect an Android whole-file signature container, without verifying it.

    The legacy 'signed' key describes presence only. 'verified' is always false.
    No signer identity or certificate fingerprint is inferred from a ZIP comment.
    """
    info = {"signed": False, "verified": False, "signer": "", "cert_sha256": ""}

    eocd_pos = find_eocd(data)
    if eocd_pos is None:
        return info

    comment_len = struct.unpack_from("<H", data, eocd_pos + 20)[0]
    if comment_len < 7:
        return info

    comment = data[eocd_pos + EOCD_MIN_SIZE:]
    signature_start, marker, footer_comment_len = struct.unpack("<H2sH", comment[-6:])
    if marker != b"\xff\xff" or footer_comment_len != comment_len:
        return info
    if not 6 < signature_start <= comment_len or EOCD_MAGIC in comment:
        return info
    info.update(signed=True, p7_size=signature_start - 6)
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
        print("  Signature container detected; signature and signer not verified")
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

    if os.path.lexists(out_path):
        if os.path.exists(out_path) and os.path.samefile(zip_path, out_path):
            print("[!!] Output refers to the input file; refusing to overwrite it")
        else:
            print(f"[!!] Output already exists; refusing to overwrite: {out_path}")
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

        try:
            # Exclusive creation also closes the race after the earlier check.
            with zipfile.ZipFile(out_path, "x") as zout:
                for name in kept:
                    info = zin.getinfo(name)
                    data = zin.read(name)
                    zout.writestr(info, data)
        except FileExistsError:
            print(f"[!!] Output already exists; refusing to overwrite: {out_path}")
            sys.exit(1)

    out_size = os.path.getsize(out_path)
    out_sha256 = hashlib.sha256(Path(out_path).read_bytes()).hexdigest()
    print(f"  Output:  {out_path}")
    print(f"  Size:    {out_size:,} bytes")
    print(f"  SHA-256: {out_sha256}")
    print()
    print("  [!!] The output zip has no valid whole-file signature.")
    print("       Updater acceptance and device compatibility are not verified.")


def cmd_sigcheck(args):
    """Detect the signature container; this is not a cryptographic verifier."""
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

    print("  Signature container detected; signature and signer not verified")
    if sig.get("p7_size"):
        print(f"  PKCS#7 size: {sig['p7_size']} bytes")
    print()
    print("  No certificate trust, AOSP testkey match, or updater acceptance established.")


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
                           help="Detect signature structure (no cryptographic verification)")
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

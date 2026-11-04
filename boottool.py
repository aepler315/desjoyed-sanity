#!/usr/bin/env python3
"""Read-only device inventory and offline preparation for stock customization.

This tool never roots, reboots, unlocks, or flashes a device. Image structure and
hashes establish neither authenticity nor compatibility with a particular unit.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from bootimage import inspect_image
from sanity import Adb, AdbError


PROPERTY_KEYS = (
    "ro.product.manufacturer", "ro.product.brand", "ro.product.model",
    "ro.product.device", "ro.product.name", "ro.product.board",
    "ro.product.cpu.abi", "ro.product.cpu.abilist", "ro.hardware",
    "ro.board.platform", "ro.boot.hardware", "ro.build.fingerprint",
    "ro.vendor.build.fingerprint", "ro.build.display.id", "ro.build.id",
    "ro.build.version.release", "ro.build.version.sdk",
    "ro.build.version.security_patch", "ro.build.date.utc", "ro.build.tags",
    "ro.build.type", "ro.bootloader", "ro.boot.flash.locked",
    "ro.boot.vbmeta.device_state", "ro.boot.verifiedbootstate",
    "ro.boot.veritymode", "ro.boot.slot_suffix", "ro.boot.slot",
    "ro.build.ab_update", "ro.virtual_ab.enabled",
    "ro.boot.dynamic_partitions", "ro.boot.super_partition", "ro.treble.enabled",
    "ro.adb.secure", "ro.secure", "ro.debuggable",
)
PARTITIONS = ("boot", "init_boot", "vendor_boot", "recovery")


def unlock_state(properties: dict) -> str:
    """Summarize OS-reported evidence, without claiming bootloader verification."""
    evidence = set()
    for key, values in (
        ("ro.boot.flash.locked", {"0": "unlocked", "1": "locked"}),
        ("ro.boot.vbmeta.device_state", {"unlocked": "unlocked", "locked": "locked"}),
        ("ro.boot.verifiedbootstate", {"orange": "unlocked", "green": "locked", "yellow": "locked"}),
    ):
        value = values.get(properties.get(key))
        if value:
            evidence.add(value)
    if len(evidence) > 1:
        return "conflicting"
    return "reported_" + next(iter(evidence)) if evidence else "unknown"


def active_slot(properties: dict):
    values = []
    for key in ("ro.boot.slot_suffix", "ro.boot.slot"):
        raw = properties.get(key, "")
        if raw:
            if raw not in ("a", "b", "_a", "_b"):
                return None
            values.append(raw.lstrip("_"))
    return values[0] if values and len(set(values)) == 1 else None


def collect_device(adb: Adb) -> dict:
    """Pin one authorized transport before collecting a bounded set of reads."""
    devices = {}
    for line in adb.run("devices", "-l").stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.startswith(("List of devices", "*")):
            devices[fields[0]] = fields[1]
    serial = adb.serial
    if not serial:
        if len(devices) != 1:
            raise AdbError("Select one connected device with --serial; found "
                           f"{len(devices)} transports")
        serial = next(iter(devices))
    if devices.get(serial) != "device":
        raise AdbError(f"Device {serial}: {devices.get(serial, 'not connected')}; "
                       "connect and authorize ADB first")
    selected = Adb(serial=serial, binary=adb.binary)
    errors = []
    try:
        props = selected.getprop()
        if not props:
            raise AdbError("getprop returned no readable properties")
    except AdbError as exc:
        props = {}
        errors.append({"section": "properties", "error": str(exc)})
    properties = {key: props[key] for key in PROPERTY_KEYS if key in props}

    partitions = []
    attempts = []
    for directory in ("/dev/block/by-name", "/dev/block/bootdevice/by-name"):
        try:
            result = selected.shell(f"ls -1 {directory}")
            names = result.stdout.splitlines()
            if not names or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", n) for n in names):
                raise AdbError(f"No usable partition-name listing at {directory}")
            partitions = sorted(set(names))
            break
        except AdbError as exc:
            attempts.append(str(exc))
    if not partitions:
        errors.append({"section": "partitions", "error": "; ".join(attempts)})
    return {
        "schema_version": 1, "kind": "device_probe",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "adb_serial": serial, "properties": properties,
        "partitions": partitions, "unlock_state": unlock_state(properties),
        "active_slot": active_slot(properties), "errors": errors,
        "scope": "OS-reported evidence; fastboot access and recovery are unverified",
    }


def validate_device_report(device: dict):
    if not isinstance(device, dict) or device.get("schema_version") != 1 or device.get("kind") != "device_probe":
        raise ValueError("Expected a schema-version-1 device report from boottool probe")
    if not isinstance(device.get("adb_serial"), str) or not device["adb_serial"]:
        raise ValueError("Device report has no ADB transport identity")
    props = device.get("properties")
    if not isinstance(props, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in props.items()):
        raise ValueError("Device properties must be a string mapping")
    names = device.get("partitions")
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError("Device partitions must be a list of names")
    if not isinstance(device.get("errors"), list):
        raise ValueError("Device report must include collection errors")


def prepare_workspace(device: dict, image: Path, partition: str, output: Path) -> dict:
    """Preserve one explicitly selected stock image; never generate a flash script."""
    validate_device_report(device)
    if partition not in PARTITIONS:
        raise ValueError("Select an unsuffixed partition: " + ", ".join(PARTITIONS))
    source = Path(image).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")
    info = inspect_image(source)
    expected_kind = "vendor_boot" if partition == "vendor_boot" else "boot"
    if info["kind"] != expected_kind:
        raise ValueError(f"Image kind {info['kind']} does not match {partition}")
    if partition == "init_boot" and (info["header_version"] != 4 or info["kernel_size"] != 0):
        raise ValueError("init_boot requires a kernel-less version-4 boot image")

    props = device["properties"]
    blockers = ["firmware_match_unverified", "image_authenticity_unverified",
                "magisk_target_unverified", "fastboot_access_unverified",
                "recovery_path_unverified"]
    if unlock_state(props) != "reported_unlocked":
        blockers.append("unlock_state_unconfirmed")
    names = device["partitions"]
    if not any(name in names for name in (partition, partition + "_a", partition + "_b")):
        blockers.append("partition_presence_unverified")
    if any(name in names for name in (partition + "_a", partition + "_b")) and active_slot(props) is None:
        blockers.append("active_slot_unknown")
    if info["ramdisk_size"] == 0:
        blockers.append("selected_image_has_no_ramdisk")
    if device["errors"]:
        blockers.append("device_probe_incomplete")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage entirely on the destination filesystem; publish only after all writes
    # and the copied-image hash check succeed.
    with tempfile.TemporaryDirectory(prefix=".boottool-", dir=output.parent) as tmp:
        stage = Path(tmp) / "workspace"
        stock = stage / "stock"
        stock.mkdir(parents=True)
        copied = stock / f"{partition}.img"
        shutil.copyfile(source, copied)
        copied_info = inspect_image(copied)
        if copied_info["sha256"] != info["sha256"] or copied_info["size_bytes"] != info["size_bytes"]:
            raise ValueError("Source image changed during preparation; workspace discarded")
        manifest = {
            "schema_version": 1, "kind": "stock_customization_workspace",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "needs_device_verification", "partition": partition,
            "device": device,
            "source": {"path": str(source), "provenance": "user_supplied_unverified"},
            "image": {**copied_info, "relative_path": f"stock/{partition}.img"},
            "blockers": blockers,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (stage / "NEXT_STEPS.md").write_text(
            "# Stock customization workspace\n\n"
            f"Selected image: `stock/{partition}.img`\n\n"
            f"SHA-256: `{copied_info['sha256']}`\n\n"
            "This is a preserved input and an evidence report, not a flashable release.\n"
            "The selected partition is your input, not a confirmed flashing target.\n\n"
            "1. Obtain the original firmware for this exact unit and installed build; "
            "confirm the selected image came from it. Keep the full package.\n"
            "2. Confirm bootloader access, partition/slot layout and a tested stock "
            "recovery procedure for this unit. OS properties alone are insufficient.\n"
            "3. Confirm the ramdisk location using the target device and Magisk. "
            "Patch a copy on that same target device using the official instructions.\n"
            "4. Keep the stock image unchanged. Inspect the patched output separately "
            "and review a device-specific flashing and rollback procedure before writing.\n\n"
            "Unresolved checks:\n" + "".join(f"- {item}\n" for item in blockers) +
            "\nOfficial guide: https://topjohnwu.github.io/Magisk/install.html\n",
            encoding="utf-8")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output appeared during preparation: {output}")
        stage.rename(output)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="Read boot properties and partition names via ADB")
    probe.add_argument("-s", "--serial", help="ADB serial (or ANDROID_SERIAL)")
    probe.add_argument("--adb", help="ADB binary (or SANITY_ADB)")
    probe.add_argument("-o", "--output", type=Path, required=True, help="New device report JSON file")
    inspect = commands.add_parser("image", help="Inspect an Android boot or vendor_boot image")
    inspect.add_argument("image", type=Path)
    inspect.add_argument("--json", action="store_true")
    prepare = commands.add_parser("prepare", help="Preserve a stock image and record unresolved checks")
    prepare.add_argument("--device", required=True, type=Path, help="JSON from probe")
    prepare.add_argument("--image", required=True, type=Path)
    prepare.add_argument("--partition", required=True, choices=PARTITIONS)
    prepare.add_argument("-o", "--output", required=True, type=Path, help="New workspace directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            if args.output.exists() or args.output.is_symlink():
                raise FileExistsError(f"Output already exists: {args.output}")
            report = collect_device(Adb(serial=args.serial or os.environ.get("ANDROID_SERIAL"), binary=args.adb))
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2)
                stream.write("\n")
            print(f"Device report: {args.output}")
            if report["errors"]:
                print("Report is incomplete; inspect errors before preparing an image.")
                return 2
        elif args.command == "image":
            report = inspect_image(args.image)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"{report['kind']} header v{report['header_version']}: "
                      f"{report['size_bytes']:,} bytes")
                print(f"Kernel: {report['kernel_size']:,}; ramdisk: {report['ramdisk_size']:,} bytes")
                print(f"SHA-256: {report['sha256']}")
                print("Structure inspected; authenticity and device compatibility unverified.")
        else:
            with args.device.open(encoding="utf-8") as stream:
                device = json.load(stream)
            manifest = prepare_workspace(device, args.image, args.partition, args.output)
            print(f"Prepared: {args.output}")
            print(f"Stock SHA-256: {manifest['image']['sha256']}")
            print("Read NEXT_STEPS.md; device verification is still required.")
        return 0
    except (AdbError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

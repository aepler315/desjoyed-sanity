# From an unlocked bootloader to stock customization

An unlocked bootloader provides a route to loading modified images. This
workbench begins with stock-based root and systemless customization, because
the stock kernel and vendor services provide the head unit's hardware support.
Replacement ROM development remains a separate, device-specific investigation.

## 1. Capture the actual device

With Android running and ADB authorized:

```bash
python boottool.py probe -o device.json
# For multiple transports:
python boottool.py probe --serial SERIAL -o device.json
```

The command selects one transport before reading properties and partition names.
It does not request root or reboot. Existing output files are refused. The
snapshot contains an ADB serial and build details; review it before sharing.

An incomplete snapshot is still saved, with errors, and the command exits 2.
Properties are OS-reported evidence. `reported_unlocked` does not establish that
fastboot is accessible, the image is trusted, or a particular partition accepts
flashing. Missing or contradictory lock information stays unknown/conflicting.

If the head unit is not connected, the command exits 1 without inventing a
snapshot. Keep collecting evidence from the real unit before selecting an image.

## 2. Obtain and inspect the matching stock image

Obtain the complete original firmware for the exact hardware revision and
installed build. Preserve its source and original files. An update ZIP from a
similar model is not sufficient evidence of a match. Extraction from proprietary
containers or OTA payloads is outside this first milestone.

```bash
python boottool.py image firmware/boot.img
python boottool.py image firmware/boot.img --json
```

The inspector recognizes Android boot headers v0-v4 and vendor boot headers
v3-v4. It checks section bounds and reports header/ramdisk facts and SHA-256.
It does not verify AVB or OTA signatures, decompress the kernel or ramdisk,
establish that the contents will boot, or confirm the package's vendor origin.

Do not select an image from the Android version alone. For example, devices
launching with Android 13 commonly separate the generic ramdisk into `init_boot`,
while upgraded devices may keep it in `boot`.
[AOSP generic boot layout](https://source.android.com/docs/core/architecture/partitions/generic-boot)

## 3. Preserve a working copy and record unresolved checks

Select the partition explicitly, based on the device and stock package:

```bash
python boottool.py prepare --device device.json --image firmware/boot.img --partition boot -o workspaces/stock
```

Accepted selections are `boot`, `init_boot`, `vendor_boot`, and `recovery`.
The command does not select slot suffixes. It checks image kind and rejects
clearly inconsistent `init_boot` headers, but it cannot establish compatibility.
The manifest always remains `needs_device_verification`.

The new workspace contains:

- `stock/<partition>.img`: an exact byte copy, checked against the source hash.
- `manifest.json`: source path, selected partition, device snapshot, image facts,
  hashes, and unresolved checks.
- `NEXT_STEPS.md`: the next evidence to collect and the official patching guide.

Existing output directories are refused. Failed copying does not publish a
finished workspace. Hashes establish byte identity, not authenticity. Keep the
full firmware package and recovery instructions in addition to this one image;
this is not a device-wide backup or a tested rollback bundle.

## 4. Next milestone: validate and patch on the target

Before a device-specific flashing procedure can be written, confirm the exact
firmware match, partition and slot layout, bootloader access, Magisk ramdisk
assessment, and recovery route. Then patch a copy on the same target device,
retain the untouched stock copy, and inspect the patched image separately.
Follow [the official Magisk installation guide](https://topjohnwu.github.io/Magisk/install.html).

This first milestone does not choose a Magisk release, download or install it,
patch AVB, generate flash commands, or change the MCU. Those actions require
the actual unit's evidence; this workbench provides the intake for that work.

Once root and recovery are verified, the next useful deliverables are a
device-specific, reversible systemless module for customization and a hardware
acceptance check covering audio, volume controls, sleep/wake and camera behavior.

## Existing debloat journal compatibility

The repaired restore command retains failed operations and supports retrying
partial recovery. Old journals may contain false-success restore records from
the earlier implementation; these cannot be identified automatically. Inspect
the actual package state if recovering an older run. Explicit `--package`
restoration now requires a pending journal entry; manual changes and missing APKs
need separate recovery. The experimental Magisk boot scripts are not part of
the validated workflow.

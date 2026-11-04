# desjoyed-sanity

Stock-firmware customization workbench for FYT-lineage Android head units,
with a reversible debloat toolkit. The first bootloader milestone collects
device evidence, inspects boot images, and preserves stock images before
any root or firmware work.

`boottool.py` reads the device and prepares local files; it never flashes,
unlocks, roots, or reboots. `sanity.py` changes package state and journals
those changes. Disables can be reversed; uninstall recovery requires the
package APK to remain available on the device.

## Start with the bootloader workbench

The chosen direction is stock firmware plus root and systemless customization,
preserving the vendor hardware support. An unlocked bootloader permits custom
images; it does not identify which image, partition, or slot to use.

```bash
# Collect a read-only device snapshot; add --serial SERIAL if needed
python boottool.py probe -o device.json

# Inspect an image extracted from the exact unit's stock firmware
python boottool.py image firmware/boot.img --json

# Preserve that explicitly selected input in a new local workspace
python boottool.py prepare --device device.json --image firmware/boot.img --partition boot -o workspaces/stock
```

`boot` is an example selection, not an automatic recommendation. Depending on
the actual device, the ramdisk may be in `boot`, `init_boot`, `vendor_boot`, or
`recovery`. The workspace contains the original bytes, a `manifest.json`, and
`NEXT_STEPS.md`. Preparation does **not** certify compatibility or generate
flashing commands. See [the workflow](docs/boot-workflow.md).

## What it does

- **Recon** the device: bootloader state, build keys, installed packages,
  known malware/telemetry hits
- **Triage** packages into a human-editable plan using curated lists
- **Apply** the plan: `pm disable-user` (or `pm uninstall -k --user 0`)
- **Restore** anything that breaks, one package or all at once
- **Inspect** firmware ZIPs (`update_car.zip`): signature, metadata,
  contents
- **Magisk boot scripts**: hosts-based blocking, ADB security fix,
  analytics opt-out

## Hardware target

The original target is the **Joying JY-UTS18N12** (QCM6490, Android 13,
12GB/256GB). The new workbench has synthetic test coverage, but no real device
or stock image has yet been validated in this development session. Treat
other models as separate compatibility investigations.
The `never.txt` protection list covers both FYT/SYU and MTC/Microntek
MCU bridges.

## Debloat workflow

```bash
# 1. Connect via ADB (USB-A to USB-A, toggle USB role to Device)
#    See docs/adb-setup.md for details
adb devices

# 2. See what you have
python3 sanity.py recon

# 3. Generate a plan
python3 sanity.py triage

# 4. Edit the plan (comment/uncomment lines)
#    The plan file is in state/<device-hash>/plan.txt

# 5. Apply it
python3 sanity.py apply

# 6. Reboot and test
adb reboot

# 7. Something broke? Undo the last one
python3 sanity.py restore --last 1
```

## List tiers

| File | Default | What |
|---|---|---|
| `known-bad.txt` | on | Published malware/telemetry (Adups, Redstone, DoFun) |
| `google-junk.txt` | on | Google side-cars that nag or report |
| `aosp.txt` | on | AOSP leftovers useless on a head unit |
| `aosp-apps.txt` | off | Stock AOSP apps (replace with FOSS first) |
| `google.txt` | off | Full de-Google (read docs/foss-stack.md first) |
| `projection.txt` | off | CarPlay / Android Auto / mirroring |
| `vehicle.txt` | off | CAN-bus, steering wheel learning |
| `vendor.txt` | off | FYT radio app, community tools |
| `qualcomm.txt` | off | Qualcomm QTI side-cars |

**on** = triage enables them by default.
**off** = triage comments them out; uncomment what you want.

`never.txt` is an absolute protection layer. Explicit tier entries take
precedence over `guard.txt`; otherwise guard matches require `force:`.

## Safety rails

1. **never.txt**: hard deny. The tool refuses to touch Android core
   components, the MCU bridge (`com.syu.ms`, `com.syu.air`), or
   anything else that would brick, silence, or strand the unit.

2. **guard.txt**: regex patterns for vendor namespaces and vehicle/audio
   keywords. Matched packages appear in the plan commented out with a
   `force:` prefix. You have to explicitly uncomment them and keep the
   `force:` prefix for `apply` to process them.

3. **Journal**: actions are logged to `state/<device-hash>/journal.jsonl`
   with timestamps. `restore --all` retries pending operations; failed restores
   stay pending. `restore --last N` selects the most recent still-pending packages.

## Firmware tools

```bash
# Inspect a firmware zip
python3 fwtool.py inspect update_car.zip

# List all entries
python3 fwtool.py inspect update_car.zip --list-all

# Strip bloatware APKs from a firmware zip
python3 fwtool.py strip update_car.zip -o clean.zip --remove 'owner_apk/.*'

# Check the zip signature
python3 fwtool.py sigcheck update_car.zip
```

See [docs/firmware.md](docs/firmware.md) for details on the firmware
format, signing, and flashing.

## Experimental Magisk boot scripts

The original scripts in `magisk/service.d/` need a separate device-tested
revision before deployment. `20-adb-secure.sh` does not restart an already-running
ADB daemon, and `10-hosts.sh` skips its blocklist when the hosts file exists.
The workbench does not install them. Changing a security property alone is not
evidence that ADB now requires authentication.

| Script | What |
|---|---|
| `10-hosts.sh` | Block telemetry domains via /etc/hosts |
| `20-adb-secure.sh` | Experimental ADB authentication property change |
| `30-disable-analytics.sh` | Opt out of usage stats and crash reporting |

## Docs

- [docs/boot-workflow.md](docs/boot-workflow.md) — device evidence, stock images, and next steps toward root
- [docs/adb-setup.md](docs/adb-setup.md) — ADB connection, wireless ADB, USB role toggle
- [docs/foss-stack.md](docs/foss-stack.md) — FOSS replacements to install before debloating
- [docs/firmware.md](docs/firmware.md) — Firmware format, signing, flashing, Magisk
- [docs/why-not-gsi.md](docs/why-not-gsi.md) — Why a GSI is not worth it on a head unit

## Requirements

- Python 3.10+
- ADB (Android SDK platform-tools)
- An FYT-lineage Android head unit with USB or wireless ADB access

Offline image inspection and preparation do not require ADB. Tests use synthetic
images and simulated device responses:

```bash
python -m unittest discover -v
```

## License

MIT

# desjoyed-sanity

Reversible debloat toolkit for FYT-lineage Android head units (Joying,
Joyforwa, Eonon, Dasaita, and other QCM6490/SM6375/T7862 units that
run FYT/SYU firmware).

**Nothing touches the system partition.** Every disable is reversible
with `pm enable` or `pm install-existing`. The toolkit records what it
does in a per-device journal so you can undo any step.

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

Developed for the **Joying JY-UTS18N12** (QCM6490, Android 13,
12GB/256GB) but the lists and logic apply to any FYT-lineage unit.
The `never.txt` protection list covers both FYT/SYU and MTC/Microntek
MCU bridges.

## Quick start

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

`never.txt` and `guard.txt` are not tiers; they are protection and
caution layers that override everything else.

## Safety rails

1. **never.txt**: hard deny. The tool refuses to touch Android core
   components, the MCU bridge (`com.syu.ms`, `com.syu.air`), or
   anything else that would brick, silence, or strand the unit.

2. **guard.txt**: regex patterns for vendor namespaces and vehicle/audio
   keywords. Matched packages appear in the plan commented out with a
   `force:` prefix. You have to explicitly uncomment them and keep the
   `force:` prefix for `apply` to process them.

3. **Journal**: every action is logged to `state/<device-hash>/journal.jsonl`
   with timestamps. `restore --all` undoes everything in the journal.

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

## Magisk boot scripts

Copy the scripts from `magisk/service.d/` to `/data/adb/service.d/`
on the head unit:

```bash
adb push magisk/service.d/*.sh /data/adb/service.d/
adb shell chmod 755 /data/adb/service.d/*.sh
```

| Script | What |
|---|---|
| `10-hosts.sh` | Block telemetry domains via /etc/hosts |
| `20-adb-secure.sh` | Fix the open-ADB-to-everyone problem |
| `30-disable-analytics.sh` | Opt out of usage stats and crash reporting |

## Docs

- [docs/adb-setup.md](docs/adb-setup.md) — ADB connection, wireless ADB, USB role toggle
- [docs/foss-stack.md](docs/foss-stack.md) — FOSS replacements to install before debloating
- [docs/firmware.md](docs/firmware.md) — Firmware format, signing, flashing, Magisk
- [docs/why-not-gsi.md](docs/why-not-gsi.md) — Why a GSI is not worth it on a head unit

## Requirements

- Python 3.10+
- ADB (Android SDK platform-tools)
- An FYT-lineage Android head unit with USB or wireless ADB access

## License

MIT

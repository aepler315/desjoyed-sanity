# Firmware notes (FYT QCM6490)

Start with [the boot workbench workflow](boot-workflow.md). The structures below
are examples from the original project, not a verified description of every
FYT package. Confirm them against the unit's actual stock firmware.

## Firmware zip format

FYT firmware is distributed as `560-6490-YYYYMMDD.zip`, containing an
inner `update_car.zip`. The outer zip is just a container; the inner zip
is what the updater processes.

### update_car.zip structure

```
META-INF/
  com/google/android/
    updater-script      edify script (AOSP recovery format)
    update-binary       the updater binary
config_file/
  CarServicePolicy.db   FYT policy database
  ...
owner_apk/
  SomeApp.apk           vendor apps installed during update
  ...
boot.img                boot partition image
system.img              (or system.new.dat / system.transfer.list)
...
```

### Signature

The zip uses Android's whole-file v1 signature format:
- PKCS#7 detached signature appended after the EOCD record
- Comment field: `signed by SignApk\0` + PKCS#7 DER + 6-byte footer
- Whether a particular updater trusts the AOSP testkey must be verified for
  that firmware. A recognizable ZIP comment is not cryptographic verification.

Use `fwtool.py sigcheck` to examine the signature, and
`fwtool.py inspect` to list contents.

### Anti-downgrade

The updater-script typically contains a timestamp check:
```
assert(less_than_int(getprop("ro.build.date.utc"), "1700000000"))
```
This prevents flashing older firmware. The check is in the edify script,
not enforced by the bootloader.

## Flashing

Use the exact unit's vendor procedure and matching firmware. USB update behavior,
accepted package layout, data wiping, fastboot support and recovery differ by
unit. The workbench does not execute flashing or provide a generic command as a
device-specific procedure.

## Modifying firmware

Use `fwtool.py strip` to remove entries (e.g., bloatware APKs from
`owner_apk/`). The output zip will not be signed; if the updater
checks the signature, a whole-file signature from a key that updater trusts is
required. For AOSP SignApk, whole-file signing uses `-w`:

```bash
# Build signapk from AOSP source, or use the one from
# https://github.com/nickel-coder/signapk (jar + keys)
java -jar signapk.jar -w \
  aosp-testkey.x509.pem aosp-testkey.pk8 \
  stripped.zip signed.zip
```

The AOSP testkey is public. A package signed with it is not thereby compatible,
trusted by a particular unit, or safe to flash. See
[AOSP OTA signing](https://source.android.com/docs/core/ota/sign_builds).

## Boot image

Use `python boottool.py image IMAGE --json` to distinguish Android boot and
vendor boot formats. Their header fields differ; inspecting a hard-coded offset
without checking the image magic is insufficient. AVB metadata markers do not
prove signature validity. The tool does not modify vbmeta or infer that a
patched image will boot merely because the bootloader reports unlocked.

## Magisk

Patch the correct stock image on the same target device, after identifying its
ramdisk layout. The appropriate image can be `boot`, `init_boot`, `vendor_boot`,
or `recovery`; an Android version label does not decide this. Preserve the stock
image and establish a recovery procedure before reviewing any flash command.
See [Magisk installation](https://topjohnwu.github.io/Magisk/install.html).

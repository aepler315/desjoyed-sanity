# Firmware notes (FYT QCM6490)

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
- The FYT updater trusts the **AOSP testkey** (the default key that
  ships with the AOSP source tree). This is not a security mechanism.

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

1. Download the firmware zip (outer zip)
2. Extract `update_car.zip` from it (do NOT unzip update_car.zip itself)
3. Copy `update_car.zip` to a FAT32-formatted USB drive (under 32 GB)
4. Insert the USB drive into the head unit's USB port
5. The updater should detect it and offer to flash
6. Flashing wipes user data

## Modifying firmware

Use `fwtool.py strip` to remove entries (e.g., bloatware APKs from
`owner_apk/`). The output zip will not be signed; if the updater
checks the signature, re-sign with the AOSP testkey:

```bash
# Build signapk from AOSP source, or use the one from
# https://github.com/nickel-coder/signapk (jar + keys)
java -jar signapk.jar \
  aosp-testkey.x509.pem aosp-testkey.pk8 \
  stripped.zip signed.zip
```

The AOSP testkey is public. Using it does not add security; it satisfies
the format check.

## Boot image

The boot image uses the standard Android boot image header. Check
`header_version` at offset 40 to know the format. AVB (Android Verified
Boot) metadata may be present as a footer (magic `AVBf` in the last 64
bytes) and in vbmeta (magic `AVB0`). With the bootloader unlocked and
vbmeta patched (flags at offset 120), the chain of trust is already
broken, so Magisk patching works.

## Magisk

To root: extract `boot.img` from `update_car.zip`, transfer to a phone
or the head unit itself, patch with Magisk Manager, flash the patched
image via fastboot.

```bash
adb push boot.img /sdcard/Download/
# Patch in Magisk Manager: Install > Select and Patch a File > boot.img
adb pull /sdcard/Download/magisk_patched-*.img
fastboot flash boot magisk_patched-*.img
fastboot reboot
```

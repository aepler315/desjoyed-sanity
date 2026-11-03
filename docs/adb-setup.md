# ADB setup for FYT head units

## USB connection

FYT units use a USB-A to USB-A cable for ADB. The connection goes
through the same OTG port you use for USB drives.

### USB role toggle

The head unit defaults to USB host mode (for drives, dongles). To use
ADB, switch to device/peripheral mode:

1. Settings > developer password: **2888** (or 8888, 126 on older units)
2. Developer options > USB role: **Device** (or "Peripheral")
3. Connect the USB-A to USB-A cable

The USB role resets to Host on every reboot. If you reboot and ADB
disappears, toggle it back.

### Verify the connection

```bash
adb devices
# Should show your device serial
```

## Wireless ADB

USB cables across the dash are annoying. Switch to wireless ADB:

```bash
# Over USB first:
adb tcpip 5555
adb connect <head-unit-ip>:5555
```

The head unit's IP is in Settings > Wi-Fi > your network > IP address.

### Persist wireless ADB across reboots

With Magisk root, add to a boot script:

```bash
# magisk/service.d/40-adb-tcpip.sh
setprop service.adb.tcp.port 5555
stop adbd
start adbd
```

### Security warning

FYT units ship with `ro.adb.secure=0`, which means ADB accepts
connections from anyone on the network without the RSA authorization
prompt. The `20-adb-secure.sh` Magisk script fixes this. After
applying it, you will need to accept the RSA key dialog once from
your computer.

## Using sanity.py over ADB

```bash
# Default: uses `adb` from PATH
python3 sanity.py recon

# Custom adb binary
SANITY_ADB=/path/to/adb python3 sanity.py recon

# Specific device serial
python3 sanity.py -s SERIAL recon

# Or set the standard env var
ANDROID_SERIAL=SERIAL python3 sanity.py recon
```

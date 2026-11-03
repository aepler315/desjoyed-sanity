# Why not a GSI?

A GSI (Generic System Image) replaces the vendor's `/system` with a
stock AOSP or custom ROM system partition. On a phone this is a
reasonable path to a clean Android. On a head unit it trades one set
of problems for a harder set.

## What breaks

The MCU bridge (`com.syu.ms`) is a vendor app that talks to the STM32
co-processor over a serial or I2C bus. It handles:

- ACC power on/off (sleep/wake)
- Hardware volume knob and steering wheel buttons
- Audio source switching (the TDA7850 amp)
- Backlight and illumination control
- Reverse camera trigger

A GSI does not include this app or its native libraries. Without it,
the head unit is a tablet that never sleeps, has no volume control,
and cannot switch audio sources.

## What you would need to fix

1. Extract `com.syu.ms`, `com.syu.air`, and their native `.so` files
   from the stock system image
2. Re-sign them (or disable signature verification in the GSI)
3. Install them as system apps with the correct permissions
4. Hope the SELinux policy does not block their IPC
5. Repeat for every vendor firmware update

## DSU: the safe alternative

DSU (Dynamic System Updates) lets you boot a GSI **without flashing**:

```
adb shell am start-activity \
  -a android.os.image.action.START_INSTALL \
  -d "file:///sdcard/system.img" \
  --el KEY_SYSTEM_SIZE $(stat -c%s system.img)
```

The stock system stays intact. Reboot to return to stock. This is the
right way to test-drive a GSI if you are curious.

The DSU loader app (`com.android.dynsystem`) is in the never-disable
list for exactly this reason.

## The realistic path

Keep the stock system. Use Magisk for root. Use sanity.py for
reversible debloating. Use a firewall (NetGuard or AFWall+) and DNS
(Quad9, AdGuard) for network control. Replace the user-facing apps
with FOSS alternatives. The vendor plumbing stays, but it is muzzled.

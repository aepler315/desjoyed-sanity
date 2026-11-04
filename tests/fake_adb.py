#!/usr/bin/env python3
"""
Fake adb that returns canned responses for testing sanity.py.

Behavior is controlled by the FAKE_ADB_SCENARIO env var:
  - "default": a stock FYT unit with typical packages
  - "empty":   a unit with minimal packages
  - "malware": a unit with known-bad packages

The script mimics `adb shell <cmd>` and `adb devices`.
"""
import os
import sys

SCENARIO = os.environ.get("FAKE_ADB_SCENARIO", "default")

PROPS_DEFAULT = """\
[ro.build.version.release]: [13]
[ro.build.version.sdk]: [33]
[ro.product.model]: [JY-UTS18N12]
[ro.product.brand]: [Joying]
[ro.product.device]: [msmnile]
[ro.hardware]: [qcom]
[ro.board.platform]: [lahaina]
[ro.build.type]: [userdebug]
[ro.build.tags]: [test-keys]
[ro.build.display.id]: [560-6490-20240101]
[ro.serialno]: [FAKE0001]
[ro.boot.verifiedbootstate]: [orange]
[ro.boot.flash.locked]: [0]
[ro.boot.vbmeta.device_state]: [unlocked]
[ro.adb.secure]: [0]
"""

PACKAGES_DEFAULT = """\
package:/system/app/Easter.apk=com.android.egg versionCode:1 uid:10001
package:/system/app/PrintSpooler.apk=com.android.printspooler versionCode:1 uid:10002
package:/system/priv-app/GmsCore.apk=com.google.android.gms versionCode:230000 uid:10100
package:/system/priv-app/GoogleQuickSearchBox.apk=com.google.android.googlequicksearchbox versionCode:140000 uid:10101
package:/system/app/Feedback.apk=com.google.android.feedback versionCode:1 uid:10102
package:/system/app/PartnerSetup.apk=com.google.android.partnersetup versionCode:1 uid:10103
package:/system/priv-app/SystemUI.apk=com.android.systemui versionCode:33 uid:10010
package:/system/priv-app/Settings.apk=com.android.settings versionCode:33 uid:1000
package:/system/priv-app/SyuMainService.apk=com.syu.ms versionCode:1 uid:1000
package:/system/priv-app/SyuAir.apk=com.syu.air versionCode:1 uid:1000
package:/system/app/SyuRadio.apk=com.syu.radio versionCode:1 uid:10200
package:/system/app/SyuCanbus.apk=com.syu.canbus versionCode:1 uid:10201
package:/system/app/SyuSteer.apk=com.syu.steer versionCode:1 uid:10202
package:/system/app/SyuCarlink.apk=com.syu.carlink versionCode:1 uid:10203
package:/system/app/QtiDevStats.apk=com.qualcomm.qti.devicestatisticsservice versionCode:1 uid:10300
package:/data/app/org.fdroid.fdroid/base.apk=org.fdroid.fdroid versionCode:1019 uid:10400
package:/system/app/AdupsFota.apk=com.adups.fota versionCode:1 uid:10500
package:/system/app/Chrome.apk=com.android.chrome versionCode:120 uid:10600
package:/system/priv-app/CarLauncher.apk=com.car.launcher versionCode:1 uid:10700
package:/data/app/app.lawnchair/base.apk=app.lawnchair versionCode:14 uid:10800
"""

PACKAGES_MALWARE = PACKAGES_DEFAULT + """\
package:/system/app/TWCore.apk=com.tw.core versionCode:1 uid:10700
package:/system/app/JarService.apk=com.tw.jar1 versionCode:1 uid:10701
"""

DISABLED_DEFAULT = """\
package:com.android.egg
"""

DUMPSYS_TEMPLATE = """\
Packages:
  Package [{pkg}] ({pkg_hash}):
    pkgFlags=[ SYSTEM HAS_CODE PERSISTENT_PREFERRED ]
    sharedUserId=0
    signatures:PackageSignatures{{deadbeef}}
    Signatures:
      deadbeef01020304
    requested permissions:
      android.permission.INTERNET
      android.permission.ACCESS_NETWORK_STATE
    User 0:  ceDataInode=12345 installed=true hidden=false suspended=false stopped=false notLaunched=false enabled=0
"""


def handle_shell(cmd: str):
    if cmd == "getprop":
        print(PROPS_DEFAULT)
    elif cmd.startswith("pm list packages"):
        if "-d" in cmd and "-f" not in cmd:
            print(DISABLED_DEFAULT)
        elif SCENARIO == "malware":
            print(PACKAGES_MALWARE)
        else:
            print(PACKAGES_DEFAULT)
    elif cmd.startswith("dumpsys package "):
        pkg = cmd.split()[-1]
        print(DUMPSYS_TEMPLATE.format(pkg=pkg, pkg_hash="abc123"))
    elif cmd.startswith("pm disable-user"):
        pkg = cmd.split()[-1]
        print(f"Package {pkg} new state: disabled-user")
    elif cmd.startswith("pm enable"):
        pkg = cmd.split()[-1]
        print(f"Package {pkg} new state: enabled")
    elif cmd.startswith("pm uninstall -k --user 0"):
        pkg = cmd.split()[-1]
        print("Success")
    elif cmd.startswith("pm install-existing"):
        pkg = cmd.split()[-1]
        print(f"Package {pkg} installed for user: 0")
    elif cmd.startswith("settings put"):
        pass
    elif cmd.startswith("settings get"):
        key = cmd.split()[-1]
        if key == "private_dns_specifier":
            print("dns.quad9.net")
        else:
            print("null")
    elif cmd.startswith("cmd role get-role-holders"):
        print("com.car.launcher")
    elif cmd.startswith("cmd role add-role-holder"):
        pass
    elif cmd.startswith("cmd package resolve-activity"):
        parts = cmd.split()
        pkg = parts[-1] if parts else "unknown"
        print(f"  name={pkg}/{pkg}.HomeActivity")
    elif cmd.startswith("cmd package set-home-activity"):
        print("Activity set as home.")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if not args:
        print("Android Debug Bridge", file=sys.stderr)
        sys.exit(1)

    while args and args[0] == "-s":
        args = args[2:]

    if not args:
        sys.exit(0)

    if args[0] == "devices":
        print("List of devices attached")
        print("FAKE0001\tdevice")
    elif args[0] == "shell":
        cmd = " ".join(args[1:])
        handle_shell(cmd)
    else:
        print(f"Unhandled adb command: {args}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

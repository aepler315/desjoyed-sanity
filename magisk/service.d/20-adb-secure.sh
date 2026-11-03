#!/system/bin/sh
# On FYT units ro.adb.secure=0 is baked into the build, meaning ADB
# accepts connections from anyone on the network with no auth prompt.
# This script sets the property at boot so that ADB requires RSA key
# authorization (the normal Android behavior).
#
# After this runs for the first time, you will need to re-authorize
# your computer by accepting the RSA fingerprint dialog.

resetprop ro.adb.secure 1

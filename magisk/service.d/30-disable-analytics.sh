#!/system/bin/sh
# Disable various analytics and crash-reporting settings at boot.
# These are the settings-database equivalents of things you toggle
# in the UI, persisted here so a factory reset does not undo them.

settings put global device_provisioned 1

# Disable usage stats collection
settings put global netstats_enabled 0

# Opt out of Google usage & diagnostics
settings put global send_action_app_error 0

# Disable crash dialog (you still see logcat)
settings put global always_finish_activities 0
settings put secure send_action_app_error 0

# Disable captive portal detection (it phones home to connectivitycheck.gstatic.com)
# Uncomment if you don't need captive portal detection on Wi-Fi:
# settings put global captive_portal_mode 0

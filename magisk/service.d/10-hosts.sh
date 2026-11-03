#!/system/bin/sh
# Block telemetry and ad domains at the OS level via /etc/hosts.
# Magisk mounts this over the real /system/etc/hosts.
#
# Requires: Magisk systemless hosts module enabled in Magisk Manager.
# This script just ensures the file exists and has the baseline entries.

HOSTS="/data/adb/modules/hosts/system/etc/hosts"

[ -d "$(dirname "$HOSTS")" ] || exit 0

if [ ! -f "$HOSTS" ]; then
  cat > "$HOSTS" <<'EOF'
127.0.0.1 localhost
::1 localhost

# --- FYT/LSEC update & telemetry ---
127.0.0.1 apk.ls-ec.com
127.0.0.1 api.ls-ec.com
127.0.0.1 ota.ls-ec.com
127.0.0.1 log.ls-ec.com

# --- Adups ---
127.0.0.1 bigdata.adups.com
127.0.0.1 rebootv5.adups.com
127.0.0.1 fota5.adups.com

# --- Qualcomm XTRA / IZat (if you firewall com.qualcomm.location instead, remove these) ---
127.0.0.1 xtrapath1.izatcloud.net
127.0.0.1 xtrapath2.izatcloud.net
127.0.0.1 xtrapath3.izatcloud.net

# --- DoFun / TW (if present) ---
127.0.0.1 cdn.lfota.com
127.0.0.1 api.lfota.com
EOF
  chmod 644 "$HOSTS"
  chown 0:0 "$HOSTS"
fi

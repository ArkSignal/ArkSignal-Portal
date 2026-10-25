#!/bin/sh
# Starts the ArkSignal mesh broker (Mosquitto with the dynamic security plugin). Every login is made and removed by the mesh service through the plugin's control topic, so members get their own
# login from the portal and nobody types a command. Runs as root only to prepare the files; mosquitto itself drops to its own user.
#
#   1883  internal listener (never published): the mesh service reads and manages the broker here
#   8883  TLS listener for nodes: it starts by itself once a certificate is in /mosquitto/config/certs (fullchain.pem and privkey.pem)
#
# The certificate is found by itself: /letsencrypt is the certificate store of the site's proxy (Nginx Proxy Manager, mounted read-only); the newest certificate that covers $MESH_CERT_HOST is
# copied to certs/, and again after every renewal (checked every few hours): a renewal reloads the broker, the first certificate restarts it so the TLS listener opens. Certificates put in certs/ by hand keep working.
# /mosquitto/config is a volume (/opt/adsb/mesh/mqtt on the host): admin.pass (the control login the mesh service reads), dynsec.json (the logins and roles), certs/.
C=/mosquitto/config
# The folder itself must belong to mosquitto: the plugin saves the logins by writing dynsec.json.new beside dynsec.json and renaming it. A folder made by Docker on the host (a bind mount) belongs to root,
# and then every save failed ("Error saving Dynamic security plugin config ... Permission denied"): logins made while the broker ran were lost at the next restart (an update, a crash) and nodes were refused (NOT_AUTHORIZED) until Apply sent them again.
chown mosquitto:mosquitto "$C" 2> /dev/null
mkdir -p "$C/certs"
if [ ! -s "$C/admin.pass" ]; then
  head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 32 > "$C/admin.pass"
fi
chmod 644 "$C/admin.pass"
if [ ! -s "$C/dynsec.json" ]; then
  mosquitto_ctrl dynsec init "$C/dynsec.json" mesh-admin "$(cat "$C/admin.pass")" > /dev/null
fi
chown mosquitto:mosquitto "$C"/dynsec.json* 2> /dev/null
HOST="${MESH_CERT_HOST:-}"
LE="${LETSENCRYPT_DIR:-/letsencrypt/live}"
sync_cert() {
  [ -n "$HOST" ] && [ -d "$LE" ] || return 1
  best=""; bestend=0
  for d in "$LE"/*/; do
    f="$d/fullchain.pem"; [ -s "$f" ] && [ -s "$d/privkey.pem" ] || continue
    openssl x509 -in "$f" -noout -checkhost "$HOST" 2> /dev/null | grep -q "does match" || continue
    openssl x509 -in "$f" -noout -checkend 86400 > /dev/null 2>&1 || continue
    end=$(date -d "$(openssl x509 -in "$f" -noout -enddate | cut -d= -f2)" +%s 2> /dev/null || echo 1)
    if [ "$end" -gt "$bestend" ]; then best="$d"; bestend="$end"; fi
  done
  [ -n "$best" ] || return 1
  if ! cmp -s "$best/fullchain.pem" "$C/certs/fullchain.pem" 2> /dev/null || ! cmp -s "$best/privkey.pem" "$C/certs/privkey.pem" 2> /dev/null; then
    cp -L "$best/fullchain.pem" "$C/certs/fullchain.pem.new" && cp -L "$best/privkey.pem" "$C/certs/privkey.pem.new" || return 1
    chmod 644 "$C/certs/fullchain.pem.new"; chmod 640 "$C/certs/privkey.pem.new"; chgrp mosquitto "$C/certs/privkey.pem.new" 2> /dev/null; chown mosquitto "$C/certs/privkey.pem.new" 2> /dev/null
    mv "$C/certs/fullchain.pem.new" "$C/certs/fullchain.pem"; mv "$C/certs/privkey.pem.new" "$C/certs/privkey.pem"
    return 0
  fi
  return 2
}
HAD_TLS=0; [ -s "$C/certs/fullchain.pem" ] && HAD_TLS=1
sync_cert; [ -s "$C/certs/privkey.pem" ] && chown mosquitto "$C/certs/privkey.pem" 2> /dev/null
[ -s "$C/certs/fullchain.pem" ] && HAD_TLS=1
(
  while true; do
    sleep "${CERT_CHECK_SECONDS:-21600}"
    sync_cert; r=$?
    if [ "$r" = 0 ]; then
      if [ "$HAD_TLS" = 1 ]; then kill -HUP 1; else kill -TERM 1; fi
    fi
  done
) &
GEN="$C/mosquitto.generated.conf"
{
  echo "per_listener_settings false"
  echo "allow_anonymous false"
  echo "persistence false"
  echo "plugin /usr/lib/mosquitto_dynamic_security.so"
  echo "plugin_opt_config_file $C/dynsec.json"
  echo "message_size_limit 8192"   # a Meshtastic packet is a few hundred bytes: nothing larger is carried
  echo "max_keepalive 1800"
  echo "listener 1883"
  if [ -s "$C/certs/fullchain.pem" ] && [ -s "$C/certs/privkey.pem" ]; then
    echo "listener 8883"
    echo "protocol mqtt"
    echo "max_connections 500"   # a listener option: only the nodes' port is capped
    echo "certfile $C/certs/fullchain.pem"
    echo "keyfile $C/certs/privkey.pem"
  fi
} > "$GEN"
if [ -s "$C/mosquitto.custom.conf" ]; then CONF="$C/mosquitto.custom.conf"; else CONF="$GEN"; fi
exec mosquitto -c "$CONF"

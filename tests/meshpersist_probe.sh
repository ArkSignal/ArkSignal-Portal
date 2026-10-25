#!/bin/sh
P=$(cat /mosquitto/config/admin.pass)
C="mosquitto_ctrl -u mesh-admin -P $P"
case "$1" in
  add) $C dynsec createClient probe-login -p probe-pass-1 ;;
  list) $C dynsec listClients | tr '\n' ' ' ;;
  del) $C dynsec deleteClient probe-login ;;
esac

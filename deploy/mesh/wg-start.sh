#!/bin/sh
# The ArkSignal private tunnel (request 205): a WireGuard server whose only job is to give gateways a private address to reach the private broker.
# Why: the Meshtastic firmware skips its OK to MQTT test only when the broker address is private (10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10). A gateway inside this tunnel connects to the
# tunnel address (10.77.0.1 by default), which the firmware sees as private, and nothing else is reachable from the tunnel:
#   * ip_forward stays off, so a peer cannot reach another peer, this container's other networks or the internet through it;
#   * the one thing listening on the tunnel address is a TCP relay from port 1884 to the broker's private listener (mesh-mqtt:1884), and the firewall lets nothing else in from the tunnel.
# The portal (through the mesh service) writes /wg/server.key, /wg/peers.json (each peer's address and the login it may use) and /wg/relay.key; this script applies them, checks every value before use, and writes /wg/status.txt (peer, last handshake, bytes) for the portal.
W=/wg
IF=wg0
ADDR="${WG_ADDRESS:-10.77.0.1}"
NET="${ADDR%.*}"
PORT="${WG_PORT:-51820}"
chown 10005:10005 "$W" 2> /dev/null; chmod 755 "$W" 2> /dev/null   # the mesh service (user 10005) writes the key and the peer list here
until [ -s "$W/server.key" ]; do echo "waiting for the portal to send the tunnel key"; sleep 5; done
ip link add "$IF" type wireguard || { echo "this host has no WireGuard kernel support"; sleep 300; exit 1; }
ip addr add "$ADDR/24" dev "$IF"
ip link set "$IF" mtu 1380 up
wg set "$IF" listen-port "$PORT" private-key "$W/server.key"
# nothing is forwarded (forwarding is off and the FORWARD chain drops everything), and from the tunnel only the relay's port answers
(echo 0 > /proc/sys/net/ipv4/ip_forward) 2> /dev/null
iptables -P FORWARD DROP
iptables -A INPUT -i "$IF" -p tcp --dport 1884 -j ACCEPT
iptables -A INPUT -i "$IF" -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -i "$IF" -j DROP
# defense in depth: nothing may be routed in or out of the tunnel whatever a policy says (a peer cannot reach another peer, this container's other networks or the internet), and nothing at all over IPv6
iptables -A FORWARD -i "$IF" -j DROP
iptables -A FORWARD -o "$IF" -j DROP
ip6tables -P FORWARD DROP 2> /dev/null
ip6tables -A INPUT -i "$IF" -j DROP 2> /dev/null
ip6tables -A FORWARD -i "$IF" -j DROP 2> /dev/null
ip6tables -A FORWARD -o "$IF" -j DROP 2> /dev/null
# one peer may hold only a few connections to the relay (it forks one process for each)
iptables -I INPUT -i "$IF" -p tcp --dport 1884 -m connlimit --connlimit-above 20 --connlimit-mask 32 -j DROP 2> /dev/null || true
# the relay reads each connection's MQTT CONNECT packet: a peer may use only the login the portal bound to its address, and the password is turned into one only the relay and the portal can make
WG_ADDRESS="$ADDR" python3 /wg-relay.py &
KEY_RE='^[A-Za-z0-9+/]{43}=$'
last=""
while true; do
  if [ -s "$W/peers.json" ]; then
    sig=$(cksum "$W/peers.json" "$W/server.key" | tr '\n' ' ')
    if [ "$sig" != "$last" ]; then
      wg set "$IF" private-key "$W/server.key" 2> /dev/null
      want=""
      for row in $(jq -r '.peers[]? | "\(.key),\(.ip)"' "$W/peers.json" 2> /dev/null); do
        key="${row%,*}"; ip="${row#*,}"
        echo "$key" | grep -Eq "$KEY_RE" || continue
        echo "$ip" | grep -Eq "^$NET\.([2-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-4])$" || continue
        wg set "$IF" peer "$key" allowed-ips "$ip/32" && want="$want $key"
      done
      for have in $(wg show "$IF" peers); do
        case " $want " in *" $have "*) ;; *) wg set "$IF" peer "$have" remove ;; esac
      done
      last="$sig"
    fi
  fi
  wg show "$IF" dump | tail -n +2 | awk '{print $1, $5, $6, $7}' > "$W/status.txt.new" 2> /dev/null && mv "$W/status.txt.new" "$W/status.txt"
  sleep 10
done

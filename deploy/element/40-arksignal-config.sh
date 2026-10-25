#!/bin/sh
# Element's configuration comes from the portal: fetched now, and again every CONFIG_REFRESH seconds (the site's settings change the chat without a new image). If the portal does not answer at start, a plain
# configuration for MATRIX_BASE_URL and MATRIX_SERVER_NAME is used until it does, never Element's built-in default (which points at matrix.org).
D=/tmp/element-web-config
mkdir -p "$D"
fetch() {
  wget -q -T 8 -O "$D/config.new" "$CONFIG_URL" && [ -s "$D/config.new" ] && mv "$D/config.new" "$D/config.json"
}
if ! fetch; then
  cat > "$D/config.json" <<JSON
{"default_server_config":{"m.homeserver":{"base_url":"${MATRIX_BASE_URL}","server_name":"${MATRIX_SERVER_NAME}"}},"disable_custom_urls":true,"disable_guests":true,"brand":"ArkSignal","sso_redirect_options":{"immediate":true}}
JSON
fi
( while true; do sleep "${CONFIG_REFRESH:-300}"; fetch; done ) >/dev/null 2>&1 &

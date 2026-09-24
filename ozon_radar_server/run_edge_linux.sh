#!/bin/sh
set -eu

PROFILE="${OZON_EDGE_PROFILE:-$HOME/.ozon-edge-chromium}"
DISPLAY="${DISPLAY:-:99}"
CHROME="${CHROME_BIN:-}"

if [ -z "$CHROME" ]; then
  for c in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$c" >/dev/null 2>&1; then CHROME="$(command -v "$c")"; break; fi
  done
fi
[ -n "$CHROME" ] || { echo "Chromium/Chrome not found"; exit 1; }

mkdir -p "$PROFILE" /tmp/.X11-unix
if command -v Xvfb >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >/tmp/ozon-edge-xvfb.log 2>&1 &
fi
export DISPLAY

"$CHROME" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1920,1080 \
  "https://www.ozon.ru/" >/tmp/ozon-edge-chrome.log 2>&1 &

i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS http://127.0.0.1:9222/json/version >/dev/null 2>&1; then break; fi
  i=$((i+1)); sleep 1
done

exec python3 "$(dirname "$0")/edge_agent_linux.py"

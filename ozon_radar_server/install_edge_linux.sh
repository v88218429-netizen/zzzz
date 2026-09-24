#!/bin/sh
set -eu

BASE="${1:-$HOME/ozon-edge}"
mkdir -p "$BASE"
cd "$BASE"

command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
python3 -m pip install --user websocket-client

echo "Install Chromium, Xvfb and Tailscale with your distro package manager."
echo "Then run: $BASE/run_edge_linux.sh"
echo "Agent listens on port 8900 and Chromium CDP on localhost:9222."

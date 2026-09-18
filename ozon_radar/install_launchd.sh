#!/bin/bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/.ozon-radar"
APP="$BASE/app"
VENV="$BASE/venv"
LOGS="$BASE/logs"
PLIST="$HOME/Library/LaunchAgents/com.max.ozon-radar.plist"
LABEL="com.max.ozon-radar"
UID_NUM="$(id -u)"

mkdir -p "$APP" "$LOGS" "$HOME/Library/LaunchAgents"

rsync -a --delete   --exclude '__pycache__'   --exclude '*.pyc'   "$SRC/" "$APP/"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$APP/requirements.txt"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$APP/worker.py</string>
    <string>--once</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OZON_SHEET_ID</key>
    <string>1SHY1rz7XZeqOGkJSkitfSPlKs4cshS_5U63NSv0TO4c</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>ThrottleInterval</key>
  <integer>20</integer>
  <key>StandardOutPath</key>
  <string>$LOGS/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/stderr.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"

launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
if ! launchctl bootstrap "gui/$UID_NUM" "$PLIST"; then
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load -w "$PLIST"
fi

launchctl kickstart -k "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

echo "Ozon Radar installed: $LABEL"
echo "App: $APP"
echo "Logs: $LOGS"

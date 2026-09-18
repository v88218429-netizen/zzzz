#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/v88218429-netizen/zzzz"
RUNNER_DIR="$HOME/actions-runner-ozon"
RUNNER_NAME="ozon-live-mac"
RUNNER_LABELS="ozon-live"
RUNNER_VERSION="2.329.0"
TOKEN="${1:-${RUNNER_TOKEN:-}}"

echo "WB OS / Ozon self-hosted Mac runner bootstrap"

if [ -z "$TOKEN" ]; then
  cat <<'EOF'
Нужен одноразовый GitHub runner registration token.

GitHub:
  repo → Settings → Actions → Runners → New self-hosted runner → macOS

Скопируй только TOKEN из команды ./config.sh и запусти:
  bash ozon_live/setup_self_hosted_mac.sh 'TOKEN'

Токен не записывается в файл и не коммитится.
EOF
  exit 2
fi

python3 --version

if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  echo "Google Chrome not found in /Applications."
  exit 3
fi
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

ARCH="$(uname -m)"
case "$ARCH" in
  arm64)
    PKG="actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
    ;;
  x86_64)
    PKG="actions-runner-osx-x64-${RUNNER_VERSION}.tar.gz"
    ;;
  *)
    echo "Unsupported Mac architecture: $ARCH"
    exit 4
    ;;
esac

URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${PKG}"

if [ ! -x "./config.sh" ]; then
  echo "Downloading GitHub Actions runner $RUNNER_VERSION for $ARCH..."
  curl -fL --retry 3 --retry-delay 2 "$URL" -o "$PKG"
  tar xzf "$PKG"
  rm -f "$PKG"
fi

# Remove a stale local registration only when this directory was already
# configured. The current one-time registration token is used for both remove
# and configure; no credential is persisted by this bootstrap script.
if [ -f ".runner" ]; then
  echo "Replacing stale local runner registration..."
  ./config.sh remove --token "$TOKEN" || true
fi

echo "Registering runner..."
./config.sh \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work "_work" \
  --unattended \
  --replace

echo "Installing launchd service..."
./svc.sh install
./svc.sh start

sleep 2
./svc.sh status || true

echo ""
echo "Runner configured."
echo "Labels expected by workflows: self-hosted, macOS, $RUNNER_LABELS"
echo "Queued WB OS / Ozon jobs should start automatically."

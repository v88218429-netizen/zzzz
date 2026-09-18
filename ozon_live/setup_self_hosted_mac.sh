#!/bin/bash
set -euo pipefail

echo "Ozon Live self-hosted runner bootstrap"
echo "1) Requires Google Chrome and Python 3.11+"
python3 --version
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version

mkdir -p "$HOME/actions-runner-ozon"
cd "$HOME/actions-runner-ozon"

ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
  PKG="actions-runner-osx-arm64-2.329.0.tar.gz"
else
  PKG="actions-runner-osx-x64-2.329.0.tar.gz"
fi

echo ""
echo "Open GitHub: Settings → Actions → Runners → New self-hosted runner"
echo "Choose macOS, then copy the TOKEN from the ./config.sh command."
echo ""
echo "After you have the token, run:"
echo "  cd $HOME/actions-runner-ozon"
echo "  ./config.sh --url https://github.com/v88218429-netizen/zzzz --token YOUR_TOKEN --name ozon-live-mac --labels ozon-live --unattended"
echo "  ./svc.sh install"
echo "  ./svc.sh start"
echo ""
echo "The workflow is already configured for labels: self-hosted, macOS, ozon-live."

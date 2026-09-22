#!/bin/bash
set -euo pipefail

REPO="v88218429-netizen/zzzz"
WORKFLOW="ozon-appscript-hosted-deploy.yml"
CLASPRC="$HOME/.clasprc.json"

echo "Ozon radar · one-time hosted Apps Script authorization"
echo "======================================================"

if [ ! -s "$CLASPRC" ]; then
  echo "ERROR: $CLASPRC not found or empty."
  echo "Existing clasp authorization is required once."
  exit 2
fi

python3 - "$CLASPRC" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
obj=json.loads(p.read_text(encoding="utf-8"))
if not isinstance(obj,dict) or not obj:
    raise SystemExit("ERROR: invalid ~/.clasprc.json")
print("Existing clasp credential: OK")
PY

if ! command -v gh >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing GitHub CLI..."
    brew install gh
  else
    echo "ERROR: GitHub CLI is not installed and Homebrew is unavailable."
    exit 3
  fi
fi

if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "GitHub authorization will open once."
  gh auth login -h github.com -w
fi

echo "Saving existing clasp credential as encrypted GitHub Actions secret..."
gh secret set CLASPRC_JSON -R "$REPO" < "$CLASPRC"

echo "Triggering Mac-independent hosted Apps Script deploy..."
gh workflow run "$WORKFLOW" -R "$REPO" --ref mainggg

echo
echo "OK: authorization uploaded and hosted deploy triggered."
echo "Mac/self-hosted runner is no longer required for this deploy path."

#!/bin/bash
set -euo pipefail

REPO="v88218429-netizen/zzzz"
BRANCH="mainggg"
RAILWAY_PROJECT="believable-nourishment"
RAILWAY_SERVICE="ozon-radar-live"
RAILWAY_ENV="production"
SCRIPT_WORKFLOW="ozon-appscript-hosted-deploy.yml"
TEST_SKU="5094364543"
TEST_QUERY="лопата садовая"

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl not found"
command -v git >/dev/null || die "git not found"
command -v python3 >/dev/null || die "python3 not found"

say "Ozon LIVE radar · one-time cloud/home-exit setup"
echo "This setup creates a SEPARATE Railway service: $RAILWAY_SERVICE"
echo "Existing wb-bot is not modified."

# Railway CLI
if ! command -v railway >/dev/null 2>&1; then
  say "Installing Railway CLI"
  if command -v brew >/dev/null 2>&1; then
    brew install railway
  elif command -v npm >/dev/null 2>&1; then
    npm install -g @railway/cli
  else
    bash <(curl -fsSL railway.com/install.sh)
    export PATH="$HOME/.railway/bin:$PATH"
  fi
fi

if ! railway whoami >/dev/null 2>&1; then
  say "Railway login required once"
  railway login --browserless
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "Downloading current radar source"
git clone --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" "$TMP/repo" >/dev/null

cd "$TMP/repo"

say "Linking existing Railway project"
railway link --project "$RAILWAY_PROJECT" --environment "$RAILWAY_ENV" --json >/tmp/ozon-railway-link.json

SERVICES="$(railway service list --json 2>/dev/null || echo '[]')"
if ! SERVICES_JSON="$SERVICES" python3 - "$RAILWAY_SERVICE" <<'PY'
import json, os, sys
name=sys.argv[1]
try:
    obj=json.loads(os.environ.get("SERVICES_JSON","[]"))
except Exception:
    raise SystemExit(1)
def walk(x):
    if isinstance(x,dict):
        if str(x.get("name",""))==name:
            return True
        return any(walk(v) for v in x.values())
    if isinstance(x,list):
        return any(walk(v) for v in x)
    return False
raise SystemExit(0 if walk(obj) else 1)
PY
then
  say "Creating separate Railway service $RAILWAY_SERVICE"
  railway add --service "$RAILWAY_SERVICE" --json >/tmp/ozon-railway-service.json
else
  say "Railway service already exists: $RAILWAY_SERVICE"
  railway service "$RAILWAY_SERVICE" >/dev/null
fi

# Persistent Tailscale state means only one browser authorization is needed.
VOLUMES="$(railway volume list --json 2>/dev/null || echo '[]')"
if ! printf '%s' "$VOLUMES" | grep -q '/var/lib/tailscale'; then
  say "Creating persistent Tailscale volume"
  railway volume add --mount-path /var/lib/tailscale --json >/tmp/ozon-volume.json
else
  say "Persistent Tailscale volume already exists"
fi

EXISTING_VARS="$(railway variable list --json -s "$RAILWAY_SERVICE" 2>/dev/null || echo '{}')"
RADAR_SECRET="$(VARS_JSON="$EXISTING_VARS" python3 - <<'PY'
import json, os
raw=os.environ.get("VARS_JSON","{}")
try:
    obj=json.loads(raw)
except Exception:
    obj={}
value=""
if isinstance(obj,dict):
    value=str(obj.get("RADAR_SECRET") or "")
elif isinstance(obj,list):
    for item in obj:
        if isinstance(item,dict) and str(item.get("name") or item.get("key") or "")=="RADAR_SECRET":
            value=str(item.get("value") or "")
            break
print(value)
PY
)"
if [ -z "$RADAR_SECRET" ]; then
  RADAR_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(36))
PY
)"
fi

say "Configuring Railway service"
printf '%s' "$RADAR_SECRET" | railway variable set RADAR_SECRET --stdin -s "$RAILWAY_SERVICE" --skip-deploys >/dev/null
railway variable set \
  OZON_BROWSER_MODE=browser \
  OZON_BROWSER_WARMUP_MS=15000 \
  TAILSCALE_HOSTNAME=ozon-radar-cloud \
  -s "$RAILWAY_SERVICE" --skip-deploys >/dev/null

say "Deploying Ozon radar container"
railway up "$TMP/repo/ozon_radar_server" \
  --path-as-root \
  --service "$RAILWAY_SERVICE" \
  --detach >/tmp/ozon-railway-up.txt

# Ensure public HTTPS domain.
DOMAINS="$(railway domain list -s "$RAILWAY_SERVICE" --json 2>/dev/null || echo '[]')"
SERVER_URL="$(DOMAINS_JSON="$DOMAINS" python3 - <<'PY'
import json, os, re
raw=os.environ.get("DOMAINS_JSON","[]")
try:
    obj=json.loads(raw)
except Exception:
    obj=raw
strings=[]
def walk(x):
    if isinstance(x,str):
        strings.append(x)
    elif isinstance(x,dict):
        for v in x.values():
            walk(v)
    elif isinstance(x,list):
        for v in x:
            walk(v)
walk(obj)
for s in strings:
    m=re.search(r'(?:https://)?([a-zA-Z0-9.-]+\.up\.railway\.app)',s)
    if m:
        print('https://'+m.group(1))
        break
PY
)"

if [ -z "$SERVER_URL" ]; then
  say "Generating Railway public domain"
  NEW_DOMAIN="$(railway domain -s "$RAILWAY_SERVICE" --json)"
  SERVER_URL="$(DOMAIN_JSON="$NEW_DOMAIN" python3 - <<'PY'
import json, os, re
raw=os.environ.get("DOMAIN_JSON","")
try:
    obj=json.loads(raw)
except Exception:
    obj=raw
strings=[]
def walk(x):
    if isinstance(x,str):
        strings.append(x)
    elif isinstance(x,dict):
        for v in x.values():
            walk(v)
    elif isinstance(x,list):
        for v in x:
            walk(v)
walk(obj)
for s in strings:
    m=re.search(r'(?:https://)?([a-zA-Z0-9.-]+\.up\.railway\.app)',s)
    if m:
        print('https://'+m.group(1))
        break
PY
)"
fi
[ -n "$SERVER_URL" ] || die "Could not determine Railway domain"
echo "Railway URL: $SERVER_URL"

say "Waiting for one-time Tailscale authorization URL"
TS_URL=""
for _ in $(seq 1 90); do
  LOGS="$(railway logs -s "$RAILWAY_SERVICE" --latest -n 250 2>/dev/null || true)"
  TS_URL="$(printf '%s\n' "$LOGS" | grep -Eo 'https://login\.tailscale\.com/a/[A-Za-z0-9_-]+' | tail -1 || true)"
  if [ -n "$TS_URL" ]; then break; fi
  if printf '%s\n' "$LOGS" | grep -q 'TAILSCALE: persistent node authentication reused'; then break; fi
  sleep 2
done

if [ -n "$TS_URL" ]; then
  echo
  echo "Tailscale authorization: $TS_URL"
  if command -v open >/dev/null 2>&1; then open "$TS_URL"; fi
  echo "Approve the cloud node in the browser. The setup will continue automatically."
fi

say "Preparing hosted Apps Script deploy"
if ! command -v gh >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install gh
  else
    die "GitHub CLI not found; install gh and rerun"
  fi
fi
if ! gh auth status -h github.com >/dev/null 2>&1; then
  gh auth login -h github.com -w
fi

if [ ! -s "$HOME/.clasprc.json" ]; then
  say "Google Apps Script authorization required once"
  if ! command -v clasp >/dev/null 2>&1; then
    command -v npm >/dev/null 2>&1 || die "Node/npm required for clasp login"
    npm install -g @google/clasp
  fi
  clasp login
fi
[ -s "$HOME/.clasprc.json" ] || die "~/.clasprc.json was not created"

gh secret set CLASPRC_JSON -R "$REPO" < "$HOME/.clasprc.json"
printf '%s' "$SERVER_URL" | gh secret set OZON_RADAR_SERVER_URL -R "$REPO"
printf '%s' "$RADAR_SECRET" | gh secret set OZON_RADAR_SERVER_SECRET -R "$REPO"

say "Triggering Mac-independent Apps Script deploy"
gh workflow run "$SCRIPT_WORKFLOW" -R "$REPO" --ref "$BRANCH"

# Wait until Railway has either a valid home exit or a fail-closed status.
say "Checking Railway health"
for _ in $(seq 1 90); do
  HEALTH="$(curl -fsS --max-time 5 "$SERVER_URL/health" 2>/dev/null || true)"
  if [ -n "$HEALTH" ]; then
    echo "$HEALTH"
    break
  fi
  sleep 2
done

say "Waiting for the approved HOME exit node"
echo "On the always-on home Android/Android TV device:"
echo "  Tailscale -> Exit Node -> Run as exit node"
echo "Then approve 'Use as exit node' in the Tailscale Machines page."
echo "This setup will detect it automatically; no Railway redeploy is needed."

EXIT_READY=0
for _ in $(seq 1 180); do
  HEALTH="$(curl -fsS --max-time 5 "$SERVER_URL/health" 2>/dev/null || true)"
  if [ -n "$HEALTH" ]; then
    if HEALTH_JSON="$HEALTH" python3 - <<'PY'
import json, os
try:
    h=json.loads(os.environ.get("HEALTH_JSON","{}"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if h.get("tailscale_active") and h.get("tailscale_exit_node") else 1)
PY
    then
      EXIT_READY=1
      echo "$HEALTH"
      break
    fi
  fi
  sleep 5
done

if [ "$EXIT_READY" -ne 1 ]; then
  echo "HOME_EXIT_PENDING=yes"
  echo "The cloud service is installed, but an approved home Tailscale exit node is not online yet."
fi

say "LIVE probe: $TEST_QUERY / $TEST_SKU"
PROBE="$(curl -sS --max-time 90 \
  -H "Authorization: Bearer $RADAR_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"$TEST_QUERY\",\"sku\":\"$TEST_SKU\",\"max_position\":30}" \
  "$SERVER_URL/probe" || true)"
echo "$PROBE"

echo
echo "============================================================"
echo "SETUP FINISHED"
echo "Railway service: $RAILWAY_SERVICE"
echo "Server: $SERVER_URL"
echo "If /health shows tailscale_active=true and /probe has checked_depth>0,"
echo "the Ozon LIVE source is working through the home exit."
echo "============================================================"

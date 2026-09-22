#!/bin/sh
set -eu

TS_DIR="${TAILSCALE_STATE_DIR:-/var/lib/tailscale}"
TS_SOCK="/var/run/tailscale/tailscaled.sock"
TS_STATE="$TS_DIR/tailscaled.state"
TS_PROXY="socks5://127.0.0.1:1055"
TS_EXIT=""

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

find_exit_node() {
  tailscale --socket="$TS_SOCK" status --json > /tmp/tailscale-status.json 2>/dev/null || return 1
  python - /tmp/tailscale-status.json <<'PY'
import json, sys
try:
    st=json.load(open(sys.argv[1],encoding="utf-8"))
except Exception:
    raise SystemExit(1)

peers=st.get("Peer") or {}
vals=list(peers.values()) if isinstance(peers,dict) else (peers if isinstance(peers,list) else [])
online=[]
fallback=[]
for peer in vals:
    if not isinstance(peer,dict) or not peer.get("ExitNodeOption"):
        continue
    ips=peer.get("TailscaleIPs") or []
    name=(ips[0] if ips else "") or peer.get("DNSName") or peer.get("HostName") or ""
    if not name:
        continue
    fallback.append(name)
    if peer.get("Online") is not False:
        online.append(name)

if online:
    print(online[0])
elif fallback:
    print(fallback[0])
else:
    raise SystemExit(2)
PY
}

mkdir -p "$TS_DIR" /var/run/tailscale /tmp
log "TAILSCALE: starting userspace networking with persistent state at $TS_STATE"
tailscaled \
  --tun=userspace-networking \
  --socks5-server=127.0.0.1:1055 \
  --state="$TS_STATE" \
  --socket="$TS_SOCK" \
  >/tmp/tailscaled.log 2>&1 &

READY=0
i=0
while [ "$i" -lt 30 ]; do
  if tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
    READY=1
    break
  fi
  i=$((i+1))
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  log "TAILSCALE: daemon did not become ready; LIVE is fail-closed"
  export OZON_TAILSCALE_ACTIVE=0
  export OZON_FORCE_LIVE_OFFLINE=1
  exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"
fi

# Existing persistent node state is reused. On first deployment we allow one
# browser/device authorization instead of requiring a reusable auth key.
if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
  log "TAILSCALE: authenticating with configured auth key"
  tailscale --socket="$TS_SOCK" up \
    --auth-key="$TAILSCALE_AUTHKEY" \
    --hostname="${TAILSCALE_HOSTNAME:-ozon-radar-cloud}" \
    --accept-dns=false
else
  BACKEND_STATE="$(tailscale --socket="$TS_SOCK" status --json 2>/dev/null | python -c 'import json,sys; print((json.load(sys.stdin).get("BackendState") or ""))' 2>/dev/null || true)"
  if [ "$BACKEND_STATE" != "Running" ]; then
    log "TAILSCALE: FIRST LOGIN REQUIRED. Open the authorization URL printed below."
    # This prints the one-time login URL into Railway runtime logs and waits
    # until the owner authorizes the node.
    tailscale --socket="$TS_SOCK" up \
      --hostname="${TAILSCALE_HOSTNAME:-ozon-radar-cloud}" \
      --accept-dns=false
  else
    log "TAILSCALE: persistent node authentication reused"
  fi
fi

if [ -n "${TAILSCALE_EXIT_NODE:-}" ]; then
  TS_EXIT="$TAILSCALE_EXIT_NODE"
else
  log "TAILSCALE: looking for an approved home exit node"
  j=0
  while [ "$j" -lt 60 ]; do
    TS_EXIT="$(find_exit_node 2>/dev/null || true)"
    [ -n "$TS_EXIT" ] && break
    j=$((j+1))
    sleep 2
  done
fi

if [ -n "$TS_EXIT" ]; then
  log "TAILSCALE: selecting exit node $TS_EXIT"
  if tailscale --socket="$TS_SOCK" set --exit-node="$TS_EXIT"; then
    export OZON_PROXY="$TS_PROXY"
    export OZON_TAILSCALE_ACTIVE=1
    export OZON_TAILSCALE_EXIT_NODE="$TS_EXIT"
    export OZON_FORCE_LIVE_OFFLINE=0
    log "TAILSCALE: LIVE Ozon traffic routed through home exit node"
  else
    log "TAILSCALE: exit node selection failed; LIVE is fail-closed"
    export OZON_TAILSCALE_ACTIVE=0
    export OZON_FORCE_LIVE_OFFLINE=1
  fi
else
  log "TAILSCALE: no approved exit node visible; LIVE is fail-closed"
  export OZON_TAILSCALE_ACTIVE=0
  export OZON_FORCE_LIVE_OFFLINE=1
fi

exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"

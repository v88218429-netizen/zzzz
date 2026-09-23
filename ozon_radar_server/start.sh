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
for peer in vals:
    if not isinstance(peer,dict) or not peer.get("ExitNodeOption"):
        continue
    # Never select an offline exit node. Returning an offline fallback can make
    # the service look LIVE while traffic is no longer leaving via the home ISP.
    if peer.get("Online") is False:
        continue
    ips=peer.get("TailscaleIPs") or []
    name=(ips[0] if ips else "") or peer.get("DNSName") or peer.get("HostName") or ""
    if name:
        online.append(name)

if online:
    print(online[0])
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
  # On a fresh node, tailscaled is fully ready while BackendState=NeedsLogin.
  # "tailscale status" exits non-zero in that state, so do not use its exit
  # code as daemon readiness. The control socket is the readiness signal.
  if [ -S "$TS_SOCK" ]; then
    READY=1
    break
  fi
  i=$((i+1))
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  log "TAILSCALE: daemon control socket did not become ready; LIVE is fail-closed"
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
    # Keep Railway healthy while the owner performs the one-time browser login.
    # tailscale up prints the authorization URL to service logs and waits in
    # the background; persistent state remembers the login on later restarts.
    (
      tailscale --socket="$TS_SOCK" up \
        --hostname="${TAILSCALE_HOSTNAME:-ozon-radar-cloud}" \
        --accept-dns=false
    ) &
  else
    log "TAILSCALE: persistent node authentication reused"
  fi
fi

# From this point the app always talks to the local Tailscale SOCKS proxy.
# Before an exit node is selected, public Ozon requests fail closed. As soon as
# an approved home exit node appears, the background selector enables it and
# subsequent requests automatically leave through the home ISP without an app
# restart.
export OZON_PROXY="$TS_PROXY"
export OZON_FORCE_LIVE_OFFLINE=0
export OZON_TAILSCALE_ACTIVE=0

select_exit_forever() {
  while true; do
    BACKEND_STATE="$(tailscale --socket="$TS_SOCK" status --json 2>/dev/null | python -c 'import json,sys; print((json.load(sys.stdin).get("BackendState") or ""))' 2>/dev/null || true)"
    if [ "$BACKEND_STATE" != "Running" ]; then
      printf '%s' "" > /tmp/ozon-tailscale-exit-node
      sleep 3
      continue
    fi

    if [ -n "${TAILSCALE_EXIT_NODE:-}" ]; then
      TS_EXIT="$TAILSCALE_EXIT_NODE"
    else
      TS_EXIT="$(find_exit_node 2>/dev/null || true)"
    fi

    if [ -z "$TS_EXIT" ]; then
      # The marker is authoritative for the Python worker. Clearing it makes
      # every Ozon check fail closed instead of leaking through cloud egress.
      printf '%s' "" > /tmp/ozon-tailscale-exit-node
      sleep 5
      continue
    fi

    CURRENT_EXIT="$(cat /tmp/ozon-tailscale-exit-node 2>/dev/null || true)"
    if [ "$CURRENT_EXIT" != "$TS_EXIT" ]; then
      log "TAILSCALE: selecting exit node $TS_EXIT"
      if tailscale --socket="$TS_SOCK" set --exit-node="$TS_EXIT" >/tmp/tailscale-exit.log 2>&1; then
        printf '%s' "$TS_EXIT" > /tmp/ozon-tailscale-exit-node
        log "TAILSCALE: LIVE Ozon traffic now routes through home exit node $TS_EXIT"
      else
        printf '%s' "" > /tmp/ozon-tailscale-exit-node
        cat /tmp/tailscale-exit.log 2>/dev/null || true
      fi
    fi

    # Keep supervising the exit node for the lifetime of the container. If the
    # home device disappears, find_exit_node stops returning it and the marker
    # is cleared on the next loop.
    sleep 10
  done
}

select_exit_forever &

exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"

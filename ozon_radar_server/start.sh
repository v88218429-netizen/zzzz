#!/bin/sh
set -eu

TS_SOCK="/var/run/tailscale/tailscaled.sock"
TS_STATE="/tmp/tailscale/tailscaled.state"
TS_PROXY="socks5://127.0.0.1:1055"
TS_EXIT=""

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

find_exit_node() {
  tailscale status --json > /tmp/tailscale/status.json 2>/dev/null || return 1
  python - /tmp/tailscale/status.json <<'PY'
import json, sys
p=sys.argv[1]
try:
    st=json.load(open(p,encoding="utf-8"))
except Exception:
    raise SystemExit(1)

peers=st.get("Peer") or {}
if isinstance(peers, list):
    vals=peers
elif isinstance(peers, dict):
    vals=list(peers.values())
else:
    vals=[]

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

if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
  log "TAILSCALE: starting userspace networking"
  tailscaled \
    --tun=userspace-networking \
    --socks5-server=127.0.0.1:1055 \
    --state="$TS_STATE" \
    --socket="$TS_SOCK" \
    >/tmp/tailscale/tailscaled.log 2>&1 &

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

  if [ "$READY" -eq 1 ]; then
    log "TAILSCALE: authenticating cloud worker"
    if tailscale --socket="$TS_SOCK" up \
      --auth-key="$TAILSCALE_AUTHKEY" \
      --hostname="${TAILSCALE_HOSTNAME:-ozon-radar-cloud}" \
      --accept-dns=false \
      >/tmp/tailscale/up.log 2>&1; then

      if [ -n "${TAILSCALE_EXIT_NODE:-}" ]; then
        TS_EXIT="$TAILSCALE_EXIT_NODE"
      else
        # Give a newly-enabled Android TV/router exit node time to appear.
        j=0
        while [ "$j" -lt 30 ]; do
          TS_EXIT="$(find_exit_node 2>/dev/null || true)"
          [ -n "$TS_EXIT" ] && break
          j=$((j+1))
          sleep 2
        done
      fi

      if [ -n "$TS_EXIT" ]; then
        log "TAILSCALE: selecting exit node $TS_EXIT"
        if tailscale --socket="$TS_SOCK" set --exit-node="$TS_EXIT" >/tmp/tailscale/exit-node.log 2>&1; then
          export OZON_PROXY="$TS_PROXY"
          export OZON_TAILSCALE_ACTIVE=1
          export OZON_TAILSCALE_EXIT_NODE="$TS_EXIT"
          log "TAILSCALE: Ozon traffic will use home exit node through $TS_PROXY"
        else
          log "TAILSCALE: exit node selection failed; starting fail-closed for LIVE source"
          cat /tmp/tailscale/exit-node.log 2>/dev/null || true
          export OZON_TAILSCALE_ACTIVE=0
          export OZON_FORCE_LIVE_OFFLINE=1
        fi
      else
        log "TAILSCALE: no approved exit node is visible; starting fail-closed for LIVE source"
        export OZON_TAILSCALE_ACTIVE=0
        export OZON_FORCE_LIVE_OFFLINE=1
      fi
    else
      log "TAILSCALE: authentication failed; starting fail-closed for LIVE source"
      cat /tmp/tailscale/up.log 2>/dev/null || true
      export OZON_TAILSCALE_ACTIVE=0
      export OZON_FORCE_LIVE_OFFLINE=1
    fi
  else
    log "TAILSCALE: daemon did not become ready; starting fail-closed for LIVE source"
    export OZON_TAILSCALE_ACTIVE=0
    export OZON_FORCE_LIVE_OFFLINE=1
  fi
else
  log "TAILSCALE: TAILSCALE_AUTHKEY not configured; using existing direct/proxy source"
fi

exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"

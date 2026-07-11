#!/bin/bash
# Keeps staging tunnels alive for the shared Ready Debug Map and reports
# their URLs to the sync worker so hosted configs always point at live URLs.
# Portable: runs on Ga'noh's MacBook or the Studio. Install as a LaunchAgent
# with KeepAlive=true; this script loops forever and self-heals.
#
# Requires: cloudflared, curl. SYNC_KEY read from the file next to this script.

WORKER="https://ready-debug-map.readyapp.workers.dev"
DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="$(cat "$DIR/.sync-key" 2>/dev/null)"
HOST="$(hostname -s)"
LOGDIR="$HOME/.debug-map-tunnels"
mkdir -p "$LOGDIR"

# port | config slug (project.json filename without extension) | env label
TARGETS=(
  "8213|ready-app-boards|staging (tunnel)"
  "8311|ready-jobs-web|staging (tunnel)"
)

declare -A TUNNEL_PID
declare -A TUNNEL_URL
declare -A FAILS

start_tunnel() {
  local port="$1"
  local log="$LOGDIR/tunnel-$port.log"
  : > "$log"
  cloudflared tunnel --config /dev/null --url "http://127.0.0.1:$port" >> "$log" 2>&1 &
  TUNNEL_PID[$port]=$!
  # wait up to 30s for the URL to appear
  for _ in $(seq 1 30); do
    sleep 1
    local url
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | head -1)
    if [ -n "$url" ]; then TUNNEL_URL[$port]="$url"; return 0; fi
  done
  return 1
}

report_env() {
  local slug="$1" label="$2" url="$3" port="$4"
  curl -s -m 10 -X POST "$WORKER/api/envs" \
    -H "content-type: application/json" -H "x-debug-key: $KEY" \
    -d "{\"project\":\"$slug\",\"host\":\"$HOST\",\"environments\":{\"$label\":\"$url\"}}" > /dev/null
  echo "$(date '+%F %T') [$port] $slug -> $url" >> "$LOGDIR/keepalive.log"
}

while true; do
  for t in "${TARGETS[@]}"; do
    IFS='|' read -r port slug label <<< "$t"
    # only tunnel ports that have a live server behind them
    if ! nc -z 127.0.0.1 "$port" 2>/dev/null; then
      echo "$(date '+%F %T') [$port] no server listening, skipping" >> "$LOGDIR/keepalive.log"
      continue
    fi
    pid="${TUNNEL_PID[$port]}"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
      if start_tunnel "$port"; then
        report_env "$slug" "$label" "${TUNNEL_URL[$port]}" "$port"
      else
        echo "$(date '+%F %T') [$port] tunnel failed to produce URL" >> "$LOGDIR/keepalive.log"
      fi
      FAILS[$port]=0
      continue
    fi
    # process alive != tunnel alive: quick tunnels expire their hostname while
    # cloudflared sits in a retry loop. Probe the URL; two straight misses = re-mint.
    url="${TUNNEL_URL[$port]}"
    if [ -n "$url" ]; then
      code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$url" 2>/dev/null)
      if [ "$code" = "000" ] || [ "$code" = "502" ] || [ "$code" = "530" ]; then
        FAILS[$port]=$(( ${FAILS[$port]:-0} + 1 ))
        echo "$(date '+%F %T') [$port] health $code (strike ${FAILS[$port]}/2) $url" >> "$LOGDIR/keepalive.log"
        if [ "${FAILS[$port]}" -ge 2 ]; then
          kill "$pid" 2>/dev/null
          FAILS[$port]=0
          if start_tunnel "$port"; then
            report_env "$slug" "$label" "${TUNNEL_URL[$port]}" "$port"
          fi
        fi
      else
        FAILS[$port]=0
      fi
    fi
  done
  sleep 20
done

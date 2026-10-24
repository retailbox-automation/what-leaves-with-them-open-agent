#!/usr/bin/env bash
# Meterless Nightshift without its window: the bundled daemon (meterlessd) and the Node engine it spawns, on our
# own ports and our own state directory, serving NVIDIA Nemotron lanes for What Leaves With Them.
#
#   scripts/nightshift_headless.sh start [lane ...|none]   # default: $NIGHTSHIFT_LANES or both Nemotron lanes
#   scripts/nightshift_headless.sh lane <id> start|stop
#   scripts/nightshift_headless.sh status
#   scripts/nightshift_headless.sh stop
#
# It never opens Nightshift.app (no window, no phone page on :8089: that one belongs to the app shell), never binds
# anything but 127.0.0.1, and never prints the API token. The app's own data in
# ~/Library/Application Support/ai.meterless.runtime is not read or written: state lives in $NIGHTSHIFT_HOME/state.
#
# Env (defaults): NIGHTSHIFT_APP=/Applications/Nightshift.app  NIGHTSHIFT_HOME=~/nightshift-openagent/headless
#   NIGHTSHIFT_MODELS=~/nightshift-openagent/models  NIGHTSHIFT_PUBLIC=127.0.0.1:8088  NIGHTSHIFT_FABRIC=127.0.0.1:11445
#   NIGHTSHIFT_INVESTIGATOR_LANE=nemotron-lightning  NIGHTSHIFT_VERIFIER_LANE=nemotron-nano
set -euo pipefail

APP="${NIGHTSHIFT_APP:-/Applications/Nightshift.app}"
NS_HOME="${NIGHTSHIFT_HOME:-$HOME/nightshift-openagent/headless}"
MODELS="${NIGHTSHIFT_MODELS:-$HOME/nightshift-openagent/models}"
PUBLIC="${NIGHTSHIFT_PUBLIC:-127.0.0.1:8088}"
FABRIC="${NIGHTSHIFT_FABRIC:-127.0.0.1:11445}"
INV_LANE="${NIGHTSHIFT_INVESTIGATOR_LANE:-nemotron-lightning}"
VER_LANE="${NIGHTSHIFT_VERIFIER_LANE:-nemotron-nano}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANES_SRC="$HERE/nightshift_lanes.toml"
BIN="$APP/Contents/MacOS"
STATE="$NS_HOME/state"; LOGS="$NS_HOME/logs"; RUN="$NS_HOME/run"; CACHE="$NS_HOME/cache"
SOCK="$RUN/runtime.sock"; PIDF="$RUN/meterlessd.pid"
PY="$(command -v python3)"

say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }

ctl() {  # ctl METHOD PATH [JSON]  -> daemon control socket
  local m="$1" p="$2"; shift 2
  if [ $# -gt 0 ]; then
    curl -sS --max-time 60 --unix-socket "$SOCK" -X "$m" -H 'content-type: application/json' -d "$1" "http://meterlessd$p"
  else
    curl -sS --max-time 60 --unix-socket "$SOCK" -X "$m" "http://meterlessd$p"
  fi
}

running() {
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && ps -o command= -p "$(cat "$PIDF")" | grep -q meterlessd; then
    return 0
  fi
  local p; p="$(pgrep -f "$BIN/meterlessd --config $NS_HOME/config.toml" | head -1 || true)"   # pid file lost
  [ -n "$p" ] && echo "$p" > "$PIDF" && return 0
  return 1
}

port_of() { echo "${1##*:}"; }

our_pids() {  # the daemon and every descendant (node engine, llama-server lanes)
  local root; root="$(cat "$PIDF" 2>/dev/null || true)"; [ -n "$root" ] || return 0
  local all="$root" frontier="$root" next p
  while [ -n "$frontier" ]; do
    next=""
    for p in $frontier; do next="$next $(pgrep -P "$p" 2>/dev/null | tr '\n' ' ')"; done
    frontier="$(echo $next)"; [ -n "$frontier" ] && all="$all $frontier"
  done
  echo $all
}

listeners() {  # every TCP listener of our processes: "pid name addr"
  local pids; pids="$(our_pids | tr ' ' ',')"; [ -n "$pids" ] || return 0
  lsof -nP -a -p "$pids" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $2, $1, $9}' || true
}

guard_loopback() {
  local bad; bad="$(listeners | awk '$3 !~ /^127\.0\.0\.1:/ && $3 !~ /^\[::1\]:/' || true)"
  if [ -n "$bad" ]; then
    say "a Nightshift process listens beyond loopback, stopping: $bad"
    cmd_stop; die "refused to keep a non-loopback listener"
  fi
}

lane_states() {
  ctl GET /admin/status | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
for l in d.get("lanes", []):
    print(l["id"], l["state"], l.get("port"), (l.get("last_error") or "")[:160])'
}

wait_lane() {  # wait_lane ID -> prints the final state; 0 when ready
  # A start is asynchronous: the lane reads "stopped" until the daemon picks it up, "stopped" with
  # waiting_for_memory while admission is refused, then "starting". Wait up to 300 s for ready.
  local id="$1" i st
  for i in $(seq 1 75); do
    sleep 4
    st="$(lane_states | awk -v id="$id" '$1 == id {print $2; exit}')"
    case "$st" in ready|failed|crashed) break ;; esac
  done
  lane_states | awk -v id="$id" '$1 == id'
  [ "$st" = ready ]
}

write_config() {
  mkdir -p "$STATE" "$LOGS" "$RUN" "$CACHE" "$MODELS"; chmod 700 "$STATE"
  cat > "$NS_HOME/config.toml" <<EOF
# headless Nightshift for What Leaves With Them (written by scripts/nightshift_headless.sh)
[engine]
public_listen = "$PUBLIC"
fabric_listen = "$FABRIC"
control_socket = "$SOCK"
core_socket = "$RUN/core.sock"
models_dir = "$MODELS"
log_dir = "$LOGS"
llama_server_bin = "$BIN/llama-server"
llama_cpp_ref = "$(cat "$APP/Contents/Resources/runtime/llama-cpp-ref" 2>/dev/null || echo unknown)"
engine_pool_size = 1
node_bin = "$BIN/node"
engine_dir = "$APP/Contents/Resources/engine"

[memory]
policy = "auto"
system_reserve_mb = 2048
emergency_headroom_mb = 1024
EOF
}

set_app_policy() {
  local doc
  doc="{\"wlwt-investigator\":{\"mode\":\"local\",\"lanes\":[\"$INV_LANE\"]},\"wlwt-verifier\":{\"mode\":\"local\",\"lanes\":[\"$VER_LANE\"]}}"
  ctl POST /admin/app-policy "$doc" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
p = d.get("app_policy", {})
print("app policy:", {k: v.get("lanes") for k, v in p.items() if k.startswith("wlwt-")}, "ok" if d.get("ok") else d)'
}

cmd_start() {
  [ -x "$BIN/meterlessd" ] || die "no $BIN/meterlessd"
  if running; then
    say "meterlessd already running (pid $(cat "$PIDF"))"
  else
    for hp in "$PUBLIC" "$FABRIC"; do
      if lsof -nP -iTCP:"$(port_of "$hp")" -sTCP:LISTEN >/dev/null 2>&1; then
        die "port $(port_of "$hp") is taken by another process; set NIGHTSHIFT_PUBLIC / NIGHTSHIFT_FABRIC"
      fi
    done
    write_config
    cp "$LANES_SRC" "$STATE/lanes.toml"
    rm -f "$SOCK" "$RUN/core.sock"
    say "starting meterlessd (state $STATE, public $PUBLIC, fabric $FABRIC)"
    cd "$NS_HOME"
    METERLESS_STATE_DIR="$STATE" METERLESS_LOG_DIR="$LOGS" METERLESS_CACHE_DIR="$CACHE" \
      METERLESS_RUNTIME_DIR="$RUN" METERLESS_MODELS_DIR="$MODELS" METERLESSD_CONFIG="$NS_HOME/config.toml" \
      nohup nice -n 15 "$BIN/meterlessd" --config "$NS_HOME/config.toml" >> "$LOGS/meterlessd.stdout.log" 2>&1 &
    echo $! > "$PIDF"
    cd - >/dev/null
    local i
    for i in $(seq 1 60); do [ -S "$SOCK" ] && ctl GET /admin/status >/dev/null 2>&1 && break; sleep 1; done
    [ -S "$SOCK" ] || die "control socket did not appear; see $LOGS/meterlessd.stdout.log"
  fi
  if ! cmp -s "$LANES_SRC" "$STATE/lanes.toml"; then
    cp "$LANES_SRC" "$STATE/lanes.toml"
    say "lanes.toml updated: $(ctl POST /admin/reload)"
  fi
  local i
  for i in $(seq 1 60); do curl -s -o /dev/null --max-time 2 "http://$PUBLIC/healthz" && break; sleep 1; done
  curl -s -o /dev/null --max-time 2 "http://$PUBLIC/healthz" || die "engine is not answering on $PUBLIC; see $LOGS/engine-0.log"
  guard_loopback
  set_app_policy
  local lanes="${*:-${NIGHTSHIFT_LANES:-$INV_LANE $VER_LANE}}" id
  for id in $lanes; do
    [ "$id" = none ] && continue
    say "lane $id: $(ctl POST "/admin/lanes/$id/start")"
    wait_lane "$id" || say "lane $id is not ready (see its state above)"
  done
  guard_loopback
  cmd_status
}

cmd_lane() {
  local id="${1:?lane id}" act="${2:?start|stop}"
  running || die "meterlessd is not running"
  say "lane $id $act: $(ctl POST "/admin/lanes/$id/$act")"
  if [ "$act" = start ]; then
    wait_lane "$id" || die "lane $id did not become ready"
    guard_loopback
  fi
  return 0
}

cmd_status() {
  if ! running; then say "meterlessd: not running"; return 0; fi
  say "meterlessd: pid $(cat "$PIDF"), state $STATE"
  say "listeners:"; listeners | sed 's/^/  /'
  say "lanes:"; lane_states | sed 's/^/  /'
  say "models in $MODELS:"; ls -l "$MODELS" 2>/dev/null | awk 'NR>1 {printf "  %s %.2f GB\n", $9, $5/1e9}'
}

cmd_stop() {
  if ! running; then say "meterlessd: not running"; rm -f "$PIDF"; return 0; fi
  local pid pids i; pid="$(cat "$PIDF")"; pids="$(our_pids)"
  say "stopping meterlessd pid $pid (with ${pids// /,})"
  ctl POST /admin/shutdown >/dev/null 2>&1 || true
  for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  kill -0 "$pid" 2>/dev/null && kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  for p in $pids; do kill -0 "$p" 2>/dev/null && ps -o command= -p "$p" | grep -q "$APP" && kill -TERM "$p" 2>/dev/null || true; done
  sleep 3
  for p in $pids; do kill -0 "$p" 2>/dev/null && ps -o command= -p "$p" | grep -q "$APP" && kill -KILL "$p" 2>/dev/null || true; done
  rm -f "$PIDF"
  local left=""
  for p in $pids; do kill -0 "$p" 2>/dev/null && left="$left $p"; done
  [ -z "$left" ] || die "still alive:$left"
  say "stopped; nothing of ours is running"
}

case "${1:-status}" in
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  lane) shift; cmd_lane "$@" ;;
  *) echo "usage: $0 start [lane ...] | lane <id> start|stop | status | stop" >&2; exit 2 ;;
esac

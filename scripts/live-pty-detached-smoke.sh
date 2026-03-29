#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

run_root="${PEER_FORGE_LIVE_SMOKE_RUN_ROOT:-$repo_root/.claude/tmp/peer-forge-live-pty-detached-smoke}"
state_file=""

cleanup() {
  if [[ -n "$state_file" && -f "$state_file" ]]; then
    "$repo_root/bin/peer-forge-live" stop --state-file "$state_file" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[live-pty-detached-smoke] starting detached PTY live run"
start_output="$("$repo_root/bin/peer-forge-live" \
  --repo "$repo_root" \
  --task "Smoke test only. Do not modify any files. Start the live peer-forge workflow and wait for the human supervisor." \
  --acceptance "Do not change repository files." \
  --acceptance "Wait for further instructions after startup." \
  --signoff-rounds 0 \
  --watchdog-seconds 0 \
  --transport pty \
  --no-attach \
  --print-control-token \
  --run-root "$run_root")"

start_lines="$(printf '%s' "$start_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["state_file"]); print(data["control_url"]); print(data["events_stream_url"]); print(data["web_url"]); print(data["control_token"]); print(data["process_mode"]); print(data["owner_pid"]); print(data["status_command"]); print(data["stop_command"])')"
run_id="$(printf '%s\n' "$start_lines" | sed -n '1p')"
state_file="$(printf '%s\n' "$start_lines" | sed -n '2p')"
base_url="$(printf '%s\n' "$start_lines" | sed -n '3p')"
events_stream_url="$(printf '%s\n' "$start_lines" | sed -n '4p')"
web_url="$(printf '%s\n' "$start_lines" | sed -n '5p')"
token="$(printf '%s\n' "$start_lines" | sed -n '6p')"
process_mode="$(printf '%s\n' "$start_lines" | sed -n '7p')"
owner_pid="$(printf '%s\n' "$start_lines" | sed -n '8p')"
status_command="$(printf '%s\n' "$start_lines" | sed -n '9p')"
stop_command="$(printf '%s\n' "$start_lines" | sed -n '10p')"

echo "[live-pty-detached-smoke] run_id=$run_id"
echo "[live-pty-detached-smoke] state_file=$state_file"
echo "[live-pty-detached-smoke] control=$base_url"
echo "[live-pty-detached-smoke] web=$web_url"
echo "[live-pty-detached-smoke] owner_pid=$owner_pid"

python3 - "$process_mode" "$owner_pid" "$status_command" "$stop_command" <<'PY'
import sys

process_mode, owner_pid, status_command, stop_command = sys.argv[1:5]
assert process_mode == "pty-detached", process_mode
assert int(owner_pid) > 0, owner_pid
assert "status --state-file" in status_command, status_command
assert "stop --state-file" in stop_command, stop_command
print("[live-pty-detached-smoke] detached metadata ok")
PY

curl -sf \
  -H "X-Peer-Forge-Token: $token" \
  "$base_url/health" \
  | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["ok"] is True; print("[live-pty-detached-smoke] health ok")'

curl -sf \
  -H "X-Peer-Forge-Token: $token" \
  "$base_url/dashboard" \
  | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["process"]["mode"] == "pty-detached"; assert data["process"]["owner_alive"] is True; print("[live-pty-detached-smoke] dashboard ok")'

status_output="$("$repo_root/bin/peer-forge-live" status --state-file "$state_file" --print-control-token)"
printf '%s' "$status_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["process_mode"] == "pty-detached"; assert data["owner_alive"] is True; assert data["control_url"]; assert data["web_url"]; assert data["control_token"]; print("[live-pty-detached-smoke] status ok")'

command_response="$(
  curl -sf \
    -X POST \
    -H "X-Peer-Forge-Token: $token" \
    -H "Content-Type: application/json" \
    -d '{"command":"status","source":"smoke-pty"}' \
    "$base_url/commands"
)"
printf '%s' "$command_response" | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["accepted"] is True; print("[live-pty-detached-smoke] command queue ok")'

stop_output="$("$repo_root/bin/peer-forge-live" stop --state-file "$state_file" --print-control-token)"
printf '%s' "$stop_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["process_mode"] == "pty-detached"; assert data["owner_alive"] is False; assert data["stop_timeout"] is False; assert data["status"] == "aborted"; print("[live-pty-detached-smoke] stop ok")'

status_after_stop="$("$repo_root/bin/peer-forge-live" status --state-file "$state_file" --print-control-token)"
printf '%s' "$status_after_stop" | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["owner_alive"] is False; assert data["status"] == "aborted"; print("[live-pty-detached-smoke] stopped status ok")'

if curl -sf -H "X-Peer-Forge-Token: $token" "$base_url/health" >/dev/null 2>&1; then
  echo "[live-pty-detached-smoke] expected control server to be down after stop" >&2
  exit 1
fi

echo "[live-pty-detached-smoke] detached PTY smoke test passed"

#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

run_root="${PEER_FORGE_LIVE_SMOKE_RUN_ROOT:-$repo_root/.claude/tmp/peer-forge-live-api-smoke}"
session_name=""
state_file=""

cleanup() {
  if [[ -n "$session_name" ]]; then
    tmux kill-session -t "$session_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[live-api-smoke] starting detached live run"
start_output="$("$repo_root/bin/peer-forge-live" \
  --repo "$repo_root" \
  --task "Smoke test only. Do not modify any files. Start the live peer-forge workflow and wait for the human supervisor." \
  --acceptance "Do not change repository files." \
  --acceptance "Wait for further instructions after startup." \
  --signoff-rounds 0 \
  --watchdog-seconds 0 \
  --no-attach \
  --run-root "$run_root")"

start_lines="$(printf '%s' "$start_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["session_name"]); print(data["state_file"])')"
run_id="$(printf '%s\n' "$start_lines" | sed -n '1p')"
session_name="$(printf '%s\n' "$start_lines" | sed -n '2p')"
state_file="$(printf '%s\n' "$start_lines" | sed -n '3p')"

echo "[live-api-smoke] run_id=$run_id"
echo "[live-api-smoke] session=$session_name"

control_output="$(python3 - "$state_file" <<'PY'
import json
import pathlib
import sys
import time

state_path = pathlib.Path(sys.argv[1])
deadline = time.time() + 30
while time.time() < deadline:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    control = state.get("runtime", {}).get("control", {})
    base_url = str(control.get("base_url", "") or "")
    token = str(control.get("token", "") or "")
    if base_url and token:
        print(base_url)
        print(token)
        raise SystemExit(0)
    time.sleep(0.5)
raise SystemExit("control API did not become ready within 30 seconds")
PY
)"

base_url="$(printf '%s\n' "$control_output" | sed -n '1p')"
token="$(printf '%s\n' "$control_output" | sed -n '2p')"
echo "[live-api-smoke] control=$base_url"

curl -sf \
  -H "X-Peer-Forge-Token: $token" \
  "$base_url/health" \
  | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["ok"] is True; print("[live-api-smoke] health ok")'

curl -sf \
  -H "X-Peer-Forge-Token: $token" \
  "$base_url/state" \
  | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["run_id"]; assert "runtime" in data; print("[live-api-smoke] state ok")'

command_response="$(
  curl -sf \
    -X POST \
    -H "X-Peer-Forge-Token: $token" \
    -H "Content-Type: application/json" \
    -d '{"command":"status","source":"smoke"}' \
    "$base_url/commands"
)"
request_id="$(printf '%s' "$command_response" | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["accepted"] is True; print(data["request_id"])')"
echo "[live-api-smoke] queued request_id=$request_id"

python3 - "$base_url" "$token" "$request_id" <<'PY'
import json
import sys
import time
import urllib.request

base_url, token, request_id = sys.argv[1:4]
deadline = time.time() + 30
while time.time() < deadline:
    req = urllib.request.Request(
        f"{base_url}/events?limit=500",
        headers={"X-Peer-Forge-Token": token},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.load(response)
    for item in payload.get("items", []):
        event = item.get("event", {})
        if event.get("type") == "command-processed" and event.get("request_id") == request_id:
            print("[live-api-smoke] command processed")
            raise SystemExit(0)
    time.sleep(0.5)
raise SystemExit("queued command was not processed within 30 seconds")
PY

echo "[live-api-smoke] control API smoke test passed"

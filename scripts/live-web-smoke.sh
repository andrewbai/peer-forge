#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

run_root="${PEER_FORGE_LIVE_SMOKE_RUN_ROOT:-$repo_root/.claude/tmp/peer-forge-live-web-smoke}"
session_name=""
state_file=""

cleanup() {
  if [[ -n "$session_name" ]]; then
    tmux kill-session -t "$session_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[live-web-smoke] starting detached live run"
start_output="$("$repo_root/bin/peer-forge-live" \
  --repo "$repo_root" \
  --task "Smoke test only. Do not modify any files. Start the live peer-forge workflow and wait for the human supervisor." \
  --acceptance "Do not change repository files." \
  --acceptance "Wait for further instructions after startup." \
  --signoff-rounds 0 \
  --watchdog-seconds 0 \
  --no-attach \
  --print-control-token \
  --run-root "$run_root")"

start_lines="$(printf '%s' "$start_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["session_name"]); print(data["state_file"]); print(data["control_url"]); print(data["events_stream_url"]); print(data["web_url"]); print(data["control_token"])')"
run_id="$(printf '%s\n' "$start_lines" | sed -n '1p')"
session_name="$(printf '%s\n' "$start_lines" | sed -n '2p')"
state_file="$(printf '%s\n' "$start_lines" | sed -n '3p')"
base_url="$(printf '%s\n' "$start_lines" | sed -n '4p')"
events_stream_url="$(printf '%s\n' "$start_lines" | sed -n '5p')"
web_url="$(printf '%s\n' "$start_lines" | sed -n '6p')"
token="$(printf '%s\n' "$start_lines" | sed -n '7p')"

echo "[live-web-smoke] run_id=$run_id"
echo "[live-web-smoke] session=$session_name"
echo "[live-web-smoke] control=$base_url"
echo "[live-web-smoke] events=$events_stream_url"
echo "[live-web-smoke] web=$web_url"

page_html="$(curl -sf "$web_url")"
PAGE_HTML="$page_html" python3 - "$run_id" "$token" <<'PY'
import os
import sys

html, run_id, token = os.environ["PAGE_HTML"], sys.argv[1], sys.argv[2]
assert "Peer Forge Live Console" in html
assert "Single-Run Supervision" in html
assert "window.__PEER_FORGE_LIVE_BOOTSTRAP__" in html
assert run_id in html
assert token in html
print("[live-web-smoke] index ok")
PY

for asset in /app.css /app.js /render.js /store.js; do
  curl -sf "$base_url$asset" >/dev/null
  echo "[live-web-smoke] asset ok $asset"
done

stream_output="$(curl -sfN --max-time 3 "$events_stream_url?token=$token" || true)"
STREAM_OUTPUT="$stream_output" python3 - <<'PY'
import os

data = os.environ["STREAM_OUTPUT"]
assert "event: supervisor" in data or "data:" in data
print("[live-web-smoke] events stream ok")
PY

echo "[live-web-smoke] web console smoke test passed"

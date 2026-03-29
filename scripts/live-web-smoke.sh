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
  --run-root "$run_root")"

start_lines="$(printf '%s' "$start_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["session_name"]); print(data["state_file"])')"
run_id="$(printf '%s\n' "$start_lines" | sed -n '1p')"
session_name="$(printf '%s\n' "$start_lines" | sed -n '2p')"
state_file="$(printf '%s\n' "$start_lines" | sed -n '3p')"

echo "[live-web-smoke] run_id=$run_id"
echo "[live-web-smoke] session=$session_name"

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
echo "[live-web-smoke] control=$base_url"

page_html="$(curl -sf "$base_url/")"
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

stream_output="$(curl -sfN --max-time 3 "$base_url/events/stream?token=$token" || true)"
STREAM_OUTPUT="$stream_output" python3 - <<'PY'
import os

data = os.environ["STREAM_OUTPUT"]
assert "event: supervisor" in data or "data:" in data
print("[live-web-smoke] events stream ok")
PY

echo "[live-web-smoke] web console smoke test passed"

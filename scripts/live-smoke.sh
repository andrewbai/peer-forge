#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

run_root="${PEER_FORGE_LIVE_SMOKE_RUN_ROOT:-$repo_root/.claude/tmp/peer-forge-live-smoke}"
session_name=""
state_file=""

cleanup() {
  if [[ -n "$session_name" ]]; then
    tmux kill-session -t "$session_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[live-smoke] starting detached live run"
start_output="$("$repo_root/bin/peer-forge-live" \
  --repo "$repo_root" \
  --task "Smoke test only. Do not modify any files. Start the live peer-forge workflow and wait for the human supervisor." \
  --acceptance "Do not change repository files." \
  --acceptance "Wait for further instructions after startup." \
  --signoff-rounds 0 \
  --watchdog-seconds 0 \
  --no-attach \
  --run-root "$run_root")"

start_lines="$(printf '%s' "$start_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["session_name"]); print(data["run_dir"]); print(data["state_file"]); print(data["attach"]); print(data["control_url"]); print(data["events_stream_url"]); print(data["web_url"])')"
run_id="$(printf '%s\n' "$start_lines" | sed -n '1p')"
session_name="$(printf '%s\n' "$start_lines" | sed -n '2p')"
run_dir="$(printf '%s\n' "$start_lines" | sed -n '3p')"
state_file="$(printf '%s\n' "$start_lines" | sed -n '4p')"
attach_cmd="$(printf '%s\n' "$start_lines" | sed -n '5p')"
control_url="$(printf '%s\n' "$start_lines" | sed -n '6p')"
events_stream_url="$(printf '%s\n' "$start_lines" | sed -n '7p')"
web_url="$(printf '%s\n' "$start_lines" | sed -n '8p')"

echo "[live-smoke] run_id=$run_id"
echo "[live-smoke] session=$session_name"
echo "[live-smoke] attach=$attach_cmd"
echo "[live-smoke] control=$control_url"
echo "[live-smoke] events=$events_stream_url"
echo "[live-smoke] web=$web_url"

if [[ -z "$control_url" || -z "$events_stream_url" || -z "$web_url" ]]; then
  echo "[live-smoke] detached JSON did not include control URLs" >&2
  exit 1
fi

tmux has-session -t "$session_name"
pane_count="$(tmux list-panes -t "$session_name" | wc -l | tr -d ' ')"
if [[ "$pane_count" -lt 3 ]]; then
  echo "[live-smoke] expected at least 3 panes, got $pane_count" >&2
  exit 1
fi

python3 - "$state_file" <<'PY'
import json
import pathlib
import sys

state_path = pathlib.Path(sys.argv[1])
state = json.loads(state_path.read_text(encoding="utf-8"))
run_dir = pathlib.Path(state["run_dir"])
assert run_dir.exists(), run_dir
turn_dir = run_dir / "turns" / "01-plan-initial"
required = [
    turn_dir / "claude" / "prompt.txt",
    turn_dir / "claude" / "turn.log",
    turn_dir / "codex" / "prompt.txt",
    turn_dir / "codex" / "turn.log",
    turn_dir / "claude" / "entry-snapshot.status.txt",
    turn_dir / "claude" / "entry-snapshot.diff.txt",
    turn_dir / "codex" / "entry-snapshot.status.txt",
    turn_dir / "codex" / "entry-snapshot.diff.txt",
    run_dir / "state.json",
]
for path in required:
    if not path.exists():
        raise SystemExit(f"missing expected smoke artifact: {path}")
PY

supervisor_pane="$(tmux list-panes -t "$session_name" -F '#{pane_id}	#{pane_title}' | awk -F '	' '$2=="peer-forge-live:supervisor"{print $1; exit}')"
if [[ -z "$supervisor_pane" ]]; then
  echo "[live-smoke] supervisor pane not found before kill" >&2
  exit 1
fi

echo "[live-smoke] killing supervisor pane $supervisor_pane"
tmux kill-pane -t "$supervisor_pane"
sleep 1

echo "[live-smoke] resuming supervisor"
resume_output="$("$repo_root/bin/peer-forge-live" resume --state-file "$state_file" --no-attach)"
resume_lines="$(printf '%s' "$resume_output" | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data["run_id"]); print(data["session_name"]); print(data["state_file"]); print(data["supervisor_action"]); print(data["control_url"]); print(data["events_stream_url"]); print(data["web_url"])')"
resume_action="$(printf '%s\n' "$resume_lines" | sed -n '4p')"
resume_control_url="$(printf '%s\n' "$resume_lines" | sed -n '5p')"
resume_events_stream_url="$(printf '%s\n' "$resume_lines" | sed -n '6p')"
resume_web_url="$(printf '%s\n' "$resume_lines" | sed -n '7p')"
if [[ "$resume_action" != "supervisor-created" && "$resume_action" != "supervisor-respawned" && "$resume_action" != "supervisor-resumed" ]]; then
  echo "[live-smoke] unexpected resume action: $resume_action" >&2
  exit 1
fi
if [[ -z "$resume_control_url" || -z "$resume_events_stream_url" || -z "$resume_web_url" ]]; then
  echo "[live-smoke] resumed detached JSON did not include control URLs" >&2
  exit 1
fi

tmux has-session -t "$session_name"
supervisor_count="$(tmux list-panes -t "$session_name" -F '#{pane_title}' | awk '$0=="peer-forge-live:supervisor"{count++} END{print count+0}')"
if [[ "$supervisor_count" -ne 1 ]]; then
  echo "[live-smoke] expected exactly 1 supervisor pane after resume, got $supervisor_count" >&2
  exit 1
fi

python3 - "$state_file" <<'PY'
import json
import pathlib
import sys

state_path = pathlib.Path(sys.argv[1])
state = json.loads(state_path.read_text(encoding="utf-8"))
events_path = pathlib.Path(state["logs"]["events_jsonl"])
if not events_path.exists():
    raise SystemExit(f"missing events log: {events_path}")
events = events_path.read_text(encoding="utf-8").splitlines()
if not any('"type": "supervisor-created"' in line or '"type": "supervisor-respawned"' in line or '"type": "supervisor-resumed"' in line for line in events):
    raise SystemExit("resume event was not recorded in events.jsonl")
PY

echo "[live-smoke] supervisor recovery smoke test passed"

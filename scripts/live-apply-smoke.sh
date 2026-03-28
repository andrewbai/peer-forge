#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
tmp_root="$(mktemp -d /tmp/peer-forge-live-apply-smoke.XXXXXX)"
trap 'rm -rf "$tmp_root"' EXIT

create_fixture() {
  local name="$1"
  local fixture_root="$tmp_root/$name"
  local target_repo="$fixture_root/repo"
  local run_dir="$fixture_root/run"
  mkdir -p "$target_repo" "$run_dir"

  git -C "$target_repo" init -q
  git -C "$target_repo" config user.name "Peer Forge Smoke"
  git -C "$target_repo" config user.email "peer-forge-smoke@example.com"
  printf 'before\n' > "$target_repo/app.txt"
  printf 'remove me\n' > "$target_repo/remove.txt"
  mkdir -p "$target_repo/src"
  printf 'legacy\n' > "$target_repo/src/legacy.txt"
  git -C "$target_repo" add .
  git -C "$target_repo" commit -q -m "Initial fixture"
  local initial_commit
  initial_commit="$(git -C "$target_repo" rev-parse HEAD)"

  REPO="$target_repo" RUN_DIR="$run_dir" INITIAL_COMMIT="$initial_commit" python3 - <<'PY'
import json
import os
from pathlib import Path

repo = Path(os.environ["REPO"])
run_dir = Path(os.environ["RUN_DIR"])
initial_commit = os.environ["INITIAL_COMMIT"]
run_id = run_dir.parent.name
package_dir = run_dir / "packages" / "execute-initial" / "claude"
files_dir = package_dir / "files"
files_dir.mkdir(parents=True, exist_ok=True)
(files_dir / "app.txt").write_text("after\n", encoding="utf-8")
(files_dir / "new.txt").write_text("brand new\n", encoding="utf-8")
(package_dir / "solution.diff").write_text(
    "--- synthetic diff preview only ---\n",
    encoding="utf-8",
)
manifest = {
    "changed_files": ["app.txt", "new.txt", "remove.txt"],
    "copied_files": ["app.txt", "new.txt"],
    "deleted_files": ["remove.txt"],
}
(package_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

logs = {
    "supervisor": str(run_dir / "supervisor.log"),
    "verbose": str(run_dir / "panes" / "verbose.log"),
    "claude_raw": str(run_dir / "panes" / "claude.raw.log"),
    "codex_raw": str(run_dir / "panes" / "codex.raw.log"),
    "supervisor_raw": str(run_dir / "panes" / "supervisor.raw.log"),
    "events_jsonl": str(run_dir / "events.jsonl"),
}
for path in logs.values():
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

package_record = {
    "turn_id": "07-execute-initial",
    "phase": "execute-initial",
    "executor": "claude",
    "created_at": "2026-03-29T00:00:00.000Z",
    "package_dir": str(package_dir),
    "manifest_path": str(package_dir / "manifest.json"),
    "diff_path": str(package_dir / "solution.diff"),
    "changed_files": manifest["changed_files"],
    "summary": {
        "summary": "Synthetic execution package for smoke testing.",
        "changed_files": manifest["changed_files"],
        "tests": [],
        "remaining_risks": [],
        "assumptions": [],
    },
}

state = {
    "run_id": run_id,
    "created_at": "2026-03-29T00:00:00.000Z",
    "status": "approved",
    "repo": str(repo),
    "task": "Smoke-test live apply.",
    "acceptance": ["Do not use real model output."],
    "scope": ["app.txt", "new.txt", "remove.txt"],
    "include_path": [],
    "claude_model": "",
    "codex_model": "",
    "claude_bare": True,
    "signoff_rounds": 0,
    "watchdog_seconds": 0,
    "max_watchdog_nudges": 0,
    "run_dir": str(run_dir),
    "state_file": str(run_dir / "state.json"),
    "tool_repo_root": "",
    "session_name": "peer-forge-live-smoke",
    "current_phase": "execution-signoff",
    "selected_executor": "claude",
    "selected_reviewer": "codex",
    "final_plan": {"summary": "Synthetic approved plan.", "steps": ["edit files"]},
    "current_execution_package": package_record,
    "execution_packages": [package_record],
    "read_only_violations": [],
    "apply_attempts": [],
    "manual_confirmations_expected": [],
    "notes": [],
    "turns": [],
    "summary": {
        "plan_approved": True,
        "execution_approved": True,
        "final_approved": True,
        "current_execution_package": package_record,
        "apply_status": "not-applied",
        "applied_branch": "",
        "applied_commit": "",
        "last_apply_report": "",
        "last_apply_attempt_id": "",
    },
    "logs": logs,
    "workspaces": {
        "repo": str(repo),
        "baseline": str(run_dir / "baseline"),
        "claude": str(run_dir / "claude"),
        "codex": str(run_dir / "codex"),
        "git_mode": True,
        "cleanup_targets": [],
        "initial_commit": initial_commit,
    },
    "agents": {
        "claude": {"workspace": "", "pane_id": "", "raw_log_path": logs["claude_raw"], "stream_offset": 0, "last_activity_at": ""},
        "codex": {"workspace": "", "pane_id": "", "raw_log_path": logs["codex_raw"], "stream_offset": 0, "last_activity_at": ""},
        "supervisor": {"pane_id": ""},
    },
}
(run_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
PY

  printf '%s\n' "$target_repo"
  printf '%s\n' "$run_dir/state.json"
}

echo "[live-apply-smoke] creating preview/apply fixture"
preview_fixture="$(create_fixture preview)"
preview_repo="$(printf '%s\n' "$preview_fixture" | sed -n '1p')"
preview_state="$(printf '%s\n' "$preview_fixture" | sed -n '2p')"

preview_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$preview_state")"
PREVIEW_JSON="$preview_json" PREVIEW_REPO="$preview_repo" python3 - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(os.environ["PREVIEW_JSON"])
repo = Path(os.environ["PREVIEW_REPO"])
assert payload["status"] == "preview", payload
assert payload["target_branch"].startswith("peer-forge/"), payload
assert (repo / "app.txt").read_text(encoding="utf-8") == "before\n"
assert (repo / "remove.txt").exists()
assert not (repo / "new.txt").exists()
assert Path(payload["report_json"]).exists(), payload["report_json"]
assert Path(payload["report_md"]).exists(), payload["report_md"]
PY

apply_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$preview_state" --apply --commit)"
APPLY_JSON="$apply_json" APPLY_REPO="$preview_repo" python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

payload = json.loads(os.environ["APPLY_JSON"])
repo = Path(os.environ["APPLY_REPO"])
assert payload["status"] == "committed", payload
assert payload["commit_sha"], payload
assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], check=True, text=True, capture_output=True).stdout.strip() == payload["target_branch"]
assert (repo / "app.txt").read_text(encoding="utf-8") == "after\n"
assert (repo / "new.txt").read_text(encoding="utf-8") == "brand new\n"
assert not (repo / "remove.txt").exists()
subject = subprocess.run(["git", "-C", str(repo), "log", "-1", "--pretty=%s"], check=True, text=True, capture_output=True).stdout.strip()
assert subject.startswith("Apply peer-forge-live run "), subject
PY

echo "[live-apply-smoke] creating drift-non-overlap fixture"
drift_safe_fixture="$(create_fixture drift-safe)"
drift_safe_repo="$(printf '%s\n' "$drift_safe_fixture" | sed -n '1p')"
drift_safe_state="$(printf '%s\n' "$drift_safe_fixture" | sed -n '2p')"
printf 'drift\n' >> "$drift_safe_repo/src/legacy.txt"
git -C "$drift_safe_repo" add src/legacy.txt
git -C "$drift_safe_repo" commit -q -m "Introduce non-overlap base drift"

drift_safe_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$drift_safe_state")"
DRIFT_SAFE_JSON="$drift_safe_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DRIFT_SAFE_JSON"])
assert payload["status"] == "preview", payload
assert payload["decision"] == "preview-safe", payload
joined = "\n".join(payload.get("warnings", []))
assert "does not overlap the execution package paths" in joined, joined
assert payload["drift_paths"] == ["src/legacy.txt"], payload
assert payload["drift_overlap"] == [], payload
PY

echo "[live-apply-smoke] creating dirty-non-overlap fixture"
dirty_fixture="$(create_fixture dirty)"
dirty_repo="$(printf '%s\n' "$dirty_fixture" | sed -n '1p')"
dirty_state="$(printf '%s\n' "$dirty_fixture" | sed -n '2p')"
printf 'dirty\n' >> "$dirty_repo/src/legacy.txt"

set +e
dirty_blocked_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$dirty_state")"
dirty_blocked_status=$?
set -e
if [[ "$dirty_blocked_status" -eq 0 ]]; then
  echo "[live-apply-smoke] expected dirty preview without flag to fail" >&2
  exit 1
fi

DIRTY_BLOCKED_JSON="$dirty_blocked_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DIRTY_BLOCKED_JSON"])
assert payload["status"] == "blocked", payload
assert payload["decision"] == "preview-needs-allow-dirty-target", payload
joined = "\n".join(payload.get("blockers", []))
assert "--allow-dirty-target" in joined, joined
assert payload["dirty_paths"] == ["src/legacy.txt"], payload
assert payload["dirty_overlap"] == [], payload
PY

dirty_allowed_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$dirty_state" --apply --allow-dirty-target)"
DIRTY_ALLOWED_JSON="$dirty_allowed_json" DIRTY_REPO="$dirty_repo" python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

payload = json.loads(os.environ["DIRTY_ALLOWED_JSON"])
repo = Path(os.environ["DIRTY_REPO"])
assert payload["status"] == "applied", payload
assert payload["decision"] == "applied", payload
assert (repo / "app.txt").read_text(encoding="utf-8") == "after\n"
assert (repo / "new.txt").read_text(encoding="utf-8") == "brand new\n"
assert not (repo / "remove.txt").exists()
assert (repo / "src/legacy.txt").read_text(encoding="utf-8").endswith("dirty\n")
branch = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
assert branch == payload["target_branch"], (branch, payload)
PY

echo "[live-apply-smoke] creating overlap fixture"
overlap_fixture="$(create_fixture overlap)"
overlap_repo="$(printf '%s\n' "$overlap_fixture" | sed -n '1p')"
overlap_state="$(printf '%s\n' "$overlap_fixture" | sed -n '2p')"
printf 'overlap drift\n' >> "$overlap_repo/app.txt"
git -C "$overlap_repo" add app.txt
git -C "$overlap_repo" commit -q -m "Introduce overlap base drift"

set +e
overlap_blocked_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$overlap_state")"
overlap_blocked_status=$?
set -e
if [[ "$overlap_blocked_status" -eq 0 ]]; then
  echo "[live-apply-smoke] expected overlap preview to fail" >&2
  exit 1
fi

OVERLAP_BLOCKED_JSON="$overlap_blocked_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["OVERLAP_BLOCKED_JSON"])
assert payload["status"] == "blocked", payload
assert payload["decision"] == "preview-blocked-drift-overlap", payload
assert payload["drift_overlap"] == ["app.txt"], payload
assert payload["blocked_paths"] == ["app.txt"], payload
PY

overlap_allowed_json="$("$repo_root/bin/peer-forge-live" apply --state-file "$overlap_state" --allow-base-drift)"
OVERLAP_ALLOWED_JSON="$overlap_allowed_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["OVERLAP_ALLOWED_JSON"])
assert payload["status"] == "preview", payload
assert payload["decision"] == "preview-safe", payload
joined = "\n".join(payload.get("warnings", []))
assert "--allow-base-drift" in joined, joined
PY

echo "[live-apply-smoke] live apply smoke test passed"

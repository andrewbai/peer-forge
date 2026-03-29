from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any

from peer_consensus import (
    build_merge_brief,
    choose_final_base,
    clip_text,
    collect_package,
    git,
    normalize_findings,
    snapshot_workspace_state,
    unique_lines,
    utc_timestamp_precise,
    write_json,
    write_text,
)


AGENTS = ("claude", "codex")
DISPLAY_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
}
SUPERVISOR_COMMANDS = {
    "running": [
        "status",
        "tail claude",
        "tail codex",
        "inspect claude",
        "inspect codex",
        "show final-plan",
        "show package",
        "show diff",
        "show manifest",
        "wait",
        "abort",
    ],
    "boundary": [
        "continue",
        "status",
        "tail claude",
        "tail codex",
        "inspect claude",
        "inspect codex",
        "show final-plan",
        "show package",
        "show diff",
        "show manifest",
        "abort",
    ],
}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path_from_run_dir(run_dir: Path) -> Path:
    return run_dir / "state.json"


def sanitize_terminal_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = OSC_RE.sub("", text)
    text = ANSI_RE.sub("", text)
    return text


def read_bytes_from(path: Path, offset: int) -> tuple[bytes, int]:
    if not path.exists():
        return b"", offset
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
        new_offset = handle.tell()
    return data, new_offset


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def load_state(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text(encoding="utf-8"))
    normalize_state(state)
    return state


def normalize_state(state: dict[str, Any]) -> None:
    state.setdefault("current_phase", "")
    state.setdefault("selected_executor", "")
    state.setdefault("selected_reviewer", "")
    state.setdefault("final_plan", None)
    state.setdefault("current_execution_package", None)
    state.setdefault("execution_packages", [])
    state.setdefault("read_only_violations", [])
    state.setdefault("manual_confirmations_expected", [])
    state.setdefault("notes", [])
    state.setdefault("turns", [])
    state.setdefault("summary", {})
    state.setdefault("logs", {})
    state.setdefault("agents", {})
    state.setdefault("runtime", {})
    state.setdefault("apply_attempts", [])
    state["summary"].setdefault("apply_status", "not-applied")
    state["summary"].setdefault("applied_branch", "")
    state["summary"].setdefault("applied_commit", "")
    state["summary"].setdefault("last_apply_report", "")
    state["summary"].setdefault("last_apply_attempt_id", "")
    state["runtime"].setdefault("transport", "tmux")
    state["runtime"].setdefault("supervisor", "queue")
    state["runtime"].setdefault("transport_resume_supported", True)
    control = state["runtime"].setdefault("control", {})
    control.setdefault("enabled", True)
    control.setdefault("host", "127.0.0.1")
    control.setdefault("port", 0)
    control.setdefault("token", "")
    control.setdefault("base_url", "")
    control.setdefault("events_stream_url", "")
    control.setdefault("web_url", "")
    control.setdefault("open_ui", False)
    control.setdefault("print_control_token", False)
    process = state["runtime"].setdefault("process", {})
    process.setdefault("mode", "tmux")
    process.setdefault("owner_pid", 0)
    process.setdefault("owner_started_at", "")
    process.setdefault("owner_last_seen_at", "")
    process.setdefault("owner_alive", False)
    process.setdefault("owner_exit_code", None)
    process.setdefault("stop_requested_at", "")
    process.setdefault("stopped_at", "")
    process.setdefault("stop_signal", "")
    boundary = state["runtime"].setdefault("boundary", {})
    boundary.setdefault("active", False)
    boundary.setdefault("label", "")
    boundary.setdefault("next_phase", "")
    boundary.setdefault("entered_at", "")
    boundary.setdefault("allowed_commands", [])
    for agent in AGENTS:
        state["agents"].setdefault(agent, {})
        state["agents"][agent].setdefault("workspace", "")
        state["agents"][agent].setdefault("pane_id", "")
        state["agents"][agent].setdefault("transport_ref", "")
        state["agents"][agent].setdefault("transport_kind", state["runtime"]["transport"])
        state["agents"][agent].setdefault("raw_log_path", "")
        state["agents"][agent].setdefault("stream_offset", 0)
        state["agents"][agent].setdefault("last_activity_at", "")
        state["agents"][agent].setdefault("pid", 0)
        state["agents"][agent].setdefault("started_at", "")
        state["agents"][agent].setdefault("exit_code", None)
    state["agents"].setdefault("supervisor", {"pane_id": ""})


def save_state(state: dict[str, Any]) -> None:
    write_json(Path(state["state_file"]), state)


def supervisor_log_line(state: dict[str, Any], message: str) -> None:
    line = f"[peer-forge-live {time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    append_text(Path(state["logs"]["supervisor"]), line + "\n")


def write_supervisor_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    append_text(
        Path(state["logs"]["events_jsonl"]),
        json.dumps(event, ensure_ascii=True) + "\n",
    )


def prompt_file_message(prompt_path: Path) -> str:
    return f"Read {prompt_path} and follow it exactly. Respond in this chat only."


def active_notes_for_turn(state: dict[str, Any], turn_index: int) -> list[dict[str, Any]]:
    return [note for note in state["notes"] if int(note["applies_from_turn"]) <= turn_index]


def next_note_id(state: dict[str, Any]) -> str:
    return f"note-{len(state['notes']) + 1:02d}"


def phase_label(phase: str) -> str:
    if phase.startswith("plan-signoff"):
        return "plan-signoff"
    if phase.startswith("plan-final-fix"):
        return "plan-final-fix"
    if phase.startswith("execution-review"):
        return "execution-review"
    if phase.startswith("execution-signoff"):
        return "execution-signoff"
    if phase.startswith("execution-fix"):
        return "execution-fix"
    return phase


def phase_summary_text(phase: str) -> str:
    labels = {
        "plan-initial": "Plan 1/6: independent initial plans",
        "plan-review": "Plan 2/6: cross-review",
        "plan-revise": "Plan 3/6: revision after review",
        "plan-consensus": "Plan 4/6: consensus on the better base",
        "plan-finalize": "Plan 5/6: base side writes the merged final plan candidate",
        "plan-signoff": "Plan 6/6: both sides sign off on the final plan candidate",
        "plan-final-fix": "Plan 6b: base side revises the final candidate after signoff objections",
        "execute-initial": "Execute 1/4: chosen side implements the final plan",
        "execution-review": "Execute 2/4: peer reviews the implementation package",
        "execution-fix": "Execute 3/4: executor revises after implementation review",
        "execution-signoff": "Execute 4/4: both sides sign off on the implementation candidate",
    }
    return labels.get(phase_label(phase), phase)


def turn_id_for(state: dict[str, Any], phase: str) -> str:
    return f"{len(state['turns']) + 1:02d}-{phase}"


def turn_dir_for(state: dict[str, Any], turn_id: str) -> Path:
    return Path(state["run_dir"]) / "turns" / turn_id


def session_prompt_path_for(state: dict[str, Any], turn_id: str, agent: str) -> Path:
    workspace = Path(state["agents"][agent]["workspace"])
    return workspace / ".peer-forge-live" / "turns" / turn_id / "prompt.txt"


def current_turn(state: dict[str, Any]) -> dict[str, Any]:
    if not state["turns"]:
        raise RuntimeError("No turn has been created.")
    return state["turns"][-1]


def find_turn(state: dict[str, Any], phase: str) -> dict[str, Any] | None:
    for turn in reversed(state["turns"]):
        if turn["phase"] == phase:
            return turn
    return None


def turn_results(turn: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for agent in AGENTS:
        turn_agent = turn["agents"][agent]
        if not turn_agent["active"]:
            continue
        result = turn_agent.get("result")
        if result is None:
            raise RuntimeError(f"Turn {turn['id']} is missing a parsed result for {agent}.")
        results[agent] = result
    return results


def boundary_pending(state: dict[str, Any], phase: str) -> bool:
    if not state["turns"]:
        return False
    turn = current_turn(state)
    return turn["phase"] == phase and turn["status"] == "completed" and state.get("current_phase") == phase


def raw_log_path(state: dict[str, Any], agent: str) -> Path:
    return Path(state["agents"][agent]["raw_log_path"])


def git_mode(state: dict[str, Any]) -> bool:
    return bool(state.get("workspaces", {}).get("git_mode", False))


def read_file_tail(path: Path, lines: int = 40) -> str:
    if not path.exists():
        return "(missing)"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not content:
        return "(empty)"
    return "\n".join(content[-lines:])


def read_text_preview(path: Path, *, max_lines: int = 200) -> str:
    if not path.exists():
        return "(missing)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(empty)"
    truncated = len(lines) > max_lines
    preview = "\n".join(lines[:max_lines])
    if truncated:
        preview += f"\n... ({len(lines) - max_lines} more lines truncated)"
    return preview


def current_execution_package(state: dict[str, Any]) -> dict[str, Any] | None:
    package = state.get("current_execution_package")
    if isinstance(package, dict) and package:
        return package
    return None


def boundary_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    runtime = state.setdefault("runtime", {})
    boundary = runtime.setdefault("boundary", {})
    boundary.setdefault("active", False)
    boundary.setdefault("label", "")
    boundary.setdefault("next_phase", "")
    boundary.setdefault("entered_at", "")
    boundary.setdefault("allowed_commands", [])
    return boundary


def process_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    runtime = state.setdefault("runtime", {})
    process = runtime.setdefault("process", {})
    process.setdefault("mode", "tmux")
    process.setdefault("owner_pid", 0)
    process.setdefault("owner_started_at", "")
    process.setdefault("owner_last_seen_at", "")
    process.setdefault("owner_alive", False)
    process.setdefault("owner_exit_code", None)
    process.setdefault("stop_requested_at", "")
    process.setdefault("stopped_at", "")
    process.setdefault("stop_signal", "")
    return process


def allowed_supervisor_commands(mode: str, *, next_phase: str | None = None) -> list[str]:
    commands = list(SUPERVISOR_COMMANDS.get(mode, []))
    if next_phase is not None:
        insert_at = len(commands) - 1 if commands and commands[-1] == "abort" else len(commands)
        commands.insert(insert_at, "note both <text>")
    return commands


def activate_boundary_state(state: dict[str, Any], *, label: str, next_phase: str | None) -> dict[str, Any]:
    boundary = boundary_runtime_state(state)
    boundary["active"] = True
    boundary["label"] = label
    boundary["next_phase"] = next_phase or ""
    boundary["entered_at"] = utc_timestamp_precise()
    boundary["allowed_commands"] = allowed_supervisor_commands("boundary", next_phase=next_phase)
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "boundary-entered",
            "timestamp": boundary["entered_at"],
            "label": label,
            "next_phase": next_phase or "",
            "allowed_commands": list(boundary["allowed_commands"]),
        },
    )
    return dict(boundary)


def clear_boundary_state(state: dict[str, Any], *, resolution: str) -> None:
    boundary = boundary_runtime_state(state)
    if not boundary.get("active"):
        return
    previous = dict(boundary)
    boundary["active"] = False
    boundary["label"] = ""
    boundary["next_phase"] = ""
    boundary["entered_at"] = ""
    boundary["allowed_commands"] = []
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "boundary-resumed",
            "timestamp": utc_timestamp_precise(),
            "resolution": resolution,
            "label": previous.get("label", ""),
            "next_phase": previous.get("next_phase", ""),
        },
    )


def update_run_status(state: dict[str, Any], status: str, *, detail: str = "") -> bool:
    previous = str(state.get("status", "") or "")
    if previous == status:
        return False
    state["status"] = status
    write_supervisor_event(
        state,
        {
            "type": "run-status-changed",
            "timestamp": utc_timestamp_precise(),
            "previous_status": previous,
            "status": status,
            "detail": detail,
        },
    )
    return True


def final_candidate_path(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "final-plan.json"


def current_final_plan_path(state: dict[str, Any]) -> Path:
    final_plan_file = state.get("summary", {}).get("final_plan_file")
    if final_plan_file:
        return Path(final_plan_file)
    return final_candidate_path(state)


def package_manifest_path(package: dict[str, Any]) -> Path:
    return Path(package["manifest_path"])


def package_diff_path(package: dict[str, Any]) -> Path:
    return Path(package["diff_path"])


def _compact_text_preview(text: str, *, max_lines: int) -> tuple[str, int, bool]:
    lines = text.splitlines()
    if not lines:
        return "(empty)", 0, False
    truncated = len(lines) > max_lines
    preview = "\n".join(lines[:max_lines])
    if truncated:
        preview += f"\n... ({len(lines) - max_lines} more lines truncated)"
    return preview, len(lines), truncated


def text_artifact_payload(path: Path, *, max_lines: int = 200, parse_json_payload: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": path.exists(),
        "path": str(path),
        "preview": "(missing)",
        "line_count": 0,
        "truncated": False,
    }
    if not path.exists():
        if parse_json_payload:
            payload["data"] = None
        return payload
    text = path.read_text(encoding="utf-8", errors="replace")
    preview, line_count, truncated = _compact_text_preview(text, max_lines=max_lines)
    payload["preview"] = preview
    payload["line_count"] = line_count
    payload["truncated"] = truncated
    if parse_json_payload:
        try:
            payload["data"] = json.loads(text)
        except json.JSONDecodeError:
            payload["data"] = None
            payload["parse_error"] = "invalid_json"
    return payload


def current_execution_package_payload(state: dict[str, Any]) -> dict[str, Any]:
    package = current_execution_package(state)
    if package is None:
        return {
            "available": False,
            "package": None,
            "manifest": None,
            "error": "",
        }
    payload: dict[str, Any] = {
        "available": True,
        "package": {
            "turn_id": package.get("turn_id", ""),
            "phase": package.get("phase", ""),
            "executor": package.get("executor", ""),
            "created_at": package.get("created_at", ""),
            "package_dir": package.get("package_dir", ""),
            "manifest_path": package.get("manifest_path", ""),
            "diff_path": package.get("diff_path", ""),
            "changed_files": list(package.get("changed_files", [])),
        },
        "manifest": None,
        "error": "",
    }
    try:
        payload["manifest"] = load_execution_manifest(package)
    except RuntimeError as exc:
        payload["error"] = str(exc)
    return payload


def final_plan_payload(state: dict[str, Any], *, max_lines: int = 200) -> dict[str, Any]:
    return text_artifact_payload(current_final_plan_path(state), max_lines=max_lines, parse_json_payload=True)


def current_diff_payload(state: dict[str, Any], *, max_lines: int = 300) -> dict[str, Any]:
    package = current_execution_package(state)
    if package is None:
        return {
            "available": False,
            "path": "",
            "preview": "(missing)",
            "line_count": 0,
            "truncated": False,
        }
    return text_artifact_payload(package_diff_path(package), max_lines=max_lines, parse_json_payload=False)


def build_dashboard_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    runtime = state.get("runtime", {})
    control = runtime.get("control", {}) if isinstance(runtime, dict) else {}
    process = runtime.get("process", {}) if isinstance(runtime, dict) else {}
    boundary = dict(boundary_runtime_state(state))
    summary = state.get("summary", {})
    package_payload = current_execution_package_payload(state)
    final_plan = final_plan_payload(state, max_lines=80)
    current_diff = current_diff_payload(state, max_lines=80)
    turns_payload: list[dict[str, Any]] = []
    current = state["turns"][-1] if state.get("turns") else None
    for turn in state.get("turns", []):
        active_agents = [agent for agent in AGENTS if turn["agents"][agent]["active"]]
        turns_payload.append(
            {
                "id": turn["id"],
                "index": turn.get("index", 0),
                "phase": turn["phase"],
                "phase_family": turn.get("phase_family", phase_label(turn["phase"])),
                "mode": turn.get("mode", ""),
                "summary": turn.get("summary", ""),
                "status": turn["status"],
                "started_at": turn.get("started_at", ""),
                "completed_at": turn.get("completed_at", ""),
                "active_agents": active_agents,
                "read_only_agents": [agent for agent in active_agents if turn["agents"][agent]["read_only"]],
            }
        )
    current_turn_payload = None
    if current is not None:
        current_turn_payload = {
            "id": current["id"],
            "index": current.get("index", 0),
            "phase": current["phase"],
            "phase_family": current.get("phase_family", phase_label(current["phase"])),
            "mode": current.get("mode", ""),
            "summary": current.get("summary", ""),
            "status": current["status"],
            "started_at": current.get("started_at", ""),
            "completed_at": current.get("completed_at", ""),
        }
    agents_payload: dict[str, Any] = {}
    for agent in AGENTS:
        agent_state = state["agents"][agent]
        turn_agent = current["agents"][agent] if current is not None else None
        agents_payload[agent] = {
            "active": bool(turn_agent["active"]) if turn_agent is not None else False,
            "status": turn_agent["status"] if turn_agent is not None else "idle",
            "read_only": bool(turn_agent["read_only"]) if turn_agent is not None else False,
            "runtime": agent_runtime_ref(state, agent),
            "transport_kind": agent_state.get("transport_kind", ""),
            "workspace": agent_state.get("workspace", ""),
            "last_activity_at": agent_state.get("last_activity_at", ""),
            "nudge_count": int(turn_agent.get("nudge_count", 0)) if turn_agent is not None else 0,
            "parse_error": turn_agent.get("parse_error", "") if turn_agent is not None else "",
        }
    return {
        "run": {
            "run_id": state["run_id"],
            "status": state.get("status", ""),
            "current_phase": state.get("current_phase", ""),
            "session_name": state.get("session_name", ""),
            "transport": runtime.get("transport", "") if isinstance(runtime, dict) else "",
            "supervisor": runtime.get("supervisor", "") if isinstance(runtime, dict) else "",
            "created_at": state.get("created_at", ""),
        },
        "control": {
            "base_url": control.get("base_url", "") if isinstance(control, dict) else "",
            "events_stream_url": control.get("events_stream_url", "") if isinstance(control, dict) else "",
            "web_url": control.get("web_url", "") if isinstance(control, dict) else "",
        },
        "process": {
            "mode": process.get("mode", "") if isinstance(process, dict) else "",
            "owner_pid": process.get("owner_pid", 0) if isinstance(process, dict) else 0,
            "owner_started_at": process.get("owner_started_at", "") if isinstance(process, dict) else "",
            "owner_last_seen_at": process.get("owner_last_seen_at", "") if isinstance(process, dict) else "",
            "owner_alive": bool(process.get("owner_alive", False)) if isinstance(process, dict) else False,
            "owner_exit_code": process.get("owner_exit_code") if isinstance(process, dict) else None,
            "stop_requested_at": process.get("stop_requested_at", "") if isinstance(process, dict) else "",
        },
        "boundary": boundary,
        "summary": {
            "plan_approved": bool(summary.get("plan_approved", False)),
            "execution_approved": bool(summary.get("execution_approved", False)),
            "final_approved": bool(summary.get("final_approved", False)),
            "selected_executor": state.get("selected_executor", ""),
            "selected_reviewer": state.get("selected_reviewer", ""),
            "read_only_violations": len(state.get("read_only_violations", [])),
            "notes_count": len(state.get("notes", [])),
        },
        "turns": turns_payload,
        "current_turn": current_turn_payload,
        "agents": agents_payload,
        "artifacts": {
            "final_plan_available": bool(final_plan.get("available", False)),
            "current_package_available": bool(package_payload.get("available", False)),
            "current_diff_available": bool(current_diff.get("available", False)),
            "current_package_changed_files": len(
                (package_payload.get("package") or {}).get("changed_files", []) if package_payload.get("package") else []
            ),
        },
        "notes": [
            {
                "id": note.get("id", ""),
                "summary": note.get("summary", ""),
                "applies_from_phase": note.get("applies_from_phase", ""),
                "applies_from_turn": note.get("applies_from_turn", 0),
            }
            for note in state.get("notes", [])
        ],
    }


def append_combined_verbose(state: dict[str, Any], agent: str, text: str) -> None:
    if not text:
        return
    verbose_path = Path(state["logs"]["verbose"])
    timestamp = utc_timestamp_precise()
    lines = text.splitlines()
    if text.endswith("\n"):
        lines.append("")
    rendered: list[str] = []
    for line in lines:
        if not line and not rendered:
            continue
        rendered.append(f"[{timestamp}][{agent}] {line}")
    if rendered:
        append_text(verbose_path, "\n".join(rendered) + "\n")


def record_agent_output(state: dict[str, Any], agent: str, text: str) -> str:
    if not text:
        return ""
    sanitized = sanitize_terminal_text(text)
    if not sanitized:
        return ""
    state["agents"][agent]["last_activity_at"] = utc_timestamp_precise()
    append_combined_verbose(state, agent, sanitized)
    return sanitized


def capture_read_only_snapshot(state: dict[str, Any], turn: dict[str, Any], agent: str) -> None:
    turn_agent = turn["agents"][agent]
    if not turn_agent["active"] or not turn_agent["read_only"]:
        return
    workspace = Path(state["agents"][agent]["workspace"])
    status, diff = snapshot_workspace_state(workspace, git_mode(state))
    write_text(Path(turn_agent["entry_snapshot_status_path"]), status)
    write_text(Path(turn_agent["entry_snapshot_diff_path"]), diff)
    turn_agent["entry_snapshot_taken_at"] = utc_timestamp_precise()


def read_only_violation_record_path(state: dict[str, Any], turn: dict[str, Any], agent: str) -> Path:
    return Path(state["run_dir"]) / "read-only-violations" / f"{turn['id']}-{agent}.json"


def validate_read_only_snapshot(state: dict[str, Any], turn: dict[str, Any], agent: str) -> None:
    turn_agent = turn["agents"][agent]
    if not turn_agent["active"] or not turn_agent["read_only"]:
        return
    before_status = Path(turn_agent["entry_snapshot_status_path"]).read_text(encoding="utf-8")
    before_diff = Path(turn_agent["entry_snapshot_diff_path"]).read_text(encoding="utf-8")
    workspace = Path(state["agents"][agent]["workspace"])
    after_status, after_diff = snapshot_workspace_state(workspace, git_mode(state))
    if before_status == after_status and before_diff == after_diff:
        return
    message = f"{agent} changed its workspace during read-only phase '{turn['phase']}'."
    record = {
        "turn_id": turn["id"],
        "phase": turn["phase"],
        "agent": agent,
        "detected_at": utc_timestamp_precise(),
        "message": message,
        "workspace": str(workspace),
        "entry_snapshot_status_path": turn_agent["entry_snapshot_status_path"],
        "entry_snapshot_diff_path": turn_agent["entry_snapshot_diff_path"],
        "record_file": str(read_only_violation_record_path(state, turn, agent)),
    }
    write_json(Path(record["record_file"]), record)
    state["read_only_violations"].append(record)
    turn_agent["read_only_violation"] = record
    turn_agent["status"] = "failed"
    turn_agent["parse_error"] = message
    turn["status"] = "failed"
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "read-only-violation",
            "timestamp": utc_timestamp_precise(),
            "turn_id": turn["id"],
            "phase": turn["phase"],
            "agent": agent,
            "message": message,
            "record_file": record["record_file"],
        },
    )
    raise RuntimeError(message)


def stream_new_agent_output(state: dict[str, Any], agent: str) -> str:
    agent_state = state["agents"][agent]
    raw_path = Path(agent_state["raw_log_path"])
    data, new_offset = read_bytes_from(raw_path, int(agent_state.get("stream_offset", 0)))
    agent_state["stream_offset"] = new_offset
    if not data:
        return ""
    return record_agent_output(state, agent, data.decode("utf-8", errors="replace"))


def agent_runtime_ref(state: dict[str, Any], agent: str) -> str:
    agent_state = state["agents"][agent]
    kind = str(agent_state.get("transport_kind", "") or state.get("runtime", {}).get("transport", ""))
    ref = str(agent_state.get("transport_ref", "") or "")
    if ref:
        return f"{kind}:{ref}" if kind else ref
    pane_id = str(agent_state.get("pane_id", "") or "")
    if pane_id:
        return f"tmux:{pane_id}"
    return "n/a"


def summarize_agent_result(phase: str, payload: dict[str, Any]) -> str:
    family = phase_label(phase)
    if family in {"plan-review", "execution-review"}:
        findings = payload.get("findings", [])
        top = ""
        if findings:
            top = f", top={clip_text(str(findings[0].get('title', '')), limit=60)}"
        return (
            f"verdict={payload.get('overall_verdict', '')}, "
            f"findings={len(findings)}, must_fix={len(payload.get('must_fix', []))}{top}"
        )
    if family == "plan-consensus":
        blockers = len(payload.get("blocking_objections_to_self_final", [])) + len(
            payload.get("blocking_objections_to_peer_final", [])
        )
        return (
            f"preferred_base={payload.get('preferred_base', '')}, "
            f"approve_self={payload.get('approve_self_as_final', False)}, "
            f"approve_peer={payload.get('approve_peer_as_final', False)}, blockers={blockers}"
        )
    if family in {"plan-signoff", "execution-signoff"}:
        return (
            f"verdict={payload.get('overall_verdict', '')}, "
            f"blockers={len(payload.get('blocking_findings', []))}, "
            f"preserve={len(payload.get('must_preserve', []))}"
        )
    risks = payload.get("risks", payload.get("remaining_risks", []))
    return (
        f"summary={clip_text(str(payload.get('summary', '')), limit=80)}, "
        f"steps={len(payload.get('steps', []))}, risks={len(risks)}, tests={len(payload.get('tests', []))}"
    )


def summarize_signoff_objections(signoffs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        str(payload.get("summary", "")).strip()
        for payload in signoffs.values()
        if payload.get("overall_verdict") == "reject"
    ]
    blocking_findings: list[dict[str, Any]] = []
    must_preserve: list[str] = []
    for payload in signoffs.values():
        blocking_findings.extend(normalize_findings(payload.get("blocking_findings", [])))
        must_preserve.extend(str(item).strip() for item in payload.get("must_preserve", []))
    return {
        "summary": "\n".join(item for item in summaries if item),
        "blocking_findings": blocking_findings,
        "must_preserve": unique_lines(must_preserve),
    }


def execution_package_root(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "packages"


def execution_package_dir_for(state: dict[str, Any], turn_id: str, agent: str) -> Path:
    return execution_package_root(state) / turn_id / agent


def peer_agent(agent: str) -> str:
    for candidate in AGENTS:
        if candidate != agent:
            return candidate
    raise ValueError(f"No peer agent for {agent}")


def collect_execution_package(
    state: dict[str, Any],
    *,
    turn: dict[str, Any],
    executor: str,
    execution_summary: dict[str, Any],
) -> dict[str, Any]:
    package_dir = execution_package_dir_for(state, turn["id"], executor)
    changed_files, diff_path = collect_package(
        Path(state["agents"][executor]["workspace"]),
        Path(state["workspaces"]["baseline"]),
        package_dir,
        git_mode(state),
    )
    record = {
        "turn_id": turn["id"],
        "phase": turn["phase"],
        "executor": executor,
        "created_at": utc_timestamp_precise(),
        "package_dir": str(package_dir),
        "manifest_path": str(package_dir / "manifest.json"),
        "diff_path": str(diff_path),
        "changed_files": changed_files,
        "summary": execution_summary,
    }
    state["execution_packages"].append(record)
    state["current_execution_package"] = record
    state["summary"]["current_execution_package"] = record
    save_state(state)
    return record


def ensure_execution_package(
    state: dict[str, Any],
    *,
    turn: dict[str, Any],
    executor: str,
    execution_summary: dict[str, Any],
) -> dict[str, Any]:
    existing = None
    current = current_execution_package(state)
    if current and current.get("turn_id") == turn["id"] and current.get("executor") == executor:
        existing = current
    if existing is None:
        for record in reversed(state.get("execution_packages", [])):
            if record.get("turn_id") == turn["id"] and record.get("executor") == executor:
                existing = record
                break
    if existing is not None and Path(existing.get("package_dir", "")).exists():
        state["current_execution_package"] = existing
        state["summary"]["current_execution_package"] = existing
        save_state(state)
        return existing
    return collect_execution_package(
        state,
        turn=turn,
        executor=executor,
        execution_summary=execution_summary,
    )


def ensure_plan_merge_brief(
    state: dict[str, Any],
    plan_consensus: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    final_plan_base = choose_final_base(plan_consensus["claude"], plan_consensus["codex"])
    merge_brief = build_merge_brief(final_plan_base, plan_consensus["claude"], plan_consensus["codex"])
    merge_path = Path(state["run_dir"]) / "plan-merge-brief.json"
    write_json(merge_path, merge_brief)
    state["summary"]["final_plan_base"] = final_plan_base
    state["summary"]["merge_brief_file"] = str(merge_path)
    save_state(state)
    return final_plan_base, merge_brief


def persist_final_candidate(state: dict[str, Any], candidate: dict[str, Any]) -> None:
    state["final_plan"] = candidate
    write_json(final_candidate_path(state), candidate)
    state["summary"]["final_plan_file"] = str(final_candidate_path(state))
    save_state(state)


def report_path(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "report.json"


def report_md_path(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "report.md"


def build_report(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("summary", {})
    return {
        "run_id": state["run_id"],
        "mode": "peer-forge-live-v2",
        "repo": state["repo"],
        "task": state["task"],
        "acceptance": state["acceptance"],
        "scope": state["scope"],
        "status": state["status"],
        "session_name": state["session_name"],
        "run_dir": state["run_dir"],
        "state_file": state["state_file"],
        "created_at": state["created_at"],
        "updated_at": utc_timestamp_precise(),
        "current_phase": state.get("current_phase", ""),
        "selected_executor": state.get("selected_executor", ""),
        "selected_reviewer": state.get("selected_reviewer", ""),
        "final_plan": state.get("final_plan"),
        "current_execution_package": state.get("current_execution_package"),
        "execution_packages": state.get("execution_packages", []),
        "apply_attempts": state.get("apply_attempts", []),
        "read_only_violations": state.get("read_only_violations", []),
        "manual_confirmations_expected": state.get("manual_confirmations_expected", []),
        "runtime": state.get("runtime", {}),
        "workspaces": state.get("workspaces", {}),
        "logs": state["logs"],
        "notes": state["notes"],
        "turns": [
            {
                "id": turn["id"],
                "phase": turn["phase"],
                "mode": turn.get("mode", ""),
                "status": turn["status"],
                "started_at": turn["started_at"],
                "completed_at": turn["completed_at"],
                "agents": {
                    agent: {
                        "active": turn["agents"][agent]["active"],
                        "read_only": turn["agents"][agent]["read_only"],
                        "status": turn["agents"][agent]["status"],
                        "prompt_path": turn["agents"][agent]["prompt_path"],
                        "session_prompt_path": turn["agents"][agent]["session_prompt_path"],
                        "result_path": turn["agents"][agent]["result_path"],
                        "turn_log_path": turn["agents"][agent]["turn_log_path"],
                        "entry_snapshot_status_path": turn["agents"][agent]["entry_snapshot_status_path"],
                        "entry_snapshot_diff_path": turn["agents"][agent]["entry_snapshot_diff_path"],
                        "read_only_violation": turn["agents"][agent]["read_only_violation"],
                    }
                    for agent in AGENTS
                },
            }
            for turn in state["turns"]
        ],
        "summary": summary,
    }


def report_markdown(data: dict[str, Any]) -> str:
    summary = data.get("summary", {})
    plan_approved = bool(summary.get("plan_approved", False))
    execution_approved = bool(summary.get("execution_approved", False))
    final_approved = bool(summary.get("final_approved", False))
    apply_attempts = data.get("apply_attempts", [])
    lines = [
        f"# Peer Forge Live Run {data['run_id']}",
        "",
        f"- Repo: `{data['repo']}`",
        f"- Task: {data['task']}",
        f"- Status: `{data['status']}`",
        f"- Session: `{data['session_name']}`",
        f"- Transport: `{data.get('runtime', {}).get('transport', '')}`",
        f"- Supervisor: `{data.get('runtime', {}).get('supervisor', '')}`",
        f"- Plan approved: `{plan_approved}`",
        f"- Execution approved: `{execution_approved}`",
        f"- Final approved: `{final_approved}`",
        f"- Final plan base: `{summary.get('final_plan_base', '')}`",
        f"- Selected executor: `{data.get('selected_executor', '')}`",
        f"- Selected reviewer: `{data.get('selected_reviewer', '')}`",
        f"- Apply status: `{summary.get('apply_status', 'not-applied')}`",
        f"- Applied branch: `{summary.get('applied_branch', '')}`",
        f"- Applied commit: `{summary.get('applied_commit', '')}`",
        f"- Read-only violations: `{len(data.get('read_only_violations', []))}`",
        f"- Run dir: `{data['run_dir']}`",
        "",
        "## Logs",
        f"- State: `{data['state_file']}`",
        f"- Supervisor: `{data['logs']['supervisor']}`",
        f"- Combined verbose: `{data['logs']['verbose']}`",
        f"- Claude raw: `{data['logs']['claude_raw']}`",
        f"- Codex raw: `{data['logs']['codex_raw']}`",
    ]
    final_plan_file = summary.get("final_plan_file")
    if final_plan_file:
        lines.append(f"- Final plan: `{final_plan_file}`")
    current_execution_package = data.get("current_execution_package")
    if current_execution_package:
        lines.append(f"- Current execution package: `{current_execution_package.get('package_dir', '')}`")
    if data.get("manual_confirmations_expected"):
        lines.extend(["", "## Manual Confirmations"])
        lines.extend(f"- {item}" for item in data["manual_confirmations_expected"])
    lines.extend(["", "## Notes"])
    if data.get("notes"):
        lines.extend(
            f"- `{note['id']}` applies from `{note['applies_from_phase']}`: {note['summary']}"
            for note in data["notes"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Read-Only Violations"])
    if data.get("read_only_violations"):
        lines.extend(
            f"- `{item['turn_id']}` `{item['agent']}`: {item['message']}"
            for item in data["read_only_violations"]
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Apply Attempts"])
    if apply_attempts:
        lines.extend(
            f"- `{item.get('attempt_id', '')}` `{item.get('status', '')}` branch=`{item.get('target_branch', '')}` commit=`{item.get('commit_sha', '')}`"
            for item in apply_attempts
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Turns"])
    for turn in data.get("turns", []):
        lines.append(f"- `{turn['id']}` `{turn['mode']}` `{turn['status']}`")
    return "\n".join(lines) + "\n"


def persist_report(state: dict[str, Any]) -> None:
    data = build_report(state)
    write_json(report_path(state), data)
    write_text(report_md_path(state), report_markdown(data))


def apply_root(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "apply"


def apply_history_path(state: dict[str, Any]) -> Path:
    return apply_root(state) / "history.jsonl"


def apply_report_json_path(state: dict[str, Any], attempt_id: str) -> Path:
    return apply_root(state) / f"{attempt_id}-report.json"


def apply_report_md_path(state: dict[str, Any], attempt_id: str) -> Path:
    return apply_root(state) / f"{attempt_id}-report.md"


def normalized_rel_path(rel: str) -> str:
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise RuntimeError(f"Package path must be relative: {rel}")
    parts = rel_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"Package path is not safe: {rel}")
    return rel_path.as_posix()


def current_branch_name(repo: Path) -> str:
    proc = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def branch_exists(repo: Path, branch: str) -> bool:
    proc = git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return proc.returncode == 0


def git_status_porcelain(repo: Path, paths: list[str] | None = None) -> list[str]:
    cmd = ["status", "--porcelain"]
    if paths:
        cmd.extend(["--", *paths])
    proc = git(repo, *cmd)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def git_dirty_paths(repo: Path) -> list[str]:
    tracked = git(repo, "diff", "--name-only", "--find-renames", "HEAD").stdout.splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return unique_lines([normalized_rel_path(path) for path in tracked + untracked if path.strip()])


def git_changed_paths_between(repo: Path, start_ref: str, end_ref: str) -> list[str]:
    if not start_ref or not end_ref or start_ref == end_ref:
        return []
    proc = git(repo, "diff", "--name-only", "--find-renames", f"{start_ref}..{end_ref}", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Unable to diff repository paths between {start_ref} and {end_ref}: {proc.stderr or proc.stdout}")
    return unique_lines([normalized_rel_path(path) for path in proc.stdout.splitlines() if path.strip()])


def overlap_paths(left: list[str], right: list[str]) -> list[str]:
    right_set = set(right)
    return [path for path in left if path in right_set]


def package_files_root(package: dict[str, Any]) -> Path:
    return Path(package["package_dir"]) / "files"


def load_execution_manifest(package: dict[str, Any]) -> dict[str, Any]:
    manifest_path = package_manifest_path(package)
    if not manifest_path.exists():
        raise RuntimeError(f"Execution package manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Execution package manifest is not an object: {manifest_path}")
    changed_files = [normalized_rel_path(str(item)) for item in manifest.get("changed_files", [])]
    copied_files = [normalized_rel_path(str(item)) for item in manifest.get("copied_files", [])]
    deleted_files = [normalized_rel_path(str(item)) for item in manifest.get("deleted_files", [])]
    changed_set = set(changed_files)
    if any(path not in changed_set for path in copied_files + deleted_files):
        raise RuntimeError(f"Execution package manifest has inconsistent copied/deleted paths: {manifest_path}")
    overlap = set(copied_files) & set(deleted_files)
    if overlap:
        raise RuntimeError(f"Execution package manifest marks the same path as copied and deleted: {sorted(overlap)!r}")
    files_root = package_files_root(package)
    for rel in copied_files:
        source = files_root / rel
        if not source.exists():
            raise RuntimeError(f"Execution package is missing copied file payload: {source}")
        if not source.is_file():
            raise RuntimeError(f"Execution package payload is not a regular file: {source}")
    return {
        "changed_files": changed_files,
        "copied_files": copied_files,
        "deleted_files": deleted_files,
    }


def apply_attempt_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Peer Forge Live Apply {data['attempt_id']}",
        "",
        f"- Run: `{data['run_id']}`",
        f"- Status: `{data['status']}`",
        f"- Decision: `{data.get('decision', '')}`",
        f"- Apply requested: `{data['apply_requested']}`",
        f"- Commit requested: `{data['commit_requested']}`",
        f"- Repo: `{data['repo']}`",
        f"- State file: `{data['state_file']}`",
        f"- Package dir: `{data['package_dir']}`",
        f"- Manifest: `{data['manifest_path']}`",
        f"- Diff: `{data['diff_path']}`",
        f"- Target branch: `{data['target_branch']}`",
        f"- Original branch: `{data.get('original_branch', '')}`",
        f"- Commit SHA: `{data.get('commit_sha', '')}`",
        "",
        "## Package",
        f"- Changed files: `{len(data.get('changed_files', []))}`",
        f"- Copied files: `{len(data.get('copied_files', []))}`",
        f"- Deleted files: `{len(data.get('deleted_files', []))}`",
        f"- Safe paths: `{len(data.get('safe_paths', []))}`",
        f"- Blocked paths: `{len(data.get('blocked_paths', []))}`",
        "",
        "## Path Analysis",
        f"- Dirty paths: `{len(data.get('dirty_paths', []))}`",
        f"- Drift paths: `{len(data.get('drift_paths', []))}`",
        f"- Dirty overlap: `{len(data.get('dirty_overlap', []))}`",
        f"- Drift overlap: `{len(data.get('drift_overlap', []))}`",
        f"- Requires --allow-dirty-target: `{data.get('requires_allow_dirty_target', False)}`",
        f"- Requires --allow-base-drift: `{data.get('requires_allow_base_drift', False)}`",
        "",
        "## Blockers",
    ]
    blockers = data.get("blockers", [])
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- None")
    lines.extend(["", "## Safe Paths"])
    safe_paths = data.get("safe_paths", [])
    if safe_paths:
        lines.extend(f"- {item}" for item in safe_paths)
    else:
        lines.append("- None")
    lines.extend(["", "## Blocked Paths"])
    blocked_paths = data.get("blocked_paths", [])
    if blocked_paths:
        lines.extend(f"- {item}" for item in blocked_paths)
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings"])
    warnings = data.get("warnings", [])
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- None")
    lines.extend(["", "## Actions"])
    actions = data.get("actions", [])
    if actions:
        lines.extend(f"- {item}" for item in actions)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def persist_apply_attempt(state: dict[str, Any], report: dict[str, Any]) -> None:
    attempt_id = str(report["attempt_id"])
    json_path = apply_report_json_path(state, attempt_id)
    md_path = apply_report_md_path(state, attempt_id)
    report["report_json"] = str(json_path)
    report["report_md"] = str(md_path)
    write_json(json_path, report)
    write_text(md_path, apply_attempt_markdown(report))
    append_text(apply_history_path(state), json.dumps(report, ensure_ascii=True) + "\n")
    state.setdefault("apply_attempts", []).append(
        {
            "attempt_id": attempt_id,
            "status": report.get("status", ""),
            "target_branch": report.get("target_branch", ""),
            "commit_sha": report.get("commit_sha", ""),
            "report_json": str(json_path),
            "report_md": str(md_path),
            "created_at": report.get("created_at", ""),
        }
    )
    state["summary"]["apply_status"] = str(report.get("status", "not-applied"))
    state["summary"]["applied_branch"] = str(report.get("target_branch", ""))
    state["summary"]["applied_commit"] = str(report.get("commit_sha", ""))
    state["summary"]["last_apply_report"] = str(json_path)
    state["summary"]["last_apply_attempt_id"] = attempt_id
    save_state(state)
    persist_report(state)


def materialize_execution_package(repo: Path, package: dict[str, Any], manifest: dict[str, Any]) -> None:
    files_root = package_files_root(package)
    for rel in manifest["copied_files"]:
        src = files_root / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in manifest["deleted_files"]:
        dst = repo / rel
        if dst.exists():
            dst.unlink()


def default_apply_branch(state: dict[str, Any]) -> str:
    return f"peer-forge/{state['run_id']}"


def initialize_state(args: argparse.Namespace, *, repo: Path, task: str, run_dir: Path, session_name: str) -> dict[str, Any]:
    state = {
        "run_id": run_dir.name,
        "created_at": utc_timestamp_precise(),
        "status": "starting",
        "repo": str(repo),
        "task": task,
        "acceptance": list(args.acceptance),
        "scope": list(args.scope),
        "include_path": list(args.include_path),
        "claude_model": args.claude_model,
        "codex_model": args.codex_model,
        "claude_bare": bool(args.claude_bare),
        "signoff_rounds": args.signoff_rounds,
        "watchdog_seconds": args.watchdog_seconds,
        "max_watchdog_nudges": args.max_watchdog_nudges,
        "run_dir": str(run_dir),
        "state_file": str(state_path_from_run_dir(run_dir)),
        "tool_repo_root": str(repo_root()),
        "session_name": session_name,
        "current_phase": "",
        "selected_executor": "",
        "selected_reviewer": "",
        "final_plan": None,
        "current_execution_package": None,
        "execution_packages": [],
        "read_only_violations": [],
        "apply_attempts": [],
        "manual_confirmations_expected": [
            "Claude may ask you to confirm entering bypassPermissions mode.",
            "Codex may ask you to trust the generated workspace before proceeding.",
        ],
        "runtime": {
            "transport": str(getattr(args, "transport", "tmux")),
            "supervisor": "queue",
            "transport_resume_supported": str(getattr(args, "transport", "tmux")) == "tmux",
            "control": {
                "enabled": True,
                "host": str(getattr(args, "control_host", "127.0.0.1") or "127.0.0.1"),
                "port": int(getattr(args, "control_port", 0) or 0),
                "token": "",
                "base_url": "",
                "events_stream_url": "",
                "web_url": "",
                "open_ui": bool(getattr(args, "open_ui", False)),
                "print_control_token": bool(getattr(args, "print_control_token", False)),
            },
            "process": {
                "mode": (
                    "pty-detached"
                    if str(getattr(args, "transport", "tmux")) == "pty" and bool(getattr(args, "no_attach", False))
                    else ("pty-inline" if str(getattr(args, "transport", "tmux")) == "pty" else "tmux")
                ),
                "owner_pid": 0,
                "owner_started_at": "",
                "owner_last_seen_at": "",
                "owner_alive": False,
                "owner_exit_code": None,
                "stop_requested_at": "",
                "stopped_at": "",
                "stop_signal": "",
            },
            "boundary": {
                "active": False,
                "label": "",
                "next_phase": "",
                "entered_at": "",
                "allowed_commands": [],
            },
        },
        "notes": [],
        "turns": [],
        "summary": {},
        "logs": {
            "supervisor": str(run_dir / "supervisor.log"),
            "verbose": str(run_dir / "panes" / "verbose.log"),
            "claude_raw": str(run_dir / "panes" / "claude.raw.log"),
            "codex_raw": str(run_dir / "panes" / "codex.raw.log"),
            "supervisor_raw": str(run_dir / "panes" / "supervisor.raw.log"),
            "events_jsonl": str(run_dir / "events.jsonl"),
        },
        "agents": {
            "claude": {
                "workspace": "",
                "pane_id": "",
                "transport_ref": "",
                "transport_kind": str(getattr(args, "transport", "tmux")),
                "raw_log_path": str(run_dir / "panes" / "claude.raw.log"),
                "stream_offset": 0,
                "last_activity_at": "",
                "pid": 0,
                "started_at": "",
                "exit_code": None,
            },
            "codex": {
                "workspace": "",
                "pane_id": "",
                "transport_ref": "",
                "transport_kind": str(getattr(args, "transport", "tmux")),
                "raw_log_path": str(run_dir / "panes" / "codex.raw.log"),
                "stream_offset": 0,
                "last_activity_at": "",
                "pid": 0,
                "started_at": "",
                "exit_code": None,
            },
            "supervisor": {
                "pane_id": "",
            },
        },
    }
    write_text(Path(state["logs"]["supervisor"]), "")
    write_text(Path(state["logs"]["verbose"]), "")
    write_text(Path(state["logs"]["events_jsonl"]), "")
    return state


def pane_by_title(panes: list[dict[str, str]], title: str) -> dict[str, str] | None:
    for pane in panes:
        if pane.get("pane_title") == title:
            return pane
    return None

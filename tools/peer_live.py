#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import select
import shutil
import sys
import time
import traceback
import uuid
from typing import Any, Callable

from live_protocol import (
    build_execution_fix_prompt,
    build_execution_prompt,
    build_execution_review_prompt,
    build_execution_signoff_prompt,
    build_final_fix_prompt,
    build_plan_consensus_prompt,
    build_plan_finalize_prompt,
    build_plan_initial_prompt,
    build_plan_review_prompt,
    build_plan_revise_prompt,
    build_plan_signoff_prompt,
    build_watchdog_nudge,
    parse_turn_result,
)
from live_tmux import (
    attach_session,
    capture_pane,
    ensure_tmux,
    has_session,
    kill_session,
    list_panes,
    new_session,
    paste_message,
    pipe_pane,
    respawn_pane,
    select_layout,
    set_pane_title,
    set_remain_on_exit,
    split_window,
)
from peer_consensus import (
    build_merge_brief,
    choose_final_base,
    clip_text,
    collect_package,
    ensure_cli,
    git,
    normalize_findings,
    prepare_workspaces,
    read_task,
    snapshot_workspace_state,
    unique_lines,
    utc_now,
    utc_timestamp_precise,
    write_json,
    write_text,
)


AGENTS = ("claude", "codex")
DISPLAY_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path_from_run_dir(run_dir: Path) -> Path:
    return run_dir / "state.json"


def parse_args() -> argparse.Namespace:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        return parse_serve_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "resume":
        return parse_resume_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        return parse_apply_args(sys.argv[2:])
    return parse_start_args(sys.argv[1:])


def parse_start_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a live, tmux-based Peer Forge run with interactive Claude and Codex sessions.",
    )
    parser.set_defaults(command="start")
    parser.add_argument("--repo", default=".", help="Repository or workspace root. Defaults to the current directory.")
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("--task", help="Task description.")
    task_group.add_argument("--task-file", help="Path to a file containing the task description.")
    parser.add_argument(
        "--acceptance",
        action="append",
        default=[],
        help="Acceptance criteria line. Repeatable.",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        help="Preferred file or directory scope. Repeatable.",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=[],
        help="Extra file or directory to copy into isolated workspaces when it is not tracked by git.",
    )
    parser.add_argument("--claude-model", help="Claude model override.")
    parser.add_argument("--codex-model", help="Codex model override.")
    parser.add_argument(
        "--signoff-rounds",
        type=int,
        default=1,
        help="Maximum additional final-fix/signoff rounds after the first signoff. Default: 1.",
    )
    parser.add_argument(
        "--watchdog-seconds",
        type=int,
        default=180,
        help="Idle seconds before a symmetric watchdog nudge is sent to both agents. Default: 180.",
    )
    parser.add_argument(
        "--max-watchdog-nudges",
        type=int,
        default=1,
        help="Maximum symmetric watchdog nudges per active turn. Default: 1.",
    )
    parser.add_argument(
        "--run-root",
        help="Override the artifact root. Defaults to <repo>/.claude/tmp/peer-forge-live.",
    )
    parser.add_argument(
        "--session-name",
        help="Optional tmux session name. Defaults to peer-forge-live-<run suffix>.",
    )
    parser.add_argument(
        "--no-attach",
        action="store_true",
        help="Create the tmux session and start the run, but do not attach to it.",
    )
    parser.add_argument(
        "--no-claude-bare",
        action="store_true",
        help="Disable Claude bare mode. Bare mode is enabled by default to reduce prompt contamination.",
    )
    args = parser.parse_args(argv)
    if args.signoff_rounds < 0:
        parser.error("--signoff-rounds must be >= 0.")
    if args.watchdog_seconds < 0:
        parser.error("--watchdog-seconds must be >= 0.")
    if args.max_watchdog_nudges < 0:
        parser.error("--max-watchdog-nudges must be >= 0.")
    return args


def parse_serve_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Internal live supervisor entrypoint for peer-forge-live.",
    )
    parser.set_defaults(command="serve")
    parser.add_argument("--state-file", required=True, help="Path to the live run state.json file.")
    return parser.parse_args(argv)


def parse_resume_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resume or re-attach to an existing peer-forge-live run.",
    )
    parser.set_defaults(command="resume")
    parser.add_argument("--state-file", required=True, help="Path to an existing live run state.json file.")
    parser.add_argument(
        "--no-attach",
        action="store_true",
        help="Repair supervisor state if needed, but print attach info instead of attaching immediately.",
    )
    return parser.parse_args(argv)


def parse_apply_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or apply an approved peer-forge-live execution package to the target repository.",
    )
    parser.set_defaults(command="apply")
    parser.add_argument("--state-file", required=True, help="Path to an existing live run state.json file.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Materialize the final execution package into the target repository. Without this flag, only a dry-run preview is produced.",
    )
    parser.add_argument(
        "--branch",
        help="Target branch name for the apply step. Defaults to peer-forge/<run-id>. Use 'current' to stay on the current branch.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Create a git commit after applying the package. Requires --apply.",
    )
    parser.add_argument(
        "--allow-base-drift",
        action="store_true",
        help="Allow apply even if the target repository HEAD has moved since the live run started.",
    )
    parser.add_argument(
        "--allow-dirty-target",
        action="store_true",
        help="Allow apply when the target repository has unrelated dirty paths that do not overlap the execution package.",
    )
    args = parser.parse_args(argv)
    if args.commit and not args.apply:
        parser.error("--commit requires --apply.")
    return args


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
    state.setdefault("apply_attempts", [])
    state["summary"].setdefault("apply_status", "not-applied")
    state["summary"].setdefault("applied_branch", "")
    state["summary"].setdefault("applied_commit", "")
    state["summary"].setdefault("last_apply_report", "")
    state["summary"].setdefault("last_apply_attempt_id", "")
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


def current_final_plan_path(state: dict[str, Any]) -> Path:
    final_plan_file = state.get("summary", {}).get("final_plan_file")
    if final_plan_file:
        return Path(final_plan_file)
    return final_candidate_path(state)


def package_manifest_path(package: dict[str, Any]) -> Path:
    return Path(package["manifest_path"])


def package_diff_path(package: dict[str, Any]) -> Path:
    return Path(package["diff_path"])


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
    text = sanitize_terminal_text(data.decode("utf-8", errors="replace"))
    if text:
        agent_state["last_activity_at"] = utc_timestamp_precise()
        append_combined_verbose(state, agent, text)
    return text


def create_note(
    state: dict[str, Any],
    *,
    text: str,
    applies_from_turn: int,
    applies_from_phase: str,
) -> dict[str, Any]:
    note_id = next_note_id(state)
    note = {
        "id": note_id,
        "created_at": utc_timestamp_precise(),
        "text": text.strip(),
        "summary": clip_text(text.strip(), limit=120),
        "applies_from_turn": applies_from_turn,
        "applies_from_phase": applies_from_phase,
        "record_file": str(Path(state["run_dir"]) / "notes" / f"{note_id}.json"),
    }
    write_json(Path(note["record_file"]), note)
    state["notes"].append(note)
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "note-added",
            "timestamp": utc_timestamp_precise(),
            "note": note,
        },
    )
    return note


def read_note_text(initial_text: str | None = None) -> str | None:
    if initial_text and initial_text.strip():
        return initial_text.strip()
    print("Enter symmetric note text. End with a line containing only ---", flush=True)
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        stripped = line.rstrip("\n")
        if stripped.strip() == "---":
            break
        lines.append(stripped)
    note_text = "\n".join(lines).strip()
    return note_text or None


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


def status_lines(state: dict[str, Any]) -> list[str]:
    turn = current_turn(state)
    package = current_execution_package(state)
    summary = state.get("summary", {})
    lines = [
        f"Run: {state['run_id']}",
        f"Session: {state['session_name']}",
        f"Phase: {turn['phase']}",
        f"Turn: {turn['id']}",
        f"Status: {state['status']}",
        f"Executor: {state.get('selected_executor', '') or 'n/a'}",
        f"Reviewer: {state.get('selected_reviewer', '') or 'n/a'}",
        f"Plan approved: {summary.get('plan_approved', False)}",
        f"Execution approved: {summary.get('execution_approved', False)}",
        f"Read-only violations: {len(state.get('read_only_violations', []))}",
        f"Notes queued/active: {len(state['notes'])}",
    ]
    if package:
        lines.extend(
            [
                f"Current package: {package.get('package_dir', '')}",
                f"Current package executor: {package.get('executor', '')}",
                f"Current package changed files: {len(package.get('changed_files', []))}",
            ]
        )
    for agent in AGENTS:
        turn_agent = turn["agents"][agent]
        agent_state = state["agents"][agent]
        detail = (
            f"{agent}: status={turn_agent['status']}, "
            f"pane={agent_state['pane_id']}, "
            f"mode={'read-only' if turn_agent['read_only'] else 'write'}, "
            f"last_activity={agent_state.get('last_activity_at', '') or 'n/a'}, "
            f"nudges={turn_agent.get('nudge_count', 0)}"
        )
        if turn_agent.get("parse_error"):
            detail += f", parse_error={clip_text(turn_agent['parse_error'], limit=100)}"
        lines.append(detail)
    return lines


def show_final_plan_lines(state: dict[str, Any]) -> list[str]:
    path = current_final_plan_path(state)
    lines = [
        f"Final plan path: {path}",
        "",
        read_text_preview(path),
    ]
    return lines


def show_package_lines(state: dict[str, Any]) -> list[str]:
    package = current_execution_package(state)
    if not package:
        return ["No current execution package is available yet."]
    lines = [
        f"Package dir: {package.get('package_dir', '')}",
        f"Executor: {package.get('executor', '')}",
        f"Phase: {package.get('phase', '')}",
        f"Turn: {package.get('turn_id', '')}",
        f"Manifest: {package.get('manifest_path', '')}",
        f"Diff: {package.get('diff_path', '')}",
        "Changed files:",
    ]
    changed_files = package.get("changed_files", [])
    if changed_files:
        lines.extend(f"- {path}" for path in changed_files)
    else:
        lines.append("- None")
    return lines


def show_manifest_lines(state: dict[str, Any]) -> list[str]:
    package = current_execution_package(state)
    if not package:
        return ["No current execution package is available yet."]
    path = package_manifest_path(package)
    if not path.exists():
        return [f"Manifest path: {path}", "", "(missing)"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return [
        f"Manifest path: {path}",
        "",
        json.dumps(manifest, indent=2, ensure_ascii=True),
    ]


def show_diff_lines(state: dict[str, Any]) -> list[str]:
    package = current_execution_package(state)
    if not package:
        return ["No current execution package is available yet."]
    path = package_diff_path(package)
    return [
        f"Diff path: {path}",
        "",
        read_text_preview(path),
    ]


def inspect_agent(state: dict[str, Any], agent: str) -> list[str]:
    turn = current_turn(state)
    turn_agent = turn["agents"][agent]
    agent_state = state["agents"][agent]
    pane_capture = capture_pane(agent_state["pane_id"], lines=80)
    lines = [
        f"Agent: {agent}",
        f"Pane: {agent_state['pane_id']}",
        f"Workspace: {agent_state['workspace']}",
        f"Prompt: {turn_agent.get('prompt_path', '') or '(none)'}",
        f"Session prompt: {turn_agent.get('session_prompt_path', '') or '(none)'}",
        f"Raw log: {agent_state['raw_log_path']}",
        f"Turn log: {turn_agent.get('turn_log_path', '') or '(none)'}",
        f"Result file: {turn_agent.get('result_path', '') or '(none)'}",
        f"Status: {turn_agent['status']}",
    ]
    if turn_agent.get("parse_error"):
        lines.append(f"Parse error: {turn_agent['parse_error']}")
    if turn_agent.get("result"):
        lines.append("Parsed result:")
        lines.append(json.dumps(turn_agent["result"], indent=2, ensure_ascii=True))
    lines.extend(
        [
            "",
            "Recent turn log:",
            read_file_tail(Path(turn_agent["turn_log_path"]), lines=40),
            "",
            "Recent pane capture:",
            sanitize_terminal_text(pane_capture).strip() or "(empty)",
        ]
    )
    return lines


def handle_command(
    state: dict[str, Any],
    *,
    mode: str,
    next_phase: str | None,
    raw_command: str,
) -> str | None:
    command = raw_command.strip()
    if not command:
        return None
    lower = command.lower()
    if lower in {"h", "help", "?"}:
        if mode == "boundary":
            supervisor_log_line(
                state,
                "Commands: continue, status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, abort",
            )
        else:
            supervisor_log_line(
                state,
                "Commands while running: status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, wait, abort",
            )
        return None
    if lower == "status":
        for line in status_lines(state):
            supervisor_log_line(state, line)
        return None
    if lower.startswith("tail "):
        _, _, agent_name = lower.partition(" ")
        if agent_name not in AGENTS:
            supervisor_log_line(state, "Usage: tail claude|codex")
            return None
        turn = current_turn(state)
        path = Path(turn["agents"][agent_name]["turn_log_path"])
        supervisor_log_line(state, f"Tail for {agent_name}:")
        print(read_file_tail(path, lines=60), flush=True)
        return None
    if lower.startswith("inspect "):
        _, _, agent_name = lower.partition(" ")
        if agent_name not in AGENTS:
            supervisor_log_line(state, "Usage: inspect claude|codex")
            return None
        for line in inspect_agent(state, agent_name):
            print(line, flush=True)
        return None
    if lower == "show final-plan":
        for line in show_final_plan_lines(state):
            print(line, flush=True)
        return None
    if lower == "show package":
        for line in show_package_lines(state):
            print(line, flush=True)
        return None
    if lower == "show diff":
        for line in show_diff_lines(state):
            print(line, flush=True)
        return None
    if lower == "show manifest":
        for line in show_manifest_lines(state):
            print(line, flush=True)
        return None
    if lower.startswith("note both"):
        if next_phase is None:
            supervisor_log_line(state, "No later phase remains, so no new symmetric note can be queued.")
            return None
        inline_text = command[len("note both") :].strip()
        note_text = read_note_text(inline_text)
        if not note_text:
            supervisor_log_line(state, "Empty note discarded.")
            return None
        note = create_note(
            state,
            text=note_text,
            applies_from_turn=len(state["turns"]) + 1,
            applies_from_phase=next_phase,
        )
        supervisor_log_line(
            state,
            f"Queued {note['id']} for {next_phase}: {note['summary']}",
        )
        return None
    if lower == "wait":
        supervisor_log_line(state, "Continuing to watch the current turn.")
        return None
    if lower == "continue":
        if mode != "boundary":
            supervisor_log_line(state, "The current turn is still running. Use wait or keep watching the panes.")
            return None
        return "continue"
    if lower == "abort":
        return "abort"
    supervisor_log_line(state, f"Unknown command: {command}")
    return None


def prepare_turn(
    state: dict[str, Any],
    *,
    phase: str,
    prompt_texts: dict[str, str],
    active_agents: list[str],
    writable_agents: list[str] | None = None,
) -> dict[str, Any]:
    turn_id = turn_id_for(state, phase)
    turn_dir = turn_dir_for(state, turn_id)
    active_set = set(active_agents)
    writable_set = set(writable_agents or [])
    invalid_writers = sorted(writable_set - active_set)
    if invalid_writers:
        raise ValueError(f"Writable agents must be active in {phase}: {', '.join(invalid_writers)}")
    phase_mode = "read-only"
    if writable_set:
        phase_mode = "write" if writable_set == active_set else "mixed"
    turn = {
        "id": turn_id,
        "index": len(state["turns"]) + 1,
        "phase": phase,
        "phase_family": phase_label(phase),
        "mode": phase_mode,
        "summary": phase_summary_text(phase),
        "created_at": utc_timestamp_precise(),
        "started_at": "",
        "completed_at": "",
        "status": "pending",
        "watchdog_nudges": 0,
        "agents": {},
    }
    for agent in AGENTS:
        raw_path = raw_log_path(state, agent)
        prompt_path = turn_dir / agent / "prompt.txt"
        session_prompt_path = session_prompt_path_for(state, turn_id, agent)
        result_path = turn_dir / agent / "result.json"
        turn_log_path = turn_dir / agent / "turn.log"
        entry_snapshot_status_path = turn_dir / agent / "entry-snapshot.status.txt"
        entry_snapshot_diff_path = turn_dir / agent / "entry-snapshot.diff.txt"
        is_active = agent in active_agents
        is_read_only = is_active and agent not in writable_set
        if is_active:
            write_text(prompt_path, prompt_texts[agent].strip() + "\n")
            write_text(session_prompt_path, prompt_texts[agent].strip() + "\n")
            write_text(turn_log_path, "")
        turn["agents"][agent] = {
            "active": is_active,
            "read_only": is_read_only,
            "status": "pending" if is_active else "skipped",
            "prompt_path": str(prompt_path) if is_active else "",
            "session_prompt_path": str(session_prompt_path) if is_active else "",
            "result_path": str(result_path) if is_active else "",
            "turn_log_path": str(turn_log_path) if is_active else "",
            "entry_snapshot_status_path": str(entry_snapshot_status_path) if is_active and is_read_only else "",
            "entry_snapshot_diff_path": str(entry_snapshot_diff_path) if is_active and is_read_only else "",
            "entry_snapshot_taken_at": "",
            "turn_start_offset": raw_path.stat().st_size if raw_path.exists() else 0,
            "parse_error": "",
            "result": None,
            "completed_at": "",
            "nudge_count": 0,
            "read_only_violation": None,
        }
    state["turns"].append(turn)
    state["current_phase"] = phase
    state["status"] = "running"
    save_state(state)
    return turn


def dispatch_turn(
    state: dict[str, Any],
    turn: dict[str, Any],
    *,
    send_prompts: bool = True,
) -> None:
    # Precondition: dispatch a follow-up turn only after wait_for_turn() has
    # observed the previous turn finish and both interactive CLIs are back at
    # an input-ready prompt. paste_message() injects text into the live TTY; if
    # a caller reorders phases and sends while an agent is still streaming its
    # previous response, the pasted prompt can be lost or interleaved with the
    # active output.
    turn["started_at"] = utc_timestamp_precise()
    turn["status"] = "running"
    for agent in AGENTS:
        turn_agent = turn["agents"][agent]
        if not turn_agent["active"]:
            continue
        turn_agent["status"] = "running"
        if send_prompts:
            turn_agent["turn_start_offset"] = raw_log_path(state, agent).stat().st_size if raw_log_path(state, agent).exists() else 0
        turn_agent["parse_error"] = ""
        turn_agent["result"] = None
        capture_read_only_snapshot(state, turn, agent)
        if send_prompts:
            paste_message(
                state["agents"][agent]["pane_id"],
                prompt_file_message(Path(turn_agent["session_prompt_path"])),
            )
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "turn-dispatched",
            "timestamp": utc_timestamp_precise(),
            "turn_id": turn["id"],
            "phase": turn["phase"],
            "active_agents": [agent for agent in AGENTS if turn["agents"][agent]["active"]],
        },
    )


def wait_for_turn(
    state: dict[str, Any],
    turn: dict[str, Any],
    *,
    next_phase: str | None,
) -> dict[str, dict[str, Any]]:
    buffers = {agent: "" for agent in AGENTS}
    offsets = {agent: int(turn["agents"][agent]["turn_start_offset"]) for agent in AGENTS}
    last_output_time = time.time()
    supervisor_log_line(state, f"Watching {turn['id']} ({turn['summary']}).")
    supervisor_log_line(
        state,
        "Live commands: status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, wait, abort",
    )
    while True:
        for agent in AGENTS:
            turn_agent = turn["agents"][agent]
            if not turn_agent["active"] or turn_agent["status"] == "completed":
                continue
            streamed = stream_new_agent_output(state, agent)
            if streamed:
                last_output_time = time.time()
            data, offsets[agent] = read_bytes_from(raw_log_path(state, agent), offsets[agent])
            if data:
                text = sanitize_terminal_text(data.decode("utf-8", errors="replace"))
                buffers[agent] += text
                append_text(Path(turn_agent["turn_log_path"]), text)
                if text:
                    last_output_time = time.time()
            try:
                envelope = parse_turn_result(
                    buffers[agent],
                    expected_turn_id=turn["id"],
                    expected_phase=turn["phase"],
                    expected_agent=agent,
                )
            except ValueError as exc:
                if "__PEER_FORGE_DONE__" in buffers[agent]:
                    turn_agent["parse_error"] = str(exc)
                    save_state(state)
                continue
            validate_read_only_snapshot(state, turn, agent)
            turn_agent["status"] = "completed"
            turn_agent["completed_at"] = utc_timestamp_precise()
            turn_agent["parse_error"] = ""
            turn_agent["result"] = envelope["result"]
            write_json(Path(turn_agent["result_path"]), envelope)
            save_state(state)
            supervisor_log_line(
                state,
                f"{turn['id']}: {agent} completed -> {summarize_agent_result(turn['phase'], envelope['result'])}",
            )
        if all(turn["agents"][agent]["status"] == "completed" for agent in AGENTS if turn["agents"][agent]["active"]):
            turn["status"] = "completed"
            turn["completed_at"] = utc_timestamp_precise()
            save_state(state)
            return {
                agent: turn["agents"][agent]["result"]
                for agent in AGENTS
                if turn["agents"][agent]["active"]
            }
        if (
            state["watchdog_seconds"] > 0
            and time.time() - last_output_time >= state["watchdog_seconds"]
            and turn["watchdog_nudges"] < state["max_watchdog_nudges"]
        ):
            nudge_text = build_watchdog_nudge(turn["id"], turn["phase"])
            for agent in AGENTS:
                if turn["agents"][agent]["active"] and turn["agents"][agent]["status"] != "completed":
                    paste_message(state["agents"][agent]["pane_id"], nudge_text)
                    turn["agents"][agent]["nudge_count"] += 1
            turn["watchdog_nudges"] += 1
            last_output_time = time.time()
            save_state(state)
            supervisor_log_line(
                state,
                f"Watchdog nudge sent symmetrically to active agents for {turn['id']}.",
            )
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        if ready:
            raw_command = sys.stdin.readline()
            if raw_command == "":
                continue
            action = handle_command(state, mode="running", next_phase=next_phase, raw_command=raw_command)
            if action == "abort":
                raise KeyboardInterrupt("Supervisor aborted the live run.")


def pause_for_boundary(state: dict[str, Any], *, label: str, next_phase: str | None) -> None:
    supervisor_log_line(state, label)
    if next_phase is None:
        supervisor_log_line(state, "No later phase remains.")
    else:
        supervisor_log_line(state, f"Next phase: {next_phase}")
    supervisor_log_line(
        state,
        "Boundary commands: continue, status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, abort",
    )
    while True:
        raw_command = input("> ")
        action = handle_command(state, mode="boundary", next_phase=next_phase, raw_command=raw_command)
        if action == "continue":
            return
        if action == "abort":
            raise KeyboardInterrupt("Supervisor aborted the live run.")


def build_claude_command(
    *,
    model: str | None,
    bare: bool,
    prompt_path: Path,
) -> list[str]:
    cmd = [
        "claude",
        "--permission-mode",
        # Minimize tool-level prompts during the live run. Claude may still
        # show its own startup bypass warning, which we intentionally leave
        # for the human supervisor to confirm manually.
        "bypassPermissions",
        "--name",
        "peer-forge-live-claude",
    ]
    if bare:
        cmd.append("--bare")
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt_file_message(prompt_path))
    return cmd


def build_codex_command(
    *,
    workspace: Path,
    model: str | None,
    prompt_path: Path,
) -> list[str]:
    cmd = [
        "codex",
        "-C",
        str(workspace),
        "-s",
        # Keep the long-lived session write-capable; protocol-level read-only
        # checks enforce no-write phases.
        "workspace-write",
        "-a",
        "never",
        "--no-alt-screen",
    ]
    if model:
        cmd.extend(["-m", model])
    cmd.append(prompt_file_message(prompt_path))
    return cmd


def build_supervisor_command(state_file: Path) -> list[str]:
    return ["python3", str(Path(__file__).resolve()), "serve", "--state-file", str(state_file)]


def placeholder_command() -> list[str]:
    return ["sleep", "3600"]


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


def final_candidate_path(state: dict[str, Any]) -> Path:
    return Path(state["run_dir"]) / "final-plan.json"


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


def ensure_turn_results(
    state: dict[str, Any],
    *,
    phase: str,
    next_phase: str | None,
    build_turn: Callable[[], dict[str, Any]],
    send_prompts: bool = True,
) -> dict[str, dict[str, Any]]:
    turn = find_turn(state, phase)
    if turn is None:
        turn = build_turn()
        dispatch_turn(state, turn, send_prompts=send_prompts)
        return wait_for_turn(state, turn, next_phase=next_phase)
    if turn["status"] == "completed":
        return turn_results(turn)
    if turn["status"] == "pending":
        dispatch_turn(state, turn, send_prompts=send_prompts)
        return wait_for_turn(state, turn, next_phase=next_phase)
    if turn["status"] == "running":
        supervisor_log_line(state, f"Resuming active turn {turn['id']} ({turn['summary']}).")
        return wait_for_turn(state, turn, next_phase=next_phase)
    raise RuntimeError(f"Turn {turn['id']} is in unsupported state {turn['status']!r}.")


def maybe_pause_boundary(state: dict[str, Any], *, phase: str, label: str, next_phase: str | None) -> None:
    if boundary_pending(state, phase):
        pause_for_boundary(state, label=label, next_phase=next_phase)


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
        "claude_bare": not args.no_claude_bare,
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
                "raw_log_path": str(run_dir / "panes" / "claude.raw.log"),
                "stream_offset": 0,
                "last_activity_at": "",
            },
            "codex": {
                "workspace": "",
                "pane_id": "",
                "raw_log_path": str(run_dir / "panes" / "codex.raw.log"),
                "stream_offset": 0,
                "last_activity_at": "",
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


def create_initial_turn(state: dict[str, Any]) -> dict[str, Any]:
    prompt_texts = {
        "claude": build_plan_initial_prompt(
            turn_id=turn_id_for(state, "plan-initial"),
            phase="plan-initial",
            agent="claude",
            agent_name=DISPLAY_NAMES["claude"],
            task=state["task"],
            acceptance=state["acceptance"],
            scope=state["scope"],
            supervisor_notes=active_notes_for_turn(state, len(state["turns"]) + 1),
        ),
        "codex": build_plan_initial_prompt(
            turn_id=turn_id_for(state, "plan-initial"),
            phase="plan-initial",
            agent="codex",
            agent_name=DISPLAY_NAMES["codex"],
            task=state["task"],
            acceptance=state["acceptance"],
            scope=state["scope"],
            supervisor_notes=active_notes_for_turn(state, len(state["turns"]) + 1),
        ),
    }
    return prepare_turn(state, phase="plan-initial", prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def build_plan_review_turn(state: dict[str, Any], plan_initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        "claude": build_plan_review_prompt(
            turn_id=turn_id_for(state, "plan-review"),
            phase="plan-review",
            reviewer="claude",
            reviewer_name=DISPLAY_NAMES["claude"],
            peer_name=DISPLAY_NAMES["codex"],
            peer_plan=plan_initial["codex"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
        "codex": build_plan_review_prompt(
            turn_id=turn_id_for(state, "plan-review"),
            phase="plan-review",
            reviewer="codex",
            reviewer_name=DISPLAY_NAMES["codex"],
            peer_name=DISPLAY_NAMES["claude"],
            peer_plan=plan_initial["claude"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
    }
    return prepare_turn(state, phase="plan-review", prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def build_plan_revise_turn(state: dict[str, Any], plan_reviews: dict[str, dict[str, Any]]) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        "claude": build_plan_revise_prompt(
            turn_id=turn_id_for(state, "plan-revise"),
            phase="plan-revise",
            agent="claude",
            agent_name=DISPLAY_NAMES["claude"],
            peer_name=DISPLAY_NAMES["codex"],
            peer_review=plan_reviews["codex"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
        "codex": build_plan_revise_prompt(
            turn_id=turn_id_for(state, "plan-revise"),
            phase="plan-revise",
            agent="codex",
            agent_name=DISPLAY_NAMES["codex"],
            peer_name=DISPLAY_NAMES["claude"],
            peer_review=plan_reviews["claude"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
    }
    return prepare_turn(state, phase="plan-revise", prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def build_plan_consensus_turn(state: dict[str, Any], plan_revisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        "claude": build_plan_consensus_prompt(
            turn_id=turn_id_for(state, "plan-consensus"),
            phase="plan-consensus",
            agent="claude",
            agent_name=DISPLAY_NAMES["claude"],
            peer_name=DISPLAY_NAMES["codex"],
            own_revision=plan_revisions["claude"],
            peer_revision=plan_revisions["codex"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
        "codex": build_plan_consensus_prompt(
            turn_id=turn_id_for(state, "plan-consensus"),
            phase="plan-consensus",
            agent="codex",
            agent_name=DISPLAY_NAMES["codex"],
            peer_name=DISPLAY_NAMES["claude"],
            own_revision=plan_revisions["codex"],
            peer_revision=plan_revisions["claude"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
    }
    return prepare_turn(state, phase="plan-consensus", prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def build_plan_finalize_turn(
    state: dict[str, Any],
    *,
    final_plan_base: str,
    merge_brief: dict[str, Any],
    plan_revisions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    if final_plan_base == "claude":
        own_revision = plan_revisions["claude"]
        peer_revision = plan_revisions["codex"]
        base_name = DISPLAY_NAMES["claude"]
        peer_name = DISPLAY_NAMES["codex"]
    else:
        own_revision = plan_revisions["codex"]
        peer_revision = plan_revisions["claude"]
        base_name = DISPLAY_NAMES["codex"]
        peer_name = DISPLAY_NAMES["claude"]
    prompt_texts = {
        final_plan_base: build_plan_finalize_prompt(
            turn_id=turn_id_for(state, "plan-finalize"),
            phase="plan-finalize",
            agent=final_plan_base,
            base_agent_name=base_name,
            peer_name=peer_name,
            merge_brief=merge_brief,
            own_revision=own_revision,
            peer_revision=peer_revision,
            supervisor_notes=active_notes_for_turn(state, turn_index),
        )
    }
    return prepare_turn(state, phase="plan-finalize", prompt_texts=prompt_texts, active_agents=[final_plan_base])


def build_plan_signoff_turn(state: dict[str, Any], *, round_index: int, final_candidate: dict[str, Any]) -> dict[str, Any]:
    phase = "plan-signoff" if round_index == 0 else f"plan-signoff-round-{round_index}"
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        "claude": build_plan_signoff_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent="claude",
            agent_name=DISPLAY_NAMES["claude"],
            final_candidate=final_candidate,
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
        "codex": build_plan_signoff_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent="codex",
            agent_name=DISPLAY_NAMES["codex"],
            final_candidate=final_candidate,
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
    }
    return prepare_turn(state, phase=phase, prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def build_final_fix_turn(
    state: dict[str, Any],
    *,
    round_index: int,
    final_plan_base: str,
    current_candidate: dict[str, Any],
    signoffs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    phase = f"plan-final-fix-round-{round_index}"
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        final_plan_base: build_final_fix_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent=final_plan_base,
            base_agent_name=DISPLAY_NAMES[final_plan_base],
            current_candidate=current_candidate,
            objections=summarize_signoff_objections(signoffs),
            supervisor_notes=active_notes_for_turn(state, turn_index),
        )
    }
    return prepare_turn(state, phase=phase, prompt_texts=prompt_texts, active_agents=[final_plan_base])


def build_execute_turn(state: dict[str, Any], *, executor: str, final_plan: dict[str, Any]) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        executor: build_execution_prompt(
            turn_id=turn_id_for(state, "execute-initial"),
            phase="execute-initial",
            agent=executor,
            agent_name=DISPLAY_NAMES[executor],
            task=state["task"],
            acceptance=state["acceptance"],
            scope=state["scope"],
            final_plan=final_plan,
            supervisor_notes=active_notes_for_turn(state, turn_index),
        )
    }
    return prepare_turn(
        state,
        phase="execute-initial",
        prompt_texts=prompt_texts,
        active_agents=[executor],
        writable_agents=[executor],
    )


def build_execution_review_turn(
    state: dict[str, Any],
    *,
    executor: str,
    reviewer: str,
    final_plan: dict[str, Any],
    execution_summary: dict[str, Any],
    execution_package: dict[str, Any],
) -> dict[str, Any]:
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        reviewer: build_execution_review_prompt(
            turn_id=turn_id_for(state, "execution-review"),
            phase="execution-review",
            agent=reviewer,
            reviewer_name=DISPLAY_NAMES[reviewer],
            executor_name=DISPLAY_NAMES[executor],
            final_plan=final_plan,
            execution_summary=execution_summary,
            execution_package_dir=execution_package["package_dir"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        )
    }
    return prepare_turn(state, phase="execution-review", prompt_texts=prompt_texts, active_agents=[reviewer])


def build_execution_fix_turn(
    state: dict[str, Any],
    *,
    round_index: int,
    executor: str,
    final_plan: dict[str, Any],
    review_feedback: dict[str, Any],
) -> dict[str, Any]:
    phase = f"execution-fix-round-{round_index}"
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        executor: build_execution_fix_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent=executor,
            agent_name=DISPLAY_NAMES[executor],
            task=state["task"],
            acceptance=state["acceptance"],
            scope=state["scope"],
            final_plan=final_plan,
            review_feedback=review_feedback,
            supervisor_notes=active_notes_for_turn(state, turn_index),
        )
    }
    return prepare_turn(
        state,
        phase=phase,
        prompt_texts=prompt_texts,
        active_agents=[executor],
        writable_agents=[executor],
    )


def build_execution_signoff_turn(
    state: dict[str, Any],
    *,
    round_index: int,
    final_plan: dict[str, Any],
    execution_summary: dict[str, Any],
    execution_package: dict[str, Any],
) -> dict[str, Any]:
    phase = "execution-signoff" if round_index == 0 else f"execution-signoff-round-{round_index}"
    turn_index = len(state["turns"]) + 1
    prompt_texts = {
        "claude": build_execution_signoff_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent="claude",
            agent_name=DISPLAY_NAMES["claude"],
            final_plan=final_plan,
            execution_summary=execution_summary,
            execution_package_dir=execution_package["package_dir"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
        "codex": build_execution_signoff_prompt(
            turn_id=turn_id_for(state, phase),
            phase=phase,
            agent="codex",
            agent_name=DISPLAY_NAMES["codex"],
            final_plan=final_plan,
            execution_summary=execution_summary,
            execution_package_dir=execution_package["package_dir"],
            supervisor_notes=active_notes_for_turn(state, turn_index),
        ),
    }
    return prepare_turn(state, phase=phase, prompt_texts=prompt_texts, active_agents=["claude", "codex"])


def serve_live(state: dict[str, Any]) -> None:
    plan_initial = ensure_turn_results(
        state,
        phase="plan-initial",
        next_phase="plan-review",
        build_turn=lambda: create_initial_turn(state),
        send_prompts=False,
    )
    maybe_pause_boundary(state, phase="plan-initial", label="Initial plans complete.", next_phase="plan-review")

    plan_reviews = ensure_turn_results(
        state,
        phase="plan-review",
        next_phase="plan-revise",
        build_turn=lambda: build_plan_review_turn(state, plan_initial),
    )
    maybe_pause_boundary(state, phase="plan-review", label="Cross-review complete.", next_phase="plan-revise")

    plan_revisions = ensure_turn_results(
        state,
        phase="plan-revise",
        next_phase="plan-consensus",
        build_turn=lambda: build_plan_revise_turn(state, plan_reviews),
    )
    maybe_pause_boundary(state, phase="plan-revise", label="Revision complete.", next_phase="plan-consensus")

    plan_consensus = ensure_turn_results(
        state,
        phase="plan-consensus",
        next_phase="plan-finalize",
        build_turn=lambda: build_plan_consensus_turn(state, plan_revisions),
    )
    final_plan_base, merge_brief = ensure_plan_merge_brief(state, plan_consensus)
    maybe_pause_boundary(
        state,
        phase="plan-consensus",
        label=f"Consensus complete. Base side: {final_plan_base}.",
        next_phase="plan-finalize",
    )

    finalize_result = ensure_turn_results(
        state,
        phase="plan-finalize",
        next_phase="plan-signoff",
        build_turn=lambda: build_plan_finalize_turn(
            state,
            final_plan_base=final_plan_base,
            merge_brief=merge_brief,
            plan_revisions=plan_revisions,
        ),
    )
    current_final = state.get("final_plan") or finalize_result[final_plan_base]
    persist_final_candidate(state, current_final)
    maybe_pause_boundary(state, phase="plan-finalize", label="Final plan candidate drafted.", next_phase="plan-signoff")

    signoff_round_index = 0
    final_approved = False
    latest_signoffs: dict[str, dict[str, Any]] = {}
    last_plan_signoff_phase = "plan-signoff"
    while True:
        signoff_phase = "plan-signoff" if signoff_round_index == 0 else f"plan-signoff-round-{signoff_round_index}"
        last_plan_signoff_phase = signoff_phase
        next_phase = None
        if signoff_round_index < state["signoff_rounds"]:
            next_phase = f"plan-final-fix-round-{signoff_round_index + 1}"
        signoffs = ensure_turn_results(
            state,
            phase=signoff_phase,
            next_phase=next_phase,
            build_turn=lambda signoff_round_index=signoff_round_index, current_final=current_final: build_plan_signoff_turn(
                state,
                round_index=signoff_round_index,
                final_candidate=current_final,
            ),
        )
        latest_signoffs = signoffs
        if all(result["overall_verdict"] == "approve" for result in signoffs.values()):
            final_approved = True
            break
        if signoff_round_index >= state["signoff_rounds"]:
            break
        fix_round = signoff_round_index + 1
        maybe_pause_boundary(
            state,
            phase=signoff_phase,
            label=f"Signoff round {fix_round} found objections.",
            next_phase=f"plan-final-fix-round-{fix_round}",
        )
        fix_phase = f"plan-final-fix-round-{fix_round}"
        fixed = ensure_turn_results(
            state,
            phase=fix_phase,
            next_phase=f"plan-signoff-round-{fix_round}",
            build_turn=lambda fix_round=fix_round, current_final=current_final, signoffs=signoffs: build_final_fix_turn(
                state,
                round_index=fix_round,
                final_plan_base=final_plan_base,
                current_candidate=current_final,
                signoffs=signoffs,
            ),
        )
        current_final = fixed[final_plan_base]
        persist_final_candidate(state, current_final)
        maybe_pause_boundary(
            state,
            phase=fix_phase,
            label=f"Final-fix round {fix_round} complete.",
            next_phase=f"plan-signoff-round-{fix_round}",
        )
        signoff_round_index = fix_round

    state["status"] = "approved" if final_approved else "needs-attention"
    state["summary"]["plan_approved"] = final_approved
    state["summary"]["final_approved"] = final_approved
    state["summary"]["plan_signoffs"] = latest_signoffs
    state["summary"]["final_signoffs"] = latest_signoffs
    state["summary"]["final_candidate"] = current_final
    state["summary"]["execution_approved"] = False
    save_state(state)
    if not final_approved:
        persist_report(state)
        supervisor_log_line(
            state,
            f"Live run finished at plan stage. plan_approved={final_approved}. Report: {report_path(state)}",
        )
        return

    executor = final_plan_base
    reviewer = peer_agent(executor)
    state["selected_executor"] = executor
    state["selected_reviewer"] = reviewer
    save_state(state)
    maybe_pause_boundary(
        state,
        phase=last_plan_signoff_phase,
        label=f"Plan approved. Executor: {executor}. Reviewer: {reviewer}.",
        next_phase="execute-initial",
    )

    execute_result = ensure_turn_results(
        state,
        phase="execute-initial",
        next_phase="execution-review",
        build_turn=lambda: build_execute_turn(state, executor=executor, final_plan=current_final),
    )
    current_execution = execute_result[executor]
    execute_turn = find_turn(state, "execute-initial")
    if execute_turn is None:
        raise RuntimeError("Missing execute-initial turn after execution.")
    current_execution_package = ensure_execution_package(
        state,
        turn=execute_turn,
        executor=executor,
        execution_summary=current_execution,
    )
    state["summary"]["execution_review"] = state["summary"].get("execution_review", {})
    state["summary"]["execution_signoffs"] = state["summary"].get("execution_signoffs", {})
    state["summary"]["current_execution"] = current_execution
    save_state(state)
    maybe_pause_boundary(state, phase="execute-initial", label="Initial execution complete.", next_phase="execution-review")

    review_next_phase = "execution-signoff"
    if state["signoff_rounds"] > 0:
        review_next_phase = "execution-fix-round-1"
    execution_review_result = ensure_turn_results(
        state,
        phase="execution-review",
        next_phase=review_next_phase,
        build_turn=lambda: build_execution_review_turn(
            state,
            executor=executor,
            reviewer=reviewer,
            final_plan=current_final,
            execution_summary=current_execution,
            execution_package=current_execution_package,
        ),
    )[reviewer]
    state["summary"]["execution_review"] = execution_review_result
    state["summary"]["current_execution"] = current_execution
    save_state(state)

    current_execution_signoffs: dict[str, dict[str, Any]] = {}
    execution_approved = False
    pending_fix_feedback: dict[str, Any] | None = None
    execution_fix_round = 0

    if execution_review_result["overall_verdict"] == "approve":
        maybe_pause_boundary(
            state,
            phase="execution-review",
            label="Implementation review approved. Proceeding to implementation signoff.",
            next_phase="execution-signoff",
        )
    else:
        if state["signoff_rounds"] == 0:
            state["status"] = "needs-attention"
            state["summary"]["execution_approved"] = False
            state["summary"]["final_approved"] = False
            save_state(state)
            persist_report(state)
            supervisor_log_line(
                state,
                "Implementation review requested changes but no execution fix rounds are available.",
            )
            return
        execution_fix_round = 1
        pending_fix_feedback = execution_review_result
        maybe_pause_boundary(
            state,
            phase="execution-review",
            label="Implementation review requested changes.",
            next_phase="execution-fix-round-1",
        )

    while True:
        if execution_fix_round > 0:
            fix_phase = f"execution-fix-round-{execution_fix_round}"
            fix_result = ensure_turn_results(
                state,
                phase=fix_phase,
                next_phase=f"execution-signoff-round-{execution_fix_round}",
                build_turn=lambda execution_fix_round=execution_fix_round, pending_fix_feedback=pending_fix_feedback: build_execution_fix_turn(
                    state,
                    round_index=execution_fix_round,
                    executor=executor,
                    final_plan=current_final,
                    review_feedback=pending_fix_feedback or {},
                ),
            )
            current_execution = fix_result[executor]
            fix_turn = find_turn(state, fix_phase)
            if fix_turn is None:
                raise RuntimeError(f"Missing {fix_phase} turn after execution fix.")
            current_execution_package = ensure_execution_package(
                state,
                turn=fix_turn,
                executor=executor,
                execution_summary=current_execution,
            )
            state["summary"]["current_execution"] = current_execution
            save_state(state)
            maybe_pause_boundary(
                state,
                phase=fix_phase,
                label=f"Execution fix round {execution_fix_round} complete.",
                next_phase=f"execution-signoff-round-{execution_fix_round}",
            )

        signoff_phase = "execution-signoff" if execution_fix_round == 0 else f"execution-signoff-round-{execution_fix_round}"
        next_phase = None
        if execution_fix_round < state["signoff_rounds"]:
            next_phase = f"execution-fix-round-{execution_fix_round + 1}"
        current_execution_signoffs = ensure_turn_results(
            state,
            phase=signoff_phase,
            next_phase=next_phase,
            build_turn=lambda execution_fix_round=execution_fix_round, current_execution=current_execution, current_execution_package=current_execution_package: build_execution_signoff_turn(
                state,
                round_index=execution_fix_round,
                final_plan=current_final,
                execution_summary=current_execution,
                execution_package=current_execution_package,
            ),
        )
        if all(result["overall_verdict"] == "approve" for result in current_execution_signoffs.values()):
            execution_approved = True
            break
        if execution_fix_round >= state["signoff_rounds"]:
            break
        pending_fix_feedback = summarize_signoff_objections(current_execution_signoffs)
        next_fix_round = execution_fix_round + 1
        maybe_pause_boundary(
            state,
            phase=signoff_phase,
            label=f"Implementation signoff round {execution_fix_round + 1} found objections.",
            next_phase=f"execution-fix-round-{next_fix_round}",
        )
        execution_fix_round = next_fix_round

    state["status"] = "approved" if execution_approved else "needs-attention"
    state["summary"]["execution_approved"] = execution_approved
    state["summary"]["execution_signoffs"] = current_execution_signoffs
    state["summary"]["current_execution"] = current_execution
    state["summary"]["final_approved"] = execution_approved
    save_state(state)
    persist_report(state)
    supervisor_log_line(
        state,
        (
            "Live run finished. "
            f"plan_approved={final_approved}, execution_approved={execution_approved}. "
            f"Report: {report_path(state)}"
        ),
    )


def pane_by_title(panes: list[dict[str, str]], title: str) -> dict[str, str] | None:
    for pane in panes:
        if pane.get("pane_title") == title:
            return pane
    return None


def resume_mode(args: argparse.Namespace) -> int:
    ensure_tmux()
    ensure_cli("python3")
    state_file = Path(args.state_file).resolve()
    if not state_file.exists():
        raise SystemExit(f"State file does not exist: {state_file}")
    state = load_state(state_file)
    session_name = state["session_name"]
    if not has_session(session_name):
        raise SystemExit(f"tmux session not found: {session_name}")

    panes = list_panes(session_name)
    claude_pane = pane_by_title(panes, "peer-forge-live:claude")
    codex_pane = pane_by_title(panes, "peer-forge-live:codex")
    supervisor_pane = pane_by_title(panes, "peer-forge-live:supervisor")
    for agent, pane in (("claude", claude_pane), ("codex", codex_pane)):
        if pane is None:
            raise SystemExit(f"{agent} pane is missing in tmux session {session_name}; live resume cannot repair agent panes.")
        if pane.get("pane_dead") == "1":
            raise SystemExit(
                f"{agent} pane {pane['pane_id']} is dead in tmux session {session_name}; live resume cannot repair agent panes."
            )
        state["agents"][agent]["pane_id"] = pane["pane_id"]

    supervisor_action = "supervisor-resumed"
    supervisor_pane_id = ""
    run_dir = Path(state["run_dir"])
    if supervisor_pane is None:
        supervisor_pane_id = split_window(
            claude_pane["pane_id"],
            cwd=run_dir,
            direction="vertical",
            command=placeholder_command(),
        )
        set_pane_title(supervisor_pane_id, "peer-forge-live:supervisor")
        pipe_pane(supervisor_pane_id, Path(state["logs"]["supervisor_raw"]))
        respawn_pane(
            supervisor_pane_id,
            cwd=run_dir,
            command=build_supervisor_command(state_file),
        )
        supervisor_action = "supervisor-created"
    else:
        supervisor_pane_id = supervisor_pane["pane_id"]
        set_pane_title(supervisor_pane_id, "peer-forge-live:supervisor")
        if supervisor_pane.get("pane_dead") == "1":
            pipe_pane(supervisor_pane_id, Path(state["logs"]["supervisor_raw"]))
            respawn_pane(
                supervisor_pane_id,
                cwd=run_dir,
                command=build_supervisor_command(state_file),
            )
            supervisor_action = "supervisor-respawned"

    select_layout(session_name, "tiled")
    state["agents"]["supervisor"]["pane_id"] = supervisor_pane_id
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": supervisor_action,
            "timestamp": utc_timestamp_precise(),
            "session_name": session_name,
            "state_file": str(state_file),
            "supervisor_pane_id": supervisor_pane_id,
        },
    )

    if args.no_attach:
        print(
            json.dumps(
                {
                    "run_id": state["run_id"],
                    "session_name": session_name,
                    "state_file": str(state_file),
                    "attach": f"tmux attach-session -t {session_name}",
                    "supervisor_action": supervisor_action,
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return 0
    attach_session(session_name)
    return 0


def apply_mode(args: argparse.Namespace) -> int:
    ensure_cli("git")
    state_file = Path(args.state_file).resolve()
    if not state_file.exists():
        raise SystemExit(f"State file does not exist: {state_file}")
    state = load_state(state_file)
    repo = Path(state["repo"]).resolve()
    summary = state.get("summary", {})
    package = current_execution_package(state)
    if package is None:
        packages = state.get("execution_packages", [])
        if packages:
            package = packages[-1]
    attempt_id = utc_now()
    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []
    commit_sha = ""
    original_branch = ""
    current_head = ""
    expected_head = str(state.get("workspaces", {}).get("initial_commit") or "")
    target_branch_arg = (args.branch or "").strip()
    branch_mode = "new"
    target_branch = target_branch_arg or default_apply_branch(state)
    report: dict[str, Any] = {}
    manifest: dict[str, Any] = {"changed_files": [], "copied_files": [], "deleted_files": []}
    package_paths: list[str] = []
    dirty_paths: list[str] = []
    drift_paths: list[str] = []
    dirty_overlap: list[str] = []
    drift_overlap: list[str] = []
    safe_paths: list[str] = []
    blocked_paths: list[str] = []
    requires_allow_dirty_target = False
    requires_allow_base_drift = False
    decision = "blocked"

    try:
        if not repo.exists():
            blockers.append(f"Target repository does not exist: {repo}")
        if state.get("status") in {"starting", "running"}:
            blockers.append(f"Live run is still active: status={state.get('status')}")
        if not summary.get("plan_approved", False):
            blockers.append("Live run has not reached an approved final plan.")
        if not summary.get("execution_approved", False):
            blockers.append("Live run does not have an approved execution result.")
        if not state.get("workspaces", {}).get("git_mode", False):
            blockers.append("Live apply currently supports only git-backed live runs.")
        if package is None:
            blockers.append("No execution package is available in state.json.")

        if package is not None:
            manifest = load_execution_manifest(package)
            package_paths = list(manifest["changed_files"])
            actions.append(f"materialize {len(manifest['copied_files'])} copied files from package")
            if manifest["deleted_files"]:
                actions.append(f"delete {len(manifest['deleted_files'])} files from package manifest")
            if not manifest["changed_files"]:
                warnings.append("Execution package is empty; apply may become a no-op.")

        if repo.exists():
            top = git(repo, "rev-parse", "--show-toplevel", check=False)
            if top.returncode != 0:
                blockers.append(f"Target path is not a git repository: {repo}")
            else:
                resolved_top = Path(top.stdout.strip()).resolve()
                if resolved_top != repo:
                    blockers.append(f"State repo path does not match git toplevel: state={repo} git={resolved_top}")
                current_head = git(repo, "rev-parse", "HEAD").stdout.strip()
                original_branch = current_branch_name(repo)
                dirty_paths = git_dirty_paths(repo)
                if expected_head and current_head != expected_head:
                    drift_paths = git_changed_paths_between(repo, expected_head, current_head)

        if package_paths:
            dirty_overlap = overlap_paths(package_paths, dirty_paths)
            drift_overlap = overlap_paths(package_paths, drift_paths)
            blocked_paths = unique_lines(dirty_overlap + drift_overlap)
            blocked_set = set(blocked_paths)
            safe_paths = [path for path in package_paths if path not in blocked_set]
        else:
            safe_paths = []

        requires_allow_dirty_target = bool(dirty_paths) and not dirty_overlap
        requires_allow_base_drift = bool(drift_overlap)

        if dirty_overlap:
            blockers.append(
                "Target repository has dirty paths that overlap the execution package: "
                + ", ".join(dirty_overlap)
            )
        elif dirty_paths:
            if args.allow_dirty_target:
                warnings.append(
                    "Target repository has unrelated dirty paths; proceeding because --allow-dirty-target was set."
                )
            else:
                blockers.append(
                    "Target repository has unrelated dirty paths. Re-run with --allow-dirty-target to proceed: "
                    + ", ".join(dirty_paths)
                )

        if drift_overlap:
            if args.allow_base_drift:
                warnings.append(
                    "Target HEAD drift overlaps execution-package paths; proceeding because --allow-base-drift was set."
                )
            else:
                blockers.append(
                    "Target HEAD drift overlaps the execution package. Re-run with --allow-base-drift to override: "
                    + ", ".join(drift_overlap)
                )
        elif drift_paths:
            warnings.append(
                "Target HEAD drift was detected, but it does not overlap the execution package paths."
            )

        if target_branch == "current":
            branch_mode = "current"
            if not original_branch:
                blockers.append("Applying to 'current' requires the repository to be on a named branch.")
            target_branch = original_branch or "current"
            actions.append(f"apply on current branch {target_branch}")
        else:
            actions.append(f"create branch {target_branch}")
            if repo.exists() and branch_exists(repo, target_branch):
                blockers.append(f"Target branch already exists: {target_branch}")

        if args.commit:
            actions.append("create a git commit after apply")

        if blockers:
            if dirty_overlap and drift_overlap:
                decision = "preview-blocked-mixed-overlap"
            elif dirty_overlap:
                decision = "preview-blocked-dirty-overlap"
            elif drift_overlap:
                decision = "preview-blocked-drift-overlap"
            elif dirty_paths and not args.allow_dirty_target:
                decision = "preview-needs-allow-dirty-target"
            else:
                decision = "preview-blocked"
        elif not args.apply:
            decision = "preview-safe"
        else:
            decision = "apply-safe"

        report = {
            "attempt_id": attempt_id,
            "created_at": utc_timestamp_precise(),
            "run_id": state["run_id"],
            "state_file": str(state_file),
            "repo": str(repo),
            "status": "blocked" if blockers else ("preview" if not args.apply else "ready"),
            "decision": decision,
            "apply_requested": bool(args.apply),
            "commit_requested": bool(args.commit),
            "allow_dirty_target": bool(args.allow_dirty_target),
            "allow_base_drift": bool(args.allow_base_drift),
            "target_branch": target_branch,
            "branch_mode": branch_mode,
            "original_branch": original_branch,
            "current_head": current_head,
            "expected_base_commit": expected_head,
            "package_dir": str(package.get("package_dir", "")) if package else "",
            "manifest_path": str(package_manifest_path(package)) if package else "",
            "diff_path": str(package_diff_path(package)) if package else "",
            "changed_files": list(manifest.get("changed_files", [])),
            "copied_files": list(manifest.get("copied_files", [])),
            "deleted_files": list(manifest.get("deleted_files", [])),
            "package_paths": list(package_paths),
            "dirty_paths": list(dirty_paths),
            "drift_paths": list(drift_paths),
            "dirty_overlap": list(dirty_overlap),
            "drift_overlap": list(drift_overlap),
            "safe_paths": list(safe_paths),
            "blocked_paths": list(blocked_paths),
            "requires_allow_dirty_target": requires_allow_dirty_target,
            "requires_allow_base_drift": requires_allow_base_drift,
            "blockers": blockers,
            "warnings": warnings,
            "actions": actions,
            "commit_sha": "",
        }
        if blockers or not args.apply:
            persist_apply_attempt(state, report)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return 1 if blockers else 0

        branch_created = False
        if branch_mode == "new":
            git(repo, "switch", "-c", target_branch)
            branch_created = True

        materialize_execution_package(repo, package, manifest)
        path_changes = git_status_porcelain(repo, manifest["changed_files"])
        if not path_changes:
            report["status"] = "noop"
            report["decision"] = "apply-noop"
            warnings.append("Applying the execution package produced no repository changes.")
        elif args.commit:
            git(repo, "add", "-A", "--", *manifest["changed_files"])
            commit_message = f"Apply peer-forge-live run {state['run_id']}"
            commit_body = (
                f"Task: {state['task']}\n"
                f"State: {state_file}\n"
                f"Package: {package['package_dir']}\n"
                f"Report: {report_path(state)}\n"
            )
            git(repo, "commit", "-m", commit_message, "-m", commit_body)
            commit_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
            report["status"] = "committed"
            report["decision"] = "committed"
        else:
            report["status"] = "applied"
            report["decision"] = "applied"

        report["branch_created"] = branch_created
        report["commit_sha"] = commit_sha
        persist_apply_attempt(state, report)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0
    except Exception as exc:
        if not report:
            report = {
                "attempt_id": attempt_id,
                "created_at": utc_timestamp_precise(),
                "run_id": state.get("run_id", ""),
                "state_file": str(state_file),
                "repo": str(repo),
                "status": "failed",
                "decision": "failed",
                "apply_requested": bool(args.apply),
                "commit_requested": bool(args.commit),
                "allow_dirty_target": bool(args.allow_dirty_target),
                "allow_base_drift": bool(args.allow_base_drift),
                "target_branch": target_branch,
                "branch_mode": branch_mode,
                "original_branch": original_branch,
                "current_head": current_head,
                "expected_base_commit": expected_head,
                "package_dir": str(package.get("package_dir", "")) if package else "",
                "manifest_path": str(package_manifest_path(package)) if package else "",
                "diff_path": str(package_diff_path(package)) if package else "",
                "changed_files": list(manifest.get("changed_files", [])),
                "copied_files": list(manifest.get("copied_files", [])),
                "deleted_files": list(manifest.get("deleted_files", [])),
                "package_paths": list(package_paths),
                "dirty_paths": list(dirty_paths),
                "drift_paths": list(drift_paths),
                "dirty_overlap": list(dirty_overlap),
                "drift_overlap": list(drift_overlap),
                "safe_paths": list(safe_paths),
                "blocked_paths": list(blocked_paths),
                "requires_allow_dirty_target": requires_allow_dirty_target,
                "requires_allow_base_drift": requires_allow_base_drift,
                "blockers": [],
                "warnings": [],
                "actions": actions,
                "commit_sha": "",
            }
        report["status"] = "failed"
        report.setdefault("errors", [])
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        persist_apply_attempt(state, report)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 1


def start_mode(args: argparse.Namespace) -> int:
    ensure_cli("claude")
    ensure_cli("codex")
    ensure_cli("python3")
    ensure_cli("git")
    ensure_tmux()
    repo = Path(args.repo).resolve()
    task = read_task(args)
    run_root = Path(args.run_root).resolve() if args.run_root else repo / ".claude" / "tmp" / "peer-forge-live"
    run_id = f"{utc_now()}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_name = args.session_name or f"peer-forge-live-{run_id[-8:]}"
    if has_session(session_name):
        raise SystemExit(f"tmux session already exists: {session_name}")

    state = initialize_state(args, repo=repo, task=task, run_dir=run_dir, session_name=session_name)
    save_state(state)

    workspaces = prepare_workspaces(repo, run_dir, args.include_path)
    state["workspaces"] = {
        "repo": str(workspaces.repo),
        "baseline": str(workspaces.baseline),
        "claude": str(workspaces.claude),
        "codex": str(workspaces.codex),
        "git_mode": workspaces.git_mode,
        "cleanup_targets": [str(item) for item in workspaces.cleanup_targets],
        "initial_commit": workspaces.initial_commit,
    }
    state["agents"]["claude"]["workspace"] = str(workspaces.claude)
    state["agents"]["codex"]["workspace"] = str(workspaces.codex)
    save_state(state)

    initial_turn = create_initial_turn(state)
    claude_prompt_path = Path(initial_turn["agents"]["claude"]["session_prompt_path"])
    codex_prompt_path = Path(initial_turn["agents"]["codex"]["session_prompt_path"])

    created_session = False
    try:
        claude_pane = new_session(
            session_name,
            cwd=workspaces.claude,
            command=placeholder_command(),
        )
        created_session = True
        codex_pane = split_window(
            claude_pane,
            cwd=workspaces.codex,
            direction="horizontal",
            command=placeholder_command(),
        )
        supervisor_pane = split_window(
            claude_pane,
            cwd=run_dir,
            direction="vertical",
            command=placeholder_command(),
        )
        set_remain_on_exit(session_name, enabled=True)
        select_layout(session_name, "tiled")
        set_pane_title(claude_pane, "peer-forge-live:claude")
        set_pane_title(codex_pane, "peer-forge-live:codex")
        set_pane_title(supervisor_pane, "peer-forge-live:supervisor")

        pipe_pane(claude_pane, Path(state["logs"]["claude_raw"]))
        pipe_pane(codex_pane, Path(state["logs"]["codex_raw"]))
        pipe_pane(supervisor_pane, Path(state["logs"]["supervisor_raw"]))

        state["agents"]["claude"]["pane_id"] = claude_pane
        state["agents"]["codex"]["pane_id"] = codex_pane
        state["agents"]["supervisor"]["pane_id"] = supervisor_pane
        save_state(state)

        dispatch_turn(state, initial_turn, send_prompts=False)
        respawn_pane(
            claude_pane,
            cwd=workspaces.claude,
            command=build_claude_command(
                model=args.claude_model,
                bare=not args.no_claude_bare,
                prompt_path=claude_prompt_path,
            ),
        )
        respawn_pane(
            codex_pane,
            cwd=workspaces.codex,
            command=build_codex_command(
                workspace=workspaces.codex,
                model=args.codex_model,
                prompt_path=codex_prompt_path,
            ),
        )
        respawn_pane(
            supervisor_pane,
            cwd=run_dir,
            command=build_supervisor_command(state_path_from_run_dir(run_dir)),
        )
    except Exception:
        if created_session:
            kill_session(session_name)
        raise

    if args.no_attach:
        output = {
            "run_id": run_id,
            "session_name": session_name,
            "run_dir": str(run_dir),
            "state_file": str(state_path_from_run_dir(run_dir)),
            "attach": f"tmux attach-session -t {session_name}",
        }
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 0
    attach_session(session_name)
    return 0


def serve_mode(args: argparse.Namespace) -> int:
    state_file = Path(args.state_file).resolve()
    state = load_state(state_file)
    supervisor_log_line(state, f"Supervisor attached to {state['run_id']} in session {state['session_name']}.")
    supervisor_log_line(state, "This is the live peer-forge protocol with plan, execution, review, and signoff phases.")
    try:
        serve_live(state)
        return 0
    except KeyboardInterrupt as exc:
        state["status"] = "aborted"
        state["summary"]["abort_reason"] = str(exc)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run aborted. Report: {report_path(state)}")
        return 130
    except Exception as exc:
        state["status"] = "failed"
        state["summary"]["error"] = f"{type(exc).__name__}: {exc}"
        traceback_path = Path(state["run_dir"]) / "failure-traceback.txt"
        write_text(traceback_path, "".join(traceback.format_exception(exc)))
        state["summary"]["traceback_file"] = str(traceback_path)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run failed: {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    args = parse_args()
    if getattr(args, "command", "") == "serve":
        return serve_mode(args)
    if getattr(args, "command", "") == "resume":
        return resume_mode(args)
    if getattr(args, "command", "") == "apply":
        return apply_mode(args)
    return start_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())

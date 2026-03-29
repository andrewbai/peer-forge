#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import traceback
from typing import Any
import urllib.error
import urllib.request
import uuid
import webbrowser

from live_api import LiveControlServer
from live_engine import ProtocolStateMachine, RunLoop
from live_state import (
    branch_exists,
    clear_boundary_state,
    current_branch_name,
    current_execution_package,
    default_apply_branch,
    git_changed_paths_between,
    git_dirty_paths,
    git_status_porcelain,
    initialize_state,
    load_execution_manifest,
    load_state,
    materialize_execution_package,
    overlap_paths,
    package_diff_path,
    package_manifest_path,
    persist_apply_attempt,
    persist_report,
    process_runtime_state,
    report_path,
    save_state,
    state_path_from_run_dir,
    supervisor_log_line,
    update_run_status,
    write_supervisor_event,
)
from live_supervisor import CliSupervisor, QueueSupervisor
from live_transport import (
    build_claude_command,
    build_codex_command,
    build_supervisor_command,
)
from live_transport_pty import PtyTransport
from live_transport_tmux import TmuxTransport
from peer_consensus import (
    ensure_cli,
    git,
    prepare_workspaces,
    read_task,
    unique_lines,
    utc_now,
    utc_timestamp_precise,
    write_text,
)

CONTROL_READY_TIMEOUT_SECONDS = 30.0
CONTROL_READY_POLL_SECONDS = 0.25
CONTROL_HEALTH_TIMEOUT_SECONDS = 1.5
STOP_WAIT_TIMEOUT_SECONDS = 10.0
STOP_WAIT_POLL_SECONDS = 0.25
TERMINAL_RUN_STATUSES = {"approved", "needs-attention", "failed", "aborted"}


def parse_args() -> argparse.Namespace:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        return parse_serve_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "resume":
        return parse_resume_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        return parse_status_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        return parse_stop_args(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        return parse_apply_args(sys.argv[2:])
    return parse_start_args(sys.argv[1:])


def parse_start_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a live Peer Forge run with interactive Claude and Codex sessions.",
    )
    parser.set_defaults(command="start")
    parser.add_argument("--repo", default=".", help="Repository or workspace root. Defaults to the current directory.")
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("--task", help="Task description.")
    task_group.add_argument("--task-file", help="Path to a file containing the task description.")
    parser.add_argument("--acceptance", action="append", default=[], help="Acceptance criteria line. Repeatable.")
    parser.add_argument("--scope", action="append", default=[], help="Preferred file or directory scope. Repeatable.")
    parser.add_argument(
        "--include-path",
        action="append",
        default=[],
        help="Extra file or directory to copy into isolated workspaces when it is not tracked by git.",
    )
    parser.add_argument("--claude-model", help="Claude model override.")
    parser.add_argument("--codex-model", help="Codex model override.")
    parser.add_argument(
        "--transport",
        choices=("tmux", "pty"),
        default="tmux",
        help="Live transport backend. Default: tmux.",
    )
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
        "--control-host",
        default="127.0.0.1",
        help="Host for the local control API. Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help="Port for the local control API. Use 0 for an ephemeral port. Default: 0.",
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
        "--claude-bare",
        action="store_true",
        help="Enable Claude bare mode. Use this only when you explicitly want bare mode, such as API-key-based auth instead of Claude Max/OAuth.",
    )
    parser.add_argument(
        "--open-ui",
        action="store_true",
        help="Open the local live Web UI in the default browser once the control server is ready.",
    )
    parser.add_argument(
        "--print-control-token",
        action="store_true",
        help="Explicitly print the local control API token in logs or detached JSON output.",
    )
    args = parser.parse_args(argv)
    if args.signoff_rounds < 0:
        parser.error("--signoff-rounds must be >= 0.")
    if args.watchdog_seconds < 0:
        parser.error("--watchdog-seconds must be >= 0.")
    if args.max_watchdog_nudges < 0:
        parser.error("--max-watchdog-nudges must be >= 0.")
    if args.control_port < 0 or args.control_port > 65535:
        parser.error("--control-port must be between 0 and 65535.")
    return args


def parse_serve_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal live supervisor entrypoint for peer-forge-live.")
    parser.set_defaults(command="serve")
    parser.add_argument("--state-file", required=True, help="Path to the live run state.json file.")
    return parser.parse_args(argv)


def parse_resume_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume or re-attach to an existing peer-forge-live run.")
    parser.set_defaults(command="resume")
    parser.add_argument("--state-file", required=True, help="Path to an existing live run state.json file.")
    parser.add_argument(
        "--no-attach",
        action="store_true",
        help="Repair supervisor state if needed, but print attach info instead of attaching immediately.",
    )
    parser.add_argument(
        "--open-ui",
        action="store_true",
        help="Open the local live Web UI in the default browser once the control server is ready.",
    )
    parser.add_argument(
        "--print-control-token",
        action="store_true",
        help="Explicitly print the local control API token in logs or detached JSON output.",
    )
    return parser.parse_args(argv)


def parse_status_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the current lifecycle status of an existing peer-forge-live run.")
    parser.set_defaults(command="status")
    parser.add_argument("--state-file", required=True, help="Path to an existing live run state.json file.")
    parser.add_argument(
        "--open-ui",
        action="store_true",
        help="Open the local live Web UI in the default browser if the control server is currently reachable.",
    )
    parser.add_argument(
        "--print-control-token",
        action="store_true",
        help="Include the local control API token in the status JSON output.",
    )
    return parser.parse_args(argv)


def parse_stop_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop a detached pty-backed peer-forge-live run.")
    parser.set_defaults(command="stop")
    parser.add_argument("--state-file", required=True, help="Path to an existing live run state.json file.")
    parser.add_argument(
        "--print-control-token",
        action="store_true",
        help="Include the local control API token in the stop result JSON output.",
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


def ensure_control_runtime(
    state: dict[str, object],
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    runtime = state.setdefault("runtime", {})  # type: ignore[assignment]
    if not isinstance(runtime, dict):
        raise RuntimeError("runtime state is not an object")
    runtime["supervisor"] = "queue"
    control = runtime.setdefault("control", {})
    if not isinstance(control, dict):
        raise RuntimeError("runtime.control state is not an object")
    control["enabled"] = True
    control["host"] = host if host is not None else str(control.get("host", "127.0.0.1") or "127.0.0.1")
    control["port"] = int(port if port is not None else int(control.get("port", 0) or 0))
    control["token"] = str(control.get("token", "") or uuid.uuid4().hex)
    control.setdefault("base_url", "")
    control.setdefault("events_stream_url", "")
    control.setdefault("web_url", "")
    control["open_ui"] = bool(control.get("open_ui", False))
    control["print_control_token"] = bool(control.get("print_control_token", False))


def update_control_preferences(
    state: dict[str, object],
    *,
    open_ui: bool | None = None,
    print_control_token: bool | None = None,
) -> None:
    runtime = state.setdefault("runtime", {})  # type: ignore[assignment]
    if not isinstance(runtime, dict):
        raise RuntimeError("runtime state is not an object")
    control = runtime.setdefault("control", {})
    if not isinstance(control, dict):
        raise RuntimeError("runtime.control state is not an object")
    if open_ui is not None:
        control["open_ui"] = bool(open_ui)
    if print_control_token is not None:
        control["print_control_token"] = bool(print_control_token)


def reset_control_runtime_urls(state: dict[str, object]) -> None:
    runtime = state.get("runtime", {})
    if not isinstance(runtime, dict):
        return
    control = runtime.get("control", {})
    if not isinstance(control, dict):
        return
    control["base_url"] = ""
    control["events_stream_url"] = ""
    control["web_url"] = ""
    control["last_started_at"] = ""


def control_runtime_endpoints(state: dict[str, object]) -> dict[str, str]:
    runtime = state.get("runtime", {})
    if not isinstance(runtime, dict):
        return {
            "control_url": "",
            "events_stream_url": "",
            "web_url": "",
            "control_token": "",
        }
    control = runtime.get("control", {})
    if not isinstance(control, dict):
        return {
            "control_url": "",
            "events_stream_url": "",
            "web_url": "",
            "control_token": "",
        }
    control_url = str(control.get("base_url", "") or "")
    events_stream_url = str(control.get("events_stream_url", "") or "")
    web_url = str(control.get("web_url", "") or "")
    if not web_url and control_url:
        web_url = f"{control_url}/"
    return {
        "control_url": control_url,
        "events_stream_url": events_stream_url,
        "web_url": web_url,
        "control_token": str(control.get("token", "") or ""),
    }


def control_preferences(state: dict[str, object]) -> dict[str, bool]:
    runtime = state.get("runtime", {})
    if not isinstance(runtime, dict):
        return {"open_ui": False, "print_control_token": False}
    control = runtime.get("control", {})
    if not isinstance(control, dict):
        return {"open_ui": False, "print_control_token": False}
    return {
        "open_ui": bool(control.get("open_ui", False)),
        "print_control_token": bool(control.get("print_control_token", False)),
    }


def control_health_ok(control_url: str, control_token: str) -> bool:
    if not control_url or not control_token:
        return False
    request = urllib.request.Request(
        f"{control_url}/health",
        headers={"X-Peer-Forge-Token": control_token},
    )
    try:
        with urllib.request.urlopen(request, timeout=CONTROL_HEALTH_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return False
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return bool(payload.get("ok", False))


def wait_for_control_ready(
    state_file: Path,
    *,
    timeout: float = CONTROL_READY_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    deadline = time.monotonic() + timeout
    latest_state: dict[str, Any] | None = None
    latest_endpoints = {
        "control_url": "",
        "events_stream_url": "",
        "web_url": "",
        "control_token": "",
    }
    while time.monotonic() < deadline:
        try:
            state = load_state(state_file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(CONTROL_READY_POLL_SECONDS)
            continue
        latest_state = state
        latest_endpoints = control_runtime_endpoints(state)
        if (
            latest_endpoints["control_url"]
            and latest_endpoints["events_stream_url"]
            and latest_endpoints["web_url"]
            and latest_endpoints["control_token"]
            and control_health_ok(latest_endpoints["control_url"], latest_endpoints["control_token"])
        ):
            return latest_state, latest_endpoints
        time.sleep(CONTROL_READY_POLL_SECONDS)
    return latest_state, latest_endpoints


def enrich_detached_output(
    payload: dict[str, Any],
    *,
    state_file: Path,
    include_token: bool,
) -> dict[str, Any]:
    _, endpoints = wait_for_control_ready(state_file)
    payload["control_url"] = endpoints["control_url"]
    payload["events_stream_url"] = endpoints["events_stream_url"]
    payload["web_url"] = endpoints["web_url"]
    if include_token:
        payload["control_token"] = endpoints["control_token"]
    return payload


def print_control_runtime_console(
    endpoints: dict[str, str],
    *,
    include_token: bool,
    stream: Any = None,
) -> None:
    target = stream or sys.stderr
    print(f"[peer-forge-live] Control API: {endpoints['control_url'] or 'n/a'}", file=target, flush=True)
    print(f"[peer-forge-live] Events stream: {endpoints['events_stream_url'] or 'n/a'}", file=target, flush=True)
    print(f"[peer-forge-live] Web UI: {endpoints['web_url'] or 'n/a'}", file=target, flush=True)
    if include_token:
        print(f"[peer-forge-live] Control token: {endpoints['control_token'] or '(empty)'}", file=target, flush=True)


def maybe_open_web_ui(
    endpoints: dict[str, str],
    *,
    log_line: Any | None = None,
    stream: Any = None,
) -> bool:
    web_url = endpoints.get("web_url", "")
    if not web_url:
        return False

    def emit(message: str) -> None:
        if log_line is not None:
            log_line(message)
            return
        if stream is not None:
            print(f"[peer-forge-live] {message}", file=stream, flush=True)

    try:
        opened = webbrowser.open(web_url, new=2)
    except Exception as exc:
        emit(f"Failed to auto-open Web UI: {type(exc).__name__}: {exc}. Open this URL manually: {web_url}")
        return False
    if opened:
        emit(f"Opened Web UI: {web_url}")
        return True
    emit(f"Could not auto-open Web UI. Open this URL manually: {web_url}")
    return False


def log_control_runtime(state: dict[str, object]) -> None:
    endpoints = control_runtime_endpoints(state)
    preferences = control_preferences(state)
    supervisor_log_line(state, f"Control API: {endpoints['control_url'] or 'n/a'}")
    supervisor_log_line(state, f"Events stream: {endpoints['events_stream_url'] or 'n/a'}")
    supervisor_log_line(state, f"Web UI: {endpoints['web_url'] or 'n/a'}")
    if preferences["print_control_token"]:
        supervisor_log_line(state, f"Control token: {endpoints['control_token'] or '(empty)'}")


def owner_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lifecycle_command(state: dict[str, Any], subcommand: str) -> str:
    launcher = Path(state.get("tool_repo_root", "")).resolve() / "bin" / "peer-forge-live"
    command = [str(launcher), subcommand, "--state-file", str(Path(state["state_file"]).resolve())]
    return " ".join(shlex.quote(part) for part in command)


def refresh_owner_process(state: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    process = process_runtime_state(state)
    pid = int(process.get("owner_pid", 0) or 0)
    status = str(state.get("status", "") or "")
    if process.get("owner_exit_code") is not None:
        alive = False
    elif process.get("stopped_at", "") and status in TERMINAL_RUN_STATUSES:
        alive = False
    else:
        alive = owner_pid_alive(pid)
    changed = False
    if bool(process.get("owner_alive", False)) != alive:
        process["owner_alive"] = alive
        changed = True
    if alive and pid > 0:
        process["owner_last_seen_at"] = utc_timestamp_precise()
        changed = True
    elif not alive and pid > 0 and not process.get("stopped_at", ""):
        process["stopped_at"] = utc_timestamp_precise()
        changed = True
    if changed and persist:
        save_state(state)
    return process


def mark_owner_started(state: dict[str, Any], pid: int) -> None:
    process = process_runtime_state(state)
    now = utc_timestamp_precise()
    process["owner_pid"] = int(pid)
    process["owner_started_at"] = now
    process["owner_last_seen_at"] = now
    process["owner_alive"] = True
    process["owner_exit_code"] = None
    process["stopped_at"] = ""
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "owner-process-started",
            "timestamp": now,
            "mode": process.get("mode", ""),
            "owner_pid": int(pid),
        },
    )


def mark_owner_stopped(state: dict[str, Any], *, exit_code: int | None) -> None:
    process = process_runtime_state(state)
    now = utc_timestamp_precise()
    process["owner_alive"] = False
    process["owner_exit_code"] = exit_code
    process["owner_last_seen_at"] = now
    if not process.get("stopped_at", ""):
        process["stopped_at"] = now
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "owner-process-stopped",
            "timestamp": now,
            "mode": process.get("mode", ""),
            "owner_pid": int(process.get("owner_pid", 0) or 0),
            "owner_exit_code": exit_code,
        },
    )


def request_owner_stop(state: dict[str, Any], *, stop_signal: str) -> None:
    process = process_runtime_state(state)
    process["stop_requested_at"] = utc_timestamp_precise()
    process["stop_signal"] = stop_signal
    save_state(state)
    write_supervisor_event(
        state,
        {
            "type": "owner-stop-requested",
            "timestamp": process["stop_requested_at"],
            "mode": process.get("mode", ""),
            "owner_pid": int(process.get("owner_pid", 0) or 0),
            "stop_signal": stop_signal,
        },
    )


def reconcile_detached_owner_state(state: dict[str, Any]) -> dict[str, Any]:
    process = refresh_owner_process(state, persist=False)
    mode = str(process.get("mode", "") or "")
    pid = int(process.get("owner_pid", 0) or 0)
    alive = bool(process.get("owner_alive", False))
    status = str(state.get("status", "") or "")
    changed = False
    if mode != "pty-detached" or pid <= 0 or alive:
        if process.get("stopped_at", "") and status in TERMINAL_RUN_STATUSES:
            changed = True
        if changed:
            save_state(state)
        return process
    if not process.get("stopped_at", ""):
        process["stopped_at"] = utc_timestamp_precise()
        changed = True
    if status in {"starting", "running"}:
        detail = "detached-owner-stopped" if process.get("stop_requested_at", "") else "detached-owner-exited"
        resolution = "abort" if process.get("stop_requested_at", "") else "error"
        clear_boundary_state(state, resolution=resolution)
        changed = update_run_status(
            state,
            "aborted" if process.get("stop_requested_at", "") else "failed",
            detail=detail,
        ) or changed
        if process.get("stop_requested_at", ""):
            state["summary"]["abort_reason"] = state["summary"].get("abort_reason") or "Detached owner process stopped."
        else:
            state["summary"]["error"] = state["summary"].get("error") or "Detached PTY owner process is not running."
        save_state(state)
        persist_report(state)
        return process
    if changed:
        save_state(state)
    return process


def spawn_detached_owner(state: dict[str, Any]) -> subprocess.Popen[bytes]:
    state_file = Path(state["state_file"]).resolve()
    raw_log_path = Path(state["logs"]["supervisor_raw"])
    raw_log_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_supervisor_command(state_file)
    with raw_log_path.open("ab") as raw_log, open(os.devnull, "rb") as devnull:
        return subprocess.Popen(
            command,
            cwd=str(Path(state["run_dir"]).resolve()),
            stdin=devnull,
            stdout=raw_log,
            stderr=raw_log,
            close_fds=True,
            start_new_session=True,
        )


def build_status_payload(
    state: dict[str, Any],
    *,
    include_token: bool,
) -> dict[str, Any]:
    process = process_runtime_state(state)
    endpoints = control_runtime_endpoints(state)
    payload = {
        "run_id": state["run_id"],
        "status": state.get("status", ""),
        "current_phase": state.get("current_phase", ""),
        "session_name": state.get("session_name", ""),
        "state_file": str(Path(state["state_file"]).resolve()),
        "run_dir": str(Path(state["run_dir"]).resolve()),
        "transport": state.get("runtime", {}).get("transport", ""),
        "process_mode": process.get("mode", ""),
        "owner_pid": int(process.get("owner_pid", 0) or 0),
        "owner_alive": bool(process.get("owner_alive", False)),
        "owner_exit_code": process.get("owner_exit_code"),
        "owner_started_at": process.get("owner_started_at", ""),
        "owner_last_seen_at": process.get("owner_last_seen_at", ""),
        "stop_requested_at": process.get("stop_requested_at", ""),
        "stopped_at": process.get("stopped_at", ""),
        "stop_signal": process.get("stop_signal", ""),
        "control_url": endpoints["control_url"],
        "events_stream_url": endpoints["events_stream_url"],
        "web_url": endpoints["web_url"],
        "status_command": lifecycle_command(state, "status"),
    }
    if state.get("runtime", {}).get("transport") == "tmux":
        payload["attach"] = f"tmux attach-session -t {state['session_name']}"
    if process.get("mode", "") == "pty-detached":
        payload["stop_command"] = lifecycle_command(state, "stop")
    if include_token:
        payload["control_token"] = endpoints["control_token"]
    return payload


def should_process_open_ui(state: dict[str, Any]) -> bool:
    process = process_runtime_state(state)
    return process.get("mode", "") != "pty-detached"


def start_mode(args: argparse.Namespace) -> int:
    ensure_cli("claude")
    ensure_cli("codex")
    ensure_cli("python3")
    ensure_cli("git")

    repo = Path(args.repo).resolve()
    task = read_task(args)
    run_root = Path(args.run_root).resolve() if args.run_root else repo / ".claude" / "tmp" / "peer-forge-live"
    run_id = f"{utc_now()}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_name = args.session_name or (
        f"peer-forge-live-{run_id[-8:]}" if args.transport == "tmux" else f"peer-forge-live-local-{run_id[-8:]}"
    )

    state = initialize_state(args, repo=repo, task=task, run_dir=run_dir, session_name=session_name)
    ensure_control_runtime(state, host=args.control_host, port=args.control_port)
    save_state(state)
    if args.transport == "tmux":
        transport = TmuxTransport(state)
        transport.ensure_available()
        if transport.has_session(session_name):
            raise SystemExit(f"tmux session already exists: {session_name}")
    else:
        transport = PtyTransport(state)
        transport.ensure_available()

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

    machine = ProtocolStateMachine(state)
    initial_turn = machine.create_initial_turn()
    claude_prompt_path = Path(initial_turn["agents"]["claude"]["session_prompt_path"])
    codex_prompt_path = Path(initial_turn["agents"]["codex"]["session_prompt_path"])
    supervisor: QueueSupervisor | CliSupervisor
    supervisor = QueueSupervisor(state, transport) if args.transport == "tmux" else CliSupervisor(state, transport)
    runloop = RunLoop(state, transport=transport, supervisor=supervisor, machine=machine)

    if args.transport == "tmux":
        created_session = False
        try:
            panes = transport.create_session_layout(
                session_name=session_name,
                claude_cwd=workspaces.claude,
                codex_cwd=workspaces.codex,
                supervisor_cwd=run_dir,
                logs=state["logs"],
            )
            created_session = True
            state["agents"]["claude"]["pane_id"] = panes["claude"]
            state["agents"]["claude"]["transport_ref"] = panes["claude"]
            state["agents"]["codex"]["pane_id"] = panes["codex"]
            state["agents"]["codex"]["transport_ref"] = panes["codex"]
            state["agents"]["supervisor"]["pane_id"] = panes["supervisor"]
            save_state(state)

            asyncio.run(runloop.dispatch_turn(initial_turn, send_prompts=False))
            transport.respawn(
                panes["claude"],
                cwd=workspaces.claude,
                command=build_claude_command(
                    model=args.claude_model,
                    bare=bool(args.claude_bare),
                    prompt_path=claude_prompt_path,
                ),
            )
            transport.respawn(
                panes["codex"],
                cwd=workspaces.codex,
                command=build_codex_command(
                    workspace=workspaces.codex,
                    model=args.codex_model,
                    prompt_path=codex_prompt_path,
                ),
            )
            transport.respawn(
                panes["supervisor"],
                cwd=run_dir,
                command=build_supervisor_command(state_path_from_run_dir(run_dir)),
            )
        except Exception:
            if created_session:
                transport.kill_session(session_name)
            raise

        if args.no_attach:
            output = {
                "run_id": run_id,
                "session_name": session_name,
                "run_dir": str(run_dir),
                "state_file": str(state_path_from_run_dir(run_dir)),
                "attach": f"tmux attach-session -t {session_name}",
            }
            enrich_detached_output(
                output,
                state_file=state_path_from_run_dir(run_dir),
                include_token=bool(args.print_control_token),
            )
            print(json.dumps(output, indent=2, ensure_ascii=True))
            return 0
        transport.attach(session_name)
        return 0

    if args.no_attach:
        owner = spawn_detached_owner(state)
        mark_owner_started(state, owner.pid)
        latest_state, endpoints = wait_for_control_ready(state_path_from_run_dir(run_dir))
        output_state = latest_state or load_state(state_path_from_run_dir(run_dir))
        reconcile_detached_owner_state(output_state)
        output = build_status_payload(output_state, include_token=bool(args.print_control_token))
        if args.open_ui:
            maybe_open_web_ui(endpoints, stream=sys.stderr)
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 0

    api_server = LiveControlServer(state, supervisor)
    try:
        supervisor.start()
        api_server.start()
        asyncio.run(runloop.dispatch_turn(initial_turn, send_prompts=False))
        transport.start_agent(
            "claude",
            cwd=workspaces.claude,
            command=build_claude_command(
                model=args.claude_model,
                bare=bool(args.claude_bare),
                prompt_path=claude_prompt_path,
            ),
        )
        transport.start_agent(
            "codex",
            cwd=workspaces.codex,
            command=build_codex_command(
                workspace=workspaces.codex,
                model=args.codex_model,
                prompt_path=codex_prompt_path,
            ),
        )
        save_state(state)
        supervisor_log_line(state, f"Supervisor running inline for {state['run_id']} using pty transport.")
        supervisor_log_line(state, "This is the live peer-forge protocol with plan, execution, review, and signoff phases.")
        log_control_runtime(state)
        if control_preferences(state)["open_ui"] and should_process_open_ui(state):
            maybe_open_web_ui(
                control_runtime_endpoints(state),
                log_line=lambda message: supervisor_log_line(state, message),
            )
        asyncio.run(runloop.serve())
        return 0
    except KeyboardInterrupt as exc:
        clear_boundary_state(state, resolution="abort")
        update_run_status(state, "aborted", detail=str(exc))
        state["summary"]["abort_reason"] = str(exc)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run aborted. Report: {report_path(state)}")
        return 130
    except Exception as exc:
        clear_boundary_state(state, resolution="error")
        update_run_status(state, "failed", detail=f"{type(exc).__name__}: {exc}")
        state["summary"]["error"] = f"{type(exc).__name__}: {exc}"
        traceback_path = Path(state["run_dir"]) / "failure-traceback.txt"
        write_text(traceback_path, "".join(traceback.format_exception(exc)))
        state["summary"]["traceback_file"] = str(traceback_path)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        api_server.shutdown()
        supervisor.shutdown()
        asyncio.run(transport.shutdown())


def resume_mode(args: argparse.Namespace) -> int:
    ensure_cli("python3")

    state_file = Path(args.state_file).resolve()
    if not state_file.exists():
        raise SystemExit(f"State file does not exist: {state_file}")
    state = load_state(state_file)
    if state.get("runtime", {}).get("transport") != "tmux":
        raise SystemExit("resume currently supports only tmux-backed live runs.")
    ensure_control_runtime(state)
    update_control_preferences(
        state,
        open_ui=bool(args.open_ui),
        print_control_token=bool(args.print_control_token),
    )
    transport = TmuxTransport(state)
    transport.ensure_available()
    session_name = state["session_name"]
    if not transport.has_session(session_name):
        raise SystemExit(f"tmux session not found: {session_name}")

    run_dir = Path(state["run_dir"])
    supervisor_pane_id, supervisor_action = transport.repair_or_create_supervisor(
        session_name=session_name,
        run_dir=run_dir,
        state_file=state_file,
    )
    state["agents"]["supervisor"]["pane_id"] = supervisor_pane_id
    if supervisor_action in {"supervisor-created", "supervisor-respawned"}:
        reset_control_runtime_urls(state)
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
        output = {
            "run_id": state["run_id"],
            "session_name": session_name,
            "state_file": str(state_file),
            "attach": f"tmux attach-session -t {session_name}",
            "supervisor_action": supervisor_action,
        }
        enrich_detached_output(
            output,
            state_file=state_file,
            include_token=bool(args.print_control_token),
        )
        if args.open_ui and supervisor_action == "supervisor-resumed":
            maybe_open_web_ui(control_runtime_endpoints(load_state(state_file)), stream=sys.stderr)
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 0
    if supervisor_action == "supervisor-resumed" and (args.open_ui or args.print_control_token):
        _, endpoints = wait_for_control_ready(state_file)
        if args.print_control_token:
            print_control_runtime_console(endpoints, include_token=True, stream=sys.stderr)
        if args.open_ui:
            maybe_open_web_ui(endpoints, stream=sys.stderr)
    transport.attach(session_name)
    return 0


def status_mode(args: argparse.Namespace) -> int:
    state_file = Path(args.state_file).resolve()
    if not state_file.exists():
        raise SystemExit(f"State file does not exist: {state_file}")
    state = load_state(state_file)
    reconcile_detached_owner_state(state)
    endpoints = control_runtime_endpoints(state)
    payload = build_status_payload(state, include_token=bool(args.print_control_token))
    if args.open_ui and control_health_ok(endpoints["control_url"], endpoints["control_token"]):
        maybe_open_web_ui(
            {
                "web_url": payload["web_url"],
                "control_url": payload["control_url"],
                "events_stream_url": payload["events_stream_url"],
            },
            stream=sys.stderr,
        )
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def stop_mode(args: argparse.Namespace) -> int:
    state_file = Path(args.state_file).resolve()
    if not state_file.exists():
        raise SystemExit(f"State file does not exist: {state_file}")
    state = load_state(state_file)
    process = reconcile_detached_owner_state(state)
    if state.get("runtime", {}).get("transport") != "pty" or process.get("mode", "") != "pty-detached":
        raise SystemExit("stop currently supports only detached pty-backed live runs.")
    pid = int(process.get("owner_pid", 0) or 0)
    if pid <= 0 or not bool(process.get("owner_alive", False)):
        print(json.dumps(build_status_payload(state, include_token=bool(args.print_control_token)), indent=2, ensure_ascii=True))
        return 0

    request_owner_stop(state, stop_signal="SIGTERM")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + STOP_WAIT_TIMEOUT_SECONDS
    final_state = state
    while time.monotonic() < deadline:
        try:
            final_state = load_state(state_file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(STOP_WAIT_POLL_SECONDS)
            continue
        process = reconcile_detached_owner_state(final_state)
        if not bool(process.get("owner_alive", False)):
            break
        time.sleep(STOP_WAIT_POLL_SECONDS)

    final_state = load_state(state_file)
    reconcile_detached_owner_state(final_state)
    payload = build_status_payload(final_state, include_token=bool(args.print_control_token))
    if bool(process_runtime_state(final_state).get("owner_alive", False)):
        payload["stop_timeout"] = True
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 1
    payload["stop_timeout"] = False
    print(json.dumps(payload, indent=2, ensure_ascii=True))
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
    report: dict[str, object] = {}
    manifest: dict[str, list[str]] = {"changed_files": [], "copied_files": [], "deleted_files": []}
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
                "Target repository has dirty paths that overlap the execution package: " + ", ".join(dirty_overlap)
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
            warnings.append("Target HEAD drift was detected, but it does not overlap the execution package paths.")

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


def serve_mode(args: argparse.Namespace) -> int:
    state_file = Path(args.state_file).resolve()
    state = load_state(state_file)
    transport_name = str(state.get("runtime", {}).get("transport", "tmux") or "tmux")
    control = state.get("runtime", {}).get("control", {})
    ensure_control_runtime(
        state,
        host=str(control.get("host", "127.0.0.1") or "127.0.0.1"),
        port=int(control.get("port", 0) or 0),
    )
    save_state(state)
    if transport_name == "tmux":
        transport = TmuxTransport(state)
        transport.ensure_available()
        supervisor = CliSupervisor(state, transport)
        supervisor.start()
        api_server = LiveControlServer(state, supervisor)
        api_server.start()
        machine = ProtocolStateMachine(state)
        runloop = RunLoop(state, transport=transport, supervisor=supervisor, machine=machine)
        supervisor_log_line(state, f"Supervisor attached to {state['run_id']} in session {state['session_name']}.")
        supervisor_log_line(state, "This is the live peer-forge protocol with plan, execution, review, and signoff phases.")
        log_control_runtime(state)
        if control_preferences(state)["open_ui"] and should_process_open_ui(state):
            maybe_open_web_ui(
                control_runtime_endpoints(state),
                log_line=lambda message: supervisor_log_line(state, message),
            )
        try:
            asyncio.run(runloop.serve())
            return 0
        except KeyboardInterrupt as exc:
            clear_boundary_state(state, resolution="abort")
            update_run_status(state, "aborted", detail=str(exc))
            state["summary"]["abort_reason"] = str(exc)
            save_state(state)
            persist_report(state)
            supervisor_log_line(state, f"Live run aborted. Report: {report_path(state)}")
            return 130
        except Exception as exc:
            clear_boundary_state(state, resolution="error")
            update_run_status(state, "failed", detail=f"{type(exc).__name__}: {exc}")
            state["summary"]["error"] = f"{type(exc).__name__}: {exc}"
            traceback_path = Path(state["run_dir"]) / "failure-traceback.txt"
            write_text(traceback_path, "".join(traceback.format_exception(exc)))
            state["summary"]["traceback_file"] = str(traceback_path)
            save_state(state)
            persist_report(state)
            supervisor_log_line(state, f"Live run failed: {type(exc).__name__}: {exc}")
            return 1
        finally:
            api_server.shutdown()
            supervisor.shutdown()

    if transport_name != "pty":
        raise SystemExit(f"Unsupported transport in state.json: {transport_name}")

    process = process_runtime_state(state)
    if process.get("mode", "") != "pty-detached":
        raise SystemExit("serve for pty transport currently supports only detached owner mode.")

    transport = PtyTransport(state)
    transport.ensure_available()
    supervisor = QueueSupervisor(state, transport)
    machine = ProtocolStateMachine(state)
    runloop = RunLoop(state, transport=transport, supervisor=supervisor, machine=machine)
    current_turn = state["turns"][-1] if state.get("turns") else machine.create_initial_turn()
    if current_turn["phase"] != "plan-initial" or current_turn["status"] != "pending":
        raise SystemExit("Detached pty serve cannot resume an existing run. Use status/stop and start a fresh run.")
    claude_prompt_path = Path(current_turn["agents"]["claude"]["session_prompt_path"])
    codex_prompt_path = Path(current_turn["agents"]["codex"]["session_prompt_path"])
    api_server = LiveControlServer(state, supervisor)
    exit_code = 0
    received_signal = {"name": ""}

    def _signal_handler(signum: int, _frame: Any) -> None:
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = f"signal-{signum}"
        received_signal["name"] = signal_name
        raise KeyboardInterrupt(f"Received {signal_name}")

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    try:
        mark_owner_started(state, os.getpid())
        supervisor.start()
        api_server.start()
        transport.start_agent(
            "claude",
            cwd=Path(state["agents"]["claude"]["workspace"]),
            command=build_claude_command(
                model=state.get("claude_model"),
                bare=bool(state.get("claude_bare", False)),
                prompt_path=claude_prompt_path,
            ),
        )
        transport.start_agent(
            "codex",
            cwd=Path(state["agents"]["codex"]["workspace"]),
            command=build_codex_command(
                workspace=Path(state["agents"]["codex"]["workspace"]),
                model=state.get("codex_model"),
                prompt_path=codex_prompt_path,
            ),
        )
        save_state(state)
        supervisor_log_line(state, f"Detached PTY owner running for {state['run_id']} (pid {os.getpid()}).")
        supervisor_log_line(state, "This is the live peer-forge protocol with plan, execution, review, and signoff phases.")
        log_control_runtime(state)
        if control_preferences(state)["open_ui"] and should_process_open_ui(state):
            maybe_open_web_ui(
                control_runtime_endpoints(state),
                log_line=lambda message: supervisor_log_line(state, message),
            )
        asyncio.run(runloop.serve())
        exit_code = 0
        return 0
    except KeyboardInterrupt as exc:
        exit_code = 130
        clear_boundary_state(state, resolution="abort")
        update_run_status(state, "aborted", detail=received_signal["name"] or str(exc))
        state["summary"]["abort_reason"] = str(exc)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run aborted. Report: {report_path(state)}")
        return 130
    except Exception as exc:
        exit_code = 1
        clear_boundary_state(state, resolution="error")
        update_run_status(state, "failed", detail=f"{type(exc).__name__}: {exc}")
        state["summary"]["error"] = f"{type(exc).__name__}: {exc}"
        traceback_path = Path(state["run_dir"]) / "failure-traceback.txt"
        write_text(traceback_path, "".join(traceback.format_exception(exc)))
        state["summary"]["traceback_file"] = str(traceback_path)
        save_state(state)
        persist_report(state)
        supervisor_log_line(state, f"Live run failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        api_server.shutdown()
        supervisor.shutdown()
        asyncio.run(transport.shutdown())
        mark_owner_stopped(state, exit_code=exit_code)
        if state.get("status") in TERMINAL_RUN_STATUSES:
            persist_report(state)


def main() -> int:
    args = parse_args()
    if getattr(args, "command", "") == "serve":
        return serve_mode(args)
    if getattr(args, "command", "") == "resume":
        return resume_mode(args)
    if getattr(args, "command", "") == "status":
        return status_mode(args)
    if getattr(args, "command", "") == "stop":
        return stop_mode(args)
    if getattr(args, "command", "") == "apply":
        return apply_mode(args)
    return start_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())

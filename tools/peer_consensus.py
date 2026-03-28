#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


TEMP_COMMIT_NAME = "peer-consensus"
TEMP_COMMIT_EMAIL = "peer-consensus@local"
DEFAULT_AGENT_TIMEOUT_SECONDS = 1800
SEVERITY_WEIGHTS = {
    "critical": 13,
    "high": 8,
    "medium": 5,
    "low": 2,
    "info": 0,
}
PROGRESS_LOCK = threading.Lock()
PROGRESS_LOG_PATH: Path | None = None
SUPERVISOR_LOG_PATH: Path | None = None
STAGE_TIMINGS: list[dict[str, Any]] = []


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "scope",
        "steps",
        "strengths",
        "risks",
        "tests",
        "assumptions",
    ],
    "properties": {
        "summary": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


PLAN_REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "scope",
        "steps",
        "accepted_review_items",
        "rejected_review_items",
        "adopted_peer_strengths",
        "remaining_risks",
        "tests",
    ],
    "properties": {
        "summary": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "accepted_review_items": {"type": "array", "items": {"type": "string"}},
        "rejected_review_items": {"type": "array", "items": {"type": "string"}},
        "adopted_peer_strengths": {"type": "array", "items": {"type": "string"}},
        "remaining_risks": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
    },
}


FINAL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "scope",
        "steps",
        "preserved_strengths",
        "remaining_risks",
        "tests",
        "assumptions",
    ],
    "properties": {
        "summary": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "preserved_strengths": {"type": "array", "items": {"type": "string"}},
        "remaining_risks": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["severity", "title", "detail"],
    "properties": {
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low", "info"],
        },
        "title": {"type": "string"},
        "detail": {"type": "string"},
        "file": {"type": "string"},
    },
}


REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_verdict",
        "summary",
        "findings",
        "strengths_to_preserve",
        "must_fix",
        "optional_improvements",
    ],
    "properties": {
        "overall_verdict": {
            "type": "string",
            "enum": ["approve", "approve_with_changes", "reject"],
        },
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": FINDING_SCHEMA},
        "strengths_to_preserve": {"type": "array", "items": {"type": "string"}},
        "must_fix": {"type": "array", "items": {"type": "string"}},
        "optional_improvements": {"type": "array", "items": {"type": "string"}},
    },
}


CONSENSUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "preferred_base",
        "approve_self_as_final",
        "approve_peer_as_final",
        "must_preserve_from_self",
        "must_preserve_from_peer",
        "blocking_objections_to_self_final",
        "blocking_objections_to_peer_final",
    ],
    "properties": {
        "summary": {"type": "string"},
        "preferred_base": {
            "type": "string",
            "enum": ["self", "peer", "neither"],
        },
        "approve_self_as_final": {"type": "boolean"},
        "approve_peer_as_final": {"type": "boolean"},
        "must_preserve_from_self": {"type": "array", "items": {"type": "string"}},
        "must_preserve_from_peer": {"type": "array", "items": {"type": "string"}},
        "blocking_objections_to_self_final": {
            "type": "array",
            "items": FINDING_SCHEMA,
        },
        "blocking_objections_to_peer_final": {
            "type": "array",
            "items": FINDING_SCHEMA,
        },
    },
}


EXECUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "changed_files",
        "tests",
        "remaining_risks",
        "assumptions",
    ],
    "properties": {
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "remaining_risks": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class StageRun:
    agent: str
    phase: str
    workspace: Path
    stage_dir: Path
    read_only: bool
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    verbose_path: Path
    parsed_path: Path
    parsed: dict[str, Any]
    changed_files: list[str]
    diff_path: Path
    package_dir: Path
    duration_seconds: float


@dataclass
class CheckpointState:
    enabled: bool
    history_path: Path
    notes_history_path: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    next_index: int = 1
    next_note_index: int = 1


class RunAborted(RuntimeError):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        checkpoint_id = str(event.get("id", "unknown"))
        super().__init__(f"Run aborted by supervisor at checkpoint {checkpoint_id}.")


@dataclass
class WorkspaceSet:
    repo: Path
    baseline: Path
    claude: Path
    codex: Path
    git_mode: bool
    cleanup_targets: list[Path]
    initial_commit: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_timestamp_precise() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_duration(seconds: float) -> str:
    rounded = int(round(seconds))
    minutes, secs = divmod(rounded, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_timeout(timeout: int | None) -> str:
    if timeout is None:
        return "none"
    return format_duration(float(timeout))


def log_progress(message: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    line = f"[peer-consensus {timestamp}] {message}"
    with PROGRESS_LOCK:
        print(line, file=sys.stderr, flush=True)
        if PROGRESS_LOG_PATH is not None:
            with PROGRESS_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if SUPERVISOR_LOG_PATH is not None:
            with SUPERVISOR_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def log_supervisor(message: str, *, verbose_handle: TextIO | None = None) -> None:
    with PROGRESS_LOCK:
        print(message, file=sys.stderr, flush=True)
        if SUPERVISOR_LOG_PATH is not None:
            with SUPERVISOR_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        if verbose_handle is not None:
            verbose_handle.write(message + "\n")
            verbose_handle.flush()


def log_checkpoint(message: str, *, include_progress: bool = False) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    line = f"[checkpoint {timestamp}] {message}"
    with PROGRESS_LOCK:
        print(line, file=sys.stderr, flush=True)
        if include_progress and PROGRESS_LOG_PATH is not None:
            with PROGRESS_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if SUPERVISOR_LOG_PATH is not None:
            with SUPERVISOR_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def initialize_run_state(progress_log_path: Path, supervisor_log_path: Path | None) -> None:
    global PROGRESS_LOG_PATH, SUPERVISOR_LOG_PATH, STAGE_TIMINGS
    with PROGRESS_LOCK:
        PROGRESS_LOG_PATH = progress_log_path
        SUPERVISOR_LOG_PATH = supervisor_log_path
        STAGE_TIMINGS = []
        progress_log_path.parent.mkdir(parents=True, exist_ok=True)
        progress_log_path.write_text("", encoding="utf-8")
        if supervisor_log_path is not None:
            supervisor_log_path.parent.mkdir(parents=True, exist_ok=True)
            supervisor_log_path.write_text("", encoding="utf-8")


def finalize_run_state() -> None:
    global PROGRESS_LOG_PATH, SUPERVISOR_LOG_PATH, STAGE_TIMINGS
    with PROGRESS_LOCK:
        PROGRESS_LOG_PATH = None
        SUPERVISOR_LOG_PATH = None
        STAGE_TIMINGS = []


def record_stage_timing(entry: dict[str, Any]) -> None:
    with PROGRESS_LOCK:
        STAGE_TIMINGS.append(entry)


def snapshot_stage_timings() -> list[dict[str, Any]]:
    with PROGRESS_LOCK:
        return [dict(item) for item in STAGE_TIMINGS]


def stream_label(agent: str, phase: str, stream_name: str) -> str:
    return f"[{utc_timestamp_precise()}][{agent}][{phase}][{stream_name}]"


def stream_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    input_text: str | None,
    timeout: int | None,
    stdout_path: Path,
    stderr_path: Path,
    verbose_path: Path,
    agent: str,
    phase: str,
) -> subprocess.CompletedProcess[str]:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    verbose_path.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def pump(
        stream: TextIO,
        raw_handle: TextIO,
        chunks: list[str],
        stream_name: str,
        verbose_handle: TextIO,
    ) -> None:
        while True:
            line = stream.readline()
            if line == "":
                break
            chunks.append(line)
            raw_handle.write(line)
            raw_handle.flush()
            log_supervisor(
                f"{stream_label(agent, phase, stream_name)} {line.rstrip()}",
                verbose_handle=verbose_handle,
            )

    with (
        stdout_path.open("w", encoding="utf-8") as stdout_handle,
        stderr_path.open("w", encoding="utf-8") as stderr_handle,
        verbose_path.open("w", encoding="utf-8") as verbose_handle,
    ):
        stdout_thread = threading.Thread(
            target=pump,
            args=(proc.stdout, stdout_handle, stdout_chunks, "stdout", verbose_handle),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=pump,
            args=(proc.stderr, stderr_handle, stderr_chunks, "stderr", verbose_handle),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        if input_text is not None and proc.stdin is not None:
            try:
                try:
                    proc.stdin.write(input_text)
                    proc.stdin.flush()
                except BrokenPipeError:
                    pass
            finally:
                proc.stdin.close()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()

        stdout_thread.join()
        stderr_thread.join()

    stdout_text = "".join(stdout_chunks)
    stderr_text = "".join(stderr_chunks)
    if timed_out:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {format_cmd_display(cmd)}\nSTDOUT:\n{stdout_text}\nSTDERR:\n{stderr_text}"
        )
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode,
        stdout_text,
        stderr_text,
    )


def clip_text(text: str, limit: int = 100) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def emit_stage_summary(agent: str, phase: str, schema: dict[str, Any], parsed: dict[str, Any]) -> None:
    prefix = f"[summary][{phase}][{agent}]"
    if schema in (PLAN_SCHEMA, PLAN_REVISION_SCHEMA, FINAL_PLAN_SCHEMA):
        log_supervisor(
            f"{prefix} summary=\"{clip_text(str(parsed.get('summary', '')))}\" "
            f"steps={len(parsed.get('steps', []))} "
            f"risks={len(parsed.get('risks', parsed.get('remaining_risks', [])))} "
            f"tests={len(parsed.get('tests', []))}"
        )
        return
    if schema is REVIEW_SCHEMA:
        findings = parsed.get("findings", [])
        top_finding = ""
        if findings:
            top_finding = clip_text(str(findings[0].get("title", "")))
        line = (
            f"{prefix} verdict={parsed.get('overall_verdict', '')} "
            f"findings={len(findings)} must_fix={len(parsed.get('must_fix', []))}"
        )
        if top_finding:
            line += f" top_finding=\"{top_finding}\""
        log_supervisor(line)
        return
    if schema is CONSENSUS_SCHEMA:
        blockers = len(parsed.get("blocking_objections_to_self_final", [])) + len(
            parsed.get("blocking_objections_to_peer_final", [])
        )
        log_supervisor(
            f"{prefix} preferred_base={parsed.get('preferred_base', '')} "
            f"approve_self={parsed.get('approve_self_as_final', False)} "
            f"approve_peer={parsed.get('approve_peer_as_final', False)} "
            f"blockers={blockers}"
        )
        return
    if schema is EXECUTION_SCHEMA:
        log_supervisor(
            f"{prefix} summary=\"{clip_text(str(parsed.get('summary', '')))}\" "
            f"changed_files={len(parsed.get('changed_files', []))} "
            f"tests={len(parsed.get('tests', []))} "
            f"remaining_risks={len(parsed.get('remaining_risks', []))}"
        )


def format_cmd_display(cmd: list[str], *, max_arg_length: int = 160) -> str:
    parts: list[str] = []
    for arg in cmd:
        normalized = arg.replace("\n", "\\n")
        if len(normalized) > max_arg_length:
            normalized = f"{normalized[: max_arg_length - 20]}...<{len(normalized)} chars>"
        parts.append(shlex.quote(normalized))
    return " ".join(parts)


def normalize_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a dual-agent peer-consensus coding workflow with Claude Code and Codex.",
    )
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
        "--review-rounds",
        "--signoff-rounds",
        dest="review_rounds",
        type=int,
        default=1,
        help="Maximum additional execute-fix-review rounds after the first implementation review. Default: 1. `--signoff-rounds` is a deprecated alias.",
    )
    parser.add_argument(
        "--apply-final",
        action="store_true",
        help="Copy the approved final files back into the source workspace.",
    )
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
        help=f"Maximum seconds to wait for each Claude/Codex stage. Use 0 to disable. Default: {DEFAULT_AGENT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="Stream Claude/Codex output to the terminal and write prefixed verbose logs without changing the protocol.",
    )
    parser.add_argument(
        "--supervise-checkpoints",
        action="store_true",
        help="Pause at stage boundaries for supervisor commands (continue, inspect, note, abort). Implies --supervise.",
    )
    parser.add_argument(
        "--cleanup-workspaces",
        action="store_true",
        help="Remove the temporary isolated workspaces after the run completes.",
    )
    parser.add_argument(
        "--keep-workspaces",
        "--keep-run-dir",
        dest="keep_workspaces",
        action="store_true",
        help="Keep isolated workspaces even if cleanup-workspaces is set. `--keep-run-dir` is a deprecated alias.",
    )
    parser.add_argument(
        "--run-root",
        help="Override the artifact root. Defaults to <repo>/.claude/tmp/peer-consensus.",
    )
    parser.add_argument(
        "--no-claude-bare",
        action="store_true",
        help="Disable Claude bare mode. Bare mode is enabled by default to reduce prompt contamination.",
    )
    args = parser.parse_args()
    if args.agent_timeout_seconds < 0:
        parser.error("--agent-timeout-seconds must be >= 0.")
    if args.supervise_checkpoints and not (args.task or args.task_file):
        parser.error("--supervise-checkpoints requires --task or --task-file because stdin is reserved for checkpoint commands.")
    if args.supervise_checkpoints and not sys.stdin.isatty():
        parser.error("--supervise-checkpoints requires an interactive terminal.")
    if "--signoff-rounds" in sys.argv:
        print("Warning: --signoff-rounds is deprecated; use --review-rounds.", file=sys.stderr)
    if "--keep-run-dir" in sys.argv:
        print("Warning: --keep-run-dir is deprecated; use --keep-workspaces.", file=sys.stderr)
    return args


def read_task(args: argparse.Namespace) -> str:
    if args.task:
        return args.task.strip()
    if args.task_file:
        return Path(args.task_file).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise SystemExit("Provide --task, --task-file, or pipe task text on stdin.")


def ensure_cli(binary: str) -> None:
    if shutil.which(binary):
        return
    raise SystemExit(f"Required binary not found in PATH: {binary}")


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=merged_env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = normalize_subprocess_output(exc.stdout)
        stderr = normalize_subprocess_output(exc.stderr)
        raise RuntimeError(
            f"Command timed out after {timeout}s: {format_cmd_display(cmd)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        ) from exc
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {format_cmd_display(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=True) + "\n")


def read_json_loose(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Expected JSON output, got empty text.")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object, got: {type(value)!r}")
    return value


def unique_lines(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def normalize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in SEVERITY_WEIGHTS:
            severity = "medium"
        normalized = {
            "severity": severity,
            "title": str(item.get("title", "")).strip() or "Untitled finding",
            "detail": str(item.get("detail", "")).strip() or "No detail provided.",
        }
        file_value = str(item.get("file", "")).strip()
        if file_value:
            normalized["file"] = file_value
        result.append(normalized)
    return result


def score_findings(findings: list[dict[str, Any]]) -> int:
    score = 0
    for finding in findings:
        score += SEVERITY_WEIGHTS.get(str(finding.get("severity", "medium")).lower(), 5)
    return score


def git(repo: Path, *args: str, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return run_cmd(["git", *args], cwd=repo, check=check, timeout=timeout)


def is_git_repo(repo: Path) -> bool:
    proc = git(repo, "rev-parse", "--show-toplevel", check=False)
    return proc.returncode == 0


def normalize_repo_path(repo: Path, raw: str) -> str:
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    try:
        return resolved.relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"--include-path must stay inside the repo: {raw}") from exc


def copy_path(src_root: Path, dst_root: Path, rel: str) -> None:
    src = src_root / rel
    dst = dst_root / rel
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prepare_workspaces(repo: Path, run_dir: Path, include_paths: list[str]) -> WorkspaceSet:
    workspaces_dir = run_dir / "workspaces"
    workspaces_dir.mkdir(parents=True, exist_ok=True)
    baseline = workspaces_dir / "baseline"
    claude_ws = workspaces_dir / "claude"
    codex_ws = workspaces_dir / "codex"

    normalized_include_paths = [normalize_repo_path(repo, item) for item in include_paths]

    if is_git_repo(repo):
        initial_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
        overlay_patch = run_dir / "source-overlay.patch"
        overlay_text = git(repo, "diff", "--binary", "HEAD").stdout
        write_text(overlay_patch, overlay_text)
        untracked = git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
        write_text(run_dir / "source-untracked.txt", "\n".join(untracked) + ("\n" if untracked else ""))

        cleanup_targets: list[Path] = []
        for workspace in (baseline, claude_ws, codex_ws):
            git(repo, "worktree", "add", "--detach", str(workspace), initial_commit)
            cleanup_targets.append(workspace)
            if overlay_text.strip():
                git(workspace, "apply", "--allow-empty", "--binary", str(overlay_patch))
            for rel in untracked:
                copy_path(repo, workspace, rel)
            for rel in normalized_include_paths:
                copy_path(repo, workspace, rel)
            status = git(workspace, "status", "--porcelain", "--untracked-files=all").stdout.strip()
            if status:
                git(workspace, "add", "-A")
                git(
                    workspace,
                    "-c",
                    f"user.name={TEMP_COMMIT_NAME}",
                    "-c",
                    f"user.email={TEMP_COMMIT_EMAIL}",
                    "commit",
                    "-m",
                    "peer-consensus baseline snapshot",
                )
        return WorkspaceSet(
            repo=repo,
            baseline=baseline,
            claude=claude_ws,
            codex=codex_ws,
            git_mode=True,
            cleanup_targets=cleanup_targets,
            initial_commit=initial_commit,
        )

    ignore = shutil.ignore_patterns(".git", "node_modules", "dist", "build", ".claude/tmp")
    shutil.copytree(repo, baseline, ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(repo, claude_ws, ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(repo, codex_ws, ignore=ignore, dirs_exist_ok=True)
    for rel in normalized_include_paths:
        copy_path(repo, baseline, rel)
        copy_path(repo, claude_ws, rel)
        copy_path(repo, codex_ws, rel)
    return WorkspaceSet(
        repo=repo,
        baseline=baseline,
        claude=claude_ws,
        codex=codex_ws,
        git_mode=False,
        cleanup_targets=[baseline, claude_ws, codex_ws],
        initial_commit=None,
    )


def cleanup_workspaces(repo: Path, workspaces: WorkspaceSet) -> None:
    if not workspaces.git_mode:
        for path in workspaces.cleanup_targets:
            shutil.rmtree(path, ignore_errors=True)
        return
    for path in workspaces.cleanup_targets:
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def maybe_mark_untracked_for_diff(workspace: Path, git_mode: bool) -> None:
    if not git_mode:
        return
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    if untracked:
        git(workspace, "add", "-N", "--", *untracked)


def changed_files_git(workspace: Path) -> list[str]:
    tracked = git(workspace, "diff", "--name-only", "--find-renames", "HEAD").stdout.splitlines()
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return unique_lines(tracked + untracked)


def build_noindex_diff(baseline: Path, workspace: Path, diff_path: Path) -> list[str]:
    files = list_changed_files_fs(baseline, workspace)
    diff_chunks: list[str] = []
    for rel in files:
        left = baseline / rel
        right = workspace / rel
        proc = run_cmd(
            [
                "git",
                "diff",
                "--no-index",
                "--binary",
                "--no-ext-diff",
                "--",
                str(left),
                str(right),
            ],
            check=False,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(proc.stderr or proc.stdout)
        diff_chunks.append(proc.stdout)
    write_text(diff_path, "".join(diff_chunks))
    return files


def list_changed_files_fs(baseline: Path, workspace: Path) -> list[str]:
    baseline_files = {
        path.relative_to(baseline).as_posix()
        for path in baseline.rglob("*")
        if path.is_file()
    }
    workspace_files = {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    all_files = sorted(baseline_files | workspace_files)
    changed: list[str] = []
    for rel in all_files:
        left = baseline / rel
        right = workspace / rel
        if not left.exists() or not right.exists():
            changed.append(rel)
            continue
        if left.read_bytes() != right.read_bytes():
            changed.append(rel)
    return changed


def collect_package(workspace: Path, baseline: Path, package_dir: Path, git_mode: bool) -> tuple[list[str], Path]:
    package_dir.mkdir(parents=True, exist_ok=True)
    diff_path = package_dir / "solution.diff"
    if git_mode:
        maybe_mark_untracked_for_diff(workspace, True)
        write_text(diff_path, git(workspace, "diff", "--binary", "HEAD").stdout)
        changed = changed_files_git(workspace)
    else:
        changed = build_noindex_diff(baseline, workspace, diff_path)
    deleted: list[str] = []
    copied: list[str] = []
    files_dir = package_dir / "files"
    for rel in changed:
        path = workspace / rel
        if not path.exists():
            deleted.append(rel)
            continue
        copied.append(rel)
        dst = files_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    write_json(
        package_dir / "manifest.json",
        {
            "changed_files": changed,
            "copied_files": copied,
            "deleted_files": deleted,
        },
    )
    return changed, diff_path


def create_empty_package(package_dir: Path) -> tuple[list[str], Path]:
    package_dir.mkdir(parents=True, exist_ok=True)
    diff_path = package_dir / "solution.diff"
    write_text(diff_path, "")
    write_json(
        package_dir / "manifest.json",
        {
            "changed_files": [],
            "copied_files": [],
            "deleted_files": [],
        },
    )
    return [], diff_path


def prompt_header(task: str, acceptance: list[str], scope: list[str]) -> str:
    acceptance_block = "\n".join(f"- {item}" for item in acceptance) or "- No extra acceptance criteria were provided."
    scope_block = "\n".join(f"- {item}" for item in scope) or "- Scope is not explicitly constrained. Keep changes minimal."
    return textwrap.dedent(
        f"""
        Task:
        {task}

        Acceptance Criteria:
        {acceptance_block}

        Preferred Scope:
        {scope_block}
        """
    ).strip()


def note_summary(note: dict[str, Any], limit: int = 120) -> str:
    return clip_text(str(note.get("text", "")), limit=limit)


def supervisor_notes_block(supervisor_notes: list[dict[str, Any]]) -> str:
    if not supervisor_notes:
        return ""
    rendered_notes: list[str] = []
    for note in supervisor_notes:
        body = textwrap.indent(str(note.get("text", "")).strip(), "  ")
        rendered_notes.append(
            "\n".join(
                [
                    f"[{note.get('id', '')}] Added at checkpoint {note.get('checkpoint_id', '')}; applies from {note.get('applies_from_phase', '')}:",
                    body or "  (empty)",
                ]
            )
        )
    notes_body = "\n\n".join(rendered_notes)
    return textwrap.dedent(
        f"""
        Supervisor Notes:
        - These notes come from the human supervisor and apply symmetrically to both agents.
        - They supplement the task and acceptance criteria but do not override hard constraints such as workspace isolation, read-only phases, or the no-commit rule.
        - Incorporate them from this phase onward.

        {notes_body}
        """
    ).strip()


def prompt_context(task: str, acceptance: list[str], scope: list[str], supervisor_notes: list[dict[str, Any]]) -> str:
    header = prompt_header(task, acceptance, scope)
    notes_block = supervisor_notes_block(supervisor_notes)
    if not notes_block:
        return header
    return f"{header}\n\n{notes_block}"


def build_plan_prompt(
    agent: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {agent} in a dual-agent peer consensus protocol.

        This phase is plan-only. Do not modify code. Do not create commits, branches, or tags.
        Produce the best implementation plan you can, as if the other agent does not exist.

        {context_block}

        Instructions:
        - Work only inside this isolated workspace.
        - Focus on the plan, not the code.
        - Keep the proposed implementation within the preferred scope unless the task clearly requires more.
        - If scope must expand, explain the minimum necessary expansion.
        - Give concrete execution steps instead of vague advice.
        - When you finish, return JSON that matches the schema exactly.
        """
    ).strip()


def build_plan_review_prompt(
    reviewer: str,
    peer: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    peer_plan: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {reviewer} reviewing {peer}'s implementation plan in a dual-agent peer consensus protocol.

        Review only. Do not modify your workspace.

        {context_block}

        Peer plan:
        {json.dumps(peer_plan, indent=2, ensure_ascii=True)}

        Review standard:
        - correctness of the proposed plan
        - missing steps
        - hidden risks
        - unrealistic scope
        - weak verification strategy

        Return JSON that matches the schema exactly.
        """
    ).strip()


def build_plan_revision_prompt(
    agent: str,
    peer: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    peer_review: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {agent} revising your own implementation plan after reading {peer}'s review.

        This is still a plan-only phase. Do not modify code. Do not create commits, branches, or tags.

        {context_block}

        Peer review of your work:
        {json.dumps(peer_review, indent=2, ensure_ascii=True)}

        Revision rules:
        - Fix valid issues.
        - Keep the plan concrete and executable.
        - Keep useful strengths from your original plan.
        - Return JSON that matches the schema exactly.
        """
    ).strip()


def build_plan_consensus_prompt(
    agent: str,
    peer: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    own_revision: dict[str, Any],
    peer_revision: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {agent} deciding which revised implementation plan should become the final plan base.

        Do not modify your workspace.

        {context_block}

        Your revised summary:
        {json.dumps(own_revision, indent=2, ensure_ascii=True)}

        Peer revised plan:
        {json.dumps(peer_revision, indent=2, ensure_ascii=True)}

        Decide:
        - which revised plan is the better final base
        - what must be preserved from your own plan
        - what must be preserved from the peer plan
        - what blockers remain against either plan

        Return JSON that matches the schema exactly.
        """
    ).strip()


def build_final_plan_prompt(
    base_agent: str,
    peer: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    merge_brief: dict[str, Any],
    own_revision: dict[str, Any],
    peer_revision: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {base_agent}. Your revised plan is the starting point for the final implementation plan.

        This is still a plan-only phase. Do not modify code. Produce the final agreed implementation plan.

        {context_block}

        Your revised summary:
        {json.dumps(own_revision, indent=2, ensure_ascii=True)}

        Peer revised plan:
        {json.dumps(peer_revision, indent=2, ensure_ascii=True)}

        Merge brief:
        {json.dumps(merge_brief, indent=2, ensure_ascii=True)}

        Final plan rules:
        - Preserve valid strengths from both sides.
        - Resolve blockers inside the plan when possible.
        - Keep the plan concrete, internally consistent, and directly executable.
        - Return JSON that matches the schema exactly.
        """
    ).strip()


def build_execute_prompt(
    agent: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    final_plan: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {agent} executing the agreed final plan.

        This is the code-writing phase. Modify code only inside this isolated workspace.
        Do not create commits, branches, or tags.

        {context_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Execution rules:
        - Follow the final plan closely.
        - Keep changes minimal and coherent.
        - If reality differs from the plan, adapt pragmatically and explain why.
        - Run targeted verification if it is cheap and local.

        Return JSON that matches the schema exactly.
        """
    ).strip()


def build_execution_review_prompt(
    reviewer: str,
    executor: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    final_plan: dict[str, Any],
    execution_summary: dict[str, Any],
    execution_package: Path,
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {reviewer} reviewing {executor}'s implementation against the agreed final plan.

        Review only. Do not modify your workspace.

        {context_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Execution summary:
        {json.dumps(execution_summary, indent=2, ensure_ascii=True)}

        Implementation artifacts:
        - Diff: {execution_package / "solution.diff"}
        - Manifest: {execution_package / "manifest.json"}
        - Changed file copies root: {execution_package / "files"}

        Review standard:
        - adherence to the final plan
        - correctness
        - regressions
        - edge cases
        - missing tests

        Return JSON that matches the schema exactly.
        """
    ).strip()


def build_execution_fix_prompt(
    agent: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    final_plan: dict[str, Any],
    review_feedback: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    context_block = prompt_context(task, acceptance, scope, supervisor_notes or [])
    return textwrap.dedent(
        f"""
        You are {agent} updating the implementation after peer review.

        This is still the execution phase. Modify code only inside this isolated workspace.
        Do not create commits, branches, or tags.

        {context_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Review feedback:
        {json.dumps(review_feedback, indent=2, ensure_ascii=True)}

        Fix rules:
        - Address valid review findings.
        - Keep the implementation aligned with the final plan.
        - Keep the diff focused.

        Return JSON that matches the schema exactly.
        """
    ).strip()


def checkpoint_stage_summary(stage: StageRun) -> str:
    parsed = stage.parsed
    if "overall_verdict" in parsed:
        findings = parsed.get("findings", [])
        return (
            f"verdict={parsed.get('overall_verdict', '')} "
            f"findings={len(findings)} must_fix={len(parsed.get('must_fix', []))}"
        )
    if "preferred_base" in parsed:
        blockers = len(parsed.get("blocking_objections_to_self_final", [])) + len(
            parsed.get("blocking_objections_to_peer_final", [])
        )
        return (
            f"preferred_base={parsed.get('preferred_base', '')} "
            f"approve_self={parsed.get('approve_self_as_final', False)} "
            f"approve_peer={parsed.get('approve_peer_as_final', False)} "
            f"blockers={blockers}"
        )
    if "changed_files" in parsed and "remaining_risks" in parsed:
        return (
            f"changed_files={len(parsed.get('changed_files', []))} "
            f"tests={len(parsed.get('tests', []))} "
            f"remaining_risks={len(parsed.get('remaining_risks', []))}"
        )
    if "steps" in parsed:
        return (
            f"summary=\"{clip_text(str(parsed.get('summary', '')))}\" "
            f"steps={len(parsed.get('steps', []))} "
            f"tests={len(parsed.get('tests', []))}"
        )
    return f"keys={','.join(sorted(parsed.keys())[:6])}"


def checkpoint_stage_record(stage: StageRun) -> dict[str, Any]:
    record = {
        "agent": stage.agent,
        "phase": stage.phase,
        "mode": "read-only" if stage.read_only else "write",
        "stage_dir": str(stage.stage_dir),
        "workspace": str(stage.workspace),
        "prompt_path": str(stage.prompt_path),
        "stdout_path": str(stage.stdout_path),
        "stderr_path": str(stage.stderr_path),
        "verbose_path": str(stage.verbose_path),
        "verbose_exists": stage.verbose_path.exists(),
        "parsed_path": str(stage.parsed_path),
        "summary": checkpoint_stage_summary(stage),
        "duration_seconds": stage.duration_seconds,
    }
    if not stage.read_only:
        record.update(
            {
                "package_dir": str(stage.package_dir),
                "manifest_path": str(stage.package_dir / "manifest.json"),
                "diff_path": str(stage.diff_path),
                "changed_files_count": len(stage.changed_files),
                "changed_files": list(stage.changed_files),
            }
        )
    return record


def supervisor_note_record(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": note.get("id", ""),
        "checkpoint_id": note.get("checkpoint_id", ""),
        "created_at": note.get("created_at", ""),
        "applies_from_phase": note.get("applies_from_phase", ""),
        "status": note.get("status", ""),
        "summary": note_summary(note),
        "record_file": note.get("record_file", ""),
    }


def format_changed_files_preview(changed_files: list[str], limit: int = 5) -> str:
    if not changed_files:
        return "none"
    preview = ", ".join(changed_files[:limit])
    remaining = len(changed_files) - limit
    if remaining > 0:
        preview += f", ... (+{remaining} more)"
    return preview


def format_checkpoint_inspection(
    checkpoint_id: str,
    description: str,
    run_dir: Path,
    stages: list[StageRun],
    active_notes: list[dict[str, Any]],
    notes_history_path: Path,
) -> list[str]:
    lines = [
        f"{checkpoint_id}: {description}",
        f"Run dir: {run_dir}",
        f"Stages: {len(stages)}",
    ]
    if active_notes:
        lines.append(f"Active supervisor notes: {len(active_notes)}")
        lines.append(f"Notes history: {notes_history_path}")
        for note in active_notes:
            lines.append(
                f"  {note.get('id', '')}: applies_from={note.get('applies_from_phase', '')} summary=\"{note_summary(note)}\""
            )
    else:
        lines.append("Active supervisor notes: none")
    for index, stage in enumerate(stages, start=1):
        mode_label = "read-only" if stage.read_only else "write"
        lines.extend(
            [
                f"Stage {index}: agent={stage.agent} phase={stage.phase} mode={mode_label}",
                f"  stage_dir: {stage.stage_dir}",
                f"  workspace: {stage.workspace}",
                f"  prompt: {stage.prompt_path}",
                f"  parsed: {stage.parsed_path}",
                f"  stdout: {stage.stdout_path}",
                f"  stderr: {stage.stderr_path}",
                f"  summary: {checkpoint_stage_summary(stage)}",
            ]
        )
        if stage.verbose_path.exists():
            lines.append(f"  verbose: {stage.verbose_path}")
        else:
            lines.append("  verbose: not available for this stage")
        if not stage.read_only:
            lines.extend(
                [
                    f"  package: {stage.package_dir}",
                    f"  manifest: {stage.package_dir / 'manifest.json'}",
                    f"  diff: {stage.diff_path}",
                    f"  changed_files: {len(stage.changed_files)}",
                    f"  changed_preview: {format_changed_files_preview(stage.changed_files)}",
                ]
            )
    return lines


def read_supervisor_command(prompt: str) -> str:
    try:
        print(prompt, file=sys.stderr, end="", flush=True)
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        print("", file=sys.stderr, flush=True)
        return "abort"
    if line == "":
        print("", file=sys.stderr, flush=True)
        return "abort"
    return line.strip()


def read_supervisor_note(checkpoint_id: str) -> str | None:
    log_checkpoint(f"{checkpoint_id}: enter supervisor note text. Finish with a line containing only ---")
    lines: list[str] = []
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            print("", file=sys.stderr, flush=True)
            log_checkpoint(f"{checkpoint_id}: note entry cancelled.")
            return None
        if line == "":
            print("", file=sys.stderr, flush=True)
            log_checkpoint(f"{checkpoint_id}: note entry cancelled because stdin closed.")
            return None
        stripped = line.rstrip("\n")
        if stripped.strip() == "---":
            break
        lines.append(stripped)
    note_text = "\n".join(lines).strip()
    if not note_text:
        log_checkpoint(f"{checkpoint_id}: empty note discarded.")
        return None
    return note_text


def run_supervisor_checkpoint(
    checkpoint_state: CheckpointState,
    *,
    name: str,
    description: str,
    stages: list[StageRun],
    run_dir: Path,
    next_note_phase: str | None,
) -> None:
    if not checkpoint_state.enabled:
        return
    checkpoint_number = checkpoint_state.next_index
    checkpoint_state.next_index += 1
    checkpoint_id = f"{checkpoint_number:02d}-{name}"
    event = {
        "id": checkpoint_id,
        "name": name,
        "description": description,
        "entered_at": utc_timestamp_precise(),
        "run_dir": str(run_dir),
        "stage_count": len(stages),
        "stages": [checkpoint_stage_record(stage) for stage in stages],
        "active_notes": [supervisor_note_record(note) for note in checkpoint_state.notes],
        "commands": [],
    }
    log_progress(f"Checkpoint {checkpoint_id} reached: {description}")
    log_checkpoint(f"{checkpoint_id}: {description}")
    if next_note_phase is None:
        log_checkpoint("Actions: Enter/continue, i/inspect, a/abort")
    else:
        log_checkpoint("Actions: Enter/continue, i/inspect, n/note, a/abort")
    while True:
        if next_note_phase is None:
            prompt = f"[{checkpoint_id}] action [Enter=continue, i=inspect, a=abort]: "
        else:
            prompt = f"[{checkpoint_id}] action [Enter=continue, i=inspect, n=note, a=abort]: "
        command = read_supervisor_command(prompt)
        normalized = command.lower()
        command_event = {
            "timestamp": utc_timestamp_precise(),
            "raw": command,
        }
        if normalized in ("", "c", "continue"):
            command_event["normalized"] = "continue"
            event["commands"].append(command_event)
            event["final_action"] = "continue"
            break
        if normalized in ("i", "inspect"):
            command_event["normalized"] = "inspect"
            event["commands"].append(command_event)
            for line in format_checkpoint_inspection(
                checkpoint_id,
                description,
                run_dir,
                stages,
                checkpoint_state.notes,
                checkpoint_state.notes_history_path,
            ):
                log_checkpoint(line)
            continue
        if normalized in ("n", "note"):
            command_event["normalized"] = "note"
            if next_note_phase is None:
                command_event["result"] = "unavailable"
                event["commands"].append(command_event)
                log_checkpoint(f"{checkpoint_id}: no later agent stage remains, so this checkpoint cannot add a supervisor note.")
                continue
            note_text = read_supervisor_note(checkpoint_id)
            if note_text is None:
                command_event["result"] = "cancelled"
                event["commands"].append(command_event)
                continue
            note_id = f"note-{checkpoint_state.next_note_index:02d}"
            checkpoint_state.next_note_index += 1
            note_record_file = checkpoint_state.notes_history_path.parent / f"{note_id}.json"
            note = {
                "id": note_id,
                "created_at": utc_timestamp_precise(),
                "checkpoint_id": checkpoint_id,
                "checkpoint_name": name,
                "applies_from_phase": next_note_phase,
                "status": "active",
                "text": note_text,
                "summary": note_summary({"text": note_text}),
                "record_file": str(note_record_file),
            }
            write_json(note_record_file, note)
            append_jsonl(checkpoint_state.notes_history_path, note)
            checkpoint_state.notes.append(note)
            event.setdefault("notes_added", []).append(supervisor_note_record(note))
            command_event["result"] = "added"
            command_event["note_id"] = note_id
            event["commands"].append(command_event)
            log_checkpoint(
                f"{checkpoint_id}: added {note_id}; applies from {next_note_phase}; summary=\"{note_summary(note)}\""
            )
            continue
        if normalized in ("a", "abort"):
            command_event["normalized"] = "abort"
            event["commands"].append(command_event)
            event["final_action"] = "abort"
            break
        if normalized in ("h", "help", "?"):
            command_event["normalized"] = "help"
            event["commands"].append(command_event)
            if next_note_phase is None:
                log_checkpoint("Available commands: Enter/continue, i/inspect, a/abort")
            else:
                log_checkpoint("Available commands: Enter/continue, i/inspect, n/note, a/abort")
            continue
        command_event["normalized"] = "invalid"
        event["commands"].append(command_event)
        if next_note_phase is None:
            log_checkpoint("Unknown command. Use Enter/continue, i/inspect, or a/abort.")
        else:
            log_checkpoint("Unknown command. Use Enter/continue, i/inspect, n/note, or a/abort.")
    event["resolved_at"] = utc_timestamp_precise()
    event["record_file"] = str(checkpoint_state.history_path.parent / f"{checkpoint_id}.json")
    write_json(Path(event["record_file"]), event)
    append_jsonl(checkpoint_state.history_path, event)
    checkpoint_state.events.append(event)
    if event["final_action"] == "abort":
        log_progress(f"Checkpoint {checkpoint_id} requested abort.")
        raise RunAborted(event)
    log_progress(f"Checkpoint {checkpoint_id} continuing.")


def snapshot_workspace_state(workspace: Path, git_mode: bool) -> tuple[str, str]:
    if git_mode:
        status = git(workspace, "status", "--porcelain", "--untracked-files=all").stdout
        diff = git(workspace, "diff", "--binary", "HEAD").stdout
        return status, diff
    files: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(f"{rel}:{path.stat().st_size}:{digest}")
    return "\n".join(files), ""


def workspace_head(workspace: Path, git_mode: bool) -> str:
    if not git_mode:
        return ""
    return git(workspace, "rev-parse", "HEAD").stdout.strip()


def assert_workspace_unchanged(
    before_status: str,
    before_diff: str,
    workspace: Path,
    git_mode: bool,
    agent: str,
    phase: str,
) -> None:
    after_status, after_diff = snapshot_workspace_state(workspace, git_mode)
    if before_status != after_status or before_diff != after_diff:
        raise RuntimeError(f"{agent} changed its workspace during read-only phase '{phase}'.")


def run_claude(
    prompt: str,
    phase: str,
    workspace: Path,
    shared_dirs: list[Path],
    schema: dict[str, Any],
    output_dir: Path,
    model: str | None,
    bare: bool,
    timeout: int | None,
    supervise: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
    verbose_path = output_dir / "verbose.log"
    parsed_path = output_dir / "parsed.json"
    write_text(prompt_path, prompt)
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, ensure_ascii=True),
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
    ]
    if bare:
        cmd.append("--bare")
    for extra in [workspace, *shared_dirs]:
        cmd.extend(["--add-dir", str(extra)])
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    if supervise:
        proc = stream_subprocess(
            cmd,
            cwd=workspace,
            input_text=None,
            timeout=timeout,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            verbose_path=verbose_path,
            agent="claude",
            phase=phase,
        )
    else:
        proc = run_cmd(cmd, cwd=workspace, check=False, timeout=timeout)
        write_text(stdout_path, proc.stdout)
        write_text(stderr_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"Claude failed in {workspace}.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    parsed = read_json_loose(proc.stdout)
    write_json(parsed_path, parsed)
    return {
        "prompt_path": prompt_path,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "verbose_path": verbose_path,
        "parsed_path": parsed_path,
        "parsed": parsed,
    }


def run_codex(
    prompt: str,
    phase: str,
    workspace: Path,
    shared_dirs: list[Path],
    schema: dict[str, Any],
    output_dir: Path,
    model: str | None,
    writable: bool,
    timeout: int | None,
    supervise: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
    verbose_path = output_dir / "verbose.log"
    parsed_path = output_dir / "parsed.json"
    schema_path = output_dir / "schema.json"
    last_message_path = output_dir / "last-message.txt"
    write_text(prompt_path, prompt)
    write_json(schema_path, schema)
    cmd = [
        "codex",
        "exec",
        "-C",
        str(workspace),
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-schema",
        str(schema_path),
        "-o",
        str(last_message_path),
    ]
    if writable:
        cmd.append("--full-auto")
    else:
        cmd.extend(["-s", "read-only"])
    for extra in shared_dirs:
        cmd.extend(["--add-dir", str(extra)])
    if model:
        cmd.extend(["-m", model])
    cmd.append("-")
    if supervise:
        proc = stream_subprocess(
            cmd,
            cwd=workspace,
            input_text=prompt,
            timeout=timeout,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            verbose_path=verbose_path,
            agent="codex",
            phase=phase,
        )
    else:
        proc = run_cmd(cmd, cwd=workspace, input_text=prompt, check=False, timeout=timeout)
        write_text(stdout_path, proc.stdout)
        write_text(stderr_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"Codex failed in {workspace}.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    parsed_text = last_message_path.read_text(encoding="utf-8")
    parsed = read_json_loose(parsed_text)
    write_json(parsed_path, parsed)
    return {
        "prompt_path": prompt_path,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "verbose_path": verbose_path,
        "parsed_path": parsed_path,
        "parsed": parsed,
    }


def run_agent_stage(
    *,
    agent: str,
    phase: str,
    workspace: Path,
    baseline: Path,
    git_mode: bool,
    shared_dirs: list[Path],
    prompt: str,
    schema: dict[str, Any],
    stage_dir: Path,
    claude_model: str | None,
    codex_model: str | None,
    claude_bare: bool,
    agent_timeout: int | None,
    supervise: bool,
    read_only: bool,
) -> StageRun:
    mode_label = "read-only" if read_only else "write"
    started_monotonic = time.monotonic()
    started_at = utc_timestamp_precise()
    log_progress(
        f"{phase}: {agent} started ({mode_label}, timeout={format_timeout(agent_timeout)})"
    )
    before_status = ""
    before_diff = ""
    before_head = workspace_head(workspace, git_mode)
    try:
        if read_only:
            before_status, before_diff = snapshot_workspace_state(workspace, git_mode)
        if agent == "claude":
            result = run_claude(
                prompt,
                phase,
                workspace,
                shared_dirs,
                schema,
                stage_dir,
                claude_model,
                claude_bare,
                agent_timeout,
                supervise,
            )
        elif agent == "codex":
            result = run_codex(
                prompt,
                phase,
                workspace,
                shared_dirs,
                schema,
                stage_dir,
                codex_model,
                not read_only,
                agent_timeout,
                supervise,
            )
        else:
            raise ValueError(agent)
        after_head = workspace_head(workspace, git_mode)
        if before_head != after_head:
            raise RuntimeError(f"{agent} changed HEAD during phase '{phase}'. Creating commits or switching refs is not allowed.")
        if read_only:
            assert_workspace_unchanged(before_status, before_diff, workspace, git_mode, agent, phase)
        package_dir = stage_dir / "package"
        if read_only:
            changed_files, diff_path = create_empty_package(package_dir)
        else:
            changed_files, diff_path = collect_package(workspace, baseline, package_dir, git_mode)
        duration_seconds = round(time.monotonic() - started_monotonic, 3)
        ended_at = utc_timestamp_precise()
        record_stage_timing(
            {
                "phase": phase,
                "agent": agent,
                "status": "completed",
                "read_only": read_only,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration_seconds,
                "timeout_seconds": agent_timeout,
                "stage_dir": str(stage_dir),
            }
        )
        duration = format_duration(duration_seconds)
        log_progress(f"{phase}: {agent} completed in {duration}")
        if supervise:
            emit_stage_summary(agent, phase, schema, result["parsed"])
        return StageRun(
            agent=agent,
            phase=phase,
            workspace=workspace,
            stage_dir=stage_dir,
            read_only=read_only,
            prompt_path=result["prompt_path"],
            stdout_path=result["stdout_path"],
            stderr_path=result["stderr_path"],
            verbose_path=result["verbose_path"],
            parsed_path=result["parsed_path"],
            parsed=result["parsed"],
            changed_files=changed_files,
            diff_path=diff_path,
            package_dir=package_dir,
            duration_seconds=duration_seconds,
        )
    except Exception as exc:
        duration_seconds = round(time.monotonic() - started_monotonic, 3)
        ended_at = utc_timestamp_precise()
        record_stage_timing(
            {
                "phase": phase,
                "agent": agent,
                "status": "failed",
                "read_only": read_only,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration_seconds,
                "timeout_seconds": agent_timeout,
                "stage_dir": str(stage_dir),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        duration = format_duration(duration_seconds)
        log_progress(f"{phase}: {agent} failed after {duration}: {exc}")
        raise


def run_parallel_stage_pair(
    *,
    claude_kwargs: dict[str, Any],
    codex_kwargs: dict[str, Any],
) -> tuple[StageRun, StageRun]:
    errors: dict[str, Exception] = {}
    results: dict[str, StageRun] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="peer-consensus") as executor:
        futures = {
            "claude": executor.submit(run_agent_stage, **claude_kwargs),
            "codex": executor.submit(run_agent_stage, **codex_kwargs),
        }
        for agent, future in futures.items():
            try:
                results[agent] = future.result()
            except Exception as exc:
                errors[agent] = exc
    if errors:
        details = "\n\n".join(f"{agent}: {error}" for agent, error in errors.items())
        raise RuntimeError(f"Parallel stage pair failed:\n{details}")
    return results["claude"], results["codex"]


def choose_final_base(claude_consensus: dict[str, Any], codex_consensus: dict[str, Any]) -> str:
    claude_candidate_score = (
        score_findings(normalize_findings(claude_consensus.get("blocking_objections_to_self_final", [])))
        + score_findings(normalize_findings(codex_consensus.get("blocking_objections_to_peer_final", [])))
    )
    codex_candidate_score = (
        score_findings(normalize_findings(codex_consensus.get("blocking_objections_to_self_final", [])))
        + score_findings(normalize_findings(claude_consensus.get("blocking_objections_to_peer_final", [])))
    )
    if claude_consensus.get("approve_self_as_final") and codex_consensus.get("approve_peer_as_final"):
        claude_candidate_score -= 50
    if codex_consensus.get("approve_self_as_final") and claude_consensus.get("approve_peer_as_final"):
        codex_candidate_score -= 50
    if claude_consensus.get("preferred_base") == "self":
        claude_candidate_score -= 5
    if codex_consensus.get("preferred_base") == "peer":
        claude_candidate_score -= 5
    if codex_consensus.get("preferred_base") == "self":
        codex_candidate_score -= 5
    if claude_consensus.get("preferred_base") == "peer":
        codex_candidate_score -= 5
    return "claude" if claude_candidate_score <= codex_candidate_score else "codex"


def build_merge_brief(
    base_agent: str,
    claude_consensus: dict[str, Any],
    codex_consensus: dict[str, Any],
) -> dict[str, Any]:
    if base_agent == "claude":
        keep_self = unique_lines(list(claude_consensus.get("must_preserve_from_self", [])))
        adopt_peer = unique_lines(
            list(claude_consensus.get("must_preserve_from_peer", []))
            + list(codex_consensus.get("must_preserve_from_self", []))
        )
        blockers = normalize_findings(
            list(claude_consensus.get("blocking_objections_to_self_final", []))
            + list(codex_consensus.get("blocking_objections_to_peer_final", []))
        )
    else:
        keep_self = unique_lines(list(codex_consensus.get("must_preserve_from_self", [])))
        adopt_peer = unique_lines(
            list(codex_consensus.get("must_preserve_from_peer", []))
            + list(claude_consensus.get("must_preserve_from_self", []))
        )
        blockers = normalize_findings(
            list(codex_consensus.get("blocking_objections_to_self_final", []))
            + list(claude_consensus.get("blocking_objections_to_peer_final", []))
        )
    return {
        "base_agent": base_agent,
        "keep_from_base": keep_self,
        "adopt_from_peer": adopt_peer,
        "resolve_blockers": blockers,
    }


def apply_final_to_source(repo: Path, workspace: Path, changed_files: list[str]) -> None:
    for rel in changed_files:
        src = workspace / rel
        dst = repo / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()


def markdown_report(data: dict[str, Any]) -> str:
    status = str(data.get("status", "completed"))
    checkpoint_events = data.get("checkpoint_events", [])
    supervisor_notes = data.get("supervisor_notes", [])
    if status == "failed":
        lines = [
            f"# Peer Consensus Run {data['run_id']}",
            "",
            f"- Repo: `{data['repo']}`",
            f"- Task: {data['task'].strip()}",
            f"- Status: `{status}`",
            f"- Failed phase: `{data.get('failed_phase', 'unknown')}`",
            f"- Exit code: `{data.get('exit_code', 1)}`",
            f"- Supervised: `{data.get('supervised', False)}`",
            f"- Checkpoint supervision: `{data.get('checkpoint_supervision', False)}`",
            "",
            "## Error",
            data.get("error", "Unknown error."),
            "",
            "## Artifacts",
            f"- Run dir: `{data['run_dir']}`",
            f"- Progress log: `{data.get('progress_log', '')}`",
            f"- Supervisor log: `{data.get('supervisor_log', '')}`",
            f"- Checkpoint history: `{data.get('checkpoint_history', '')}`",
            f"- Checkpoint events: `{len(checkpoint_events)}`",
            f"- Notes history: `{data.get('notes_history', '')}`",
            f"- Supervisor notes: `{len(supervisor_notes)}`",
            f"- Report file: `{Path(data['run_dir']) / 'report.json'}`",
            f"- Traceback file: `{data.get('traceback_file', '')}`",
        ]
        return "\n".join(lines) + "\n"
    if status == "aborted":
        aborted_checkpoint = data.get("aborted_checkpoint", {})
        lines = [
            f"# Peer Consensus Run {data['run_id']}",
            "",
            f"- Repo: `{data['repo']}`",
            f"- Task: {data['task'].strip()}",
            f"- Status: `{status}`",
            f"- Exit code: `{data.get('exit_code', 130)}`",
            f"- Supervised: `{data.get('supervised', False)}`",
            f"- Checkpoint supervision: `{data.get('checkpoint_supervision', False)}`",
            "",
            "## Abort",
            data.get("error", "Run aborted by supervisor."),
            "",
            "## Checkpoint",
            f"- ID: `{aborted_checkpoint.get('id', '')}`",
            f"- Description: {aborted_checkpoint.get('description', '')}",
            "",
            "## Artifacts",
            f"- Run dir: `{data['run_dir']}`",
            f"- Progress log: `{data.get('progress_log', '')}`",
            f"- Supervisor log: `{data.get('supervisor_log', '')}`",
            f"- Checkpoint history: `{data.get('checkpoint_history', '')}`",
            f"- Checkpoint events: `{len(checkpoint_events)}`",
            f"- Notes history: `{data.get('notes_history', '')}`",
            f"- Supervisor notes: `{len(supervisor_notes)}`",
            f"- Report file: `{Path(data['run_dir']) / 'report.json'}`",
        ]
        return "\n".join(lines) + "\n"

    lines = [
        f"# Peer Consensus Run {data['run_id']}",
        "",
        f"- Repo: `{data['repo']}`",
        f"- Task: {data['task'].strip()}",
        f"- Status: `{status}`",
        f"- Exit code: `{data.get('exit_code', 0 if data.get('final_approved') else 2)}`",
        f"- Supervised: `{data.get('supervised', False)}`",
        f"- Checkpoint supervision: `{data.get('checkpoint_supervision', False)}`",
        f"- Final plan base: `{data['final_plan_base']}`",
        f"- Executor: `{data['executor']}`",
        f"- Reviewer: `{data['reviewer']}`",
        f"- Final approved: `{data['final_approved']}`",
        "",
        "## Final Changed Files",
    ]
    final_files = data.get("final_changed_files", [])
    if final_files:
        lines.extend(f"- `{item}`" for item in final_files)
    else:
        lines.append("- None")
    lines.extend(["", "## Supervisor Notes"])
    if supervisor_notes:
        lines.extend(
            f"- `{note.get('id', '')}` from `{note.get('checkpoint_id', '')}` applies from `{note.get('applies_from_phase', '')}`: {note_summary(note)}"
            for note in supervisor_notes
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Implementation Review",
            f"- Verdict: `{data['implementation_review']['overall_verdict']}`",
            f"- Summary: {data['implementation_review']['summary']}",
            "",
            "## Artifacts",
            f"- Run dir: `{data['run_dir']}`",
            f"- Progress log: `{data.get('progress_log', '')}`",
            f"- Supervisor log: `{data.get('supervisor_log', '')}`",
            f"- Checkpoint history: `{data.get('checkpoint_history', '')}`",
            f"- Checkpoint events: `{len(checkpoint_events)}`",
            f"- Notes history: `{data.get('notes_history', '')}`",
            f"- Supervisor notes: `{len(supervisor_notes)}`",
            f"- Final plan file: `{data['final_plan_file']}`",
            f"- Final package: `{data['final_package']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    ensure_cli("claude")
    ensure_cli("codex")
    repo = Path(args.repo).resolve()
    task = read_task(args)
    agent_timeout = args.agent_timeout_seconds or None
    supervise_enabled = args.supervise or args.supervise_checkpoints
    run_root = Path(args.run_root).resolve() if args.run_root else repo / ".claude" / "tmp" / "peer-consensus"
    run_id = f"{utc_now()}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    progress_log_path = run_dir / "progress.log"
    supervisor_log_path = run_dir / "supervisor.log" if supervise_enabled else None
    checkpoint_state = CheckpointState(
        enabled=args.supervise_checkpoints,
        history_path=run_dir / "checkpoints" / "history.jsonl",
        notes_history_path=run_dir / "notes" / "history.jsonl",
    )
    if checkpoint_state.enabled:
        write_text(checkpoint_state.history_path, "")
        write_text(checkpoint_state.notes_history_path, "")
    initialize_run_state(progress_log_path, supervisor_log_path)
    log_progress(
        f"Run {run_id} started for {repo}; artifacts: {run_dir}; agent-timeout={format_timeout(agent_timeout)}"
    )
    write_text(run_dir / "task.txt", task + "\n")
    write_json(
        run_dir / "config.json",
        {
            "repo": str(repo),
            "task": task,
            "acceptance": args.acceptance,
            "scope": args.scope,
            "include_path": args.include_path,
            "claude_model": args.claude_model,
            "codex_model": args.codex_model,
            "review_rounds": args.review_rounds,
            "agent_timeout_seconds": args.agent_timeout_seconds,
            "supervise": supervise_enabled,
            "supervise_checkpoints": args.supervise_checkpoints,
            "apply_final": args.apply_final,
            "cleanup_workspaces": args.cleanup_workspaces,
            "keep_workspaces": args.keep_workspaces,
        },
    )

    current_phase = "prepare-workspaces"
    workspaces: WorkspaceSet | None = None
    final_report: dict[str, Any] = {}
    final_plan_base = ""
    final_plan_file = run_dir / "final-plan.json"
    current_execution: StageRun | None = None
    implementation_review: StageRun | None = None
    final_approved = False

    def checkpoint_history_value() -> str:
        return str(checkpoint_state.history_path) if checkpoint_state.enabled else ""

    def notes_history_value() -> str:
        return str(checkpoint_state.notes_history_path) if checkpoint_state.enabled else ""

    try:
        workspaces = prepare_workspaces(repo, run_dir, args.include_path)
        log_progress("Prepared isolated workspaces.")
        common_stage_kwargs = {
            "baseline": workspaces.baseline,
            "git_mode": workspaces.git_mode,
            "claude_model": args.claude_model,
            "codex_model": args.codex_model,
            "claude_bare": not args.no_claude_bare,
            "agent_timeout": agent_timeout,
            "supervise": supervise_enabled,
        }

        def maybe_checkpoint(name: str, description: str, *stages: StageRun, next_note_phase: str | None) -> None:
            nonlocal current_phase
            if not checkpoint_state.enabled:
                return
            previous_phase = current_phase
            current_phase = f"checkpoint-{name}"
            try:
                run_supervisor_checkpoint(
                    checkpoint_state,
                    name=name,
                    description=description,
                    stages=list(stages),
                    run_dir=run_dir,
                    next_note_phase=next_note_phase,
                )
            except Exception:
                raise
            else:
                current_phase = previous_phase

        current_phase = "plan-consensus"
        log_progress("Phase 1/3: Plan consensus (4 parallel stages + final plan)")
        current_phase = "plan-initial"
        initial_claude, initial_codex = run_parallel_stage_pair(
            claude_kwargs={
                **common_stage_kwargs,
                "agent": "claude",
                "phase": "plan-initial",
                "workspace": workspaces.claude,
                "shared_dirs": [run_dir],
                "prompt": build_plan_prompt("Claude Code", task, args.acceptance, args.scope, checkpoint_state.notes),
                "schema": PLAN_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-initial" / "claude",
                "read_only": True,
            },
            codex_kwargs={
                **common_stage_kwargs,
                "agent": "codex",
                "phase": "plan-initial",
                "workspace": workspaces.codex,
                "shared_dirs": [run_dir],
                "prompt": build_plan_prompt("Codex", task, args.acceptance, args.scope, checkpoint_state.notes),
                "schema": PLAN_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-initial" / "codex",
                "read_only": True,
            },
        )
        maybe_checkpoint(
            "plan-initial",
            "Initial plan drafts completed.",
            initial_claude,
            initial_codex,
            next_note_phase="plan-review",
        )

        current_phase = "plan-review"
        review_claude, review_codex = run_parallel_stage_pair(
            claude_kwargs={
                **common_stage_kwargs,
                "agent": "claude",
                "phase": "plan-review",
                "workspace": workspaces.claude,
                "shared_dirs": [run_dir],
                "prompt": build_plan_review_prompt(
                    "Claude Code",
                    "Codex",
                    task,
                    args.acceptance,
                    args.scope,
                    initial_codex.parsed,
                    checkpoint_state.notes,
                ),
                "schema": REVIEW_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-review" / "claude-on-codex",
                "read_only": True,
            },
            codex_kwargs={
                **common_stage_kwargs,
                "agent": "codex",
                "phase": "plan-review",
                "workspace": workspaces.codex,
                "shared_dirs": [run_dir],
                "prompt": build_plan_review_prompt(
                    "Codex",
                    "Claude Code",
                    task,
                    args.acceptance,
                    args.scope,
                    initial_claude.parsed,
                    checkpoint_state.notes,
                ),
                "schema": REVIEW_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-review" / "codex-on-claude",
                "read_only": True,
            },
        )
        maybe_checkpoint(
            "plan-review",
            "Cross-review of the initial plans completed.",
            review_claude,
            review_codex,
            next_note_phase="plan-revise",
        )

        current_phase = "plan-revise"
        revised_claude, revised_codex = run_parallel_stage_pair(
            claude_kwargs={
                **common_stage_kwargs,
                "agent": "claude",
                "phase": "plan-revise",
                "workspace": workspaces.claude,
                "shared_dirs": [run_dir],
                "prompt": build_plan_revision_prompt(
                    "Claude Code",
                    "Codex",
                    task,
                    args.acceptance,
                    args.scope,
                    review_codex.parsed,
                    checkpoint_state.notes,
                ),
                "schema": PLAN_REVISION_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-revision" / "claude",
                "read_only": True,
            },
            codex_kwargs={
                **common_stage_kwargs,
                "agent": "codex",
                "phase": "plan-revise",
                "workspace": workspaces.codex,
                "shared_dirs": [run_dir],
                "prompt": build_plan_revision_prompt(
                    "Codex",
                    "Claude Code",
                    task,
                    args.acceptance,
                    args.scope,
                    review_claude.parsed,
                    checkpoint_state.notes,
                ),
                "schema": PLAN_REVISION_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-revision" / "codex",
                "read_only": True,
            },
        )
        maybe_checkpoint(
            "plan-revise",
            "Each side revised its plan after peer review.",
            revised_claude,
            revised_codex,
            next_note_phase="plan-consensus",
        )

        current_phase = "plan-consensus-evaluate"
        consensus_claude, consensus_codex = run_parallel_stage_pair(
            claude_kwargs={
                **common_stage_kwargs,
                "agent": "claude",
                "phase": "plan-consensus",
                "workspace": workspaces.claude,
                "shared_dirs": [run_dir],
                "prompt": build_plan_consensus_prompt(
                    "Claude Code",
                    "Codex",
                    task,
                    args.acceptance,
                    args.scope,
                    revised_claude.parsed,
                    revised_codex.parsed,
                    checkpoint_state.notes,
                ),
                "schema": CONSENSUS_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-consensus" / "claude",
                "read_only": True,
            },
            codex_kwargs={
                **common_stage_kwargs,
                "agent": "codex",
                "phase": "plan-consensus",
                "workspace": workspaces.codex,
                "shared_dirs": [run_dir],
                "prompt": build_plan_consensus_prompt(
                    "Codex",
                    "Claude Code",
                    task,
                    args.acceptance,
                    args.scope,
                    revised_codex.parsed,
                    revised_claude.parsed,
                    checkpoint_state.notes,
                ),
                "schema": CONSENSUS_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-consensus" / "codex",
                "read_only": True,
            },
        )
        maybe_checkpoint(
            "plan-consensus",
            "Consensus evaluation completed and the final plan base is ready to choose.",
            consensus_claude,
            consensus_codex,
            next_note_phase="plan-finalize",
        )

        final_plan_base = choose_final_base(consensus_claude.parsed, consensus_codex.parsed)
        merge_brief = build_merge_brief(final_plan_base, consensus_claude.parsed, consensus_codex.parsed)
        write_json(run_dir / "plan-merge-brief.json", merge_brief)
        log_progress(f"Plan consensus selected {final_plan_base} as the final plan base.")

        if final_plan_base == "claude":
            base_workspace = workspaces.claude
            reviewer_workspace = workspaces.codex
            base_revision = revised_claude
            peer_revision = revised_codex
            peer_agent_name = "Codex"
            executor_name = "Claude Code"
            reviewer_name = "Codex"
        else:
            base_workspace = workspaces.codex
            reviewer_workspace = workspaces.claude
            base_revision = revised_codex
            peer_revision = revised_claude
            peer_agent_name = "Claude Code"
            executor_name = "Codex"
            reviewer_name = "Claude Code"
        log_progress(f"Execution assignment: executor={executor_name}, reviewer={reviewer_name}.")

        current_phase = "plan-finalize"
        final_plan = run_agent_stage(
            **common_stage_kwargs,
            agent=final_plan_base,
            phase="plan-finalize",
            workspace=base_workspace,
            shared_dirs=[run_dir],
            prompt=build_final_plan_prompt(
                executor_name,
                peer_agent_name,
                task,
                args.acceptance,
                args.scope,
                merge_brief,
                base_revision.parsed,
                peer_revision.parsed,
                checkpoint_state.notes,
            ),
            schema=FINAL_PLAN_SCHEMA,
            stage_dir=run_dir / "stages" / "plan-final" / final_plan_base,
            read_only=True,
        )
        write_json(final_plan_file, final_plan.parsed)
        maybe_checkpoint(
            "plan-finalize",
            "Final execution plan is ready.",
            final_plan,
            next_note_phase="execute-initial",
        )

        current_phase = "execution"
        log_progress("Phase 2/3: Execution")
        current_phase = "execute-initial"
        current_execution = run_agent_stage(
            **common_stage_kwargs,
            agent=final_plan_base,
            phase="execute-initial",
            workspace=base_workspace,
            shared_dirs=[run_dir],
            prompt=build_execute_prompt(
                executor_name,
                task,
                args.acceptance,
                args.scope,
                final_plan.parsed,
                checkpoint_state.notes,
            ),
            schema=EXECUTION_SCHEMA,
            stage_dir=run_dir / "stages" / "execute" / final_plan_base / "round-0",
            read_only=False,
        )
        maybe_checkpoint(
            "execute-initial",
            "Initial implementation completed.",
            current_execution,
            next_note_phase="implementation-review-0",
        )

        current_phase = "implementation-review"
        log_progress("Phase 3/3: Implementation review")
        for round_idx in range(args.review_rounds + 1):
            current_phase = f"implementation-review-{round_idx}"
            implementation_review = run_agent_stage(
                **common_stage_kwargs,
                agent="codex" if final_plan_base == "claude" else "claude",
                phase=f"implementation-review-{round_idx}",
                workspace=reviewer_workspace,
                shared_dirs=[run_dir, current_execution.package_dir],
                prompt=build_execution_review_prompt(
                    reviewer_name,
                    executor_name,
                    task,
                    args.acceptance,
                    args.scope,
                    final_plan.parsed,
                    current_execution.parsed,
                    current_execution.package_dir,
                    checkpoint_state.notes,
                ),
                schema=REVIEW_SCHEMA,
                stage_dir=run_dir / "stages" / "implementation-review" / f"round-{round_idx}" / reviewer_name.lower().replace(" ", "-"),
                read_only=True,
            )
            review_next_note_phase = None
            if implementation_review.parsed.get("overall_verdict") != "approve" and round_idx < args.review_rounds:
                review_next_note_phase = f"execute-fix-{round_idx + 1}"
            maybe_checkpoint(
                f"implementation-review-{round_idx}",
                f"Implementation review round {round_idx} completed.",
                implementation_review,
                next_note_phase=review_next_note_phase,
            )

            if implementation_review.parsed.get("overall_verdict") == "approve":
                final_approved = True
                log_progress(f"Implementation approved in review round {round_idx}.")
                break

            if round_idx >= args.review_rounds:
                log_progress("Implementation review still has open issues and no more fix rounds remain.")
                break

            log_progress(
                f"Implementation review requested changes; starting fix round {round_idx + 1} of {args.review_rounds}."
            )

            current_phase = f"execute-fix-{round_idx + 1}"
            current_execution = run_agent_stage(
                **common_stage_kwargs,
                agent=final_plan_base,
                phase=f"execute-fix-{round_idx + 1}",
                workspace=base_workspace,
                shared_dirs=[run_dir, current_execution.package_dir],
                prompt=build_execution_fix_prompt(
                    executor_name,
                    task,
                    args.acceptance,
                    args.scope,
                    final_plan.parsed,
                    implementation_review.parsed,
                    checkpoint_state.notes,
                ),
                schema=EXECUTION_SCHEMA,
                stage_dir=run_dir / "stages" / "execute-fix" / final_plan_base / f"round-{round_idx + 1}",
                read_only=False,
            )
            maybe_checkpoint(
                f"execute-fix-{round_idx + 1}",
                f"Execution fix round {round_idx + 1} completed.",
                current_execution,
                next_note_phase=f"implementation-review-{round_idx + 1}",
            )

        if args.apply_final and final_approved and current_execution is not None:
            checkpoint_stages = [stage for stage in [current_execution, implementation_review] if stage is not None]
            maybe_checkpoint(
                "apply-final",
                "Final result is approved and ready to copy back into the source workspace.",
                *checkpoint_stages,
                next_note_phase=None,
            )
            current_phase = "apply-final"
            apply_final_to_source(repo, current_execution.workspace, current_execution.changed_files)
            log_progress("Applied approved final files back to the source workspace.")

        final_report = {
            "run_id": run_id,
            "repo": str(repo),
            "task": task,
            "run_dir": str(run_dir),
            "status": "completed",
            "exit_code": 0 if final_approved else 2,
            "supervised": supervise_enabled,
            "checkpoint_supervision": args.supervise_checkpoints,
            "progress_log": str(progress_log_path),
            "supervisor_log": str(supervisor_log_path) if supervisor_log_path else "",
            "checkpoint_history": checkpoint_history_value(),
            "checkpoint_events": checkpoint_state.events,
            "notes_history": notes_history_value(),
            "supervisor_notes_count": len(checkpoint_state.notes),
            "supervisor_notes": checkpoint_state.notes,
            "stage_timings": snapshot_stage_timings(),
            "final_plan_base": final_plan_base,
            "executor": final_plan_base,
            "reviewer": "codex" if final_plan_base == "claude" else "claude",
            "final_approved": final_approved,
            "final_plan_file": str(final_plan_file),
            "final_package": str(current_execution.package_dir) if current_execution else "",
            "final_changed_files": current_execution.changed_files if current_execution else [],
            "implementation_review": implementation_review.parsed if implementation_review else {},
        }
        write_json(run_dir / "report.json", final_report)
        write_text(run_dir / "report.md", markdown_report(final_report))
        log_progress(f"Run {run_id} finished; final_approved={final_approved}; report={run_dir / 'report.json'}")
        print(json.dumps(final_report, ensure_ascii=True, indent=2))
        return 0 if final_approved else 2
    except RunAborted as exc:
        final_report = {
            "run_id": run_id,
            "repo": str(repo),
            "task": task,
            "run_dir": str(run_dir),
            "status": "aborted",
            "exit_code": 130,
            "supervised": supervise_enabled,
            "checkpoint_supervision": args.supervise_checkpoints,
            "progress_log": str(progress_log_path),
            "supervisor_log": str(supervisor_log_path) if supervisor_log_path else "",
            "checkpoint_history": checkpoint_history_value(),
            "checkpoint_events": checkpoint_state.events,
            "notes_history": notes_history_value(),
            "supervisor_notes_count": len(checkpoint_state.notes),
            "supervisor_notes": checkpoint_state.notes,
            "stage_timings": snapshot_stage_timings(),
            "final_plan_base": final_plan_base,
            "executor": final_plan_base,
            "reviewer": "codex" if final_plan_base == "claude" else ("claude" if final_plan_base else ""),
            "final_approved": final_approved,
            "final_plan_file": str(final_plan_file) if final_plan_file.exists() else "",
            "final_package": str(current_execution.package_dir) if current_execution else "",
            "final_changed_files": current_execution.changed_files if current_execution else [],
            "implementation_review": implementation_review.parsed if implementation_review else {},
            "aborted_checkpoint": exc.event,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(run_dir / "report.json", final_report)
        write_text(run_dir / "report.md", markdown_report(final_report))
        log_progress(f"Run {run_id} aborted at {exc.event.get('id', 'unknown')}; report={run_dir / 'report.json'}")
        print(json.dumps(final_report, ensure_ascii=True, indent=2))
        return 130
    except Exception as exc:
        traceback_path = run_dir / "failure-traceback.txt"
        write_text(traceback_path, traceback.format_exc())
        final_report = {
            "run_id": run_id,
            "repo": str(repo),
            "task": task,
            "run_dir": str(run_dir),
            "status": "failed",
            "exit_code": 1,
            "supervised": supervise_enabled,
            "checkpoint_supervision": args.supervise_checkpoints,
            "progress_log": str(progress_log_path),
            "supervisor_log": str(supervisor_log_path) if supervisor_log_path else "",
            "checkpoint_history": checkpoint_history_value(),
            "checkpoint_events": checkpoint_state.events,
            "notes_history": notes_history_value(),
            "supervisor_notes_count": len(checkpoint_state.notes),
            "supervisor_notes": checkpoint_state.notes,
            "stage_timings": snapshot_stage_timings(),
            "failed_phase": current_phase,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_file": str(traceback_path),
        }
        write_json(run_dir / "report.json", final_report)
        write_text(run_dir / "report.md", markdown_report(final_report))
        log_progress(
            f"Run {run_id} failed in {current_phase}; report={run_dir / 'report.json'}; error={type(exc).__name__}: {exc}"
        )
        print(json.dumps(final_report, ensure_ascii=True, indent=2))
        return 1
    finally:
        try:
            if args.cleanup_workspaces and workspaces is not None:
                cleanup_workspaces(repo, workspaces)
                if not args.keep_workspaces:
                    shutil.rmtree(run_dir / "workspaces", ignore_errors=True)
        finally:
            finalize_run_state()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

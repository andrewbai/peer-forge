#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEMP_COMMIT_NAME = "peer-consensus"
TEMP_COMMIT_EMAIL = "peer-consensus@local"
SEVERITY_WEIGHTS = {
    "critical": 13,
    "high": 8,
    "medium": 5,
    "low": 2,
    "info": 0,
}


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
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    parsed_path: Path
    parsed: dict[str, Any]
    changed_files: list[str]
    diff_path: Path
    package_dir: Path


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
        "--cleanup-workspaces",
        action="store_true",
        help="Remove the temporary isolated workspaces after the run completes.",
    )
    parser.add_argument(
        "--keep-run-dir",
        action="store_true",
        help="Keep the run directory even if cleanup-workspaces is set.",
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
    if "--signoff-rounds" in sys.argv:
        print("Warning: --signoff-rounds is deprecated; use --review-rounds.", file=sys.stderr)
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
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


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


def build_plan_prompt(agent: str, task: str, acceptance: list[str], scope: list[str]) -> str:
    return textwrap.dedent(
        f"""
        You are {agent} in a dual-agent peer consensus protocol.

        This phase is plan-only. Do not modify code. Do not create commits, branches, or tags.
        Produce the best implementation plan you can, as if the other agent does not exist.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {reviewer} reviewing {peer}'s implementation plan in a dual-agent peer consensus protocol.

        Review only. Do not modify your workspace.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {agent} revising your own implementation plan after reading {peer}'s review.

        This is still a plan-only phase. Do not modify code. Do not create commits, branches, or tags.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {agent} deciding which revised implementation plan should become the final plan base.

        Do not modify your workspace.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {base_agent}. Your revised plan is the starting point for the final implementation plan.

        This is still a plan-only phase. Do not modify code. Produce the final agreed implementation plan.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {agent} executing the agreed final plan.

        This is the code-writing phase. Modify code only inside this isolated workspace.
        Do not create commits, branches, or tags.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {reviewer} reviewing {executor}'s implementation against the agreed final plan.

        Review only. Do not modify your workspace.

        {prompt_header(task, acceptance, scope)}

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
) -> str:
    return textwrap.dedent(
        f"""
        You are {agent} updating the implementation after peer review.

        This is still the execution phase. Modify code only inside this isolated workspace.
        Do not create commits, branches, or tags.

        {prompt_header(task, acceptance, scope)}

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
    workspace: Path,
    shared_dirs: list[Path],
    schema: dict[str, Any],
    output_dir: Path,
    model: str | None,
    bare: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
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
    proc = run_cmd(cmd, cwd=workspace, check=False)
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
        "parsed_path": parsed_path,
        "parsed": parsed,
    }


def run_codex(
    prompt: str,
    workspace: Path,
    shared_dirs: list[Path],
    schema: dict[str, Any],
    output_dir: Path,
    model: str | None,
    writable: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
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
    proc = run_cmd(cmd, cwd=workspace, input_text=prompt, check=False)
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
    read_only: bool,
) -> StageRun:
    before_status = ""
    before_diff = ""
    before_head = workspace_head(workspace, git_mode)
    if read_only:
        before_status, before_diff = snapshot_workspace_state(workspace, git_mode)
    if agent == "claude":
        result = run_claude(prompt, workspace, shared_dirs, schema, stage_dir, claude_model, claude_bare)
    elif agent == "codex":
        result = run_codex(prompt, workspace, shared_dirs, schema, stage_dir, codex_model, not read_only)
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
    return StageRun(
        agent=agent,
        phase=phase,
        workspace=workspace,
        prompt_path=result["prompt_path"],
        stdout_path=result["stdout_path"],
        stderr_path=result["stderr_path"],
        parsed_path=result["parsed_path"],
        parsed=result["parsed"],
        changed_files=changed_files,
        diff_path=diff_path,
        package_dir=package_dir,
    )


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
    lines = [
        f"# Peer Consensus Run {data['run_id']}",
        "",
        f"- Repo: `{data['repo']}`",
        f"- Task: {data['task'].strip()}",
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
    lines.extend(
        [
            "",
            "## Implementation Review",
            f"- Verdict: `{data['implementation_review']['overall_verdict']}`",
            f"- Summary: {data['implementation_review']['summary']}",
            "",
            "## Artifacts",
            f"- Run dir: `{data['run_dir']}`",
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
    run_root = Path(args.run_root).resolve() if args.run_root else repo / ".claude" / "tmp" / "peer-consensus"
    run_id = f"{utc_now()}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
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
            "apply_final": args.apply_final,
        },
    )

    workspaces = prepare_workspaces(repo, run_dir, args.include_path)
    final_report: dict[str, Any] = {}
    common_stage_kwargs = {
        "baseline": workspaces.baseline,
        "git_mode": workspaces.git_mode,
        "claude_model": args.claude_model,
        "codex_model": args.codex_model,
        "claude_bare": not args.no_claude_bare,
    }

    try:
        initial_claude, initial_codex = run_parallel_stage_pair(
            claude_kwargs={
                **common_stage_kwargs,
                "agent": "claude",
                "phase": "plan-initial",
                "workspace": workspaces.claude,
                "shared_dirs": [run_dir],
                "prompt": build_plan_prompt("Claude Code", task, args.acceptance, args.scope),
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
                "prompt": build_plan_prompt("Codex", task, args.acceptance, args.scope),
                "schema": PLAN_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-initial" / "codex",
                "read_only": True,
            },
        )

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
                ),
                "schema": REVIEW_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-review" / "codex-on-claude",
                "read_only": True,
            },
        )

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
                ),
                "schema": PLAN_REVISION_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-revision" / "codex",
                "read_only": True,
            },
        )

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
                ),
                "schema": CONSENSUS_SCHEMA,
                "stage_dir": run_dir / "stages" / "plan-consensus" / "codex",
                "read_only": True,
            },
        )

        final_plan_base = choose_final_base(consensus_claude.parsed, consensus_codex.parsed)
        merge_brief = build_merge_brief(final_plan_base, consensus_claude.parsed, consensus_codex.parsed)
        write_json(run_dir / "plan-merge-brief.json", merge_brief)

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
            ),
            schema=FINAL_PLAN_SCHEMA,
            stage_dir=run_dir / "stages" / "plan-final" / final_plan_base,
            read_only=True,
        )
        final_plan_file = run_dir / "final-plan.json"
        write_json(final_plan_file, final_plan.parsed)

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
            ),
            schema=EXECUTION_SCHEMA,
            stage_dir=run_dir / "stages" / "execute" / final_plan_base / "round-0",
            read_only=False,
        )

        implementation_review: StageRun | None = None
        final_approved = False

        for round_idx in range(args.review_rounds + 1):
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
                ),
                schema=REVIEW_SCHEMA,
                stage_dir=run_dir / "stages" / "implementation-review" / f"round-{round_idx}" / reviewer_name.lower().replace(" ", "-"),
                read_only=True,
            )

            if implementation_review.parsed.get("overall_verdict") == "approve":
                final_approved = True
                break

            if round_idx >= args.review_rounds:
                break

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
                ),
                schema=EXECUTION_SCHEMA,
                stage_dir=run_dir / "stages" / "execute-fix" / final_plan_base / f"round-{round_idx + 1}",
                read_only=False,
            )

        if args.apply_final and final_approved:
            apply_final_to_source(repo, current_execution.workspace, current_execution.changed_files)

        final_report = {
            "run_id": run_id,
            "repo": str(repo),
            "task": task,
            "run_dir": str(run_dir),
            "final_plan_base": final_plan_base,
            "executor": final_plan_base,
            "reviewer": "codex" if final_plan_base == "claude" else "claude",
            "final_approved": final_approved,
            "final_plan_file": str(final_plan_file),
            "final_package": str(current_execution.package_dir),
            "final_changed_files": current_execution.changed_files,
            "implementation_review": implementation_review.parsed if implementation_review else {},
        }
        write_json(run_dir / "report.json", final_report)
        write_text(run_dir / "report.md", markdown_report(final_report))
        print(json.dumps(final_report, ensure_ascii=True, indent=2))
        return 0 if final_approved else 2
    finally:
        if args.cleanup_workspaces:
            cleanup_workspaces(repo, workspaces)
            if not args.keep_run_dir:
                shutil.rmtree(run_dir / "workspaces", ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

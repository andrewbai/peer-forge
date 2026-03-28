from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess


class TmuxError(RuntimeError):
    pass


def ensure_tmux() -> None:
    if shutil.which("tmux"):
        return
    raise SystemExit("Required binary not found in PATH: tmux")


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def run_tmux(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["tmux", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)} failed ({proc.returncode}): {proc.stderr or proc.stdout}")
    return proc


def first_pane_id(target: str) -> str:
    proc = run_tmux(["list-panes", "-t", target, "-F", "#{pane_id}"])
    pane_ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not pane_ids:
        raise TmuxError(f"tmux did not return any panes for target {target!r}.")
    return pane_ids[0]


def has_session(session_name: str) -> bool:
    return run_tmux(["has-session", "-t", session_name], check=False).returncode == 0


def kill_session(session_name: str) -> None:
    run_tmux(["kill-session", "-t", session_name], check=False)


def new_session(session_name: str, *, cwd: Path, command: list[str] | None = None, shell: str = "/bin/zsh") -> str:
    tmux_args = [
        "new-session",
        "-d",
        "-s",
        session_name,
        "-c",
        str(cwd),
        "-P",
        "-F",
        "#{pane_id}",
    ]
    if command is None:
        tmux_args.append(shell)
    else:
        tmux_args.extend(command)
    proc = run_tmux(tmux_args)
    pane_id = proc.stdout.strip()
    if not pane_id:
        pane_id = first_pane_id(session_name)
    return pane_id


def split_window(
    target_pane: str,
    *,
    cwd: Path,
    direction: str,
    command: list[str] | None = None,
    shell: str = "/bin/zsh",
) -> str:
    flag = "-h" if direction == "horizontal" else "-v"
    tmux_args = [
        "split-window",
        "-d",
        flag,
        "-t",
        target_pane,
        "-c",
        str(cwd),
        "-P",
        "-F",
        "#{pane_id}",
    ]
    if command is None:
        tmux_args.append(shell)
    else:
        tmux_args.extend(command)
    proc = run_tmux(tmux_args)
    pane_id = proc.stdout.strip()
    if not pane_id:
        pane_id = first_pane_id(target_pane)
    return pane_id


def set_remain_on_exit(session_name: str, *, enabled: bool = True) -> None:
    run_tmux(["set-option", "-t", session_name, "remain-on-exit", "on" if enabled else "off"])


def select_layout(target: str, layout: str) -> None:
    run_tmux(["select-layout", "-t", target, layout])


def set_pane_title(pane_id: str, title: str) -> None:
    run_tmux(["select-pane", "-t", pane_id, "-T", title])


def pipe_pane(pane_id: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = f"cat >> {shlex.quote(str(log_path))}"
    run_tmux(["pipe-pane", "-o", "-t", pane_id, command])


def respawn_pane(pane_id: str, *, cwd: Path, command: list[str]) -> None:
    run_tmux(
        [
            "respawn-pane",
            "-k",
            "-t",
            pane_id,
            "-c",
            str(cwd),
            *command,
        ]
    )


def send_shell_command(pane_id: str, command: str) -> None:
    run_tmux(["send-keys", "-t", pane_id, command, "Enter"])


def paste_message(pane_id: str, text: str) -> None:
    buffer_name = f"peer-forge-{os.getpid()}-{abs(hash((pane_id, text))) % 1000000}"
    try:
        run_tmux(["set-buffer", "-b", buffer_name, text])
        run_tmux(["paste-buffer", "-d", "-b", buffer_name, "-t", pane_id])
    finally:
        run_tmux(["delete-buffer", "-b", buffer_name], check=False)
    run_tmux(["send-keys", "-t", pane_id, "Enter"])


def capture_pane(pane_id: str, *, lines: int = 200) -> str:
    start = f"-{max(lines, 1)}"
    proc = run_tmux(["capture-pane", "-p", "-t", pane_id, "-S", start], check=False)
    return proc.stdout


def attach_session(session_name: str) -> None:
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])

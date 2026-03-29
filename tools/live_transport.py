from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from live_state import pane_by_title, prompt_file_message
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


class LiveTransport(Protocol):
    def send_prompt(self, pane_id: str, text: str) -> None:
        ...

    def capture_recent(self, pane_id: str, *, lines: int = 200) -> str:
        ...


class TmuxTransport:
    CLAUDE_TITLE = "peer-forge-live:claude"
    CODEX_TITLE = "peer-forge-live:codex"
    SUPERVISOR_TITLE = "peer-forge-live:supervisor"

    def ensure_available(self) -> None:
        ensure_tmux()

    def has_session(self, session_name: str) -> bool:
        return has_session(session_name)

    def kill_session(self, session_name: str) -> None:
        kill_session(session_name)

    def send_prompt(self, pane_id: str, text: str) -> None:
        paste_message(pane_id, text)

    def capture_recent(self, pane_id: str, *, lines: int = 200) -> str:
        return capture_pane(pane_id, lines=lines)

    def respawn(self, pane_id: str, *, cwd: Path, command: list[str]) -> None:
        respawn_pane(pane_id, cwd=cwd, command=command)

    def attach(self, session_name: str) -> None:
        attach_session(session_name)

    def create_session_layout(
        self,
        *,
        session_name: str,
        claude_cwd: Path,
        codex_cwd: Path,
        supervisor_cwd: Path,
        logs: dict[str, str],
    ) -> dict[str, str]:
        claude_pane = new_session(
            session_name,
            cwd=claude_cwd,
            command=placeholder_command(),
        )
        codex_pane = split_window(
            claude_pane,
            cwd=codex_cwd,
            direction="horizontal",
            command=placeholder_command(),
        )
        supervisor_pane = split_window(
            claude_pane,
            cwd=supervisor_cwd,
            direction="vertical",
            command=placeholder_command(),
        )
        set_remain_on_exit(session_name, enabled=True)
        select_layout(session_name, "tiled")
        set_pane_title(claude_pane, self.CLAUDE_TITLE)
        set_pane_title(codex_pane, self.CODEX_TITLE)
        set_pane_title(supervisor_pane, self.SUPERVISOR_TITLE)
        pipe_pane(claude_pane, Path(logs["claude_raw"]))
        pipe_pane(codex_pane, Path(logs["codex_raw"]))
        pipe_pane(supervisor_pane, Path(logs["supervisor_raw"]))
        return {
            "claude": claude_pane,
            "codex": codex_pane,
            "supervisor": supervisor_pane,
        }

    def repair_or_create_supervisor(
        self,
        *,
        session_name: str,
        state: dict[str, Any],
        run_dir: Path,
        state_file: Path,
    ) -> tuple[str, str]:
        panes = list_panes(session_name)
        claude_pane = pane_by_title(panes, self.CLAUDE_TITLE)
        codex_pane = pane_by_title(panes, self.CODEX_TITLE)
        supervisor_pane = pane_by_title(panes, self.SUPERVISOR_TITLE)
        for agent, pane in (("claude", claude_pane), ("codex", codex_pane)):
            if pane is None:
                raise SystemExit(f"{agent} pane is missing in tmux session {session_name}; live resume cannot repair agent panes.")
            if pane.get("pane_dead") == "1":
                raise SystemExit(
                    f"{agent} pane {pane['pane_id']} is dead in tmux session {session_name}; live resume cannot repair agent panes."
                )
            state["agents"][agent]["pane_id"] = pane["pane_id"]

        supervisor_action = "supervisor-resumed"
        if supervisor_pane is None:
            supervisor_pane_id = split_window(
                claude_pane["pane_id"],
                cwd=run_dir,
                direction="vertical",
                command=placeholder_command(),
            )
            set_pane_title(supervisor_pane_id, self.SUPERVISOR_TITLE)
            pipe_pane(supervisor_pane_id, Path(state["logs"]["supervisor_raw"]))
            respawn_pane(
                supervisor_pane_id,
                cwd=run_dir,
                command=build_supervisor_command(state_file),
            )
            supervisor_action = "supervisor-created"
        else:
            supervisor_pane_id = supervisor_pane["pane_id"]
            set_pane_title(supervisor_pane_id, self.SUPERVISOR_TITLE)
            if supervisor_pane.get("pane_dead") == "1":
                pipe_pane(supervisor_pane_id, Path(state["logs"]["supervisor_raw"]))
                respawn_pane(
                    supervisor_pane_id,
                    cwd=run_dir,
                    command=build_supervisor_command(state_file),
                )
                supervisor_action = "supervisor-respawned"

        select_layout(session_name, "tiled")
        return supervisor_pane_id, supervisor_action


def build_claude_command(
    *,
    model: str | None,
    bare: bool,
    prompt_path: Path,
) -> list[str]:
    cmd = [
        "claude",
        "--permission-mode",
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
    peer_live = Path(__file__).resolve().parent / "peer_live.py"
    return ["python3", str(peer_live), "serve", "--state-file", str(state_file)]


def placeholder_command() -> list[str]:
    return ["sleep", "3600"]

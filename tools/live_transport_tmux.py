from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from live_state import pane_by_title, raw_log_path, read_bytes_from, record_agent_output, sanitize_terminal_text
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
from live_transport import build_supervisor_command, placeholder_command


class TmuxTransport:
    CLAUDE_TITLE = "peer-forge-live:claude"
    CODEX_TITLE = "peer-forge-live:codex"
    SUPERVISOR_TITLE = "peer-forge-live:supervisor"

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def ensure_available(self) -> None:
        ensure_tmux()

    def has_session(self, session_name: str) -> bool:
        return has_session(session_name)

    def kill_session(self, session_name: str) -> None:
        kill_session(session_name)

    async def send_prompt(self, agent: str, text: str) -> None:
        await asyncio.to_thread(paste_message, self.state["agents"][agent]["pane_id"], text)

    def _refresh_stream(self, agent: str) -> None:
        agent_state = self.state["agents"][agent]
        data, new_offset = read_bytes_from(raw_log_path(self.state, agent), int(agent_state.get("stream_offset", 0)))
        agent_state["stream_offset"] = new_offset
        if data:
            record_agent_output(self.state, agent, data.decode("utf-8", errors="replace"))

    def _read_output_since_sync(self, agent: str, offset: int) -> tuple[str, int]:
        self._refresh_stream(agent)
        data, new_offset = read_bytes_from(raw_log_path(self.state, agent), offset)
        if not data:
            return "", new_offset
        return sanitize_terminal_text(data.decode("utf-8", errors="replace")), new_offset

    async def read_output_since(self, agent: str, offset: int) -> tuple[str, int]:
        return await asyncio.to_thread(self._read_output_since_sync, agent, offset)

    async def capture_recent(self, agent: str, *, lines: int = 200) -> str:
        return await asyncio.to_thread(capture_pane, self.state["agents"][agent]["pane_id"], lines=lines)

    def describe_agent(self, agent: str) -> str:
        pane_id = str(self.state["agents"][agent].get("pane_id", "") or "")
        return pane_id or "pane=missing"

    def _output_size_sync(self, agent: str) -> int:
        path = raw_log_path(self.state, agent)
        return path.stat().st_size if path.exists() else 0

    async def output_size(self, agent: str) -> int:
        return await asyncio.to_thread(self._output_size_sync, agent)

    async def shutdown(self) -> None:
        return

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
        run_dir: Path,
        state_file: Path,
    ) -> tuple[str, str]:
        panes = list_panes(session_name)
        panes_by_id = {pane["pane_id"]: pane for pane in panes}

        def resolve_pane(agent: str, title: str) -> dict[str, str] | None:
            pane_id = str(self.state["agents"].get(agent, {}).get("pane_id", "") or "")
            if pane_id and pane_id in panes_by_id:
                return panes_by_id[pane_id]
            return pane_by_title(panes, title)

        claude_pane = resolve_pane("claude", self.CLAUDE_TITLE)
        codex_pane = resolve_pane("codex", self.CODEX_TITLE)
        supervisor_pane = resolve_pane("supervisor", self.SUPERVISOR_TITLE)
        for agent, pane in (("claude", claude_pane), ("codex", codex_pane)):
            if pane is None:
                raise SystemExit(f"{agent} pane is missing in tmux session {session_name}; live resume cannot repair agent panes.")
            if pane.get("pane_dead") == "1":
                raise SystemExit(
                    f"{agent} pane {pane['pane_id']} is dead in tmux session {session_name}; live resume cannot repair agent panes."
                )
            self.state["agents"][agent]["pane_id"] = pane["pane_id"]
            self.state["agents"][agent]["transport_ref"] = pane["pane_id"]
            self.state["agents"][agent]["transport_kind"] = "tmux"
            set_pane_title(pane["pane_id"], self.CLAUDE_TITLE if agent == "claude" else self.CODEX_TITLE)

        supervisor_action = "supervisor-resumed"
        if supervisor_pane is None:
            supervisor_pane_id = split_window(
                claude_pane["pane_id"],
                cwd=run_dir,
                direction="vertical",
                command=placeholder_command(),
            )
            set_pane_title(supervisor_pane_id, self.SUPERVISOR_TITLE)
            pipe_pane(supervisor_pane_id, Path(self.state["logs"]["supervisor_raw"]))
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
                pipe_pane(supervisor_pane_id, Path(self.state["logs"]["supervisor_raw"]))
                respawn_pane(
                    supervisor_pane_id,
                    cwd=run_dir,
                    command=build_supervisor_command(state_file),
                )
                supervisor_action = "supervisor-respawned"

        select_layout(session_name, "tiled")
        return supervisor_pane_id, supervisor_action

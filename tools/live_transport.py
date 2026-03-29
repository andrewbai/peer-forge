from __future__ import annotations

from pathlib import Path
from typing import Protocol

from live_state import prompt_file_message


class LiveTransport(Protocol):
    async def send_prompt(self, agent: str, text: str) -> None:
        ...

    async def read_output_since(self, agent: str, offset: int) -> tuple[str, int]:
        ...

    async def capture_recent(self, agent: str, *, lines: int = 200) -> str:
        ...

    def describe_agent(self, agent: str) -> str:
        ...

    async def output_size(self, agent: str) -> int:
        ...

    async def shutdown(self) -> None:
        ...


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

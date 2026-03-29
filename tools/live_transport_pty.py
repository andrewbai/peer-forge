from __future__ import annotations

import asyncio
import codecs
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from live_state import append_text, raw_log_path, read_file_tail, read_bytes_from, record_agent_output


class PtyTransport:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.master_fds: dict[str, int] = {}
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.decoders: dict[str, codecs.IncrementalDecoder] = {}

    def ensure_available(self) -> None:
        return

    def start_agent(self, agent: str, *, cwd: Path, command: list[str]) -> None:
        master_fd, slave_fd = os.openpty()
        os.set_blocking(master_fd, False)
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            os.close(slave_fd)
            os.close(master_fd)
            raise
        os.close(slave_fd)
        self.master_fds[agent] = master_fd
        self.processes[agent] = proc
        self.decoders[agent] = codecs.getincrementaldecoder("utf-8")("replace")
        self.state["agents"][agent]["pane_id"] = ""
        self.state["agents"][agent]["transport_kind"] = "pty"
        self.state["agents"][agent]["transport_ref"] = str(proc.pid)

    def _send_prompt_sync(self, agent: str, text: str) -> None:
        master_fd = self.master_fds[agent]
        os.write(master_fd, text.encode("utf-8", errors="replace"))
        os.write(master_fd, b"\n")

    async def send_prompt(self, agent: str, text: str) -> None:
        await asyncio.to_thread(self._send_prompt_sync, agent, text)

    def _pump_agent_output(self, agent: str) -> None:
        master_fd = self.master_fds.get(agent)
        if master_fd is None:
            return
        while True:
            try:
                chunk = os.read(master_fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                decoder = self.decoders.get(agent)
                if decoder is not None:
                    text = record_agent_output(self.state, agent, decoder.decode(b"", final=True))
                    if text:
                        append_text(raw_log_path(self.state, agent), text)
                break
            decoder = self.decoders.get(agent)
            decoded = chunk.decode("utf-8", errors="replace") if decoder is None else decoder.decode(chunk)
            text = record_agent_output(self.state, agent, decoded)
            if text:
                append_text(raw_log_path(self.state, agent), text)

    def _read_output_since_sync(self, agent: str, offset: int) -> tuple[str, int]:
        self._pump_agent_output(agent)
        data, new_offset = read_bytes_from(raw_log_path(self.state, agent), offset)
        if not data:
            return "", new_offset
        return data.decode("utf-8", errors="replace"), new_offset

    async def read_output_since(self, agent: str, offset: int) -> tuple[str, int]:
        return await asyncio.to_thread(self._read_output_since_sync, agent, offset)

    def _capture_recent_sync(self, agent: str, *, lines: int = 200) -> str:
        self._pump_agent_output(agent)
        return read_file_tail(raw_log_path(self.state, agent), lines=lines)

    async def capture_recent(self, agent: str, *, lines: int = 200) -> str:
        return await asyncio.to_thread(self._capture_recent_sync, agent, lines=lines)

    def describe_agent(self, agent: str) -> str:
        proc = self.processes.get(agent)
        if proc is None:
            return "pty=missing"
        status = "running" if proc.poll() is None else f"exit={proc.returncode}"
        return f"pid={proc.pid} {status}"

    def _output_size_sync(self, agent: str) -> int:
        self._pump_agent_output(agent)
        path = raw_log_path(self.state, agent)
        return path.stat().st_size if path.exists() else 0

    async def output_size(self, agent: str) -> int:
        return await asyncio.to_thread(self._output_size_sync, agent)

    def _shutdown_sync(self) -> None:
        for proc in self.processes.values():
            if proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except OSError:
                    pass
        for proc in self.processes.values():
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        for master_fd in self.master_fds.values():
            try:
                os.close(master_fd)
            except OSError:
                pass
        self.decoders.clear()
        self.master_fds.clear()
        self.processes.clear()

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._shutdown_sync)

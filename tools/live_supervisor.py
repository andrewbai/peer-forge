from __future__ import annotations

import asyncio
import json
from pathlib import Path
import queue
import sys
import threading
import uuid
from typing import Any, TextIO

from peer_consensus import clip_text, utc_timestamp_precise, write_json

from live_state import (
    AGENTS,
    allowed_supervisor_commands,
    agent_runtime_ref,
    clear_boundary_state,
    current_execution_package,
    current_final_plan_path,
    current_turn,
    next_note_id,
    process_runtime_state,
    package_diff_path,
    package_manifest_path,
    read_file_tail,
    read_text_preview,
    sanitize_terminal_text,
    save_state,
    supervisor_log_line,
    write_supervisor_event,
)


class QueueSupervisor:
    def __init__(
        self,
        state: dict[str, Any],
        transport: Any,
        *,
        output_stream: TextIO | None = None,
    ) -> None:
        self.state = state
        self.transport = transport
        self.output_stream = output_stream or sys.stdout
        self.command_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def start(self) -> None:
        return

    def shutdown(self) -> None:
        return

    def submit_command(
        self,
        raw_command: str,
        *,
        source: str = "external",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "request_id": request_id or f"cmd-{uuid.uuid4().hex[:10]}",
            "source": source,
            "raw_command": raw_command,
            "queued_at": utc_timestamp_precise(),
        }
        self.command_queue.put(item)
        write_supervisor_event(
            self.state,
            {
                "type": "command-enqueued",
                "timestamp": item["queued_at"],
                "request_id": item["request_id"],
                "source": item["source"],
                "raw_command": str(raw_command),
            },
        )
        return item

    def log(self, message: str) -> None:
        supervisor_log_line(self.state, message)

    def emit_lines(self, lines: list[str]) -> None:
        for line in lines:
            print(line, file=self.output_stream, flush=True)

    def create_note(
        self,
        *,
        text: str,
        applies_from_turn: int,
        applies_from_phase: str,
    ) -> dict[str, Any]:
        note_id = next_note_id(self.state)
        note = {
            "id": note_id,
            "created_at": utc_timestamp_precise(),
            "text": text.strip(),
            "summary": clip_text(text.strip(), limit=120),
            "applies_from_turn": applies_from_turn,
            "applies_from_phase": applies_from_phase,
            "record_file": str(Path(self.state["run_dir"]) / "notes" / f"{note_id}.json"),
        }
        write_json(Path(note["record_file"]), note)
        self.state["notes"].append(note)
        save_state(self.state)
        write_supervisor_event(
            self.state,
            {
                "type": "note-added",
                "timestamp": utc_timestamp_precise(),
                "note": note,
            },
        )
        return note

    def status_lines(self) -> list[str]:
        turn = current_turn(self.state)
        package = current_execution_package(self.state)
        summary = self.state.get("summary", {})
        lines = [
            f"Run: {self.state['run_id']}",
            f"Session: {self.state['session_name']}",
            f"Transport: {self.state.get('runtime', {}).get('transport', 'n/a')}",
            f"Phase: {turn['phase']}",
            f"Turn: {turn['id']}",
            f"Status: {self.state['status']}",
            f"Executor: {self.state.get('selected_executor', '') or 'n/a'}",
            f"Reviewer: {self.state.get('selected_reviewer', '') or 'n/a'}",
            f"Plan approved: {summary.get('plan_approved', False)}",
            f"Execution approved: {summary.get('execution_approved', False)}",
            f"Read-only violations: {len(self.state.get('read_only_violations', []))}",
            f"Notes queued/active: {len(self.state['notes'])}",
        ]
        control = self.state.get("runtime", {}).get("control", {})
        if control.get("base_url"):
            lines.append(f"Control API: {control['base_url']}")
        if control.get("web_url"):
            lines.append(f"Web UI: {control['web_url']}")
        process = process_runtime_state(self.state)
        lines.append(
            "Owner process: "
            f"mode={process.get('mode', 'n/a')}, "
            f"pid={process.get('owner_pid', 0) or 'n/a'}, "
            f"alive={process.get('owner_alive', False)}, "
            f"exit={process.get('owner_exit_code')}",
        )
        boundary = self.state.get("runtime", {}).get("boundary", {})
        if boundary.get("active"):
            lines.append(
                f"Boundary: active label={boundary.get('label', '')!r} next_phase={boundary.get('next_phase', '') or 'n/a'}",
            )
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
            agent_state = self.state["agents"][agent]
            runtime_detail = self.transport.describe_agent(agent) or agent_runtime_ref(self.state, agent)
            detail = (
                f"{agent}: status={turn_agent['status']}, "
                f"runtime={runtime_detail}, "
                f"mode={'read-only' if turn_agent['read_only'] else 'write'}, "
                f"last_activity={agent_state.get('last_activity_at', '') or 'n/a'}, "
                f"nudges={turn_agent.get('nudge_count', 0)}"
            )
            if turn_agent.get("parse_error"):
                detail += f", parse_error={clip_text(turn_agent['parse_error'], limit=100)}"
            lines.append(detail)
        return lines

    def show_final_plan_lines(self) -> list[str]:
        path = current_final_plan_path(self.state)
        return [
            f"Final plan path: {path}",
            "",
            read_text_preview(path),
        ]

    def show_package_lines(self) -> list[str]:
        package = current_execution_package(self.state)
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

    def show_manifest_lines(self) -> list[str]:
        package = current_execution_package(self.state)
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

    def show_diff_lines(self) -> list[str]:
        package = current_execution_package(self.state)
        if not package:
            return ["No current execution package is available yet."]
        path = package_diff_path(package)
        return [
            f"Diff path: {path}",
            "",
            read_text_preview(path),
        ]

    def tail_agent_payload(self, agent: str, *, lines: int = 60) -> dict[str, Any]:
        if agent not in AGENTS:
            raise ValueError(f"Unknown agent: {agent}")
        turn = current_turn(self.state)
        turn_agent = turn["agents"][agent]
        turn_log_path = str(turn_agent.get("turn_log_path", "") or "")
        path = Path(turn_log_path) if turn_log_path else None
        return {
            "agent": agent,
            "turn_id": turn["id"],
            "phase": turn["phase"],
            "active": bool(turn_agent.get("active", False)),
            "path": str(path) if path is not None else "",
            "tail": read_file_tail(path, lines=lines) if path is not None else "(inactive)",
            "lines": lines,
        }

    async def inspect_agent_payload(self, agent: str) -> dict[str, Any]:
        turn = current_turn(self.state)
        turn_agent = turn["agents"][agent]
        agent_state = self.state["agents"][agent]
        turn_log_path = str(turn_agent.get("turn_log_path", "") or "")
        path = Path(turn_log_path) if turn_log_path else None
        pane_capture = await self.transport.capture_recent(agent, lines=80)
        return {
            "agent": agent,
            "runtime": self.transport.describe_agent(agent) or agent_runtime_ref(self.state, agent),
            "workspace": agent_state["workspace"],
            "prompt_path": turn_agent.get("prompt_path", "") or "",
            "session_prompt_path": turn_agent.get("session_prompt_path", "") or "",
            "raw_log_path": agent_state["raw_log_path"],
            "turn_log_path": turn_log_path,
            "result_path": turn_agent.get("result_path", "") or "",
            "status": turn_agent["status"],
            "active": bool(turn_agent.get("active", False)),
            "parse_error": turn_agent.get("parse_error", "") or "",
            "result": turn_agent.get("result"),
            "turn_log_tail": read_file_tail(path, lines=40) if path is not None else "(inactive)",
            "pane_capture": sanitize_terminal_text(pane_capture).strip() or "(empty)",
            "phase": turn["phase"],
            "turn_id": turn["id"],
        }

    async def inspect_agent(self, agent: str) -> list[str]:
        payload = await self.inspect_agent_payload(agent)
        lines = [
            f"Agent: {payload['agent']}",
            f"Runtime: {payload['runtime']}",
            f"Workspace: {payload['workspace']}",
            f"Prompt: {payload['prompt_path'] or '(none)'}",
            f"Session prompt: {payload['session_prompt_path'] or '(none)'}",
            f"Raw log: {payload['raw_log_path']}",
            f"Turn log: {payload['turn_log_path'] or '(none)'}",
            f"Result file: {payload['result_path'] or '(none)'}",
            f"Status: {payload['status']}",
        ]
        if payload["parse_error"]:
            lines.append(f"Parse error: {payload['parse_error']}")
        if payload["result"]:
            lines.append("Parsed result:")
            lines.append(json.dumps(payload["result"], indent=2, ensure_ascii=True))
        lines.extend(
            [
                "",
                "Recent turn log:",
                payload["turn_log_tail"],
                "",
                "Recent pane capture:",
                payload["pane_capture"],
            ]
        )
        return lines

    def command_schema(self) -> dict[str, list[str]]:
        return {
            "running": allowed_supervisor_commands("running", next_phase="future-phase"),
            "boundary": allowed_supervisor_commands("boundary", next_phase="future-phase"),
        }

    async def handle_command(
        self,
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
                self.log("Commands: " + ", ".join(allowed_supervisor_commands("boundary", next_phase=next_phase)))
            else:
                self.log("Commands while running: " + ", ".join(allowed_supervisor_commands("running", next_phase=next_phase)))
            return None
        if lower == "status":
            for line in self.status_lines():
                self.log(line)
            return None
        if lower.startswith("tail "):
            _, _, agent_name = lower.partition(" ")
            if agent_name not in AGENTS:
                self.log("Usage: tail claude|codex")
                return None
            turn = current_turn(self.state)
            path = Path(turn["agents"][agent_name]["turn_log_path"])
            self.log(f"Tail for {agent_name}:")
            self.emit_lines([read_file_tail(path, lines=60)])
            return None
        if lower.startswith("inspect "):
            _, _, agent_name = lower.partition(" ")
            if agent_name not in AGENTS:
                self.log("Usage: inspect claude|codex")
                return None
            self.emit_lines(await self.inspect_agent(agent_name))
            return None
        if lower == "show final-plan":
            self.emit_lines(self.show_final_plan_lines())
            return None
        if lower == "show package":
            self.emit_lines(self.show_package_lines())
            return None
        if lower == "show diff":
            self.emit_lines(self.show_diff_lines())
            return None
        if lower == "show manifest":
            self.emit_lines(self.show_manifest_lines())
            return None
        if lower.startswith("note both"):
            if next_phase is None:
                self.log("No later phase remains, so no new symmetric note can be queued.")
                return None
            inline_text = command[len("note both") :].strip()
            if not inline_text:
                self.log("Usage: note both <text>. In CLI mode, plain 'note both' opens multiline capture until ---.")
                return None
            note = self.create_note(
                text=inline_text,
                applies_from_turn=len(self.state["turns"]) + 1,
                applies_from_phase=next_phase,
            )
            self.log(f"Queued {note['id']} for {next_phase}: {note['summary']}")
            return None
        if lower == "wait":
            self.log("Continuing to watch the current turn.")
            return None
        if lower == "continue":
            if mode != "boundary":
                self.log("The current turn is still running. Use wait or keep watching the panes.")
                return None
            clear_boundary_state(self.state, resolution="continue")
            return "continue"
        if lower == "abort":
            clear_boundary_state(self.state, resolution="abort")
            return "abort"
        self.log(f"Unknown command: {command}")
        return None

    def emit_boundary_prompt(self) -> None:
        return

    def _get_command_sync(self, *, timeout: float | None) -> dict[str, Any] | None:
        try:
            if timeout is None:
                return self.command_queue.get()
            return self.command_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    async def _next_command(self, *, timeout: float | None) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_command_sync, timeout=timeout)

    async def _consume_command(
        self,
        *,
        mode: str,
        next_phase: str | None,
        item: dict[str, Any],
    ) -> str | None:
        action = await self.handle_command(
            mode=mode,
            next_phase=next_phase,
            raw_command=str(item.get("raw_command", "")),
        )
        write_supervisor_event(
            self.state,
            {
                "type": "command-processed",
                "timestamp": utc_timestamp_precise(),
                "request_id": item.get("request_id", ""),
                "source": item.get("source", ""),
                "mode": mode,
                "raw_command": str(item.get("raw_command", "")),
                "action": action or "",
            },
        )
        return action

    async def poll_running_command(self, *, timeout: float, next_phase: str | None) -> str | None:
        item = await self._next_command(timeout=timeout)
        if item is None:
            return None
        return await self._consume_command(mode="running", next_phase=next_phase, item=item)

    async def pause_for_boundary(self, *, label: str, next_phase: str | None) -> None:
        self.log(label)
        if next_phase is None:
            self.log("No later phase remains.")
        else:
            self.log(f"Next phase: {next_phase}")
        self.log("Boundary commands: " + ", ".join(allowed_supervisor_commands("boundary", next_phase=next_phase)))
        while True:
            self.emit_boundary_prompt()
            item = await self._next_command(timeout=None)
            if item is None:
                continue
            action = await self._consume_command(mode="boundary", next_phase=next_phase, item=item)
            if action == "continue":
                return
            if action == "abort":
                raise KeyboardInterrupt("Supervisor aborted the live run.")


class CliSupervisor(QueueSupervisor):
    def __init__(
        self,
        state: dict[str, Any],
        transport: Any,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        super().__init__(state, transport, output_stream=output_stream)
        self.input_stream = input_stream or sys.stdin
        self._stop_event = threading.Event()
        self._input_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._input_thread and self._input_thread.is_alive():
            return
        self._stop_event.clear()
        self._input_thread = threading.Thread(
            target=self._stdin_loop,
            name="peer-forge-live-stdin",
            daemon=True,
        )
        self._input_thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()

    def emit_boundary_prompt(self) -> None:
        print("> ", end="", file=self.output_stream, flush=True)

    def _read_multiline_note(self) -> str | None:
        print(
            "Enter symmetric note text. End with a line containing only ---",
            file=self.output_stream,
            flush=True,
        )
        lines: list[str] = []
        while not self._stop_event.is_set():
            line = self.input_stream.readline()
            if line == "":
                break
            stripped = line.rstrip("\n")
            if stripped.strip() == "---":
                break
            lines.append(stripped)
        note_text = "\n".join(lines).strip()
        return note_text or None

    def _stdin_loop(self) -> None:
        while not self._stop_event.is_set():
            raw_command = self.input_stream.readline()
            if raw_command == "":
                return
            command = raw_command.rstrip("\n")
            if command.strip().lower() == "note both":
                note_text = self._read_multiline_note()
                if not note_text:
                    self.log("Empty note discarded.")
                    continue
                command = f"note both {note_text}"
            self.submit_command(command, source="cli")

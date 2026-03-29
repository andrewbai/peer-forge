from __future__ import annotations

import json
from pathlib import Path
import select
import sys
from typing import Any, TextIO

from peer_consensus import clip_text, utc_timestamp_precise, write_json

from live_state import (
    AGENTS,
    agent_runtime_ref,
    current_execution_package,
    current_final_plan_path,
    current_turn,
    next_note_id,
    package_diff_path,
    package_manifest_path,
    read_file_tail,
    read_text_preview,
    sanitize_terminal_text,
    save_state,
    supervisor_log_line,
    write_supervisor_event,
)


class CliSupervisor:
    def __init__(
        self,
        state: dict[str, Any],
        transport: Any,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self.state = state
        self.transport = transport
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout

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

    def read_note_text(self, initial_text: str | None = None) -> str | None:
        if initial_text and initial_text.strip():
            return initial_text.strip()
        print(
            "Enter symmetric note text. End with a line containing only ---",
            file=self.output_stream,
            flush=True,
        )
        lines: list[str] = []
        while True:
            line = self.input_stream.readline()
            if line == "":
                break
            stripped = line.rstrip("\n")
            if stripped.strip() == "---":
                break
            lines.append(stripped)
        note_text = "\n".join(lines).strip()
        return note_text or None

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

    def inspect_agent(self, agent: str) -> list[str]:
        turn = current_turn(self.state)
        turn_agent = turn["agents"][agent]
        agent_state = self.state["agents"][agent]
        pane_capture = self.transport.capture_recent(agent, lines=80)
        lines = [
            f"Agent: {agent}",
            f"Runtime: {self.transport.describe_agent(agent) or agent_runtime_ref(self.state, agent)}",
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
                self.log(
                    "Commands: continue, status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, abort",
                )
            else:
                self.log(
                    "Commands while running: status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, wait, abort",
                )
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
            self.emit_lines(self.inspect_agent(agent_name))
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
            note_text = self.read_note_text(inline_text)
            if not note_text:
                self.log("Empty note discarded.")
                return None
            note = self.create_note(
                text=note_text,
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
            return "continue"
        if lower == "abort":
            return "abort"
        self.log(f"Unknown command: {command}")
        return None

    def poll_running_command(self, *, timeout: float, next_phase: str | None) -> str | None:
        ready, _, _ = select.select([self.input_stream], [], [], timeout)
        if not ready:
            return None
        raw_command = self.input_stream.readline()
        if raw_command == "":
            return None
        return self.handle_command(mode="running", next_phase=next_phase, raw_command=raw_command)

    def pause_for_boundary(self, *, label: str, next_phase: str | None) -> None:
        self.log(label)
        if next_phase is None:
            self.log("No later phase remains.")
        else:
            self.log(f"Next phase: {next_phase}")
        self.log(
            "Boundary commands: continue, status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, abort",
        )
        while True:
            print("> ", end="", file=self.output_stream, flush=True)
            raw_command = self.input_stream.readline()
            if raw_command == "":
                continue
            action = self.handle_command(mode="boundary", next_phase=next_phase, raw_command=raw_command)
            if action == "continue":
                return
            if action == "abort":
                raise KeyboardInterrupt("Supervisor aborted the live run.")

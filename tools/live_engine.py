from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from live_protocol import (
    build_execution_fix_prompt,
    build_execution_prompt,
    build_execution_review_prompt,
    build_execution_signoff_prompt,
    build_final_fix_prompt,
    build_plan_consensus_prompt,
    build_plan_finalize_prompt,
    build_plan_initial_prompt,
    build_plan_review_prompt,
    build_plan_revise_prompt,
    build_plan_signoff_prompt,
    build_watchdog_nudge,
    parse_turn_result,
)
from live_state import (
    AGENTS,
    DISPLAY_NAMES,
    active_notes_for_turn,
    append_text,
    boundary_pending,
    capture_read_only_snapshot,
    ensure_execution_package,
    ensure_plan_merge_brief,
    find_turn,
    peer_agent,
    persist_final_candidate,
    persist_report,
    phase_label,
    phase_summary_text,
    prompt_file_message,
    report_path,
    save_state,
    session_prompt_path_for,
    summarize_agent_result,
    summarize_signoff_objections,
    supervisor_log_line,
    turn_dir_for,
    turn_id_for,
    turn_results,
    validate_read_only_snapshot,
    write_supervisor_event,
)
from peer_consensus import utc_timestamp_precise, write_json, write_text


class ProtocolStateMachine:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def prepare_turn(
        self,
        *,
        phase: str,
        prompt_texts: dict[str, str],
        active_agents: list[str],
        writable_agents: list[str] | None = None,
    ) -> dict[str, Any]:
        turn_id = turn_id_for(self.state, phase)
        turn_dir = turn_dir_for(self.state, turn_id)
        active_set = set(active_agents)
        writable_set = set(writable_agents or [])
        invalid_writers = sorted(writable_set - active_set)
        if invalid_writers:
            raise ValueError(f"Writable agents must be active in {phase}: {', '.join(invalid_writers)}")
        phase_mode = "read-only"
        if writable_set:
            phase_mode = "write" if writable_set == active_set else "mixed"
        turn = {
            "id": turn_id,
            "index": len(self.state["turns"]) + 1,
            "phase": phase,
            "phase_family": phase_label(phase),
            "mode": phase_mode,
            "summary": phase_summary_text(phase),
            "created_at": utc_timestamp_precise(),
            "started_at": "",
            "completed_at": "",
            "status": "pending",
            "watchdog_nudges": 0,
            "agents": {},
        }
        for agent in AGENTS:
            prompt_path = turn_dir / agent / "prompt.txt"
            session_prompt_path = session_prompt_path_for(self.state, turn_id, agent)
            result_path = turn_dir / agent / "result.json"
            turn_log_path = turn_dir / agent / "turn.log"
            entry_snapshot_status_path = turn_dir / agent / "entry-snapshot.status.txt"
            entry_snapshot_diff_path = turn_dir / agent / "entry-snapshot.diff.txt"
            is_active = agent in active_agents
            is_read_only = is_active and agent not in writable_set
            if is_active:
                write_text(prompt_path, prompt_texts[agent].strip() + "\n")
                write_text(session_prompt_path, prompt_texts[agent].strip() + "\n")
                write_text(turn_log_path, "")
            turn["agents"][agent] = {
                "active": is_active,
                "read_only": is_read_only,
                "status": "pending" if is_active else "skipped",
                "prompt_path": str(prompt_path) if is_active else "",
                "session_prompt_path": str(session_prompt_path) if is_active else "",
                "result_path": str(result_path) if is_active else "",
                "turn_log_path": str(turn_log_path) if is_active else "",
                "entry_snapshot_status_path": str(entry_snapshot_status_path) if is_active and is_read_only else "",
                "entry_snapshot_diff_path": str(entry_snapshot_diff_path) if is_active and is_read_only else "",
                "entry_snapshot_taken_at": "",
                "turn_start_offset": 0,
                "parse_error": "",
                "result": None,
                "completed_at": "",
                "nudge_count": 0,
                "read_only_violation": None,
            }
        self.state["turns"].append(turn)
        self.state["current_phase"] = phase
        self.state["status"] = "running"
        save_state(self.state)
        return turn

    def create_initial_turn(self) -> dict[str, Any]:
        prompt_texts = {
            "claude": build_plan_initial_prompt(
                turn_id=turn_id_for(self.state, "plan-initial"),
                phase="plan-initial",
                agent="claude",
                agent_name=DISPLAY_NAMES["claude"],
                task=self.state["task"],
                acceptance=self.state["acceptance"],
                scope=self.state["scope"],
                supervisor_notes=active_notes_for_turn(self.state, len(self.state["turns"]) + 1),
            ),
            "codex": build_plan_initial_prompt(
                turn_id=turn_id_for(self.state, "plan-initial"),
                phase="plan-initial",
                agent="codex",
                agent_name=DISPLAY_NAMES["codex"],
                task=self.state["task"],
                acceptance=self.state["acceptance"],
                scope=self.state["scope"],
                supervisor_notes=active_notes_for_turn(self.state, len(self.state["turns"]) + 1),
            ),
        }
        return self.prepare_turn(phase="plan-initial", prompt_texts=prompt_texts, active_agents=["claude", "codex"])

    def build_plan_review_turn(self, plan_initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            "claude": build_plan_review_prompt(
                turn_id=turn_id_for(self.state, "plan-review"),
                phase="plan-review",
                reviewer="claude",
                reviewer_name=DISPLAY_NAMES["claude"],
                peer_name=DISPLAY_NAMES["codex"],
                peer_plan=plan_initial["codex"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
            "codex": build_plan_review_prompt(
                turn_id=turn_id_for(self.state, "plan-review"),
                phase="plan-review",
                reviewer="codex",
                reviewer_name=DISPLAY_NAMES["codex"],
                peer_name=DISPLAY_NAMES["claude"],
                peer_plan=plan_initial["claude"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
        }
        return self.prepare_turn(phase="plan-review", prompt_texts=prompt_texts, active_agents=["claude", "codex"])

    def build_plan_revise_turn(self, plan_reviews: dict[str, dict[str, Any]]) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            "claude": build_plan_revise_prompt(
                turn_id=turn_id_for(self.state, "plan-revise"),
                phase="plan-revise",
                agent="claude",
                agent_name=DISPLAY_NAMES["claude"],
                peer_name=DISPLAY_NAMES["codex"],
                peer_review=plan_reviews["codex"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
            "codex": build_plan_revise_prompt(
                turn_id=turn_id_for(self.state, "plan-revise"),
                phase="plan-revise",
                agent="codex",
                agent_name=DISPLAY_NAMES["codex"],
                peer_name=DISPLAY_NAMES["claude"],
                peer_review=plan_reviews["claude"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
        }
        return self.prepare_turn(phase="plan-revise", prompt_texts=prompt_texts, active_agents=["claude", "codex"])

    def build_plan_consensus_turn(self, plan_revisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            "claude": build_plan_consensus_prompt(
                turn_id=turn_id_for(self.state, "plan-consensus"),
                phase="plan-consensus",
                agent="claude",
                agent_name=DISPLAY_NAMES["claude"],
                peer_name=DISPLAY_NAMES["codex"],
                own_revision=plan_revisions["claude"],
                peer_revision=plan_revisions["codex"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
            "codex": build_plan_consensus_prompt(
                turn_id=turn_id_for(self.state, "plan-consensus"),
                phase="plan-consensus",
                agent="codex",
                agent_name=DISPLAY_NAMES["codex"],
                peer_name=DISPLAY_NAMES["claude"],
                own_revision=plan_revisions["codex"],
                peer_revision=plan_revisions["claude"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
        }
        return self.prepare_turn(phase="plan-consensus", prompt_texts=prompt_texts, active_agents=["claude", "codex"])

    def build_plan_finalize_turn(
        self,
        *,
        final_plan_base: str,
        merge_brief: dict[str, Any],
        plan_revisions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        if final_plan_base == "claude":
            own_revision = plan_revisions["claude"]
            peer_revision = plan_revisions["codex"]
            base_name = DISPLAY_NAMES["claude"]
            peer_name = DISPLAY_NAMES["codex"]
        else:
            own_revision = plan_revisions["codex"]
            peer_revision = plan_revisions["claude"]
            base_name = DISPLAY_NAMES["codex"]
            peer_name = DISPLAY_NAMES["claude"]
        prompt_texts = {
            final_plan_base: build_plan_finalize_prompt(
                turn_id=turn_id_for(self.state, "plan-finalize"),
                phase="plan-finalize",
                agent=final_plan_base,
                base_agent_name=base_name,
                peer_name=peer_name,
                merge_brief=merge_brief,
                own_revision=own_revision,
                peer_revision=peer_revision,
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            )
        }
        return self.prepare_turn(phase="plan-finalize", prompt_texts=prompt_texts, active_agents=[final_plan_base])

    def build_plan_signoff_turn(self, *, round_index: int, final_candidate: dict[str, Any]) -> dict[str, Any]:
        phase = "plan-signoff" if round_index == 0 else f"plan-signoff-round-{round_index}"
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            "claude": build_plan_signoff_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent="claude",
                agent_name=DISPLAY_NAMES["claude"],
                final_candidate=final_candidate,
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
            "codex": build_plan_signoff_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent="codex",
                agent_name=DISPLAY_NAMES["codex"],
                final_candidate=final_candidate,
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
        }
        return self.prepare_turn(phase=phase, prompt_texts=prompt_texts, active_agents=["claude", "codex"])

    def build_final_fix_turn(
        self,
        *,
        round_index: int,
        final_plan_base: str,
        current_candidate: dict[str, Any],
        signoffs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        phase = f"plan-final-fix-round-{round_index}"
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            final_plan_base: build_final_fix_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent=final_plan_base,
                base_agent_name=DISPLAY_NAMES[final_plan_base],
                current_candidate=current_candidate,
                objections=summarize_signoff_objections(signoffs),
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            )
        }
        return self.prepare_turn(phase=phase, prompt_texts=prompt_texts, active_agents=[final_plan_base])

    def build_execute_turn(self, *, executor: str, final_plan: dict[str, Any]) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            executor: build_execution_prompt(
                turn_id=turn_id_for(self.state, "execute-initial"),
                phase="execute-initial",
                agent=executor,
                agent_name=DISPLAY_NAMES[executor],
                task=self.state["task"],
                acceptance=self.state["acceptance"],
                scope=self.state["scope"],
                final_plan=final_plan,
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            )
        }
        return self.prepare_turn(
            phase="execute-initial",
            prompt_texts=prompt_texts,
            active_agents=[executor],
            writable_agents=[executor],
        )

    def build_execution_review_turn(
        self,
        *,
        executor: str,
        reviewer: str,
        final_plan: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_package: dict[str, Any],
    ) -> dict[str, Any]:
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            reviewer: build_execution_review_prompt(
                turn_id=turn_id_for(self.state, "execution-review"),
                phase="execution-review",
                agent=reviewer,
                reviewer_name=DISPLAY_NAMES[reviewer],
                executor_name=DISPLAY_NAMES[executor],
                final_plan=final_plan,
                execution_summary=execution_summary,
                execution_package_dir=execution_package["package_dir"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            )
        }
        return self.prepare_turn(phase="execution-review", prompt_texts=prompt_texts, active_agents=[reviewer])

    def build_execution_fix_turn(
        self,
        *,
        round_index: int,
        executor: str,
        final_plan: dict[str, Any],
        review_feedback: dict[str, Any],
    ) -> dict[str, Any]:
        phase = f"execution-fix-round-{round_index}"
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            executor: build_execution_fix_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent=executor,
                agent_name=DISPLAY_NAMES[executor],
                task=self.state["task"],
                acceptance=self.state["acceptance"],
                scope=self.state["scope"],
                final_plan=final_plan,
                review_feedback=review_feedback,
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            )
        }
        return self.prepare_turn(
            phase=phase,
            prompt_texts=prompt_texts,
            active_agents=[executor],
            writable_agents=[executor],
        )

    def build_execution_signoff_turn(
        self,
        *,
        round_index: int,
        final_plan: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_package: dict[str, Any],
    ) -> dict[str, Any]:
        phase = "execution-signoff" if round_index == 0 else f"execution-signoff-round-{round_index}"
        turn_index = len(self.state["turns"]) + 1
        prompt_texts = {
            "claude": build_execution_signoff_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent="claude",
                agent_name=DISPLAY_NAMES["claude"],
                final_plan=final_plan,
                execution_summary=execution_summary,
                execution_package_dir=execution_package["package_dir"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
            "codex": build_execution_signoff_prompt(
                turn_id=turn_id_for(self.state, phase),
                phase=phase,
                agent="codex",
                agent_name=DISPLAY_NAMES["codex"],
                final_plan=final_plan,
                execution_summary=execution_summary,
                execution_package_dir=execution_package["package_dir"],
                supervisor_notes=active_notes_for_turn(self.state, turn_index),
            ),
        }
        return self.prepare_turn(phase=phase, prompt_texts=prompt_texts, active_agents=["claude", "codex"])


class RunLoop:
    def __init__(
        self,
        state: dict[str, Any],
        *,
        transport: Any,
        supervisor: Any,
        machine: ProtocolStateMachine,
    ) -> None:
        self.state = state
        self.transport = transport
        self.supervisor = supervisor
        self.machine = machine

    def dispatch_turn(
        self,
        turn: dict[str, Any],
        *,
        send_prompts: bool = True,
    ) -> None:
        turn["started_at"] = utc_timestamp_precise()
        turn["status"] = "running"
        for agent in AGENTS:
            turn_agent = turn["agents"][agent]
            if not turn_agent["active"]:
                continue
            turn_agent["status"] = "running"
            if send_prompts:
                turn_agent["turn_start_offset"] = self.transport.output_size(agent)
            turn_agent["parse_error"] = ""
            turn_agent["result"] = None
            capture_read_only_snapshot(self.state, turn, agent)
            if send_prompts:
                self.transport.send_prompt(agent, prompt_file_message(Path(turn_agent["session_prompt_path"])))
        save_state(self.state)
        write_supervisor_event(
            self.state,
            {
                "type": "turn-dispatched",
                "timestamp": utc_timestamp_precise(),
                "turn_id": turn["id"],
                "phase": turn["phase"],
                "active_agents": [agent for agent in AGENTS if turn["agents"][agent]["active"]],
            },
        )

    def wait_for_turn(
        self,
        turn: dict[str, Any],
        *,
        next_phase: str | None,
    ) -> dict[str, dict[str, Any]]:
        buffers = {agent: "" for agent in AGENTS}
        offsets = {agent: int(turn["agents"][agent]["turn_start_offset"]) for agent in AGENTS}
        last_output_time = time.time()
        self.supervisor.log(f"Watching {turn['id']} ({turn['summary']}).")
        self.supervisor.log(
            "Live commands: status, tail claude, tail codex, inspect claude, inspect codex, show final-plan, show package, show diff, show manifest, note both, wait, abort",
        )
        while True:
            for agent in AGENTS:
                turn_agent = turn["agents"][agent]
                if not turn_agent["active"] or turn_agent["status"] == "completed":
                    continue
                text, offsets[agent] = self.transport.read_output_since(agent, offsets[agent])
                if text:
                    buffers[agent] += text
                    append_text(Path(turn_agent["turn_log_path"]), text)
                    last_output_time = time.time()
                try:
                    envelope = parse_turn_result(
                        buffers[agent],
                        expected_turn_id=turn["id"],
                        expected_phase=turn["phase"],
                        expected_agent=agent,
                    )
                except ValueError as exc:
                    if "__PEER_FORGE_DONE__" in buffers[agent]:
                        turn_agent["parse_error"] = str(exc)
                        save_state(self.state)
                    continue
                validate_read_only_snapshot(self.state, turn, agent)
                turn_agent["status"] = "completed"
                turn_agent["completed_at"] = utc_timestamp_precise()
                turn_agent["parse_error"] = ""
                turn_agent["result"] = envelope["result"]
                write_json(Path(turn_agent["result_path"]), envelope)
                save_state(self.state)
                self.supervisor.log(
                    f"{turn['id']}: {agent} completed -> {summarize_agent_result(turn['phase'], envelope['result'])}",
                )
            if all(turn["agents"][agent]["status"] == "completed" for agent in AGENTS if turn["agents"][agent]["active"]):
                turn["status"] = "completed"
                turn["completed_at"] = utc_timestamp_precise()
                save_state(self.state)
                return {
                    agent: turn["agents"][agent]["result"]
                    for agent in AGENTS
                    if turn["agents"][agent]["active"]
                }
            if (
                self.state["watchdog_seconds"] > 0
                and time.time() - last_output_time >= self.state["watchdog_seconds"]
                and turn["watchdog_nudges"] < self.state["max_watchdog_nudges"]
            ):
                nudge_text = build_watchdog_nudge(turn["id"], turn["phase"])
                for agent in AGENTS:
                    if turn["agents"][agent]["active"] and turn["agents"][agent]["status"] != "completed":
                        self.transport.send_prompt(agent, nudge_text)
                        turn["agents"][agent]["nudge_count"] += 1
                turn["watchdog_nudges"] += 1
                last_output_time = time.time()
                save_state(self.state)
                self.supervisor.log(
                    f"Watchdog nudge sent symmetrically to active agents for {turn['id']}.",
                )
            action = self.supervisor.poll_running_command(timeout=0.5, next_phase=next_phase)
            if action == "abort":
                raise KeyboardInterrupt("Supervisor aborted the live run.")

    def ensure_turn_results(
        self,
        *,
        phase: str,
        next_phase: str | None,
        build_turn: Callable[[], dict[str, Any]],
        send_prompts: bool = True,
    ) -> dict[str, dict[str, Any]]:
        turn = find_turn(self.state, phase)
        if turn is None:
            turn = build_turn()
            self.dispatch_turn(turn, send_prompts=send_prompts)
            return self.wait_for_turn(turn, next_phase=next_phase)
        if turn["status"] == "completed":
            return turn_results(turn)
        if turn["status"] == "pending":
            self.dispatch_turn(turn, send_prompts=send_prompts)
            return self.wait_for_turn(turn, next_phase=next_phase)
        if turn["status"] == "running":
            supervisor_log_line(self.state, f"Resuming active turn {turn['id']} ({turn['summary']}).")
            return self.wait_for_turn(turn, next_phase=next_phase)
        raise RuntimeError(f"Turn {turn['id']} is in unsupported state {turn['status']!r}.")

    def maybe_pause_boundary(self, *, phase: str, label: str, next_phase: str | None) -> None:
        if boundary_pending(self.state, phase):
            self.supervisor.pause_for_boundary(label=label, next_phase=next_phase)

    def serve(self) -> None:
        plan_initial = self.ensure_turn_results(
            phase="plan-initial",
            next_phase="plan-review",
            build_turn=self.machine.create_initial_turn,
            send_prompts=False,
        )
        self.maybe_pause_boundary(phase="plan-initial", label="Initial plans complete.", next_phase="plan-review")

        plan_reviews = self.ensure_turn_results(
            phase="plan-review",
            next_phase="plan-revise",
            build_turn=lambda: self.machine.build_plan_review_turn(plan_initial),
        )
        self.maybe_pause_boundary(phase="plan-review", label="Cross-review complete.", next_phase="plan-revise")

        plan_revisions = self.ensure_turn_results(
            phase="plan-revise",
            next_phase="plan-consensus",
            build_turn=lambda: self.machine.build_plan_revise_turn(plan_reviews),
        )
        self.maybe_pause_boundary(phase="plan-revise", label="Revision complete.", next_phase="plan-consensus")

        plan_consensus = self.ensure_turn_results(
            phase="plan-consensus",
            next_phase="plan-finalize",
            build_turn=lambda: self.machine.build_plan_consensus_turn(plan_revisions),
        )
        final_plan_base, merge_brief = ensure_plan_merge_brief(self.state, plan_consensus)
        self.maybe_pause_boundary(
            phase="plan-consensus",
            label=f"Consensus complete. Base side: {final_plan_base}.",
            next_phase="plan-finalize",
        )

        finalize_result = self.ensure_turn_results(
            phase="plan-finalize",
            next_phase="plan-signoff",
            build_turn=lambda: self.machine.build_plan_finalize_turn(
                final_plan_base=final_plan_base,
                merge_brief=merge_brief,
                plan_revisions=plan_revisions,
            ),
        )
        current_final = self.state.get("final_plan") or finalize_result[final_plan_base]
        persist_final_candidate(self.state, current_final)
        self.maybe_pause_boundary(phase="plan-finalize", label="Final plan candidate drafted.", next_phase="plan-signoff")

        signoff_round_index = 0
        final_approved = False
        latest_signoffs: dict[str, dict[str, Any]] = {}
        last_plan_signoff_phase = "plan-signoff"
        while True:
            signoff_phase = "plan-signoff" if signoff_round_index == 0 else f"plan-signoff-round-{signoff_round_index}"
            last_plan_signoff_phase = signoff_phase
            next_phase = None
            if signoff_round_index < self.state["signoff_rounds"]:
                next_phase = f"plan-final-fix-round-{signoff_round_index + 1}"
            signoffs = self.ensure_turn_results(
                phase=signoff_phase,
                next_phase=next_phase,
                build_turn=lambda signoff_round_index=signoff_round_index, current_final=current_final: self.machine.build_plan_signoff_turn(
                    round_index=signoff_round_index,
                    final_candidate=current_final,
                ),
            )
            latest_signoffs = signoffs
            if all(result["overall_verdict"] == "approve" for result in signoffs.values()):
                final_approved = True
                break
            if signoff_round_index >= self.state["signoff_rounds"]:
                break
            fix_round = signoff_round_index + 1
            self.maybe_pause_boundary(
                phase=signoff_phase,
                label=f"Signoff round {fix_round} found objections.",
                next_phase=f"plan-final-fix-round-{fix_round}",
            )
            fix_phase = f"plan-final-fix-round-{fix_round}"
            fixed = self.ensure_turn_results(
                phase=fix_phase,
                next_phase=f"plan-signoff-round-{fix_round}",
                build_turn=lambda fix_round=fix_round, current_final=current_final, signoffs=signoffs: self.machine.build_final_fix_turn(
                    round_index=fix_round,
                    final_plan_base=final_plan_base,
                    current_candidate=current_final,
                    signoffs=signoffs,
                ),
            )
            current_final = fixed[final_plan_base]
            persist_final_candidate(self.state, current_final)
            self.maybe_pause_boundary(
                phase=fix_phase,
                label=f"Final-fix round {fix_round} complete.",
                next_phase=f"plan-signoff-round-{fix_round}",
            )
            signoff_round_index = fix_round

        self.state["status"] = "approved" if final_approved else "needs-attention"
        self.state["summary"]["plan_approved"] = final_approved
        self.state["summary"]["final_approved"] = final_approved
        self.state["summary"]["plan_signoffs"] = latest_signoffs
        self.state["summary"]["final_signoffs"] = latest_signoffs
        self.state["summary"]["final_candidate"] = current_final
        self.state["summary"]["execution_approved"] = False
        save_state(self.state)
        if not final_approved:
            persist_report(self.state)
            self.supervisor.log(
                f"Live run finished at plan stage. plan_approved={final_approved}. Report: {report_path(self.state)}",
            )
            return

        executor = final_plan_base
        reviewer = peer_agent(executor)
        self.state["selected_executor"] = executor
        self.state["selected_reviewer"] = reviewer
        save_state(self.state)
        self.maybe_pause_boundary(
            phase=last_plan_signoff_phase,
            label=f"Plan approved. Executor: {executor}. Reviewer: {reviewer}.",
            next_phase="execute-initial",
        )

        execute_result = self.ensure_turn_results(
            phase="execute-initial",
            next_phase="execution-review",
            build_turn=lambda: self.machine.build_execute_turn(executor=executor, final_plan=current_final),
        )
        current_execution = execute_result[executor]
        execute_turn = find_turn(self.state, "execute-initial")
        if execute_turn is None:
            raise RuntimeError("Missing execute-initial turn after execution.")
        current_execution_package = ensure_execution_package(
            self.state,
            turn=execute_turn,
            executor=executor,
            execution_summary=current_execution,
        )
        self.state["summary"]["execution_review"] = self.state["summary"].get("execution_review", {})
        self.state["summary"]["execution_signoffs"] = self.state["summary"].get("execution_signoffs", {})
        self.state["summary"]["current_execution"] = current_execution
        save_state(self.state)
        self.maybe_pause_boundary(phase="execute-initial", label="Initial execution complete.", next_phase="execution-review")

        review_next_phase = "execution-signoff"
        if self.state["signoff_rounds"] > 0:
            review_next_phase = "execution-fix-round-1"
        execution_review_result = self.ensure_turn_results(
            phase="execution-review",
            next_phase=review_next_phase,
            build_turn=lambda: self.machine.build_execution_review_turn(
                executor=executor,
                reviewer=reviewer,
                final_plan=current_final,
                execution_summary=current_execution,
                execution_package=current_execution_package,
            ),
        )[reviewer]
        self.state["summary"]["execution_review"] = execution_review_result
        self.state["summary"]["current_execution"] = current_execution
        save_state(self.state)

        current_execution_signoffs: dict[str, dict[str, Any]] = {}
        execution_approved = False
        pending_fix_feedback: dict[str, Any] | None = None
        execution_fix_round = 0

        if execution_review_result["overall_verdict"] == "approve":
            self.maybe_pause_boundary(
                phase="execution-review",
                label="Implementation review approved. Proceeding to implementation signoff.",
                next_phase="execution-signoff",
            )
        else:
            if self.state["signoff_rounds"] == 0:
                self.state["status"] = "needs-attention"
                self.state["summary"]["execution_approved"] = False
                self.state["summary"]["final_approved"] = False
                save_state(self.state)
                persist_report(self.state)
                self.supervisor.log(
                    "Implementation review requested changes but no execution fix rounds are available.",
                )
                return
            execution_fix_round = 1
            pending_fix_feedback = execution_review_result
            self.maybe_pause_boundary(
                phase="execution-review",
                label="Implementation review requested changes.",
                next_phase="execution-fix-round-1",
            )

        while True:
            if execution_fix_round > 0:
                fix_phase = f"execution-fix-round-{execution_fix_round}"
                fix_result = self.ensure_turn_results(
                    phase=fix_phase,
                    next_phase=f"execution-signoff-round-{execution_fix_round}",
                    build_turn=lambda execution_fix_round=execution_fix_round, pending_fix_feedback=pending_fix_feedback: self.machine.build_execution_fix_turn(
                        round_index=execution_fix_round,
                        executor=executor,
                        final_plan=current_final,
                        review_feedback=pending_fix_feedback or {},
                    ),
                )
                current_execution = fix_result[executor]
                fix_turn = find_turn(self.state, fix_phase)
                if fix_turn is None:
                    raise RuntimeError(f"Missing {fix_phase} turn after execution fix.")
                current_execution_package = ensure_execution_package(
                    self.state,
                    turn=fix_turn,
                    executor=executor,
                    execution_summary=current_execution,
                )
                self.state["summary"]["current_execution"] = current_execution
                save_state(self.state)
                self.maybe_pause_boundary(
                    phase=fix_phase,
                    label=f"Execution fix round {execution_fix_round} complete.",
                    next_phase=f"execution-signoff-round-{execution_fix_round}",
                )

            signoff_phase = "execution-signoff" if execution_fix_round == 0 else f"execution-signoff-round-{execution_fix_round}"
            next_phase = None
            if execution_fix_round < self.state["signoff_rounds"]:
                next_phase = f"execution-fix-round-{execution_fix_round + 1}"
            current_execution_signoffs = self.ensure_turn_results(
                phase=signoff_phase,
                next_phase=next_phase,
                build_turn=lambda execution_fix_round=execution_fix_round, current_execution=current_execution, current_execution_package=current_execution_package: self.machine.build_execution_signoff_turn(
                    round_index=execution_fix_round,
                    final_plan=current_final,
                    execution_summary=current_execution,
                    execution_package=current_execution_package,
                ),
            )
            if all(result["overall_verdict"] == "approve" for result in current_execution_signoffs.values()):
                execution_approved = True
                break
            if execution_fix_round >= self.state["signoff_rounds"]:
                break
            pending_fix_feedback = summarize_signoff_objections(current_execution_signoffs)
            next_fix_round = execution_fix_round + 1
            self.maybe_pause_boundary(
                phase=signoff_phase,
                label=f"Implementation signoff round {execution_fix_round + 1} found objections.",
                next_phase=f"execution-fix-round-{next_fix_round}",
            )
            execution_fix_round = next_fix_round

        self.state["status"] = "approved" if execution_approved else "needs-attention"
        self.state["summary"]["execution_approved"] = execution_approved
        self.state["summary"]["execution_signoffs"] = current_execution_signoffs
        self.state["summary"]["current_execution"] = current_execution
        self.state["summary"]["final_approved"] = execution_approved
        save_state(self.state)
        persist_report(self.state)
        self.supervisor.log(
            (
                "Live run finished. "
                f"plan_approved={final_approved}, execution_approved={execution_approved}. "
                f"Report: {report_path(self.state)}"
            ),
        )

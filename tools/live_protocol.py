from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from peer_consensus import (
    CONSENSUS_SCHEMA,
    EXECUTION_SCHEMA,
    FINAL_PLAN_SCHEMA,
    FINDING_SCHEMA,
    PLAN_REVISION_SCHEMA,
    PLAN_SCHEMA,
    REVIEW_SCHEMA,
    prompt_header,
)


DONE_SENTINEL = "__PEER_FORGE_DONE__"
RESULT_START = "<PEER_FORGE_RESULT>"
RESULT_END = "</PEER_FORGE_RESULT>"

LIVE_SIGNOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_verdict",
        "summary",
        "blocking_findings",
        "must_preserve",
    ],
    "properties": {
        "overall_verdict": {
            "type": "string",
            "enum": ["approve", "reject"],
        },
        "summary": {"type": "string"},
        "blocking_findings": {"type": "array", "items": FINDING_SCHEMA},
        "must_preserve": {"type": "array", "items": {"type": "string"}},
    },
}

PHASE_PAYLOAD_SCHEMAS: dict[str, dict[str, Any]] = {
    "plan-initial": PLAN_SCHEMA,
    "plan-review": REVIEW_SCHEMA,
    "plan-revise": PLAN_REVISION_SCHEMA,
    "plan-consensus": CONSENSUS_SCHEMA,
    "plan-finalize": FINAL_PLAN_SCHEMA,
    "plan-final-fix": FINAL_PLAN_SCHEMA,
    "plan-signoff": LIVE_SIGNOFF_SCHEMA,
    "execute-initial": EXECUTION_SCHEMA,
    "execution-review": REVIEW_SCHEMA,
    "execution-fix": EXECUTION_SCHEMA,
    "execution-signoff": LIVE_SIGNOFF_SCHEMA,
}

RESULT_BLOCK_RE = re.compile(
    rf"{re.escape(RESULT_START)}\s*(.*?)\s*{re.escape(RESULT_END)}",
    re.DOTALL,
)


def phase_payload_schema(phase: str) -> dict[str, Any]:
    if phase.startswith("plan-final-fix"):
        return PHASE_PAYLOAD_SCHEMAS["plan-final-fix"]
    if phase.startswith("plan-signoff"):
        return PHASE_PAYLOAD_SCHEMAS["plan-signoff"]
    if phase.startswith("execution-review"):
        return PHASE_PAYLOAD_SCHEMAS["execution-review"]
    if phase.startswith("execution-fix"):
        return PHASE_PAYLOAD_SCHEMAS["execution-fix"]
    if phase.startswith("execution-signoff"):
        return PHASE_PAYLOAD_SCHEMAS["execution-signoff"]
    try:
        return PHASE_PAYLOAD_SCHEMAS[phase]
    except KeyError as exc:
        raise ValueError(f"Unsupported live phase: {phase}") from exc


def schema_shape_text(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=True)


def render_supervisor_notes(notes: list[dict[str, Any]] | None) -> str:
    if not notes:
        return ""
    rendered: list[str] = []
    for note in notes:
        body = textwrap.indent(str(note.get("text", "")).strip(), "  ")
        rendered.append(
            "\n".join(
                [
                    f"[{note.get('id', '')}] applies from {note.get('applies_from_phase', '')}:",
                    body or "  (empty)",
                ]
            )
        )
    notes_body = "\n\n".join(rendered)
    return textwrap.dedent(
        f"""
        Supervisor Notes:
        - These notes are symmetric and apply equally to both agents.
        - They supplement the original task and constraints from this point forward.
        - Do not treat them as permission to write code or bypass the live protocol.

        {notes_body}
        """
    ).strip()


def phase_guardrails(
    turn_id: str,
    phase: str,
    agent: str,
    schema: dict[str, Any],
    *,
    allow_writes: bool,
) -> str:
    phase_rule = (
        "This phase allows code changes, but only inside your own isolated workspace."
        if allow_writes
        else "This phase is read-only. Do not modify code, do not run write commands, and do not create commits, branches, or tags."
    )
    return textwrap.dedent(
        f"""
        Live Protocol:
        - Current turn_id: {turn_id}
        - Current phase: {phase}
        - Current agent field value: {agent}
        - {phase_rule}
        - Stay inside your own isolated workspace only.
        - Never create commits, branches, or tags.
        - When you are done, output exactly one result envelope using the required markers.
        - The inner `result` object must match this JSON schema exactly:

        {schema_shape_text(schema)}

        Required completion format:
        {RESULT_START}
        {{"turn_id":"{turn_id}","phase":"{phase}","agent":"{agent}","status":"done","result":{{...}}}}
        {RESULT_END}
        {DONE_SENTINEL}
        """
    ).strip()


def build_plan_initial_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} in Peer Forge Live.

        Produce your best implementation plan independently, as if the peer agent does not exist yet.

        {prompt_header(task, acceptance, scope)}

        {notes_block}

        Instructions:
        - Be concrete.
        - Keep scope tight unless expansion is strictly necessary.
        - Focus on plan quality, risks, and verification strategy.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_plan_review_prompt(
    *,
    turn_id: str,
    phase: str,
    reviewer: str,
    reviewer_name: str,
    peer_name: str,
    peer_plan: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {reviewer_name} reviewing {peer_name}'s plan in Peer Forge Live.

        You already know the task context from this live session. Do not restate the whole task unless needed.

        {notes_block}

        Peer plan:
        {json.dumps(peer_plan, indent=2, ensure_ascii=True)}

        Review standard:
        - correctness
        - missing steps
        - hidden risks
        - unrealistic scope
        - weak verification

        {phase_guardrails(turn_id, phase, reviewer, schema, allow_writes=False)}
        """
    ).strip()


def build_plan_revise_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    peer_name: str,
    peer_review: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} revising your own plan after reading {peer_name}'s review.

        {notes_block}

        Peer review of your plan:
        {json.dumps(peer_review, indent=2, ensure_ascii=True)}

        Revision rules:
        - Fix valid issues.
        - Keep useful strengths from your original plan.
        - Keep the plan concrete and directly executable.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_plan_consensus_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    peer_name: str,
    own_revision: dict[str, Any],
    peer_revision: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} deciding which revised plan should become the final base.

        {notes_block}

        Your revised plan:
        {json.dumps(own_revision, indent=2, ensure_ascii=True)}

        {peer_name}'s revised plan:
        {json.dumps(peer_revision, indent=2, ensure_ascii=True)}

        Decide:
        - which revised plan is the better base
        - what must be preserved from each side
        - what blockers remain against either candidate

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_plan_finalize_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    base_agent_name: str,
    peer_name: str,
    merge_brief: dict[str, Any],
    own_revision: dict[str, Any],
    peer_revision: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {base_agent_name}. Your side was chosen as the base for the final plan candidate.

        {notes_block}

        Your revised plan:
        {json.dumps(own_revision, indent=2, ensure_ascii=True)}

        {peer_name}'s revised plan:
        {json.dumps(peer_revision, indent=2, ensure_ascii=True)}

        Merge brief:
        {json.dumps(merge_brief, indent=2, ensure_ascii=True)}

        Produce the best final candidate that preserves valid strengths from both sides and resolves blockers where possible.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_plan_signoff_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    final_candidate: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} signing off on the current final plan candidate.

        {notes_block}

        Final candidate:
        {json.dumps(final_candidate, indent=2, ensure_ascii=True)}

        Signoff rules:
        - Approve only if the candidate is acceptable as the final agreed plan.
        - Reject only for substantive blockers.
        - List blockers as structured findings.
        - If you reject, clearly state what must be preserved in the next revision.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_final_fix_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    base_agent_name: str,
    current_candidate: dict[str, Any],
    objections: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {base_agent_name} revising the current final plan candidate after signoff objections.

        {notes_block}

        Current candidate:
        {json.dumps(current_candidate, indent=2, ensure_ascii=True)}

        Combined objections:
        {json.dumps(objections, indent=2, ensure_ascii=True)}

        Fix rules:
        - Resolve valid blockers.
        - Preserve strengths explicitly called out by the reviewers.
        - Keep the final candidate internally consistent.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_execution_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    final_plan: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} executing the agreed final plan in Peer Forge Live.

        This is the code-writing phase. Modify code only inside this isolated workspace.

        {prompt_header(task, acceptance, scope)}

        {notes_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Execution rules:
        - Follow the final plan closely.
        - Keep changes minimal and coherent.
        - If reality differs from the plan, adapt pragmatically and explain why.
        - Run targeted verification if it is cheap and local.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=True)}
        """
    ).strip()


def build_execution_review_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    reviewer_name: str,
    executor_name: str,
    final_plan: dict[str, Any],
    execution_summary: dict[str, Any],
    execution_package_dir: str,
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {reviewer_name} reviewing {executor_name}'s implementation against the agreed final plan.

        Review only. Do not modify your workspace.

        {notes_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Execution summary:
        {json.dumps(execution_summary, indent=2, ensure_ascii=True)}

        Implementation artifacts:
        - Diff: {execution_package_dir}/solution.diff
        - Manifest: {execution_package_dir}/manifest.json
        - Changed file copies root: {execution_package_dir}/files

        Review standard:
        - adherence to the final plan
        - correctness
        - regressions
        - edge cases
        - missing tests

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_execution_fix_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    task: str,
    acceptance: list[str],
    scope: list[str],
    final_plan: dict[str, Any],
    review_feedback: dict[str, Any],
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} updating the implementation after peer review.

        {prompt_header(task, acceptance, scope)}

        {notes_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Review feedback:
        {json.dumps(review_feedback, indent=2, ensure_ascii=True)}

        Fix rules:
        - Address valid review findings.
        - Keep the implementation aligned with the final plan.
        - Keep the diff focused.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=True)}
        """
    ).strip()


def build_execution_signoff_prompt(
    *,
    turn_id: str,
    phase: str,
    agent: str,
    agent_name: str,
    final_plan: dict[str, Any],
    execution_summary: dict[str, Any],
    execution_package_dir: str,
    supervisor_notes: list[dict[str, Any]] | None = None,
) -> str:
    schema = phase_payload_schema(phase)
    notes_block = render_supervisor_notes(supervisor_notes)
    return textwrap.dedent(
        f"""
        You are {agent_name} signing off on the current implementation candidate.

        {notes_block}

        Final plan:
        {json.dumps(final_plan, indent=2, ensure_ascii=True)}

        Execution summary:
        {json.dumps(execution_summary, indent=2, ensure_ascii=True)}

        Implementation artifacts:
        - Diff: {execution_package_dir}/solution.diff
        - Manifest: {execution_package_dir}/manifest.json
        - Changed file copies root: {execution_package_dir}/files

        Signoff rules:
        - Approve only if this implementation is acceptable as the final agreed result.
        - Reject only for substantive blockers.
        - List blockers as structured findings.
        - If you reject, clearly state what must be preserved in the next revision.

        {phase_guardrails(turn_id, phase, agent, schema, allow_writes=False)}
        """
    ).strip()


def build_watchdog_nudge(turn_id: str, phase: str) -> str:
    return textwrap.dedent(
        f"""
        Watchdog follow-up for turn {turn_id} ({phase}).

        If you have completed this turn, emit the required completion envelope and {DONE_SENTINEL} now.
        If you are not done, reply briefly with what remains, then continue until completion.
        """
    ).strip()


def build_supervisor_note(note_text: str) -> str:
    return textwrap.dedent(
        f"""
        Peer Forge Live supervisor note. This note is symmetric and applies equally to both agents.

        Note:
        {note_text}

        Incorporate it from this point forward without violating the live protocol.
        """
    ).strip()


def has_done_sentinel(text: str) -> bool:
    return DONE_SENTINEL in text


def extract_result_block(text: str) -> dict[str, Any] | None:
    matches = RESULT_BLOCK_RE.findall(text)
    if not matches:
        return None
    payload_text = matches[-1].strip()
    if not payload_text:
        return None
    return json.loads(payload_text)


def validate_shape(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object.")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValueError(f"{path}.{key} is required.")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            extra = sorted(set(value.keys()) - allowed)
            if extra:
                raise ValueError(f"{path} has unexpected keys: {', '.join(extra)}.")
        for key, sub_schema in schema.get("properties", {}).items():
            if key in value:
                validate_shape(value[key], sub_schema, f"{path}.{key}")
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array.")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            validate_shape(item, item_schema, f"{path}[{index}]")
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string.")
        enum = schema.get("enum")
        if enum is not None and value not in enum:
            raise ValueError(f"{path} must be one of: {', '.join(enum)}.")
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean.")
        return
    if schema_type is None:
        return
    raise ValueError(f"Unsupported schema type at {path}: {schema_type}")


def parse_turn_result(
    text: str,
    *,
    expected_turn_id: str,
    expected_phase: str,
    expected_agent: str,
) -> dict[str, Any]:
    if not has_done_sentinel(text):
        raise ValueError("Completion sentinel not found.")
    envelope = extract_result_block(text)
    if not isinstance(envelope, dict):
        raise ValueError("Result envelope not found.")
    if envelope.get("turn_id") != expected_turn_id:
        raise ValueError(f"Unexpected turn_id: {envelope.get('turn_id')!r}")
    if envelope.get("phase") != expected_phase:
        raise ValueError(f"Unexpected phase: {envelope.get('phase')!r}")
    if envelope.get("agent") != expected_agent:
        raise ValueError(f"Unexpected agent: {envelope.get('agent')!r}")
    if envelope.get("status") != "done":
        raise ValueError(f"Unexpected status: {envelope.get('status')!r}")
    if "result" not in envelope:
        raise ValueError("Missing result payload.")
    validate_shape(envelope["result"], phase_payload_schema(expected_phase), path="result")
    return envelope

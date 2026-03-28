---
name: codex-collab
description: |
  Let Claude Code call OpenAI Codex in headless mode for bounded collaboration on a single file.
  Use when the user wants Claude and Codex to co-author a change, wants Codex to propose or apply
  a patch to one target file, or wants a Codex review pass without opening interactive Codex.
---

# codex-collab

Use this skill when the user wants a faster, tighter workflow than "two separate branches plus cross-review".

This skill is for same-file collaboration inside the current repo. Claude stays in control, Codex runs as a headless specialist.

## When This Is Better

Prefer this skill when:
- one file or one narrow change is the center of gravity
- the user wants fast turn-taking inside the current workspace
- Claude should remain the conductor and final integrator

Do not use this skill when:
- you need two truly independent solutions
- the task spans architecture, many files, or a large refactor
- you want review rigor stronger than collaboration speed

Compared with the earlier "parallel output + cross-review" workflow:
- `codex-collab` is faster and lower-friction
- the earlier workflow is more independent and better for adjudication

## Core Rule

"Two agents on one file" means serialized ownership, not simultaneous writes.

Rules:
- Claude is the orchestrator.
- Codex runs through `scripts/codex-headless-collab.sh`.
- Only one Codex run may target a file at a time.
- Default to `plan`, then `review`.
- Use `apply` only when the target file and success criteria are explicit.
- If Codex says another file is required, stop and decide whether to widen scope.

## Workflow

1. Identify the exact target file and acceptance criteria.
2. Run `plan` first to get Codex's independent approach and risk scan.
3. Decide who edits next:
   - Claude edits the file directly, or
   - Claude asks Codex to do a bounded `apply`
4. Run `review` on the current file state.
5. Claude integrates, validates, and reports the result.

## Commands

Before first use:

```bash
which codex
codex exec --help >/dev/null
```

Plan:

```bash
./.claude/skills/codex-collab/scripts/codex-headless-collab.sh \
  --mode plan \
  --file path/to/file.ts \
  -- "Add retry logic without changing the public API. Call out edge cases and tests."
```

Apply:

```bash
./.claude/skills/codex-collab/scripts/codex-headless-collab.sh \
  --mode apply \
  --file path/to/file.ts \
  -- "Implement the agreed retry logic in this file only. Keep the diff minimal."
```

Review:

```bash
./.claude/skills/codex-collab/scripts/codex-headless-collab.sh \
  --mode review \
  --file path/to/file.ts \
  -- "Review the current state of this file for correctness, regressions, and missing tests."
```

## Prompt Contract For Codex

Always make the task concrete:
- target file path
- exact change request
- non-goals
- API or behavior constraints
- verification expectations

Keep this guardrail in the prompt:
- read anything you need, but only modify the target file
- if another file is necessary, stop and say so

## Output Handling

The helper writes each run under:

- `.claude/tmp/codex-collab/<timestamp>-<mode>/prompt.txt`
- `.claude/tmp/codex-collab/<timestamp>-<mode>/codex.log`
- `.claude/tmp/codex-collab/<timestamp>-<mode>/last-message.txt`
- `.claude/tmp/codex-collab/<timestamp>-<mode>/changed-files.txt`

After every `apply`:
- inspect `changed-files.txt`
- if files other than the target changed, stop and review before continuing

## Failure Modes

If `codex` is missing or auth fails:
- tell the user Codex headless collaboration is unavailable
- fall back to Claude-only implementation, or use the earlier parallel-review workflow

If the file is already in the middle of live edits by another agent or the user:
- do not run `apply`
- use `plan` or `review` until ownership is clear

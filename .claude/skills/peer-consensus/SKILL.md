---
name: peer-consensus
description: |
  Run a full dual-agent consensus workflow with Claude Code and Codex: isolated workspaces,
  plan consensus first, then single-agent execution plus peer review.
  Use when the user wants two peer coding agents that do not contaminate each other's initial work.
---

# peer-consensus

Use this skill when the user wants Claude Code and Codex to behave like two peer students:
- both produce plans independently
- both review and revise plans
- both converge on one final plan
- one side executes the chosen plan
- the other side reviews the implementation result

This skill is powered by:

```bash
~/.peer-forge/bin/peer-consensus --help
```

## Inputs

Minimum:
- the task

Optional:
- acceptance criteria
- preferred scope

If the repo has important ignored files that the agents need, include them with `--include-path`.

## Standard Run

```bash
~/.peer-forge/bin/peer-consensus \
  --repo . \
  --task "Replace the current API retry logic with a bounded exponential backoff implementation." \
  --acceptance "Do not change the public API." \
  --acceptance "Add or update targeted tests if they exist locally." \
  --scope src/api/client.ts \
  --scope src/api/client.test.ts
```

## Apply The Final Result Back To The Source Workspace

```bash
~/.peer-forge/bin/peer-consensus \
  --repo . \
  --task-file /tmp/task.md \
  --scope src/foo.ts \
  --apply-final
```

If this toolkit is vendored inside the current project instead of being installed globally, replace `~/.peer-forge/bin/peer-consensus` with `python3 tools/peer_consensus.py`.

## Notes

- The script creates isolated workspaces under `.claude/tmp/peer-consensus/`.
- The plan phases are read-only: plan, cross-review, revision, and consensus.
- The execution phase is single-writer: the chosen side writes code, the other side reviews.
- Independent paired plan phases run in parallel.
- Claude runs in `--bare` mode by default to reduce prompt contamination.
- The protocol is round-based, not free-form multi-agent chat.
- The script exits non-zero if the implementation review does not reach approval.

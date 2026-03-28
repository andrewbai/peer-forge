---
name: peer-consensus
description: |
  Run a full dual-agent consensus workflow with Claude Code and Codex: isolated workspaces,
  independent solutions, cross-review, revision, consensus merge, and final sign-off from both sides.
  Use when the user wants two peer coding agents that do not contaminate each other's initial work.
---

# peer-consensus

Use this skill when the user wants Claude Code and Codex to behave like two peer students:
- both solve the same task independently
- both review each other's work
- both revise
- both converge on a stronger final answer
- both explicitly sign off, or raise blocking objections

This skill is powered by:

```bash
python3 tools/peer_consensus.py --help
```

## Required Inputs

Before you run it, define:
- the task
- acceptance criteria
- preferred scope

If the repo has important ignored files that the agents need, include them with `--include-path`.

## Standard Run

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "Replace the current API retry logic with a bounded exponential backoff implementation." \
  --acceptance "Do not change the public API." \
  --acceptance "Add or update targeted tests if they exist locally." \
  --scope src/api/client.ts \
  --scope src/api/client.test.ts
```

## Apply The Final Result Back To The Source Workspace

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task-file /tmp/task.md \
  --scope src/foo.ts \
  --apply-final
```

## Notes

- The script creates isolated workspaces under `.claude/tmp/peer-consensus/`.
- Independent paired phases such as initial solve, cross-review, revision, consensus, and sign-off run in parallel.
- Claude runs in `--bare` mode by default to reduce prompt contamination.
- The protocol is round-based, not free-form multi-agent chat.
- The script exits non-zero if the final candidate is not approved by both agents.

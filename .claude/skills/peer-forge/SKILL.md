---
name: peer-forge
description: |
  Productized entrypoint for the Peer Forge workflow. Use when the user wants Claude Code and Codex
  to work like two peer students: independent first draft, cross-review, revision, consensus merge,
  and final dual sign-off. Task-only usage is allowed. Acceptance criteria are optional.
---

# peer-forge

`peer-forge` is the simple skill-first entrypoint.

Use it when the user says things like:
- "让 Claude 和 Codex 都出方案，然后互相 review"
- "按平级学生做卷子的方式来"
- "用 peer-forge 跑一下这个需求"

## Default Rule

Do not force the user to write formal acceptance criteria.

Minimum input:
- a task

Optional input:
- scope paths
- hard constraints such as "do not change public API"
- whether to write the final approved result back with `--apply-final`

If the user only gives a task:
- run with `--task`
- omit `--acceptance`
- omit `--scope` unless the scope is obvious from the conversation or repo context

## What To Run

From a project root that already contains this toolkit:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "用户的原始任务"
```

If the user also gave scope:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "用户的原始任务" \
  --scope path/to/file1 \
  --scope path/to/file2
```

If the user gave hard constraints:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "用户的原始任务" \
  --acceptance "不要改 public API" \
  --acceptance "先不要动数据库层"
```

If the user wants the final approved result copied back:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "用户的原始任务" \
  --apply-final
```

## How To Think

The "验收机制" here is not only user-written acceptance criteria.

When explicit acceptance criteria are missing, the workflow still converges through:
- independent implementation
- cross-review
- revision
- consensus merge
- dual sign-off

That means task-only usage is valid.

## When To Use `peer-consensus` Instead

If the user wants maximum control over:
- explicit acceptance criteria
- explicit scope
- repeatable command-line runs

then `peer-consensus` is the lower-level workflow.

`peer-forge` is the easier front door.

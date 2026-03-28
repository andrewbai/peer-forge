---
name: peer-forge-live
description: |
  Run the live tmux-based Peer Forge workflow when the user wants to watch Claude Code and Codex
  side by side in long-lived interactive sessions, supervise the process in real time, and keep the
  two agents symmetric and isolated. This v1 mode is plan-only.
---

# peer-forge-live

Use this skill when the user wants:
- 两个交互式 session 并排跑
- 实时看到 Claude 和 Codex 各自输出
- 中间可以监督、看日志、加对称 note
- 保持双方是平级 agent，不做单边污染

This is the live counterpart to `peer-consensus`.

Current scope:
- plan-only
- no code writing
- no apply-final
- no asymmetric supervisor notes

## Requirements

- `tmux`
- `claude`
- `codex`
- `python3`
- `git`

## What To Run

Always use the launcher layer, not a direct Python path.

Global install:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务"
```

If the current project vendors `peer-forge` locally:

```bash
./.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务"
```

Optional inputs:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务" \
  --acceptance "不要改 public API" \
  --scope src/module \
  --watchdog-seconds 180 \
  --signoff-rounds 1
```

If the user wants the tmux session created without auto-attach:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务" \
  --no-attach
```

## Live Supervisor Commands

Inside the supervisor pane:

- `status`
- `tail claude`
- `tail codex`
- `inspect claude`
- `inspect codex`
- `note both`
- `wait`
- `continue`
- `abort`

Rules:
- `note both` is symmetric only.
- Notes are queued into the next turn for both agents equally.
- `continue` is used at phase boundaries after both sides finish a turn.

## When To Prefer `peer-consensus`

Use `peer-consensus` instead when the user wants:
- headless repeatable CLI runs
- structured JSON-only batch artifacts
- execution plus implementation review
- CI-friendly automation instead of live supervision

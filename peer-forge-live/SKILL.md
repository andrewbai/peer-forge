---
name: peer-forge-live
description: |
  Run the live tmux-based Peer Forge workflow when the user wants to watch Claude Code and Codex
  side by side in long-lived interactive sessions, supervise the process in real time, and keep the
  two agents symmetric and isolated across planning, execution, review, and signoff.
---

## Preamble (run first)

```bash
_UPD=""
if [ -x "./.claude/skills/peer-forge/bin/peer-forge-update-check" ]; then
  _UPD=$(./.claude/skills/peer-forge/bin/peer-forge-update-check 2>/dev/null || true)
elif [ -x "$HOME/.claude/skills/peer-forge/bin/peer-forge-update-check" ]; then
  _UPD=$("$HOME/.claude/skills/peer-forge/bin/peer-forge-update-check" 2>/dev/null || true)
fi
[ -n "$_UPD" ] && echo "$_UPD" || true
```

If output shows `UPGRADE_AVAILABLE <old> <new>`: read the installed `peer-forge-upgrade/SKILL.md` and follow the `Inline upgrade flow` section. Prefer the vendored install in `./.claude/skills/peer-forge/` when it exists; otherwise use `$HOME/.claude/skills/peer-forge/`. Auto-upgrade if enabled; otherwise ask whether to upgrade now, always auto-upgrade, snooze, or disable checks.

If output shows `JUST_UPGRADED <from> <to>`: tell the user `Running peer-forge v{to} (just updated!)` and continue.

# peer-forge-live

Use this skill when the user wants:
- 两个交互式 session 并排跑
- 实时看到 Claude 和 Codex 各自输出
- 中间可以监督、看日志、加对称 note
- 保持双方是平级 agent，不做单边污染

This is the live counterpart to `peer-consensus`.

Current scope:
- full plan -> execute -> review -> signoff workflow
- no asymmetric supervisor notes
- protocol automation after startup, but CLI-native safety/trust prompts remain manual on purpose
- protocol-level read-only enforcement on non-write phases
- package-based apply back into the target repo after approval

Phase order:
- independent plans
- cross-review
- revision
- consensus
- final plan
- plan signoff
- selected-side execution
- peer implementation review
- bounded execution fix/signoff rounds

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
  --task "用户的原始任务" \
  --no-attach \
  --open-ui
```

If the current project vendors `peer-forge` locally:

```bash
./.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务" \
  --no-attach \
  --open-ui
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
  --no-attach \
  --open-ui
```

If the user wants the detached JSON to include the local control token for direct API calls:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务" \
  --no-attach \
  --open-ui \
  --print-control-token
```

`peer-forge-live` 默认不会给 Claude 加 `--bare`，这样 Claude Max、OAuth 和 keychain 登录态可以在 live 交互会话里继续使用。

Only enable bare mode when explicitly needed:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "用户的原始任务" \
  --claude-bare
```

If the user already has a `state.json` and wants to re-attach or repair the supervisor pane:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json \
  --open-ui
```

If the user wants to preview or land an approved live run back into the repo:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

For a real apply plus git commit:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

If the user wants to control the landing branch explicitly:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch peer-forge/custom-branch
```

If the user wants to stay on the current branch instead of creating the default `peer-forge/<run-id>` branch:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch current
```

If the target repo has unrelated dirty paths and the user still wants to land the package:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-dirty-target
```

Important startup behavior:

- Do not promise a fully unattended start.
- The live protocol runs automatically after startup, but Claude's own `bypassPermissions` confirmation may still need a human to accept.
- Codex may also show a trust confirmation depending on local CLI state and workspace history.
- This is intentional. Do not implement tmux keypress hacks unless the user explicitly asks for that tradeoff.

Detached browser flow:

- `--no-attach` returns JSON with `run_id`, `session_name`, `run_dir`, `state_file`, `attach`, `control_url`, `events_stream_url`, and `web_url`.
- `--print-control-token` adds `control_token` to that JSON.
- `--open-ui` best-effort opens `web_url` in the default browser once the local control server is ready.
- Use the Web UI for timeline, artifacts, events, and boundary actions; keep tmux for trust prompts and raw pane inspection.

## Live Supervisor Commands

Inside the supervisor pane:

- `status`
- `tail claude`
- `tail codex`
- `inspect claude`
- `inspect codex`
- `show final-plan`
- `show package`
- `show diff`
- `show manifest`
- `note both`
- `wait`
- `continue`
- `abort`

Rules:
- `note both` is symmetric only.
- Notes are queued into the next turn for both agents equally.
- `continue` is used at phase boundaries after both sides finish a turn.
- `status` also shows executor/reviewer, plan/execution approval state, read-only violations, current package summary, and each pane's current mode.
- In the browser UI, `Status`, `Continue`, `Abort`, and the note form map to these same supervisor actions.

Live apply rules:
- `apply` without `--apply` is dry-run only.
- Actual repo writes require `--apply`.
- The default landing branch is `peer-forge/<run-id>`.
- Apply is allowed only after approved plan and approved execution.
- Apply currently requires a git-backed target repo.
- Non-overlap base drift is allowed automatically.
- Unrelated dirty target paths require `--allow-dirty-target`.
- Overlap between target drift/dirty paths and package paths stays blocked by default.
- `--allow-base-drift` is the explicit override when drift overlaps package paths.

## When To Prefer `peer-consensus`

Use `peer-consensus` instead when the user wants:
- headless repeatable CLI runs
- structured JSON-only batch artifacts
- CI-friendly automation instead of live supervision

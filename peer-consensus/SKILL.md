---
name: peer-consensus
description: |
  Run a full dual-agent consensus workflow with Claude Code and Codex: isolated workspaces,
  plan consensus first, then single-agent execution plus peer review.
  Use when the user wants two peer coding agents that do not contaminate each other's initial work.
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

# peer-consensus

Use this skill when the user wants Claude Code and Codex to behave like two peer students:
- both produce plans independently
- both review and revise plans
- both converge on one final plan
- one side executes the chosen plan
- the other side reviews the implementation result

This skill is powered by:

```bash
~/.claude/skills/peer-forge/bin/peer-consensus --help
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
~/.claude/skills/peer-forge/bin/peer-consensus \
  --repo . \
  --task "Replace the current API retry logic with a bounded exponential backoff implementation." \
  --acceptance "Do not change the public API." \
  --acceptance "Add or update targeted tests if they exist locally." \
  --scope src/api/client.ts \
  --scope src/api/client.test.ts
```

## Apply The Final Result Back To The Source Workspace

```bash
~/.claude/skills/peer-forge/bin/peer-consensus \
  --repo . \
  --task-file /tmp/task.md \
  --scope src/foo.ts \
  --apply-final
```

If this toolkit is vendored inside the current project instead of being installed globally, replace `~/.claude/skills/peer-forge` with `./.claude/skills/peer-forge`.

## Notes

- The script creates isolated workspaces under `.claude/tmp/peer-consensus/`.
- The plan phases are read-only: plan, cross-review, revision, and consensus.
- The execution phase is single-writer: the chosen side writes code, the other side reviews.
- Independent paired plan phases run in parallel.
- Claude runs in `--bare` mode by default to reduce prompt contamination.
- The protocol is round-based, not free-form multi-agent chat.
- The script exits non-zero if the implementation review does not reach approval.

If the user wants live side-by-side panes with long-lived interactive sessions instead of a headless batch run, use `peer-forge-live`.

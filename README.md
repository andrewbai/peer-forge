# Peer Forge

[中文说明](./README.zh-CN.md)

Standalone toolkit for running a dual-agent coding workflow with Claude Code and Codex:

- isolated workspaces
- independent planning
- plan cross-review
- plan revision
- plan consensus
- one-sided execution
- peer implementation review

This repo is designed to install cleanly as a Claude Code skill pack while still exposing a direct CLI launcher.

Current version: `v0.5.0`

## Structure

```text
peer-forge/
├── SKILL.md
├── peer-consensus/
│   └── SKILL.md
├── codex-collab/
│   ├── SKILL.md
│   └── scripts/
│       └── codex-headless-collab.sh
├── tools/
│   └── peer_consensus.py
├── bin/
│   ├── peer-forge
│   └── peer-consensus
├── setup
├── uninstall
├── README.md
├── README.zh-CN.md
├── CHANGELOG.md
├── GITHUB_METADATA.md
├── LICENSE
└── VERSION
```

## Install

### Global (recommended)

```bash
git clone git@github.com:andrewbai/peer-forge.git ~/.claude/skills/peer-forge
~/.claude/skills/peer-forge/setup
```

After that, Claude Code can discover:

- `/peer-forge`
- `/peer-consensus`
- `/codex-collab`

If Claude Code was already open, restart it once so it reloads the skills.

### Project-Local Vendoring

From the target project root:

```bash
git clone git@github.com:andrewbai/peer-forge.git .claude/skills/peer-forge
./.claude/skills/peer-forge/setup --local
```

This keeps the whole repo inside the project, then registers the sibling skill links:

- `.claude/skills/peer-consensus -> peer-forge/peer-consensus`
- `.claude/skills/codex-collab -> peer-forge/codex-collab`

### Standalone CLI

Global install:

```bash
~/.claude/skills/peer-forge/bin/peer-forge \
  --repo /path/to/project \
  --task "Implement the change." \
  --apply-final
```

Project-local vendoring:

```bash
./.claude/skills/peer-forge/bin/peer-forge \
  --repo . \
  --task "Implement the change." \
  --apply-final
```

## Requirements

- `claude` CLI installed and logged in
- `codex` CLI installed and logged in
- Python 3 available as `python3`
- Git available as `git`

Quick checks:

```bash
claude -v
codex -V
python3 --version
git --version
```

## Fastest Usage

If you do not want to spell out the full CLI, use the main skill:

- `/peer-forge 处理这个任务：...`
- `/peer-forge refactor this module so Claude Code and Codex draft independently, review each other, and converge`

`peer-forge` is the easier front door:

- task-only usage is valid
- acceptance criteria are optional
- scope is optional

## What The Workflow Does

1. Creates isolated Claude and Codex workspaces.
2. Runs independent planning phases in parallel when possible.
3. Makes each side review and revise the other side's plan.
4. Uses consensus to choose the final plan base.
5. Produces one final implementation plan.
6. Lets the chosen side execute that plan.
7. Lets the other side review the implementation result, with bounded fix-review rounds if needed.

## Artifacts

Each run writes artifacts under the target repo:

```text
<target-repo>/.claude/tmp/peer-consensus/<run-id>/
```

That directory includes:

- task and config
- isolated workspaces
- prompts
- model outputs
- per-stage diff packages
- final plan
- implementation review results
- `report.json`
- `report.md`

## Skills

### `peer-forge`

The simpler front door. Use this when the user wants the workflow without thinking in terms of detailed CLI flags. Task-only usage is valid.

### `peer-consensus`

The lower-level workflow when you want more explicit control over task, acceptance criteria, scope, and the exact two-phase protocol.

### `codex-collab`

Smaller helper for Claude-driven headless Codex collaboration on a bounded file. This is faster, but it is not the same as the full peer workflow.

## Notes

- The repo itself is the main skill. `~/.claude/skills/peer-forge/SKILL.md` is directly discoverable by Claude Code.
- Sub-skills are registered as sibling symlinks by `setup`.
- All skill docs route through the `bin/` launchers rather than calling the Python entrypoint directly.
- The main script exits non-zero if the final candidate does not get dual approval.

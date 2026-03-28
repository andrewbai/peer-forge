# Peer Consensus Toolkit

[中文说明](./README.zh-CN.md)

Standalone toolkit for running a dual-agent coding workflow with Claude Code and Codex:

- isolated workspaces
- independent planning
- plan cross-review
- plan revision
- plan consensus
- one-sided execution
- peer implementation review

This repo is meant to live on its own and be synced into future projects when needed.

Current version: `v0.2.0`

## Structure

```text
peer-consensus-toolkit/
├── .claude/
│   └── skills/
│       ├── peer-forge/
│       │   └── SKILL.md
│       ├── peer-consensus/
│       │   └── SKILL.md
│       └── codex-collab/
│           ├── SKILL.md
│           └── scripts/
│               └── codex-headless-collab.sh
├── tools/
│   └── peer_consensus.py
└── README.md
```

## Fastest Skill-First Usage

If you prefer the skill route over manually composing arguments, use `peer-forge`.

`peer-forge` is the easier entrypoint:

- task is required
- acceptance criteria are optional
- scope is optional

Example intent:

- `Use the peer-forge skill for this task`
- `/peer-forge refactor this module so Claude Code and Codex draft independently, review each other, and converge`

Under the hood, the skill still runs `tools/peer_consensus.py`, but it treats task-only usage as normal.

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

## Usage Modes

### 1. Standalone Against Any Project

You can keep this toolkit in its own repo and point it at another codebase:

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task "Implement the requested change."
```

Add `--scope` and `--acceptance` only when needed:

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task "Implement the requested change." \
  --acceptance "Do not break the public API." \
  --scope src/example.ts
```

If both agents approve the final result and you want to write it back to the target project:

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task-file /path/to/task.md \
  --scope src/example.ts \
  --apply-final
```

### 2. Sync Into a Specific Project

Copy these paths into the root of the target project:

- `.claude/skills/peer-forge/`
- `.claude/skills/peer-consensus/`
- `.claude/skills/codex-collab/`
- `tools/peer_consensus.py`

Then use either the skill-first path or the script:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "Implement the requested change."
```

Add `--scope` and `--acceptance` only when useful.

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

The simpler front door. Use this when the user wants the workflow but does not want to think in terms of detailed CLI flags.

Task-only usage is valid.

### `peer-consensus`

The lower-level workflow when you want more explicit control over task, acceptance criteria, scope, and the exact two-phase protocol.

### `codex-collab`

Smaller helper for Claude-driven headless Codex collaboration on a bounded file. This is faster, but it is not the same as the full peer workflow.

## Publishing To GitHub

Typical flow:

```bash
cd /Users/andrew/Desktop/peer-consensus-toolkit
git init
git add .
git commit -m "Initial peer consensus toolkit"
```

Then create your GitHub repo and push as usual.

## Notes

- The toolkit does not require living inside a specific project.
- The skills are kept in `.claude/skills/` so their relative paths still work when you sync them into a project root.
- The main script exits non-zero if the final candidate does not get dual approval.

# Peer Consensus Toolkit

[中文说明](./README.zh-CN.md)

Standalone toolkit for running a dual-agent coding workflow with Claude Code and Codex:

- isolated workspaces
- independent initial solutions
- cross-review
- revision
- consensus merge
- final sign-off from both sides

This repo is meant to live on its own and be synced into future projects when needed.

## Structure

```text
peer-consensus-toolkit/
├── .claude/
│   └── skills/
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

- `.claude/skills/peer-consensus/`
- `.claude/skills/codex-collab/`
- `tools/peer_consensus.py`

Then run from that project root:

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "Implement the requested change." \
  --acceptance "Do not break the public API." \
  --scope src/example.ts
```

## What The Workflow Does

1. Creates isolated Claude and Codex workspaces.
2. Runs independent paired phases in parallel when possible.
3. Makes each side review the other side's work.
4. Lets each side revise after review.
5. Asks both sides to state what must be preserved and what still blocks approval.
6. Builds a final candidate from the stronger base.
7. Requires sign-off from both sides, with bounded objection/fix rounds.

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
- sign-off results
- `report.json`
- `report.md`

## Skills

### `peer-consensus`

Claude skill wrapper for the full dual-agent protocol. It is the main entry point when you want:

- two peer agents
- initial non-contamination
- review and convergence
- final dual sign-off

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

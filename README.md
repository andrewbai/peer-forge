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

Current version: `v0.9.0`

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
├── peer-forge-upgrade/
│   └── SKILL.md
├── tools/
│   └── peer_consensus.py
├── bin/
│   ├── peer-forge
│   ├── peer-consensus
│   └── peer-forge-upgrade
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
- `/peer-forge-upgrade`

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

Useful runtime flags:

- `--agent-timeout-seconds 1800` sets a per-stage Claude/Codex timeout. Use `0` to disable it.
- `--supervise` streams Claude/Codex output to the terminal and writes prefixed verbose logs without changing the protocol.
- `--supervise-checkpoints` adds interactive stage-boundary checkpoints on top of `--supervise`. It supports `continue`, `inspect`, `retry`, `note`, and `abort`, and requires `--task` or `--task-file`.
- `--cleanup-workspaces` removes the temporary isolated workspaces after the run.
- `--keep-workspaces` keeps those isolated workspaces even when cleanup is enabled. `--keep-run-dir` remains as a deprecated alias.

Upgrade the installed checkout:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade
```

Check whether an upgrade is available without modifying the checkout:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade --check
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
- `progress.log`
- `supervisor.log` when `--supervise` is enabled
- `checkpoints/history.jsonl` when `--supervise-checkpoints` is enabled
- per-checkpoint retry records such as `checkpoints/01-plan-initial-retry-01.json`
- `notes/history.jsonl` when supervisor notes are added
- isolated workspaces
- prompts
- model outputs
- per-stage diff packages
- per-stage retry artifacts under `<stage-dir>/retries/`
- final plan
- implementation review results
- `report.json`
- `report.md`

Runtime behavior:

- Progress logs stream to `stderr`, so the terminal shows which phase is currently running.
- The same progress lines are also written to `progress.log` inside the run directory.
- With `--supervise`, Claude/Codex stdout and stderr are streamed live to the terminal with prefixes and mirrored into a run-level `supervisor.log`.
- With `--supervise`, each stage also gets a prefixed `<stage-dir>/verbose.log` alongside the raw `stdout.txt` and `stderr.txt`.
- With `--supervise-checkpoints`, the run pauses after each major stage boundary. You can `continue`, `inspect`, `retry`, `note`, or `abort` without changing the agent protocol or workspace isolation.
- `retry` reruns the current checkpoint stage safely. Parallel plan checkpoints retry both agents together. Write stages restore their stage-entry snapshot before each retry. `apply-final` remains inspect/continue/abort only.
- `note` records a symmetric supervisor note that is injected into both agents' later prompts. Multi-line note entry ends with a line containing only `---`. Notes added at a checkpoint do not affect retries of that same checkpoint.
- `inspect` prints the current stage artifact paths, active supervisor notes, retry attempt summaries, `parsed.json`, `stdout.txt`, `stderr.txt`, `verbose.log` when present, and diff package paths for write stages.
- Final machine-readable output stays on `stdout` as JSON.
- `report.json` and `report.md` are written for completed, aborted, and failed runs.
- `report.json` includes `progress_log`, `supervisor_log`, `checkpoint_history`, `checkpoint_events`, `retry_attempts`, `notes_history`, `supervisor_notes`, and structured `stage_timings` entries for each Claude/Codex stage.

## Skills

### `peer-forge`

The simpler front door. Use this when the user wants the workflow without thinking in terms of detailed CLI flags. Task-only usage is valid.

### `peer-consensus`

The lower-level workflow when you want more explicit control over task, acceptance criteria, scope, and the exact two-phase protocol.

### `codex-collab`

Smaller helper for Claude-driven headless Codex collaboration on a bounded file. This is faster, but it is not the same as the full peer workflow.

### `peer-forge-upgrade`

Self-upgrade helper for refreshing the installed `peer-forge` checkout through the packaged upgrade script and re-running `setup`.

## Notes

- The repo itself is the main skill. `~/.claude/skills/peer-forge/SKILL.md` is directly discoverable by Claude Code.
- Sub-skills are registered as sibling symlinks by `setup`.
- All skill docs route through the `bin/` launchers rather than calling the Python entrypoint directly.
- Exit codes: `0` = approved final result, `1` = runtime failure, `2` = run completed but the final candidate was not approved.

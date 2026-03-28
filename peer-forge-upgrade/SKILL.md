---
name: peer-forge-upgrade
description: |
  Upgrade the installed Peer Forge skill pack in place. Use when the user wants to update
  their global Claude Code install, or a vendored project-local install, using the packaged
  upgrade script instead of hand-written git commands.
---

# peer-forge-upgrade

Use this skill when the user says things like:
- "升级 peer-forge"
- "更新这个 skill 包"
- "pull 一下最新版本"
- "/peer-forge-upgrade"

## Default Rule

Default to upgrading the global install at `~/.claude/skills/peer-forge/bin/peer-forge-upgrade`.

If the user explicitly says to upgrade the vendored copy inside the current project, use the local path instead:

- `./.claude/skills/peer-forge/bin/peer-forge-upgrade`

If the global install path does not exist but the current project has a vendored install, upgrade the vendored install instead of failing immediately.

## Global Upgrade

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade
```

## Project-Local Upgrade

```bash
./.claude/skills/peer-forge/bin/peer-forge-upgrade
```

## Check Without Upgrading

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade --check
```

## What To Report

After the upgrade:
- report the new HEAD commit
- mention whether `setup` re-registered any skill links
- say if Claude Code should be restarted to reload skills

## Failure Modes

If the install path does not exist:
- say that Peer Forge is not installed at that location
- tell the user which install command to run first

If `git pull --ff-only` fails because of local modifications:
- stop
- report that the install has local changes
- tell the user to inspect the diff before upgrading

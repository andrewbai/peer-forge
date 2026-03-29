---
name: peer-forge-upgrade
description: |
  Upgrade the installed Peer Forge skill pack in place. Use when the user wants to update
  their global Claude Code install, or a vendored project-local install, using the packaged
  upgrade script instead of hand-written git commands.
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

If output shows `UPGRADE_AVAILABLE <old> <new>`: follow the `Inline upgrade flow` section below. Prefer the vendored install in `./.claude/skills/peer-forge/` when it exists; otherwise use `$HOME/.claude/skills/peer-forge/`.

If output shows `JUST_UPGRADED <from> <to>`: tell the user `Running peer-forge v{to} (just updated!)` and continue.

# peer-forge-upgrade

## Inline upgrade flow

This section is referenced by the preambles of the other Peer Forge skills when they detect `UPGRADE_AVAILABLE`.

State lives in `~/.peer-forge/`:

- `auto-upgrade-enabled`
- `update-check-disabled`
- `update-snoozed`
- `just-upgraded-from`

### Step 1: Detect which install is active

Prefer the vendored install in the current repo when it exists. Otherwise fall back to the global install.

```bash
PF_ROOT=""
if [ -x "./.claude/skills/peer-forge/bin/peer-forge-upgrade" ]; then
  PF_ROOT="./.claude/skills/peer-forge"
elif [ -x "$HOME/.claude/skills/peer-forge/bin/peer-forge-upgrade" ]; then
  PF_ROOT="$HOME/.claude/skills/peer-forge"
fi
PF_UPGRADE_BIN="$PF_ROOT/bin/peer-forge-upgrade"
echo "PF_ROOT=$PF_ROOT"
```

If `PF_ROOT` is empty, tell the user Peer Forge is not installed at either location and stop.

### Step 2: Auto-upgrade or ask

```bash
PF_STATE_DIR="${PEER_FORGE_STATE_DIR:-$HOME/.peer-forge}"
PF_AUTO=$([ -f "$PF_STATE_DIR/auto-upgrade-enabled" ] && echo "true" || echo "false")
echo "AUTO_UPGRADE=$PF_AUTO"
```

If `AUTO_UPGRADE=true`, tell the user `Auto-upgrading peer-forge v{old} -> v{new}...` and continue directly to Step 3.

Otherwise ask the user:

- `Upgrade now`
- `Always auto-upgrade`
- `Not now`
- `Never ask again`

If the user chooses `Always auto-upgrade`, run:

```bash
PF_STATE_DIR="${PEER_FORGE_STATE_DIR:-$HOME/.peer-forge}"
mkdir -p "$PF_STATE_DIR"
touch "$PF_STATE_DIR/auto-upgrade-enabled"
rm -f "$PF_STATE_DIR/update-check-disabled"
```

Then continue to Step 3.

If the user chooses `Not now`, write a snooze record with escalating backoff:

```bash
PF_STATE_DIR="${PEER_FORGE_STATE_DIR:-$HOME/.peer-forge}"
mkdir -p "$PF_STATE_DIR"
PF_SNOOZE_FILE="$PF_STATE_DIR/update-snoozed"
PF_REMOTE_VER="{new}"
PF_CUR_LEVEL=0
if [ -f "$PF_SNOOZE_FILE" ]; then
  PF_SNOOZED_VER=$(awk '{print $1}' "$PF_SNOOZE_FILE" 2>/dev/null || true)
  if [ "$PF_SNOOZED_VER" = "$PF_REMOTE_VER" ]; then
    PF_CUR_LEVEL=$(awk '{print $2}' "$PF_SNOOZE_FILE" 2>/dev/null || true)
    case "$PF_CUR_LEVEL" in *[!0-9]*) PF_CUR_LEVEL=0 ;; esac
  fi
fi
PF_NEW_LEVEL=$((PF_CUR_LEVEL + 1))
[ "$PF_NEW_LEVEL" -gt 3 ] && PF_NEW_LEVEL=3
echo "$PF_REMOTE_VER $PF_NEW_LEVEL $(date +%s)" > "$PF_SNOOZE_FILE"
```

Tell the user the next reminder window:

- level 1: 24 hours
- level 2: 48 hours
- level 3: 7 days

Then continue with the current skill without upgrading.

If the user chooses `Never ask again`, run:

```bash
PF_STATE_DIR="${PEER_FORGE_STATE_DIR:-$HOME/.peer-forge}"
mkdir -p "$PF_STATE_DIR"
touch "$PF_STATE_DIR/update-check-disabled"
rm -f "$PF_STATE_DIR/auto-upgrade-enabled"
```

Tell the user update checks are disabled and continue with the current skill.

### Step 3: Run the upgrade

```bash
"$PF_UPGRADE_BIN"
```

After the upgrade:

- report the new version and commit from the script output
- mention that the checker writes a `just-upgraded-from` marker for the next skill load
- tell the user to restart Claude Code if it is already open so refreshed skills reload cleanly

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

## Fast Version Check

```bash
~/.claude/skills/peer-forge/bin/peer-forge-update-check --force
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

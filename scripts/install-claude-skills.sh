#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
claude_root="$HOME/.claude"
claude_skills_dir="$claude_root/skills"
toolkit_link="$HOME/.peer-forge"

require_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "ERROR: required path not found: $path" >&2
    exit 1
  fi
}

link_path() {
  local src="$1"
  local dst="$2"

  if [[ -L "$dst" ]]; then
    local current
    current="$(readlink "$dst")"
    if [[ "$current" == "$src" ]]; then
      echo "OK: $dst -> $src"
      return
    fi
    rm "$dst"
  elif [[ -e "$dst" ]]; then
    echo "ERROR: $dst already exists and is not a symlink. Move it away and rerun." >&2
    exit 1
  fi

  ln -s "$src" "$dst"
  echo "LINK: $dst -> $src"
}

print_check() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "FOUND: $name -> $(command -v "$name")"
  else
    echo "WARN: $name not found in PATH"
  fi
}

require_path "$repo_root/.claude/skills/peer-forge/SKILL.md"
require_path "$repo_root/.claude/skills/peer-consensus/SKILL.md"
require_path "$repo_root/.claude/skills/codex-collab/SKILL.md"
require_path "$repo_root/tools/peer_consensus.py"
require_path "$repo_root/bin/peer-forge"
require_path "$repo_root/bin/peer-consensus"

mkdir -p "$claude_skills_dir"

link_path "$repo_root" "$toolkit_link"
link_path "$toolkit_link/.claude/skills/peer-forge" "$claude_skills_dir/peer-forge"
link_path "$toolkit_link/.claude/skills/peer-consensus" "$claude_skills_dir/peer-consensus"
link_path "$toolkit_link/.claude/skills/codex-collab" "$claude_skills_dir/codex-collab"

print_check claude
print_check codex
print_check python3
print_check git

cat <<EOF

Peer Forge is installed for Claude Code.

Installed links:
- $toolkit_link
- $claude_skills_dir/peer-forge
- $claude_skills_dir/peer-consensus
- $claude_skills_dir/codex-collab

Use in Claude Code:
- /peer-forge 处理这个任务：...
- /peer-consensus 按完整双 Agent 共识协议处理这个任务：...

Direct CLI:
- $toolkit_link/bin/peer-forge --repo /path/to/project --task "Implement the change." --apply-final
- $toolkit_link/bin/peer-consensus --repo /path/to/project --task "Implement the change." --apply-final

If Claude Code was already open, restart it once so the new skills are reloaded.
EOF

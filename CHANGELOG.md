# Changelog

## v0.4.0 - 2026-03-28

Claude Code installation flow and global launchers.

- Added `bin/peer-forge` and `bin/peer-consensus` launcher scripts so the toolkit can run from a stable global path.
- Added `scripts/install-claude-skills.sh` to register the toolkit under `~/.peer-forge` and install the skills into `~/.claude/skills/`.
- Updated the skill docs and both README files to distinguish global Claude Code installation from project-local vendoring.

## v0.3.0 - 2026-03-28

CLI cleanup and plan-stage optimization.

- Renamed the public round-control flag to `--review-rounds` and kept `--signoff-rounds` as a deprecated alias.
- Removed the duplicate `peer-consensus` heading from the Chinese README.
- Skipped empty diff/package collection for read-only plan and review stages.

## v0.2.0 - 2026-03-28

Workflow redesign and skill-first simplification.

- Changed the main protocol to: plan consensus first, then single-agent execution plus peer review.
- Added the `peer-forge` productized skill as the simpler front door.
- Updated the English and Chinese README files to reflect task-only skill-first usage.
- Updated the orchestrator prompts, schemas, reports, and review flow to match the new protocol.

## v0.1.0 - 2026-03-28

Initial public toolkit release.

- Added the standalone `peer_consensus.py` orchestrator for Claude Code and Codex peer workflow.
- Added the `peer-consensus` Claude skill wrapper for the full dual-agent protocol.
- Added the `codex-collab` helper skill for bounded headless Codex collaboration.
- Added English and Chinese README files for GitHub publishing and reuse across projects.
- Added isolated-workspace, dual-drafting, cross-review, revision, consensus, and dual sign-off workflow support.

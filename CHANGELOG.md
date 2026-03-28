# Changelog

## v0.7.0 - 2026-03-28

Script-backed upgrade flow.

- Added `bin/peer-forge-upgrade` as the real upgrade engine behind the self-upgrade skill.
- Switched `peer-forge-upgrade` to call the packaged upgrade script instead of embedding raw git commands in the skill doc.
- Added `--check` mode to the upgrade script so users can see whether an update is available without modifying the checkout.
- Made `setup` validate and advertise the new CLI upgrade script alongside the existing launchers.

## v0.6.0 - 2026-03-28

Self-upgrade skill and dynamic skill registration output.

- Added the new `peer-forge-upgrade` skill so Claude Code can upgrade the installed `peer-forge` checkout in place.
- Updated `setup` to validate the new skill and print the installed skill list dynamically instead of hardcoding it.
- Updated the docs to surface `/peer-forge-upgrade` as a first-class command.

## v0.5.1 - 2026-03-28

Path consistency and safer install tooling.

- Updated `codex-collab` docs to reference the helper script through `~/.claude/skills/peer-forge/` like the other skills.
- Added exclusion guards to `setup` and `uninstall` so unexpected directories such as `docs/` or `test/` are not treated as installable skills.
- Added an `uninstall --force` escape hatch and a default confirmation prompt before deleting the installed repo.

## v0.5.0 - 2026-03-28

Root-skill repo layout and gstack-style installation flow.

- Promoted `peer-forge` to the repo root with `SKILL.md` as the main skill entrypoint.
- Moved `peer-consensus` and `codex-collab` to root-level skill directories and removed the old `.claude/skills/` nesting from the repo layout.
- Replaced the previous installer with a single root `setup` script and added a matching `uninstall` script.
- Switched the documented install flow to clone directly into `.claude/skills/peer-forge` globally or locally, then register sibling skill symlinks.
- Unified skill execution docs around the `bin/peer-forge` and `bin/peer-consensus` launchers.

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

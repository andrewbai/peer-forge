# Changelog

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

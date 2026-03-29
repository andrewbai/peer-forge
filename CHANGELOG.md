# Changelog

## Unreleased

Nothing yet.

## v0.14.1 - 2026-03-29

Live Claude auth fix for `peer-forge-live`.

- Changed `peer-forge-live` so Claude bare mode is now opt-in instead of opt-out.
- Replaced `--no-claude-bare` with `--claude-bare` in the live CLI.
- Defaulted live Claude sessions to preserve Claude Max, OAuth, and keychain-backed auth.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs to document the new live bare-mode behavior.

## v0.14.0 - 2026-03-29

Conflict-aware live apply.

- Changed `peer-forge-live apply` from whole-repo dirty/drift blocking to path-level overlap analysis.
- Allowed non-overlap base drift automatically while continuing to block overlapping drift unless `--allow-base-drift` is set.
- Added `--allow-dirty-target` for unrelated dirty worktree paths while keeping overlapping dirty paths blocked.
- Expanded apply reports with dirty/drift path sets, overlap summaries, safe paths, blocked paths, and decision metadata.
- Extended `scripts/live-apply-smoke.sh` to cover safe apply, non-overlap drift, non-overlap dirty targets, and overlap blocking.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs for the new conflict-aware apply rules.

## v0.13.1 - 2026-03-29

Live apply skill-documentation completeness fix.

- Added `peer-forge-live` skill examples for explicit landing branches via `--branch ...` and `--branch current`.
- Documented the baseline drift safety check and the `--allow-base-drift` override in `peer-forge-live/SKILL.md`.

## v0.13.0 - 2026-03-29

Live apply / landing from approved execution packages.

- Added `peer-forge-live apply --state-file ...` with dry-run preview by default and explicit `--apply` for real repo writes.
- Landed approved live runs from the persisted execution package instead of trusting the live agent workspaces directly.
- Added safety checks for clean git-backed target repos, expected baseline HEAD, default `peer-forge/<run-id>` landing branches, and optional post-apply commits.
- Added per-apply audit artifacts under `apply/`, including `history.jsonl`, JSON reports, Markdown reports, and main live report integration.
- Added `scripts/live-apply-smoke.sh` to verify preview, apply+commit, and base-drift rejection behavior.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs for the new apply flow and safety model.

## v0.12.0 - 2026-03-29

Live supervisor recovery and richer inspection.

- Added `peer-forge-live resume --state-file ...` so an existing tmux run can re-attach cleanly and recreate or respawn the supervisor pane without disturbing the live Claude/Codex panes.
- Reworked the live supervisor flow to resume from persisted state, including in-flight turns and boundary pauses, instead of assuming the run always starts from turn 1.
- Expanded live supervisor commands with `show final-plan`, `show package`, `show diff`, and `show manifest`, and made `status` surface executor/reviewer, approval state, read-only violations, package summary, and pane modes.
- Added `scripts/live-smoke.sh` to smoke test detached live startup plus supervisor-pane kill-and-resume recovery.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs for the new resume flow, richer supervisor inspection, and smoke harness.

## v0.11.0 - 2026-03-29

Live startup behavior clarified and hardened.

- Kept live-mode protocol automation while explicitly leaving CLI-native safety/trust confirmations as manual supervisor actions.
- Switched live Claude sessions to `--permission-mode bypassPermissions` so tool-level prompts are minimized once the human confirms startup.
- Kept live Codex startup on the interactive CLI path and documented that workspace trust remains a manual confirmation point there.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs to document the expected manual confirmation points instead of pretending they are fully unattended.

Live workflow expanded beyond plan-only.

- Added execution-phase schemas and prompt builders for live execution, implementation review, execution fixes, and implementation signoff.
- Extended `peer-forge-live` to continue from approved plan consensus into selected-side execution, peer review, package collection, and bounded fix/signoff rounds.
- Switched live Codex sessions to a write-capable sandbox and enforced non-write phases at the protocol layer with read-only workspace snapshots and violation reporting.
- Added live execution package records plus richer report fields for selected executor/reviewer, current execution package, and read-only violations.
- Updated the English README, Chinese README, and `peer-forge-live` skill docs to describe the full live workflow instead of the earlier plan-only scope.

## v0.10.0 - 2026-03-29

Live tmux supervision and persistent plan sessions.

- Added `peer-forge-live`, a separate tmux-based live workflow with long-lived Claude and Codex interactive sessions.
- Added `tools/peer_live.py`, `tools/live_tmux.py`, and `tools/live_protocol.py` for live plan-stage orchestration, tmux pane control, and structured result parsing.
- Added symmetric live supervisor commands for status, tail, inspect, queued `note both`, boundary `continue`, and `abort`.
- Added live-run artifacts under `.claude/tmp/peer-forge-live/`, including `state.json`, `supervisor.log`, raw pane logs, combined `panes/verbose.log`, per-turn prompts/results, and final reports.
- Added the `peer-forge-live` skill, launcher, setup validation, and English/Chinese documentation for the new live plan-only mode.

Supervisor notes and safe checkpoint retries.

- Added symmetric supervisor `note` support so humans can inject later-phase guidance without polluting already-completed stages.
- Added checkpoint `retry` support with safe stage-entry restoration for write phases and isolated retry artifact directories.
- Added per-retry checkpoint JSON records plus `retry_attempts` metadata in `report.json` and `report.md`.
- Updated the English and Chinese docs for checkpoint retry behavior, note semantics, and retry artifacts.

## v0.9.0 - 2026-03-29

Supervisor visibility and checkpoint control.

- Added `--supervise` for live Claude/Codex streaming to the terminal plus run-level `supervisor.log` and per-stage `verbose.log`.
- Added stage summaries, structured supervise metadata, and supervisor log paths to `report.json` and `report.md`.
- Added `--supervise-checkpoints` for stage-boundary `continue`, `inspect`, and `abort` control without changing the peer-consensus protocol.
- Added checkpoint audit artifacts under `checkpoints/`, including per-checkpoint JSON files and `history.jsonl`.
- Updated the English and Chinese docs for supervision modes, runtime artifacts, and aborted-run reporting.

## v0.8.0 - 2026-03-28

Runtime observability and failure reporting.

- Added live progress logging with phase boundaries and per-stage status output on `stderr`.
- Added `--agent-timeout-seconds` so Claude and Codex stages do not hang forever by default.
- Added failure-mode `report.json` and `report.md` generation plus `failure-traceback.txt`.
- Renamed `--keep-run-dir` to `--keep-workspaces` and kept the old flag as a deprecated alias.
- Added persisted `progress.log` output and structured `stage_timings` entries in `report.json`.
- Updated the English and Chinese docs for runtime flags, artifacts, and exit codes.

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

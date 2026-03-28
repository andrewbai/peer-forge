# GitHub Metadata

Suggested repository name:

- `peer-forge`

Suggested description:

- `Dual-agent coding workflow for plan consensus first, then single-agent execution and peer review.`

Alternative product-style tagline:

- `Run two coding agents as peers: isolate, review, converge, and ship one stronger result.`

Suggested topics:

- `ai-coding`
- `multi-agent`
- `claude-code`
- `codex`
- `code-review`
- `developer-tools`
- `automation`
- `llm`
- `orchestration`
- `prompt-engineering`

Suggested short pitch:

- `A toolkit for making coding agents work like peer students, not master and assistant.`

Release tag:

- `v0.8.0`

Suggested release title:

- `v0.8.0: Runtime observability and failure reporting`

Suggested release notes (English):

````md
## Highlights

- Added live progress logging with phase boundaries and per-stage status output on `stderr`.
- Added `--agent-timeout-seconds` so Claude and Codex stages do not hang forever by default.
- Added failure-mode `report.json` and `report.md` generation plus `failure-traceback.txt`.
- Renamed `--keep-run-dir` to `--keep-workspaces` and kept the old flag as a deprecated alias.
- Added persisted `progress.log` output and structured `stage_timings` entries in `report.json`.

## Highlights

Artifacts now include:

```bash
<target-repo>/.claude/tmp/peer-consensus/<run-id>/progress.log
```

Structured timing data is now available in `report.json`:

```json
{
  "progress_log": ".../progress.log",
  "stage_timings": [
    {
      "phase": "plan-initial",
      "agent": "claude",
      "status": "completed",
      "duration_seconds": 3.12
    }
  ]
}
```
````

Suggested release notes (中文):

````md
## 亮点

- 新增实时进度日志，终端可以看到分阶段进度、每个 stage 的开始、完成和失败状态。
- 新增 `--agent-timeout-seconds`，避免 Claude / Codex 某个阶段无限卡住。
- 新增失败态 `report.json`、`report.md` 和 `failure-traceback.txt`。
- 将 `--keep-run-dir` 更名为 `--keep-workspaces`，同时保留旧参数作为废弃别名。
- 新增持久化的 `progress.log` 和 `report.json` 里的结构化 `stage_timings` 阶段耗时信息。

## 亮点

现在运行产物里会包含：

```bash
<target-repo>/.claude/tmp/peer-consensus/<run-id>/progress.log
```

`report.json` 里现在也会有结构化的耗时信息：

```json
{
  "progress_log": ".../progress.log",
  "stage_timings": [
    {
      "phase": "plan-initial",
      "agent": "claude",
      "status": "completed",
      "duration_seconds": 3.12
    }
  ]
}
```
````

Notes:

- Repository description and topics must be set in the GitHub UI or via authenticated GitHub API access.
- License selection is intentionally left separate because it is a legal choice, not a tooling default.

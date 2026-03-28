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

- `v0.7.0`

Suggested release title:

- `v0.7.0: Script-backed upgrade flow`

Suggested release notes (English):

````md
## Highlights

- Added `bin/peer-forge-upgrade` as the real upgrade engine behind the self-upgrade skill.
- Switched `/peer-forge-upgrade` to call the packaged script instead of embedding raw git commands in the skill doc.
- Added `--check` mode so users can see whether an update is available without modifying the checkout.
- Updated `setup` to validate and advertise the new upgrade launcher alongside the existing CLI entrypoints.

## Upgrade

Global install:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade
```

Check without upgrading:

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade --check
```

Project-local vendored install:

```bash
./.claude/skills/peer-forge/bin/peer-forge-upgrade
```
````

Suggested release notes (中文):

````md
## 亮点

- 新增 `bin/peer-forge-upgrade`，作为自升级能力的真实执行入口。
- `/peer-forge-upgrade` 现在改为调用仓库自带脚本，不再在 skill 文档里直接写裸 `git` 命令。
- 新增 `--check` 模式，可以只检查是否有新版本，而不修改当前 checkout。
- `setup` 现在会校验并展示新的升级 CLI 入口。

## 升级方式

全局安装：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade
```

仅检查更新：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade --check
```

项目内 vendored 安装：

```bash
./.claude/skills/peer-forge/bin/peer-forge-upgrade
```
````

Notes:

- Repository description and topics must be set in the GitHub UI or via authenticated GitHub API access.
- License selection is intentionally left separate because it is a legal choice, not a tooling default.

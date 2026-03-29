# Peer Forge

[English README](./README.md)

这是一个独立的双 Agent 编码协作工具包，用来让 Claude Code 和 Codex 按照“平级学生做卷子”的方式协作：

- 隔离工作区
- 初始阶段独立做方案
- 交叉评审
- 各自修订方案
- 先达成最终方案共识
- 由一方执行代码
- 另一方 review 实现结果

另外，这个仓库现在也带了一个独立的 live 模式，用来在 tmux 里并排看两个交互式 session 实时工作。

这个仓库的目标是同时具备两种形态：

- 作为 Claude Code 的技能包直接安装
- 作为独立 CLI 仓库直接运行

当前版本：`v0.16.0`

Peer Forge 现在会在 skill 加载时检查新版本。升级提示状态放在 `~/.peer-forge/`，支持自动升级、暂缓提醒、关闭检查，以及刚升级后的提示。

## 目录结构

```text
peer-forge/
├── SKILL.md
├── peer-consensus/
│   └── SKILL.md
├── peer-forge-live/
│   └── SKILL.md
├── codex-collab/
│   ├── SKILL.md
│   └── scripts/
│       └── codex-headless-collab.sh
├── peer-forge-upgrade/
│   └── SKILL.md
├── tools/
│   ├── live_api.py
│   ├── live_protocol.py
│   ├── live_state.py
│   ├── live_tmux.py
│   ├── peer_consensus.py
│   └── peer_live.py
├── bin/
│   ├── peer-forge
│   ├── peer-consensus
│   ├── peer-forge-live
│   └── peer-forge-upgrade
├── scripts/
│   ├── live-api-smoke.sh
│   ├── live-apply-smoke.sh
│   ├── live-smoke.sh
│   └── live-web-smoke.sh
├── web/
│   └── live/
│       ├── index.html
│       ├── app.js
│       ├── render.js
│       ├── store.js
│       └── app.css
├── setup
├── uninstall
├── README.md
├── README.zh-CN.md
├── CHANGELOG.md
├── GITHUB_METADATA.md
├── LICENSE
└── VERSION
```

## 安装

### 全局安装（推荐）

```bash
git clone git@github.com:andrewbai/peer-forge.git ~/.claude/skills/peer-forge
~/.claude/skills/peer-forge/setup
```

装完后，Claude Code 会直接发现：

- `/peer-forge`
- `/peer-consensus`
- `/peer-forge-live`
- `/codex-collab`
- `/peer-forge-upgrade`

如果 Claude Code 当时已经打开，重启一次让它重新加载 skill。

### 项目内 vendoring

在目标项目根目录执行：

```bash
git clone git@github.com:andrewbai/peer-forge.git .claude/skills/peer-forge
./.claude/skills/peer-forge/setup --local
```

这样会把整个仓库放进项目里，再由 `setup` 注册 sibling symlink：

- `.claude/skills/peer-consensus -> peer-forge/peer-consensus`
- `.claude/skills/codex-collab -> peer-forge/codex-collab`

### 直接用 CLI

全局安装：

```bash
~/.claude/skills/peer-forge/bin/peer-forge \
  --repo /path/to/project \
  --task "实现这次需求改动。" \
  --apply-final
```

项目内 vendoring：

```bash
./.claude/skills/peer-forge/bin/peer-forge \
  --repo . \
  --task "实现这次需求改动。" \
  --apply-final
```

常用运行参数：

- `--agent-timeout-seconds 1800` 为每个 Claude/Codex 阶段设置超时，传 `0` 表示关闭超时。
- `--supervise` 会把 Claude/Codex 的输出实时流式打印到终端，同时写入带前缀的详细日志，但不改变协议本身。
- `--supervise-checkpoints` 会在 `--supervise` 的基础上增加阶段边界暂停，支持 `continue`、`inspect`、`retry`、`note`、`abort`，并要求使用 `--task` 或 `--task-file`。
- `--cleanup-workspaces` 在运行结束后删除临时隔离工作区。
- `--keep-workspaces` 会在启用 cleanup 时仍然保留这些隔离工作区。`--keep-run-dir` 仍可用，但已废弃。

升级已安装的 checkout：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade
```

只检查是否有新版本，不真正升级：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-upgrade --check
```

live tmux 模式：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "让 Claude Code 和 Codex 独立出方案、互相 review，并且我在旁边实时监督。"
```

推荐的 detached + 浏览器监督启动方式（不需要 tmux）：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "让 Claude Code 和 Codex 独立出方案、互相 review，并且我在旁边实时监督。" \
  --transport pty \
  --no-attach \
  --open-ui
```

基于 tmux 的 detached 启动方式（需要 tmux）：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "让 Claude Code 和 Codex 独立出方案、互相 review，并且我在旁边实时监督。" \
  --no-attach \
  --open-ui
```

如果你还想直接用 `curl` 或自定义脚本访问本地 control API：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "让 Claude Code 和 Codex 独立出方案、互相 review，并且我在旁边实时监督。" \
  --no-attach \
  --open-ui \
  --print-control-token
```

detached 启动输出的 JSON 现在会补充：

- `control_url`
- `events_stream_url`
- `web_url`
- 传了 `--print-control-token` 时额外给出 `control_token`
- `process_mode`（`pty-detached`、`pty-inline` 或 `tmux`）
- detached PTY run 的 `owner_pid` 和 `owner_alive`
- 用于生命周期管理的 `status_command` 和 `stop_command`

查看一个 detached PTY run 的状态：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live status \
  --state-file /path/to/state.json \
  --open-ui
```

停止一个 detached PTY run：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live stop \
  --state-file /path/to/state.json
```

`peer-forge-live` 现在默认不会给 Claude 加 `--bare`，这样 Claude Max、OAuth 和 keychain 登录态在交互式 live session 里可以继续使用。

只有在你明确需要 bare mode 时才显式开启：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "以 Claude bare mode 运行 live workflow。" \
  --claude-bare
```

恢复或重新附着已有的 live run：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json \
  --open-ui
```

预览一个已批准的 live run 会如何落回目标仓库：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

真正 apply，并且自动 commit 到新分支：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

如果目标仓库只有不相关的脏文件，也可以显式允许 apply：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-dirty-target
```

## 环境要求

- 已安装并登录 `claude` CLI
- 已安装并登录 `codex` CLI
- 如果要用 `peer-forge-live`，还需要系统里有 `tmux`
- 系统里可用 `python3`
- 系统里可用 `git`

快速检查：

```bash
claude -v
codex -V
tmux -V
python3 --version
git --version
```

## 最简单的 Skill 用法

如果你不想手动组织一堆参数，就用 `peer-forge`。

`peer-forge` 是更产品化的入口：

- 只给任务也可以
- `acceptance` 不是必填
- `scope` 也不是必填

你在 Claude Code 里可以直接表达这种意图：

- `使用 peer-forge skill 处理这个任务`
- `/peer-forge 让 Claude 和 Codex 都出方案，互相 review，然后收敛成最终版本`

## 这套工作流具体做什么

1. 创建 Claude 和 Codex 各自独立的隔离工作区。
2. 在可以并行的阶段并行运行双方的方案任务。
3. 让双方先独立完成各自方案。
4. 让双方互相 review 对方的方案。
5. 让双方根据 review 再各自修订方案。
6. 让双方基于共识选出最终方案。
7. 由被选中的一方按最终方案执行代码。
8. 由另一方 review 实现结果，必要时进入有限轮次的 fix/review。

## Live 模式

`peer-forge-live` 是 batch 工作流的交互式对应物。

当前 live 范围：

- 覆盖完整的 plan -> execute -> review -> signoff 流程
- Claude 和 Codex 都是长生命周期 session
- 用 tmux 分 pane 同时看 Claude、Codex、supervisor
- 只允许对称 supervisor note
- 非写阶段由协议层强制做只读校验
- 在双方批准后，可把最终 execution package 安全落回目标仓库

当前 live 阶段顺序：

1. 双方独立出方案
2. 交叉 review
3. 各自修订
4. 共识选 base
5. 产出最终方案
6. 方案 signoff
7. 选中的一方执行代码
8. 另一方 review 实现结果
9. 有限轮次的 execution fix/signoff

启动说明：

- 协议本身在启动后会自动继续跑，但 CLI 自己的原生安全确认仍然保留人工处理。
- 实际上，Claude 可能会先让你确认是否进入 `bypassPermissions` 模式。
- 在某些机器或首次进入新工作区时，Codex 也可能会先要求你确认 trust。
- 这里故意不做 tmux 自动按键确认，因为那种做法比人工确认一次更脆弱，也更容易被 CLI 升级打断。

推荐的监督路径：

1. 用 `--transport pty --no-attach --open-ui` 启动（不需要 tmux）。
2. 浏览器打开 `web_url`，在 Web UI 里看时间线、事件流、产物和边界按钮。
3. 用 `status --state-file ...` 查看 run 状态，用 `stop --state-file ...` 停止 run。
4. 只有在你要自己调 API / SSE 时，才使用 `control_url` 加 `control_token`。
5. 需要 CLI 原生 trust / bypass 确认或原始 pane 检查时，再用 tmux（`--transport tmux`）。

supervisor pane 里的主要命令：

- `status`
- `tail claude`
- `tail codex`
- `inspect claude`
- `inspect codex`
- `show final-plan`
- `show package`
- `show diff`
- `show manifest`
- `note both`
- `wait`
- `continue`
- `abort`

`status` 现在还会显示当前 executor/reviewer、plan/execution 是否已批准、只读违规次数、当前 package 摘要，以及每个 pane 当前是 `read-only` 还是 `write`。

内置 Web UI 操作和 supervisor 命令是一一对应的：

- `Status` 按钮对应 `status`
- `Continue` 只会在边界暂停时可用
- `Abort` 对应 `abort`
- note 表单会排队成 `note both <text>`

如果 supervisor pane 挂掉了，或者你 detach 之后想原地修复会话：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file <target-repo>/.claude/tmp/peer-forge-live/<run-id>/state.json \
  --open-ui
```

如果 live run 已结束，而且 plan / execution 都批准了，就可以预览或 apply 最终 execution package：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file <target-repo>/.claude/tmp/peer-forge-live/<run-id>/state.json
```

apply 语义：

- 不带 `--apply` 时，只做 dry-run 预览，不改仓库。
- 真正写入必须显式传 `--apply`。
- 默认会落到 `peer-forge/<run-id>` 新分支。
- `--commit` 会在 apply 完成后自动创建 git commit。
- 当前 live apply 只支持 git-backed 的 live run。
- 如果目标仓库的 HEAD 已漂移，但漂移路径和 execution package 不重叠，会自动允许 preview/apply。
- 如果目标仓库有不相关的脏文件，必须显式传 `--allow-dirty-target`。
- 如果脏文件路径或漂移路径和 execution package 重叠，默认仍然拒绝 apply。
- `--allow-base-drift` 是“漂移路径和 package 重叠时”的显式 override。

每次 live run 的产物会写到：

```text
<target-repo>/.claude/tmp/peer-forge-live/<run-id>/
```

里面包括：

- `state.json`
- `events.jsonl`
- `supervisor.log`
- `panes/verbose.log`
- `panes/claude.raw.log`
- `panes/codex.raw.log`
- `panes/supervisor.raw.log`
- `turns/<turn-id>/...`
- `report.json`
- `report.md`
- `apply/history.jsonl`
- `apply/<timestamp>-report.json`
- `apply/<timestamp>-report.md`

另外，仓库里还带了一个用于验证 live 启动和 supervisor 恢复链路的 smoke 脚本：

- `scripts/live-api-smoke.sh`
- `scripts/live-smoke.sh`
- `scripts/live-apply-smoke.sh`
- `scripts/live-web-smoke.sh`
- `scripts/live-pty-detached-smoke.sh`

## 运行产物

每次运行都会把产物写到目标仓库下面：

```text
<target-repo>/.claude/tmp/peer-consensus/<run-id>/
```

里面包括：

- task 和 config
- `progress.log`
- 启用 `--supervise` 时还会有 `supervisor.log`
- 启用 `--supervise-checkpoints` 时还会有 `checkpoints/history.jsonl`
- 每次 retry 还会写出类似 `checkpoints/01-plan-initial-retry-01.json` 的记录
- 添加监督 note 后还会有 `notes/history.jsonl`
- 隔离工作区
- 每个阶段的 prompt
- 模型输出
- 每个阶段的 diff package
- 每个 stage 的 retry 产物会落在 `<stage-dir>/retries/`
- 最终方案文件
- implementation review 结果
- `report.json`
- `report.md`

运行行为：

- 进度日志会输出到 `stderr`，终端里可以看到当前跑到哪个阶段。
- 同样的进度内容也会写入运行目录里的 `progress.log`。
- 启用 `--supervise` 后，Claude/Codex 的 stdout 和 stderr 会带前缀实时打印到终端，并同步写入 run 级别的 `supervisor.log`。
- 启用 `--supervise` 后，每个 stage 目录下也会多出一个带前缀的 `<stage-dir>/verbose.log`，和原始的 `stdout.txt` / `stderr.txt` 并存。
- 启用 `--supervise-checkpoints` 后，每个主要阶段边界都会暂停。你可以选择 `continue`、`inspect`、`retry`、`note` 或 `abort`，但不会改变 agent 协议和工作区隔离。
- `retry` 会安全地重跑当前 checkpoint 对应的阶段。并行 plan checkpoint 会同时重跑双方；写阶段会先恢复到该阶段入口快照；`apply-final` 仍然只支持 inspect/continue/abort。
- `note` 会记录一条对双方对称生效的监督意见，并从后续阶段开始注入到双方 prompt。多行输入以单独一行 `---` 结束。同一个 checkpoint 里新增的 note 不会影响这个 checkpoint 自己的 retry。
- `inspect` 会打印当前 stage 的关键产物路径、当前 active supervisor notes、retry 摘要、`parsed.json`、`stdout.txt`、`stderr.txt`、存在时的 `verbose.log`，以及写阶段的 diff/package 路径。
- 最终机器可读输出仍然会以 JSON 形式写到 `stdout`。
- 无论运行完成、用户中止还是中途失败，都会写出 `report.json` 和 `report.md`。
- `report.json` 里还会包含 `progress_log`、`supervisor_log`、`checkpoint_history`、`checkpoint_events`、`retry_attempts`、`notes_history`、`supervisor_notes` 路径/记录，以及结构化的 `stage_timings` 阶段耗时信息。

## Skills 说明

### `peer-forge`

这是更适合日常使用的前门入口。

适用于：

- 想用 skill 的方式
- 不想先想清楚所有 CLI 参数
- 只有一句任务描述也想直接跑

### `peer-consensus`

这是更底层的完整双 Agent 共识协议入口，适用于：

- 需要两个平级 Agent
- 需要初始阶段互不污染
- 需要先做方案共识，再做执行与 review
- 希望更明确地控制 `task / acceptance / scope`

### `peer-forge-live`

这是 live tmux 模式，适用于：

- 你想并排看两个交互式 session
- 你想实时监督它们的过程
- 你想保留 session memory，而不是每个阶段都冷启动
- 你想把方案、执行、review、signoff 都放在同一轮 live 协议里

### `codex-collab`

这是一个更轻量的辅助 skill，用来让 Claude 驱动无头 Codex 做局部协作。它更快，但不等同于完整的 peer workflow。

### `peer-forge-upgrade`

这是一个自升级 skill，用来通过仓库自带的升级脚本更新已经安装好的 `peer-forge` 并重新执行 `setup`。

## 备注

- 仓库本体本身就是主 skill，`~/.claude/skills/peer-forge/SKILL.md` 会被 Claude Code 直接发现。
- 子 skill 由 `setup` 自动注册成 sibling symlink。
- 所有 skill 文档都统一走 `bin/` launcher，不直接写 Python 入口。
- 退出码：`0` 表示最终结果获批，`1` 表示运行时失败，`2` 表示流程跑完但最终候选没有获批。

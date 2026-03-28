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

当前版本：`v0.10.0`

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
│   ├── live_protocol.py
│   ├── live_tmux.py
│   ├── peer_consensus.py
│   └── peer_live.py
├── bin/
│   ├── peer-forge
│   ├── peer-consensus
│   ├── peer-forge-live
│   └── peer-forge-upgrade
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

- 只做 plan，不写代码
- Claude 和 Codex 都是长生命周期 session
- 用 tmux 分 pane 同时看 Claude、Codex、supervisor
- 只允许对称 supervisor note
- 还没有实现 execution 阶段

启动说明：

- 协议本身在启动后会自动继续跑，但 CLI 自己的原生安全确认仍然保留人工处理。
- 实际上，Claude 可能会先让你确认是否进入 `bypassPermissions` 模式。
- 在某些机器或首次进入新工作区时，Codex 也可能会先要求你确认 trust。
- 这里故意不做 tmux 自动按键确认，因为那种做法比人工确认一次更脆弱，也更容易被 CLI 升级打断。

supervisor pane 里的主要命令：

- `status`
- `tail claude`
- `tail codex`
- `inspect claude`
- `inspect codex`
- `note both`
- `wait`
- `continue`
- `abort`

每次 live run 的产物会写到：

```text
<target-repo>/.claude/tmp/peer-forge-live/<run-id>/
```

里面包括：

- `state.json`
- `supervisor.log`
- `panes/verbose.log`
- `panes/claude.raw.log`
- `panes/codex.raw.log`
- `turns/<turn-id>/...`
- `report.json`
- `report.md`

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

### `codex-collab`

这是一个更轻量的辅助 skill，用来让 Claude 驱动无头 Codex 做局部协作。它更快，但不等同于完整的 peer workflow。

### `peer-forge-upgrade`

这是一个自升级 skill，用来通过仓库自带的升级脚本更新已经安装好的 `peer-forge` 并重新执行 `setup`。

## 备注

- 仓库本体本身就是主 skill，`~/.claude/skills/peer-forge/SKILL.md` 会被 Claude Code 直接发现。
- 子 skill 由 `setup` 自动注册成 sibling symlink。
- 所有 skill 文档都统一走 `bin/` launcher，不直接写 Python 入口。
- 退出码：`0` 表示最终结果获批，`1` 表示运行时失败，`2` 表示流程跑完但最终候选没有获批。

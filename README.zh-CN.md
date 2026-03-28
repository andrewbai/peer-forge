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

这个仓库的目标是同时具备两种形态：

- 作为 Claude Code 的技能包直接安装
- 作为独立 CLI 仓库直接运行

当前版本：`v0.8.0`

## 目录结构

```text
peer-forge/
├── SKILL.md
├── peer-consensus/
│   └── SKILL.md
├── codex-collab/
│   ├── SKILL.md
│   └── scripts/
│       └── codex-headless-collab.sh
├── peer-forge-upgrade/
│   └── SKILL.md
├── tools/
│   └── peer_consensus.py
├── bin/
│   ├── peer-forge
│   ├── peer-consensus
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

## 环境要求

- 已安装并登录 `claude` CLI
- 已安装并登录 `codex` CLI
- 系统里可用 `python3`
- 系统里可用 `git`

快速检查：

```bash
claude -v
codex -V
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

## 运行产物

每次运行都会把产物写到目标仓库下面：

```text
<target-repo>/.claude/tmp/peer-consensus/<run-id>/
```

里面包括：

- task 和 config
- `progress.log`
- 启用 `--supervise` 时还会有 `supervisor.log`
- 隔离工作区
- 每个阶段的 prompt
- 模型输出
- 每个阶段的 diff package
- 最终方案文件
- implementation review 结果
- `report.json`
- `report.md`

运行行为：

- 进度日志会输出到 `stderr`，终端里可以看到当前跑到哪个阶段。
- 同样的进度内容也会写入运行目录里的 `progress.log`。
- 启用 `--supervise` 后，Claude/Codex 的 stdout 和 stderr 会带前缀实时打印到终端，并同步写入 run 级别的 `supervisor.log`。
- 启用 `--supervise` 后，每个 stage 目录下也会多出一个带前缀的 `<stage-dir>/verbose.log`，和原始的 `stdout.txt` / `stderr.txt` 并存。
- 最终机器可读输出仍然会以 JSON 形式写到 `stdout`。
- 无论运行完成还是中途失败，都会写出 `report.json` 和 `report.md`。
- `report.json` 里还会包含 `progress_log`、`supervisor_log` 路径和结构化的 `stage_timings` 阶段耗时信息。

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

### `codex-collab`

这是一个更轻量的辅助 skill，用来让 Claude 驱动无头 Codex 做局部协作。它更快，但不等同于完整的 peer workflow。

### `peer-forge-upgrade`

这是一个自升级 skill，用来通过仓库自带的升级脚本更新已经安装好的 `peer-forge` 并重新执行 `setup`。

## 备注

- 仓库本体本身就是主 skill，`~/.claude/skills/peer-forge/SKILL.md` 会被 Claude Code 直接发现。
- 子 skill 由 `setup` 自动注册成 sibling symlink。
- 所有 skill 文档都统一走 `bin/` launcher，不直接写 Python 入口。
- 退出码：`0` 表示最终结果获批，`1` 表示运行时失败，`2` 表示流程跑完但最终候选没有获批。

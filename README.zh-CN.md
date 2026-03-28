# Peer Consensus Toolkit

[English README](./README.md)

这是一个独立的双 Agent 编码协作工具包，用来让 Claude Code 和 Codex 按照“平级学生做卷子”的方式协作：

- 隔离工作区
- 初始阶段独立做方案
- 交叉评审
- 各自修订方案
- 先达成最终方案共识
- 由一方执行代码
- 另一方 review 实现结果

这个仓库的目标不是绑死在某一个项目里，而是作为一个独立工具仓库存在，之后按需同步到别的项目中使用。

当前版本：`v0.4.0`

## 目录结构

```text
peer-consensus-toolkit/
├── bin/
│   ├── peer-consensus
│   └── peer-forge
├── .claude/
│   └── skills/
│       ├── peer-forge/
│       │   └── SKILL.md
│       ├── peer-consensus/
│       │   └── SKILL.md
│       └── codex-collab/
│           ├── SKILL.md
│           └── scripts/
│               └── codex-headless-collab.sh
├── scripts/
│   └── install-claude-skills.sh
├── tools/
│   └── peer_consensus.py
├── README.md
└── README.zh-CN.md
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

底层依然是跑 `tools/peer_consensus.py`，但它把“只给任务”视为正常用法。

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

## 为什么不是把整个仓库都塞进 `.claude`

因为 `.claude/skills/` 本质上只应该放 skill 本身。

- `SKILL.md` 和 skill 自带的小脚本，应该放在 `.claude/skills/`
- `tools/peer_consensus.py`、`bin/peer-forge` 这种运行工具，不应该混在 `.claude` 配置目录里
- `README`、`CHANGELOG`、`LICENSE`、版本文件也不是 Claude Code 运行 skill 时必须放进 `.claude` 的内容

所以更干净的结构是：

- 仓库本体独立放在一个稳定目录，比如 `~/.peer-forge`
- Claude Code 只在 `~/.claude/skills/` 里注册这几个 skill

## 安装到 Claude Code 里用 `skill`（推荐）

如果你已经把仓库放在本机任意位置，比如现在这份：

```bash
cd /Users/andrew/Desktop/peer-consensus-toolkit
bash scripts/install-claude-skills.sh
```

这个安装脚本会做 4 件事：

1. 把当前仓库注册成 `~/.peer-forge`
2. 把 `peer-forge` 安装到 `~/.claude/skills/peer-forge`
3. 把 `peer-consensus` 安装到 `~/.claude/skills/peer-consensus`
4. 把 `codex-collab` 安装到 `~/.claude/skills/codex-collab`

装完后，如果 Claude Code 当时已经开着，重启一次让它重新加载 skill。

之后你在任意项目里都可以直接这样说：

- `/peer-forge 处理这个任务：...`
- `/peer-consensus 按完整双 Agent 共识协议处理这个任务：...`

如果你是从 GitHub 新装，一套更标准的流程是：

```bash
git clone git@github.com:andrewbai/peer-forge.git ~/peer-forge
cd ~/peer-forge
bash scripts/install-claude-skills.sh
```

## 两种运行方式

### 1. 作为独立工具，直接作用于任意项目

你可以把这个工具仓库独立放着，然后直接指向别的代码仓库运行：

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task "实现这次需求改动。"
```

如果你已经完成了上面的 Claude Code 全局安装，也可以直接用 launcher：

```bash
~/.peer-forge/bin/peer-forge \
  --repo /path/to/target-project \
  --task "实现这次需求改动。"
```

只有在需要时，再补 `--scope` 和 `--acceptance`：

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task "实现这次需求改动。" \
  --acceptance "不要破坏公开 API。" \
  --scope src/example.ts
```

如果双方最终都认可，并且你要把结果直接写回目标项目：

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
  --repo /path/to/target-project \
  --task-file /path/to/task.md \
  --scope src/example.ts \
  --apply-final
```

### 2. 同步进某个具体项目里使用

把下面这些路径复制到目标项目根目录：

- `.claude/skills/peer-forge/`
- `.claude/skills/peer-consensus/`
- `.claude/skills/codex-collab/`
- `tools/peer_consensus.py`

然后可以直接用 skill 思路，或者直接跑脚本：

```bash
python3 tools/peer_consensus.py \
  --repo . \
  --task "实现这次需求改动。"
```

只有在有必要时，再补 `--scope` 和 `--acceptance`。

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
- 隔离工作区
- 每个阶段的 prompt
- 模型输出
- 每个阶段的 diff package
- 最终方案文件
- implementation review 结果
- `report.json`
- `report.md`

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

## 推到 GitHub

一个常见流程：

```bash
cd /Users/andrew/Desktop/peer-consensus-toolkit
git init
git add .
git commit -m "Initial peer consensus toolkit"
```

然后你再创建 GitHub 仓库并正常 push。

## 备注

- 这套工具不需要长期住在某一个具体项目里。
- 如果你走全局安装路线，推荐让仓库本体固定挂在 `~/.peer-forge`，Claude Code 只加载 `~/.claude/skills/` 里的 skill。
- 如果你走项目内同步路线，skill 放在 `.claude/skills/` 下，是为了相对路径还能保持正确。
- 如果最终候选没有拿到双方批准，主脚本会返回非零退出码。

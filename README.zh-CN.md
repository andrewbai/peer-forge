# Peer Consensus Toolkit

[English README](./README.md)

这是一个独立的双 Agent 编码协作工具包，用来让 Claude Code 和 Codex 按照“平级学生做卷子”的方式协作：

- 隔离工作区
- 初始阶段独立出方案
- 交叉评审
- 各自修订
- 共识合并
- 双方最终签字认可

这个仓库的目标不是绑死在某一个项目里，而是作为一个独立工具仓库存在，之后按需同步到别的项目中使用。

当前版本：`v0.1.0`

## 目录结构

```text
peer-consensus-toolkit/
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

## 两种使用方式

### 1. 作为独立工具，直接作用于任意项目

你可以把这个工具仓库独立放着，然后直接指向别的代码仓库运行：

```bash
python3 /path/to/peer-consensus-toolkit/tools/peer_consensus.py \
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
2. 在可以并行的阶段并行运行双方任务。
3. 让双方先独立完成各自方案。
4. 让双方互相 review 对方的结果。
5. 让双方根据 review 再各自修订。
6. 让双方明确说明：自己的哪些优点必须保留、对对方方案还有哪些阻塞意见。
7. 从更优的一侧作为最终候选底稿继续合并。
8. 要求双方都 sign-off；如果一方反对，则进入有限轮次的 objection/fix。

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
- sign-off 结果
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
- 需要交叉评审和收敛
- 需要最终双方都认可
- 希望更明确地控制 `task / acceptance / scope`

### `peer-consensus`

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
- skill 放在 `.claude/skills/` 下，是为了以后同步到项目根目录后还能保持相对路径正确。
- 如果最终候选没有拿到双方批准，主脚本会返回非零退出码。

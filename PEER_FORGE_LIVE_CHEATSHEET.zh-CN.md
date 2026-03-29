# Peer Forge Live 完整使用 Cheat Sheet

版本：`v0.15.0`

这份文档是给你实际跑一整次 `peer-forge-live` 用的中文操作手册。

目标不是介绍原理，而是让你能从：

1. 启动 live run
2. 在 tmux 里监督 Claude 和 Codex
3. 必要时恢复 supervisor pane
4. 最后 preview / apply 回真实仓库

一路跑通。

---

## 1. 这是什么

`peer-forge-live` 是 `peer-forge` 里的 live 模式。

它和 batch 模式的区别：

- batch 模式：一次次冷调用 CLI，适合自动化、CI、结构化报告
- live 模式：Claude 和 Codex 各自是长生命周期交互式 session，你能实时看过程

live 模式当前支持的完整流程：

1. 双方独立出方案
2. 交叉 review
3. 各自修订
4. 共识选 base
5. 产出最终方案
6. 方案 signoff
7. 选中的一方执行代码
8. 另一方 review 实现结果
9. 有限轮次 execution fix / signoff
10. run 完成后 preview / apply 最终 execution package

---

## 2. 运行前要求

你需要：

- 已安装并登录 `claude`
- 已安装并登录 `codex`
- 已安装 `tmux`
- 已安装 `python3`
- 已安装 `git`

快速检查：

```bash
claude -v
codex -V
tmux -V
python3 --version
git --version
```

如果是全局安装，主命令一般是：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live
```

如果是项目内 vendoring，命令一般是：

```bash
./.claude/skills/peer-forge/bin/peer-forge-live
```

下面示例默认都用全局路径。

---

## 3. 你先要理解的 4 个事实

### 3.1 live 模式不是完全无人值守

协议本身会自动推进，但 CLI 自己的原生确认仍然可能要你手动确认。

常见情况：

- Claude 可能要求确认进入 `bypassPermissions`
- Codex 可能要求确认 trust 某个工作区

这不是 bug，这是当前设计。

### 3.2 Claude 和 Codex 不共享工作区

它们各自有隔离工作区，live 模式只是让你并排看两个 session。

### 3.3 supervisor 只能发对称 note

你不能单独给 Claude 或 Codex 发私货。

当前 live 模式只支持：

- `note both`

这是为了保持双方平级、避免污染。

### 3.4 apply 不直接信任 agent workspace

最终回写真实仓库时，依赖的是最终 execution package，不是直接把 agent 当前工作区原样 rsync 回来。

---

## 4. 最常用的 3 个命令

### 4.1 启动 live run

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo . \
  --task "让 Claude Code 和 Codex 独立出方案、互相 review，然后收敛到一个最终实现，我全程实时监督。"
```

### 4.2 恢复 / 重新附着已有 run

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json
```

### 4.3 preview / apply 最终结果

先 preview：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

真正 apply：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply
```

apply 后自动 commit：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

---

## 5. 启动一次完整 live run

### 5.1 最小启动命令

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo /path/to/project \
  --task "你的真实任务描述"
```

### 5.2 推荐启动命令

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo /path/to/project \
  --task "重构上传模块，支持分片异步上传，并保持现有 public API 不变。" \
  --acceptance "不要改 public API" \
  --acceptance "保留现有 CLI 行为" \
  --scope src/upload \
  --scope tests \
  --watchdog-seconds 180 \
  --signoff-rounds 1
```

### 5.3 参数解释

- `--repo`
  - 目标仓库根目录
- `--task`
  - 任务描述
- `--task-file`
  - 用文件提供任务描述，和 `--task` 二选一
- `--acceptance`
  - 验收约束，可重复传
- `--scope`
  - 倾向修改的路径范围，可重复传
- `--include-path`
  - git 没跟踪但又需要带进隔离工作区的额外路径
- `--claude-model`
  - 指定 Claude 模型
- `--codex-model`
  - 指定 Codex 模型
- `--signoff-rounds`
  - 额外 final-fix / execution-fix 轮数，默认 `1`
- `--watchdog-seconds`
  - 多久没输出就对双方发一次对称 watchdog nudge
- `--max-watchdog-nudges`
  - 每个活跃 turn 最多发几次 watchdog
- `--run-root`
  - 自定义 run artifact 根目录
- `--session-name`
  - 自定义 tmux session 名称
- `--no-attach`
  - 创建 session 但不自动 attach
- `--no-claude-bare`
  - 关闭 Claude bare mode

---

## 6. 推荐的第一次真实运行方式

第一次建议你不要直接 attach，而是先拿到 `state.json` 路径。

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo /path/to/project \
  --task "你的真实任务" \
  --acceptance "不要改 public API" \
  --scope src \
  --no-attach
```

它会打印一段 JSON，里面通常有：

- `run_id`
- `session_name`
- `run_dir`
- `state_file`
- `attach`

拿到之后：

```bash
tmux attach-session -t <session_name>
```

这样有两个好处：

1. 你知道准确的 `state.json` 路径
2. 如果 supervisor pane 后面挂掉，恢复时不用再找路径

---

## 7. tmux 里会看到什么

典型是 3 个 pane：

- Claude pane
- Codex pane
- supervisor pane

如果你不熟悉 `tmux`，先记这 3 个最常用操作：

- `Ctrl+B` 然后方向键：切换 pane
- `Ctrl+B` 然后 `D`：detach（退出但不关闭 session）
- `Ctrl+B` 然后 `Z`：放大 / 还原当前 pane

你要重点看的是：

- Claude / Codex 是否都已经过了原生确认
- 是否都开始正常思考和输出
- supervisor 是否在推进 turn

---

## 8. supervisor pane 能做什么

当前支持的主要命令：

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

下面是每个命令的真实用途。

### 8.1 `status`

看 run 当前全局状态。

会显示：

- run id
- session 名称
- 当前 phase / turn
- run status
- executor / reviewer
- plan 是否批准
- execution 是否批准
- 只读违规数量
- 当前 package 信息
- 每个 agent 当前状态
- 每个 pane 是 `read-only` 还是 `write`

适合在你一时不知道现在跑到哪一步时先敲一次。

### 8.2 `tail claude` / `tail codex`

看对应 agent 当前 turn log 尾部。

适合：

- 怀疑它卡住
- 想看它最近一段结构化输出
- 不想直接在 pane 里翻屏

### 8.3 `inspect claude` / `inspect codex`

比 `tail` 更重。

通常会给你：

- pane id
- workspace
- prompt 路径
- session prompt 路径
- raw log
- turn log
- result file
- parsed result
- 最近 pane capture

适合：

- 你怀疑解析失败
- 你想确认它到底收到的 prompt 是什么
- 你想看这轮的结构化结果文件

### 8.4 `show final-plan`

直接看当前最终方案文件。

适合：

- plan 阶段已经基本收敛
- 你想在执行前复核最终方案

### 8.5 `show package`

看当前 execution package 概览。

通常最有用的内容是：

- package dir
- executor
- manifest 路径
- diff 路径
- changed files 列表

### 8.6 `show manifest`

看当前 package 的 `manifest.json`。

适合：

- 你想确认 package 里到底有哪些文件被当成 changed/copied/deleted
- apply 前做最后核对

### 8.7 `show diff`

直接看 package diff 预览。

适合：

- execution 已经跑完
- 你想先 eyeball 一眼主要改动

### 8.8 `note both`

给后续 turn 注入一条对称 note。

特点：

- 只会作用于后续 turn
- 不会反向污染已经完成的 turn
- 两边收到的是同一条 note

适合：

- 你发现双方都忽略了某个约束
- 你想提醒“不要改 public API”
- 你想提醒“优先保留现有测试结构”

多行输入结束符是单独一行：

```text
---
```

### 8.9 `wait`

继续看当前 turn，不做别的。

### 8.10 `continue`

只在边界暂停时有意义。

比如：

- 初始方案完成
- cross-review 完成
- plan signoff 完成
- execution review 完成

此时 supervisor 会停住等你确认，再由你敲 `continue` 进入下一阶段。

### 8.11 `abort`

中止整个 live run。

适合：

- 你已经确认方向错了
- 某个 agent 进入错误状态
- 你不想继续烧 token / 时间

---

## 9. 一次完整 live run 的推荐操作顺序

### 阶段 1：启动

1. 用 `--no-attach` 启动
2. 记下 `state_file`
3. attach 进入 tmux
4. 手动处理 Claude / Codex 可能出现的原生确认

### 阶段 2：观察 plan

你重点看：

- 两边是否都真正开始做 plan
- 是否都理解任务边界
- 有没有明显跑偏

如果跑偏：

- 不要急着 `abort`
- 先用 `note both`

例如：

```text
note both
保留现有 public API，不要把这次任务扩展成接口重设计。
---
```

### 阶段 3：边界确认

每到一个 boundary：

1. 先敲 `status`
2. 必要时 `show final-plan` / `show package`
3. 确认没有问题再 `continue`

### 阶段 4：execution 完成后检查 package

建议至少做这三步：

1. `show package`
2. `show manifest`
3. `show diff`

你要确认：

- 改动范围是不是你预期的
- 是否带入了奇怪的文件
- 是否有误删

### 阶段 5：run 结束

run 成功结束后，先不要急着 apply。

先在终端外做一次 preview：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

---

## 10. run 结束后去哪找文件

每次 run 的主目录一般在：

```text
<target-repo>/.claude/tmp/peer-forge-live/<run-id>/
```

你最常用的文件：

- `state.json`
- `report.json`
- `report.md`
- `events.jsonl`
- `supervisor.log`
- `panes/verbose.log`
- `panes/claude.raw.log`
- `panes/codex.raw.log`
- `panes/supervisor.raw.log`

如果已经进入 apply，还会有：

- `apply/history.jsonl`
- `apply/<timestamp>-report.json`
- `apply/<timestamp>-report.md`

---

## 11. 如何恢复一个被你关掉的 session

如果 tmux session 还在，只是你 detach 了：

```bash
tmux attach-session -t <session_name>
```

如果 supervisor pane 死了，但 Claude / Codex pane 还活着：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json
```

如果你不想立刻 attach：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json \
  --no-attach
```

适合场景：

- 你误关了 supervisor pane
- 你想从另一个终端恢复
- 你先修 pane，再决定是否 attach

注意：

- `resume` 只能修 supervisor pane
- 如果 Claude / Codex 自己那两个 pane 死了，当前版本不会自动重建 agent pane

---

## 12. apply 前你必须知道的规则

### 12.1 不带 `--apply` = 只 preview

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

这一步不会写仓库，只会做：

- 前置校验
- package 检查
- drift / dirty / overlap 分析
- 生成 apply report

### 12.2 真正写仓库必须显式带 `--apply`

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply
```

### 12.3 默认新分支

默认目标分支：

```text
peer-forge/<run-id>
```

这意味着：

- 默认不会直接污染你当前分支
- apply 会在新分支上进行

### 12.4 可指定自定义分支

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch peer-forge/my-custom-branch
```

### 12.5 可显式要求落在当前分支

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch current
```

只有你非常清楚当前分支状态时才建议这么做。

### 12.6 可自动 commit

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

### 12.7 非 git-backed run 不能 apply

当前 live apply 只支持 git-backed live run。

---

## 13. apply 的决策规则

当前版本是 `v0.14.0`，apply 规则如下。

### 13.1 允许的情况

#### 情况 A：repo 干净，HEAD 没漂移

最简单，直接允许。

#### 情况 B：repo HEAD 漂移了，但漂移路径和 package 不重叠

允许。

也就是：

- repo 后来有新提交
- 但改的是 package 不涉及的文件

这种情况 preview / apply 都允许继续。

#### 情况 C：repo 有不相关的脏文件，但你显式传了 `--allow-dirty-target`

允许。

前提是这些脏文件路径与 package 不重叠。

### 13.2 默认阻止的情况

#### 情况 D：dirty paths 和 package 重叠

默认阻止。

#### 情况 E：drift paths 和 package 重叠

默认阻止。

### 13.3 显式 override

如果是 drift overlap，但你就是要继续：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-base-drift
```

如果只是 non-overlap dirty target：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-dirty-target
```

---

## 14. 一套推荐的真实 apply 操作顺序

### 第一步：先 preview

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

重点看输出里的：

- `status`
- `decision`
- `target_branch`
- `changed_files`
- `dirty_paths`
- `drift_paths`
- `dirty_overlap`
- `drift_overlap`
- `safe_paths`
- `blocked_paths`
- `blockers`
- `warnings`

### 第二步：打开 apply report

打开：

- `apply/<timestamp>-report.json`
- `apply/<timestamp>-report.md`

重点看：

- 它到底是 `preview-safe` 还是 `blocked`
- 被 block 的真实原因是什么
- 是 dirty 引起的，还是 drift 引起的
- overlap 命中了哪些文件

### 第三步：再决定是否真正写入

如果 preview 结果没有问题：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

---

## 15. 常用 apply 场景模板

### 15.1 最保守：先预览

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

### 15.2 默认安全：新分支 apply + commit

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

### 15.3 指定分支 apply + commit

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch peer-forge/upload-refactor \
  --commit
```

### 15.4 当前分支 apply

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --branch current
```

### 15.5 非重叠 dirty target 下 apply

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-dirty-target
```

### 15.6 重叠 drift 也强行继续

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --allow-base-drift
```

这个命令风险更高，建议只在你完全知道为什么重叠时才用。

---

## 16. 你第一次真实跑时，我建议的最稳流程

### 方案 A：最稳

1. `--no-attach` 启动
2. attach 看 live run
3. run 完成后先 `apply` preview
4. 看 `apply-report.md`
5. 再 `--apply --commit`

命令序列：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo /path/to/project \
  --task "你的真实任务" \
  --acceptance "不要改 public API" \
  --scope src \
  --no-attach
```

```bash
tmux attach-session -t <session_name>
```

run 完成后：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

确认没问题后：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

---

## 17. 常见问题

### Q1：为什么启动后不动了？

通常先看两件事：

1. Claude / Codex 是否在等你确认原生提示
2. supervisor pane 是否已经停在 boundary 等你 `continue`

先敲：

```text
status
```

### Q2：我关掉了 tmux，run 丢了吗？

不一定。

如果 session 还在：

```bash
tmux attach-session -t <session_name>
```

如果只有 supervisor pane 死了：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live resume \
  --state-file /path/to/state.json
```

### Q3：为什么 preview 返回 blocked？

去看：

- `blockers`
- `dirty_overlap`
- `drift_overlap`
- `blocked_paths`

通常不是整个 repo 都不能动，而是某些路径和 package 重叠了。

### Q4：为什么 apply 没有真正写仓库？

通常是因为你只跑了 preview：

```bash
peer-forge-live apply --state-file ...
```

没有带：

```bash
--apply
```

### Q5：为什么 apply 去了一个新分支？

这是默认行为。

默认 branch：

```text
peer-forge/<run-id>
```

这是为了避免直接污染你当前分支。

### Q6：什么时候用 `--allow-dirty-target`？

只有当：

- 目标 repo 有脏文件
- 这些脏文件和 execution package 不重叠
- 你确认要继续 apply

才用。

### Q7：什么时候用 `--allow-base-drift`？

只有当：

- repo 当前 HEAD 和 run 启动时不同
- 且重叠路径本来应该 block
- 但你明确知道要强行继续

才用。

---

## 18. 最后给你的最短实战清单

如果你现在就想跑一遍，照这个顺序：

1. 启动

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live \
  --repo /path/to/project \
  --task "你的真实任务" \
  --acceptance "不要改 public API" \
  --scope src \
  --no-attach
```

2. attach

```bash
tmux attach-session -t <session_name>
```

3. 在 supervisor 里常用：

```text
status
show final-plan
show package
show diff
continue
```

4. 如果需要提醒双方：

```text
note both
保留现有 public API，不要把这次任务扩展成接口重设计。
---
```

5. run 完成后 preview：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json
```

6. 确认无误后 apply：

```bash
~/.claude/skills/peer-forge/bin/peer-forge-live apply \
  --state-file /path/to/state.json \
  --apply \
  --commit
```

---

## 19. 这份 cheat sheet 不覆盖什么

当前这份手册不展开讲：

- batch 模式 `peer-consensus`
- skill 安装流程
- GitHub Release / PR 流程
- 更高阶的 repo 冲突处理策略

它只服务一个目标：

让你在 `v0.14.0` 上，完整跑通一次 live workflow，从启动一路到 apply。

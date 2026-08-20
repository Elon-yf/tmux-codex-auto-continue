# Codex `429 Too Many Requests` 自动恢复设计

## 1. 问题是什么

截图里的终端状态是：

```text
■ exceeded retry limit, last status: 429 Too Many Requests
• Goal active Objective: ... Time: 58m.
```

这里有两个不同层次的状态：

1. `429 Too Many Requests` 是本次 Codex 请求已经连续失败后的终止错误。
2. `Goal active` 表示当前 goal/session 仍然存在，并不等于本次请求已经成功，也不等于 Codex 会自动重新发起请求。

因此，恢复目标不是创建一个新 session，也不是伪造 goal 状态，而是在同一个已验证的 Codex tmux pane 中，等待限流窗口后提交一次 `Continue`，让 Codex 自己决定如何继续当前 goal。

## 2. 原 watcher 的原理

`tmux-codex-auto-continue` 是一个本地 watcher，核心循环如下：

```text
列出 tmux panes
  ↓
确认 pane 前台进程组确实包含 npm @openai/codex 的 native binary
  ↓
capture-pane 读取当前 viewport 和有限历史
  ↓
严格匹配 Codex 的错误/中断/安全菜单文本
  ↓
等待短暂 settle window，重新确认事件仍在当前 pane
  ↓
确认 pane 非 copy-mode、composer 为空、watcher 仍启用
  ↓
使用 tmux bracketed paste 写入 Continue，再发送真实 Enter
```

它只向已有 pane 注入输入，不创建、重命名、重启、关闭或杀死 tmux session/pane。输入注入前会再次检查进程身份、pane 模式、当前错误文本和 composer，避免把按键发给 shell、旧 pane 或用户正在输入的内容。

原实现已经支持普通 processing error、streaming error、server overloaded、model capacity、部分安全提示和高置信度的中断 `Worked for` 状态。但它依赖“支持的列 0 文本事件 + Continue”，并没有把截图中的 429 文案归类为事件，所以该错误不会进入恢复队列。

## 3. 为什么只加一条正则还不够

单纯加入：

```python
r"^■ exceeded retry limit, last status: 429 Too Many Requests$"
```

仍有三个问题：

### 3.1 会把限流变成请求风暴

普通错误可以在短 settle window 后重试；429 的原因恰恰是服务端拒绝了过多/过快请求。如果每轮 poll 都重新发送 `Continue`，就会继续撞上同一限流窗口。

### 3.2 同一个错误需要事件身份和去重

watcher 每 0.5 秒读取滚动终端历史。相同文本持续留在 pane 中时，不能把它当成无限个新事件；只有同一错误再次被 Codex 重新渲染，才应视为下一次恢复尝试。

### 3.3 截图中的 `Goal active` 不是新一轮输出

原来的 stale-event 检查会把错误后面的 `• ...` 行视为后续 Codex 输出，从而拒绝恢复。截图中的 `Goal active Objective ... Time ...` 是状态 cell，不是新的 assistant/tool turn；但其他后续输出仍然必须取消旧恢复。因此它必须是一个严格、仅对 429 生效的例外，而不能放宽全局终端校验。

## 4. 当前实现如何解决

### 4.1 严格事件识别

新增 `RATE_LIMIT` 事件，只匹配完整列 0 文本：

```text
■ exceeded retry limit, last status: 429 Too Many Requests
```

允许 Codex 显示的末尾 `---` 和空格；以下内容均不会匹配：

- 去掉 `■` 的文本；
- 带 `›`、`•` 或缩进的引用文本；
- 改成 500 或其他 HTTP 状态；
- 改写 `Too Many Requests` 的文案；
- 不完整的错误头。

### 4.2 有界指数退避

每个 pane 的 `PaneState` 记录：

- 已观察到的 429 次数；
- 最近一次 429 时间；
- 本次恢复允许发送的时间点。

新渲染的 429 使用以下延迟：

| 第几次新 429 | 等待时间 |
| ---: | ---: |
| 1 | 1 分钟 |
| 2 | 2 分钟 |
| 3 | 5 分钟 |
| 4 | 10 分钟 |
| 5 及以后 | 15 分钟封顶 |

其他 Codex 事件会重置该退避序列；超过 30 分钟没有新 429 后，下一次 429 也从 1 分钟开始。这样既不会把一次偶发限流永久放大，也不会在服务恢复后一直保持旧的长延迟。

### 4.3 保持原有 fail-closed 发送门槛

即使退避时间到了，发送前仍要满足所有原有条件：

1. pane 的前台进程组仍是已验证的 npm Codex native binary；
2. pane 不在 copy-mode 或其他 tmux mode；
3. watcher 的全局开关仍是 `on`；
4. composer 为空，用户没有正在输入；
5. 429 仍是当前终端事件；
6. 没有安全菜单接管键盘；
7. pane geometry、进程 identity 和当前 viewport 没有发生不允许的变化。

### 4.4 只放行截图中的状态 trailer

对 `RATE_LIMIT` 的当前事件检查，会严格忽略这一类状态行：

```text
• Goal active Objective: <non-empty objective> Time: <duration>.
```

例如截图中的 `Time: 58m.`。该例外只作用于 429 事件；如果后面出现 `• Ran ...`、新的错误、用户输入或其他未知输出，恢复仍会被取消。

### 4.5 pane mode 下的延迟保持

普通 deferred event 的保留窗口是 30 秒，但 429 的第一阶段退避是 60 秒。若 429 恰好在 copy-mode 中出现，不能因为普通 30 秒窗口到期而把它丢掉。因此 429 deferred event 会保留到“计划重试时间 + 30 秒”，同时发送前仍重新检查 live viewport；超出该边界后 fail closed。

## 5. 代码路径

主要改动集中在 `bin/tmux-codex-auto-continue`：

- `RATE_LIMIT_RE`：严格识别 429 文案；
- `event_records()`：把文本转换成 `rate_limit` 事件；
- `PaneState`：保存退避计数、最近观察时间和 `retry_after`；
- `observe_rate_limit_events()`：计算并记录 1/2/5/10/15 分钟退避；
- `schedule_pending()`：把 429 的计划发送时间纳入 pending event；
- `terminal_event_is_current_text()`：允许严格的 `Goal active` trailer；
- `expire_deferred_events()`：为 pane-mode 下的 429 保留足够长的边界。

发送动作仍复用原来的 `submit_text()`，所以实际输入序列没有另起一套不安全的实现：

```text
tmux set-buffer Continue
tmux paste-buffer -p
tmux send-keys Enter
```

## 6. 如何验证

单元/self-test 覆盖：

- 正确 429 文案识别；
- `---` 后缀识别；
- 引用、缩进、改状态码和改文案拒绝；
- `Goal active` trailer 可接受；
- 其他后续输出仍拒绝；
- 退避序列为 60/120/300/600/900 秒；
- 其他事件和 30 分钟空闲后退避重置；
- pane-mode 中的 429 不在普通 30 秒窗口内误丢失。

隔离 tmux integration test 使用 native fake-Codex 进程，注入截图同构的错误和状态行，验证：

1. 429 出现后 3 秒内不会发送 `Continue`；
2. 约 60 秒后只发送一次 `Continue`；
3. 不需要创建或重启 pane；
4. 现有普通错误、上下文压缩、安全菜单和 watcher restart 回归不受影响。

本地完整集成测试命令：

```sh
python3 bin/tmux-codex-auto-continue --self-test
python3 tests/worked_integration.py
python3 tests/install_integration.py
python3 -m py_compile bin/tmux-codex-auto-continue tests/worked_integration.py tests/install_integration.py
sha256sum --check SHA256SUMS
```

## 7. 边界和不能承诺的事情

- 这不是 Codex 官方的 session/goal API，也不会修改 Codex 的内部 goal 文件或服务端状态。
- watcher 启动时会 baseline 已经存在的终端文本，不会盲目重放启动前的旧 429；这样可以避免安装/重启 watcher 时误触发历史请求。
- 如果 Codex 版本改变 UI 文案或布局，未知布局会被忽略；需要新增精确 fixture 和回归测试后再支持。
- 如果账号/模型本身仍处于 usage limit，`Continue` 可能再次得到 429；退避会继续加长并封顶，不保证服务端一定恢复。
- watcher 只负责当前 pane 的输入恢复，不负责跨重启的 Codex 进程恢复、goal 持久化、权限批准、GPU/远程任务租约或业务 checkpoint。

## 8. 一句话总结

原问题是“429 错误没有被识别”；真正的修复是“识别 429 + 保留当前 goal + 有界退避 + 严格重验证 + 只忽略合法的 Goal 状态 trailer”，而不是简单地多按几次 Enter。

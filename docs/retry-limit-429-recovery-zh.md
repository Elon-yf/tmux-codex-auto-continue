# tmux-codex-auto-continue：429 与 Goal 恢复的原理和实现

这份文档从操作系统的最底层开始解释这个插件。假设读者此前不知道
`tmux`、`pane`、PTY、TUI 或 Codex Goal 是什么，也可以顺着读下来。

本文针对的现象是终端里出现类似下面的内容：

```text
■ exceeded retry limit, last status: 429 Too Many Requests
• Goal active Objective: 现在做的就是goal Time: 58m.
```

本次修复的行为规则是：

| 当前终端状态 | 429 退避结束后发送的输入 |
| --- | --- |
| 429 仍是当前错误，并且有可恢复的 Goal 状态（active、paused、stalled/blocked、usage limited） | `/goal resume` |
| 429 仍是当前错误，但没有可恢复的 Goal | `Continue` |
| Goal 已 complete 或 limited by budget | 不自动把它重新启动 |

这里的“发送”不是调用一个隐藏 API，而是像用户一样向**同一个已经运行的
Codex CLI 终端**粘贴一行文字，再按一次 Enter。官方 CLI 文档明确列出了
`/goal pause`、`/goal resume` 和 `/goal clear`；桌面应用的按钮说明不能替代
终端 TUI 的命令。参见 [Codex CLI developer commands](https://developers.openai.com/codex/cli/slash-commands)
和 [Follow a goal](https://developers.openai.com/codex/use-cases/follow-goals)。

---

## 1. 先建立整体地图：这个插件位于哪里

从用户按键到 Codex 收到输入，中间有多个层。把这些层混在一起，就很容易
误以为插件在“控制 OpenAI 服务”或“直接修改 Goal 数据库”；实际上它做的事
要窄得多。

```text
┌──────────────────────────────────────────────────────────────┐
│ Linux 主机                                                    │
│                                                              │
│  终端模拟器（kitty、gnome-terminal、DingTalk 内嵌终端等）      │
│       │ 键盘字符 / 控制序列                                   │
│       ▼                                                      │
│  tmux client ───────► tmux server                             │
│                              │                                │
│                              ▼                                │
│                    session / window / pane                   │
│                              │                                │
│                         pane 的 PTY                           │
│                              │                                │
│                    shell + Codex CLI TUI                     │
│                              │                                │
│                         HTTPS 请求                            │
│                              ▼                                │
│                       Codex 服务端                            │
└──────────────────────────────────────────────────────────────┘

tmux-codex-auto-continue（另一个本地 Python 进程）
        │
        ├─ tmux list-panes / /proc：确认目标真的是 Codex
        ├─ tmux capture-pane：读取 pane 上已经渲染的文字
        ├─ 本地正则和状态机：判断是否出现可恢复事件
        └─ tmux set-buffer + paste-buffer + send-keys Enter：注入输入
```

每一层的职责如下：

| 层 | 它是什么 | 它在本问题中做什么 |
| --- | --- | --- |
| Linux 内核 | 管理进程、进程组、伪终端、文件描述符和调度 | 让 shell、Codex 和 tmux 有真实的进程与输入输出通道 |
| 终端模拟器 | 把字节流显示成窗口，把键盘转换成终端输入 | 画出你看到的字符；它通常不知道 Goal 的语义 |
| tmux server | 后台保存终端会话和窗口布局 | 即使终端窗口关闭，session/pane 仍可继续运行 |
| pane | tmux 中一个分割出来的终端区域及其 PTY | Codex CLI 实际运行、显示文字、接收输入的地方 |
| Codex CLI/TUI | 交互式终端程序 | 调用模型服务、维护会话/Goal 状态、渲染错误和状态行 |
| watcher | 本插件启动的本地守护进程 | 观察已存在的 pane，并在严格条件满足时模拟用户输入 |
| Codex 服务端 | 接收请求并返回模型结果的远端服务 | 可能返回 HTTP 429；watcher 不直接接触这一层 |

因此，这个项目不是一个 API 客户端，也不是一个任务调度器。它是一个
**基于 tmux 终端画面的本地输入恢复器**。

## 2. Linux 基础：进程、PTY 和前台进程组

### 2.1 终端不是“一个字符串窗口”

Linux 终端程序通过一个伪终端（PTY，pseudo-terminal）通信。PTY 有两端：

```text
程序端（slave）                         控制端（master）
shell / Codex  ──写输出──►  tmux / 终端模拟器
shell / Codex  ◄─读输入──  tmux / 终端模拟器
```

Codex 打印的字符先进入 PTY，再由 tmux 维护屏幕网格，最后由终端模拟器显示。
插件并不从 Codex 的网络连接里读 JSON，也不从模型服务拿状态；它读取 tmux
保存的屏幕网格和有限滚动历史。因此插件看到的是“用户能看到的 UI 证据”，而
不是 Codex 内部 Rust 对象的直接引用。

### 2.2 为什么要看前台进程组

一个 pane 的 `pane_pid` 往往是 shell，而不是 Codex 本身。用户在 shell 里
启动 `codex` 后，shell 会把 Codex 放进一个前台进程组；输入按键会送给这个
前台组。只检查命令行里是否出现字符串 `codex` 不够安全，因为：

- shell 可能恰好有一个名为 codex 的参数；
- Codex 已退出，但旧文字仍留在滚动历史里；
- pane 可能在 watcher 检查期间切换回 shell；
- 同一台机器上可能同时有多个 agent 和多个 pane。

Linux `/proc/<pid>/stat` 提供进程组和控制终端前台进程组信息。watcher 的
身份检查大致是：

```text
pane_pid
  └─读取前台 PGID（foreground process group）
      └─遍历该组的进程树
          └─找到 npm @openai/codex/.../vendor/.../codex native binary
```

只有这条链条成立，插件才认为“这个 pane 当前由 Codex 拥有键盘”。如果
Codex 退出、进程组改变或 `/proc` 信息不一致，插件会放弃发送。相关 Linux
概念可参阅 [`proc_pid_stat(5)`](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)。

### 2.3 什么是 TUI

TUI（terminal user interface）是运行在字符终端里的交互界面。Codex TUI
通过 ANSI 控制序列移动光标、画分隔线、显示 `›` 输入提示和 `•` 状态行。
屏幕上看到的行不一定是一条“模型消息”：

- `■ ...` 通常是错误/诊断信息；
- `• Goal ...` 是 Goal 状态信息；
- `• Ran ...`、`• Edited ...` 是活动记录；
- `› ...` 是 composer（输入框）里的用户输入；
- `─ Worked for ... ─` 是一次 turn 的耗时边界。

所以 watcher 必须按**行的前缀、列位置和上下文**识别，而不能搜索一个
孤立的关键词 `429` 或 `goal`。

## 3. tmux 基础：session、window、pane 到底是什么

### 3.1 三个层级

```text
tmux server（一个后台 tmux 实例，通常由 socket 标识）
└── session（例如 work）
    └── window（例如 0:codex）
        ├── pane %1（左侧 shell）
        └── pane %2（右侧 Codex CLI）
```

- **server**：后台进程，持有所有 session 和 pane。不同 `tmux -L name`
  可以有不同 server/socket。
- **session**：一组可脱离、可重新连接的工作环境。
- **window**：session 中的一个标签页。
- **pane**：window 内的一个终端分割区域。`%2` 是 pane 的 ID，不是屏幕
  坐标；它对应一个真实 PTY 和一个进程树。

“pane”可以类比成一个持续存在的远程终端插座：终端窗口只是插在这个插座
上的显示器，tmux server 和 pane 可以在显示器关闭后继续存在。

### 3.2 如何亲眼查看 pane

```sh
# 列出所有 pane、PID、尺寸和 mode
tmux list-panes -a -F \
  '#{pane_id} pid=#{pane_pid} mode=#{pane_in_mode} size=#{pane_width}x#{pane_height}'

# 把 pane 当前可见网格打印到 stdout；-J 合并视觉换行
tmux capture-pane -p -J -t %2

# 查看某个 pane 的前台进程（这里只是辅助观察）
ps -o pid,pgid,tpgid,stat,cmd -p "$(tmux display-message -p -t %2 '#{pane_pid}')"
```

插件使用的是同一组 tmux 能力，但通过 `tmux -S <socket>` 明确指定 server，
避免把输入发到另一个 socket 的 pane。

### 3.3 pane mode 为什么重要

当用户按下 prefix+`[` 进入 copy-mode，或打开 tmux 的其他选择界面时，键盘
不再属于 Codex composer。此时自动发送 `/goal resume` 或 Enter 可能只是滚动
屏幕、选择菜单项，甚至破坏用户操作。

因此 `pane_in_mode != 0` 被视为“键盘被占用”，事件可以暂存，但绝不发送。
退出 mode 后，插件还要重新确认同一个事件仍在当前画面中；如果用户已经
输入了内容或 Codex 已经产生新输出，就丢弃旧事件。

## 4. Codex Goal 和普通 Continue 的区别

### 4.1 普通 turn

普通 Codex 交互大致是：

```text
用户输入 ─► Codex CLI ─► 模型/工具循环 ─► 最终回答或错误
             ▲                              │
             └──────── Continue ────────────┘
```

`Continue` 是一条普通的用户输入。它要求当前线程再开始一个 turn，但它
本身不改变 Goal 的持久状态。

### 4.2 Goal turn

`/goal <objective>` 会把一个持久目标附加到当前**已保存的 thread/session**。
Goal 有自己的生命周期和使用量，例如：

```text
             /goal <objective>
                    │
                    ▼
      active ──► paused ──► active
         │          │
         ├──────► stalled/blocked
         ├──────► usage limited
         ├──────► limited by budget
         └──────► complete
```

官方 CLI 的 TUI 会在两个位置表达 Goal 状态。第一处是 turn 记录区里的信息
cell，也就是截图中以 `•` 开头的行：

```text
• Goal active Objective: ... Time: 58m.
• Goal paused Objective: ... Time: 58m.
• Goal stalled Objective: ... Time: 58m.
• Goal usage limited Objective: ... Time: 58m.
```

其中 `stalled` 是用户可见的 blocked 状态标签。Goal 状态行是 TUI 的信息
cell，不是一次新的 assistant/tool turn；这正是截图中 429 后面那一行不能
被当成“已经有新输出”的原因。

第二处是输入框附近、通常靠右显示的 footer。终端宽度和当前布局会改变它的
横向位置，但 Codex 0.147.0 的状态文案是：

```text
  gpt-5.4                 Pursuing goal (58m)                    # active
  gpt-5.4                 Goal paused (/goal resume)             # paused
  gpt-5.4                 Goal stalled (/goal resume)            # blocked/stalled
  gpt-5.4                 Goal hit usage limits (/goal resume)   # usage limited
  gpt-5.4                 Goal unmet (50K / 50K tokens)          # limited by budget
  gpt-5.4                 Goal abandoned                         # limited by budget、无用量文本
  gpt-5.4                 Goal achieved (58m)                    # complete
```

因此你看到右下角的 `Goal stalled (/goal resume)` 不是按钮，也不是终端模拟器
添加的提示；它是 **Codex TUI 自己给出的 slash-command 操作提示**。左边的
模型、目录等信息和右边 Goal 文案由 TUI 画在同一行，右侧位置随 pane 宽度
改变；这一行不以记录区的 `•` 开头。tmux 的 `capture-pane` 读取的是整张字符
网格，所以 watcher 能看到该 footer，之后仍然只能通过向 pane 的 PTY 输入
字面量 `/goal resume` 来执行它。

### 4.3 为什么 Goal 要用 `/goal resume`

当 Goal 处于 paused、stalled/blocked 或 usage-limited 等需要恢复的状态时，
普通 `Continue` 只是追加一条普通消息；`/goal resume` 才是 Codex CLI 提供的
Goal 状态操作。

截图中的 `Goal active` 需要单独解释：这里“active”描述的是 Goal 元数据仍然
处于活动态，不等于刚才那次模型请求仍在执行。前一行已经说明请求重试耗尽，
所以执行循环事实上停在 429 上。这个 watcher 采用明确的恢复策略：

```text
429 已终止当前执行 + 当前仍有 active Goal
        │
        └─► 输入 /goal resume，重新从 Goal 控制入口触发
```

也就是说，`active + 429 -> /goal resume` 是本插件对这个组合状态的恢复规则；
它不是把 `active` 误说成 `paused`。这样既遵守你的输入要求，也保留了两种
状态各自真正表达的含义。

注意：这里说的是**终端 CLI 命令**。桌面应用有进度条按钮，但本插件的目标
是 Linux tmux 中的 Codex TUI，输入只能通过终端注入，所以实现使用字面量
`/goal resume`，不依赖任何桌面按钮。

### 4.4 为什么不能看到 `goal` 这个单词就发送 resume

目标文本本身可能是“修复 goal parser”，普通回答也可能提到 goal。可靠的
判定必须同时满足：

1. 行首是 Codex 的状态前缀 `•`，而不是用户输入或引用内容；
2. 状态词是可恢复 Goal 状态，而不是任意出现的字符串；
3. 同一个当前 pane 中存在仍有效的 429 事件；
4. composer 为空，没有用户正在输入；
5. pane 仍由同一个 Codex 前台进程组拥有。

完成状态和 limited-by-budget 状态不会触发 `/goal resume`，避免把已经结束或
达到硬预算的工作偷偷重新启动。

## 5. 429 到底发生了什么

HTTP 429（Too Many Requests）表示服务端暂时拒绝请求，常见原因包括账户/模型
使用额度、速率窗口或服务侧保护。Codex CLI 通常会在客户端进行有限次重试；
当这些重试也失败时，TUI 渲染：

```text
■ exceeded retry limit, last status: 429 Too Many Requests
```

这句话不是“插件已经重试了很多次”，而是 Codex 自己的请求循环已经达到
重试上限。此时立即再按一次输入，通常只会再次撞同一个限流窗口，形成：

```text
429 ─► Continue ─► 429 ─► Continue ─► 429 ─► ...
```

因此恢复必须同时解决两个问题：

- **识别问题**：旧 watcher 没有把这条精确文本纳入事件集合；
- **节流问题**：识别后不能每个 0.5 秒 poll 都发送一次。

Goal 行还增加了第三个问题：普通 `Continue` 和 `/goal resume` 的语义不同。

## 6. watcher 的状态机：从画面到一次输入

插件每个 0.5 秒轮询一次，但“轮询”不等于“发送”。每个 pane 有一个独立
的 `PaneState`，保存上一次画面中的事件、进程身份、尺寸、pending 事件、
退避计数和最近发送时间。

完整流程如下：

```text
┌──────────────┐
│ 发现 pane     │ list-panes
└──────┬───────┘
       ▼
┌────────────────┐      否
│ 前台组是 Codex? │────────────► 丢弃该 pane
└──────┬─────────┘
       │是
       ▼
┌────────────────┐
│ capture-pane    │ 读取 viewport + 有限 scrollback
└──────┬─────────┘
       ▼
┌────────────────┐      否
│ 严格事件匹配?   │────────────► 仅更新 baseline
└──────┬─────────┘
       │是
       ▼
┌────────────────┐
│ appended_events │ 去重：是不是新渲染的一次事件？
└──────┬─────────┘
       ▼
┌────────────────┐      是
│ pane mode?      │────────────► 有界暂存，不注入
└──────┬─────────┘
       │否
       ▼
┌────────────────┐
│ settle window   │ 等待 TUI 停止重绘（1.5s）
└──────┬─────────┘
       ▼
┌────────────────────────────┐
│ 429? 当前 Goal 属于哪一类？ │
└──────┬─────────────────────┘
       │
       ├─ 可恢复 Goal ─► 选择 `/goal resume`
       ├─ 没有 Goal ───► 选择 `Continue`
       └─ 终态 Goal ───► 取消，不发送
       ▼
┌────────────────┐
│ bounded backoff │ 429 等待 60/120/300/600/900s
└──────┬─────────┘
       ▼
┌─────────────────────────┐      任一失败
│ 发送前重新 capture/校验  │────────────► 清除 pending，fail closed
└──────┬──────────────────┘
       │全部通过
       ▼
┌─────────────────────────────────────────┐
│ set-buffer → bracketed paste → Enter    │
└─────────────────────────────────────────┘
```

### 6.1 baseline：为什么 watcher 启动时不立即重放旧错误

watcher 启动或重启时，会把当时已经存在的事件保存为 baseline。否则只要 pane
里还留着昨天的 429，重启 watcher 就会误认为它是刚发生的新错误，再提交一次
输入。baseline 是“只处理 watcher 运行期间新观察到的变化”的边界。

### 6.2 appended event：为什么同一行不会每半秒触发一次

`capture-pane` 每次都可能返回相同的滚动历史。插件把前后事件列表做尾部/头部
重叠匹配，只把新增部分视为 `new_events`。同一条错误保持在屏幕上时，事件
身份不变；Codex 再次渲染一条新的错误记录时，才会出现新的事件。

### 6.3 settle window：为什么不是一匹配就发

TUI 可能先画错误头，再画 Goal 状态行，再把 composer 恢复出来。立即注入会
和重绘竞争，导致输入被写入错误位置。短暂 settle window 允许界面稳定，发送
前还会重新 capture，因此 settle 不是唯一安全措施。

### 6.4 bounded backoff：为什么是 60/120/300/600/900 秒

429 的根因是请求太早或额度未恢复；短间隔重试会放大问题。每个 pane 的新
429 使用有界序列：

| 新渲染的第几次 429 | 延迟 |
| ---: | ---: |
| 1 | 60 秒 |
| 2 | 120 秒 |
| 3 | 300 秒 |
| 4 | 600 秒 |
| 5 及以后 | 900 秒封顶 |

其他 Codex 事件会重置序列；30 分钟没有新 429 也会重置。这样一次偶发限流
不会永久污染这个 pane，同时连续限流不会变成请求风暴。

### 6.5 deferred event：为什么 copy-mode 下不能简单丢弃

429 的第一次退避就是 60 秒，而普通 pane-mode 暂存窗口只有 30 秒。如果在
copy-mode 里观察到 429，不能让普通 TTL 在 30 秒时把它丢掉；该事件会保留到
计划重试时间之后的有限窗口，但退出 mode 后仍必须重新确认画面没有变化。

## 7. “当前事件”检查为什么这么严格

滚动历史里出现过一个错误，不代表现在还应该对它操作。发送前的检查至少
包括：

1. **精确错误行**：必须是列 0 的 `■ exceeded retry limit...`；缩进、引用、
   改写文案或改成 500 都不匹配。
2. **事件位置**：429 必须是当前事件列表的最后一个可操作错误。
3. **合法 Goal 证据**：429 后可以有严格格式的 `• Goal ...` trailer；当前
   viewport 的 footer 也可以是严格的 `Pursuing goal`、`Goal stalled
   (/goal resume)` 等文案。前者必须属于最后一次 429，不能借用滚动历史里的
   旧 Goal；后者必须仍是当前屏幕 footer，而不是普通回答里提到这些单词。
4. **其他输出拒绝**：`• Ran ...`、新的 `■`、用户输入或未知行都会取消
   旧 pending。
5. **composer 空**：底部必须是空的 `› `，不能覆盖用户已经输入的文字。
6. **没有 tmux mode**：copy-mode、选择菜单等都拥有键盘。
7. **进程身份没变**：再次读取前台进程组和 native Codex 路径。
8. **watcher 仍启用**：用户按 toggle 关闭后，所有 pending 都失效。

这是一种 fail-closed 策略：证据不足时宁可不恢复，也不把命令发到错误的
shell、另一个 pane 或用户的半成品输入里。

## 8. 429 + Goal 的具体判定与动作

### 8.1 判定输入

watcher 不读取 Goal 数据库，也不猜测 objective 内容。它只从当前 pane 的
两种官方渲染结构识别状态：

```text
• Goal <status> Objective: <非空文本> [Time: <合法时长>.] [Tokens: <已用>/<预算>.]

› Ask Codex to do anything
  gpt-5.6-sol default                  Goal stalled (/goal resume)
```

其中 `<status>` 的恢复集合是：

- `active`
- `paused`
- `stalled`（Codex 内部通常对应 blocked）
- `usage limited`

`complete` 和 `limited by budget` 是终止/硬限制状态，不在自动恢复集合中。
`Time` 的合法形式不只包括 `58m`，还包括 `30s`、`2h`、`1h 2m` 和带天数的
组合；整小时不能因为没有分钟字段而漏判。零耗时 Goal 可以没有 `Time`，有
token budget 时还会带 `Tokens: 63.9K/50K.`，所以状态解析不能把时间字段写死。
footer 只在当前 composer 之后才成立；出现在旧记录或普通回答里的同一句文字
不能作为 Goal 证据。

### 8.2 动作矩阵

```text
                    当前 Goal 状态
                 ┌───────────────────────┐
                 │ recoverable?           │
                 └──────────┬────────────┘
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼
       有 429 + Goal                  有 429 + 无 Goal
             │                             │
             ▼                             ▼
       `/goal resume`                 `Continue`
```

如果证据表明 Goal 已 complete/limited by budget，第三条分支是不发送。这里
不能把“有一个终态 Goal”降级成“没有 Goal”，否则 `Continue` 会偷偷开启一个
本不该开启的新 turn。

这条分支必须在**实际发送前**再次计算，而不是只在第一次发现错误时计算。
因为用户可能在等待 60 秒期间手动暂停/清除 Goal，也可能已经手动恢复了它。
发送前看到的状态才是最终依据。

### 8.3 为什么不会把 `/goal resume` 发给普通 pane

`/goal resume` 是 Codex TUI 的 slash command；普通 shell、非 Codex pane 或
没有 Goal 的 Codex pane 不应该收到它。进程身份、严格 Goal 状态行、当前 429、
空 composer 四个条件共同构成门槛。缺一个就回到 `Continue`（如果仍有普通
429 恢复资格）或直接取消。

## 9. 实际输入是怎样注入的

发送 `/goal resume` 和发送 `Continue` 使用同一个安全路径，只有字符串不同：

```text
1. tmux set-buffer -- "/goal resume"
2. tmux paste-buffer -p -d -t <pane>
3. tmux send-keys -t <pane> Enter
```

`paste-buffer -p` 使用 bracketed paste。原因是 Codex TUI 会把快速连续的字符
当作 paste burst；如果在字符事件中间直接发送 Enter，Enter 可能被当成换行而
不是提交。先完成一次明确的 paste，再发送独立的真实 Enter，顺序更可靠。

插件不调用 `xdotool`，不发送全局键，不操作鼠标，也不把命令写进 shell 历史。
输入直接进入指定 pane 的 PTY。

## 10. 代码结构和概念的对应关系

主要实现位于 [`bin/tmux-codex-auto-continue`](../bin/tmux-codex-auto-continue)：

| 概念 | 代码职责 |
| --- | --- |
| 事件词法识别 | `RATE_LIMIT_RE`、Goal 状态正则和 `event_records()` |
| pane 身份 | `list_panes()`、`codex_process_identity()`、`/proc` 读取 |
| 当前画面 | `capture_visible_text()`、`capture_event_text()` |
| 去重 | `appended_events()` |
| 当前事件验证 | `terminal_event_is_current_text()`、`terminal_suffix_is_idle()` |
| 429 退避 | `PaneState.rate_limit_*`、`observe_rate_limit_events()` |
| pending/deferred | `schedule_pending()`、`defer_events()`、`expire_deferred_events()` |
| Goal/普通动作选择 | Goal 状态解析及发送前的动作重判定 |
| 输入注入 | `submit_text()` |
| watcher 单实例 | socket 派生 lock 文件和 `run_daemon()` |

这里的 `PaneState` 是 watcher 自己的内存状态，不是 Codex 的 Goal 状态。两者
不要混淆：

```text
Codex Goal 状态 ──通过 TUI 状态行被观察──► watcher PaneState
watcher PaneState ──通过 tmux 输入──► Codex TUI 命令
```

watcher 重启后会丢失自己的退避计数并重新 baseline；它不会因此删除或创建
Codex Goal。

## 11. 如何测试：每个测试证明什么

### 11.1 纯函数 self-test

```sh
python3 bin/tmux-codex-auto-continue --self-test
```

覆盖的不是“字符串能不能搜到”，而是状态机边界：

- 正确 429 文案和允许的 `---` 尾缀；
- 去掉 `■`、加缩进、加 `›` 引用、改状态码、改英文文案均拒绝；
- active/paused/stalled/usage-limited Goal trailer 与 footer 的识别；
- complete/limited-by-budget 不选择 `/goal resume` 或 `Continue`；
- 429 + Goal 选择 `/goal resume`；
- 429 + 无 Goal 选择 `Continue`；
- 旧 429 前后的 Goal 不能被错误地借给当前 429；
- `Time: 2h.` 这样的整点时长不会漏判；
- 新输出、手动输入、Goal 状态消失会取消旧事件；
- 退避序列和重置条件；
- pane-mode 延迟事件的保留边界。

### 11.2 worked integration

```sh
python3 tests/worked_integration.py
```

该测试启动隔离的 tmux server 和一个 native fake-Codex 进程，不接触真实
账号或模型服务。它验证真实 tmux 输入路径：

1. 普通可恢复事件仍收到 `Continue`；
2. 429 + Goal 状态在短时间内不会发送任何输入；
3. 第一阶段退避结束后收到且只收到一次字面量 `/goal resume`；
4. 不创建、重命名、重启或杀死测试 pane；
5. copy-mode、菜单、手动输入、watcher 重启和安装迁移回归不受影响。

“429 无 Goal -> `Continue`”的分支由 self-test 覆盖；集成测试重点证明真正
经过 tmux bracketed-paste 和 Enter 后，fake Codex 收到的是 `/goal resume`，
而不是只在 Python 函数里算出了这个字符串。

### 11.3 其他检查

```sh
python3 tests/install_integration.py
python3 -m py_compile bin/tmux-codex-auto-continue \
  tests/worked_integration.py tests/install_integration.py
sha256sum --check SHA256SUMS
git diff --check
```

如果本机没有 `ruff` 或 `shellcheck`，应明确记录为未运行，而不是把它们的
结果猜成通过。

## 12. 安全边界和不能解决的事情

### 能解决

- watcher 运行期间，准确识别当前 pane 的 429 retry-limit 文案；
- 在限流窗口内停止重复请求；
- 保留同一个 Codex pane/thread，不新建 session；
- 有可恢复 Goal 时使用 `/goal resume`，无 Goal 时使用 `Continue`；
- 用户接管键盘、改变 pane 或关闭 watcher 时 fail closed。

### 不能保证

- 账号额度已经恢复；如果服务端仍拒绝，下一次仍可能是 429；
- Codex 改变英文 UI 文案或状态行布局后的兼容性；未知布局会被忽略；
- watcher 自己重启后恢复之前内存里的退避计数；
- 跨机器、跨 tmux server、跨 Codex session 的任务调度；
- Goal 数据库、权限审批、模型选择、业务 checkpoint 或远程 GPU 租约；
- 把已经 complete/limited-by-budget 的 Goal 强行重新打开。

### 为什么这些边界是必要的

如果插件直接改 Codex 的内部数据库或绕过 CLI 状态机，它可能造成 UI 状态和
runtime 状态不一致，形成更难排查的“假恢复”。当前设计只使用 Codex 自己
公开给终端用户的 `/goal resume` 命令，因此状态转换仍由 Codex 完成。

## 13. 从用户视角的最短操作说明

```text
你继续照常在 tmux 里运行 Codex。

出现 429 时：
  1. watcher 先等待，不会立刻连发请求；
  2. 如果画面显示可恢复 Goal 状态，等待结束后输入 /goal resume；
  3. 如果没有 Goal，等待结束后输入 Continue；
  4. 如果你正在 copy-mode、输入文字或关闭 watcher，自动动作会取消。
```

可以用下面的命令观察 watcher 是否运行：

```sh
~/.local/bin/tmux-codex-auto-continue \
  --socket "$(tmux display-message -p '#{socket_path}')" --status
tail -f ~/.cache/tmux-codex-auto-continue.log
```

日志只记录 pane/session 标识和动作类型，不记录完整 pane 内容。

## 14. 参考资料

- [项目仓库：tmux-codex-auto-continue](https://github.com/yeahdongcn/tmux-codex-auto-continue)
- [Codex CLI developer commands（含 `/goal resume`）](https://developers.openai.com/codex/cli/slash-commands)
- [Codex Follow a goal](https://developers.openai.com/codex/use-cases/follow-goals)
- [Codex continuation prompt（官方开源仓库）](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/goals/continuation.md)
- [Codex TUI Goal 状态与命令提示源码](https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget/goal_menu.rs)
- [Codex 0.147.0 TUI footer 状态映射源码](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/tui/src/bottom_pane/footer.rs)
- [tmux 手册](https://man7.org/linux/man-pages/man1/tmux.1.html)
- [`proc_pid_stat(5)`：进程组和前台进程组字段](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)

# FuncGraph 设计文档

> 使用方法和界面说明见 [guide.cn.md](guide.cn.md)；本文只讲实现。
>
> 版本：v0.9.0（语义化版本：修 bug 加修订号，加功能加次版本号，不兼容的改动加主版本号）。本文描述当前实现。`docs/plans/` 下的 2026-03-06 文档是那次重构的计划，保留作历史记录，内容已过时。

## 1. 目标与约束

FuncGraph 把 Linux ftrace `function_graph` 的文本输出转成一个可交互的 HTML 报告，用于内核性能和错误路径分析。

约束：

- **单文件输出**：报告不引用任何外部资源，可以离线打开、作为附件发送。
- **运行时依赖少**：主程序只用 Python 标准库。`--fast` 需要 pyelftools，`--highlight-code` 需要 Pygments。BTF 解析是自己实现的，不引入新依赖。
- **兼容各种采集选项**：用户采集时开的 ftrace 选项各不相同，每一列都可能缺失。工具要能处理，并告诉用户缺了什么、会有什么影响。
- **兼容旧用法**：`python3 funcgraph.py ...` 的命令行和 `import funcgraph` 的公开名称保持不变。

## 2. 目录结构

```
funcgraph.py              命令行入口（薄壳），重新导出 fglib 的公开名称
fastfaddr2line.py         独立的地址解析工具，以子进程方式调用（可单独使用）
fglib/
  constants.py            PROJECT_DIR、版本号、errno 名称表
  model.py                FtraceLine（解析结果的一行）
  util.py                 路径清理、URL 拼接、模块查找、信息面板数据
  highlight.py            ftrace 行和 C 源码的语法高亮
  parser.py               FtraceParser：文本 → FtraceLine 列表，配对折叠块
  resolver.py             AddressResolver：func+off/len → 源码位置（调用 faddr2line）
  report.py               HtmlGenerator：拼装 HTML（内容行、信息面板、内嵌数据）
  assets.py               读取 assets/ 下的前端文件
  timeline.py             TimelineBuilder：按任务重建调用切片、错误链、唤醒、开销修正
  btf.py                  BTF 解析：函数返回类型分类
  prefilter.py            转换前预过滤（pid/comm/cpu/时间窗口/调用树）
  compare.py              两份 trace 按函数比较
  cli.py                  参数解析、参数检查、工具链查找、处理流程
fgcapture.sh              目标机采集脚本（场景预设、开销标定、丢事件检查、恢复 tracefs）
assets/
  report/report.{js,css}  文本视图前端
  views/                  附加视图的注册与标签切换（boot.js，全局）+ 共用样式
  timeline/               时间轴前端：model/render/interact/analysis/panels.js（闭包）+ timeline.css
  compare/                对比视图前端
tests/                    自测，见第 9 节
docs/design.md            本文
```

模块之间是单向依赖，没有循环：

```
constants ← util ← highlight ← resolver ← report ← cli
          ← model ← parser ───────────────────↗
          ← assets ← report
            btf, timeline（只被 cli 使用，彼此独立）
```

前端代码原来以 Python 字符串的形式写在 `HtmlGenerator` 里（约 3700 行），现在放在 `assets/` 下作为普通文件，可以直接编辑、做语法检查。读取时用 `newline=''`，保证输出逐字节稳定。

## 3. 处理流程

```
trace.txt
  │  prefilter.apply()                （可选）按 pid/comm/cpu/时间/调用树缩小，保留完整调用树
  │  FtraceParser.parse()             逐行解析，enable_fold 时配对 { }，生成 fold id
  ▼
list[FtraceLine]
  ├─► AddressResolver.resolve_batch()  按 vmlinux / 每个模块分批解析返回地址
  │      └─ faddr2line 或 fastfaddr2line.py（常驻 addr2line 子进程）
  ├─► (可选) build_compare_data()      compare.compare(当前, 基准) → cmpData
  ├─► (可选) build_timeline_data()
  │      ├─ btf.build_return_db()      vmlinux / .ko / --btf 的返回类型
  │      └─ TimelineBuilder.build()    → 按列存储的 dict（tlData）
  ▼
HtmlGenerator.generate()
  ├─ _prepare_data()                   函数名链接、过滤器字段、模块 URL
  ├─ _generate_content_lines()         每行一个 div；源码块去重放进池中
  ├─ CSS / JS（assets）                文本视图；有 tlData 时追加时间轴
  ▼
output.html
```

`cli.main()` 分为几个阶段：`build_parser()` → `validate_args()` → `collect_module_dirs()` → `find_faddr2line()` → 解析 → （时间轴）→ 生成 → 写文件。

faddr2line 的查找顺序：`<kernel-src>/scripts/faddr2line`，然后 PATH，最后是自带的 `fastfaddr2line.py`。只要指定了 `--fast`，即使找到了原生工具，vmlinux 的解析也改用 `fastfaddr2line.py`。`CROSS_COMPILE` 和 `LLVM` 环境变量决定使用哪个 `addr2line`。

## 4. 文本视图

### 4.1 行结构

trace 的每一行（包括注释行）对应一个 `div.line-container#L<行号>`。行上的 `data-*` 属性用来过滤：`cpu`、`pid`、`comm`、`func`、`params`、`duration`、`heat`、`irqs`、`resched`、`hirq`、`preempt`、`fold-id`、`fold-type`、`fold-ancestors`、`call-depth`。

### 4.2 源码块懒加载

可展开的行（带返回地址的行）后面跟一个空的 `div.expanded-content`，带 `data-src="N"` 属性。源码块的 HTML 去重后放在 `<script type="application/json" id="fgSrc">` 中，是一个 JSON 数组。`fgFill(el)` 在第一次显示时把内容填进去。任何要显示展开块的代码，都必须先调用 `fgFill`。

效果：traceB_small（1.7 万行）中 13024 个展开块去重后只剩几百种，HTML 从 100.7MB 降到 33.4MB，浏览器加载从 6.0s 降到 2.6s。

### 4.3 折叠

- fold id 是 `f` 加入口行号的 36 进制，例如 `fk3`。原来是 `cpu_pid_func_line_depth_indent` 这样的长字符串，光 `data-fold-ancestors` 就占了 116MB。
- `data-fold-ancestors` 是空格分隔的祖先 id 列表，JS 用 `[data-fold-ancestors~=id]` 选择器找到整个子树。
- `_setFolded()` 是唯一修改折叠状态的函数，其他折叠操作都调用它。折叠状态按文件存入 localStorage，键名前缀是文档标题的哈希。

### 4.4 统一过滤器

`field:value` 语法，支持 AND/OR/NOT 和括号，由 `tokenizeFilter → parseFilterExpr → evalExpr` 解析和求值。`graph:` 和 `notrace:` 先算出匹配的入口 fold id 集合（`precomputeGraphSets`），再按祖先关系判断每一行。

过滤结果同步给时间轴：`applyFilter` 和 `clearFilter` 结束时调用 `fgNotifyFilter(text, Set<行号>|null)`，结果存入 `window.fgFilterState`，再调用 `window.fgOnFilter`（由时间轴注册）。

## 5. ftrace 输出格式

`timeline.py` 按 `kernel/trace/trace_functions_graph.c`（6.18）的输出规则解析。行首的每一列都是可选的，依次是：

| 列 | 选项 | 格式 |
|---|---|---|
| 绝对时间 | `funcgraph-abstime` | `%5lu.%06lu \|  ` |
| 相对时间 | （rel time） | `%9llu us \|  ` |
| CPU | `funcgraph-cpu` | ` %d) ` |
| 任务 | `funcgraph-proc` | `comm-pid \| ` |
| 延迟标志 | `latency-format` | ` dNh2. \| `：irqs-off / need-resched / hardirq-softirq / preempt / migrate |
| 耗时 | `funcgraph-duration` | `+ 12.345 us   \|  `，嵌套入口行填空格 |

之后是 `depth*2` 个空格的缩进和函数部分：

```
func(args) {   [/* <-caller+off/len [mod] */]        入口
func(args);    [/* <-caller+off/len [mod] ret=X */]  叶子
}              [/* func [mod] ret=X */]              出口（retval 或 tail 时带函数名）
/* 事件或 trace_printk 文本 */                         注释
==========> |  /  <========== |                       中断入口/出口标记（funcgraph-irqs）
```

另外还有上下文切换标记 ` 3)  sh-1 => <idle>-0 `，以及 `CPU:N [LOST M EVENTS]`。

已确认的内核行为：

- `retval` 只在值属于 `IS_ERR_VALUE` 范围时打印成十进制负数，其他都打印成十六进制。void 函数也会打印返回值，内容是寄存器残值。
- 在 6.18 上，同时开 `funcgraph-retaddr` 和 `funcgraph-args` 时不会输出参数，也没有任何报错。
- 6.18 没有合入 BTF 打印优化，开 `funcgraph-args` 后读取 `trace` 约 36ms/行（树莓派 4 实测）。必须先用 `set_graph_function` 等方式缩小范围。

## 6. 时间轴模型（`fglib/timeline.py`）

### 6.1 能力识别与时间模式

解析时记录 `caps`：abs、rel、cpu、proc、latency、duration、retval、retaddr、tail、args、irq_markers、sched_events、switch_markers。

时间模式按优先级选择：`abs` > `rel` > `compressed`（只有耗时，切片在每个任务内首尾相接）> `count`（既没有时间也没有耗时，每个叶子算 1 个单位）。前端根据 `caps` 显示"缺少 X 会怎样"的提示。

### 6.2 按任务重建调用栈

- 栈的键是 pid。idle 任务的 pid 都是 0，所以按 `('idle', cpu)` 区分。没有 `funcgraph-proc` 时，用上下文切换标记推断当前任务，再不行就按 CPU 区分。
- 任务被换出后可能在另一个 CPU 上恢复，所以不能按 CPU 重建栈。
- 入口时间取时间戳，结束时间 = 开始时间 + duration。两者来自两次不同的时钟读取，实测偏差在 p1 -0.87us 到 p99 +0.93us 之间。
- 配对：出口带函数名时（tail 或 retval）按名字向上找匹配的帧，中间未闭合的帧标记 `TRUNCATED`。找不到入口的出口生成 `ORPHAN` 切片，并收养同一深度下没有父节点的子切片。
- 父节点取栈顶（最内层的未闭合帧），只要它的深度比当前调用小，不要求正好小 1。内核打印的深度有时一下跳两级或更多：中间那一帧进入了，但入口没有记录（arm64 上不可跟踪的 `el1_interrupt` 包着 `irq_enter_rcu`/`do_interrupt_handler`，`arch_stack_walk` 包着 `unwind_find_stack`）。以前这些调用没有父节点，外层的 self 时间把它们也算了进去，时间轴上还空出一行。前端在缺的那一层画"未跟踪帧"（见第 7 节）。
- 后处理：截断帧的结束时间扩展到覆盖其子切片；子切片限制在父切片范围内；同一行的兄弟切片去掉重叠；没有父节点的同行切片截断前一个。这些规则保证了第 9 节测试的不变量。

### 6.3 上下文与延迟窗口

- 有 `latency-format` 时直接取 flags 列，得到上下文（task/softirq/hardirq/NMI）、关中断、BH 关闭、need-resched 类型、preempt 深度和 migrate-disable 深度。
- 没有时根据中断入口函数名推断（如 `gic_handle_irq`、`__sysvec_*`、`handle_softirqs`、`exc_nmi`），以及 `==========>` 标记，并设置 `CTX_INFERRED` 标志。
- need-resched 窗口：从 flags 的第 2 位变成非 `.` 开始，到恢复为 `.` 或任务被换出为止，同时记录期间最大的 preempt 深度和是否关过中断。
- 关中断窗口：只统计 task 上下文中的 `d`/`D`。
- 时间原点 `t0` 取所有带时间的记录中最早的时刻：调用、off-CPU、need-resched、关中断、临界区、唤醒、CPU 运行段。调度事件常常早于第一个被跟踪的调用（例如任务开始跟踪前就在睡眠），只按调用取原点时这些记录会是负时间。前端的 Fit 和初始视图从第一个调用（`tCall`）开始，概览条覆盖 `[tCall, tMax]`，所以前面的空闲时间不会把调用挤到一边。
- 事件行（`/* sched_switch: ... */` 等）不参与临界区和关中断窗口：它们是在调度器或事件代码里关着中断打印的，和任务自己的代码无关。以前任务第一次调用之前的一条 `sched_waking` 会打开一个关中断区间，一直延续到下一次调用，得到负的起点和几百微秒的假区间。
- preempt 基线（`_preempt_baseline`）：function_graph 记录时用的是 `tracing_gen_ctx()`，而它自己的入口路径关着抢占（其他 tracer 用 `tracing_gen_ctx_dec()` 减掉这一层），所以在 `CONFIG_PREEMPTION` 内核上每一行的 preempt 数字都多 1。基线取任务上下文里出现过的最小值，最大为 1：整份 trace 都在关抢占区间里时（最小值 2），这段区间不会被当成基线掩盖。临界区、原子上下文睡眠、need-resched 窗口的 max preempt（`resched[i][3]`），以及前端的 `preemptOff(f)`（详情标签、Preempt depth 叠加层）都减去基线。文本视图的 `data-preempt` 保留原始数字。

### 6.4 Off-CPU

在上下文切换标记处，用被换出任务的最后一个时间戳作为开始，这个任务下一次出现的时间戳作为结束。阻塞原因取栈上第一个不属于调度器内部的函数（例如 `mutex_lock`）。是否被抢占根据栈上有没有 `preempt_schedule`、`irqentry_exit` 等函数判断。有 `sched_switch` 事件时用它的 `prev_state`，有 `sched_wakeup` 时记录唤醒者。

每个任务的运行段（`runs`）在换入时开始新段，并扩展到切片的结束时间。CPU 轨道就是用它生成的。

### 6.5 错误路径

1. **取错误值**：有 BTF 时按返回类型判断：`void`、`uint`、`bool`、`enum` 一律不算错误；`sint` 按类型宽度截断后，值在 [-4095, -1] 内算错误；`ptr` 按 `IS_ERR` 判断，值为 0 时标记 `NULL_RET`；比较函数（名字含 `cmp`/`compare`）不算错误。没有类型信息时只根据数值猜测，并标记为未验证。
2. **源头**：返回 errno，并且没有任何子调用返回同一个值。
3. **传播链**：从源头沿父节点向上，只要父节点返回同一个值就继续。
4. **去向**：链上出现 `*_sys_*` 记为"返回到用户态"；到达采集窗口顶部记为"传出窗口"；否则记为"被调用者处理"。
5. **次要错误**：单帧、去向为"被处理"、且该函数在整份 trace 中没有任何一条链长大于 1 的错误，标为 minor（`chains[i][6]`），默认不显示、不计数。例如 7.2 VM 的一份 trace 中 241 条链有 217 条来自 `memcg_state_val_in_pages`。

效果：traceA 不用 BTF 时有 3768 条错误链，用 BTF 后剩 75 条，其中 50 条一直传到 syscall。

### 6.6 BTF（`fglib/btf.py`）

用标准库读取 ELF 的 `.BTF` 段（支持 `SHF_COMPRESSED`），或直接读原始 BTF 数据（`/sys/kernel/btf/*` 的副本）。模块的 split BTF 需要以 vmlinux 的 BTF 为基础，类型 id 和字符串偏移接在基础之后。解析只保留函数名到 (类别, 字节数) 的映射，查找时会去掉 `.isra.0` 这类编译器后缀。pi4 的 vmlinux 有 55599 个函数，约 1s 解析完。

### 6.7 数据格式（`tlData`）

`to_data()` 输出的 JSON 内嵌在 `<script type="application/json" id="tlData">` 中，所有 `<` 都转义成 `\u003c`。

```
v, mode, caps, t0, btf, n, stats
strings[]                       字符串表（函数名、调用点、参数、返回值……）
tasks[]   {pid, comm, comms, kind(0 任务/1 idle/2 CPU/3 all), cpu, runs[[s,e,cpu]]}
cols      按 (task, start, depth) 排序的列：
          task depth start(同任务内的差分) dur name line eline flags parent
          ret caller args module cpu（稀疏列全空时为 null）
errs[]    [slice, errno, role(0 源头/1 传播), fate, verified, chain]
chains[]  [origin, top, errno, fate, length, verified]
offcpu[]  [task, start, end|null, depth, reason, state, cpu_out, cpu_in, preempted, waker]
resched[] [task, start, end, max_preempt, irqoff, closed_by]
irqoff[]  [task, start, end]
lost[]    [cpu, count, line]
```

`flags` 的位定义（`timeline.py` 和 `model.js` 中的 `FL` 必须保持一致）：

| 位 | 含义 |
|---|---|
| 0-1 | 上下文：0 task / 1 softirq / 2 hardirq / 3 NMI |
| 2 / 3 | 关中断 / BH 关闭 |
| 4-6 | need-resched 类型（`.nNplLbB` 依次编号） |
| 7-10 / 11-14 | preempt 深度 / migrate-disable 深度 |
| 15 | 有 latency 列 |
| 16 / 17 | TRUNCATED（没有出口）/ ORPHAN（没有入口） |
| 18 | 耗时是估算的 |
| 19 | 调用点与父切片不一致（中间有未跟踪的帧） |
| 20 / 21 / 22 | 返回 NULL / 叶子 / 上下文是推断的 |

## 7. 附加视图与时间轴前端

- `assets/views/boot.js` 在全局作用域执行。附加视图注册到 `window.fgViews[name] = {el, init}`，第一次切换到该视图时才调用 `init()`。它提供 `fgSwitchView()`、`fgCurrentView()`，以及在文本行上 Alt+点击跳到时间轴。报告里视图代码在前、boot.js 在后。新增标签页只需在 `HtmlGenerator._views()` 加一项并写一个注册脚本。
- 时间轴的 `model.js`、`render.js`、`interact.js`、`analysis.js`、`panels.js` 按顺序拼接到 `function __tlBoot(){...}` 中，共享同一个作用域，返回 `{resize, revealLine, draw}`。
  - **analysis**：错误汇总（syscall → 源头 → errno）、函数耗时分布（对数直方图、分位数、最慢调用）、调度延迟归因（need-resched 窗口内的自身耗时）、等待链（off-CPU → 记录中的唤醒者 → 唤醒者此前等待的时间关联候选，并非已证实的因果链）。等待链按任务惰性建立已结束 off-CPU 的结束时间索引，二分查找前驱；相同结束时间保留原始首项选择。缓存当前根区间的增量遍历和访问集合，每页追加 100 行，刷新不重走链，CSV 补齐全部后续项但不展开表格。50 ms 间隔阈值和停止原因显式展示；文本过滤和 Overhead 不改变调度几何。`model.js` 的 `OPEN_OFF` 在补齐 trace 末尾几何之前保存原始未闭合区间，不能将其作为已恢复的前驱，也不能据其计算恢复 CPU 或唤醒延迟。
  - 显示用的耗时统一经过 `durOf(i)`；`setAdjusted()` 在实测值和扣除开销后的值之间切换（快捷键 `O`）。切片的位置和宽度始终用实测的 `sStart/sEnd`。
  - **真实调用树与分解**：按 `0..N-1` 的真实父子关系惰性构建 `first/next` 索引（约 `8*N` 字节），只连接同任务、深度递增的边。`subtree` 以迭代前序遍历返回真实后代，不沿可视化行搜索，也没有 256 层截断；直接子调用同样跨过深度缺口。原始耗时分解最多缓存 256 个结果，不受共享过滤、Overhead 或 Inline 影响。缺少分类显示 unattributed，分类合计超出原始区间时提示不一致并改用分类合计作为百分比分母。
  - **详情上下文**：只缓存当前选择的完整祖先索引和合成帧原始跨度，结构不依赖过滤/Overhead。调用栈与错误路径使用迭代父链、访问集合和同任务/深度校验，不再以 128/64 帧上限代替完整性检查；真正的采集边界由最外层已记录祖先的深度确定。统一生成器把真实调用、调用点所在函数、内联、未跟踪和窗口外上下文区分为有类型的行，每页准备/追加 100 行，已显示行和加载焦点保留。CSV 从独立生成器导出全部行，不推动显示游标；仅真实调用具有切片/实测值，保留原始错误链元数据与路径状态。合成帧是否仍被过滤命中取决于其任一真实子调用，不能用 `textHits[syntheticIndex]` 判断。
  - **快慢对比**：缓存键为所选根样本数组和 `sDur` 身份，使用耗时降序/原始索引升序选取最快一半与最慢 `ceil(n/10)`，并列时也不重叠。每个根访问一次真实子树，聚合工作量与被选子树大小之和成正比；递归祖先会重复计入其后代，不宣称全 trace 单次线性聚合。排序/分页/折叠复用准备结果，每页追加 100 行，CSV 包含未加载行或确切分组成员。函数自身耗时随开销修正，原始调度上下文另记口径，二者不能相加；文本过滤只筛根，耗时柱子不筛比较样本。
  - **model**：解码列数据到 TypedArray，计算自身耗时，建立轨道和每个深度的行索引，构建 CPU 轨道数据，生成 DOM 骨架。
  - **合成切片**：下标 `>= N` 的切片不是 trace 里的调用，按顺序追加：内联帧（`buildInline`，`FL.INL`，只在 Inline 视图的 `rowsI` 里）和未跟踪帧（`buildGhosts`，`FL.GHOST`，`rowsT` 和 `rowsI` 里都有）。未跟踪帧填补父子之间缺的深度，名字取自里面那些调用的返回地址（它们的调用者），缺多层时外层名字为 `?`；范围是里面第一个到最后一个调用，相邻且同名的合并成一个。`SYN[i - N]` 保存它们的信息（`kids`、`p`，未跟踪帧另有 `ghost/level/gap/name`）。统计和导航用的 `for (i < N)` 循环只看真实切片。
  - **render**：Canvas 2D 绘制。宽度小于 1.5px 的切片合并成密集块，用二分查找跳过同一像素内的切片。叠加层包括错误、关中断斜纹、need-resched 角标和横条、off-CPU 段、preempt 深度条、各种标记。另有概览条和时间轴刻度。
  - **interact**：命中测试、缩放平移、拖选范围、键盘操作（在捕获阶段处理，并阻止事件传给文本视图）、错误导航、搜索。
  - **panels**：详情面板（错误链、调用栈、最重的子调用）、范围汇总表、跳到文本视图（先展开折叠的祖先）、接收文本过滤器的结果、能力提示、帮助。
    - 调用点源码：详情面板不另存数据，按切片的入口行号找到文本行 `L<n>`，取它的 `data-src`，用 `fgFill` 填入同一个源码块；调用所在的 `文件:行` 从块里第一个位置链接解析（`callSitePos`，按块缓存），悬停提示也用它。`--no-text-view` 时没有。
    - 底部面板的高度由 `.tl-resizer` 拖动设置，按占窗口高度的比例存在 localStorage（`fgTl.detailFrac`），`resize()` 根据它和最小高度（画布 160px、面板 60px）分配空间。`Max`（`M`）隐藏页头和标签页，不跨页面记忆；Notes 的收起状态会记住。
  - 选中切片时，`render()` 在画布上画起止竖线，`drawSelSpan()` 在标尺和概览条上画区间和时长（参照 Perfetto）；有框选范围时不画。两块画布的 CSS 宽度都设为轨道区不含滚动条的宽度 `W`，否则标尺被拉伸，刻度和标记与轨道错开。
  - `probe()`：`fgView('timeline').probe()` 返回只读的模型数据和几个操作（`select`、`zoomTo`），供浏览器测试和控制台调试使用。
- 配色：按上下文选择色相，每个函数在色相上有小幅偏移以便区分相邻切片。也可以按函数名、自身耗时、CPU、模块配色。支持深色和浅色主题（监听 `data-theme` 属性）。
- 文本视图和时间轴通过 trace 行号互相定位：切片记录了入口行号和出口行号，文本行的锚点是 `#L<n>`。
- 切换标签页：大报告的文本视图有上百万个元素。隐藏时用 `content-visibility: hidden`（`#content.fg-off`），浏览器保留布局，切回来不必重新布局；隐藏用的 class 直接加在元素上，不用 `body.xxx 后代选择器`，否则一次 class 变化就要重新匹配整棵树的样式；这些页面元素只查找一次（`fgPageChrome()`）。实测 100 万元素的报告，切回文本视图从 5.2s 降到 1.1s，剩下的是绘制本身。

## 7a. 预过滤、对比、唤醒与开销修正

- **预过滤**（`prefilter.py`）在原始行上工作。每个任务维护一个三态栈：KEEP（在保留的调用树内）、DROP、PENDING（`--graph` 尚未匹配，子调用仍可能匹配）。`--time-range` 按调用的开始时间判断，开始在窗口内的调用连同所有子调用都保留，不会截断。保留的上下文切换标记会带上前后的分隔线。
- **对比**（`compare.py`）对两份 trace 都走时间轴模型，按函数汇总调用次数、总耗时、自身耗时（总耗时减去直接子调用）、最大值、错误源头数。两份都有开销估计时额外生成 `rows_adj`。
- **唤醒**：优先使用 `sched_waking`，它在唤醒者的上下文里触发；`sched_wakeup` 可能在目标 CPU 的 IPI 里触发，唤醒者会变成 idle。记录为 `wakeups[] = [waker_task, ts, target_task, ctx, waker_slice, offcpu_index]`。前端在显示唤醒点时跳过 `try_to_wake_up`、`__wake_up*` 等内部函数，显示唤醒者实际在做的事，例如 `anon_pipe_write`。
- **开销修正**：function_graph 的入口和出口 hook 都计入函数耗时。`fgcapture.sh --calibrate` 用相同选项跟踪 3000 次 `vfs_read`，一次用 `set_graph_function`（跟踪子调用），一次用 `set_ftrace_filter`（只跟踪 vfs_read 本身），由两者中位数之差除以子孙调用数得到每个被跟踪子调用的开销 `child`，叶子耗时的 p1 作为每次调用的测量开销 `self`。修正值 = 实测 − child × 子孙调用数 − self（不小于 0），存为 `cols.adj`。树莓派 4（6.18、lockdep）实测 child ≈ 1.1 us、self ≈ 0.46 us；`vfs_read` 修正后的中位数 1.2 us，与不跟踪子调用时的 0.94 us 接近。开销随 CPU、频率和内核配置变化，修正值只是估计，默认显示实测值。

## 7b. flag 与耗时的进一步分析

- **临界区**：`_track_lat` 在 task 上下文里按行跟踪三种状态：关中断（`d`/`D`）、关抢占（preempt 数字大于基线）、关软中断（`b`/`D`）。基线取 task 上下文中出现过的最小 preempt 深度，6.18 上为 1（跟踪路径本身关了一次抢占）。区间在 flag 恢复、任务换出、或出现 hardirq/softirq 行时结束：中断能进来说明在没被跟踪的代码里已经重新开了中断。记录为 `sections[] = [task, kind, start, end, start_slice, end_slice, max_depth, start_line, end_line]`，起止切片是 flag 变化时栈顶的函数。精度受被跟踪函数密度限制。
- **原子上下文睡眠**：调用 `RE_MAY_SLEEP` 中的函数（`__might_sleep`、`mutex_lock*`、`down*`、`wait_for_completion*`、`msleep`、`schedule_timeout*` 等）时，若入口行关中断、关抢占（高于基线）、关软中断或处于中断上下文，记入 `atomic[] = [slice, bits]`。`vfree` 等内部会延迟到安全上下文执行的函数不在列表中。
- **耗时去向**（前端 `breakdown()`）：对切片的子树分类，结果是实测耗时的精确划分：自身 self、被跟踪子调用的 self 之和、子树中进入更高上下文的中断切片、与该切片重叠的 off-CPU 段（阻塞/抢占，从它所在帧的 self 中扣除）。有开销估计时另外给出其中的跟踪开销。
- **快慢对比**：同名（可限定调用点）调用按耗时排序，取最快一半与最慢 10%，比较两组每次调用在子树中各函数的平均调用次数和 self 时间，以及中断和 off-CPU 时间，按差值排序。

## 7c. 内联、源码来源与新内核

- **内联链**（`inline.py`）：解析 faddr2line 的 `(inlined by)` 输出，得到每个调用点由外到内的帧 `[[函数, 文件, 行], ...]`，`chain[0]` 是包含调用点的被跟踪函数。`HtmlGenerator._attach_inline()` 在地址解析之后把 `调用点字符串索引 → chain` 写入 `tlData.inline`（只保留带内联帧的调用点）。
- **时间轴内联视图**（`model.js` 的 `buildInline`）：同一父切片下连续、内联链前缀相同的子调用合并为一个内联帧，追加为合成切片（下标 ≥ N，`FL.INL`），范围是第一个到最后一个子调用。逻辑深度 `sLd = 父的 sLd + 1 + 内联层数`，两套行索引 `rowsT`（只有被跟踪帧）和 `rowsI`（含内联帧）通过 `applyView()` 切换；所有绘制和命中用 `rowOf(i)`，导航、子树和统计始终用 `rowsT` 和 `sDepth`，合成切片不参与任何统计（循环只到 N）。
- **源码来源**（`sources.py`）：`GitProvider`（`git show REV:path`）和 `UrlProvider`（URL 模板，本地缓存）重新生成 faddr2line 输出中每个位置的上下文行。版本检查：内联链里外层帧的那一行就是调用（内联）内层函数的地方，所以内层函数名必须出现在该行 ±1 行；比例低于 75% 时警告。实测：正确版本 86–94%，错误版本 26–59%。
- **BTF 感知 retval 的内核**（7.2 `trim_retval`）：内核用 BTF 判断返回类型，void 不打印、截断到类型宽度、bool 打印为 true/false、无符号用十进制、超宽类型加 `(trunc)`、ERR_PTR 打印为负 errno。出现 `true/false/(trunc)` 时设置 `caps.retval_btf`，没有主机 BTF 时把负的十进制返回值直接视为已验证的 errno。该内核上 `funcgraph-args` 与 `funcgraph-retaddr` 可以同时输出，读取 11.7 万行仅约 1.1 秒（6.18 上约 36ms/行）。
- **采集辅助的探测**：`fgcapture.sh --args` 先用 `set_ftrace_filter=vfs_read` 做一次小探测，测每行读取耗时和参数是否与 retaddr 共存，再决定保留 retaddr/latency-format 还是关掉，以及是否必须指定 `-g/-p`。

## 7d. 调度频繁时的可读性

实测（7.2 VM，`yes | head | cat`，18.6 万行）：切换标记不到 1% 的行；每次切换在 `__schedule` 下带出 74–154 个调用（1–6% 的切片）；真正影响阅读的是多 CPU 按时间交错，相邻两行属于不同 CPU 的比例为 25–58%。

- **按任务分组**（`report.js` 的 `fgSetTextOrder`）：把 `#content` 按 `data-line-number` 排序后，按任务（pid；idle 按 CPU；无 proc 时按 CPU）分组，繁忙的任务在前、idle 在后；每组前加吸顶的组标题，切换标记之后插入一行摘要（切到谁、多久后在第几行恢复、中间有多少行其他任务）。分隔线和空行在分组时隐藏。折叠和过滤都基于属性，不受顺序影响；恢复时按行号排回。重排时把 `#content` 暂时移出文档；2 万行重排约 0.26s，其余是浏览器重新布局。
- **唤醒箭头降密**（`render.js` 的 `drawWakeups`）：视野内箭头超过 15 条，或某条箭头 12px 内还有其他箭头终点时视为拥挤，拥挤的只在目标恢复处画小箭头；悬停或选中的任务、悬停的 off-CPU 段、范围汇总所在轨道的箭头总是完整画出。右上角显示被折叠的数量。

### 任务导航与完整时间范围

任务导航（`analysis.js` 的 `taskData/taskOrder/showTasks`）懒构建全体 `TRACKS` 的身份事实：真实切片、runs、offcpu、wakeups、resched、irqoff 与 sections 的计数/几何边界及 CPU 集合，错误计数含 minor 源头，不纳入合成帧。原始事实独立于过滤、Overhead 和 Inline；过滤匹配数按 `textHits` 身份另缓存，查询/排序只处理任务，不重扫调用。100 行追加分页与全量 CSV 共用同一排序结果；query 使用字面匹配，名称转义、CSV 复用公式防护。时间列区分实测、压缩和调用顺序口径，跨度含空隙、不是 CPU 用量；开放等待按 `OPEN_OFF` 保留未知恢复和下界身份。

`model.js` 在补开放等待端点前遍历所有调用/调度/窗口边界，确定报告右界，不再仅取调用和已完成等待。`interact.js` 按需将仅调度任务加入 `ORDER`，显示真实头部而非伪造切片；CPU 点击选择实际深度中的最内层真实调用，否则清除旧选择、保留时间视野并显示该任务的 Tasks 行。Tasks 的 Show 则缩放到任务完整记录跨度。Tasks 的标题/工具/页脚均为详情区直接子元素，横向 sticky，紧凑窗口标题纵向滚动，动态排序后重算表头偏移。

## 8. 性能数据

| 场景 | 数据 |
|---|---|
| traceA（18.6 万行）构建时间轴 | 14.2 万个切片，约 1.5s |
| traceB 全量（7.2 万行），带时间轴 | 生成 20s（优化前 66s）；HTML 114MB（懒加载前 330MB），其中时间轴数据 3.1MB |
| 36 万行，`--timeline --no-text-view` | 生成 12.7s，HTML 15.7MB，浏览器约 1.1s 打开 |
| 时间轴初始化（1.3 万个切片） | 约 55ms |
| Tasks（合成 5 万身份、15 万真实调用；Chromium 同步 DOM handler，不含页面加载、后续绘制和下载） | 首开 54.3ms / 100 行；已准备排序 10.6–21.2ms；字面查询 9.6ms；追加至 200 行 2.3ms；重开 5 次中位数 24.3ms；完整 5 万行 CSV handler 86.0ms（另核对导出行数）。新功能容量验证，不是与旧版不同工作量的提速比较 |

生成耗时的优化：源码块按（位置文本，URL）缓存渲染结果（55183 次调用只有 3207 种）；`fastfaddr2line.py` 用 `struct` 直接读 `.symtab`（4s → 0.1s，与 pyelftools 结果逐项一致）；函数名解析与返回地址解析并行。剩余时间主要是 addr2line 本身。

文本视图的体积主要来自每行的 `data-*` 属性和高亮 span。超过约 15 万行时 CLI 会提示改用预过滤或 `--no-text-view`。文本视图的虚拟列表（按需生成 DOM）还没做，见第 11 节。

## 9. 自测

```bash
tests/run.sh             # 单元 + 端到端 + 浏览器冒烟，不需要内核树，约 8s
tests/run.sh --golden    # 另外与已记录的基线逐字节比较
python3 tests/golden.py --update   # 修改代码前先记录基线
```

| 文件 | 覆盖内容 |
|---|---|
| `test_parser.py` | 各种格式行的字段提取、overhead 标记、模块名、折叠配对、fold id 唯一且引用都能找到 |
| `test_btf.py` | 用代码构造 BTF 数据和最小 ELF，测试类型分类、`.BTF` 段读取、split BTF、原始数据作为基础、错误的 magic；设置 `FG_VMLINUX` 时再测真实 vmlinux |
| `test_timeline.py` | 构造的边界格式（完整选项、无 proc、孤儿行、无 duration、上下文切换、BTF 过滤）；pi4 上 6 种选项组合实测数据的模式和能力识别；所有切片的不变量；`FG_TRACES` 可以指定额外的 trace |
| `test_report.py` | 用假的 vmlinux 和 faddr2line 跑完整命令行：功能标记、行数、懒加载池、折叠引用、有无时间轴时文本部分一致、HTML 和 `<script>` 注入、`node --check` 检查所有内联脚本、每种实测数据都能生成时间轴 |
| `test_fastfaddr2line.py` | 符号匹配：精确长度、别名、编译器后缀、同名符号按长度区分、entry offset、最后一个符号；设置 `FG_VMLINUX` 时与内核的 `scripts/faddr2line` 对比结果 |
| `test_browser.py` | Playwright：展开源码块、折叠、错误导航和跳到文本、过滤联动、范围汇总、主题和配色切换，同时检查页面没有 JS 错误 |
| `test_prefilter.py` | 各过滤条件、保留完整调用树、时间窗口语义、过滤后的实测数据仍满足不变量 |
| `test_compare.py` | 按函数统计与差值、错误计数、修正行需要两份都有估计值 |
| `test_inline.py` | 内联链解析（有无 --list、多候选、无输出）与调用点映射 |
| `test_sources.py` | git 正确/错误版本、URL 获取与缓存、缺失文件、路径归一化 |
| `test_flex.py` | 无 vmlinux、stdin/gzip 与普通文件结果一致、多文件按时间合并、配置文件与预设、导出 JSON/CSV |
| `test_fgcapture.py` | 脚本语法、帮助、非 root 拒绝运行、`--args` 探测前清空 `set_graph_function`（实际采集在树莓派 4 和 QEMU 上手工验证） |
| `golden.py` | 6 种配置（x86 完整/最少/时间轴，pi4 完整/默认选项/sched 且不用 BTF），去掉耗时等动态字段后逐字节比较 |

`tests/data/` 中的夹具来自树莓派 4（6.18 arm64）和 QEMU（7.2 x86_64），每份取前 1500 行，说明见 `tests/helpers.py` 的 `FIXTURES`。`FG_GOLDEN_KEEP=1` 时 `golden.py` 保留本次输出（`$FG_GOLDEN_DIR/current`），便于比较 `tlData` 的具体字段。

CI（`.github/workflows/ci.yml`）：每次推送到 master 和每个 PR 在 Python 3.8 和 3.12 上运行 `tests/run.sh`，3.12 装 Chromium 跑浏览器测试（Playwright 不支持 3.8）。CI 上没有内核树，golden 和真实 vmlinux 的测试会跳过，这些仍要在本地跑。测试通过后 `pages` 任务把 `site/index.html`、`sample.html` 和截图发布到 GitHub Pages。

这次架构调整的每一步都用 golden 验证过：JS/CSS 外置、拆包、资源加载统一、`main()` 拆分前后，6 份输出逐字节一致（只有 4 行 JS 文件头注释是有意修改的）。

## 10. 修改指南

- **加一个文本视图的过滤字段**：在 `report.py` 的 `_generate_content_lines` 中输出 `data-<字段>` 属性，在 `_prepare_data` 中生成字段按钮，然后修改 `report.js` 中的 `evalExpr`。
- **加一个时间轴叠加层**：在 `timeline.py` 中计算数据并加入 `to_data()`，在 `model.js` 中解码，在 `render.js` 的 `drawTrack`/`drawSlice` 中绘制，在 `model.js` 的工具栏加开关，在 `panels.js` 的 init 中处理"数据不存在"的情况。
- **改了 flags 的位**：同时修改 `timeline.py` 的 `F_*` 常量和 `model.js` 的 `FL`，并更新第 6.7 节。
- **改了 HTML 输出**：先运行 `python3 tests/golden.py --update` 记录基线，改完后确认只有预期的差异。
- **新的 ftrace 格式**：把一段真实输出加入 `tests/data/`，并在 `test_timeline.py` 的 `FixtureTest.EXPECT` 中写明期望的模式和能力。

## 10a. 示例

`sample.html` 是 `samples/sample_io.txt` 的报告，用 `samples/demo_io.c` 在 QEMU 7.2 上采集，命令见 README 的"示例"一节。程序先打开文件、建好 socket，再连续执行被跟踪的 read/write/sendto/recvfrom，所以时间轴上没有空白。改了报告的输出后要重新生成 `sample.html` 并重拍 `sample.png`（选中 `__x64_sys_sendto`）和 `samples/sample_text.png`（`graph:udp_sendmsg`，展开第一行）；推送后由 CI 发布到 Pages。

使用指南的截图在 `docs/img/`，由 `python3 docs/img/make_shots.py [名字...]` 生成：输入是 `sample.html`、`tests/data/pi4_sched.txt`（调度事件）、`tests/data/pi4_nodur.txt`（深度跳级）和 `samples/sample_io_slow.txt`（对比用，`sendto` 的软中断顺带释放了 55 个 skb 的那次采集）。脚本通过 `probe()` 选中切片、缩放、打开面板。界面变了就重新运行，和改动一起提交。

## 11. 已知限制

- 时间轴依赖文本格式。读取 `trace_pipe_raw`（二进制）可以绕过 6.18 格式化参数很慢、以及 args 和 retaddr 不能同时输出的问题，但还没有实现。
- 没有 `latency-format` 时，上下文是根据函数名推断的，覆盖了常见的中断入口函数，但不完整。
- `funcgraph-abstime` 只精确到 us。时间戳和 duration 不一致的部分用限制在父切片内的方式修正，修正次数记录在 `stats.clamped` 中。
- 文本视图仍然为每一行生成 DOM，十几万行以上的 trace 浏览器难以打开；目前的办法是预过滤或 `--no-text-view`。下一步是虚拟列表，需要重写 `report.js` 中直接操作 DOM 的过滤、折叠和键盘导航。
- 开销修正假设每个被跟踪调用的开销相同；在中断、迁移或缓存行为不同的路径上会有偏差。

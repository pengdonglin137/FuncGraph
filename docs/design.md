# FuncGraph 设计文档

> 版本：v0.7 + Timeline（`timeline` 分支）。本文描述当前实现。`docs/plans/` 下的 2026-03-06 文档是那次重构的计划，保留作历史记录，内容已过时。

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
  timeline.py             TimelineBuilder：按任务重建调用切片、错误链分析
  btf.py                  BTF 解析：函数返回类型分类
  cli.py                  参数解析、参数检查、工具链查找、处理流程
assets/
  report/report.{js,css}  文本视图前端
  timeline/               时间轴前端：boot.js（全局）+ model/render/interact/panels.js（闭包）+ timeline.css
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
  │  FtraceParser.parse()             逐行解析，enable_fold 时配对 { }，生成 fold id
  ▼
list[FtraceLine]
  ├─► AddressResolver.resolve_batch()  按 vmlinux / 每个模块分批解析返回地址
  │      └─ faddr2line 或 fastfaddr2line.py（常驻 addr2line 子进程）
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
- 后处理：截断帧的结束时间扩展到覆盖其子切片；子切片限制在父切片范围内；同一行的兄弟切片去掉重叠；没有父节点的同行切片截断前一个。这些规则保证了第 9 节测试的不变量。

### 6.3 上下文与延迟窗口

- 有 `latency-format` 时直接取 flags 列，得到上下文（task/softirq/hardirq/NMI）、关中断、BH 关闭、need-resched 类型、preempt 深度和 migrate-disable 深度。
- 没有时根据中断入口函数名推断（如 `gic_handle_irq`、`__sysvec_*`、`handle_softirqs`、`exc_nmi`），以及 `==========>` 标记，并设置 `CTX_INFERRED` 标志。
- need-resched 窗口：从 flags 的第 2 位变成非 `.` 开始，到恢复为 `.` 或任务被换出为止，同时记录期间最大的 preempt 深度和是否关过中断。
- 关中断窗口：只统计 task 上下文中的 `d`/`D`。

### 6.4 Off-CPU

在上下文切换标记处，用被换出任务的最后一个时间戳作为开始，这个任务下一次出现的时间戳作为结束。阻塞原因取栈上第一个不属于调度器内部的函数（例如 `mutex_lock`）。是否被抢占根据栈上有没有 `preempt_schedule`、`irqentry_exit` 等函数判断。有 `sched_switch` 事件时用它的 `prev_state`，有 `sched_wakeup` 时记录唤醒者。

每个任务的运行段（`runs`）在换入时开始新段，并扩展到切片的结束时间。CPU 轨道就是用它生成的。

### 6.5 错误路径

1. **取错误值**：有 BTF 时按返回类型判断：`void`、`uint`、`bool`、`enum` 一律不算错误；`sint` 按类型宽度截断后，值在 [-4095, -1] 内算错误；`ptr` 按 `IS_ERR` 判断，值为 0 时标记 `NULL_RET`；比较函数（名字含 `cmp`/`compare`）不算错误。没有类型信息时只根据数值猜测，并标记为未验证。
2. **源头**：返回 errno，并且没有任何子调用返回同一个值。
3. **传播链**：从源头沿父节点向上，只要父节点返回同一个值就继续。
4. **去向**：链上出现 `*_sys_*` 记为"返回到用户态"；到达采集窗口顶部记为"传出窗口"；否则记为"被调用者处理"。

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

## 7. 时间轴前端（`assets/timeline/`）

- `boot.js` 在全局作用域执行，提供 `fgSwitchView()`、在文本行上 Alt+点击跳转，并在第一次切换到时间轴时调用 `__tlBoot()` 初始化。
- `model.js`、`render.js`、`interact.js`、`panels.js` 按顺序拼接到 `function __tlBoot(){...}` 中，共享同一个作用域，返回 `{resize, revealLine, draw}`。
  - **model**：解码列数据到 TypedArray，计算自身耗时，建立轨道和每个深度的行索引，构建 CPU 轨道数据，生成 DOM 骨架。
  - **render**：Canvas 2D 绘制。宽度小于 1.5px 的切片合并成密集块，用二分查找跳过同一像素内的切片。叠加层包括错误、关中断斜纹、need-resched 角标和横条、off-CPU 段、preempt 深度条、各种标记。另有概览条和时间轴刻度。
  - **interact**：命中测试、缩放平移、拖选范围、键盘操作（在捕获阶段处理，并阻止事件传给文本视图）、错误导航、搜索。
  - **panels**：详情面板（错误链、调用栈、最重的子调用）、范围汇总表、跳到文本视图（先展开折叠的祖先）、接收文本过滤器的结果、能力提示、帮助。
- 配色：按上下文选择色相，每个函数在色相上有小幅偏移以便区分相邻切片。也可以按函数名、自身耗时、CPU、模块配色。支持深色和浅色主题（监听 `data-theme` 属性）。
- 文本视图和时间轴通过 trace 行号互相定位：切片记录了入口行号和出口行号，文本行的锚点是 `#L<n>`。

## 8. 性能数据

| 场景 | 数据 |
|---|---|
| traceA（18.6 万行）构建时间轴 | 14.2 万个切片，约 1.5s |
| traceB 全量（7.2 万行），带时间轴 | HTML 114MB（懒加载前 330MB），其中时间轴数据 3.1MB |
| 时间轴初始化（1.3 万个切片） | 约 55ms |

剩下的体积主要来自每行的 `data-*` 属性和高亮用的 span。如果要继续减小，下一步是把文本行也改成虚拟列表，按需生成 DOM。

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
| `golden.py` | 6 种配置（x86 完整/最少/时间轴，pi4 完整/默认选项/sched 且不用 BTF），去掉耗时等动态字段后逐字节比较 |

`tests/data/` 中的 6 份夹具来自树莓派 4（6.18 arm64），每份取前 1500 行，说明见 `tests/helpers.py` 的 `FIXTURES`。

这次架构调整的每一步都用 golden 验证过：JS/CSS 外置、拆包、资源加载统一、`main()` 拆分前后，6 份输出逐字节一致（只有 4 行 JS 文件头注释是有意修改的）。

## 10. 修改指南

- **加一个文本视图的过滤字段**：在 `report.py` 的 `_generate_content_lines` 中输出 `data-<字段>` 属性，在 `_prepare_data` 中生成字段按钮，然后修改 `report.js` 中的 `evalExpr`。
- **加一个时间轴叠加层**：在 `timeline.py` 中计算数据并加入 `to_data()`，在 `model.js` 中解码，在 `render.js` 的 `drawTrack`/`drawSlice` 中绘制，在 `model.js` 的工具栏加开关，在 `panels.js` 的 init 中处理"数据不存在"的情况。
- **改了 flags 的位**：同时修改 `timeline.py` 的 `F_*` 常量和 `model.js` 的 `FL`，并更新第 6.7 节。
- **改了 HTML 输出**：先运行 `python3 tests/golden.py --update` 记录基线，改完后确认只有预期的差异。
- **新的 ftrace 格式**：把一段真实输出加入 `tests/data/`，并在 `test_timeline.py` 的 `FixtureTest.EXPECT` 中写明期望的模式和能力。

## 11. 已知限制

- 时间轴依赖文本格式。读取 `trace_pipe_raw`（二进制）可以绕过 6.18 格式化参数很慢、以及 args 和 retaddr 不能同时输出的问题，但还没有实现。
- 没有 `latency-format` 时，上下文是根据函数名推断的，覆盖了常见的中断入口函数，但不完整。
- `funcgraph-abstime` 只精确到 us。时间戳和 duration 不一致的部分用限制在父切片内的方式修正，修正次数记录在 `stats.clamped` 中。
- 文本视图仍然为每一行生成 DOM，几十万行的 trace 打开会比较慢。

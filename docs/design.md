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
  output.py               ReportSpool：UTF-8 分段写出、精确字节计数、原子发布
  assets.py               读取 assets/ 下的前端文件
  timeline.py             TimelineBuilder：按任务重建调用切片、错误链、唤醒、开销修正
  btf.py                  BTF 解析：函数返回类型分类
  prefilter.py            转换前预过滤（pid/comm/cpu/时间窗口/调用树）
  compare.py              两份 trace 按函数比较
  cli.py                  参数解析、参数检查、工具链查找、处理流程
fgcapture.sh              目标机采集脚本（场景预设、开销标定、丢事件检查、恢复 tracefs）
assets/
  report/report.{js,css}  文本视图前端
  views/                  head 预启动（early.js）、附加视图注册/切换（boot.js）+ 共用样式
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
          ← output ← report
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
write_html() → HtmlGenerator.iter_sections()
  ├─ _prepare_data()                   函数名链接、过滤器字段、模块 URL
  ├─ _iter_content_lines()             每行 yield 一个 div；源码块去重放进池中
  ├─ CSS / JS（assets）                文本视图；有 tlData 时追加时间轴
  ├─ ReportSpool                       编码写入同目录临时文件，记录区段字节范围
  └─ publish()                         完成后 flush/fsync/os.replace
  ▼
output.html
```

`cli.main()` 分为几个阶段：`build_parser()` → `validate_args()` → `collect_module_dirs()` → `find_faddr2line()` → 解析 → （时间轴）→ 生成 → 写文件。

CLI 不再拼装完整 HTML 字符串；`generate_html()` / `HtmlGenerator.generate()` 仍返回原来的 `(html_str, vmlinux_time, module_time)`，供旧调用者使用。命名区段区分文本、源码池、Timeline/Compare 数据和界面脚本；相邻同名区段合并，索引不会为每行保存一个对象。`ReportSpool.copy_sections()` 按原顺序提取区段，每次最多读取 1 MiB，并恢复文件位置，为后续 sidecar 输出提供边界。

只有完整渲染后才能比较大小：`byte_count` 是最终 HTML 的未压缩 UTF-8 字节数，`exceeds_threshold()` 使用严格 `>`（等于阈值不触发）；不是 trace、JSON 或 DOM 大小。当前只实现计数/提取基础，**尚未接入自动 sidecar 或新的输出模式参数**。源码池和各视图 JSON 仍各自完整序列化，解析结果和模型仍常驻内存，不是全流水线常量内存。

临时文件与目标位于同一目录，仅在完整写入且 flush/fsync 成功后原子替换；生成、写入或替换失败会清理临时文件并保留原报告。新文件遵循 umask，已有普通文件保留权限，输出符号链接保留且更新其实际目标。输出目标必须是普通文件（或尚不存在的路径）；目录、设备和 FIFO 会报错，不能再用 `--output /dev/null`。原子替换需要目标目录可写；这不承诺断电后的目录项持久性。

faddr2line 的查找顺序：`<kernel-src>/scripts/faddr2line`，然后 PATH，最后是自带的 `fastfaddr2line.py`。只要指定了 `--fast`，即使找到了原生工具，vmlinux 的解析也改用 `fastfaddr2line.py`。`CROSS_COMPILE` 和 `LLVM` 环境变量决定使用哪个 `addr2line`。

## 4. 文本视图

### 4.1 行结构

trace 的每一行（包括注释行）对应一个 `div.line-container#L<行号>`。行上的 `data-*` 属性用来过滤：`cpu`、`pid`、`comm`、`func`、`params`、`duration`、`heat`、`irqs`、`resched`、`hirq`、`preempt`、`fold-id`、`fold-type`、`fold-ancestors`、`call-depth`。

输出按 `TEXT_CHUNK_ROWS = 512` 条完整片段流式增加 `div.text-chunk[data-chunk-size]` 包装；片段中的原始行与相邻源码占位不拆开，不缓冲整块或整份 Text。所有行和源码仍保留，块不是请求或模型边界。支持 `overflow-clip-margin:6px` 才给块开启 `contain:paint` 和 `will-change:transform` 合成提示；后者不施加实际 transform，也不隔离每一行。浏览器自行决定哪些块实际合成，不承诺固定层数或内存上限。不启用 `content-visibility` 跳过块，以免改变选区、滚动和源码展开。`#content` 仍保留 fit-content 横向尺寸。

### 4.2 源码块懒加载

可展开的行（带返回地址的行）后面跟一个空的 `div.expanded-content`，带 `data-src="N"` 属性。源码块的 HTML 去重后放在 `<script type="application/json" id="fgSrc">` 中，是一个 JSON 数组。`fgFill(el)` 在第一次显示时把内容填进去。任何要显示展开块的代码，都必须先调用 `fgFill`。

`fgFill` 用弱引用登记已填充的面板，Timeline 源码模式按钮也登记到同一集合；`WeakSet` 去重，`FinalizationRegistry` 清理已回收目标的登记项。模式切换只遍历这些目标，不使用整页 CSS/class 选择器，也不保存被替换详情节点的强引用；临时脱离文档的目标跳过但不注销，因此重排/重新挂载后仍可同步。没有弱引用能力时保留扫描兼容路径。未展开块不预填充，源码池读取时机不变。

效果：traceB_small（1.7 万行）中 13024 个展开块去重后只剩几百种，HTML 从 100.7MB 降到 33.4MB，浏览器加载从 6.0s 降到 2.6s。

### 4.3 折叠

- fold id 是 `f` 加入口行号的 36 进制，例如 `fk3`。原来是 `cpu_pid_func_line_depth_indent` 这样的长字符串，光 `data-fold-ancestors` 就占了 116MB。
- `data-fold-ancestors` 是空格分隔的祖先 id 列表，JS 用 `[data-fold-ancestors~=id]` 选择器找到整个子树。
- `_setFolded()` 是唯一修改折叠状态的函数，其他折叠操作都调用它。折叠状态按文件存入 localStorage，键名前缀是文档标题的哈希。
- 更新可见性时只查询当前调用子树，在一次遍历中根据所有已折叠祖先决定每行及其源码块是否显示；不再先展开全部后递归重放子折叠。源码展开偏好与可见性分离：父调用折叠期间保留偏好，重新展开时只填充可见行的源码。恢复 localStorage 时强制应用已有折叠状态，再按相邻行恢复可见源码，隐藏行不提前填充；延迟的源码单击回调不能重新打开已隐藏行。
- `fgBlocks` 同时读取旧平铺结构和新绘制块；任务/时间重排及旧耗时排序消费路径按完整行/源码对重建最多 512 行的块，不留下空旧块。任务标题和切换间隙是 `#content` 的直接孩子，不放在 paint containing block 内；排序后再重建键盘行缓存。折叠的 `fg-guides-change` 直接发给 `#content`，不能再假定行的父元素就是根。悬停绘制缓存跨块查询实际行，再按原折叠成员关系画可见列，不把块边界误当调用边界。
- Text 折叠箭头移到函数名前、跟随原始缩进；原生空按钮的伪元素用 5px 方形的两条 1px 边绘制灰色空心折线，折叠/展开时只旋转伪元素，不依赖字体中的实心三角字形，不改原始 trace 字符。零宽标识盒采用一个行高、顶部对齐，箭头垂直居中；祖先竖线高度采用一个行高加上下合计 6px 的行/内容 padding，因此跨行接满，不再用字形盒加固定 8px 短线。缩进 span 必须保留 inline，让制表符继续相对原文列定位；改成 inline-block 会重置 tab stop，导致函数名水平偏移。出口拐角与箭头/祖先线共轴，留出与 `}` 的空隙并用 3px 小圆角收尾；开关层级线不改变盒模型。入口 `{` 是独立辅助折叠按钮，跳过源码单击/双击和全局 Enter/Space 处理，数字深度快捷键仍可用。摘要放在 `{ … }` 后、调用点尾注前，计入全部已记录后代 entry/leaf，排除 exit 和自身；独立的 data-fold-ret 保存出口原文中的返回值并在插入摘要时转义，包含普通数值、指针和布尔值，不改过滤器 data-ret 的已知 errno 口径。普通返回值沿用灰色，错误状态沿用已有 errno 属性。
- 祖先线、入口连接线、3px 圆角出口和当前列强调统一由一个无交互视口 canvas 绘制，不再叠加渐变与 CSS 边框。重复渐变的分数 `ch` 瓦片会经栅格插值扩散为 2–3 像素；仅加 `image-rendering:pixelated` 或 SVG crispEdges 仍不能保证各列一致。现在保留原始缩进间距，仅将各轴的笔画原点及宽度对齐设备网格；宽度为 `max(1,round(DPR))` 个设备像素，约 1 CSS px。悬停只换色、不叠画或加粗。`tree.css` 只提供不改变布局的 2ch 测量伪元素和透明的连接线/拐角命中区，不再绘线。浅色/深色 `--tree-guide` 仍为 `#b2bcc6` / `#687582`，与源码链边线共用中性灰；`--tree-guide-active` 仍为 `#7a8998` / `#9aaaba`。Tree guides 按钮仍通过 stylesheet.disabled 开关并按报告保存，early.js 在 trace DOM 之前恢复；旧 HTML 需重新生成。开关不改行几何，但样式切换仍可能使浏览器重算全报告样式，不是 O(1) 渲染或虚拟化。
- `#content` 委托折叠点击和 pointer/focus 事件；`data-fold-end/start` 配对入口/出口，悬停经 getElementById 最多更新两行。可见性版本变化后只重建直接绘制块引用，再按实际块矩形二分定位视口；只查询相交块（每块最多 512 行），过滤隐藏行后二分定位可见行。绘线不再全量查询或缓存百万行引用；没有逐行 elementFromPoint、后代 class 批量修改或每深度节点。原始 fold-id/ancestors 限定强调归属，源码面板空间和无关任务不被强调。所有线段按可见行边界接满，视口与工具栏裁切；滚动、缩放、主题、内容尺寸和可见性变化经 rAF 合并重绘。关线/隐藏 Text 时清空画布，离开标识只恢复普通线色，焦点仍可强调当前列。18 层回归在 DPR 1/1.25/1.5/2 和明暗主题中对开关/悬停实际截图逐列验证物理像素宽度；旧渐变在全部八组条件中失败，新实现全部通过。

2026-11-10 的本机大报告回归：72,176 行、约 127.7 MiB、1,051,588 个原有元素、141 个 paint chunk；新实现只额外保留一个视口 canvas。`tests/benchmark_text.py` 用全新 Chromium 上下文、交错顺序各测三轮，每轮三次真实 pointer 和三次 focus；统计中位数，动作反馈使用两次 rAF 代理，不是物理呈现时间，RSS 是采样值而非峰值。

| 指标 | 原渐变 + 强调画布 | 统一像素画布 |
|---|---:|---:|
| 首次 pointer 进入 | 287.9 ms | 162.0 ms |
| 后续 pointer 进入 | 168.4 ms | 181.9 ms |
| focus 进入 | 88.6 ms | 88.4 ms |
| 页面 load + 两次 rAF | 12.985 s | 13.434 s |
| renderer 采样 RSS | 3180.8 MiB | 3142.6 MiB |

这次修复首先保证所有深度、缩放和悬停的笔画一致，并减少首次强调时的全量行查询；不能据此宣称总体加载变快。热 pointer 有 169.8–318.5 ms 的波动，首开中位数略增，完整 DOM 的样式、布局、绘制及内存成本仍然存在，后续应继续用独立冷启动样本定位。

同日继续定位时，CDP timeline/CPU 采样显示，真实进入/离开、四次滚轮动作的主要成本是浏览器原生 Paint/HitTest，而不是层级线 JS。单次诊断中，统一画布基线累计 HitTest 为 1182.8 ms、Paint 为 1366.1 ms，FunctionCall 为 9.5 ms；只提升画布层仍有 1141.1 ms HitTest，给现有绘制块提供 `will-change:transform` 后 HitTest 降至 12.3 ms，但 Paint 仍为 1435.8 ms。这些是有 profiler 开销、场景内求和的诊断量，不是逐帧预算或跨机器保证；不能把优化解释为减少 trace 解析、布局、绘制或内存。

随后用生成器当前完整 `report.css` 替换基线 HTML 中的对应样式，其余 trace/源码/数据不变。使用同一 72,176 行报告和全新 Chromium 145.0.7632.6，交错顺序各跑三轮无 profiler 的 `python3 tests/benchmark_text.py --rounds 3 --samples 3 --output RESULT.json BEFORE.html AFTER.html`，不以加载后注入样式代替首开测量：

| 指标中位数 | 统一画布 / 原 paint chunk | 增加块级合成提示 |
|---|---:|---:|
| 首次 pointer 进入 | 163.1 ms | 84.7 ms |
| 后续 pointer 进入 | 253.3 ms | 100.0 ms |
| pointer 离开 | 180.9 ms | 100.0 ms |
| focus 进入 | 92.4 ms | 90.8 ms |
| 页面 load + 两次 rAF | 13.322 s | 12.019 s |
| renderer 采样 RSS | 3152.5 MiB | 3124.6 MiB |

同样只将两次 rAF 当反馈代理、RSS 当采样而非峰值；focus 变化很小，首开差异不足以保证普遍加速。基线热 pointer 的 181.8–335.8 ms 波动仍需披露。独立 layer-tree 检查中，初始返回的层记录为 9/10、滚动后为 10/12，并非为 141 个块全部永久建层；GPU 进程滚动后采样 RSS 为 141.5/142.9 MiB，不是显存用量。这些层数/内存仅描述该浏览器当次决定。全部 72,176 行的矩形、内容宽高、滚动尺寸摘要一致，跨第 512 行的原生 Range 选区文本一致；浏览器回归另用真实拖选验证跨块选择，和禁用提示的参考逐字比较，并保留明暗主题、窄窗、焦点轮廓、源码、折叠、排序及层级线像素检查。没有新增 DOM、每行层、Worker、虚拟列表或 sidecar；大报告完整 DOM 的约 3 GiB renderer 成本仍在。

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
| 19 | 调用点与记录父切片不一致（尾调用、内联或未跟踪代码等，原因未确定） |
| 20 / 21 / 22 | 返回 NULL / 叶子 / 上下文是推断的 |

### 6.8 可选的尾跳转静态证据（`fglib/tailcalls.py`）

`--timeline --tail-calls --vmlinux` 在 `TimelineBuilder.to_data()` 之后附加版本为 1 的 `tail_calls`，不修改 `cols`、统计、合成帧或原始 trace。默认不执行这一步；ftrace 的 `caps.tail` / `funcgraph-tail` 只是出口行是否有函数名，不是尾调用能力。

- 从 `F_CALLER_MISMATCH` 去重 `(parent name, parent module, child name, child module)`；每对保存 `supported/unresolved`、原因与全部直接跳转点。`supported` 仅表示二进制静态证据，不是分支执行证明。`basis=supplied_binary`、`trace_build_id_verified=false`；保存二进制名称、架构和可用 build ID，但不声称它与 trace 已匹配。`code_sha256` 只覆盖被选中父函数的名称、链接地址、长度和代码，不是整个二进制或全部符号映射的哈希。
- 惰性导入已有 `[fast]/[all]` 的 pyelftools。只接受有 `.symtab` 的 ELF64 小端 `ET_EXEC/ET_DYN`、x86-64 / arm64，精确匹配唯一 `STT_FUNC` 与可执行 PROGBITS 内的有效大小。模块符号不能套用同名 vmlinux 结果；子函数只需地址，不复制其代码。
- 只读取候选父函数字节，以原始链接地址写入临时小 ELF，一次调用 GNU/LLVM objdump。每函数最多 256 KiB，总代码最多 4 MiB、4096 个函数，子进程超时 30 秒；输出写临时文件，完成后超过 64 MiB 就拒绝，不是实时磁盘上限。工具及 ELF 错误降级为明确的未知原因。对代码读取前后做文件身份、大小和 mtime 校验；不运行目标二进制。
- 仅接受外跳的无条件直接 `jmp/jmpq` 或 `b`，目标必须是子函数入口；排除普通调用、条件分支、寄存器间接跳转和函数内循环。只证明字节中有这样的静态指令，不证明其可达性或本次执行路径。无匹配仍为 unresolved，不是“无尾调用”。
- 前端仅在相邻记录深度且版本为 1 时应用证据，缺失深度仍保持未知。Markers 使用中性灰小箭头，调用栈追加低调标签；详情列出全部静态点。静态点源码与普通调用点共用 resolver batch，但用指令的**精确 offset**，不做返回地址减 1；最深有效内联位置单独附在 site 上，不替换原始栈位置。纯 `--no-text-view` 不增加源码解析，`--keep-source` 才保留。
- 文本与 raw 共用数据流程；raw 需要可解析 retaddr，不能从形状猜尾调用。JSON CLI 导出包含可读符号的独立证据，但早于 HTML 源码补充；上下文 CSV 追加证据口径，统计 CSV 不变。浏览器不重新反汇编，不把静态点变成带实测值的假帧。

2026-11-10 本机实测：模型已构建，单独计时 `tailcalls.analyze()`，同一进程顺序执行三次取中位数，不与测试/构建并发；包含 ELF 符号读取与 objdump，不含 DWARF/源码解析和 HTML 生成，不代表冷磁盘端到端耗时。

| 输入 | 记录调用 / 不同关系 | 有静态证据的关系 | 选中父函数 / 代码字节 | 分析中位数 | 紧凑证据 JSON |
|---|---:|---:|---:|---:|---:|
| x86-64 `samples/sample_io.txt`，695 行 | 442 / 52 | 33 | 44 / 19,321 B | 2.920 s | 12,164 B |
| arm64 `/tmp/pi4/traceB.txt`，72,176 行 | 55,186 / 485 | 1 | 167 / 127,996 B | 3.040 s | 70,825 B |

这些数量是不同符号关系，不是执行过尾调用的次数；arm64 的其余关系仍未确定，不据此评价尾调用发生率。示例 `__x64_sys_read+0x14/0x19` 的 `jmp ksys_read` 可映射到 `fs/read_write.c:725`，但仍不自动核验 trace/build ID。未启用选项的七份真实内核 golden 只有预期的前端资源变化；仅逐个换回原资源后，整份标准化 HTML（包含 Text、源码池、全部 JSON）逐字节一致，没有扩大 golden 的动态字段掩码。

## 7. 附加视图与时间轴前端

详情上下文列表采用三列共享 Grid；行与原生选中按钮通过 `subgrid` 复用耗时/类型、符号、源码列。`minmax(66px,max-content)` 按已显示内容统一容纳较长耗时或 `untraced`，避免独立 flex item 的自动最小宽度把某一行的符号推远。分页追加和 Overhead 刷新由布局自然重算，不增加 trace 扫描或标签测量缓存。1.3 倍行高兼顾双行长符号和下划线/下伸笔画。浏览器回归覆盖全部帧类型、11.5/14/18px、390/1500px、明暗主题；另将文本截图与取消裁切的参考逐字节比较，验证普通/加粗/斜体中的下划线与 descender。

顶栏帮助按当前视图路由：Text/Compare 共用有视图专属内容的 `keyboardHint`；它不再属于 `pageChrome.hide`，切换时主动关闭并取消原来的隐藏计时器，避免按钮把 `display:block` 写到仍带 `fg-off !important` 的不可见弹层。Timeline 经可等待的 `fgSwitchView()` API 打开现有导航/图例，不能用会同步排空准备迭代器的 `fgView()`；完成后核对当前视图，取消或离开时不弹出陈旧帮助。普通布局只显示页头帮助入口，`setMax()` 仅在隐藏页头时显示工具栏替代按钮，不添加祖先状态选择器。Timeline 帮助也区分函数名正则 Search 与共享条件 Filter。帮助按钮保留原生 Enter/Space，所有视图都有关闭按钮与 Escape；弹层在窄屏/低窗口中限制高度并可滚动。五项浏览器回归覆盖实际点击、明暗主题/390 和 1500 px、键盘、等待、取消、切换与最大化。

- `assets/views/early.js` 在 head 内、任何 trace 行之前运行：沿用原有标题哈希存储命名空间，预先恢复主题，按生成器声明的可用视图/默认视图读取初始标签页。只有恢复 Timeline/Compare（或 `--no-text-view` 默认 Timeline）时才临时添加局部 `#content { content-visibility:hidden }` 规则；普通文本首开、未知保存值和行号锚点不隐藏 Text。`boot.js` 在 DOMContentLoaded 发起切换，并在 finally 移除临时规则；`--no-text-view` 默认优先于保存状态和锚点，不再为识别它提前复制整块 Timeline JSON。文本键盘初始化仍准备索引/选中行，但仅在 Text 当前可见时调用 focus，避免强制渲染跳过的子树；主题不在报告尾部和 load 再次重设。文本、源码、原始 JSON 和 Timeline 分析语义不变，未实现文本虚拟化。
- `assets/views/boot.js` 在全局作用域执行。附加视图注册到 `window.fgViews[name] = {el, init}`，第一次切换到该视图时才初始化。Timeline 另注册 `steps`、`staged`；通过 `tlData` 单文本节点的 length 判断是否达到 1 MiB，避免只为判大小复制整块 JSON。小报告和缓存 API 保持同步；大报告的 `fgSwitchView('timeline')` 返回 readiness Promise，重复切换共享同一个 Promise，完整初始化/resize 后 resolve API，取消/失败 resolve null。`await` 同时兼容两条路径。保留同步 `__tlBoot()` 和 `fgView('timeline')`；后者遇到 pending 会取消计时器并同步耗尽同一个 iterator，不另起一份模型。测响应性时不能用它提前强制完成。
- 时间轴的 `model.js`、`render.js`、`interact.js`、`analysis.js`、`panels.js` 按顺序拼接到 `function* __tlSteps(){...}` 中，共享同一个作用域，返回 `{resize, revealLine, draw, probe}`；`__tlBoot()` 同步耗尽该生成器。纯模型准备阶段 yield 状态，内联循环每 8192 个真实调用也 yield；8ms 是协作预算，不截断原子阶段或 GC。首个工作块在两次 rAF 后的独立任务执行，让状态和返回按钮先获得绘制机会；后续块让出事件循环。最后一次 yield 在 DOM skeleton 之前，挂载 DOM、注册全局事件直到返回 API 之间不暂停。状态使用 role=status、aria-live、aria-busy，错误使用转义文本和 role=alert；按钮/布局均使用局部选择器，不新增祖先状态后代规则。
  - 切回 Text、Compare、返回按钮或 pending 时 Escape 会清定时器、关闭 iterator、移除 loading/busy，并丢弃半成品。返回按钮/Escape 恢复 Text 滚动并将焦点放回 Text tab；重新打开重新构建。共享过滤以完成时的最新状态应用，Alt+点击等待 readiness 且核对当前视图，取消不会异步夺回页面。文本全局快捷键在非 Text 视图直接返回，避免 Timeline 键盘监听尚未注册时吞掉原生 Enter/Space。
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

### 调用栈位置与顶部统计

`_attach_timeline_sources` 只复用已有 `func_name_results/func_locations_map`，不增加符号解析命令。`tlData.sources.functions` 按 `name字符串索引:module字符串索引` 去重保存 `[文件, 行, URL]`，`callers` 按调用点字符串索引保存完整位置链 `[[函数, 文件, 行, URL], ...]`（含非内联调用点）。位置遵循现有内核/模块 URL 和相对路径规则，保留入口与调用点语义；不增加每次调用一份源码 HTML。当前详情路径的真实父帧只在下一条记录调用的外层函数名匹配、且无缺失深度时使用该调用点，否则回退到已解析入口，未知则留空。内联/最内层已知未跟踪帧使用相应调用点，不获得伪造实测值。列表仍按 100 行分页，CSV 可导出完整来源与 `source_kind/source_url`；没有 Text DOM 时也不依赖隐藏行。路径链接与原生帧选择按钮是兄弟节点，避免嵌套交互元素；链接协议检查、HTML 转义、新标签隔离与低对比度省略展示分别处理安全和窄屏可用性。

页面顶部的 `recorded calls` 直接读取已有 `tlData.n`，Timeline 标签不再携带数字徽标；统计是报告内记录调用总次数、非唯一函数数或 trace 行数，不受显示过滤影响，不含 synthetic 帧。悬停解释口径；不新增 trace 扫描或动态计数。

CALL STACK 与其他详情上下文列表采用相同的无缩进网格行：左侧原生帧按钮内部固定耗时/类型列，函数名最多两行；右侧源码位置与首行基线对齐。位置链接与按钮仍是兄弟节点，长路径文件部分省略、`:行号` 独立且不收缩，完整路径和调用点/函数入口口径留在 title 与 CSV，不在各行重复显示 `call site` / `entry`。仅调整 DOM 展示和主题色，不改变模型、父链、分页、来源索引、导航历史或导出数据。

2026-11-10 验证：完整测试 352 项（3 项可选跳过）；wheel/sdist 均在隔离环境验证基础安装与 all+raw 安装，资源字节与工作树一致。七份真实内核 golden 因展示/新增索引有预期差异，不扩大 normalization；另逐字节核对完整 Text 区域及原有 fgSrc/fgSrcLines/tlData/cmpData（仅沿用 build_time 屏蔽），原有内容不变。72,176 行、55,186 调用的真实 trace 同样保真；新增去重索引为 1,505 个入口和 3,200 个调用点，共 370,848 字节（约 362 KiB），不是按每个调用复制源码。

2026-11-10 布局与层级线修订验证：完整测试 355 项（3 项可选跳过，其余通过，raw 依赖已启用）。新增亮/暗主题及 320/390/800/1500px 宽度下的调用栈实际几何对齐、长名称/长路径与行号保留检查；DPR 1/1.25/2 下对比层级线开关前后的真实截图，确认祖先线确实绘出，开关仍不改行几何。额外在真实样例复现旧 0.75px 渐变没有像素差、新 1px 有像素差，避免仅检查 CSS 颜色误判。七份真实内核 golden 的展示差异符合本次修改，未扩大 normalization；完整 Text markup 和全部 fgSrc/fgSrcLines/tlData/cmpData（含来源索引，仅屏蔽既有 build_time）保持一致。wheel/sdist 均通过基础安装和 all+raw 隔离安装检查、资源逐字节核对及 twine 检查。更新 sample.html、根示例截图和受影响的指南截图；未修改版本、创建标签或发布。

2026-11-10 截图反馈后的层级标识修订：空心折线取代实心三角，控件居中、祖先线按行高连续、出口圆角共轴，悬停线保持相同 CSS 宽度。完整测试 363 项（3 项可选跳过，raw 依赖启用），新增原文/行几何/原生选区与不同前缀下 tab stop 不变检查，以及亮暗主题 × DPR 1/1.25/2 的逐像素行边界连续性测试。真实样例另用旧固定高度作反例，三种 DPR 均复现缺失像素行，新样式没有断行；仍不承诺跨任务或源码面板接线。由于控件居中后原生默认焦点不再越过行顶，绘制隔离的负例改用 4px outline-offset 验证较宽焦点仍需裁剪余量，默认焦点保真检查继续保留。七份内核 golden 与样例的完整 HTML 只发生三份 CSS/JS 的精确替换及既有计时差异，未扩大 normalization；全部 Text/源码/模型数据原样保留。同步双语指南与样例截图，wheel/sdist 构建及 twine 检查通过，包内三份前端资源与工作树逐字节一致；不改版本或发布。

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

### 直接记录子调用探索

`analysis.js` 的 `childData/childOrder/showChildren` 与详情预览共享当前父调用的真实孩子集合、两种耗时口径的未过滤聚合及前八项排序结果，切换父调用即清除旧引用。统一过滤器只匹配孩子，按 `textHits/sDur` 另缓存一次聚合；分组和逐次模式各保留一个查询/排序/分页结果，Overhead 换口径保留已加载页数。表格只初始生成 100 行，追加不替换旧行；两个 CSV 不修改模式/分页，导出完整当前范围。每组维护实际最长切片，原先的“组总耗时但点击第一个调用”改为明确定位最长一次。深度缺口、合成帧及孙辈不混入直接孩子；按最近真实父节点限定关系，不伪称完整运行时被调清单，不将 inclusive 合计视为父时间分区。

### 记录树导航

详情导航仅保留 `Parent`、`First child`、`Previous`、`Next` 四个原生按钮。移除与父子导航体验重叠的 `Back`、`Dive deeper`，同时删除选择历史数组和分支目标缓存，不保留不可达状态。连续点击 `First child` / `Parent` 即可逐层下探 / 返回；第一个孩子是叶子时不会自动跳过，使用兄弟导航明确选择另一分支。Parent 始终表示最近的真实记录父调用，而不是历史返回；合成帧沿用 `First recorded call`，不补造层级。

`interact.js` 的 `callTarget/navigateCall` 将 ↑/↓/`[`/`]` 与详情中的原生按钮统一到记录关系，而不是 `rowsT` 绘图行。共享的惰性 `callTreeIndex` 在同一次逆序构建中增加 previous 链和各任务的根序列：跨缺失深度的真实兄弟按原切片索引排列，不跨父节点/任务，根调用不要求深度相同，零耗时/同起点不会被二分查找遗漏；端点返回无目标而非循环。共享 first/next 遍历仍限制在选中子树，不跟随根节点的外部兄弟。

previous 增加每真实切片 4 字节持久索引；任务根头部只在构建时临时分配。首次使用为 O(N)，准备后的父/子/前后同级目标查询为 O(1)，过滤、Overhead 和 Inline 不重建它。合成帧只提供真实记录父节点和成员集合的第一个真实调用，不声称恢复了合成帧之间的运行时关系。按钮名称/目标提示转义，使用原生禁用及键盘激活；新详情滚回顶部，有后续目标时恢复同方向控件焦点，端点回到画布，避免重复 Enter 反向跳转。控件是详情区直接子元素、局部横向 sticky，窄屏换行，不引入全局祖先状态选择器。

### 密集像素与合成帧的匹配真实性

原 `drawRow` 为跳过同像素细调用仅检查首成员，导致后续匹配被隐藏；合并相邻块时又对整块做 AND，使一个匹配点亮周围未匹配像素。现在按实际 `(start-view.s)*scale` 做 `lbPixel`，每像素 `denseSummary` 使用完整行匹配前缀查询数量与首匹配位置；相邻块只在明暗一致时合并。无过滤的绘制路径不分配摘要对象；带过滤的行缓存按 `searchHits/textHits` 身份失效，Overhead/主题/缩放不重建成员统计。每已访问行的前缀使用 4*(行长度+1) 字节，合成帧匹配使用每帧一字节；旧过滤缓存整体替换、行用 WeakMap，不保存各查询历史。

合成匹配对实际孩子执行搜索/统一过滤的交集，缓存任一孩子命中结果。`interact.js` 的 hitTest 与绘制共用像素边界和代表帧，鼠标实际落入宽切片时保留宽切片优先；多帧提示给出帧数/匹配数和代表身份，合成帧不冒充真实调用数。视野左边界零耗时用 start/end 两种下界的较小值起步，避免严格 end 二分跳过。原始 `syntheticGeometry` 只保留当前合成帧的范围、覆盖与间隙，重复 tooltip/详情复用；不复制孩子集合、不受 Overhead/过滤影响。匹配统计首次构建成本明确披露，不把更完整但更贵的渲染冒称全面加速。

## 8. 性能数据

| 场景 | 数据 |
|---|---|
| traceA（18.6 万行）构建时间轴 | 14.2 万个切片，约 1.5s |
| traceB 全量（7.2 万行），带时间轴 | 生成 20s（优化前 66s）；HTML 114MB（懒加载前 330MB），其中时间轴数据 3.1MB |
| 36 万行，`--timeline --no-text-view` | 生成 12.7s，HTML 15.7MB，浏览器约 1.1s 打开 |
| 时间轴初始化（1.3 万个切片） | 约 55ms |
| Tasks（合成 5 万身份、15 万真实调用；Chromium 同步 DOM handler，不含页面加载、后续绘制和下载） | 首开 54.3ms / 100 行；已准备排序 10.6–21.2ms；字面查询 9.6ms；追加至 200 行 2.3ms；重开 5 次中位数 24.3ms；完整 5 万行 CSV handler 86.0ms（另核对导出行数）。新功能容量验证，不是与旧版不同工作量的提速比较 |
| 子调用预览（合成 100001 个真实调用、5 万直接子调用、2.5 万分组，5 轮交替 Chromium 同步 handler/DOM，不含页面初始化及后续绘制） | 同父调用连续刷新 10 次，中位数 45.3 → 14.1ms；首次详情统计 12.3 → 15.9ms（增加完整分组/最长调用数据的前置成本，不宣称首次加速）。前后均显示同一批 8 个预览项，新版改用原生按钮 |
| 完整子调用探索（同一数据，预览已准备） | 打开 11.0ms / 100 分组；排序 12.8ms；字面查询 7.0ms；追加至 200 行 1.3ms；首次打开逐次模式 13.6ms / 100 行；完整调用 CSV handler 105.2ms（不含下载）。另核对全部 25000 分组 / 50000 调用，导出不扩张 DOM |
| 记录同级目标查询（合成 100001 个真实调用、10 万同起点零耗时兄弟，5 轮交替 Chromium；隔离查询，不含页面初始化、选择详情/DOM 或绘制） | 末端附近 5000 次相同目标查询中位数 443.9 → 约 0.4ms，核对全部目标的校验和；新惰性索引首次准备 1.5ms，对照旧版单次首次查询 0.8ms。增加 previous 每切片 4 字节；这验证时间戳相同情况下移除线性行扫描，不宣称整个交互帧或一般 trace 有相同比例提速 |
| 密集合成帧悬停/绘制（合成 100001 个真实调用、一个含 10 万孩子的未跟踪帧，5 轮交替 Chromium；仅同步 tooltip HTML/Canvas 调用，不含页面初始化、tooltip DOM 布局） | 同帧 200 次 tooltip HTML 中位数 82.3 → 约 0.1ms，首次 1.2 → 1.3ms；HTML 逐字相同，无过滤画布逐像素一致。20 次无过滤 render 为 1.5 → 1.8ms；过滤首次 0.3 → 2.4ms，准备后 20 次 1.7 → 2.7ms。过滤输出有意修正漏匹配/误点亮，工作量不再相同，不能据此宣称渲染提速 |

### 8.1 大报告冷启动（2026-11-10）

对照 `464bd91c3e84` 与本次预启动修改，使用真实 Raspberry Pi 4 trace、相同参数生成报告，Chromium 145.0.7632.6、1500×900、浅色主题，三轮交替顺序，每次独立浏览器 context，文件系统缓存不清空。不是之前的 warm handler 微基准，也不是物理树莓派上的浏览器测试。

| 场景 | 修改前 → 修改后，中位数 |
|---|---|
| traceB：72176 行，55186 个真实调用 / 84453 个含合成帧的切片，约 114.7 MB HTML、103.5 万 DOM 元素；保存 Timeline 后重新导航打开 | 导航至双 rAF 绘制机会代理：27913.3 → 1694.5ms（约减少 94%）；最长主线程长任务：914 → 264ms；观测窗口内每个长任务超过 50ms 的部分之和：21279 → 214ms |
| 同一 traceB，先加载 Text，再首次调用 `fgSwitchView('timeline')` | 切换开始至双 rAF：375.8 → 381.4ms；这条路径的同步预处理/布局仍在，本轮不宣称加速 |
| demo：371997 行、283143 个真实调用 / 283338 个含合成帧的切片，约 18.0 MB，`--timeline --no-text-view` 默认打开 | 导航至双 rAF：357.8 → 368.8ms；最长长任务均为 129ms。该路径原本没有巨大 Text DOM，不能套用恢复大文本报告的提速比例 |

双 rAF 是首次绘制机会的代理，不是实际合成器完成显示的时间，也不是整个报告的交互延迟；`blocked_ms` 是所述窗口内的自定义超过 50ms 部分之和，不是 Lighthouse TBT。这个预启动版本的大文本报告只增加一个 head script 元素；逐行 Text DOM、源码池、Timeline JSON（除生成耗时 `stats.build_time`）均完整核对不变，不通过删除数据或功能提速。首次没有保存状态的 Text 报告仍会渲染逐行 DOM；在该版本中 JSON 解析、typed arrays、inline/ghost 和绘图行预处理仍为同步，后续优化需要分别测量，文本虚拟列表尚未实现。

输入标识：traceB SHA-256 `5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62`；demo SHA-256 `b2375370c5bafba8de3bea2cf6bd8fff67e2435a71be2505a35cc9be72411ae7`。traceB 生成参数为 `--timeline --enable-fold --filter --func-links --fast --vmlinux /vol_1t/raspi/linux/vmlinux --kernel-src /vol_1t/raspi/linux`，demo 另加 `--no-text-view`，均设置 `CROSS_COMPILE=aarch64-linux-gnu-`。

复现（需要 Playwright + Chromium；只清理新建 context 的存储，不使用个人浏览器配置；JSON 记录报告 SHA-256、体积、模型/DOM 数量、导航与长任务）：

```bash
python3 tests/benchmark_startup.py /tmp/before.html /tmp/after.html \
    --mode restore --rounds 3 --output /tmp/startup-restore.json
python3 tests/benchmark_startup.py /tmp/before.html /tmp/after.html \
    --mode switch --rounds 3 --output /tmp/startup-switch.json
python3 tests/benchmark_startup.py /tmp/before-no-text.html /tmp/after-no-text.html \
    --mode auto --rounds 3 --output /tmp/startup-no-text.json
```

### 8.2 首次切换的可取消分阶段准备（2026-11-10）

对照 `9f2b6b4b425a` 与分阶段准备版本，同样使用上节两个真实输入及参数、Chromium 145.0.7632.6、1500×900、浅色主题，每项三轮交替独立 context，无其它浏览器/测试重负载。`benchmark_startup.py` 已改为等待 readiness 完整结束再取双 rAF，另外单独记录状态反馈机会，不把 loading 当成已经可用的时间轴。

| 场景 | 修改前 → 修改后，中位数 |
|---|---|
| traceB，加载 Text 后首次切换 Timeline | 同步调用返回 415.7 → 1.7ms；新版状态反馈双 rAF 105.2ms；**完整就绪双 rAF 433.4 → 596.8ms**；最长主线程长任务 416 → 141ms；窗口内各长任务超过 50ms 部分之和 366 → 211ms |
| 同一 traceB，恢复已保存 Timeline | 导航至完整就绪双 rAF 1673.2 → 1648.9ms；新版反馈机会 1617.3ms；最长长任务 254 → 191ms，超过 50ms 部分之和 204 → 141ms |
| demo，`--no-text-view` 默认 Timeline | 导航至完整就绪双 rAF 358.6 → 357.3ms；新版反馈机会 231.3ms；旧版最长长任务 127ms、超过 50ms 部分之和 77ms；新版窗口中未观测到 >=50ms 的 longtask 条目（记为 0，不代表没有主线程工作） |

结论是减少一次连续冻结并提供取消入口，**不是全面降低总就绪时间**：traceB 首次切换完整就绪反而增加约 163ms，恢复和 no-text 的总时间近似持平。对照过每个工作块都等待 rAF 的版本，其首次切换完整就绪中位数约 715ms；保留最初的绘制机会，后续只让出事件循环，减少多余等待，仍无法消除浏览器 GC 和样式重算。

另用 CDP Tracing/CPU profiler 诊断旧版首次切换：JSON.parse 约 4.5–5ms，UpdateLayoutTree 约 113–115ms，GC incremental embedder tracing 约 174ms；profile 本身有干扰，不能和上表无干扰计时混算。瓶颈不是单独的 JSON.parse，因此没有为此盲目增加 Worker 或只优化临时 Map。准备仍在主线程，8ms 预算不保证任何单阶段、原生 sort、样式/布局或 GC 都能在 8ms 内返回。

代码不改变原始模型算法，仅增加 yield 边界。七组真实内核 golden 按精确嵌入资源白名单比较；真实报告另外核对完整解码列、inline/ghost 成员/父节点、Text/Inline 行索引、off-CPU 的 inline 深度和调度几何，不以删除文本/源码/切片换取响应性。

后续结构性方向见 [大报告 sidecar 与按需加载方案](plans/2026-11-10-sidecar-lazy-loading.md)：**只有最终 HTML 的未压缩 UTF-8 字节数超过可配置阈值时才自动 sidecar，小报告保留单文件**。sidecar、文本按块/虚拟化和 Worker 尚未实现；仅把 JSON 外置不能消除百万级常驻文本 DOM。这与当前 1 MiB Timeline JSON 的分阶段启动阈值是两件独立的事。

生成耗时的优化：源码块按（位置文本，URL）缓存渲染结果（55183 次调用只有 3207 种）；`fastfaddr2line.py` 用 `struct` 直接读 `.symtab`（4s → 0.1s，与 pyelftools 结果逐项一致）；函数名解析与返回地址解析并行。剩余时间主要是 addr2line 本身。

文本视图的体积主要来自每行的 `data-*` 属性和高亮 span。超过约 15 万行时 CLI 会提示改用预过滤或 `--no-text-view`。文本视图的虚拟列表（按需生成 DOM）还没做，见第 11 节。

### 8.3 分段写出的生成阶段内存（2026-11-10）

对照 `19b144c53fcc` 与分段写出版本，使用同一真实 traceB（SHA-256 同 §8.1），独立 Python 进程，三轮交替次序，`/usr/bin/time` 记录 wall time 和峰值 RSS；不清文件缓存、无并行重负载。不是浏览器测量，也不是只计写文件函数的微基准。

| 配置 | wall time 中位数，修改前 → 后 | 峰值 RSS 中位数，修改前 → 后 |
|---|---|---|
| 带真实内核符号：`--timeline --enable-fold --filter --func-links --fast --vmlinux /vol_1t/raspi/linux/vmlinux --kernel-src /vol_1t/raspi/linux`，`CROSS_COMPILE=aarch64-linux-gnu-`，HTML 约 114.75 MB | 18.56 → 18.25s | 739256 → 739096 KiB，基本不变 |
| 无符号解析控制组：仅 `--timeline --enable-fold --filter`，不传内核或符号路径，HTML 约 94.68 MB | 6.17 → 6.08s | 564544 → 141624 KiB，约减少 75% |

两组均设置 `FG_CONFIG=/dev/null`。新旧完整 HTML 经现有 golden 规范化（仅耗时、生成时间及环境/输出元数据）逐字一致，包含全部 Text、源码池和模型。流式写出避免了完整报告字符串的重复拼接，但带符号流水线的整体峰值没有明显改善；不能把控制组比例推广到所有生成场景，也未据此证明具体哪个阶段主导内存。输出内容和浏览器加载路径不变，**不表示大型 Timeline 打开已因此加快，也不表示 sidecar/按需加载已经实现**。

### 8.4 无 Text DOM 时保留完整源码（2026-11-10）

Timeline 调用点不再通过 `L<n>` / `line_<n>_content` 查源码。`fgSrcLines` 保存原始 trace 行号到源码池 ID 的稀疏映射，`fgSourceForLine/fgSourceHtml` 通过 `fgData` 读取索引和共享 `fgSrc`，真实行不存在于 DOM 也能定位。源码渲染按完整 `func_info` 缓存，池内仍按 HTML 去重；正常报告的 Text 行、源码块、inline 和 Timeline 完整核对不变，仅增加索引和相应前端资源。

默认 `--no-text-view` 保持不解析地址的轻量行为。显式 `--timeline --no-text-view --keep-source` 才将完整 parsed trace 交给 resolver 和 inline 生成，只在输出时跳过非注释 Text 行；最多保留 200 行原始行号的头部/注释，不创建逐调用占位 DOM。源码块及 chain/flat 模式照常可用，面板内原生按钮支持鼠标/Enter/Space，不会误触 disclosure；无源码时仍只显示有效位置，不显示空代码面板。Text 跳转保持“该视图已省略”的提示，不指向不存在的行。

真实 traceB 保留 55,186 个调用、84,453 个含合成帧的模型条目、3,210 个去重源码块、1,594 个内联调用点和全部 55,186 条源索引；新旧源码池及完整 Timeline（除现有耗时/no_text 元数据）逐项相同。新完整报告约 114.65 MiB，保留源码的无 Text 报告约 14.60 MiB，仅 16 行注释；索引 JSON 为 644,590 字节。此选项显式省略 Text，并非完整 Text 的按需加载；不能据此宣称 sidecar 或默认报告已虚拟化。

### 8.5 大型 Text 的分块绘制隔离（2026-11-10）

百万元素报告的悬停瓶颈包含浏览器的 Paint/HitTest/PrePaint，不只是委托事件函数的执行时间。逐行 `contain:paint` 虽有改善，另一组独立实验的末端 renderer RSS 样本却增加数百 MiB，因此没有采用。最终在生成器中每 512 条记录增加一个静态绘制边界；行与源码面板成对，任务标题/间隙留在块外，6px clip margin 保护第一行按钮越过块顶部的部分及原生焦点轮廓。浏览器不支持该 margin 时不启用隔离。

对照 `63bdc0150d8a`，使用真实 traceB（输入 SHA-256 同 §8.1），开启 `--timeline --enable-fold --filter --func-links --highlight-code --fast`，使用 `/vol_1t/raspi/linux/vmlinux` 及其源码目录，设置 ARM64 工具链及 v6.18 Bootlin 源码 URL。报告约 127.7 MiB，72,176 行、55,186 个真实调用；DOM 从 1,051,447 增至 1,051,588，恰好增加 141 个包装元素，不删减 trace、源码、高亮或模型。Chromium 145.0.7632.6、1500×900、浅色主题，三轮交替顺序，每次独立浏览器和 context，清空该 context 的存储，不清文件系统缓存；测量时不并发测试/构建等重负载。

| 指标 | 修改前 → 分块，中位数 |
|---|---|
| 默认 Text：导航至 load 后双 rAF，三轮 | 37,636.2 → 12,934.4ms |
| 首次真实鼠标进入折叠按钮，三轮 | 1,058.5 → 338.6ms |
| warm 鼠标进入 / 离开，各十二个样本 | 760.2 → 174.1ms / 442.7 → 178.3ms |
| 原生 DOM focus / blur，各十五个样本 | 307.2 → 89.7ms / 298.7 → 87.2ms |
| 交互结束时 renderer RSS 样本之和，三轮 | 3,354.1 → 3,178.0 MiB，约减少 176 MiB；不是峰值 |
| 另测保存 Timeline 后冷恢复，三轮 | 完整就绪双 rAF 1,818.9 → 1,784.1ms；最长长任务 208 → 159ms。启动保持同量级，不据此宣称新的 Timeline 提速；该组三轮有 2,785.5ms 的旧版就绪样本，不能忽略变异 |

`tests/benchmark_text.py` 使用真实 `page.mouse.move` 进入/离开同一可见折叠按钮，记录动作至双 rAF 的代理延迟；每轮五次，第一次单列为冷交互，其余十二个样本汇总为 warm。另以原生 DOM `focus/blur` 测焦点反馈，不冒称真实 Tab 键输入。双 rAF 不是物理呈现完成时间，单机结果不保证其他设备的比例；仍保留完整 DOM，较大的交互及加载延迟并没有被消除。RSS 是交互结束时该浏览器全部 renderer 进程的 `/proc/<pid>/status` 样本之和，不是峰值、JS heap 或整个浏览器的内存，也不能称常量内存。

复现（JSON 保留报告字节数/SHA-256、浏览器版本、行/元素/块数、实际 containment、全部冷/warm 样本、renderer PID/RSS 和残留 scope 检查）：

```bash
python3 tests/benchmark_text.py /tmp/before.html /tmp/after.html \
    --rounds 3 --samples 5 --output /tmp/text-paint.json
python3 tests/benchmark_startup.py /tmp/before.html /tmp/after.html \
    --mode restore --rounds 3 --output /tmp/text-restore.json
```

保真检查只精确去掉新增的 `text-chunk` 开闭包装，然后比较**完整原始 Text HTML**；七组真实内核 golden、完整 traceB 和仓库样例的行/源码 markup 均相同，全部 JSON 载荷仅沿用已有 `stats.build_time` 屏蔽。没有扩大 golden normalization 来消除呈现差异。浏览器回归覆盖跨块父子折叠/悬停、390/1500px 与 light/dark、长行横向溢出、选区、源码相邻关系、反复任务/时间/排序消费重排后的行唯一性及块上限；针对块首行实际截图确认 `overflow-clip-margin:0` 会裁焦点，而生产 6px 保留轮廓。这里的静态绘制块与未来按最终 HTML 字节大小触发的 sidecar/按需加载没有关系，后者尚未实现。

### 8.6 源码布局切换不再扫描整页（2026-11-10）

Source chain/flat 切换原先先查询全页模式按钮，再枚举每个 `.expanded-content[data-src]` 占位块，即使只展开了三个源码面板也扫描百万元素。仅把选择器改为 `getElementsByClassName` 并没有实测收益，因此未采用；最终使用 §4.2 的已填充目标弱引用登记表。Text 排序/分组、已折叠但填充过的块、Timeline 详情替换及源码按钮同步仍保持原有行为，不给未使用块填充内容。

本机实测：同一个 traceB 报告，72,176 行、55,186 个源码占位块、约 127.7 MiB，展开三个源码面板后有 1,051,693 个元素。仅替换两份前端脚本构造前后对照，使用 Chromium 145.0.7632.6、各三个独立新浏览器、交错顺序；每轮七次程序触发原生 Source 按钮 click，首个切换单列，其余十八次汇总为 warm。两份报告的 Text/源码/Timeline 数据不变。

| 指标（中位数，ms） | 原扫描路径 | 弱引用登记 |
|---|---:|---:|
| 首次 click 处理函数 | 58.3 | 22.2 |
| warm click 处理函数 | 57.8 | 21.7 |
| 首次切换到两次 rAF | 155.8 | 119.8 |
| warm 切换到两次 rAF | 179.4 | 148.0 |

处理函数仍包含已打开面板的源码 DOM 重建；消除的是与未使用 trace 占位块数量相关的扫描，不是所有重绘成本。两次 rAF 只是反馈代理，不是物理呈现时间；程序触发 click 不包括真实指针输入/命中测试。没有宣称常量内存、首开变快、Text 虚拟化或 sidecar 已实现。缺少弱引用能力的浏览器仍走功能兼容路径，不适用这些性能结果。

可复测命令（需要 Playwright/Chromium；前后报告均保留完整 Text 和源码）：

```bash
python3 tests/benchmark_sources.py BEFORE.html AFTER.html \
    --opened 3 --rounds 3 --samples 7 --output /tmp/source-layout.json
```

回归测试禁止现代路径上的整页选择器与 class collection 查询，验证 1,200 个未填充块不被访问、初次使用前不解析源码池、断开/重挂载及重排仍同步、缺少弱引用时的兼容行为。另通过浏览器强制 GC 验证移除的源码面板可回收，防止登记表因保存节点强引用而造成详情页内存泄漏。

### 8.7 显式 Sidecar 的首次 Timeline（2026-11-10）

真实 `/tmp/pi4/traceB.txt`，72,176 行 / 55,186 个记录调用，使用 Pi 4 vmlinux/源码，`--timeline --enable-fold --filter --func-links --highlight-code --fast`，分别生成默认 single 与显式 sidecar。两者均通过实际 `fgview` HTTP/CSP；Chromium 145.0.7632.6，1500×900，有滚动条，每次全新浏览器进程/context，三轮交替顺序。单文件预存 Timeline 偏好，避免把先绘制整个 Text 的成本混入对比；sidecar 同样停在 Timeline。

| 指标（三轮中位数） | single | sidecar |
|---|---:|---:|
| 导航到完整 Timeline + 两次 rAF（ms） | 1873.5 | 340.1 |
| 首次 loading/canvas 的 rAF 反馈代理（ms） | 1767.0 | 173.9 |
| 启动观察到的最长主线程长任务（ms） | 186 | 0 |
| 长任务超过 50ms 的部分之和（ms） | 136 | 0 |
| 首屏响应体字节（不含 HTTP 头） | 133,883,020 | 16,321,652 |
| 初始 DOM 元素 | 1,051,768 | 440 |
| 初始 Text 行 | 72,176 | 0 |
| 就绪后采样的 renderer RSS 总和（MiB，非峰值） | 757.6 | 201.8 |

Sidecar 外壳 12,055,186 字节，首屏只请求入口、manifest 和完整 Timeline（4,266,049 字节），不请求 Text 或 Compare。两种产物的完整 Timeline（仅排除已有 `stats.build_time` 测量值）、3,210 条源码池及 55,186 项行号索引完全一致；真实/inline/调度/错误等数据未抽样或截断。源码仍内嵌，以上收益来自避免未访问 Text 的传输、解析和 DOM，并非把完整模型移出主线程。

代价必须单列：最后一轮首次 Text 请求 117,562,202 字节并完整挂载 72,176 行，至两次 rAF **8,462.3ms（单次观测）**；之后 Text DOM 常驻，回访没有第二次请求，HTML 字符串缓存已释放。统一过滤也会承担首次完整挂载成本；这不是虚拟列表、源码分块或有界内存。rAF 是反馈/就绪代理，不等于物理呈现；没有观察到 ≥50ms 长任务不代表所有机器/trace 都不会阻塞。浏览器错误和 CSP 违规均为零。

可复测（需 Playwright/Chromium，renderer RSS 采样使用 Linux `/proc`）：

```bash
python3 tests/benchmark_sidecar.py SAME_TRACE_SINGLE.html SAME_TRACE_SIDECAR_DIRECTORY \
    --rounds 3 --text --output /tmp/sidecar-startup.json
```

脚本使用相同 HTTP 查看器、安全策略和保存标签，记录产物 SHA-256、浏览器版本、完整就绪、rAF 反馈、长任务、请求/响应体大小、DOM 和采样 RSS；核对模型数量，拒绝 sidecar 首屏意外加载 Text/Compare，`--text` 检查完整行数、一次请求及回访复用。完整 payload 一致性另由生成/浏览器回归与本次产物比对验证，不将相同数量等同于相同数据。

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
| `test_output.py` | UTF-8 精确大小与严格阈值、单次增量迭代、区段合并/有界提取、绘制块 0/1/512/513/1025 片段边界及行/源码对保留、跨块旧字符串 API 字节一致、权限/符号链接/普通文件约束、生成及写入/发布失败不破坏旧报告 |
| `test_fastfaddr2line.py` | 符号匹配：精确长度、别名、编译器后缀、同名符号按长度区分、entry offset、最后一个符号；设置 `FG_VMLINUX` 时与内核的 `scripts/faddr2line` 对比结果 |
| `test_browser.py` | Playwright：展开源码块、折叠、错误导航和跳到文本、过滤联动、范围汇总、主题和配色切换，同时检查页面没有 JS 错误 |
| `test_prefilter.py` | 各过滤条件、保留完整调用树、时间窗口语义、过滤后的实测数据仍满足不变量 |
| `test_compare.py` | 按函数统计与差值、错误计数、修正行需要两份都有估计值 |
| `test_inline.py` | 内联链解析（有无 --list、多候选、无输出）与调用点映射 |
| `test_tailcalls.py` | x86/arm64 真实小 ELF + objdump、去重与原始数据不变、循环/间接/普通调用排除、工具/依赖/预算/符号歧义降级、raw retaddr 流程、精确指令源码位置与 no-text 源码策略；浏览器测试补充主题/窄屏、全部静态点、安全链接、CSV、未知版本与深度缺口 |
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

### Raw 容器输入（2026-11-10）

`inputs.read_text/read_lines` 在解压后检测 trace-cmd 魔数；普通文本不构建任何原生代码。raw 路径由 `fglib/raw.py` 在私有临时目录构建 `fglib/native/raw.c`，经 libtracecmd 处理容器/压缩/CPU 时序，经 libtraceevent 按元数据字段读取，避免手写 reader 或 ctypes ABI 假设。所有 native 输出只在退出成功后交给现有 parser；失败不发布部分 HTML。实例按 `--raw-instance` 显式选择，顶层为默认，不混合不同 buffer。

内部 reader 按调用者所需表示读取：`read_lines` 直接接收 raw decoder 的行列表，避免整串 join/split 的额外字符串与逐行副本；`read_text` 保留字符串 API，普通文本的换行/解码规则不变。压缩容器、stdin 和混合多输入共用此路径，多文件合并仍保留纳秒排序、同时间输入顺序和跟随注释。局部实测：100,000 页合成容器（409,604,096 字节，400,003 条归一化行）的加载阶段 Python 分配峰值由 90.45 MiB 降到 54.42 MiB（tracemalloc，约减少 40%）；gzip 为 90.47 → 54.45 MiB。未开启 tracemalloc 的三次独立进程测量中，raw 的中位进程 RSS 峰值为 108.26 → 72.56 MiB，文本对照为 108.88 → 108.83 MiB；所有完整行序列 SHA-256 相同。这些仅为输入加载阶段指标，不代表完整报告或浏览器内存，也不是常量内存 reader 或 sidecar。

桥接器禁用插件，保留符号、模块归属、原始参数/返回位模式及 retaddr，生成纳秒入口/出口行；调度注释及同时间 switch-in 行复用现有 off-CPU/唤醒分析。模块归属通过公共 `tep_print_funcs` 导出到子进程私有临时文件，建立仅模块符号的有序查找表，不访问 libtraceevent 私有结构体。时钟检查使用固定 libtracecmd 的导出 accessor，限制纳秒时钟；cycle/counter 时钟明确失败。丢事件数量未知时保留 null，不伪造数量。多输入排序和 Timeline 支持 6–9 位小数，预过滤时间范围用十进制定点换算，避免浮点丢失纳秒。

第三方源码固定为官方 submodule，普通 wheel/sdist 只分发桥接 C 源码；`third_party/build.sh` 为可选依赖构建入口。构建脚本需要较新已导出的 UAPI 头文件，检查后在用户 prefix 安装，禁用上游系统 ldconfig 修改。原生 CI 在 Ubuntu 24.04 构建全部固定库，再检查离线合成容器、安装态 wheel/sdist。支持范围和限制见 [中文](raw-input.cn.md) / [English](raw-input.md)。这不是 sidecar 或实时裸页 reader。

本地验收使用固定子模块构建的库：VM 两份 v7/zstd 容器各 24 条 graph 事件、Pi 4 aarch64 容器 162 条 graph 事件，与 `trace-cmd report -N -t -R` 核对时间戳、CPU/PID、函数/深度、calltime/rettime 差值及返回位模式全部一致；真实记录的十六进制返回约定经内核 print fmt 确认，不把 `-R` 当作通用数字协议。三份报告在 Chromium 中打开 Timeline 无脚本错误；Pi 4 的符号/源码模式解析 82 个条目。原始参数数组另由合成容器覆盖，实测文件未启用/未携带的参数不会伪造。

### 报告数据提供者（2026-11-10）

`assets/report/data.js` 提供 `fgData.read/load/ready/external/length/release`；Timeline/Compare 不再直接解析 DOM JSON。普通输出保持内嵌数据、离线打开；显式 `--timeline --report-mode sidecar` 由 `fglib/sidecar.py` 导出目录，通过 `fgview DIRECTORY` 打开。Text/源码按块传输、缓存淘汰和自动阈值仍未实现；静态 `text-chunk` 绘制包装不是外部数据块。

Sidecar 第一阶段使用 deferred-full Text：默认 Timeline 首屏只读取外壳/manifest/Timeline，不获取 Text/Compare，也不创建逐调用 Text DOM；源码池及索引仍内嵌。进入 Text、行号/锚点跳转或统一过滤时完整获取 `text.html` 并一次挂载，过滤仍处理完整 trace，且不切走 Timeline。挂载后释放 HTML 字符串缓存，保留完整 DOM，回访不重复初始化/请求；这不限制首次 Text 的成本或后续内存。Text 工具栏在挂载前 inert；保存的 Text 偏好/行号锚点优先。网络错误允许 Retry，Text 初始化失败则要求 Reload report，防止复用半初始化状态；取消后迟到数据不会挂载或激活视图。

产物为 `index.html`、隐藏归属标记、唯一 `data-<report-id>/`（manifest、Timeline、可选 Compare/Text），资源大小/SHA-256/UTF-8 和报告身份逐一校验。`ReportSpool` 的完整单文件计数保留；以不超过 1 MiB 的读取提取资源和改写外壳，不重新拼完整 HTML。新/空目录完整暂存后发布；更新先发布不可变资源、最后原子替换 index.html，失败保留旧入口，入口替换后即使中断也不能删除已提交资源。旧版本为已打开页面保留，增加磁盘占用；拒绝未托管非空目录、文件和输出符号链接。全 trace 解析/符号解析未减少，也不自动判断大小/行数阈值。使用/清理见双语查看器指南。

原型通过 `fgDataManifest` JSON 配置 `{version:1, report_id, manifest:{file,bytes,sha256,format:"json"}}`。ID 为 32 位小写十六进制；manifest 内容为 `{version:1,report_id,resources:{tlData:{file,bytes,sha256,format:"json"}}}`，也可包含 `cmpData` 或 `text` 格式资源。manifest 路径相对 HTML，资源路径相对 manifest 目录。只允许 HTTP(S) 下同目录内的相对路径；禁止跨域、目录逃逸、编码路径和重定向，不带凭据。响应按声明大小有界读取，逐一验证长度、SHA-256、严格 UTF-8、JSON、版本及报告 ID；Web Crypto 需要安全上下文（如 loopback HTTP）。

重复请求共享底层 fetch，但每个订阅者独立取消；最后一个订阅者退出才 abort。请求身份校验避免取消后的旧结果污染缓存。切标签/Back/Escape 取消加载和模型迭代；失败显示原生 Retry 按钮。Timeline 拷贝 strings 和每条 offcpu 行，防止半初始化或重试污染缓存。`fgView()` 在外部请求期间返回 readiness Promise；内嵌兼容同步 probe。15 项真实 loopback HTTP 浏览器测试覆盖共享取消、重入、重试、紧凑窗口、损坏响应和模型一致性。

### 只读回环查看器（2026-11-10）

`fgview` 的安装态入口是 `fglib.viewer:main`，源码目录可用 `./fgview` 或 `python3 -m fglib.viewer`。只绑定 `127.0.0.1`，默认端口 0，由操作系统分配；显式 `--port` 限制为 0–65535，没有全网绑定参数。先打印本次随机路径 URL，只有 `--open` 才调用浏览器，Ctrl+C 关闭监听及目录句柄。选单文件只提供该文件；选目录必须存在 index.html，提供其中的普通非隐藏文件，但不列目录。目录模式需要用户指定独立报告目录，不能当作只允许 manifest 资源的 allowlist。

根目录先解析用户显式选择的路径，再从文件系统根起逐级 no-follow 打开，并核对选中对象的 dev/inode，随后固定目录句柄。后续每级路径使用 `dir_fd + O_DIRECTORY + O_NOFOLLOW`，叶子使用 `O_NOFOLLOW + O_NONBLOCK` 后检查普通文件，防止符号链接置换及 FIFO 阻塞；平台缺少这些能力就明确拒绝，不降级成跟随链接。关闭与根句柄复制共用锁，单次请求持有独立目录/文件句柄，重命名目录或并发请求不共享文件偏移。HTTP GET 以不超过 1 MiB 的块发送，HEAD 不读取正文；启动不解析或提前读取 Timeline、Text、源码资源。路径校验在一次严格 UTF-8 URL 解码后进行，拒绝逃逸、空组件、反斜杠、NUL 与隐藏路径。显式选择的入口可以先解析符号链接，但解析后的路径和根下面的内部链接不能在打开时重新跟随。

请求要求正确回环 Host 和随机路径前缀，显式 Origin 必须同源，没有 CORS、目录列表或写接口。响应禁缓存、禁止 MIME 嗅探及跨源资源读取；CSP 允许报告现有内联脚本/样式和同源 fetch，但不开放 unsafe-eval、表单、base 或嵌入。随机 URL 只是防网页猜测入口，不是多用户认证；可信报告的 JS 仍在浏览器执行，不提供 HTML 安全沙箱。该查看器不改变生成输出、模型、阈值策略或 DOM 规模，也不产生 sidecar；后续仍需资源发布、Text 模型解耦/虚拟化及源码按需读取。命令、安全边界及排障见 [English](viewer.md) / [中文](viewer.cn.md)。

- 时间轴内部仍以归一化文本建模；已支持 trace-cmd 二进制容器。独立 `trace_pipe_raw` 裸页仍缺少解码所需元数据，不支持实时流；raw 参数目前是原始寄存器值，不是 BTF 格式化参数。
- 没有 `latency-format` 时，上下文是根据函数名推断的，覆盖了常见的中断入口函数，但不完整。
- `funcgraph-abstime` 只精确到 us。时间戳和 duration 不一致的部分用限制在父切片内的方式修正，修正次数记录在 `stats.clamped` 中。
- 文本视图仍然为每一行生成 DOM，十几万行以上的 trace 浏览器难以打开；目前的办法是预过滤或 `--no-text-view`。下一步是虚拟列表，需要重写 `report.js` 中直接操作 DOM 的过滤、折叠和键盘导航。
- 开销修正假设每个被跟踪调用的开销相同；在中断、迁移或缓存行为不同的路径上会有偏差。

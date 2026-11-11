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

`ReturnTypeLoader` 按报告惰性缓存 `(路径, 基础 BTF)` 的解析结果和失败结果，并为不同模块路径集合保存独立的 `ReturnTypeDB`。`ReturnTypeResolver` 复用本次命令的 `ModuleIndex`；Timeline 完成实际调用/孤立退出配对后，在错误分析之前才传入自身的 `s_module`。Compare 两侧因此分别取得类型表，既不会遗漏基准独有模块，也不会在两侧同名函数属于不同模块时混用类型。不重新解析原始 trace 或额外调用解析器来猜模块，已解析的内核 BTF 跨视图复用。无 BTF 或 `--no-btf` 时不扫描模块目录；新命令创建新缓存，替换二进制或先前缺失的 BTF 不会受进程级缓存污染。显式 `rettypes` 和惰性 resolver 不可同时传入；单次类型表内的同名符号仍沿用既有优先级，不宣称按模块消除所有符号歧义。

CLI 完成 Timeline、导出与 Compare 数据准备后释放类型 resolver，再进入 HTML/源码渲染，避免基础 BTF 的原始类型段和解析数组继续常驻到大报告生成阶段。此释放不影响供源码解析共享的模块路径索引，也不代表整个报告采用常量内存。

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

`ret/caller/args/module` 的缺失值为 `-1`；字符串表索引 `0` 是有效值。
`cpu` 同样以 `-1` 表示未知，CPU `0` 必须保留。只有 `eline` 的 `0` 表示未记录退出行。
序列化时仅在整列都是对应缺失值时省略该列，原生 ftrace 和 uftrace 使用同一规则。

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
  - **临界区与调度窗口分页**：`textHits` / `sDur` 身份控制统计缓存；临界区三类分别缓存当前排序和页码，调度窗口独立缓存。每页替换最多 200 / 300 行，重开和 Text 往返保留页码；过滤、开销口径或排序变化时重置。临界区尚未访问的 profile 仍按需计算，已计算结果复用；CSV 访问全部排序行，不推动页码。原始窗口位置、完整长度和窗口定位语义不受分页影响。翻页端点保留可用焦点，窄屏与极短面板释放纵向空间，横向停靠和 44px 触控目标保持一致；基准与限制见 `performance-window-paging.md`。
  - **错误与原子睡眠分页**：错误组 / 原子睡眠组分别缓存页码，每页替换最多 200 / 100 行；完整分组、排序和组内 occurrence 索引独立于 DOM。翻页、刷新、重开、Text 往返及 Overhead 保留位置，排序 / minor 只重置错误组页码，过滤身份变化重置两张表。CSV 始终遍历全部当前范围，不推进页码或 occurrence 游标。触控按钮、横向停靠及短面板表头回退与其他分析面板一致；实测和内存边界见 `performance-error-paging.md`。
  - **真实调用树与分解**：按 `0..N-1` 的真实父子关系惰性构建 `first/next` 索引（约 `8*N` 字节），只连接同任务、深度递增的边。`subtree` 以迭代前序遍历返回真实后代，不沿可视化行搜索，也没有 256 层截断；直接子调用同样跨过深度缺口。原始耗时分解最多缓存 256 个结果，不受共享过滤、Overhead 或 Inline 影响。缺少分类显示 unattributed，分类合计超出原始区间时提示不一致并改用分类合计作为百分比分母。
  - **详情上下文**：只缓存当前选择的完整祖先索引和合成帧原始跨度，结构不依赖过滤/Overhead。调用栈与错误路径使用迭代父链、访问集合和同任务/深度校验，不再以 128/64 帧上限代替完整性检查；真正的采集边界由最外层已记录祖先的深度确定。统一生成器把真实调用、调用点所在函数、内联、未跟踪和窗口外上下文区分为有类型的行，每页准备/追加 100 行，已显示行和加载焦点保留。CSV 从独立生成器导出全部行，不推动显示游标；仅真实调用具有切片/实测值，保留原始错误链元数据与路径状态。合成帧是否仍被过滤命中取决于其任一真实子调用，不能用 `textHits[syntheticIndex]` 判断。
  - **快慢对比**：缓存键为所选根样本数组和 `sDur` 身份，使用耗时降序/原始索引升序选取最快一半与最慢 `ceil(n/10)`，并列时也不重叠。每个根访问一次真实子树，聚合工作量与被选子树大小之和成正比；递归祖先会重复计入其后代，不宣称全 trace 单次线性聚合。排序/分页/折叠复用准备结果，每页追加 100 行，CSV 包含未加载行或确切分组成员。函数自身耗时随开销修正，原始调度上下文另记口径，二者不能相加；文本过滤只筛根，耗时柱子不筛比较样本。
  - **model**：解码列数据到 TypedArray，计算自身耗时，建立轨道和每个深度的行索引，构建 CPU 轨道数据，生成 DOM 骨架。
  - **合成切片**：下标 `>= N` 的切片不是 trace 里的调用，按顺序追加：内联帧（`buildInline`，`FL.INL`，只在 Inline 视图的 `rowsI` 里）和未跟踪帧（`buildGhosts`，`FL.GHOST`，`rowsT` 和 `rowsI` 里都有）。未跟踪帧填补父子之间缺的深度，名字取自里面那些调用的返回地址（它们的调用者），缺多层时外层名字为 `?`；范围是里面第一个到最后一个调用，相邻且同名的合并成一个。`SYN[i - N]` 保存它们的信息（`kids`、`p`，未跟踪帧另有 `ghost/level/gap/name`）。统计和导航用的 `for (i < N)` 循环只看真实切片。
  - **render**：Canvas 2D 绘制。宽度小于 1.5px 的切片合并成密集块，用二分查找跳过同一像素内的切片。叠加层包括错误、关中断斜纹、need-resched 角标和横条、off-CPU 段、preempt 深度条、各种标记。另有概览条和时间轴刻度。
  - **interact**：命中测试、缩放平移、拖选范围、键盘操作（在捕获阶段处理，并阻止事件传给文本视图）、错误导航、搜索。
  - **panels**：详情面板（错误链、调用栈、最重的子调用）、范围汇总表、跳到文本视图（先展开折叠的祖先）、接收文本过滤器的结果、能力提示、帮助。
    - 范围汇总按范围、轨道、`textHits` 和 `sDur` 身份缓存全量统计，排序仅在排序键变化时执行。每页替换最多 300 个函数行，普通刷新和重开同一范围保留页码；更换统计口径或排序从首页开始。原生前后按钮在端点把焦点交给仍可用的反向按钮；CSV 遍历完整的已排序统计，与当前页无关。底部分页条仅在足够高的面板停靠；窄屏标题和极短面板标题/表头恢复普通滚动，触控按钮至少 44px 高。实测与限制见 `performance-range-summary.md`。
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

`callers` 的空数组现在明确表示该调用点在本报告的既有解析结果中没有可用位置；缺少 key 仍表示旧格式/未覆盖的表示，不能当作已知不可用。后端仅在原位置结果为空时生成空数组；非空但无法解析的 provider 输出保持缺 key，允许原有 HTML 回退。完整索引发布后保持不可变，不追加解析请求。详情栈把空链当作无源码链，避免深度缺口访问 `chain[0]`，也不把函数入口位置伪装为调用行。

页面顶部的 `recorded calls` 直接读取已有 `tlData.n`，Timeline 标签不再携带数字徽标；统计是报告内记录调用总次数、非唯一函数数或 trace 行数，不受显示过滤影响，不含 synthetic 帧。悬停解释口径；不新增 trace 扫描或动态计数。

CALL STACK 与其他详情上下文列表采用相同的无缩进网格行：左侧原生帧按钮内部固定耗时/类型列，函数名最多两行；右侧源码位置与首行基线对齐。位置链接与按钮仍是兄弟节点，长路径文件部分省略、`:行号` 独立且不收缩，完整路径和调用点/函数入口口径留在 title 与 CSV，不在各行重复显示 `call site` / `entry`。仅调整 DOM 展示和主题色，不改变模型、父链、分页、来源索引、导航历史或导出数据。

uftrace 的原生导出为内核入口增加可选 `retaddr_symbol`：仅解析采集目录的 kallsyms，一次读取并排序，逐条二分查找 `PC - 1` 所属的代码符号，保留模块和下一不同地址边界。数据符号、零地址、无界末符号及相同地址却模块冲突的符号不派生调用点。原始 PC 和原始返回偏移不变，Python 校验地址算术、代码名、范围及来源后恢复 `函数+偏移/范围 [模块]`，接入原有批量 faddr2line，并只在源码定位时减一次 1；不会拿运行地址直接减 vmlinux 地址猜 KASLR。报告按池中 caller ID 去重保存符号元数据，按原始行号保存返回 PC；旧 JSONL 的裸地址仍不猜解。

提供匹配的 `vmlinux`、`--kernel-src` 和 `--func-links` 后，调用点及内联链优先展示，入口位置作为明确标注的回退。源码片段读取选定的源码提供器或限定根目录内的文件，Text 展开行与 Timeline 共用去重片段池。内联链的路径链接保留为原生锚点，不在 chain 转换时丢失。混合调用栈的内联/跳帧上下文继承所属入口的地址空间；分界线落在首个内核上下文行之前，不会跨过内核内联帧才显示分界，也不把上下文当作额外实测调用。用户态仍只使用记录中的位置和 `--user-src`，即使路径、行号或函数名相同也不混用片段缓存或内核链接。缺失、越界和符号链接逃逸的文件不嵌入；`--no-text-view --keep-source` 保留相同片段和原始行号，默认薄报告不新增源码读取。

发布的混合样例使用 QEMU Linux `7.4.0-rc2` 的匹配调试镜像与源码提交 `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`，保留 332 个内核函数入口位置，并为全部 1,067 次内核调用恢复符号偏移，解析出 500 个不同调用点和 305 条内联链，网页链接固定到 `v7.4-rc2`。采集目录重新导出的 79,194 条内核记录经独立页解码器逐字段核对；样例 2,186 条记录的所有既有字段均保持一致，只增加已验证的符号元数据。匹配 BTF 另确认非阻塞接收的 `-EAGAIN` 传播链；不改变采集的原始返回值、时序、跨态边界或上下文位。无镜像回归独立核对全部原始列、用户源码和重建稳定性，发布产物回归另要求调用点、内联链、入口位置、片段、版本外链与离线浏览操作有效。

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
- **唤醒箭头降密**（`render.js` 的 `drawWakeups`）：视野内至多 15 条时保留原有 12px 邻近判断；超过 15 条时，按目标绘制行、4 CSS px 的横向桶及方向合并标记。焦点箭头优先画完整，超出 400 条仍画标记。悬停的 off-CPU 对应箭头保留一个完整名额。右上角统计的是原始可见唤醒数，而非绘制的标记数。

### 函数耗时分布调用分页

`analysis.js` 的 `distView.page` 以每页 50 次调用替换耗时列表 tbody；翻页复用已按耗时/原始索引排序的函数或调用点总体，不重新聚合、排序或改变分位数/柱子口径。普通刷新与重开保留页码，换总体、柱子或 Overhead 时回到首页；翻页和无总体变化的刷新保留可用控件焦点。分页条限制在调用列表 section 内，后面的 Slow vs fast 与 By call site 不被覆盖；CSV 仍遍历完整当前调用范围。该限制不覆盖调用点表、对比表或全量模型，详见[耗时调用分页记录](performance-duration-calls.md)。

同一 Timeline 实例的 `distHistogramOpen` 仅保存直方图展示偏好，默认开启，不写 localStorage。标题的原生 disclosure 按钮只切换图表的 hidden 状态及六项统计的紧凑排版，不重建调用 tbody、重新聚合或改变页码/区间；后续过滤、Overhead、函数和调用点切换仍照常更新完整总体。收起时不安排图表绘制，展开后按当前尺寸、主题和区间重画。手机紧凑布局把重复区间标签与排序提示留在无障碍文本中，让选择器、导出和分页少换行；标题信息与动作分组，六项统计仍完整显示。极短横屏继续使用已有详情扩展，不暗改用户保存的分隔位置。

### 任务导航与完整时间范围

任务导航（`analysis.js` 的 `taskData/taskOrder/showTasks`）懒构建全体 `TRACKS` 的身份事实：真实切片、runs、offcpu、wakeups、resched、irqoff 与 sections 的计数/几何边界及 CPU 集合，错误计数含 minor 源头，不纳入合成帧。原始事实独立于过滤、Overhead 和 Inline；过滤匹配数按 `textHits` 身份另缓存，查询/排序只处理任务，不重扫调用。当前每页最多 100 行，前后翻页仅替换 tbody，普通刷新与重开保留页码，新条件重置首页；分页条在正常高度面板底部固定，极短面板的标题与分页条恢复普通滚动。分页与全量 CSV 共用同一排序结果，导出不受当前页限制；query 使用字面匹配，名称转义、CSV 复用公式防护。时间列区分实测、压缩和调用顺序口径，跨度含空隙、不是 CPU 用量；开放等待按 `OPEN_OFF` 保留未知恢复和下界身份。下表保留旧追加式 Tasks 的容量基准，当前分页的对照实测与边界见[任务分页性能记录](performance-tasks.md)。

`model.js` 在补开放等待端点前遍历所有调用/调度/窗口边界，确定报告右界，不再仅取调用和已完成等待。`interact.js` 按需将仅调度任务加入 `ORDER`，显示真实头部而非伪造切片；CPU 点击选择实际深度中的最内层真实调用，否则清除旧选择、保留时间视野并显示该任务的 Tasks 行。Tasks 的 Show 则缩放到任务完整记录跨度。Tasks 的标题/工具/页脚均为详情区直接子元素，横向 sticky，紧凑窗口标题纵向滚动，动态排序后重算表头偏移。

### 极短详情区的临时展开

`panels.js` 为详情头部统一插入一个展开控件，仅在面板不足 140px 或已经展开时显示。状态保留在本次 Timeline 实例，不写入 `detailFrac`；展开时仅在 `#timelineView` 上切换类，隐藏主画布与 resizer，让详情通过 flex 占用剩余空间。主画布的尺寸、时间范围和选中身份不因隐藏而重建；恢复时重新 resize。`.tl-stage` 使用 `display: contents`，让帮助浮层继续挂在同一 DOM 中显示，键盘焦点转向可见的展开控件而非隐藏画布。关闭面板清除展开状态；正常高度的面板不增加可见按钮。范围、查询、分页、CSV 和采集数据口径不变，验证与限制见[详情临时展开记录](detail-panel-expansion.md)。

### 直接记录子调用探索

`analysis.js` 的 `childData/childOrder/showChildren` 与详情预览共享当前父调用的真实孩子集合、两种耗时口径的未过滤聚合及前八项排序结果。统一过滤器只匹配孩子，按 `textHits/sDur` 另缓存一次聚合；分组和逐次模式各保留一个查询/排序结果。`childView.pages` 独立保存两模式页码，避免查看子调用详情时预览缓存的切换误清掉父调用浏览位置。每页替换最多 100 行，Previous / Next 可逆遍历全部结果，单页/空页隐藏分页控件。Overhead 重排保留页码；排序只复位当前模式，字面查询/函数范围/reset 复位两模式。共享过滤集合重建时只比较当前父调用的孩子身份序列，实际成员变化才复位；等价通知及 Text 往返不丢页码，不逐帧重复比较，也不以完整报告数组扫描替代局部比较。

分页状态不参与两个 CSV 的完整结果枚举，也不缩减真实孩子、分组和排序数组。聚合与导出仍随总孩子数增长，不能宣称总内存/首次准备有界。每组维护实际最长切片，Longest / Show 保持分析面板和选中身份，Details 明确进入调用详情；页返回时按当前选中身份重新标记。深度缺口、合成帧及孙辈不混入直接孩子；按最近真实父节点限定关系，不伪称完整运行时被调清单，不将 inclusive 合计视为父时间分区。翻页控件保留原生焦点，端点转向仍可用的相反方向；紧凑触屏的该面板交互控件至少 44px 高，Find 使用 16px。基准、完整性与回归记录见 [performance-child-paging.md](performance-child-paging.md)。

### 记录树导航

详情导航仅保留 `Parent`、`First child`、`Previous`、`Next` 四个原生按钮。移除与父子导航体验重叠的 `Back`、`Dive deeper`，同时删除选择历史数组和分支目标缓存，不保留不可达状态。连续点击 `First child` / `Parent` 即可逐层下探 / 返回；第一个孩子是叶子时不会自动跳过，使用兄弟导航明确选择另一分支。Parent 始终表示最近的真实记录父调用，而不是历史返回；合成帧沿用 `First recorded call`，不补造层级。

`interact.js` 的 `callTarget/navigateCall` 将 ↑/↓/`[`/`]` 与详情中的原生按钮统一到记录关系，而不是 `rowsT` 绘图行。共享的惰性 `callTreeIndex` 在同一次逆序构建中增加 previous 链和各任务的根序列：跨缺失深度的真实兄弟按原切片索引排列，不跨父节点/任务，根调用不要求深度相同，零耗时/同起点不会被二分查找遗漏；端点返回无目标而非循环。共享 first/next 遍历仍限制在选中子树，不跟随根节点的外部兄弟。

previous 增加每真实切片 4 字节持久索引；任务根头部只在构建时临时分配。首次使用为 O(N)，准备后的父/子/前后同级目标查询为 O(1)，过滤、Overhead 和 Inline 不重建它。合成帧只提供真实记录父节点和成员集合的第一个真实调用，不声称恢复了合成帧之间的运行时关系。按钮名称/目标提示转义，使用原生禁用及键盘激活；新详情滚回顶部，有后续目标时恢复同方向控件焦点，端点回到画布，避免重复 Enter 反向跳转。控件是详情区直接子元素、局部横向 sticky，窄屏换行，不引入全局祖先状态选择器。

### 密集像素与合成帧的匹配真实性

原 `drawRow` 为跳过同像素细调用仅检查首成员，导致后续匹配被隐藏；合并相邻块时又对整块做 AND，使一个匹配点亮周围未匹配像素。现在按实际 `(start-view.s)*scale` 做 `lbPixel`，每像素 `denseSummary` 使用完整行匹配前缀查询数量与首匹配位置；相邻块只在明暗一致时合并。无过滤的绘制路径不分配摘要对象；带过滤的行缓存按 `searchHits/textHits` 身份失效，Overhead/主题/缩放不重建成员统计。每已访问行的前缀使用 4*(行长度+1) 字节，合成帧匹配使用每帧一字节；旧过滤缓存整体替换、行用 WeakMap，不保存各查询历史。

合成匹配对实际孩子执行搜索/统一过滤的交集，缓存任一孩子命中结果。`interact.js` 的 hitTest 与绘制共用像素边界和代表帧，鼠标实际落入宽切片时保留宽切片优先；多帧提示给出帧数/匹配数和代表身份，合成帧不冒充真实调用数。视野左边界零耗时用 start/end 两种下界的较小值起步，避免严格 end 二分跳过。原始 `syntheticGeometry` 只保留当前合成帧的范围、覆盖与间隙，重复 tooltip/详情复用；不复制孩子集合、不受 Overhead/过滤影响。匹配统计首次构建成本明确披露，不把更完整但更贵的渲染冒称全面加速。

### 精确视窗定位

`Go to…` / `G` 通过原生 modal dialog 输入当前时间模式的坐标，只改变 `view.s/e`，不将附近函数自动选中，
也不重新定义过滤、轨道或分析面板范围。空结束值复用既有 `panBy` / `clampView` 保持缩放；显式区间要求
`end > start` 并直接设置精确边界，不套用滚轮缩放的最小跨度。弹框本身不扫描调用数组；正常 `draw()` 的成本仍保留，
不将此功能宣称为大型 trace 绘制性能优化。

十进制输入用 `BigInt` 解析单位与小数，拒绝非整数纳秒和超出报告范围/安全整数的相对坐标。
时间戳基准仅用于 `abs` 模式且共享时钟可信、原点为精确 `uftrace.origin_ns` 字符串或安全整数 `t0` 的报告；
先减精确原点再转 `Number`，避免绝对时间戳的低位提前丢失。`rel` / `compressed` 不伪称原始时间，
`count` 将一个绘图单位映射为 1,000 个内部坐标，不将其解释为调用序号。切换基准会事务式转换两字段；校验失败保留原输入和旧基准。

对话框阻止底层快捷键传播并约束 Tab 边界，支持 Enter / Escape、就地错误和关闭后的焦点恢复。
切离 Timeline 时主动关闭，不把焦点抢回隐藏视图。紧凑工具栏先收起 More 再打开对话框，矮窗口允许弹框内部滚动，
紧凑输入和按钮至少 44px 高。实现与可复现验证见 [timeline-positioning.md](timeline-positioning.md)。

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

结构性方向见 [大报告 sidecar 与按需加载方案](plans/2026-11-10-sidecar-lazy-loading.md)：显式 sidecar 和可选 `--report-mode auto --sidecar-threshold BYTES` 已实现，**只有最终 HTML 的未压缩 UTF-8 字节数严格超过显式阈值时才自动 sidecar，小报告在同一独立目录保留离线 index.html**。默认仍为 single，没有默认阈值。文本按块/虚拟化和 Worker 尚未实现；仅把 JSON 外置不能消除百万级常驻文本 DOM，当前 sidecar 首屏还会推迟完整 Text。这与当前 1 MiB Timeline JSON 的分阶段启动阈值是两件独立的事。

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

### 8.8 Timeline 悬停位置不再解析源码池（2026-11-11）

`tipSlice` 原先为了显示一行调用位置，通过 `fgSourceForLine` 读取完整行号映射，再由 `fgSourceHtml` 读取完整源码池，把目标源码块放进临时 DOM 后查找位置。新路径复用已有 `sources.callers` 的最内层位置，按不可变位置链缓存结果；明确空链直接返回无位置。缺 key、旧格式或不支持的非空表示仍走原 HTML 回退。只在已有解析结果为空时生成空链，不新增符号解析，也不把函数入口行号替代为调用行。

真实 `/tmp/pi4/traceB.txt`，72,176 行 / 55,186 个记录调用 / 84,453 帧 / 12 轨道，以同一 Pi 4 vmlinux/源码和 `--timeline --enable-fold --func-links --fast --filter` 生成改动前后报告，未启用代码高亮。保留完整 sidecar、6,526,932 字节的源码池（3,210 块）及 644,590 字节的源码行映射（55,186 项）。55,170 个调用有紧凑位置，2 个调用明确无位置，14 个调用保留旧回退；不能把后者当作已知不可用。

Chromium 145.0.7632.6，1500×900、有滚动条，实际 `fgview` HTTP/CSP，每次全新浏览器进程/context，三对交替顺序。报告入口仅在基准拦截中等量注入 `callSitePos` 计时/源码读取/临时元素计数及只读位置探针，不修改生产探针。初始 Timeline 完整就绪后，由真实鼠标移动到同一个宽切片 `do_el0_svc`（索引 1883，调用行 `arch/arm64/kernel/entry-common.c:749`），分别测首次和同位置缓存悬停；捕获阶段记录事件处理起点，双 rAF 仅作为反馈代理，不是输入设备延迟或物理呈现时间。

| 指标（三对中位数） | HTML 回退路径 | 紧凑位置路径 |
|---|---:|---:|
| 首次悬停的位置函数同步时间（ms） | 12.2 | 0.1 |
| 首次悬停到双 rAF（ms） | 21.0 | 16.7 |
| 同位置缓存悬停到双 rAF（ms） | 3.4 | 3.3 |
| 首次悬停源码池 / 行映射读取次数 | 1 / 1 | 0 / 0 |
| 首次悬停位置查询创建的临时元素 | 1 | 0 |
| 缓存悬停源码池 / 行映射读取次数 | 0 / 1 | 0 / 0 |

消除的是悬停对完整源码 JSON/DOM 的同步依赖，不是普遍的帧率提升：本次两个版本均没有观察到 ≥50ms 长任务，缓存悬停的新路径仍有一次 17.0ms 样本，帧相位和浏览器调度影响双 rAF。没有测量或宣称启动、峰值内存、完整 Text 挂载或源码详情的改进。

计时完成后在每次真实浏览器中遍历全部 55,186 个调用，比较实际位置函数返回的函数名、文件行号、URL、inline 标志及空位置；六次结果 SHA-256 均为 `f0bf22fb0f49d423101d6116405df6979a02317ce491d95ccdc718892564bf3b`，其中 16 个空位置，未抽样。计时悬停不请求 Text、Text 索引或 Compare，也不挂载 Text 行。完整资源字节数/哈希及报告身份检查通过；Text 与 Text 索引、完整源码池/行映射字节不变，Timeline 除已有 `build_time` 和两个经原源码块证实不可用的空链外完全一致。七份真实内核 golden 和 sample 只替换准确的 `panels.js` 后通过原有规范化比较；两张重新生成的 sample 截图字节不变。

`FG_RAW_REQUIRED=1 tests/run.sh` 共 546 项，543 通过、3 项可选跳过；新增回归覆盖 caller 索引 0、最内层 inline、模块/安全链接、缺失与不支持的解析结果、旧 HTML 缓存、明确空链和详情深度缺口，并通过实际文件/HTTP canvas 悬停拒绝源码池/行映射读取。源码详情仍会同步解析完整内嵌源码池；尚无源码分块、LRU、有界内存或 Text 虚拟列表。

### 8.9 Sidecar 源码按 ID 分块交付（2026-11-11）

后续源码交付将完整 `fgSrc` 从 sidecar 外壳移到不可变资源目录，每块最多 64 条源码记录；默认 single 和 auto 未超阈值的离线 HTML 保留原内嵌池。`HtmlGenerator.iter_source_chunks()` 保持原池次序和全部 HTML，`fgSourceChunks={version:1,count,chunk_size:64}` 是内嵌寻址目录，source ID 的 `floor(id / 64) * 64` 对应 manifest 的 `fgSrc<first>` / `source-<first>.json`。manifest 能力为 `sources=chunks-v1`；`fgSrcLines` 和过滤索引的行/source 身份不变。自动阈值仍计完整假想单文件的 UTF-8 字节，不能把移走源码后的外壳大小用于阈值判断。

`fgData.readSource/loadSource/sourceReady` 统一同步/异步来源，旧内嵌报告仍同步可读。块沿用字节数、SHA-256、严格 UTF-8/JSON 及 report ID 验证；源码目录检查非负安全整数和版本，块检查完整数组长度及每项字符串类型。无效内容移出缓存，允许重新请求。有效块只校验一次字符串形状，同块请求共享，取消一个消费者不会中止其他消费者；最后一个取消才 abort 底层下载。最初成功块常驻资源缓存；后续 §8.10 增加 LRU 及缓存总容量预算，仍不限制单次完整源码的解析/展示工作，64 是记录数而不是内存保证。

Text 展开与 Timeline 源码详情统一经过 `fgFill`：缓存/内嵌仍同步填充，未加载显示轻量 **Loading source…** / aria-busy，失败提供 **Retry**，不显示部分源码。完成后按当时的 chain/flat 模式填充并发送 `fg-source-ready`，Timeline 再安装模式按钮、补充位置和更新详情布局；没有源码行的详情仍仅留位置链接。关闭的详情面板不预读。收起源码、祖先折叠/过滤隐藏、替换/关闭详情、离开视图取消当前消费者；过期或已移除节点不填充。视图切换只记录未完成面板的恢复列表，返回时重启仍存在的面板，不扫描整页源码占位节点。紧凑悬停位置继续零正文请求；旧/未知位置只读取对应块并在静止鼠标下更新提示，离开悬停或视图取消，不复活旧提示。

真实 traceB：72,176 行、55,186 调用、3,210 条源码记录，51 个源码块合计 5,742,082 UTF-8 字节。外壳从 7,679,644 降至 1,159,791 字节；这是存储/传输边界变化，不能直接当作内存或就绪时间降幅。逐项拼回全部源码和原池一致，完整 Text/过滤索引逐字节相同，行/source 映射及 Timeline 全部字段相同（仅排除已有 `stats.build_time`）。全部 manifest 资源大小、哈希和 report ID 均核对。七组真实内核 single golden 仅精确替换六份预期前端资源后使用原掩码一致，说明存储变更没有改变默认 trace/源码/模型语义。

可重复测量入口：`python3 tests/benchmark_source_delivery.py BEFORE_REPORT AFTER_REPORT --rounds 3 --output measurements.json`。接受报告目录或普通单文件 HTML，使用实际 fgview、独立 Chromium 进程/上下文和交替顺序，分别测量完整 Timeline 就绪、首次调用点源码和缓存重开（包含 source ID 查找，均至两次 rAF），记录响应体 Content-Length、请求、DOM、renderer RSS 采样及同一源码 HTML 的 SHA-256；缓存重开产生额外请求或模型/源码不一致会失败。RSS 是阶段采样而非峰值，不能宣称普遍内存下降或源码展开免费。

在完整回归、生成和打包进程结束后，使用 Chromium 145.0.7632.6 对上述 traceB 做三对交替冷启动，阶段中位数如下。首屏均仅入口/manifest/Timeline 三个请求，没有 Text、过滤索引、Compare 或源码正文；首次展开 source ID 0 只增加 `source-0.json`（101,672 字节），缓存重开没有新请求。所有六次均为 55,186 调用 / 84,453 帧 / 12 轨道 / 0 Text 行，选中源码的 22 行代码 HTML 的 SHA-256 一致。

| 指标 | 先前 sidecar（内嵌源码） | 源码分块 sidecar |
|---|---:|---:|
| 完整 Timeline 就绪至两次 rAF | 399.1ms | 248.3ms |
| 首屏响应体字节总数 | 11,946,305 | 5,433,904 |
| 首屏 renderer RSS 采样 | 232.9 MiB | 184.6 MiB |
| 首次源码展开 handler（含 source ID 查找） | 37.4ms | 23.4ms |
| 首次源码展开至两次 rAF | 48.0ms | 71.0ms |
| 缓存源码重开至两次 rAF | 21.4ms | 31.4ms |

代价是首次按需展开多了下载/校验和异步调度，本次完整反馈比先前晚约 23ms，而不是“源码展开也更快”；缓存重开的小样本差异还受帧相位影响，不能承诺点击延迟或帧率改善。这里的首屏收益是同一大源码池报告的局部观察，不是普遍保证；RSS 为离散采样，旧版在后续阶段还因 GC 下降，不是峰值内存证据。完整 Text 的首次挂载/布局没有在本次性能对照中优化或重新宣称加速。

最终 `FG_RAW_REQUIRED=1 tests/run.sh`：568 项，565 通过、3 项可选跳过，另对加强后的两项悬停禁读断言和新增的迟到源码/替换详情测试做三项定向验证。覆盖跨块 ID、中文/超大单块、空池、无效目录/内容/校验、同块共享消费者、失败重试、Text 收起/祖先折叠/视图重入、关闭/替换详情、持久化闭合详情、未知旧位置的静止悬停和取消；raw 解析后的源码池与嵌入式输出逐项相同。wheel/sdist + Twine 通过，四个全新环境分别做基础/`[all]` 安装和安装态 raw/源码回归：基础各 9 通过、2 可选跳过，extras 各 11 全通过。固定 sample 只替换六份精确前端资源后符合原掩码，重新拍摄的两张 sample 图片字节相同，未改变外观。

上述初始交付不包含 Text 网络分块/窗口化、Worker、模型分轨道或源码缓存淘汰，也不解决先前完整 Text 聚焦触发的同步布局。后续源码缓存策略见 §8.10。默认阈值及行数触发仍未引入。完整 trace、raw 输入、单文件离线、版本固定 sample、wheel/sdist 继续回归；边界、取消及操作说明同步到中英文指南与查看器文档。

### 8.10 Sidecar 源码资源缓存预算（2026-11-11）

顺序访问多个源码块可复现原先 `fgData` 全部保留的问题：访问 12 块后，12 块仍全部 ready。新报告只对带 `fgSourceChunks` 目录的外部 `fgSrc<first>` 资源应用 LRU，最多 8 块且已校验 UTF-8 JSON 资源字节数之和不超过 16 MiB。按 manifest 的精确字节数计费，不以字符数或 DOM 数估计；实际 `read/load`（包括源码包装接口）命中才刷新顺序，`ready/sourceReady` 探测不刷新。普通数据、旧版完整源码池和单文件内嵌来源保留原行为。

新块校验完成后按需淘汰最久未读块；恰好等于预算可缓存。单块超过预算仍完整交付给所有等待者，但不进入资源缓存，也不逐出已有块。淘汰只去掉 provider 持有的载荷，不清空已展示源码 DOM；后续新消费者重新下载，并重新执行大小、哈希、UTF-8/JSON 及源码形状校验。显式 release 和无效内容清理同时归还块数与字节预算。失败/取消不发布缓存，也不刷新已有块的顺序；同块共享请求和独立取消规则不变，即使其他并发完成逐出该块，等待者仍接收本次完整结果。

这是资源缓存保留预算，**不是 JS heap、renderer RSS、并发下载或整份报告的内存上限**。网络/解码/解析的临时值、已填充源码 DOM、完整 Text 与 Timeline 模型仍可能占用更多内存；一条大源码不拆分。重新访问被淘汰或超大块会增加网络与校验开销，不能据此宣称首屏或每次展开更快。旧报告需重新生成；不改变 sidecar 触发规则、报告 schema、源码内容或单文件离线行为。

`tests/benchmark_source_cache.py` 可将真实单文件报告的完整源码池，经回环 HTTP 在独立浏览器页面中逐块重放，并比较指定版本的 provider。每块解码后重新序列化的 SHA-256 必须与原始资源逐项一致；结果保留报告和脚本摘要、Chromium 版本、全部源码数、缓存 ID/资源字节及返回首块后的请求数。它不是整份大报告的 DOM、启动或 heap 性能测量。

对本机留存的 128 MiB 报告源码池做前后重放：3,210 条源码、51 块，共 9,806,903 字节；这份历史 HTML 的源码 markup 不同于 §8.9 的交付基线，不能混用其传输大小。Chromium 145.0.7632.6 下，两侧全部源码哈希一致，顺序访问全部块后结果如下；缓存保留量在返回首块前统计。

| 指标 | 原常驻缓存 | 双预算 LRU |
|---|---:|---:|
| 保留块数 | 51 | 8 |
| 保留的已校验 JSON 资源字节 | 9,806,903 | 1,366,143 |
| 访问全部源码后再返回首块，该块累计请求数 | 1 | 2 |

可复现命令（`BIG_REPORT.html` 为保留完整 `fgSrc` 的报告）：

```bash
git show 88cfdfb17930:assets/report/data.js > /tmp/source-cache-before.js
python3 tests/benchmark_source_cache.py BIG_REPORT.html \
    --scripts /tmp/source-cache-before.js assets/report/data.js \
    --output /tmp/source-cache-replay.json
```

本轮新增 13 项真实浏览器回归，覆盖块数、全部读/加载入口的 LRU、UTF-8 多字节计费、恰好/超过预算、大块交付、显式 release 归还字节/槽位、无效内容、淘汰后重新校验及重试、并发消费者、取消和已显示 DOM 保留；另外确保源码形状在缓存发布前校验，损坏块不逐出有效缓存，直接资源请求也不能绕过目录对齐和安全整数检查。旧内嵌/完整外部池及普通数据缓存不变。冻结暂存快照的 33 项 provider 浏览器测试全通过；完整 required raw/uftrace/sample-browser 测试 760 项，757 通过、3 项可选跳过。

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

使用指南的截图在 `docs/img/`，由 `python3 docs/img/make_shots.py [名字...]` 生成：输入是 `sample.html`（当前 7.4-rc2 的 `demo_io` 采集）、`tests/data/pi4_sched.txt`（调度事件）、`tests/data/pi4_nodur.txt`（深度跳级）和 `samples/sample_io_slow.txt`（7.2.0+ 历史对比采集，不是受控的跨版本性能基准）。`python3 samples/make_shots.py` 生成根目录总览和 Text 示例，保留 1841×951 的总览尺寸并选中 `process_backlog`。截图只缩放到实际系统调用范围，不删除启动时的调度记录。脚本通过 `probe()` 选中切片、缩放、打开面板。界面或示例变了就重新运行，和改动一起提交。

## 11. 已知限制

### Raw 容器输入（2026-11-10）

`inputs.read_text/read_lines` 在解压后检测 trace-cmd 魔数；普通文本不构建任何原生代码。raw 路径由 `fglib/raw.py` 在私有临时目录构建 `fglib/native/raw.c`，经 libtracecmd 处理容器/压缩/CPU 时序，经 libtraceevent 按元数据字段读取，避免手写 reader 或 ctypes ABI 假设。所有 native 输出只在退出成功后交给现有 parser；失败不发布部分 HTML。实例按 `--raw-instance` 显式选择，顶层为默认，不混合不同 buffer。

内部 reader 按调用者所需表示读取：`read_lines` 直接接收 raw decoder 的行列表，避免整串 join/split 的额外字符串与逐行副本；`read_text` 保留字符串 API，普通文本的换行/解码规则不变。压缩容器、stdin 和混合多输入共用此路径，多文件合并仍保留纳秒排序、同时间输入顺序和跟随注释。局部实测：100,000 页合成容器（409,604,096 字节，400,003 条归一化行）的加载阶段 Python 分配峰值由 90.45 MiB 降到 54.42 MiB（tracemalloc，约减少 40%）；gzip 为 90.47 → 54.45 MiB。未开启 tracemalloc 的三次独立进程测量中，raw 的中位进程 RSS 峰值为 108.26 → 72.56 MiB，文本对照为 108.88 → 108.83 MiB；所有完整行序列 SHA-256 相同。这些仅为输入加载阶段指标，不代表完整报告或浏览器内存，也不是常量内存 reader 或 sidecar。

桥接器禁用插件，保留符号、模块归属、原始参数/返回位模式及 retaddr，生成纳秒入口/出口行；调度注释及同时间 switch-in 行复用现有 off-CPU/唤醒分析。模块归属通过公共 `tep_print_funcs` 导出到子进程私有临时文件，建立仅模块符号的有序查找表，不访问 libtraceevent 私有结构体。时钟检查使用固定 libtracecmd 的导出 accessor，限制纳秒时钟；cycle/counter 时钟明确失败。丢事件数量未知时保留 null，不伪造数量。多输入排序和 Timeline 支持 6–9 位小数，预过滤时间范围用十进制定点换算，避免浮点丢失纳秒。

第三方源码固定为官方 submodule，普通 wheel/sdist 只分发桥接 C 源码；`third_party/build.sh` 为可选依赖构建入口。构建脚本需要较新已导出的 UAPI 头文件，检查后在用户 prefix 安装，禁用上游系统 ldconfig 修改。原生 CI 在 Ubuntu 24.04 构建全部固定库，再检查离线合成容器、安装态 wheel/sdist。支持范围和限制见 [中文](raw-input.cn.md) / [English](raw-input.md)。这不是 sidecar 或实时裸页 reader。

本地验收使用固定子模块构建的库：VM 两份 v7/zstd 容器各 24 条 graph 事件、Pi 4 aarch64 容器 162 条 graph 事件，与 `trace-cmd report -N -t -R` 核对时间戳、CPU/PID、函数/深度、calltime/rettime 差值及返回位模式全部一致；真实记录的十六进制返回约定经内核 print fmt 确认，不把 `-R` 当作通用数字协议。三份报告在 Chromium 中打开 Timeline 无脚本错误；Pi 4 的符号/源码模式解析 82 个条目。原始参数数组另由合成容器覆盖，实测文件未启用/未携带的参数不会伪造。

### 报告数据提供者（2026-11-10）

`assets/report/data.js` 提供 `fgData.read/load/ready/external/length/release`；Timeline/Compare 不再直接解析 DOM JSON。普通输出保持内嵌数据、离线打开；显式 `--timeline --report-mode sidecar` 由 `fglib/sidecar.py` 导出目录，通过 `fgview DIRECTORY` 打开。可选 auto 按完整 HTML 字节数分流，见下文。Text/源码按块传输和缓存淘汰仍未实现；静态 `text-chunk` 绘制包装不是外部数据块。

Sidecar 使用 deferred-full Text：默认 Timeline 首屏只读取外壳/manifest/Timeline，不获取 Text/Compare/过滤索引，也不创建逐调用 Text DOM；源码池及索引仍内嵌。进入 Text 或行号/锚点跳转时完整获取 `text.html` 并一次挂载，重新应用当前过滤，释放索引和 HTML 字符串缓存，保留完整 DOM，回访不重复初始化/请求；这不限制首次 Text 的成本或后续内存。Text 工具栏在挂载前 inert；保存的 Text 偏好/行号锚点优先。网络错误允许 Retry，Text 初始化失败则要求 Reload report，防止复用半初始化状态；取消后迟到数据不会挂载或激活视图。

统一过滤改为按需读取完整 `text-index.json`（`fgTextIndex`，schema 1），包含原始行号、实际行文本及 14 个共享结构属性。`_text_attributes` 为 HTML 与索引提供同一属性值，HTML 边界统一转义；`TextIndexWriter` 逐行写 JSON 并累计 UTF-8 字节数/SHA-256，不在 Python 中保留整份索引。实际行文本由 HTMLParser 提取，保留 `/* <-caller */` 等不是 HTML 标签的内容，按浏览器规则规范 CR/NUL。索引不重复源码，`--no-text-view` 不导出索引。

浏览器复用原表达式/graph/notrace 求值逻辑，以轻量行适配器扫描所有记录，不创建逐行 DOM。schema 校验与过滤生成器按批次协作让出，目标 8ms；下载、JSON.parse、正则、图目标匹配和 GC 仍可超过预算，未迁移 Worker。进度回调只更新状态，不反复重建 Timeline 模型；Escape/Clear/更新查询可取消，代数校验防止迟到结果覆盖新状态。错误保留上次结果并提供原生 Retry，键盘焦点回到输入框；Text 挂载中会取消索引工作并按原生路径重算。没有索引标记的旧报告保持完整 Text 回退，默认单文件行为不变。`tests/benchmark_sidecar.py --filter QUERY` 核对与完整 Text 的全部命中行摘要，并分别记录耗时、请求、DOM 和采样 RSS；索引完整驻留至 Text 挂载，并非块缓存、虚拟列表或有界内存。

真实 Pi 4 `traceB`（72,176 行 / 55,186 调用）在一轮独立 Chromium 单文件/sidecar 对比中，查询 `duration:>10 AND NOT graph:finish_task_switch` 均命中 4,804 行，全部行号 SHA-256 一致。已挂载完整 DOM 的原生过滤为 283.2ms；首次含索引下载/解析/校验的 sidecar 过滤为 458.4ms，不能据此宣称单次过滤更快。关键区别是 sidecar 过滤后仍为 0 行 Text DOM，只请求一次 34,285,238 字节索引、不请求 `text.html`；过滤后采样 renderer RSS 为 240.4MiB，对照为 811.0MiB。该轮完整 Timeline 就绪为 352.4ms / 1802.8ms，是单次观察而非稳定中位数；双 rAF 只代表反馈代理，RSS 非峰值。首次完整 Text 的高成本并未被本次优化消除。

产物为 `index.html`、隐藏归属标记、唯一 `data-<report-id>/`（manifest、Timeline、可选 Compare/Text），资源大小/SHA-256/UTF-8 和报告身份逐一校验。`ReportSpool` 的完整单文件计数保留；以不超过 1 MiB 的读取提取资源和改写外壳，不重新拼完整 HTML。新/空目录完整暂存后发布；更新先发布不可变资源、最后原子替换 index.html，失败保留旧入口，入口替换后即使中断也不能删除已提交资源。旧版本为已打开页面保留，增加磁盘占用；拒绝未托管非空目录、文件和输出符号链接。全 trace 解析/符号解析未减少。使用/清理见双语查看器指南。

可选自动模式使用 `--timeline --report-mode auto --sidecar-threshold BYTES --output DIRECTORY`，默认 single 及字符串 API 不变，没有内置默认阈值。比较对象为本次假想完整 HTML 的精确未压缩 UTF-8 字节数，包含源码池/视图数据/脚本/元数据，只有严格 `>` 才 sidecar；不是外壳、压缩文件、trace 行数或 Timeline JSON 大小。阈值为非负整数，0 可显式强制 sidecar。单次渲染同步写出完整过滤索引；未超限时原子发布原样 `index.html`，丢弃暂存索引/资源目录，仅保留隐藏归属标记；不重复符号解析或拼接全量字符串。超限时复用已有外置流程。两种结果固定相同目录/入口，避免生成后改目标类型、遗留同名旧 HTML 或破坏已有文件；新旧模式互换时仍保留已发布资源和入口权限，故障不损坏旧入口。临时磁盘包含完整 HTML、索引及超限后的外置副本，生成成本仍随全量输入增长。

CLI 明确打印实际模式、完整字节数、阈值及入口，sidecar 时打印 shell 安全的查看命令。配置可保留 auto 和阈值，显式 single 仍可覆盖。`fgreport` 同步读取默认/preset/显式/透传模式，目录模式输出 `.report` 而非 `.html`、不再按 15 万行擅自省略 Text，`--open` 以前台只读 loopback 服务打开任一种结果，Ctrl+C 结束；single 原有行为保留。覆盖 UTF-8 多字节、阈值以下/相等/超一字节、一次渲染、两向更新/故障/权限/旧资源、配置覆盖、raw 模型一致性和真实浏览器离线/延迟路径。示例 64 MiB 不是默认值或未经测量的推荐界限；Text 虚拟化、源码分块/LRU 和 Worker 仍是后续工作。

#### Auto 分流验收（2026-11-10）

两份输入 SHA-256 与 §8.1 相同，显式阈值均为 67,108,864 字节。真实 Pi 4 traceB 使用 `--timeline --enable-fold --filter --func-links --fast`、原有 Pi 4 vmlinux/源码及 ARM64 工具链、v6.18 Elixir 链接；demo 保留 Timeline，未做源码解析。

| 输入/模式 | 完整 HTML UTF-8 字节 | 判断结果 |
|---|---:|---|
| traceB，72,176 行，保留完整 Text/源码 | 129,591,083 | sidecar，入口 7,669,488 字节 |
| demo，371,997 行，保留完整 Text | 478,970,419 | sidecar，完整索引仍为 371,997 行 |
| 同一 demo，显式 `--no-text-view` | 18,370,284 | single，只有 index.html 和隐藏归属标记 |

traceB 的完整 Timeline、去重源码池和导出 Text 与独立 single 逐项/逐字节一致（仅忽略已有 `stats.build_time`）；所有外置资源的字节数及 SHA-256 均核对。两份 demo 保留相同 283,143 个调用和完整模型，仅有意区别 `no_text`/构建耗时。重新生成的固定 7.4-rc2 sample 与原 sample 在既有 golden 掩码下相同，七组真实内核 golden 全部相同；无需替换用户截图。

浏览器复测命令：`python3 tests/benchmark_sidecar.py SINGLE.html AUTO_DIRECTORY --rounds 3 --text --filter 'duration:>10 AND NOT graph:finish_task_switch' --output result.json`。Chromium 145.0.7632.6、1500×900，HTTP fgview，三轮交替顺序，每轮独立浏览器/context 并预置 Timeline 偏好，无并行测试/基准。以下为中位数；ready/反馈均为双 rAF 等绘制机会的代理，不是物理呈现，RSS 为采样 renderer 总量而非峰值，传输为启动阶段响应 Content-Length 之和。

| 指标 | single | auto 选择的 sidecar |
|---|---:|---:|
| 完整 Timeline 就绪 | 1,751.1ms | 275.7ms |
| 首次状态反馈 | 1,649.2ms | 120.6ms |
| 启动传输字节 | 129,590,836 | 11,936,129 |
| 启动 DOM 元素 | 1,051,774 | 449 |
| 采样 renderer RSS | 749.1 MiB | 210.4 MiB |

三轮双方均保留 55,186 个真实调用、84,453 个含合成帧的切片、12 个任务；过滤均命中 4,804 行，全部行号 SHA-256 相同（`77070d8005bf1352b0fa19eeef9a4e7a050200ba8fd02e71038f6c02a49acedc`）。sidecar 只请求一次过滤索引，过滤后仍 0 行 Text DOM，无 Text 请求；过滤耗时中位数 460.7ms，慢于已挂载完整 Text 的 272.7ms，不能宣称过滤更快。最后一轮完整 Text 首次挂载 3,515.2ms，全部 72,176 行恢复，重入不再请求 Text，HTML 字符串缓存已释放；这仍不是 Text 虚拟化。

生成阶段另以独立 Python 进程观察一次：traceB single 为 20.09s / 739,572 KiB 峰值 RSS，auto 为 26.30s / 739,472 KiB。Auto 包含过滤索引和额外资源写出，不代表生成更快或峰值内存更低；完整 demo auto 为 62.32s / 643,412 KiB，no-text auto 为 12.10s / 550,164 KiB。不同输出模式的参数元数据会改变少量 HTML 字节，阈值比较始终使用本次完整渲染，而不是对照 single 的文件大小。这些是本机观察，不是默认阈值或跨机器性能保证。

回归记录：`FG_RAW_REQUIRED=1 tests/run.sh` 共 480 项，477 通过、3 跳过；wheel/sdist 分别在独立 venv 以 `python -I tests/smoke_installed.py --with-raw` 运行 11 项（各 9 通过、2 个 extras 测试跳过），覆盖两个已安装入口的 auto 双分支。真实 `fgreport --open` 在两种判断结果下均返回可访问的 loopback HTTP 入口，Ctrl+C 正常退出；启动浏览器命令使用 `/bin/true` 替身，不启动桌面浏览器。套件仍报告既有未关闭 socket 的 ResourceWarning，未在本次修改无关代码。

### 完整 Text 的协作准备（2026-11-10）

首次 Text 仍下载、校验完整 `fgText`，不新增网络分块。准备生成器识别 `_iter_content_chunks()` 生成的唯一完整块起始标记，按原有最多 512 条完整记录及相邻源码占位逐块解析，而非按任意 `</div>` 截断嵌套 HTML。trace 文本已转义，不能伪造这个起始标记；解析后还核对单个顶层 text-chunk。使用与最终容器相同文档的 detached div 解析，累计到 DocumentFragment，不将半成品行暴露给过滤、折叠或导航。

准备期间保留 `data-fg-text`，工具栏保持 inert；共享调度器以约 8ms 协作预算推进，块之间可返回 Timeline、切换标签或 Escape 取消。百分比按已处理的 HTML 字符位置计算，不是下载字节或 trace 行百分比，也不是每块耗时上限。取消调用生成器 return/finally 丢弃节点，保留已校验的 HTML 以供重入；未完成结果不能激活旧视图。解析阶段没有安装 Text 监听器，失败可以 Retry；最后初始化失败仍要求 Reload report。

生成器可返回 `{label, paint:true}`，调度器在继续前提供双 rAF 绘制机会。Text 在最终整块挂载前用它显示 **Finishing Text layout…**，避免进度一直停在 100% 而没有阶段说明。随后仍同步执行完整挂载、排序/折叠/源码状态恢复、当前过滤和键盘/锚点初始化；成功才释放 HTML/索引字符串缓存，完整 DOM 常驻。直接 `fgView()` probe 兼容同步排空；正常视图入口使用 readiness Promise。普通离线 single 路径不启用 deferred Text，不增加依赖或改变阈值策略。

这不是虚拟列表、按块下载、Worker 或有界内存。单个复杂块、GC、最终初始化及浏览器样式/布局仍可阻塞；保持完整语义与更频繁让出主线程，也可能增加总打开时间。后续仍需完整 Text 模型与渲染解耦，而不是据此宣称大 trace 的 Text 卡顿已解决。

### 唤醒箭头的视口裁剪

构建 Timeline 时，每 128 条唤醒记录保存最早的唤醒和最晚的恢复时刻。重绘先排除与当前窗口不相交的块，再逐条检查剩余记录。块内、块间均不重排；早于窗口唤醒但在窗口内或窗口后恢复的箭头保留，未关联 off-CPU 的唤醒仍按时间点匹配。等待链和导出不使用该索引裁剪。可见箭头超过 15 条时直接采用密集规则，不再排序；窄屏提示保留计数。

用同一份单文件报告模板分别生成旧版和新版（基线 `e99883cc5da8`），运行 `python3 tests/benchmark_wakeups.py BEFORE.html AFTER.html --output wakes.json`。Chromium 145.0.7632.6、本机 50 万条合成唤醒、每种模式每版本 60 帧：窄窗口重绘回调中位数 3.7 → 0.2 ms（p95 4.3 → 0.3 ms）；约 800 条可见的窗口 3.9 → 0.5 ms（p95 4.5 → 0.6 ms）。计时包含 Canvas 命令提交和标尺重绘，不测打开、布局或屏幕呈现。索引不减少 trace 保留内存；全范围或大量跨度穿过窗口时仍要检查并绘制可见记录。

全景降密以 `114b7b1d7c86` 为基线，在同一 Chromium 上用 `--overview --frames 10 --rounds 3` 测 50 万条合成唤醒，每版本每场景 30 帧。全部唤醒在视口内时，重绘回调中位数 331.7 → 12.65 ms（p95 349.5 → 13.3 ms）；窄窗口中位数 0.2 → 0.3 ms，约 800 条可见时 0.5 → 0.65 ms。全景仍逐条统计与视口相交的记录，省下的是重复的 Canvas 绘制命令，而不是丢弃事件。等待链、导出和稀疏箭头规则不变。

### 多任务轨道的可见范围

`layout()` 按现有排序建立未隐藏轨道的紧凑列表，同时保留各轨道完整的竖向位置和高度。绘制二分定位首个与视窗相交的轨道，越过视窗下沿后立即停止；悬停和点选沿用同一列表二分定位。轨道间隙仍不命中任务，起始边界属于下一条轨道。过滤、折叠、Inline、窗口尺寸变化及 CPU 导航动态揭示任务时，都通过布局更新列表；不改变任务排序、详情、完整分析或导出。顶部概览仍使用所有原始轨道，但每次构建只检查一次是否存在普通任务，不再对每条无 PID/CPU 轨道重复扫描。

`python3 tests/benchmark_scheduling.py BEFORE.html AFTER.html --tracks 20000 --output tracks.json` 可复现对比（基线 `69d29873705a`）。本机 Chromium 145.0.7632.6，滚至 2 万个合成任务的 90% 位置，每版本 60 个样本：命中函数中位数约 27 → 0.5 μs（每样本平均 200 次调用）；重绘回调中位数 0.4 → 0.3 ms。只测桌面 Chromium 命中函数与回调，不代表真机帧率或首开性能。回归另用属性读取计数确认悬停/重绘不遍历屏幕外任务，检查筛选恢复、折叠、轨道间隙、边界、调度专有任务的动态显示，以及无 PID 的 CPU 轨道概览构建和像素一致性。

手机折叠回归在切回桌面视窗后，使用同一次页面求值读取折叠按钮与函数名称的边界；不在多个自动化调用之间分别取坐标，避免滚动期间混用不同帧的几何。仍要求按钮宽度恢复、相邻边界差不超过 1px，并保留嵌套点按与父子折叠验证；单独重复十轮通过。

### 时间游标的画面复用

鼠标在同一调用、off-CPU 等待、CPU 执行区间或空白区域内移动时，不重新遍历和绘制调度叠加层。完整场景不含游标，分别保存在调用画布和顶部标尺的物理像素备份中；下一帧恢复备份，再绘制竖线和时间标签。悬停对象变化、滚动、缩放、筛选、布局、配色或叠加层设置变化仍调用完整重绘；合并的 rAF 不允许游标请求覆盖已排队的完整重绘。CPU 区间按 CPU 和原始区间序号识别，而非临时命中对象的引用。

备份仅覆盖当前视窗，不随 trace 长度增长，但会增加两张物理像素位图的显存／内存开销。鼠标离开、游标清除、切换 Text 视图时释放；隐藏视图中完成的排队帧也不得重新保留备份。完整重绘先以单位变换覆盖画布的全部物理像素，再恢复 DPR 变换，避免 DPR 1.25 等小数缩放时边缘残留半透明像素。回归逐像素比较缓存与强制完整重绘，覆盖 DPR 1、1.25、2，浅／深主题、尺寸变化、各类悬停、排队更新和缓存释放。

`python3 tests/benchmark_scheduling.py BEFORE.html AFTER.html --frames 10 --overview-frames 3 --cursor-frames 20 --output cursor.json` 可复现对比（基线 `e4c51f64f1ff`）。本机 Chromium 145.0.7632.6，每种叠加层 20 万条区间，全景同一调用内交替移动 ±2px，丢弃五帧预热，三轮交替运行，每版本 60 个游标样本：rAF 回调中位数 517.35 → 0.1ms，p95 547.28 → 0.2ms。全景缩放完整重绘仍为 531.2 → 515ms（各 9 帧），窄视窗仍约 0.3ms（各 30 帧）。这些结果只衡量回调和 Canvas 命令提交，不是实际呈现、完整指针事件延迟、手机帧率或首开时间，也不表示全景拖动、缩放已解决。

### 调度叠加层的视窗索引

每条任务轨道的 off-CPU、resched 和 IRQ-off 列表独立索引。少于 128 条直接检查；起止时刻均非递减时二分定位首个相交候选，否则每 128 条记录保留最早开始和最晚结束，用块边界排除不相交的块。构建时分批让出主线程，绘制与等待命中均保留原始顺序和包含端点的语义。开放等待仍以 trace 末尾作为可视化边界，保留未观察到恢复的身份；等待链、导出、Inline 行映射和 CPU 轨道上的 IRQ 标记不变。

使用 `python3 tests/benchmark_scheduling.py BEFORE.html AFTER.html --overview-frames 3 --output scheduling.json` 对比单文件报告（基线 `53548c10801a`）。本机 Chromium 145.0.7632.6，每种叠加层 20 万条有序合成区间，每版本 60 个窄视窗样本：重绘回调中位数 3.9 → 0.4 ms（p95 5.6 → 0.6 ms）；off-CPU 命中测试由约 250 → 0.5 μs（每个样本为 200 次命中的平均值）。全景每版本 9 帧，中位数 604 → 560 ms，仍需绘制所有相交区间。结果只衡量桌面 Chromium 的重绘回调和命中函数，不代表真机帧率、首次加载速度或屏幕呈现。

真实 traceB 对照保留同一 72,176 行、55,186 个调用、84,453 个模型帧和 12 个任务。Chromium 145.0.7632.6、1500×900、fgview HTTP、Timeline 预选，三轮交替旧/新报告，每次独立浏览器/context，无并行测试或基准。重复运行 `tests/benchmark_sidecar.py SINGLE.html SIDECAR_DIRECTORY --rounds 1 --text --output result.json` 可记录完整首开、准备反馈、最终阶段和长任务；对比旧/新 sidecar 时保持 trace/options 相同。以下为三轮中位数，不是跨机器承诺：

| 首次完整 Text 指标 | 旧版整体解析 | 协作准备 |
|---|---:|---:|
| 到双 rAF 就绪的总耗时 | 7,340.8ms | 7,954.7ms |
| 观测到的最长长任务 | 6,218ms | 3,780ms |
| 长任务超过 50ms 部分之和 | 7,136ms | 4,719ms |
| 准备进度的 rAF 采样次数 | 0 | 119 |

新版本约 1.18s 出现准备反馈、约 3.13s 出现最终布局阶段（均从首次 Text 打开计时，包含资源请求），但最大长任务仍达约 3.78s；总时间反而增加约 8.4%，不能称为“首开加速”。rAF 只是绘制机会，不是物理输入/呈现测量。挂载后采样 renderer RSS 仍约 3.4GiB，不是峰值或内存上限。每轮都完整恢复 72,176 行，释放 HTML 字符串，重入不重复请求 Text。

另用 `duration:>10 AND NOT graph:finish_task_switch` 核对统一索引过滤再完整挂载，双方均命中 4,804 行，行号 SHA-256 同为 `77070d8005bf1352b0fa19eeef9a4e7a050200ba8fd02e71038f6c02a49acedc`；过滤时不挂载 Text、不请求 text.html，进入 Text 后只请求一次完整资源。已有过滤、源码、任务排序、折叠、行号/锚点语义仍须保持。

回归记录：`FG_RAW_REQUIRED=1 tests/run.sh` 共 490 项，487 通过、3 跳过；本次新增 7 项浏览器用例，覆盖完整未挂载准备、取消/缓存重入、最终状态绘制机会、紧凑窗口双主题、Escape/Compare 切换、同步 probe 和解析失败重试。wheel/sdist 分别在独立新 venv 安装并运行 `python -I tests/smoke_installed.py --with-raw`，各 11 项（9 通过、2 个 extras 测试跳过）。以本次修改前的 `f2f4160fdfed` 重新建立真实内核基线，七组 golden 均仅在有视图脚本时替换有意变化的 `assets/views/boot.js`，再应用既有时间/环境掩码后完全一致；两组无 Timeline 的默认 Text 报告无需替换。JS 语法和 diff 检查通过，既有文件/socket ResourceWarning 未在本次修改无关代码。固定 sample HTML/PNG 未改动，本次不增加版本标签或发布。

原型通过 `fgDataManifest` JSON 配置 `{version:1, report_id, manifest:{file,bytes,sha256,format:"json"}}`。ID 为 32 位小写十六进制；manifest 内容为 `{version:1,report_id,resources:{tlData:{file,bytes,sha256,format:"json"}}}`，也可包含 `cmpData` 或 `text` 格式资源。manifest 路径相对 HTML，资源路径相对 manifest 目录。只允许 HTTP(S) 下同目录内的相对路径；禁止跨域、目录逃逸、编码路径和重定向，不带凭据。响应按声明大小有界读取，逐一验证长度、SHA-256、严格 UTF-8、JSON、版本及报告 ID；Web Crypto 需要安全上下文（如 loopback HTTP）。

重复请求共享底层 fetch，但每个订阅者独立取消；最后一个订阅者退出才 abort。请求身份校验避免取消后的旧结果污染缓存。切标签/Back/Escape 取消加载和模型迭代；失败显示原生 Retry 按钮。Timeline 拷贝 strings 和每条 offcpu 行，防止半初始化或重试污染缓存。`fgView()` 在外部请求期间返回 readiness Promise；内嵌兼容同步 probe。15 项真实 loopback HTTP 浏览器测试覆盖共享取消、重入、重试、紧凑窗口、损坏响应和模型一致性。

### Text 焦点与滚动边界（2026-11-11）

`setKeyboardSelectedLine(index, false)` 原先仍调用原生 `focus()`，浏览器会先自行滚动，使“不滚动”的初始化/恢复失效；显式导航再根据已经变化的矩形决定 center/nearest，也会失去原始目标位置。真实浏览器负对照中，视口从 `scrollY=400` 跳到远端约 32,000px。现在行焦点统一使用 `preventScroll:true`，仍保留原生焦点及 Enter 行为，只有显式导航分支负责平滑滚动。该分支以固定工具栏的实际下边界计算顶部安全范围，窄窗口工具栏换行后，原先落在工具栏后面的行也会被移到可见区域。三个新增浏览器回归覆盖双主题/390 与 1500px 窗口、不滚动更新、先聚焦后显式定位及紧凑工具栏遮挡，旧实现均不能通过。

本次先用同一 72,176 行 traceB 定位最终挂载：带 Profiler 的诊断轮中，`fgInitText` 约 3.83s，其中 `initKeyboardNavigation` 约 3.80s；CPU 样本大部分落在原生 focus，而 CDP 同时报告约 1.23s 的样式重算和 2.47s 的布局。不能把整个时间都归为键盘导航 JS 循环，也不能认为禁止原生滚动就会消除全量布局。`tests/benchmark_sidecar.py --text` 现在在 `first_text.browser_work` 记录独立 Text 窗口的 CDP script/style/layout 毫秒数和样式/布局次数，辅助区分脚本与浏览器工作；仍须同时检查完整就绪、长任务和真实交互，不用计数器替代体验测量。

最终生成报告以 Chromium 145.0.7632.6、1500×900、fgview HTTP、独立浏览器/context 三轮交替比较，测量时没有并行测试或构建。中位数完整 Text 就绪为旧版 8,749.7ms、新版 8,982.5ms，最长长任务为 4,158ms / 4,225ms，CDP 布局为 2,647.1ms / 2,654.4ms；没有证据表明本次焦点修复加速首开，仍有约 4s 的长任务及约 3.4GiB 的挂载后 renderer RSS。每轮均完整恢复 72,176 行、55,186 个调用、84,453 个帧和 12 个任务，Text 只请求一次、字符串缓存释放、切回不重载；全部 manifest/resource 大小与 SHA-256 通过，Text 和过滤索引逐字节一致，完整模型仅 `stats/build_time` 不同，源码池及行号索引一致。以上是本轮测量，不与前一轮协作准备测量混作同一对照。

另对 `content-visibility:auto` 做了未交付的诊断原型：直接为每块设置估算占位可减少屏外布局，但首次内容高度由 1,840,344px 变为 1,554,176px，横向滚动宽度由 1,770px 缩到 1,461px。即使原型的完整行数和 Range 选区字符数一致，也不能据此声称布局/滚动兼容。该原型没有进入产品；后续 Text 模型与投影解耦仍需处理完整过滤、折叠、源码、行号导航和可靠尺寸，不以简单 CSS 隐藏代替虚拟化验收。本次焦点修复不新增样式、资源分块或 DOM 上限。

验证记录：最初整套 `tests/run.sh` 进程被 SIGTERM 中断，未产生最终摘要，不计为通过；确认已退出后，按同一 unittest 发现清单分成 124 / 124 / 124 / 121 项，四批均设置 `FG_RAW_REQUIRED=1`。清单摘要相同、无重复或遗漏，共 493 项（490 通过、3 跳过），三个新回归在旧实现上的负对照失败、修复后通过。七组真实内核 golden 相对修改前 `c0ad4a5fa603` 仅替换有意变化的 `report.js` 后，在既有时间/环境掩码下完全一致。wheel/sdist 分别在独立新 venv 安装并各运行 11 项 raw smoke（9 通过、2 个 extras 测试跳过），两种安装态的 `report.js` SHA 与源码一致。390×640 双主题截图中，L808 原先顶部 156.28px 被下边界 167.28px 的工具栏遮挡，修复后顶部为 346.28px、原生焦点保留；样式和固定 sample HTML/PNG 未改动。JS 语法和 diff 检查通过；不升级版本、不打标签或推送。

### 完整 Text 过滤模型与耗时排序（2026-11-11）

`report/filter-index.js` 提供 `fgTextModel.fields/prepare/filter`。沿用 version 1 过滤索引和既有表达式/调用树求值器，不新增后端 schema 或符号解析。每条记录是稳定的非 DOM 对象，保留原始文本、行号及全部过滤属性；不再用随遍历变化的单个可变适配器。准备生成器每 1024 行让出一次，只缓存完整校验结果；取消后不能复用半成品。输入数据按不可变契约读取，不复制或修改已缓存的原始行。原有异步 indexed-filter 入口仍保留结果字段、进度和 AbortError 语义。

已挂载 Text 在第一次实际过滤时复用现有索引模型，或对全部 authored 行惰性快照。快照排除运行时生成的折叠摘要，按原 trace 行号恢复逻辑顺序，不受任务分组或耗时排列影响。模型只输出完整匹配行号集合，DOM 再投影显示状态；浏览器回归在移除全部 Text DOM 后仍用同一模型得到完整结果，后续查询也不再读取 DOM 的 comm/params 元数据。`fgData` 释放 HTML/索引输入缓存不等于释放模型：完整记录和 DOM 仍常驻，首次快照可能更慢并增加内存。折叠状态、源码展开、键盘/锚点及展示排序仍依赖全量 DOM；本次不是虚拟列表、按块网络加载、Worker 或有界内存。

同时修复原有 `sort:asc/desc` 分支被通用 `field:value` 分词提前捕获的问题。旧逻辑把未知 sort 字段作为通过条件，所以是**排序被忽略，而不是匹配集变空**。现在先识别带完整边界、大小写不敏感的排序指令；重复指令以后一个为准，非法取值仍保持原有未知字段行为。排序只改变 Text 展示，保留完整匹配集、行/源码相邻关系以及原有 task/time 偏好；清空、移除指令或手动 Order 可恢复/切换。仅排序的查询以 `lines=null` 保留在共享查询状态，Timeline 不改变时间顺序，也不为它提前下载 Text/索引；进入 Text 后再执行。新增浏览器回归先在旧实现复现该状态丢失，再验证修复及清除。

`tests/benchmark_sidecar.py` 新增可重复的 `--text-filter QUERY`，在末轮完整 Text 挂载后按序测量原生查询，记录同步调用、双 rAF 就绪代理、完整行数、匹配数/行号 SHA-256 及采样 renderer RSS。它与挂载前的 `--filter` 索引测量分开；检查查询不减少完整行库存、不重新下载 Text。小型 FakeKernel 的真实 CLI 验证涵盖重复查询、排序、索引/原生匹配一致性和 RSS 字段，不用其耗时推断大 trace 性能。

真实 traceB 使用同一 72,176 行、55,186 调用、84,453 帧和 12 任务，Chromium 145.0.7632.6、1500×900、fgview HTTP，三轮交替 `98807dbb3aae` 与本次最终报告，每次独立浏览器/context，计时期间无并行测试或构建。不预先执行 indexed filter，先完整挂载 Text，再依次运行下列查询；第一条包含惰性快照，其余复用模型。每次查询的命中数及完整行号 SHA-256 在两版六次运行中一致；所有行仍挂载，Text 只请求一次。以下为各指标独立中位数，时间单位 ms，**同步调用含匹配、DOM 投影及通知，不是纯匹配耗时**；双 rAF 是反馈代理，不是物理呈现：

| 顺序与查询 | 命中数 | 旧版同步 / 双 rAF | 模型同步 / 双 rAF |
|---|---:|---:|---:|
| 冷：`duration:>10 AND NOT graph:finish_task_switch` | 4,804 | 507.2 / 1,373.6 | 608.3 / 1,457.5 |
| 热：`func:mutex` | 386 | 202.6 / 302.8 | 173.3 / 274.9 |
| 热：`comm:.* AND duration:>1` | 17,691 | 209.7 / 1,081.0 | 174.1 / 1,102.2 |
| 重复第一条 | 4,804 | 236.4 / 459.5 | 173.4 / 409.7 |

冷查询更慢；后续同步查询在本轮较快，但完整反馈并非每种查询都加速。首次 Text 完整就绪为 8,146.5 / 8,120.0ms，不能据此宣称改善首开。挂载后的采样 renderer RSS 为 3,263.6 / 3,476.8MiB，冷查询后为 3,234.1 / 3,464.2MiB；挂载时尚未创建惰性模型就已有采样差异，不能把差值全部归因于模型，也没有内存优化结论。完整模型新增常驻元数据，完整 DOM、布局和重绘仍是后续窗口化需要解决的成本。

最终验证：38 项针对性 Node/真实 HTTP 浏览器回归通过；完整 unittest 发现清单分为 127 / 127 / 127 / 126 项，四批均启用 `FG_RAW_REQUIRED=1`，清单 SHA-256 相同且无重叠或遗漏，共 507 项（504 通过、3 项可选跳过）。七组真实内核 golden 相对 `98807dbb3aae` 只替换两份有意变化的 JS，沿用既有时间/环境掩码后完全一致。真实大报告的全部 manifest/resource 大小与 SHA-256 通过，完整 Text 和过滤索引逐字节相同，源码池及行号索引相同，Timeline 仅既有 `stats/build_time` 不同。固定 sample HTML/PNG、版本号与后端数据格式不变；后续 Text 折叠/导航解耦和窗口化仍未完成。

### 折叠可见性与批量投影（2026-11-11）

三个真实浏览器负对照确认原实现的问题：过滤直接覆盖 `display` 会打开仍标记 folded 的祖先；展开块直接恢复相关行会泄漏过滤拒绝的调用及源码；Clear 使用未加报告前缀的旧 `expanded_<id>` 键，会丢失当前 `expandedLines` 中保存的可见源码状态。现在过滤匹配集与折叠可见性分开：Text 显示必须同时满足完整匹配集和所有已折叠祖先/自身出口规则。清空过滤保留折叠，展开保留过滤；源码只在行可见且保存了展开偏好时恢复。匹配行号集合、计数及 Timeline 过滤不受 Text 折叠改变。

本阶段的 `fgTextModel.folding(model)` 在首次折叠时惰性构建并缓存纯元数据视图，读取既有 version 1 字段而不改变后端 schema。按原始 fold ID/祖先 tokens 定义成员，支持任务交错、递归同名、未配对入口和深层祖先；不根据当前 DOM 排列或函数名猜测调用树。入口/出口引用及后代调用数来自完整记录；计数包括后代入口与叶子，不计出口/本入口，不受过滤或折叠影响。祖先字符串按组去重解析；不为每个祖先永久保存整份后代 DOM/行数组。当时单次成员查询仍遍历完整记录；后续的协作准备与范围索引见下文，缓存仍非有界结构，Text 窗口化尚未完成。

深度、递归和保存状态恢复先修改所有相关折叠状态、控制与摘要，再统一投影一次并刷新键盘可见行/层级线。摘要计数不再为每个块查询整页后代；返回值、实测耗时、原文 errno 格式仍读取对应真实入口/出口的展示属性，保留原有技术语义。深度操作仍读 `data-call-depth`，源码展开及导航仍依赖完整 DOM。批量投影也可能触发全量样式/布局，不把减少查询次数当作已验证的端到端性能收益。

新增 Node 验证在 document getter 抛错时完成成员、计数和过滤/折叠组合求值，包括 600 层祖先、任务交错、缺失入口及精确 token 匹配。浏览器回归除三个负对照外，还拦截 Document/Element 的 data-fold 成员查询，确认深度、全展开和递归操作各只调用一次统一投影，缓存复用且全量行数不变；HTTP sidecar 回归覆盖索引过滤、Text 挂载/重入、完整匹配集和只请求一次 Text。

无变化投影也需避免写入：本轮早期原型在小 fixture 上有 1,014 次不必要的按钮文本子节点重写。现在更新前比较行/源码的 display 和按钮内容，MutationObserver 回归中的同状态投影没有 DOM mutation；仍会遍历元数据和刷新导航/画布，不等于零工作。

真实 traceB 沿用 72,176 行、55,186 调用、84,453 帧、12 任务，使用 Chromium 145.0.7632.6、1500×900、fgview HTTP。对 `d52da146a1f9` 与最终报告，每类操作各三轮交替、每次独立浏览器/context；先完整打开 Text，不预过滤、不预建模型，测量期间没有并行测试或构建。固定目标是 L282 的 `vfs_statx`、fold ID `f7t`，有 28 个后代入口。每轮依次折叠、展开、再折叠、再展开；递归操作修改 29 个折叠状态，普通操作只修改目标自身。两类各六次观察的每一步均比较完整可见行号、折叠 ID、全部摘要文本的 SHA-256，结果相同；折叠时 72,087 行可见，展开后恢复全部 72,176 行。每次只请求一次 Text，不下载过滤索引；源数据 ready 状态只表示提供者可用性，不将它误当成“源码尚未解析”。

以下为各指标的独立中位数，单位 ms；同步时间包含状态、摘要、DOM 投影和键盘索引更新，双 rAF 只是反馈代理，不是物理呈现时间：

| 操作 | 旧版同步 / 双 rAF | 模型同步 / 双 rAF |
|---|---:|---:|
| 冷递归折叠 | 3,278.7 / 3,398.7 | 814.4 / 934.1 |
| 热递归展开 | 1,650.4 / 1,750.1 | 233.1 / 330.5 |
| 再次递归折叠 | 3,315.2 / 3,410.9 | 215.5 / 311.2 |
| 再次递归展开 | 1,686.8 / 1,787.1 | 220.6 / 321.0 |
| 冷普通折叠 | 117.4 / 211.6 | 585.8 / 702.5 |
| 热普通展开 | 57.0 / 155.3 | 70.2 / 170.1 |
| 再次普通折叠 | 113.4 / 208.1 | 68.9 / 161.9 |
| 再次普通展开 | 58.0 / 157.6 | 75.5 / 172.7 |

递归批处理有明显收益，**普通冷首击退化约 491ms，普通热展开也没有改善**，不可据递归表格宣称每次点击都更快。首次需准备完整记录/折叠缓存，单次操作仍遍历完整元数据，还增加了正确刷新键盘可见索引的工作。后续应在协作 Text 准备期间产出完整模型，而不是用预热计时器把成本藏到别处；目前未实现。普通冷折叠后的采样 renderer RSS 为 3,464.9 / 3,529.7MiB，最后一步为 3,480.5 / 3,553.2MiB；其他轮有不同的 GC/分配波动，不是峰值，也没有内存降低结论。完整 DOM、同步初始化及布局仍需窗口化解决。

最终完整回归启用 `FG_RAW_REQUIRED=1`，同一发现清单按 129 / 129 / 129 / 128 分批，摘要一致且无重复或遗漏：515 项，512 通过、3 项可选跳过。七组真实内核 golden 相对修改前 `d52da146a1f9` 仅替换两份有意变化的 JS，在既有时间/环境掩码下完全一致。大 trace 的全部 manifest/resource 大小与 SHA-256 通过，完整 Text/过滤索引逐字节相同，源码池及行映射相同，Timeline 仅既有 `stats/build_time` 不同。版本、固定 sample HTML/PNG、样式和后端 schema 不变；本次不是完整 Text 虚拟化。

### 折叠元数据纳入协作准备（2026-11-11）

针对上节普通冷首击的回退，延后加载的 Text 现在在节点仍位于文档外时准备完整原始记录及折叠事实。`fgPrepareTextModel` 复用已准备的索引模型，或从每条已解析原始行读取规范字段；不额外请求 `text-index.json`，也不将生成的折叠摘要混入原文。行快照每 512 条让出执行机会，记录校验沿用完整索引生成器，`fgTextModel.prepareFolding` 每 512 条记录让出机会。所有祖先 tokens、入口/出口及完整后代计数准备完毕后才缓存折叠事实，根模型随最终挂载发布；第一次点击直接复用完整模型。

取消关闭嵌套生成器，未挂载的节点和部分折叠事实不会发布，已校验 HTML 留作重入。挂载前元数据失败仍显示 Retry，不复用部分初始化状态；最终初始化失败仍要求 Reload report。直接同步 probe 和内嵌 Text 保留同步完成的兼容路径，后者依旧按需建模。因此本次主要改善 sidecar/auto-sidecar 的冷交互，不宣称默认内嵌报告也消除了首次建模。准备仍在主线程，单次 DOM 查询、排序、复杂记录、最终初始化、布局和 GC 都可能超过调度预算；不是 Worker、虚拟列表、子树范围索引或有界内存。

**对照方法**：`07b48dae8ced` 的既有真实 traceB sidecar 与当前重新生成的报告，均为 `/tmp/pi4/traceB.txt`、Pi 4 vmlinux/源码、`--timeline --enable-fold --func-links --fast --filter`，没有源码高亮，线上源码地址固定为 Elixir `v6.18`。完整 72,176 行 / 55,186 调用 / 84,453 帧 / 12 tracks；Chromium 145.0.7632.6、1500×900，每次独立浏览器/context，通过真实 `fgview` HTTP。普通与递归场景各三轮交替前后顺序，共十二次观察，计时时不并行运行测试/构建。目标仍为 `vfs_statx` L282 / `f7t`、28 个后代入口，每次折叠/展开/折叠/展开；普通改变 1 个状态，递归改变 29 个。没有预过滤或手动预热模型；当前模型由实际 Text 打开流程生成，而不是在测量前另行调用准备函数。

| 操作（同步 / 双 rAF 反馈代理中位数，ms） | 惰性建模 | 协作准备 |
|---|---:|---:|
| 冷普通折叠 | 587.7 / 707.0 | 73.4 / 174.8 |
| 热普通展开 | 78.9 / 196.2 | 74.2 / 174.5 |
| 再次普通折叠 | 70.6 / 165.1 | 71.2 / 170.3 |
| 再次普通展开 | 71.1 / 170.3 | 80.2 / 189.9 |
| 冷递归折叠 | 802.3 / 922.6 | 262.6 / 382.6 |
| 热递归展开 | 331.0 / 443.1 | 219.4 / 324.6 |
| 再次递归折叠 | 211.8 / 317.2 | 214.6 / 314.5 |
| 再次递归展开 | 217.7 / 317.4 | 220.2 / 327.4 |

这是**把首击成本纳入可反馈、可取消的准备过程，而不是让工作消失**：普通场景完整 Text 打开到双 rAF 的中位数由 8,159.1 增至 8,809.0ms，递归场景由 8,287.9 增至 8,918.7ms，分别增加约 650/631ms。热普通再次展开还有小幅回退；不据冷首击宣称所有操作或启动都更快。普通冷折叠后的采样 renderer RSS 为 3,528.8 / 3,265.4MiB，最后一步为 3,555.1 / 3,158.9MiB；准备/分配/GC 时机改变，采样不是峰值，这里没有验证内存优化或内存上限。所有观察均保留完整模型与行，每个步骤的完整可见行号、折叠 ID 和摘要文本哈希在六次同场景观察中相同；Text 各请求一次，没有过滤索引请求，源码就绪状态未被点击改变。双 rAF 是反馈代理，不是物理呈现时延。

验证新增一项纯模型取消/完整缓存测试与三项真实 HTTP 浏览器测试，覆盖挂载前分阶段准备、首击复用、取消/重入不重复下载、挂载前失败 Retry。完整回归以 `FG_RAW_REQUIRED=1` 按 130 / 130 / 130 / 129 批执行，发现清单 SHA-256 一致且无重复遗漏：519 项，516 通过、3 项可选跳过。七组真实内核 golden 相对 `d52da146a1f9` 精确替换三份有意变化的 JS 后一致（无 Timeline 的报告没有 boot 资源），其余沿用既有掩码。新生成大报告的完整 Text/索引逐字节相同，源码池及行映射相同，Timeline 仅 `stats/build_time` 不同，全部 manifest/resource 大小与 SHA-256 验证通过。版本、固定 sample HTML/PNG、CSS 和后端 schema 未改变；后续仍需模型驱动导航和真正窗口化。

### Text 数字行号导航模型（2026-11-11）

两个真实浏览器负对照复现了导航问题：从非可展开行出发，旧代码先找最近调用再额外移动一位，向下把 L17 错跳为 L18，向上也会错过最近调用；重排后虽然保留了选中节点，`keyboardSelectedIndex` 仍为旧位置，例如任务分组后实际位置从 16 变为 1139，键盘仍从 16 继续导航。

`fgTextModel.navigation` 现在保存完整数字行号、源码展开能力、显式隐藏集合和展示排列，不保存 DOM 节点。初始排列与完整成员必须唯一，重排必须是全部原始行号的排列，缺行/重复/异报告行号失败且不覆盖旧顺序。原始 trace 顺序和过滤记录保持不变；时间/任务/耗时展示重排提交新的完整排列，通过行号 → 位置 Map 保留选中行身份。过滤与折叠在投影行时同步更新逻辑可见性；候选刷新只扫描完整数字元数据，不再枚举整页 DOM 或逐行读取样式/展开能力。可见候选按展示位置排序，最近调用查找使用二分，非调用行不额外跨一条，首尾保留循环。Home/End 在没有可见源码调用时使用可见普通行，没有可见行则不操作。

Sidecar 在挂载前逐批采集完整导航事实并准备索引，取消关闭嵌套生成器，不发布部分排列，重入复用已校验 HTML。内嵌 Text 在首次初始化导航时采集一次，后续不反复建表。行点击从事件的实际行直接找位置，hash 选择按稳定行号找位置；只对目标节点执行焦点/滚动，高亮归入已有 `highlightedLine` 状态，因此 Escape 也能清除锚点高亮。已移除导航常驻的全页 `allLines` / 可展开 DOM 节点数组，保留位置数组兼容现有按键处理。原生 `preventScroll` 和固定工具栏定位语义不变。

该版的范围限制：展开能力仍从完整原始 DOM 行采集，候选重建仍为全量数字扫描；源码填充、完整 DOM 重排、复制/视口辅助函数和原生焦点/滚动/布局仍依赖已挂载节点。准备阶段的最终 Map/Set 构建和布局没有固定时延保证；索引本身也有常驻内存成本。后续窗口化需要从结构化索引直接交付能力元数据，并在定位目标前确保其正确呈现；该次改动不能宣称已经支持未挂载行跳转、网络分块、视口 DOM 上限或有界内存。

回归新增三个无 DOM 的 Node 用例、四个真实浏览器用例及一个 HTTP sidecar 用例：覆盖非连续行号/展示排列、严格完整性/空候选、131,072 个候选下少于 50 次数组探测的双向查找、最近调用、重排身份、热路径禁止全页查询、过滤/折叠/耗时排序的完整候选一致性，以及准备取消/重入与 360px 浅深主题状态/返回按钮边界。已有焦点、固定工具栏、折叠投影用例保持原断言，仅将内部 DOM 数组探针改为目标行号解析。

**真实大报告对照**：`e83ead378f56` 生成的 sidecar 与当前重新生成的报告，同一 traceB/命令参数/完整资源，72,176 行 / 55,186 调用及可导航调用 / 84,453 帧 / 12 tracks。Chromium 145.0.7632.6、1500×900、真实 `fgview` HTTP、独立全新浏览器/context，三轮交替顺序，共六次观察；计时期间没有并行测试或构建。先完成实际 Text 打开，不额外预热模型；对 `f7t` 做四次普通折叠/展开，随后选取末尾第三个可展开行，测两次行选中、四次前后导航及一次 hash 选中。

| 操作（同步 / 双 rAF 反馈代理中位数，ms） | DOM 导航缓存 | 数字行号索引 |
|---|---:|---:|
| 首次普通折叠 | 78.5 / 184.0 | 11.5 / 114.4 |
| 普通展开 | 84.3 / 195.1 | 9.7 / 119.1 |
| 再次普通折叠 | 84.9 / 198.0 | 10.6 / 114.0 |
| 再次普通展开 | 78.0 / 185.3 | 10.0 / 135.3 |
| 首次行点击的选中处理 | 68.9 / 153.6 | 0.8 / 81.7 |
| 重复行点击的选中处理 | 47.7 / 87.1 | 0.1 / 21.7 |
| 首次向后导航 | 49.7 / 133.5 | 41.7 / 133.4 |
| 首次向前导航 | 50.5 / 316.3 | 50.2 / 319.1 |
| hash 选中处理 | 109.7 / 404.8 | 15.8 / 307.2 |

折叠同步处理内整页行查询由每次 2 次降为 0，行点击/hash 由每次 1 次降为 0。这里的行点击只计选中阶段，主动取消了原有 200ms 延迟源码展开定时器，**不包含原生鼠标命中测试、源码填充或完整点击体验**；键盘通过同一导航函数调用，双 rAF 不是物理呈现或平滑滚动完成时间。键盘前后导航仍受焦点/滚动/布局影响，向前的反馈中位数还有小幅回退，不能用二分探测数宣称其完整响应明显更快。完整 Text 就绪由 9,449.2 增至 9,539.4ms，约增加 90.2ms；末步采样 renderer RSS 为 3,230.4 / 3,338.5MiB，观察到约 108.1MiB 增加，但不是峰值，也不能把分配/GC 时机差异全归因于索引。没有启动加速、内存优化或有界内存结论。

全部六次观察保留完整行/调用/帧/轨道和候选数，每个折叠步骤的完整可见行号、折叠 ID、摘要哈希相同，每个导航步骤的目标 ID/展示位置相同；Text 各请求一次，没有过滤索引请求。最终 `FG_RAW_REQUIRED=1` 完整发现清单按 132 / 132 / 132 / 131 批执行，SHA-256 一致且无重复遗漏，共 527 项（524 通过、3 项可选跳过）。七组新生成真实内核 golden 相对 `d52da146a1f9` 仅精确替换三份有意变化的 JS，沿用既有掩码后一致；大报告的完整 Text/过滤索引逐字节相同，源码池/行映射相同，Timeline 仅 `stats/build_time` 不同，全部 manifest/resource 大小与 SHA-256 通过。版本、固定 sample HTML/PNG、CSS 和后端 schema 不变。

### Text 源码能力的结构化交付（2026-11-11）

Text 索引现写出 version 2：`fields` 的 14 个过滤属性和原有位置不变，每行在 `[lineNumber, text, ...attributes]` 后追加 `source`。该列是源码池的安全非负整数 ID，`0` 有效、多个调用可复用同一个 ID，`null` 明确表示原始行没有源码展开能力。不能根据函数名、折叠类型或 raw 返回地址猜测能力；没有解析环境的 raw 仍完整保留调用，但其源码列为 `null`。后端为每个可展开行仅调用一次既有 `_source_index`，将同一个 ID 写入 Text 占位节点、`fgSrcLines` 和过滤索引，不追加符号化或改变源码池顺序。写出仍逐行流式进行、保持精确 UTF-8 大小/哈希，非法 ID 在写入该行之前失败，不保留完整 JSON 数组。

`fgTextModel.prepare` 同时严格校验 version 1/2，保持原属性的空字符串/缺失语义；`record.source` 对 version 2 返回明确 ID/null，对 version 1 返回 `undefined`，区分未知能力与不可展开。只缓存完整模型，读取不修改原始行。`prepareNavigation(model)` 从完整 version-2 记录协作准备初始 trace 排列和源码候选，不接收 DOM；该初始状态刻意为全部可见，后续排序、折叠和过滤仍使用已有显式排列/可见性更新，不用初始建表覆盖当前状态。

延迟 Text 在最终挂载前传入已完成的模型：已有完整索引时无需再读 DOM 能力；没有索引时，在原有完整 DOM 快照循环中同时采集准确 source ID，然后使用同一纯记录导航路径，不为此新增索引下载。version-1 未知能力保留 DOM 回退；内嵌 Text 首次导航也仍按当前展示行采集一次。源码 ID 缺失/损坏的未挂载快照失败可重试，不发布部分根模型/排列。取消关闭嵌套生成器，最终挂载恢复既有用户状态。

真实 traceB 仍为 72,176 行 / 55,186 可展开调用，完整 Text 字节、源码池及行映射不变；全部原有过滤记录单元逐一相同，Timeline 仅生成用时不同。索引由 34,285,238 增至 34,581,620 字节，增加 296,382 字节，这是显式交付能力的成本，不是内存优化。逐行核对新 source 列与全部 HTML 占位节点/行映射/源码池，并验证全部资源大小和 SHA-256。七组真实内核 golden 仅精确替换三份有意变化的 JS，沿用既有掩码后相同。固定 sample 用原有 739 行 / 485 调用和固定 v7.4-rc2 内核重新生成，纳入先前尚未更新到 sample 的前端修复；两个 sample 截图重新拍摄后字节相同，没有视觉外观变化。

对照 `113e519fd1dc` 的同一 traceB sidecar：Chromium 145.0.7632.6、1500×900、真实 `fgview` HTTP，每次独立新浏览器/context；先完成 Timeline，分别测试没有过滤索引和已完整下载/解析索引两种情形，各三轮交替先后顺序，共 12 次观察。计时期间没有并行测试或构建。模型/导航阶段时间累计生成器各次 `next()` 的同步执行，不含让出后的等待；Text 就绪计从切换开始到完整 API 返回后的双 rAF，不是首次 loading 反馈或实际物理呈现。预加载索引的时间单列，不混入随后 Text 打开。

| 中位数，ms（RSS 除外） | 未读索引：DOM 能力 / 结构化能力 | 已读索引：DOM 能力 / 结构化能力 |
|---|---:|---:|
| 完整记录/折叠模型准备 | 386.5 / 422.8 | 128.5 / 129.0 |
| 导航准备 | 104.1 / 15.6 | 118.7 / 20.7 |
| 完整 Text 双 rAF 就绪 | 9,109.2 / 8,982.1 | 8,684.5 / 8,529.5 |
| 预加载完整索引（不含模型准备） | 不请求 | 461.8 / 437.3 |
| 末步 renderer RSS 采样，MiB | 3,267.0 / 3,216.6 | 3,248.5 / 3,173.7 |

无索引时采集 source ID 使快照阶段增加约 36.3ms，导航阶段少约 88.5ms；不能只报告后一项而忽略前者。两个情形的完整就绪仅观察到约 127.1 / 155.0ms 差异，主耗时仍是完整加载/挂载/布局，三对样本不能证明普遍启动加速。RSS 是导航身份摘要生成后的单次 renderer 采样，不是峰值，可能受分配/GC 时机影响，不作内存优化结论。全部 12 次保留完整行/调用/帧/轨道及 55,186 个候选，每对完整排列/可见位置/候选位置的 SHA-256 一致；Text 请求各一次，索引仅在明确预加载的六次请求一次，没有为了新导航增加请求。

最终 `FG_RAW_REQUIRED=1 tests/run.sh` 为 537 项，534 通过、3 项可选跳过；31 项 raw 专项和 23 项 writer/无 DOM 模型用例通过。wheel/sdist 在独立全新环境中分别做基础/`[all]` 安装，基础各 9 项通过、2 项可选跳过，`[all]` 各 11 项全部通过，包含准确源码 ID 的安装态检查；Twine、JS 语法和差异检查通过。

范围仍未完成：初次没有索引的快照、源码填充、目标节点焦点/滚动和完整 Text 的挂载/重排/布局仍依赖 DOM；纯导航最终 Map/Set 构建也无固定延迟保证。尚无未挂载目标的呈现接口、网络分块、视口 DOM 上限、源码 LRU 或有界内存，不能据此把第二阶段窗口化验收标记为完成。

### 折叠范围索引与局部投影（2026-11-11）

纯模型测试在准备结束后禁止记录属性读取，旧实现的 `members(id)` 确定失败：小子树查询仍遍历全部记录寻找成员。批量状态投影虽然已经合并为一次调用，却仍传入完整 `folding.records`。这两个热路径都必须按真正受影响的范围工作，而不是只减少 DOM 查询次数。

`prepareFolding()` 现按原始记录序号，为 own fold ID 和去重后的完整祖先 token 生成半开成员范围，压缩相邻成员；不能用最小/最大 trace 行号作为整个子树的包络，否则会误包含交错任务。范围最终编码为一份共享 `Uint32Array`，Map 保存每个 ID 的数值偏移，每组头部保存端点数量。偏移 0 有效，缺失必须以 `undefined` 判断。保留空/null own ID 的旧匹配语义、稀疏行号、重复祖先、缺失入口及完整后代调用数，不改变 schema。记录准备和范围最终编码均每 512 个单位让出执行机会；只有全部完成才发布缓存，取消后重试不能复用部分范围。

`members(id)` 直接枚举对应范围，不再读全 trace 的成员属性。`membersOf(ids)` 去重 ID，排序并合并范围，在原模型顺序中只枚举并集一次；交错任务的非成员空隙保留，重叠的父/子范围不会重复输出。`fgSetFoldStates()` 只收集真正改变状态的 scope，无效重复变化和不存在的 ID 不扩大投影。单个折叠也复用范围查询；过滤、任务/时间排序和源码恢复仍遵守原本的可见性规则。

真实对照使用 `/tmp/pi4/traceB.txt` 的完整 72,176 行、16,747 个折叠块，目标 L282 `vfs_statx` / `f7t`，含 60 个已记录后代调用、90 个成员行；本次没有提供 vmlinux/DWARF 或源码块，不能当作源码丰富报告的性能证据。双方以相同参数生成 sidecar，对照资源来自 `44cb6f29f5e8`，新版使用共享 Uint32 范围索引。Chromium 145.0.7632.6、1500×900、真实 `fgview` HTTP，各类三对交替先后顺序，每次新浏览器/context；计时期间没有并行测试或构建。先完整准备 Text，再连续折叠/展开四次，调用与控件相同的生产 API；同步 handler 与双 rAF 反馈分别计时，后者只是反馈代理，不是物理鼠标到屏幕呈现时延。

以下为每个步骤三次观察的独立中位数，单位 ms：

| 操作 | 普通旧 handler / 双 rAF | 普通新 handler / 双 rAF | 递归旧 handler / 双 rAF | 递归新 handler / 双 rAF |
|---|---:|---:|---:|---:|
| 初次折叠 | 8.5 / 90.4 | 2.9 / 84.3 | 145.2 / 228.0 | 3.7 / 86.3 |
| 展开 | 6.7 / 90.1 | 1.3 / 84.3 | 103.4 / 188.5 | 1.3 / 92.1 |
| 再折叠 | 8.2 / 87.3 | 2.1 / 99.3 | 115.5 / 210.8 | 43.8 / 144.8 |
| 再展开 | 7.2 / 99.3 | 1.3 / 90.0 | 98.4 / 183.3 | 1.7 / 98.9 |

普通操作双方都投影 90 行，收益来自去除全量属性扫描；递归投影从 72,176 行降至 90 行。投影计数在另两次操作中采集，不污染上述计时。不能隐藏慢样本：新版普通再折叠有 37.7ms，递归再折叠有 43.8 / 46.2ms，旧版普通末次展开也有 101.2ms。普通再折叠的反馈中位数反而增加，因此不能宣称每次点击反馈都更快。

准备并非免费：普通情形的 Text 就绪/定位中位数为 6,913.1 → 7,066.6ms，递归为 7,162.3 → 7,254.9ms；该指标包含导航、完整 Text 准备、双 rAF、目标查找和 `scrollIntoView` 调用，不是纯解析时间。四步结束后的 renderer RSS 采样分别为 3,173.4 → 3,188.4MiB 和 3,096.4 → 3,031.1MiB，非峰值/JS heap，不能据此证明内存下降或上限。每对各步骤的完整可见行号、折叠 ID 和摘要文本哈希相同，Text 各请求一次，没有过滤索引请求。完整 Text 91,810,464 字节和过滤索引 34,646,118 字节逐字节相同，全部 manifest/resource 长度与 SHA-256 通过；Timeline 只有 `stats.build_time` 不同。

复现（分别用对照/新版生成的独立报告目录）：

```bash
python3 funcgraph.py /tmp/pi4/traceB.txt --no-config --no-btf \
  --timeline --enable-fold --filter --report-mode sidecar --output /tmp/fold-after
python3 tests/benchmark_folds.py /tmp/fold-before /tmp/fold-after \
  --rounds 3 --output /tmp/folds-ordinary.json
python3 tests/benchmark_folds.py /tmp/fold-before /tmp/fold-after \
  --recursive --rounds 3 --output /tmp/folds-recursive.json
```

新增五项纯模型测试覆盖准备后禁止全量属性扫描、精确范围/并集、固定种子随机参考、3,000 层祖先和最终编码取消；三项浏览器用例覆盖只投影真实变化并集、无关行/源码 DOM 不受递归影响，以及过滤/重排后的完整可见性。索引准备/保留仍有成本，键盘候选刷新仍扫描完整数字元数据，深度控制、源码填充及完整 DOM 挂载/重排/布局仍有全量工作。这是模型驱动折叠的基础，不是 Text 网络分块、未挂载目标呈现、窗口化或有界内存已经交付的证明。版本、固定 sample HTML/PNG 和后端 schema 均未改变。

最终 `FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh` 共 768 项，765 通过、3 项可选跳过；暂存导出的冻结源码另跑 29 项模型/浏览器/sidecar 生命周期用例，全部通过。冻结源码构建的原生 wheel 和 sdist 重建的纯 Python wheel 在独立新 venv 安装 `[all]`，使用 `python -I tests/smoke_installed.py` 加 raw 检查：原生含 uftrace 的 13 项全部通过，纯 Python 的 13 项中 12 通过、1 项内置采集器检查按分发类型跳过。两种 wheel 及 sdist 的新 JS 与冻结源码逐字节相同，sdist 包含可复现基准；Twine、JS 语法、Python 编译、依赖锁定、uftrace 补丁和差异空白检查通过。完整回归仍有既有文件/socket ResourceWarning，未顺带修改无关测试。

### Text 网络分块与有界资源缓存（2026-11-11）

此前即使逐个解析 512 行绘制块，浏览器也必须先下载、校验并解码整份 `text.html`；72,176 行 traceB 的单资源为 91,810,464 字节。现在新生成的大型 sidecar 把**传输**与绘制块分开：超过 4,096 条原始 Text 记录（包含表头）时，每个 HTML 网络资源最多包含 4,096 条，保持 512 行绘制块及完整行/源码占位配对。小型 sidecar 和旧报告仍读完整 `fgText` / `text.html`；single 以及 auto 未超过显式字节阈值的结果不使用外部块。这个记录数仅是 sidecar 内部资源分组规则，不新增自动分流默认值。

`ReportSpool.write_sections(..., observer=...)` 将已编码的每个片段及真实起止字节传给观察器，不重新编码、不扫描巨型 HTML 标记、不再次解析/符号化 trace。`TextChunkRanges` 核对生成器原生的开块、行、闭块与连续边界，压缩为网络范围，并与同次生成的完整 Text 索引记录数核对。仅确定采用 sidecar 后，按这些范围有界复制资源；不足阈值不会额外导出网络块。分块结果不重复保留整份 `text.html`，原子发布和旧版本保留约定不变。

分块 manifest 的 `capabilities.text` 为 `chunks-v1`，资源命名 `fgText0`、`fgText4096`……，对应 `text-0.html`、`text-4096.html`……；每份仍有独立精确字节数、SHA-256 及 `format:"text"`。HTML 内嵌 `fgTextChunks`，形如 `{version:1,count:72176,chunk_size:4096,paint_rows:512}`。这里的 `first` 和 `fgData.loadTextChunk(row)` 参数是**从 0 开始的原始记录序号，不是 trace 行号、过滤后的行号或 DOM 展示位置**。提供者核对目录 schema、数值范围、对齐的资源身份、完整资源组集合和文本格式；资源组数与目录不符不能悄悄丢弃整块尾部。传输完成后仍验证大小、摘要及严格 UTF-8，解析时逐绘制块核对记录数量，缺行/多行/错误边界均在挂载前失败。

Text HTML 资源使用独立 LRU：最多 4 块、合计 16 MiB **已校验 UTF-8 字节**。与源码的 8 块 / 16 MiB 预算分开，不淘汰 Timeline、过滤索引或其他数据。读取/命中刷新，ready 探针不刷新，释放归还预算；超预算单块完整返回当前请求但不缓存，也不挤出其他块。行数限制不约束单行字节数；这不是网络并发、JS heap、完整 DOM/模型或整个报告的内存上限。

Text 准备生成器可让出带 `load(signal)` 的等待步骤；调度器在异步成功后只向仍然有效的生成器传入结果。`fgView('text')` 不会同步排空正在等待网络的分块生成器。切标签/返回/Escape 关闭迭代器、释放未挂载节点并取消自己的订阅，底层共享请求由最后一个订阅者取消；即使第三方 loader 忽略取消，旧结果也不得继续准备或激活视图。网络/校验/挂载前解析失败均清理生成器并提供 Retry，最终初始化失败仍要求重载。重入复用仍在缓存的块，被淘汰的块需重新下载；不再承诺保存整份已校验 HTML 以免取消后重读。成功后释放全部 Text HTML 资源及输入索引缓存，切回已挂载 Text 不再下载。

当前消费方式仍顺序获取各网络块，在文档外累计**全部 DOM**，再建立/复用完整记录、折叠和导航模型，最后一次完整挂载。Timeline 初始显示与统一过滤不请求 HTML 块；既有过滤索引可复用，打开 Text 不会为此额外获取索引。跨块折叠/递归/过滤、源码、重排、锚点和窄窗口入口保持完整 trace 语义。已交付的是网络分块、随机块读取与有界 HTML 资源缓存，不是视口 DOM 窗口化；最终布局/完整模型内存及单个复杂块仍需继续优化。

测试覆盖精确 UTF-8 范围和 0 / 1 / 4,095 / 4,096 / 4,097 / 8,192 / 8,199 记录边界、非法/中断生成、索引一致性、混合空间 version-3 字段、故障保留旧报告/旧资源、随机块读取、独立缓存数量/字节/超大块、目录截断与校验错误、共享取消、等待取消/重入、不可取消迟到结果、挂载前 Retry，以及跨块调用树/源码/过滤/顺序/窄窗口锚点。可复现浏览器基准 `tests/benchmark_sidecar.py --text` 同时识别单资源和分块格式，验证完整记录数、缓存释放和返回 Text 后不重复请求全部资源；性能数据不以减少单个资源大小替代完整就绪、长任务和最终 DOM 成本。

实测使用 `/tmp/pi4/traceB.txt` 的 72,176 条记录，旧报告来自 `def33d122c99`，新报告使用本节分块实现。两者均启用 Timeline、折叠、过滤及 sidecar，不传 vmlinux，不解析 BTF；这份输入没有 DWARF/源码块，不能外推到源码丰富报告。18 个新 Text 块拼接后与旧 `text.html` 的 **91,810,464 字节逐字节相同**，完整过滤索引 **34,646,118 字节逐字节相同**；Timeline 只有生成耗时字段不同。全部 manifest 资源大小和摘要均核对通过。

Chromium 145.0.7632.6，1500×900，`fgview` HTTP，同机三对交替测量，每次使用新浏览器/上下文，测量期间无并行测试或构建。先显示 Timeline 再首次打开完整 Text，以下为各三次中位数：

| 指标 | 旧单资源 | 新网络分块 |
| --- | ---: | ---: |
| 首批已解析进度的 rAF 反馈 | 948.6 ms | 74.2 ms |
| 完整 Text 就绪（双 rAF） | 7,119.7 ms | 7,378.9 ms |
| Text 阶段最长长任务 | 3,320 ms | 3,402 ms |
| Text 阶段阻塞时间 | 4,067 ms | 4,113 ms |
| Text 阶段 script / style / layout | 1,575.4 / 994.1 / 2,252.2 ms | 1,799.7 / 1,017.7 / 2,311.5 ms |
| Text 后 renderer RSS 采样 | 2,905.9 MiB | 2,942.2 MiB |
| Text 资源请求数 | 1 | 18 |

收益是更早反馈首批解析进度及可独立读取、校验、淘汰的传输单元，**不是首开加速**：完整就绪反而增加 259.2 ms（约 3.6%），最长长任务、脚本/布局成本和采样 RSS 也没有改善。首批反馈排除初始空的 `Preparing Text…` 状态，但仍只是文档外批次解析后的状态绘制代理，不是已经看到 trace 行、物理呈现或交互延迟。RSS 为阶段采样而非峰值；有界 HTML 资源缓存不代表完整模型/DOM/报告内存有界。

六次均准备并挂载全部 72,176 条记录，完整 Text 模型摘要相同；Timeline 的 calls / frames / tracks 均为 55,186 / 55,213 / 12，初始 Timeline 不请求 Text。Text 成功后 HTML 资源缓存全部释放，返回 Text 不再请求资源，浏览器错误及 CSP 违规为零。完整 DOM 的最终同步布局仍是后续窗口化工作的重点，不能用进度提前掩盖这一回退。

复现生成和交替测量（旧报告需先用上述基线代码生成，两个输出必须使用相同输入/参数）：

```bash
python3 funcgraph.py /tmp/pi4/traceB.txt --no-config --no-btf \
  --timeline --enable-fold --filter --report-mode sidecar --output /tmp/fg-text-after
python3 tests/benchmark_text_delivery.py /tmp/fg-text-before /tmp/fg-text-after \
  --rounds 3 --output /tmp/fg-text-delivery.json
```

最终验证：`FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh` 共 **789 项，786 通过、3 项可选跳过**。冻结暂存代码的 `test_textchunks test_data_provider test_output test_sidecar` 专项 **154 项全部通过**。从冻结快照构建原生 wheel、sdist 和由 sdist 重建的纯 Python wheel，安装到独立虚拟环境后以 `python -I tests/smoke_installed.py` 的对应 `--with-extras --with-raw [--with-uftrace]` 模式验证：原生 **14/14 通过**，纯 Python **13 通过、1 项内置 recorder 按包类型跳过**，包含真实 CLI 分块输出及安装态 HTTP 资源校验。三份产物通过 Twine 检查；固定版本、sample HTML/PNG、依赖 pin 和 uftrace 补丁均未更改。

### 只读回环查看器（2026-11-10）

`fgview` 的安装态入口是 `fglib.viewer:main`，源码目录可用 `./fgview` 或 `python3 -m fglib.viewer`。只绑定 `127.0.0.1`，默认端口 0，由操作系统分配；显式 `--port` 限制为 0–65535，没有全网绑定参数。先打印本次随机路径 URL，只有 `--open` 才调用浏览器，Ctrl+C 关闭监听及目录句柄。选单文件只提供该文件；选目录必须存在 index.html，提供其中的普通非隐藏文件，但不列目录。目录模式需要用户指定独立报告目录，不能当作只允许 manifest 资源的 allowlist。

根目录先解析用户显式选择的路径，再从文件系统根起逐级 no-follow 打开，并核对选中对象的 dev/inode，随后固定目录句柄。后续每级路径使用 `dir_fd + O_DIRECTORY + O_NOFOLLOW`，叶子使用 `O_NOFOLLOW + O_NONBLOCK` 后检查普通文件，防止符号链接置换及 FIFO 阻塞；平台缺少这些能力就明确拒绝，不降级成跟随链接。关闭与根句柄复制共用锁，单次请求持有独立目录/文件句柄，重命名目录或并发请求不共享文件偏移。HTTP GET 以不超过 1 MiB 的块发送，HEAD 不读取正文；启动不解析或提前读取 Timeline、Text、源码资源。路径校验在一次严格 UTF-8 URL 解码后进行，拒绝逃逸、空组件、反斜杠、NUL 与隐藏路径。显式选择的入口可以先解析符号链接，但解析后的路径和根下面的内部链接不能在打开时重新跟随。

请求要求正确回环 Host 和随机路径前缀，显式 Origin 必须同源，没有 CORS、目录列表或写接口。响应禁缓存、禁止 MIME 嗅探及跨源资源读取；CSP 允许报告现有内联脚本/样式和同源 fetch，但不开放 unsafe-eval、表单、base 或嵌入。随机 URL 只是防网页猜测入口，不是多用户认证；可信报告的 JS 仍在浏览器执行，不提供 HTML 安全沙箱。该查看器不改变生成输出、模型、阈值策略或 DOM 规模，也不产生 sidecar；后续仍需资源发布、Text 模型解耦/虚拟化及源码按需读取。命令、安全边界及排障见 [English](viewer.md) / [中文](viewer.cn.md)。

- 时间轴内部仍以归一化文本建模；已支持 trace-cmd 二进制容器。独立 `trace_pipe_raw` 裸页仍缺少解码所需元数据，不支持实时流；raw 参数目前是原始寄存器值，不是 BTF 格式化参数。
- 没有 `latency-format` 时，上下文是根据函数名推断的，覆盖了常见的中断入口函数，但不完整。
- `funcgraph-abstime` 只精确到 us。时间戳和 duration 不一致的部分用限制在父切片内的方式修正，修正次数记录在 `stats.clamped` 中。
- 文本视图仍然为每一行生成 DOM，十几万行以上的 trace 浏览器难以打开；目前的办法是预过滤或 `--no-text-view`。下一步是虚拟列表，需要重写 `report.js` 中直接操作 DOM 的过滤、折叠和键盘导航。
- 开销修正假设每个被跟踪调用的开销相同；在中断、迁移或缓存行为不同的路径上会有偏差。

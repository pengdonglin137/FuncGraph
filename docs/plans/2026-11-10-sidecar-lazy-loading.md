# 大报告 sidecar 与按需加载方案

2026-11-10 更新：显式 `--timeline --report-mode sidecar --output REPORT_DIRECTORY` 和可选 `--report-mode auto --sidecar-threshold BYTES` 已实现，使用 `fgview REPORT_DIRECTORY` 打开。默认单文件不变，没有默认阈值；auto 必须显式指定完整 HTML 字节数阈值，只有严格超过才 sidecar，否则在同一目录生成可离线打开的 index.html。Timeline 首屏不请求 Text/Compare/过滤索引，也不创建逐行 Text DOM；统一过滤按需读取完整 `text-index.json`，不挂载 Text。Text 在首次访问或行号/锚点跳转时**完整加载**，重新应用当前过滤并释放索引缓存，随后保留 DOM。源码仍内嵌；Text 虚拟化、源码分块/LRU 仍未实现。详见 [查看器指南](../viewer.cn.md)。

源码前置解耦已完成：`fgSrcLines` 保存真实行号 → source ID，Timeline 从共享 `fgData` / `fgSrc` 查调用点，不再要求对应 Text 行或占位 DOM 已挂载；源码块按完整函数标识缓存。可选 `--timeline --no-text-view --keep-source` 保留完整源码池及 inline，同时省略逐调用 Text DOM，默认轻量模式不变。源码池/索引仍完整内嵌、首次使用时同步解析，尚无外部源码分块或 LRU。

用户随后提出按 trace 行数判断是否启用 sidecar。行数方案保留为后续候选，尚未接入 CLI；本次实现原方案的显式最终 HTML 字节阈值，不擅自确定行数/字节默认值。原因是同样行数的 Text、源码池和内嵌数据大小可能不同；字节阈值可以严格保证小于/等于设定大小时不启用 sidecar。包装脚本 single 模式原有 15 万行省略 Text 的规则仍独立存在，auto/sidecar 不自动省略 Text。

状态：第一阶段显式 sidecar 及显式字节阈值自动分流已完成，采用 deferred-full Text 而非虚拟列表。不改变默认输出或已有 single 命令；auto 是可选模式，目录入口固定，未超限报告保留完整离线 HTML。现有 `--no-text-view` 语义不变。

当前产物为 `index.html`、`.funcgraph-sidecar` 和 `data-<32位report-id>/`，后者含带精确字节数/SHA-256 的 manifest、Timeline、可选 Compare、完整 `text.html` 及 `text-index.json`。同级暂存、先发布不可变资源、最后原子替换入口；拒绝非空未托管目录。更新保留旧版本，避免已打开页面读错资源，也意味着需要手工管理磁盘占用。故障时保留旧入口；网络错误可重试，Text 半初始化失败要求重新加载，避免复用旧监听器。完整 Text 字符串及过滤索引在挂载后从 `fgData` 缓存释放，但完整 DOM 不淘汰。

## 问题与目标

用户提出：HTML 达到一定规模后切换到 sidecar，并按需加载。

真实 traceB 有 72176 行、55186 个真实调用，完整报告约 114.7 MB、103.5 万 DOM 元素。首次 Text → Timeline 的诊断中，JSON.parse 约 5ms，样式更新约 115ms，GC 的 DOM embedder tracing 约 174ms。诊断 profile 有额外开销，不能替代无干扰基准，但说明只外置 Timeline JSON 并不能消除巨大 Text DOM 的负担。

CLI 使用 `HtmlGenerator.iter_sections()` / `ReportSpool`：逐行写临时文件、准确统计假想单文件的完整 UTF-8 字节数，再有界提取 sidecar 资源；不改变完整 trace 的解析/符号解析成本。旧的 `generate_html()` 字符串 API 保留。源码池/索引仍内嵌，Timeline/Compare 在显式 sidecar 或 auto 超限时外置。统一过滤已脱离逐行 DOM，使用完整行文本及共享结构属性索引；Text 折叠、键盘导航和跳转仍需先全量挂载，保持完整语义。

目标不是把一个大文件换成几个同时加载的大文件，而是：首屏不传输、不解析、不创建未使用的内容；已访问内容复用缓存；不牺牲完整 trace 语义。

## 后续分块/虚拟化产物提案（尚未实现）

```text
report/
  index.html              轻量 UI 外壳、首屏概要、report ID 和 manifest 地址
  manifest.json           schema 版本、能力、数量、资源索引、大小和校验信息
  timeline.json           第一阶段仍保留完整 Timeline 模型输入
  compare.json            仅进入 Compare 时加载
  text/index.json         行号、调用树、块范围、过滤/定位所需的紧凑元数据
  text/00000.json         按块保存文本行及其结构化属性
  sources/index.json      源码 ID 到资源块的映射
  sources/00000.json      按展开/调用点请求读取的源码内容
```

布局为提案，不代表当前 CLI 已支持这些路径。

| 访问操作 | 加载/渲染边界 |
|---|---|
| 打开报告 | 只读外壳、manifest 和概要；大报告默认 Timeline |
| 首次进入 Timeline | 获取 Timeline 数据并构建模型，不创建全部文本 DOM |
| 切到 Text 或定位某行 | 先读元数据，定位所需块，只挂载可见窗口及少量缓冲行 |
| 展开源码/查看调用点 | 根据 source ID 读取对应源码块，而非解析整份源码池 |
| 进入 Compare | 独立读取 Compare 数据；不为未访问标签加载资源 |
| 再次访问 | 复用完整模型和有上限的块缓存，不重复请求或无限累积 DOM |

第一阶段 Timeline 可以仍完整读取，其后再按测量结果考虑分轨道/时间段存储或可转移 typed arrays。不能直接按任意调用数切开调用树：跨块父子、inline/ghost、off-CPU、未闭合等待、错误链和统一过滤均需要明确的全局身份与边界。

## 输出模式与打开方式

- 保留默认单文件模式，继续支持离线双击和单附件传递。
- 当前已提供 `--report-mode single|sidecar|auto`，默认 single。Auto 必须显式指定 `--sidecar-threshold BYTES`；无默认阈值，0 对非空报告强制 sidecar。sidecar/auto 均需 Timeline 和独立目录，auto 的两种结果固定为该目录的 index.html，便于反复生成及安全切换；显式 sidecar 不代表开启自动分流。
- 自动分流依据是**如果按单文件完整生成**、包含文本、源码池、内嵌数据及资源时的 HTML 未压缩 UTF-8 字节数，不是 sidecar 外壳大小、原始 trace 文件体积、行数、Timeline JSON 大小或预计 DOM 数。行数/DOM 数只作诊断，不单独触发 sidecar。通过计数字节的输出阶段判断，避免只为选模式在内存拼出巨型字符串；保守上界只能用于筛选是否需要精确计数，不能因高估而让未超限报告进入 sidecar。阈值做成可配置项，默认值由实测确定，不能挪用当前分阶段准备的 1 MiB JSON 阈值。
- 本地只读 HTTP 查看器为 `fgview`：只绑定 `127.0.0.1`、默认空闲端口；单文件模式不提供兄弟文件，目录模式需独立报告目录。没有目录列表/写接口，不跟随内部符号链接，无新依赖。导出及 auto 判断由 CLI 完成，不由查看器完成；Text 虚拟化仍待实现。`fgreport --open` 对目录模式在前台运行 fgview，Ctrl+C 结束。
- 页面通过 `file://` 打开时给出明确启动指引，不能假定浏览器允许跨本地文件 fetch。服务器关闭或资源缺失时显示可重试错误，不能永久停在 loading。
- 归档/共享 sidecar 时打包整个目录，不再承诺单个 index.html 独立可用。

## 必须先解耦的接口

1. **数据提供者**：Timeline 输入、源码查找、行元数据、文本块独立于 DOM。单文件实现读取内嵌数据，sidecar 实现按资源读取；分析算法不关心存储格式。
2. **Text 视图**：过滤/折叠/调用树导航不能再枚举已挂载 DOM 当作完整 trace。先在结构化数据/索引计算结果，再映射为可见行；搜索与 CSV 必须覆盖未加载块。
3. **Timeline → Text/源码**：通过真实行号和 source ID 定位，不能要求 `L<n>` 元素或 `data-src` 预先存在。`#L<n>` 锚点应加载正确块并恢复行定位。
4. **生命周期**：统一异步 ready/error/cancel；重复请求共享 Promise，切换取消使用 AbortController/请求代数并丢弃过期结果。完整模型才缓存，块缓存有容量上限。
5. **输出一致性**：manifest 中的 schema、report ID、资源大小/校验防止混用旧数据；临时目录写完整后再发布，失败不破坏原报告，也不留下看似完整的半成品。

Worker 是独立的第二步：仅 fetch JSON 不会把 JSON 解析和模型准备自动移出主线程。可在消除巨大常驻 Text DOM 后重新 profile，决定哪些纯模型阶段值得迁移；不把大量结构化对象往返复制当成免费操作。

## 分阶段落地与验收

前置输出基础、第一阶段显式 sidecar 和可选自动字节阈值已完成：精确 UTF-8 计数、连续区段合并、有界提取、故障保留旧文件，默认单文件不变。早期前端改造的七组 golden 仅替换五个有意变化的前端资源后逐字节相同；本次 auto 不修改前端资源或默认报告，继续使用原有掩码对照新基线。生成性能见[设计文档 §8.3](../design.md#83-分段写出的生成阶段内存2026-11-10)，浏览器 sidecar 对比见 §8.7。虚拟化、默认阈值及模型优化尚未完成。

1. **资源提供者 + 显式 sidecar（已完成）**：外壳按标签读取 Timeline/Compare，Timeline 首屏没有逐行 Text；首次 Text/行号完整挂载。后续过滤索引已独立，Timeline 过滤不再触发 Text 挂载。覆盖校验、取消、重入、失败/重试、初始化失败后的重载、发布中断和实际 fgview CSP，raw 输入和 wheel/sdist 安装态也验证。
2. **Text 按块 + 虚拟化 + 源码按需**：接入完整元数据索引，验证过滤/折叠/键盘/锚点/源码/导出和跨块调用树，不接受“只能分析当前可见行”。
3. **按 HTML 大小自动分流 + 模型优化（部分完成）**：可选 auto + 显式字节阈值已实现，覆盖阈值以下/恰好相等/超过一字节，以及中文等多字节字符的 UTF-8 计数。仅超限自动 sidecar，单次渲染不二次符号化，覆盖模式互换/故障保护/旧资源/权限、配置及 raw 输入。尚无默认阈值；后续需更多实测再决定，另考虑 Worker/二进制列存储及分轨道加载，保留用户显式 single 选择。

性能分别记录：首次状态反馈、完整 Timeline 就绪、最长主线程长任务、窗口内超过 50ms 的阻塞部分之和、首屏传输/资源数量、DOM 上限、切换/跳行和滚动响应。比较同一 trace 的单文件与 sidecar，使用独立 context、交替轮次，并核对完整模型/数据；不能用显示 loading 的时间代替就绪时间。

本次 auto 验收：真实 traceB 完整 HTML 129,591,083 字节、完整 demo 478,970,419 字节均超过显式 64 MiB 示例阈值；同一 371,997 行 demo 显式 no-text 后为 18,370,284 字节，保留 single。完整模型/源码/Text、资源校验及七组默认 golden 均通过；固定 sample 既有掩码下相同。三轮真实浏览器就绪中位数 single 1,751.1ms、auto-sidecar 275.7ms，首次完整 Text 仍需 3,515.2ms（一次观察），过滤命中摘要相同；方法、生成开销及限制见设计文档的 Auto 分流验收。其余虚拟化/分块验收不据此标记完成。

验收至少包含：真实 traceB、371997 行 demo、小报告、只含调度事件、调用顺序模式、深调用树/跨块父子、源码池较大的报告，以及缺文件/失败后重试。单文件报告、Python wheel/sdist 和文档/截图继续回归。

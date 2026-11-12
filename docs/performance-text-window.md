# 实验性 Text DOM 窗口（2026-11-13）

这是可以实际滚动、跳行和筛选的窗口 renderer，不再只是离文档取块接口。
**默认行为不变**：单文件和默认 sidecar Text 仍完整挂载；只有新生成的分块 sidecar
且用户显式选择时启用。没有新增默认大小阈值，也没有改动发布版本号。

## 使用 / Quick start

```bash
funcgraph TRACE --no-config --timeline --enable-fold --filter \
  --report-mode sidecar --output report-directory
fgview report-directory
```

在查看器输出的完整 HTTP URL 后加 `?text=windowed`，再进入 Text。
有其他查询参数时使用 `&text=windowed`；锚点仍位于最后，例如
`index.html?text=windowed#L50000`。不要丢掉 `fgview` 的随机路径前缀。
也可在默认 Text 工具区点击 **Windowed Text**；该按钮会以实验 URL 重载报告。

Only newly generated **chunked** sidecar reports support the opt-in. Append
`?text=windowed` to the HTTP report URL before opening Text, or use **Windowed Text**
in the Text tools. This reloads the report; it does not change the CLI default or
add an automatic threshold. Small sidecars and offline single-file reports keep
their complete renderer. Older reports need regeneration for the v4 presentation index.

- 最多挂载 **160 个展示单元**，其中包含 trace 行或任务分组标题；trace 行数不会超限。
  每条所需行的源码占位一并挂载，离屏行不保留 DOM 缓存。
- 完整记录参与过滤、调用树/深度折叠、折叠摘要、time/task/duration 排列和键盘导航。
  原始行号可以稀疏；网络资源仍按原始 ordinal 定位，不按当前排列位置猜测。
- 滚动高度通过数值索引估算，再由已挂载元素的实测高度修正；展开源码、窄/宽屏切换
  会更新尺寸。估算不是整篇文档的精确排版；首次到访不同高度区域仍可能修正滚动条。
- 点击源码、hash、Timeline → Text 和键盘跳行可以进入未挂载区域。取消或后到的旧请求
  不得覆盖新目标；离开 Text 取消窗口/完整回退的读取，重新进入可继续浏览。
- 源码展开状态随稳定行保留；行淘汰时取消它的未完成源码请求，回来时按需恢复。
  过滤临时关闭源码面板，不抹掉原有展开状态。
- 块失败显示重试；导航失败保留原窗口，筛选失败回滚到上一个完整筛选结果。

## 整篇文档操作 / Whole-document actions

**Copy** 从完整可见行排列生成原始 trace 文本，涵盖所有未被筛选/折叠隐藏的行，
不是只复制这 160 行；不附带源码正文或 UI 生成的折叠摘要。

**Full Text** 在成功准备全部行之后替换窗口，保留筛选、折叠、排列和源码状态。
失败保留窗口，可重试；该回退自然会失去 DOM 上限。
**Expand All / Collapse All** 和 **Ctrl/⌘A、Ctrl/⌘F、Ctrl/⌘P** 也先使用完整回退，
不把当前窗口当成整篇文档。全选在准备完成后执行，打印随后打开浏览器打印界面；
原生查找需要在提示 Full Text ready 后**再按一次快捷键**。
直接使用浏览器菜单打印窗口模式时输出明确提示，避免静默打印不完整的 trace。

普通鼠标/触摸文本选择固定当前窗口，直到清除选择；跨窗口连续原生选择尚未提供。
如需整篇选择、浏览器查找/打印，或希望不受窗口淘汰影响，请先点 **Full Text**。
Full Text remains the correctness fallback for whole-document browser operations.
The experimental renderer does not claim native selection or find across unmounted DOM.

## 数据与边界

- sidecar presentation index **v4**：原有 14 个过滤字段之后依次追加 `space`、
  `sort-duration`、`fold-ret`、`call-depth`，最后仍为实际 source ID/null。
  源码 0、零返回值、原始返回表示、精确排序耗时及空值语义均保留；旧 v1/v2/v3 可读。
  本次真实 trace 的索引从 34,646,118 增至 36,233,756 字节（增加 1,587,638 字节），
  这是保留展示语义的交付成本；18 个 Text HTML 资源逐字节不变。
- HTML 仍按最多 **4,096 条记录**校验下载，每次解析目标 **512 条** paint block；
  不宣称 HTTP Range 按 160 行交付。四个任务以内并发读取所需 paint block，排序后一个
  窗口可能涉及多个资源。暂时的解析 DOM 与已挂载 DOM 是不同预算。
- 按需解析会核对每行 ID、内容、完整属性及 source 配对。完整 renderer 仍保留原先
  DOM/模型行数覆盖校验；实验 renderer 另校验完整模型/catalog/navigation 一致。
- 完整元数据、折叠/导航/高度索引和 Timeline **仍为全量**；源码正文大小也不由行数
  限制。已有 HTML 与 source LRU 继续独立计数，不能当作整进程或峰值内存上限。
- 当前只在 Chromium 实测；没有据此宣称其他浏览器或任意百万行 trace 已完成验收。
  大模型解析、重排、GC 仍可能产生长任务；以后还需协作拆分、模型压缩与更多 trace 验收。
- task 分组时，原始标题仍随首个可见记录进入窗口；滚动越过标题后，以一条固定提示
  延续当前视口的任务上下文，原始标题被淘汰后仍有效。提示由完整可见分组边界定位，
  不依赖选中行或仅当前 DOM；沿用现有标题配色，不新增 trace 行或扩大 160 行预算。
  它位于横向滚动容器之外，窄屏单行省略、完整内容保留于 title；行数始终描述完整组。
  时间/耗时排序、无结果或离开 Text 时隐藏，Full Text 清理，Copy/打印不包含提示。

## 真实 trace 三轮对照

输入 `/tmp/pi4/traceB.txt`：10,453,189 字节，72,176 原始 Text 行、55,186 调用、
55,213 帧、12 条轨道。输入 SHA-256：

```text
5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62
```

同一个新生成 sidecar，通过真实 `fgview` HTTP 服务，Chromium **145.0.7632.6**、
1500×900；先打开 Timeline，再测冷 Text。六个独立浏览器，三轮 complete/windowed
交替顺序；计时及内存取样期间不并行运行测试/打包。不是与旧版本或不同数据集混比。

| 三轮中位数 | 默认完整 Text | 实验窗口 Text |
| --- | ---: | ---: |
| Text API 就绪 | 2,359.4 ms | 798.4 ms |
| 再等待两次 rAF 的反馈代理 | 2,392.4 ms | 811.9 ms |
| 就绪窗口内最长主线程任务 | 165 ms | 92 ms |
| 已挂载 trace 行 | 72,176 | 160 |
| 整页元素 | 792,534 | 2,369 |
| 完整模型记录 | 72,176 | 72,176 |
| 首次 Text 资源数 | 18 HTML 块 | 1 索引 + 1 HTML 块 |
| renderer VmRSS 合计取样 | 751.4 MiB | 365.4 MiB |
| JSHeapUsedSize 取样 | 33.4 MiB | 75.4 MiB |

就绪中位数约减少 **66%**；RSS 只是已知进程取样，不是峰值或严格上限。窗口模式的
JS heap 取样**反而更高**，因为完整 JSON 文本/元数据及数值索引仍常驻，不能将 DOM
减少等同于所有内存类别都减少，也不能从这组取样推断内存泄漏或确定的回收时机。
三次就绪原始值分别为 complete 2,359.4/2,526.6/2,335.1 ms，windowed
798.7/784.7/798.4 ms；保留所有轮次，没有用最佳值代替中位数。

计时后另外跳往原始中/末/首行；窗口模式中/末冷跳行中位数为 90.8/73.3 ms，
完整模式为 50.6/51.2 ms。按需下载需要付出代价，**不是所有操作都更快**。
六次完整模型的标准化摘要均为：

```text
bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6
```

摘要逐项包含全部原始行号、文本、14 个过滤属性及 source ID，不只比较数量或首屏。
v4 的新增展示属性另外在导出/按需读取测试中核对。

复现：

```bash
funcgraph /tmp/pi4/traceB.txt --no-config --timeline --enable-fold --filter \
  --func-links --report-mode sidecar --output /tmp/text-window-report
PYTHONPATH=.:tests python3 tests/benchmark_textwindow.py /tmp/text-window-report \
  --trace /tmp/pi4/traceB.txt --rounds 3 --output /tmp/text-window.json
PYTHONPATH=tests python3 -m unittest -v test_textwindow test_textindex test_textmodel test_textchunks
```

本机原始 JSON、生成/测试/打包日志和资源校验记录归档在
`build/benchmarks/20261113-text-task-context/`；初次窗口化验收保留在
`build/benchmarks/20261113-text-window/`。两者不纳入安装包，也不自动上传。
样例 HTML 同步更新前端；固定小样例仍使用默认完整渲染，不伪装成大 trace 窗口样例。

# Text 窗口的重排与重建 / Window rebuilds（2026-11-13）

This optimizes complete-model work in **opt-in windowed Text**, not only the
visible subset. Defaults, sidecar thresholds, the 160-unit mounted budget and
whole-document fallback remain unchanged. Regenerate reports to use the new
runtime; see [windowed usage and limitations](performance-text-window.md).

后续优化复用 task/duration ordinal，合并高度构建，并按次缓存折叠可见性。
新的无折叠、稀疏/密集折叠基准及额外内存成本见[重建热点优化](performance-text-geometry.md)；
下文保留原阶段的基线与测量结果，不将不同阶段的数字混比。
无筛选折叠的进一步范围复用见[折叠范围优化](performance-text-fold-visibility.md)。

## 实现与准确性

- 原始行号连续时，先用首行号与目标行号之差探测对应 ordinal，**核对真实 ID 后**
  才返回。不新增完整行号 Map；稀疏 ID 未命中继续二分，不将行号误当作数组下标。
- 完整导航顺序逐项相同时，复用位置 Map、计数树及仍有效的快照；待处理可见性
  更新仍刷新。反转、重复、缺失、空洞或外部改动的排列不绕过原有验证/重建。
- 窗口只保留一份完整耗时排列缓存，以完整 time/task plan 身份与升降序为键。
  筛选或折叠没有改变这个键时复用；方向/模式变化重算，退出耗时排序或销毁时释放。
  相同耗时保留既有稳定排序顺序，过滤/折叠仍遍历完整记录。
- 显式重建绘制期间，滚动/ResizeObserver 的视口反馈不另起绘制抢占该重建。
  完成或失败后重新调度视口检查；新的显式操作与离开 Text 仍可取消旧请求。

完整 paint block 的行号、原文、展示属性及源码配对校验没有删减；没有选择性跳过
未显示行的校验，也没有离屏 DOM 缓存。网络仍按 4,096 行交付，按需 DOM 解析仍以
512 行为单位。缓存新增一份 O(N) 数值排列，**不是**总内存或每帧时间上限。

## 三轮真实 trace 对照

基线运行时 `e9baea408899`，同一输入/参数生成新旧 sidecar，通过真实 `fgview` HTTP
访问。Chromium **145.0.7632.6**，1440×900，显式 `?text=windowed`，每轮独立浏览器。
顺序 before/after、after/before、before/after；没有 CPU Profiler，也没有并行测试或构建。
两组输入均未提供 vmlinux/DWARF，源码展开为零，不外推为源码丰富报告的性能结论。

每轮依次执行原顺序 rebuild、首次 task 分组、重复 task rebuild、首次/重复降序耗时
排序、`func:mutex` 筛选、Clear。表中是三轮中位数，单位 ms；“同步”测 API 返回
Promise 前的阻塞，“就绪”包含下载、校验、DOM 解析与挂载，不把两者混用。

| 72,176 行操作 | 同步：旧 → 新 | 就绪：旧 → 新 |
| --- | ---: | ---: |
| 原顺序 rebuild | 76.0 → 45.7 | 86.1 → 55.9 |
| 首次 task | 116.5 → 93.4 | 147.5 → 123.0 |
| 重复 task | 84.5 → 57.9 | 91.8 → 65.5 |
| 首次 duration | 148.7 → 103.1 | 732.9 → 632.6 |
| 重复 duration | 153.5 → 87.0 | 1,650.6 → 1,565.4 |
| 筛选 | 8.9 → 8.8 | 528.3 → 516.9 |
| Clear | 4.0 → 3.9 | 124.4 → 98.3 |

| 371,997 行操作 | 同步：旧 → 新 | 就绪：旧 → 新 |
| --- | ---: | ---: |
| 原顺序 rebuild | 309.4 → 184.1 | 317.1 → 191.9 |
| 首次 task | 495.1 → 373.7 | 513.5 → 397.9 |
| 重复 task | 352.1 → 218.6 | 360.4 → 226.7 |
| 首次 duration | 817.5 → 578.9 | 1,618.4 → 1,382.7 |
| 重复 duration | 854.4 → 439.1 | 2,998.0 → 2,591.5 |
| 筛选 | 8.9 → 8.9 | 755.2 → 703.7 |
| Clear | 8.3 → 8.3 | 472.2 → 308.6 |

37 万行重复耗时排序的同步阻塞减少约 **49%**，但完整就绪仍需约 **2.6 秒**：
排序后窗口散布到多个 paint/network 块，资源读取、完整 DOM 解析及 GC 仍昂贵。
“重复”指重复排序请求，不保证目标窗口/HTML 资源仍在缓存中，因此可能比首次更慢。
后续已针对同一窗口重复读取网络块进行[资源分组优化](performance-text-block-locality.md)；
这里保留本轮重建优化的原始数字，不将不同阶段的测量混合。
这不是“卡顿已解决”，首次 task/排序和全量重建也仍会阻塞主线程。
原始 JSON 同时保留两次 rAF 反馈代理和 Long Task 观测；后者可能漏掉 observer
注册前已开始的当前任务，零值不代表无阻塞，不用它推导帧率或严格长任务上限。

Inputs / complete model SHA-256（所有 6 次浏览器结果一致）：

| Trace | 原文件字节 | 原文件 SHA-256 | 完整模型 SHA-256 |
| --- | ---: | --- | --- |
| traceB.txt | 10,453,189 | `5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62` | `bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6` |
| demo.txt | 53,711,530 | `b2375370c5bafba8de3bea2cf6bd8fff67e2435a71be2505a35cc9be72411ae7` | `ec9496babc841d7b3620ff4439d35ea0e49d517fc52f8b2b480ac4f214d3616d` |

完整模型摘要含所有原始行号、原文、14 个过滤属性及 source ID，不只比较首屏。
每步另对完整可见行号、完整导航排列及可见总数做 SHA-256，新旧各轮逐项一致；
挂载行始终不超过 160，没有浏览器错误。完整展示属性与源码配对另由回归验证。

## 复现 / Reproduce

分别在基线和更新后的 checkout 使用相同输入和参数生成不同目录，不覆盖旧报告：

```bash
python3 funcgraph.py TRACE --no-config --timeline --enable-fold --filter \
  --func-links --report-mode sidecar --output REPORT_DIRECTORY
PYTHONPATH=.:tests python3 tests/benchmark_text_rebuild.py BEFORE_DIR AFTER_DIR \
  --trace TRACE --rounds 3 --output rebuild-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_textmodel test_textwindow test_textchunks
```

基准拒绝提前取消/失败的 API 结果、尚未结束的挂载、超过预算的 DOM 和完整结果差异，
不以吞掉 AbortError 后的快速返回充当性能提升。回归覆盖非 1 起点、稀疏/大行号、
导航分配计数及待刷新可见性、排序缓存/方向/模式/折叠，还有延迟读取中的真实滚动
反馈，确保它不能取消显式 rebuild；已有离开/取消/重入测试继续有效。

本机未提交的原始报告、三轮 JSON、诊断（非正式性能对照）、测试/打包与摘要记录
位于 `build/benchmarks/20261113-text-interaction/`，保留失败诊断，不自动上传。

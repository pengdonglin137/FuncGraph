# Text 按行取块与可取消定位（2026-11-12）

本次交付是窗口化的前置能力，**不是完整 Text 窗口化**。默认 Text 仍在首次进入或跳行时
准备全部记录并完整挂载；过滤、折叠、排序、打印和原生复制的范围没有缩小。新增能力不
改变 CLI、sidecar schema、网络块大小或默认输出模式，也没有引入新的自动阈值。

## 按行取块

- `fgTextModel.locate(model, lineNumber)` 在完整原始记录中二分定位，返回原始 ordinal
  和原记录引用。行号可以不连续，任务排列不改变原始 ordinal；不存在的行返回 `null`，
  非正安全整数被拒绝。不创建第二份全量行号索引，兼容索引 v1/v2/v3。
- `fgData.loadTextBlock(ordinal, {signal})` 复用已校验网络资源，只提取目标 paint block
  的 HTML 和原始起点/行数。每个网络块最多 4,096 条，paint block 最多 512 条，末块
  可以不足 512 条。它检查 paint 边界、封闭尾部和块数；错误结果不会留在资源缓存。
  网络资源仍完整下载和 SHA-256 校验，**并非按 512 行发送 HTTP Range 请求**。
- `fgLoadTextBlock(model, lineNumber, {signal})` 校验完整 catalog/模型行数，只为目标
  paint block 创建离文档 DOM，再逐行检查稳定 ID、行数和 v2/v3 的源码占位配对。
  返回 `{first, count, element, line}`，不挂载、不填充源码、不保存 detached DOM 缓存。
  v1 的源码能力仍未知，不凭空推断源码 ID；不存在的记录不读取 HTML。
- 取消沿用资源订阅机制；同一网络块上的其他订阅者不受影响。解析前再次检查取消，避免
  已取消消费者创建节点。缓存仍是独立的最多 4 个网络块、16 MiB 已校验 UTF-8 字节 LRU；
  这不限制调用者保留的返回值，也不限制完整模型或整个浏览器进程的内存。

## 定位契约

`fgEnsureTextLine(lineNumber, {signal})` 为导航统一提供真实行元素：现有已挂载报告保持
同步返回；deferred Text 等待现有完整初始化；将来的 renderer 可以在其 view API 提供
`ensureLine(lineNumber, {signal})`，按需挂载正确行后返回元素或 Promise。服务验证行 ID、
行号属性和所属 Text 根，不能用另一个行或离文档副本冒充目标。

键盘、`#L<n>` 和 Timeline → Text 使用同一 `fgRequestTextLine` 请求代数。新定位、离开
Text 或 Escape 会取消旧定位；即使旧 Promise 忽略取消或迟到完成，也不能覆盖新选择、
聚焦或触发旧滚动/闪烁。连续键盘下一行从最新待定位行继续，而不是反复请求上一已挂载行。
加载失败保留原选择，允许重试；取消导航订阅本身不终止其他消费者共享的完整 Text 初始化。
首次完整初始化不会用默认首行或残留 hash 抢占已经发出的显式跳行；没有 Timeline/Compare
引导脚本的纯 Text 报告也保留同步定位服务。
renderer 仍须在提交 DOM 前检查 signal，并负责自己的节点清理、源码加载取消与高度占位。

浏览器测试包含一个最小 renderer 验证该契约：完整模型 4,203 条，定位第 4,101 行时仅挂载
目标末块 107 条，没有读取首个 Text 网络块。**这个测试 renderer 不是默认产品 UI**；
不能据此声称筛选、折叠、重排、原生查找、打印或跨窗口选择已经支持稀疏 DOM。

## 真实 trace 观察

输入为 `/tmp/pi4/traceB.txt`，72,176 条 Text 原始记录，55,186 calls、55,213 frames、
12 tracks。输入 SHA-256：

```text
5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62
```

Chromium 145.0.7632.6，1500×900，经真实 `fgview` 回环 HTTP；三个独立浏览器，每轮先
打开 Timeline，加载完整 Text 索引/模型，再顺序读取首行、中间行和末行。测量期间没有
并行构建或测试。下表是 **reader 的下载/验证/提取/解析耗时**，不是生产 Text 打开耗时；
完整索引加载/模型准备另计，中位数为 416.4 ms。

| 目标行 | 目标 paint 起始 ordinal | 离文档行数 | 后代元素数 | 三轮耗时中位数 |
| --- | --- | --- | --- | --- |
| 1 | 0 | 512 | 5,401 | 55.1 ms |
| 36,089 | 35,840 | 512 | 5,603 | 67.4 ms |
| 72,176 | 71,680 | 496 | 5,421 | 46.0 ms |

每轮仅请求 `text-0.html`、`text-32768.html`、`text-69632.html` 三个 Text HTML 网络块，
另有完整索引和 Timeline 资源；其余 Text 块没有下载。读取后 `#content` 始终 0 行，仍为
deferred 状态；每次返回块的所有行号都与完整模型对应区间一致，无 pageerror/CSP 违规。
三个完整模型摘要相同：

```text
bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6
```

另以 `b721e5bf6fae` runtime 为基线，进行三对交替、独立浏览器的**默认完整 Text**
对照。完整就绪中位数为 2,471.1 → 2,403.3 ms（-2.7%），最长主线程任务为 169 → 188 ms
（新版本其中一轮为 231 ms），解析后两帧反馈为 82.9 → 84.9 ms。完整 Timeline/Text
模型摘要相同；18 个原始 Text
HTML 块与完整索引逐字节相同，每份 20 个资源的长度/SHA-256 校验通过。Timeline 仅
`stats.build_time` 不同。这里只记录观测，**没有证明默认 Text 打开变快**；reader 的
55–67 ms 不能替代完整就绪耗时。公开两个样例仅替换五份有意变更的 JS 及既有诊断字段，
内容归一化后完全相同；重新生成的三张样例 PNG 与此前逐字节相同。

复现：

```bash
python3 funcgraph.py /tmp/pi4/traceB.txt --no-config --no-btf \
  --timeline --enable-fold --filter --report-mode sidecar --output /tmp/fg-row-reader
python3 tests/benchmark_text_rows.py /tmp/fg-row-reader \
  --rounds 3 --output /tmp/fg-row-reader-results.json
PYTHONPATH=tests python3 -m unittest -v \
  test_textmodel test_data_provider test_textchunks test_browser test_sidecar
```

`benchmark_text_rows.py` 需要项目现有的 Playwright/Chromium 测试环境。它只对新生成、
有 `fgTextChunks` 的 sidecar 测量，不向旧报告注入新 runtime。原始 JSON、生成日志和
完整报告保存在本地 `build/benchmarks/20261112-text-rows/`，不计入发行包。

## 后续边界

真正窗口化还需要：完整过滤/折叠结果向当前窗口投影、time/task/耗时排列下的区间映射、
有上限的已挂载行与节点生命周期、源码展开后的高度与横向范围、连续滚动、跨窗口原生
选择/复制/查找/打印的明确处理。这些能力不能通过删除完整性校验、只分析当前窗口或
把所有 detached 节点缓存下来替代。本次保留默认完整挂载，先确保未挂载行的取块与定位
契约可被独立验证，并修复当前完整挂载路径中迟到定位覆盖新操作的问题。

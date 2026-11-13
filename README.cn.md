# FuncGraph

**从用户函数到内核调用，看清执行过程和时间去向。**

将 ftrace、uftrace 记录变成交互式报告：沿调用链下探，定位耗时，查看参数、
返回值和对应源码。纯内核态、纯用户态、用户态与内核态混合记录，使用同一套阅读方式。
默认生成一个可离线打开的 HTML，无需部署服务。

[English](README.md) · [简体中文](README.cn.md) ·
[在线演示](https://pengdonglin137.github.io/FuncGraph/) ·
[使用指南](docs/guide.cn.md) ·
[下载安装包](https://github.com/pengdonglin137/FuncGraph/releases)

[![CI](https://github.com/pengdonglin137/FuncGraph/actions/workflows/ci.yml/badge.svg)](https://github.com/pengdonglin137/FuncGraph/actions/workflows/ci.yml)

[![用户态与内核态混合 Timeline，展示记录的参数与源码](sample_mixed.png)](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html)

**先试试[混合报告](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html)：**
沿 `main → run_round → exchange_loopback → send_frame → dispatch_datagram → sendto`
进入 `__x64_sys_sendto`。选中 `dispatch_datagram`，查看具名参数、返回值和调用点；
分界线标出已匹配的用户态／内核态边界。
也可以打开[纯内核报告](https://pengdonglin137.github.io/FuncGraph/sample.html)，
或查看其[源码与错误路径截图](sample.png)。无需安装即可体验。

## 你能用它解决什么问题？

| 想弄清楚什么 | 从哪里看 |
| --- | --- |
| 执行了哪些函数，调用顺序是什么？ | **Timeline** 的任务与 CPU 轨道、嵌套 slice、已匹配的用户态／内核态边界，以及记录到的 exec/exit 事件。 |
| 时间花在哪里？ | Total/Self 耗时配色、函数统计、慢调用、off-CPU 区间、唤醒路径与等待链。Self time 不等同于纯 CPU 执行时间。 |
| 这个调用为什么失败？ | 记录的参数与返回值、结合 BTF 的内核类型解码、errno 传播，以及调用点和源码详情。 |
| 是否存在调度延迟或长临界区？ | 上下文 flags、关中断／关抢占区间与调度延迟；需要 trace 中实际记录了对应字段或事件。 |
| 如何逐行阅读、比较和复用数据？ | **Text** 的折叠、过滤、源码展开与键盘导航；**Compare** 的函数级差异；面向脚本的 JSON/CSV 导出。 |

Timeline 支持全屏、可调整大小的详情区、紧凑菜单和明暗主题。
Text 与 Timeline 之间保留可达的标签页和 trace 行跳转入口。
通过 **Tools → Saved views…** 为分析位置命名、添加备注，或导出给使用同一份报告的人恢复。
选中调用后，**Actions → Focus this task / Focus this function** 可生成精确筛选草稿，确认后应用，并支持撤销本次筛选。
缺少事件或调试信息时，不补造调用点、参数和时间数据。

**本页导航：**[安装](#安装) · [快速开始](#快速开始) ·
[采集](#采集用户态与内核态) · [源码与类型](#源码与类型信息) ·
[大型 trace](#大型-trace) · [文档](#文档导航)

## 安装

报告生成需要 Python **3.8+**。读取普通文本或已导出的 FuncGraph uftrace JSONL，
不需要采集器、root 权限或 `vmlinux`。

从 [GitHub Releases](https://github.com/pengdonglin137/FuncGraph/releases)
下载安装包，再安装本地文件。以下适用于在 x86_64 Linux 上采集：

```bash
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fguftrace --version
```

将 `<version>` 替换为下载文件的版本号；ARM64 选择 `linux_aarch64`。
两种平台 wheel 均包含**打过补丁的 uftrace 和 libmcount 采集库**，
`fgcapture`、`funcgraph`、`fgreport` 会自动选择内置采集器，
`--uftrace-bin PATH` 只是覆盖选项，不是必传参数。

`py3-none-any` wheel 与源码发行包不包含编译好的 uftrace。
原生 wheel 基于 Ubuntu 22.04 构建，并非 manylinux，仍依赖兼容的 glibc 和系统库；
`[all]` 安装 pyelftools、Pygments，不会安装系统动态库。
详见[安装包选择与依赖](docs/capture.cn.md#选择安装包与采集模式)。
当前发布流程不向 PyPI 上传。

开发报告功能或重放仓库样例时，克隆项目并在根目录执行：

```bash
git clone https://github.com/pengdonglin137/FuncGraph.git
cd FuncGraph
python3 -m pip install '.[all]'
```

源码安装**不会**编译 uftrace。新采集用户态／混合记录时，请使用原生 wheel，
或按[固定版本构建步骤](docs/uftrace.cn.md)准备采集器。
文本和已保存 JSONL 的重放不需要初始化 submodule。

## 快速开始

### 打开已有 trace

```bash
funcgraph --no-config trace.txt --timeline --enable-fold --filter --output report.html
```

直接用浏览器打开 `report.html`。先在 Timeline 选中调用，再通过 trace 行链接进入 Text。
`--no-config` 避免首次使用受个人内核预设干扰；源码解析不是查看调用结构的前提。

| 输入 | 用法 | 额外要求 |
| --- | --- | --- |
| ftrace `function_graph` 文本 | 直接传 `trace.txt`，也支持 `.gz`、`.xz`、`.bz2`、stdin 和多文件输入。 | 仅查看调用结构无额外依赖。 |
| trace-cmd 二进制容器 | 直接传 `trace.dat`，自动识别格式。 | 可选原生解码库与主机 C 编译器，见 [raw 输入配置](docs/raw-input.cn.md)；裸 `trace_pipe_raw` 页不是独立输入文件。 |
| uftrace 采集目录 | 使用 `--uftrace capture.data`，不要当作普通文件传入。 | FuncGraph 补丁版 uftrace；安装包内置时自动选用。 |
| FuncGraph uftrace JSONL 导出 | 直接传 `capture.jsonl`。 | 无需本机 uftrace；并非任意上游 JSON 格式。 |

### 重放内置样例

安装后，在仓库根目录执行：

```bash
mkdir -p build/examples
funcgraph --no-config samples/sample_io.txt --timeline --enable-fold --filter \
    --output build/examples/kernel.html
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --output build/examples/mixed.html
```

不需要 root、QEMU、uftrace 或匹配的内核镜像，就能查看调用结构和记录值。
没有匹配的调试文件时，这些命令不会重建公开 HTML 中的内核源码与 BTF 增强信息。
安装 wheel 不会自动在工作目录放置 `samples/`。

## 采集用户态与内核态

统一入口，三种模式：

| 模式 | 采集内容 | 环境要求 |
| --- | --- | --- |
| `fgcapture --kernel` | 命令执行期间的内核函数图，无需重新编译应用。 | root、Linux tracefs、Bash；无需 uftrace。 |
| `fgcapture --user` | 插桩的应用／库函数、参数、返回值与调用点。 | 补丁版 uftrace 和适合的编译／调试信息；无需 root 或 tracefs。 |
| `fgcapture --mixed` | 同次运行中的用户态／内核态调用，以及可用的调度和 exec/exit 事件。 | 用户态采集条件、root、空闲的专用 tracefs。 |

只看现有程序的内核执行路径：

```bash
sudo "$(command -v fgcapture)" --kernel --sched -o kernel.txt -- cat /etc/hostname
fgreport -P kernel.txt
```

想看完整的用户态调用层次，可以使用多层文件复制／UDP 示例。
从仓库根目录执行，以下编译选项适用于 **x86_64**；
**ARM64** 请将 `-pg` 改为 `-fpatchable-function-entry=2`。

```bash
cc -pg -g -O0 -fno-omit-frame-pointer -o demo_mixed samples/demo_mixed.c
printf 'FuncGraph demo\n' > demo-input.txt
: > demo-output.txt
fgcapture --user -o user.data -- ./demo_mixed demo-input.txt demo-output.txt
funcgraph --no-config --uftrace user.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output user.html
```

在**空闲的实验机**上，用混合模式采集同一个程序：

```bash
: > demo-output.txt
sudo "$(command -v fgcapture)" --mixed -o mixed.data -- ./demo_mixed demo-input.txt demo-output.txt
fgreport -P --uftrace mixed.data -- --user-src "$PWD"
```

每次使用新的采集目录。用户态／混合模式默认请求参数、返回值和调用点，实际覆盖取决于
插桩和记录元数据；纯内核模式先探测参数支持，再决定是否启用。
混合模式会检查、恢复 tracefs 设置，失败时也会恢复；默认范围是系统调用根函数、
**八层内核调用**，并非整个内核的无限深度追踪，也不能代替生产环境的追踪隔离机制。

[完整采集案例](docs/capture.cn.md)提供范围控制、调度、exec/exit 检查与排错步骤。
需要真实业务工具及依赖库的案例，可看 [pigz + zlib](docs/pigz.cn.md)：
从编译选项、短时工作负载，到采集、两套源码配置与产物核对。

## 源码与类型信息

**用户态与内核态源码分别配置。**以下混合报告示例中的路径、版本必须对应
**采集时实际运行的内核**，不是生成报告的主机内核：

```bash
VMLINUX=/path/to/matching/vmlinux
KERNEL_SRC=/path/to/linux
KERNEL_REV=recorded-kernel-commit
KERNEL_WEB_REV=matching-upstream-tag-or-commit
funcgraph --no-config --uftrace mixed.data --timeline --enable-fold --filter \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --func-links --highlight-code --fast \
    --source-browser github --source-browser-rev "$KERNEL_WEB_REV" --output mixed.html
```

- **用户源码：**`--user-src` 指定允许嵌入的本地源码根目录，按记录路径选择：
  上面的新采集使用 `"$PWD"`，公开 JSONL 样例使用 `samples`。
  用户路径不会套用内核源码浏览器链接。
- **内核源码：**匹配的 `vmlinux`、源码树和工具链用于解析调用点及内联栈。
  `--source-rev` 控制嵌入的内核源码，`--source-browser-rev` 独立控制网页链接。
  默认使用 GitHub 的 `torvalds/linux`，也可通过 `--source-browser elixir` 切换。
  请固定匹配的标签／commit，不要依赖会变化的 `master`／`latest`。
- **参数与返回值：**可用的内核 BTF 用于修正已知参数个数、位宽及返回值解释；
  用户参数名和类型来自可用的已记录 DWARF／规格信息，而不是内核 BTF。
  原始证据、未知信息和不支持的情况保持可区分。

经常使用同一个内核时，保存为预设：

```bash
fgreport init target --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_WEB_REV" --default
fgreport trace.txt
fgreport --uftrace mixed.data -- --user-src "$PWD"
```

模块、交叉工具链、路径映射和自定义浏览器等配置，见[使用指南](docs/guide.cn.md)。

## 大型 trace

**默认仍是单 HTML。**内嵌 Text 达到 4,096 行后，在支持的浏览器中自动跳过屏外渲染，
减轻 Timeline → Text 反复切换的开销，不丢弃行、源码、原生选区或横向阅读空间。
首次准备仍可能耗时，详见[实测与边界](docs/performance-view-roundtrip.md)。

需要先打开 Timeline、之后再加载 Text 时，显式选择 **sidecar 目录**：

```bash
funcgraph --no-config trace.txt --timeline --enable-fold --filter \
    --report-mode sidecar --output trace.report
fgview trace.report --open
```

- sidecar 按需加载 Text 与源码资源，Compare 单独加载。请使用 `fgview` 打开，
  分享时保留**整个目录**，不能只发送 `index.html`。
- 若只想超过指定大小才切换，使用 `--report-mode auto --sidecar-threshold 67108864`，
  输出指定为目录。这里的 **64 MiB 指完整 HTML 大小**，不是内置默认值或 trace 行数阈值。
- 默认 Text 仍准备并保留完整 DOM／模型。新生成的分块 sidecar 可手动启用实验性
  **Windowed Text**，限制挂载的行数，**不代表限制整个报告内存**。
  详见[窗口化模式说明](docs/performance-text-window.md)。
- `fgreport` 在单文件模式下，对超过 150k 行的输入选择仅 Timeline，`--text` 可保留 Text；
  目录模式保留 Text。也可以显式使用 `--no-text-view --keep-source` 生成保留源码的纯 Timeline 报告。

服务启动、自动模式和资源加载行为见[本地查看器](docs/viewer.cn.md)。
`fgview` 只监听 loopback；请服务专用报告目录，不要把整个源码／工作目录作为根目录。

## 文档导航

| 目标 | 从这里开始 |
| --- | --- |
| 了解控件、过滤、配色和分析面板 | [使用指南](docs/guide.cn.md) |
| 重放、预设、对比与 JSON/CSV 导出 | [场景用例](docs/examples.cn.md) |
| 采集纯内核、纯用户或混合执行流 | [完整采集案例](docs/capture.cn.md) |
| 编译并跟踪真实工具和依赖库 | [pigz + zlib](docs/pigz.cn.md) |
| 安装／构建 uftrace，了解采集边界 | [uftrace 集成](docs/uftrace.cn.md) |
| 读取 trace-cmd 二进制输入 | [raw 输入](docs/raw-input.cn.md) |
| 打开大报告，选择交付方式 | [本地查看器](docs/viewer.cn.md) |
| 理解实现或维护发布流程 | [设计文档](docs/design.md) · [发布操作清单与验收记录](docs/releasing.cn.md) |

## 开发与验证

```bash
tests/run.sh
```

测试覆盖解析、分析、命令行流程和浏览器交互。安装 Playwright／Chromium、Node 后
可执行浏览器和 JavaScript 检查；可选原生与真实内核测试还需对应依赖。
请按[发布验证说明](docs/releasing.cn.md)准备环境，不要将跳过的可选测试当作已覆盖。
第三方源码固定到 commit，uftrace 的本地修改通过可校验的补丁序列维护：
[依赖版本锁](third_party/README.md) · [uftrace 补丁](patches/uftrace/README.md)。

背景文章：[ftrace 可视化工具](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q) ·
[续篇](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw) ·
[写了个 ftrace 可视化工具](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)。

# FuncGraph 使用指南

本文介绍从采集 trace 到阅读报告的完整流程，并逐一说明报告中每个视图的字段和配色。命令行参数的完整列表见 `python3 funcgraph.py --help`，实现细节见 [design.md](design.md)。

## 目录

1. [概述](#1-概述)
2. [安装](#2-安装)
3. [采集 trace](#3-采集-trace)
4. [生成报告](#4-生成报告)
5. [文本视图](#5-文本视图)
6. [时间轴视图](#6-时间轴视图)
7. [分析面板](#7-分析面板)
8. [对比视图](#8-对比视图)
9. [导出与自动化](#9-导出与自动化)
10. [常见问题](#10-常见问题)

---

## 1. 概述

FuncGraph 读取内核 `function_graph` tracer 的文本输出，生成一个可以离线打开的 HTML 报告。报告包含以下标签页：

| 标签页 | 内容 | 需要 |
|---|---|---|
| **Text** | 原始 trace 逐行显示，加上源码定位、折叠、过滤和耗时热力图 | 默认生成 |
| **Timeline** | 每个任务一条轨道的时序火焰图，横轴是时间，纵轴是调用深度；附带错误、临界区、调度延迟、唤醒等分析 | `--timeline` |
| **Compare** | 和另一份 trace 按函数比较调用次数和耗时 | `--compare BASE` |

一个典型流程：

```bash
# 目标机上（root）：采集一条命令的执行过程，带调度事件和开销标定
fgcapture.sh --sched --calibrate -o /tmp/ftrace.txt -- cat /nonexistent

# 开发机上：生成报告
python3 funcgraph.py --timeline --enable-fold --filter --fast \
    --vmlinux vmlinux --kernel-src linux/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output report.html ftrace.txt
```

## 2. 安装

FuncGraph 不需要安装，直接运行仓库里的 `funcgraph.py` 即可。

| 依赖 | 用途 | 是否必需 |
|---|---|---|
| Python 3.6+ | 主程序（只用标准库，BTF 解析也是自己实现的） | 必需 |
| binutils `addr2line`，交叉编译时用 `aarch64-linux-gnu-addr2line` 等 | 把地址解析成源码位置 | 提供 `--vmlinux` 时需要 |
| pyelftools | `--fast` 模式的地址解析（`fastfaddr2line.py`） | 用 `--fast` 时需要 |
| Pygments | `--highlight-code` 源码语法高亮 | 可选 |
| git | `--source-rev` 从 git 仓库读取指定版本的源码 | 可选 |

交叉编译的内核需要设置工具链前缀，规则和内核的 `scripts/faddr2line` 一样：

```bash
export CROSS_COMPILE=aarch64-linux-gnu-     # GCC 工具链
export LLVM=1                               # 或使用 llvm-addr2line
```

## 3. 采集 trace

### 3.1 用 fgcapture.sh 采集（推荐）

`fgcapture.sh` 在目标机上以 root 运行。它会按场景设置 tracefs、采集、检查是否丢事件，最后恢复所有被它改过的设置。

```bash
fgcapture.sh -d 0.5 -o /tmp/all.txt                  # 全系统采集 0.5 秒
fgcapture.sh --sched -- cat /nonexistent             # 只跟踪一条命令（含子进程），带调度事件
fgcapture.sh --sched --calibrate -p 1234 -d 1        # 跟踪指定 pid，并标定跟踪开销
fgcapture.sh --args -g vfs_read,vfs_write -d 1       # 带函数参数，只跟踪这两个函数的调用树
fgcapture.sh -n __schedule -d 1                      # 不跟踪调度器内部的调用
```

| 选项 | 含义 |
|---|---|
| `-o FILE` | 输出文件，默认 `./ftrace.txt` |
| `-d SECONDS` | 没有指定命令时的采集时长，默认 1 秒 |
| `-p PID[,PID]` | 只跟踪这些进程（`set_ftrace_pid`） |
| `-g FUNC[,..]` | 只跟踪这些函数的调用树（`set_graph_function`） |
| `-n FUNC[,..]` | 不跟踪这些函数的调用树（`set_graph_notrace`） |
| `-b KB` | 每个 CPU 的环形缓冲区大小，默认 16384 KB |
| `--sched` | 同时记录 `sched_switch`、`sched_waking`、`sched_wakeup` 事件，用于唤醒箭头和等待链 |
| `--calibrate` | 先用相同的选项测量跟踪开销，结果写进输出文件的第一行 |
| `--args` | 记录函数参数（见 3.3 节） |
| `--keep` | 采集后不恢复 tracefs 的原有设置 |
| `-- CMD ...` | 运行 CMD 并只跟踪它和它的子进程。命令启动时先暂停，把 pid 写入 `set_ftrace_pid` 后再继续运行 |

默认开启的 ftrace 选项是 `funcgraph-abstime`、`funcgraph-proc`、`funcgraph-retval`、`funcgraph-retaddr`、`funcgraph-tail`、`latency-format`、`funcgraph-duration`、`funcgraph-cpu`、`funcgraph-overhead` 和 `funcgraph-irqs`。

采集结束后脚本会检查有没有丢事件。如果出现 `WARNING: events were lost`，说明环形缓冲区写满被覆盖了，可以加大 `-b`、缩短 `-d`，或者用 `-p`/`-g` 缩小跟踪范围。

> 脚本通过 `trap` 在退出时恢复设置，进程被 `kill -9` 杀掉时来不及恢复。这时可以手动执行：`echo nop > /sys/kernel/tracing/current_tracer`。

### 3.2 手动采集

```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr \
         funcgraph-tail latency-format; do echo 1 > options/$o; done
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
echo nop > current_tracer
```

### 3.3 每个选项影响报告的哪部分

FuncGraph 能处理任意的选项组合，缺少的信息会在报告顶部用提示条说明。

| ftrace 选项 | 提供的信息 | 缺少时的影响 |
|---|---|---|
| `funcgraph-abstime` | 每行的绝对时间戳 | 时间轴没有真实的时间轴，切片按耗时首尾相接（"压缩时间"）；看不到空隙、off-CPU 和唤醒 |
| `funcgraph-duration` | 每个函数的耗时 | 耗时改用下一行的时间戳推算，在报告里标为"估算" |
| `funcgraph-proc` | 每行的进程名和 pid | 根据上下文切换标记推断任务，推断不出时每个 CPU 一条轨道 |
| `latency-format` | flags 列：关中断、need-resched、硬/软中断、preempt 深度 | 上下文改为根据中断入口函数名推断；没有关中断、调度延迟、临界区等分析 |
| `funcgraph-retval` | 返回值 | 没有错误路径分析 |
| `funcgraph-retaddr` | 调用点（返回地址） | 没有源码位置、调用点统计和内联帧 |
| `funcgraph-tail` | 每个 `}` 后面打印函数名 | 调用配对的可靠性稍差（开了 retval 时内核本来也会打印函数名） |
| `funcgraph-args` | 函数参数 | 只是看不到参数 |
| `sched_switch` / `sched_waking` 事件 | 换出时的任务状态、谁唤醒了谁 | 等待链里看不到唤醒者 |

**关于 `funcgraph-args`，不同内核差别很大：**

- **6.18 及没有合入 BTF 打印优化的内核**：读取 `trace` 时格式化参数非常慢，树莓派 4 上实测约 36ms 一行；而且同时开着 `funcgraph-retaddr` 时参数根本不会输出。所以采参数时必须先用 `set_graph_function`、`set_ftrace_filter` 或 `set_ftrace_pid` 缩小范围，并且关掉 retaddr。
- **带 BTF 打印优化的内核**（例如 7.2 的 trim_retval 补丁）：参数和返回地址可以同时输出，11.7 万行读出来约 1.1 秒。

`fgcapture.sh --args` 会先在一个函数上做小规模探测，自动判断属于哪种情况，然后选择合适的选项组合。

### 3.4 跟踪开销

function_graph 在每次函数进入和退出时都执行 hook，这部分时间会算进函数的耗时里。函数下面被跟踪的子调用越多，耗时被高估得越多。实测数据：

| 环境 | 每个被跟踪子调用的开销 | 每次调用自身的测量开销 |
|---|---|---|
| 树莓派 4，6.18 arm64，开启 lockdep | 约 1.1 us | 约 0.46 us |
| QEMU x86_64，7.2 | 约 0.2 us | 约 0.1 us |

用 `--calibrate` 采集，或者转换时用 `--overhead CHILD,SELF` 手动指定开销，报告里就可以切换显示扣除开销后的耗时（见 6.7 节）。

## 4. 生成报告

### 4.1 常用组合

```bash
# 完整报告：文本视图（折叠、过滤、函数名链接、源码高亮）+ 时间轴
python3 funcgraph.py --timeline --enable-fold --filter --func-links --highlight-code --fast \
    --vmlinux vmlinux --kernel-src linux/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output report.html ftrace.txt

# 没有 vmlinux：只看 trace 本身和时间轴，没有源码信息
python3 funcgraph.py --timeline --enable-fold --filter --output report.html ftrace.txt

# 很大的 trace（十几万行以上）：只生成时间轴
python3 funcgraph.py --timeline --no-text-view --vmlinux vmlinux --output report.html ftrace.txt

# 先缩小范围再生成：只保留 pid 1234 中 vfs_read 的调用树
python3 funcgraph.py --timeline --pid 1234 --graph vfs_read --vmlinux vmlinux --output report.html ftrace.txt
```

### 4.2 参数分组说明

**源码定位**

| 参数 | 含义 |
|---|---|
| `--vmlinux PATH` | 用于地址到源码的解析、函数名链接和 BTF。可选 |
| `--kernel-src DIR` | 内核源码根目录，用来显示源码内容和计算相对路径 |
| `--fast` | 使用自带的 `fastfaddr2line.py` 解析地址（需要 pyelftools），比内核脚本快得多 |
| `--base-url URL` | 在线源码的根地址，例如 bootlin。源码位置会变成可点击的链接 |
| `--func-links` | 把文本视图里的函数名也解析成源码链接 |
| `--highlight-code` | 源码块使用 C 语法高亮（需要 Pygments） |
| `--module-dirs DIR...` / `--module-srcs DIR...` | 内核模块 `.ko` 文件所在目录 / 模块源码目录 |
| `--module-url URL:mod1,mod2` | 指定某些模块使用的在线源码地址，可以重复多次 |
| `--path-prefix PATH...` | 编译时的路径前缀，从 DWARF 文件名中去掉 |
| `--entry-offset N` | 函数入口地址的偏移量，用于 `-fpatchable-function-entry` 编译的内核 |

**源码来源**（默认读取 faddr2line 能找到的本地文件）

| 参数 | 含义 |
|---|---|
| `--source-rev REV` | 从 `--kernel-src` 这个 git 仓库读取指定版本的文件（`git show REV:path`）。本地工作区已切到其他分支时使用 |
| `--source-url TEMPLATE` | 从 URL 下载源码文件，模板中 `{path}` 是文件路径，`{rev}` 是 `--source-rev` 的值 |
| `--source-cache DIR` | 下载文件的缓存目录，默认 `~/.cache/funcgraph/src` |

```bash
--source-url 'https://raw.githubusercontent.com/torvalds/linux/{rev}/{path}' --source-rev v6.18
--source-url 'https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/{path}?h={rev}' --source-rev v6.18.5
```

使用这两种来源时，工具会检查源码版本是否和 vmlinux 一致，结果显示在命令行输出和报告的 ⓘ 信息面板里，例如 `90% match the function names`。检查方法：内联链里外层帧的那一行应当调用了内层函数，所以内层函数名应该出现在那一行。版本正确时实测匹配率是 86% 到 94%，错误时是 26% 到 59%。低于 75% 时会警告"probably a different version than the vmlinux"，这时源码行号可能对不上。

**报告内容**

| 参数 | 含义 |
|---|---|
| `--enable-fold` | 文本视图支持折叠调用块 |
| `--filter` | 文本视图显示统一过滤器 |
| `--timeline` | 生成 Timeline 标签页 |
| `--compare BASE` | 生成 Compare 标签页，和 BASE 这份 trace 比较 |
| `--no-text-view` | 不生成逐行的文本视图，也不解析地址，只生成 Timeline/Compare。用于很大的 trace |
| `--btf FILE...` / `--no-btf` | 指定 BTF 来源（例如从目标机复制的 `/sys/kernel/btf/vmlinux`）/ 不使用 BTF |
| `--overhead CHILD[,SELF]` | 跟踪开销估计值（纳秒）。默认从 `fgcapture.sh --calibrate` 写入的文件头读取 |
| `--export FILE.json\|FILE.csv` | 另外导出分析结果（见第 9 节） |
| `--output FILE` | 输出的 HTML 文件，默认 `ftrace_viz.html` |

**预过滤**：在转换前执行，保留完整的调用树，不会从中间截断。

| 参数 | 含义 |
|---|---|
| `--pid 1,2` | 只保留这些任务 |
| `--comm RE[,RE]` | 只保留进程名匹配这些正则的任务 |
| `--cpu 0,1` | 只保留这些 CPU |
| `--time-range START:END` | 只保留在这个时间窗口内开始的调用（连同它们的子调用）。可以写绝对秒数，如 `2003249.69:2003249.70`；也可以写相对第一个时间戳的偏移并带单位，如 `+2ms:+5ms`；两端都可以省略 |
| `--graph RE[,RE]` | 只保留这些函数的调用树 |
| `--notrace RE[,RE]` | 去掉这些函数的调用树 |

### 4.3 输入方式

```bash
python3 funcgraph.py ftrace.txt                  # 普通文件
python3 funcgraph.py ftrace.txt.gz               # 自动解压 .gz / .xz / .bz2
ssh target cat /sys/kernel/tracing/trace | python3 funcgraph.py -   # 从 stdin 读取
python3 funcgraph.py cpu0.txt cpu1.txt cpu2.txt  # 多个文件按时间戳合并
```

多个文件都有 `funcgraph-abstime` 时间戳时按时间合并；只要有一个文件没有时间戳，就按给出的顺序直接拼接，命令行会提示。

### 4.4 配置文件和预设

把每次都一样的参数写进 INI 文件，工具会按 `--config` 指定的文件、`./funcgraph.ini`、`~/.config/funcgraph/config.ini` 的顺序查找：

```ini
[default]
fast = true
filter = true
enable-fold = true

[preset pi4]
vmlinux = /vol_1t/raspi/linux/vmlinux
kernel-src = /vol_1t/raspi/linux
base-url = https://elixir.bootlin.com/linux/v6.18/source
cross-compile = aarch64-linux-gnu-
timeline = true

[preset vm72]
vmlinux = /vol_1t/Qemu/trim_retval/x86_64/vmlinux
kernel-src = /vol_1t/Qemu/trim_retval
source-rev = HEAD
timeline = true
```

```bash
python3 funcgraph.py --preset pi4 --output r.html ftrace.txt
```

规则：
- `[default]` 段总是生效，`--preset NAME` 再叠加 `[preset NAME]` 段，命令行参数优先级最高。
- 键名就是长参数名去掉 `--`。开关类参数写 `true` 或 `false`；多值参数用空格分隔，例如 `module-dirs = a b`。
- `cross-compile` 和 `llvm` 会设置对应的环境变量（环境里已经设置了的话不覆盖）。
- 写错的键名会直接报错。`--no-config` 忽略所有配置文件。

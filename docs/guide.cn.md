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

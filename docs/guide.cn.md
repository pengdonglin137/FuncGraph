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

## 5. 文本视图

### 5.1 页面布局

- **页头**：trace 总行数、可展开行数、解析到的内核函数数；过滤后会显示 `Filtered: N`。右侧依次是 **Theme**（切换深色/浅色）、**?**（快捷键说明）和 **ⓘ**（信息面板：运行环境、处理耗时、源码来源及版本一致性检查、生成参数）。
- **标签页**：Text / Timeline / Compare。
- **顶部工具栏**（滚动时保持在顶部）：

| 按钮 | 作用 |
|---|---|
| Filter | 显示或隐藏过滤框 |
| Top | 回到页面顶部 |
| Expand All / Collapse All | 展开或收起所有源码块 |
| Copy | 把当前可见的行复制为纯文本 |
| Heatmap | 打开或关闭耗时热力着色，右侧显示图例 |
| Order: time / by task | 行的顺序：按时间（原样，所有 CPU 的行交错）或按任务分组，见 5.6 节 |
| Source: chain / flat | 源码块的排列方式，见 5.5 节 |

### 5.2 一行 trace 的各列

以开了全部选项的一行为例：

```
2003237.699097 |   0)  true-130265   |  d.h1. | + 12.500 us   |    link_path_walk(); /* <-path_lookupat+0x84/0x180 ret=-2 */
└─ 时间戳 ────┘   └CPU┘ └─ 任务 ──┘     └flags┘ └─ 耗时 ─────┘   └ 缩进 + 函数 ┘     └─ 调用点 ─────────────┘ └返回值┘
```

| 列 | 来自哪个选项 | 含义 |
|---|---|---|
| 时间戳 | `funcgraph-abstime` | 事件发生时间，单位秒，精确到微秒 |
| CPU | `funcgraph-cpu` | 事件发生的 CPU |
| 任务 | `funcgraph-proc` | `进程名-pid`。`<idle>-0` 是 idle 任务 |
| flags | `latency-format` | 5 个字符，含义见下表 |
| 耗时 | `funcgraph-duration` | 函数耗时。叶子函数在调用行显示，非叶子函数在 `}` 行显示。前面的标记字符表示耗时等级，见下表 |
| 函数 | | 按调用深度缩进。`func() {` 是进入，`func();` 是叶子调用（进入和返回合成一行），`}` 是返回 |
| `/* <-caller+off/len */` | `funcgraph-retaddr` | 调用点：从哪个函数的哪个偏移调用的。`len` 是调用者函数的长度 |
| `ret=` | `funcgraph-retval` | 返回值，见 5.3 节 |

**flags 列**（与 `/sys/kernel/tracing` 文档中 latency-format 的定义一致）：

| 位置 | 字符 | 含义 |
|---|---|---|
| 1 中断 | `d` | 关中断 |
| | `D` | 关中断且关软中断（BH） |
| | `b` | 只关软中断 |
| | `.` | 都没关 |
| 2 需要调度 | `N` | 设置了 TIF_NEED_RESCHED，且 preempt 计数要求调度 |
| | `n` | 只设置了 TIF_NEED_RESCHED |
| | `p` | 只有 preempt 计数要求调度 |
| | `L` / `l` | 懒抢占（PREEMPT_LAZY）的 need-resched，`L` 同时有 preempt 要求 |
| | `B` / `b` | need-resched 和 lazy 同时设置（`B` 同时有 preempt 要求） |
| | `.` | 不需要调度 |
| 3 上下文 | `h` | 硬中断 |
| | `s` | 软中断 |
| | `H` | 软中断中又来了硬中断 |
| | `z` / `Z` | NMI / 硬中断中的 NMI |
| | `.` | 进程上下文 |
| 4 preempt 深度 | `0`–`f` | preempt_count 的低 4 位，`.` 表示 0 |
| 5 migrate-disable 深度 | `0`–`f` | 禁止迁移的嵌套深度，`.` 表示 0 |

注意：preempt 深度是在跟踪 hook 里读到的，跟踪路径本身通常会多加 1。所以在 6.18 和 7.2 上，进程上下文里正常的值是 `1` 而不是 `.`。时间轴分析会自动识别这个基线（见 7.3 节）。

**耗时标记字符**（内核 `trace_find_mark()` 的定义）：

| 标记 | 耗时 | 热力图等级 | 热力图颜色 |
|---|---|---|---|
| （空格） | ≤ 10 us | 1 | 很淡的绿 |
| `+` | > 10 us | 2 | 浅绿 |
| `!` | > 100 us | 3 | 黄 |
| `#` | > 1 ms | 4 | 琥珀 |
| `*` | > 10 ms | 5 | 橙 |
| `@` | > 100 ms | 6 | 红 |
| `$` | > 1 s | 7 | 深红 |

热力图打开后，整行的背景色按这个等级着色。

### 5.3 语法高亮的颜色

| 元素 | 颜色（浅色主题） |
|---|---|
| CPU 编号 | 红色加粗 |
| 时间戳、耗时数字 | 蓝色 |
| 单位 `us` | 紫色加粗 |
| 函数名 | 紫色加粗；开了 `--func-links` 时是可点击的链接 |
| 模块函数名 | 青蓝色加粗 |
| 十六进制值（参数、地址） | 深蓝色 |
| 注释 `/* ... */` | 灰色斜体 |
| 括号、分号等 | 深灰加粗 |
| `ret=` 是已知 errno（如 -2） | **红色带浅红底**，后面附上名字，例如 `ret=-2 /* ENOENT */` |
| `ret=` 其他值 | 灰色加粗 |

返回值的格式和内核版本有关：
- **6.18 等内核**：错误码以负的十进制打印（`ret=-2`），其他值都是十六进制（`ret=0x0`）。void 函数也会打印 `ret=`，内容是寄存器里的残留值，不代表任何意义。
- **带 BTF 感知 retval 的内核**（如 7.2 trim_retval）：void 函数不打印 `ret=`；bool 打印成 `ret=true/false`；有符号整数用十进制，无符号用十进制或十六进制；超过 long 宽度的类型后面带 `(trunc)`；返回 ERR_PTR 的指针打印成负 errno。

### 5.4 折叠

开启 `--enable-fold` 后，每个 `func() {` 行前有一个 ▶ 图标：

- 点击 ▶ 折叠或展开这个调用块；按住 Shift 点击会递归折叠或展开所有子块。
- 按数字键 `1`–`9`：只显示深度不超过 N 的调用；`0`：展开全部。
- 折叠后，入口行后面显示摘要：子调用次数、总耗时、返回值。
- 折叠状态保存在浏览器的 localStorage 中，按报告区分。

折叠相关的行背景色：

| 颜色 | 含义 |
|---|---|
| 很淡的绿 | 调用块的入口行 `func() {` |
| 很淡的蓝 | 调用块内部的行 |
| 很淡的红 | 调用块的出口行 `}` |
| 较深的红 | 已折叠的入口行 |
| 左侧绿色竖线 | 调用块的范围（从入口到出口） |

### 5.5 源码块

带调用点的行可以展开，点击行或按 Enter 都可以。展开后显示调用点对应的源码：前后各 5 行，当前行高亮，标成 `>行号<`。

调用点落在内联代码里时，faddr2line 会给出一条内联链。**Source** 按钮在两种排列之间切换：

- **chain**（默认）：从外到内排列。标 `traced` 的是被 ftrace 跟踪到的那个函数，下面依次是内联进来的各层 `inline` 函数，每层缩进一级、可以折叠，并注明"→ inlines X here"。只有最内层（"← the traced call happens here"，真正发起这次调用的那一行）默认展开。每层右边的 ↗ 是在线源码链接。
- **flat**：和 faddr2line 的原始输出一样，最内层在前，全部展开。

### 5.6 按任务分组

ftrace 按时间输出所有 CPU 的事件。同时运行的任务越多、切换越频繁，同一个任务的调用就越零散：实测相邻两行属于不同 CPU 的比例在 25% 到 58% 之间。

点 **Order** 切换成"by task"后：
- 每个任务的行连在一起，保持原有顺序。行数多的任务排在前面，idle 任务排在最后。
- 每组上方有一个固定在顶部的标题：`任务名 pid · 行数 · 运行过的 CPU`。
- 每次上下文切换后插入一行灰色斜体的摘要，例如 `⇣ switched out on CPU 0 → idle-0; resumes at line 19703 after 7.640 ms (1 lines of other tasks in between)`，含义是：在 CPU 0 上被切走，换上了谁，多久之后在第几行恢复，中间插了多少行其他任务。切换标记之间的分隔线和空行会被隐藏。
- 折叠、过滤、热力图和源码展开在分组模式下都照常可用。切回"time"后按行号恢复原顺序。

### 5.7 统一过滤器

在过滤框中输入 `字段:值`，可以用 `AND`、`OR`、`NOT` 和括号组合，按 Enter 或 Apply 生效。也可以点下面的字段按钮，从下拉列表里选。

| 字段 | 匹配 | 示例 |
|---|---|---|
| `cpu` | CPU 编号（正则） | `cpu:0`、`cpu:0\|1` |
| `pid` | 进程 pid（正则） | `pid:1234` |
| `comm` | 进程名（正则） | `comm:bash` |
| `func` | 本行的函数名（正则），只匹配这一行 | `func:mutex` |
| `graph` | 匹配函数的**整棵调用树**（类似 `set_graph_function`） | `graph:vfs_read` |
| `notrace` | 隐藏匹配函数的整棵调用树（类似 `set_graph_notrace`） | `notrace:__schedule` |
| `ret` | 返回值；`ret:all` 匹配所有已知的错误码 | `ret:-22`、`ret:EINVAL`、`ret:all` |
| `params` | 参数文本（正则） | `params:folio` |
| `duration` | 耗时（us），支持 `>` `<` `>=` `<=` | `duration:>10` |
| `irqs` | flags 第 1 位 | `irqs:d` |
| `resched` | flags 第 2 位 | `resched:N` |
| `hirq` | flags 第 3 位 | `hirq:h`、`hirq:s` |
| `preempt` | preempt 深度，支持比较 | `preempt:>1` |
| `sort:desc` / `sort:asc` | 按耗时对可见行排序 | `duration:>0 sort:desc` |

示例：

```
comm:cat AND graph:anon_pipe_read             cat 进程里 anon_pipe_read 的完整调用树
graph:vfs_read AND NOT notrace:security_      vfs_read 调用树，但去掉 security_ 开头的子树
ret:all AND comm:ls                           ls 进程里所有返回错误码的行
irqs:d AND duration:>50                       关中断状态下耗时超过 50 us 的调用
```

过滤结果会同步到时间轴（见 6.6 节）。

### 5.8 键盘与鼠标

| 操作 | 作用 |
|---|---|
| `j` / `k` 或 ↓ / ↑ | 移到下一个/上一个可展开的行 |
| `h` / `l` | 左右滚动 |
| Enter | 展开或收起当前行的源码块 |
| Space / PageDown / PageUp | 翻页 |
| `gg` / `G`（或 Home / End） | 跳到开头/结尾 |
| `1`–`9` / `0` | 折叠到深度 N / 展开全部 |
| Esc | 取消选中 |
| `?` | 显示快捷键说明 |
| 点击行号 | 复制这一行的锚点链接（`#L行号`），可以分享给别人直接定位 |
| Alt + 点击一行 | 在时间轴中定位这一行对应的调用 |

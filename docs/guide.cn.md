# FuncGraph 使用指南

本文介绍从采集 trace 到阅读报告的完整流程，并逐一说明报告中每个视图的字段和配色。命令行参数的完整列表见 `python3 funcgraph.py --help`，实现细节见 [design.md](design.md)。English: [guide.en.md](guide.en.md).

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
| `ret` | 返回值的数字（正则）；`ret:all` 匹配所有已知的错误码 | `ret:-22`、`ret:-2$`、`ret:all` |
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

## 6. 时间轴视图

### 6.1 布局

从上到下依次是：

1. **工具栏第一行**：Color（配色模式）、Overlay（叠加层开关）、函数搜索框、错误导航。
2. **工具栏第二行**：Fit（显示全部）、Tracks（展开或收起所有轨道）、Inline（内联帧视图）、Overhead（实测/扣除开销）、Errors、Sections、Sched latency（三个分析面板）、?（帮助和图例）。
3. **Filter 栏**：与文本视图共用的过滤表达式，见 6.6 节。
4. **提示条**：说明这份 trace 缺少哪些信息、有什么限制，例如"No timestamps…""Arguments are not printed…"。蓝色是提示，黄色是需要注意的问题。
5. **概览条**：整份 trace 的缩略图。蓝色柱子表示各时刻的繁忙程度，红色竖线是错误源头（高的表示传到了用户态，矮的橙色表示被调用者处理掉了），蓝框是当前看到的范围。点击或拖动可以跳转，在上面滚动滚轮可以缩放。
6. **时间刻度**：右侧显示当前范围的跨度（`span`）和起始的绝对时间（`start`）。框选范围后会显示 `selected`，鼠标所在位置的时间显示在光标旁。
7. **CPU 轨道**（只在有时间戳时显示）和**任务轨道**。

**时间模式**取决于 trace 里有哪些列，在时间刻度右侧注明：

| 模式 | 条件 | 横轴含义 |
|---|---|---|
| abs | 有 `funcgraph-abstime` | 真实时间，空隙也是真实的 |
| rel | 有相对时间戳 | 同上 |
| compressed | 只有耗时 | 每个任务内的调用按耗时首尾相接，看不到空隙，标为"compressed time" |
| count | 都没有 | 每个叶子调用算一个单位，只表示调用顺序 |

### 6.2 轨道

**任务轨道**：每个任务一条，任务越繁忙越靠上，idle 任务排在最后并默认收起。

- 标题：`进程名 pid`，后面是运行过的 CPU、调用次数、错误数。过滤时还有匹配数（`N matched`）；任务改过名字时显示 `was: 旧名字`（例如 exec 前叫 bash）。没有 `funcgraph-proc` 时标题是 `CPU N (no funcgraph-proc)`。
- 点击标题可以收起或展开这条轨道。收起后只剩一行很窄的条，显示最外层的调用。
- 轨道内横轴是时间，纵轴是调用深度，最外层在最上面。**切片的宽度就是函数的实测耗时。**

**CPU 轨道**（顶部的 `CPUs`）：每个 CPU 一行，显示这个 CPU 上依次运行了哪个任务。

- 每段的颜色按任务名取色（同一任务颜色相同），宽度足够时标上 `任务名 pid`。
- 段底部的细条表示中断占用：**橙色**是硬中断，**绿色**是软中断，**紫色**是 NMI。
- 悬停显示这个任务在这个 CPU 上连续运行了多久，以及期间硬中断和软中断各占多少时间；点击可以跳到这个任务的轨道。

### 6.3 配色模式（Color）

| 模式 | 颜色表示什么 |
|---|---|
| **Context**（默认） | 执行上下文。**蓝色**：进程上下文；**绿色**：软中断；**橙色**：硬中断；**紫色**：NMI。同一上下文中不同函数的色相和亮度有轻微差别，方便区分相邻的切片 |
| **Function** | 按函数名取色，同名函数颜色相同，用来找同一个函数在各处的调用 |
| **Self time** | 自身耗时（不含子调用），对数刻度。**蓝色**表示最短，过渡到**红色**表示最长，用来找真正耗时的地方 |
| **CPU** | 调用运行在哪个 CPU 上，每个 CPU 一种颜色。未知时为灰色。用来观察迁移 |
| **Module** | 所属的内核模块，每个模块一种颜色；内核本体的函数是浅灰色。trace 里没有模块函数时不显示这个选项 |

没有 `latency-format` 时，Context 模式下的上下文是根据中断入口函数名（如 `gic_handle_irq`、`__sysvec_*`、`handle_softirqs`）推断出来的。这些切片的悬停提示里会标注 `(inferred)`。

### 6.4 叠加层（Overlay）和标记

每个叠加层都可以单独开关。trace 里缺少相应数据时，对应的按钮会隐藏。

| 叠加层 | 显示 | 含义 |
|---|---|---|
| **Errors** | **深红实心**切片 | 错误源头，且错误一直返回到了用户态（调用链上有 `*_sys_*`） |
| | **橙色实心**切片 | 错误源头，但被某个调用者处理掉了 |
| | **浅红底 + 红边框** | 错误传播：返回了和子调用相同的错误码 |
| | **浅橙底 + 橙边框** | 被处理掉的错误的传播帧 |
| | 小**三角**（红/橙） | 错误源头太窄、放不下标签时，在它上方画一个三角 |
| | 切片标签 `func = ENOENT` | 错误源头会在函数名后附上 errno 名字 |
| **IRQs off** | 切片上的**斜纹** | 这次调用开始时中断是关闭的 |
| | 轨道标题底部的**红色细线** | 关中断的时间段 |
| **Resched delay** | 切片右上角的**琥珀色三角** | 这次调用开始时已经设置了 need-resched，也就是该调度了但还没调度 |
| | 轨道标题底部的**琥珀色横条** | need-resched 窗口：从设置到真正发生调度的这段时间，即调度延迟 |
| **Off-CPU** | 灰色斜纹段，标注 `off-CPU 7.640 ms · do_wait [S]` | 任务不在 CPU 上的时间，画在当时打开的那一帧的下一层。标注依次是：时长、在哪个函数里等待、换出时的状态（`S` 可中断睡眠、`D` 不可中断、`R` 仍可运行） |
| | 同样的段带**琥珀色底** | 被抢占（任务本来还能继续运行） |
| **Wakeups** | **青色箭头**，从唤醒者的圆点指向被唤醒者恢复运行的位置 | 谁在什么时候唤醒了谁（需要 `sched_waking` 或 `sched_wakeup`） |
| | 只有一个小三角 | 唤醒挤在一起时只画终点。悬停或选中某个任务、悬停某段 off-CPU 时，会画出相关的完整箭头；右上角提示"N of M wakeups bunched" |
| **Preempt depth**（默认关闭） | 切片底部的灰色条，越深表示 preempt 深度越大 | |
| **Markers** | 切片右边缘的**点线** | 只看到了进入，没看到返回（采集窗口在这里结束，或者事件丢失） |
| | 切片左边缘的**点线** | 只看到了返回，没看到进入（调用在采集开始之前就已经开始） |
| | 切片左上角的**紫色小方块** | 调用点所在的函数和父切片不是同一个函数，说明中间有没被跟踪的帧（notrace、汇编入口、尾调用）或者内联函数 |
| | 切片底部的**红色下划线** | 在原子上下文中调用了可能睡眠的函数（见 7.4 节） |

其他视觉效果：

- **变暗**：被搜索或过滤排除的切片显示为原透明度的 18%。
- **密集块**：宽度不足 1.5 像素的相邻切片合并成 1 像素宽的块，颜色取其中第一个切片。放大后会还原成单独的切片。
- **选中**：粗的深色边框。**悬停**：细边框。
- **内联帧**（Inline 视图下）：灰色半透明底加虚线框，标签是 `inline 函数名`。见 6.8 节。

### 6.5 悬停提示和详情面板

**悬停在切片上**时显示：

| 字段 | 含义 |
|---|---|
| 函数名 `[模块]` | 模块函数会附上模块名 |
| 耗时、`self` | 实测耗时和自身耗时。标了 `(estimated)` 表示没有 duration 列，耗时是由时间戳推算的 |
| start | 相对 trace 起点的时间，括号里是绝对时间戳 |
| task | 任务和 CPU |
| context | 上下文，后面附上 flags 的解读：irqs off、BH off、need-resched 类型、preempt N、migrate-off N。`(inferred)` 表示是推断的 |
| return | 返回值。是错误时后面附上 errno 名字、角色（error origin 错误源头 / propagated 传播）和去向（returned to user space 返回到用户态 / handled by caller 被调用者处理 / propagates beyond trace 传出了采集范围）。`unverified: no BTF type` 表示没有类型信息，只是根据数值判断的 |
| caller | 调用点。`(untraced frame between)` 的含义同紫色标记 |
| inlined in | 调用点所在的内联函数链 |
| args | 函数参数（采集时开了 `funcgraph-args` 才有） |

**悬停在 off-CPU 段上**：等待时长、在哪个函数里等待、换出时的状态、从哪个 CPU 换出又在哪个 CPU 上恢复、唤醒者。

**点击切片**会在下方打开详情面板：

- 标题旁的标签：上下文（`?` 表示推断）、irqs off、BH off、need-resched、error origin/propagated、may sleep in atomic context、no exit、no entry、estimated。
- 左栏：duration、self（及占总耗时的比例）、start、task、depth、flags、return、call site、args、trace lines（入口和出口在 trace 中的行号，点击跳到文本视图）。
- 右栏：
  - **Where the time went**：一条堆叠条，把实测耗时拆成几部分（加起来正好等于实测耗时）：

    | 颜色 | 部分 |
    |---|---|
    | 深蓝 | own code：函数自身的代码 |
    | 浅蓝 | traced callees：被跟踪的子调用 |
    | 橙色 | interrupts / softirq：打断这次调用的中断和软中断 |
    | 灰色 | blocked：阻塞、睡眠的时间 |
    | 黄色 | preempted：被抢占的时间 |

    下方会注明里面有多少次被跟踪的调用，以及大约多少时间是跟踪开销（有开销估计值时）。
  - **Error chain**：错误从源头到最外层的传播路径。
  - **Call stack**：调用栈，左边是每一帧的耗时，内联帧用斜体并标为 `inline`。调用栈的起点如果不在采集范围内，会显示"… N frame(s) outside trace window"。
  - **Children**：耗时最多的子函数，`×N` 表示调用次数。
- 右上角链接：**Duration distribution (H)**（见 7.2 节）和 **Open in text view ↵**。

### 6.6 搜索和过滤

- **搜索框**（快捷键 `/`）：输入函数名正则。不匹配的切片变暗，右侧显示匹配的调用数；按 Enter 或 `N` 跳到下一个匹配，Shift+Enter 或 Shift+`N` 跳到上一个。
- **Filter 栏**：语法和文本视图的统一过滤器完全相同（5.7 节），两边会同步。不匹配的调用变暗，错误导航、范围汇总和各个分析面板都只统计匹配的调用；右侧显示 `N of M calls`。**Hide unmatched** 隐藏没有任何匹配的轨道，**Clear** 清除过滤。

### 6.7 实测耗时与扣除开销

有跟踪开销估计值时（`fgcapture.sh --calibrate` 写入的文件头，或者 `--overhead` 参数），工具栏会出现 **Overhead** 按钮（快捷键 `O`）：

- **measured**：显示实测耗时。
- **subtracted**：悬停提示、详情面板、各种汇总和分析面板里的耗时，都改成扣除开销后的值。计算方法：`实测 - 每个子调用的开销 × 被跟踪的子孙调用数 - 每次调用的开销`，最小为 0。

**切片的位置和宽度始终按实测时间绘制**，只有显示的数字会变。这个修正值是估计：开销会随 CPU、频率和内核配置变化，在中断较多或缓存行为不同的路径上会有偏差。

### 6.8 内联帧视图（Inline）

开启 `--vmlinux` 并有 `funcgraph-retaddr` 时，工具会知道每个调用点是否位于内联代码中。**Inline** 按钮（快捷键 `I`）在两种视图间切换，选择会被记住：

- **hidden**：只显示被 ftrace 跟踪到的帧。
- **shown**：在父切片和子切片之间插入内联帧。同一个父切片下，连续几个调用如果位于同一段内联代码里，会合并成一个内联帧。

内联帧的**开始和结束位置**是它里面第一个被跟踪调用的开始和最后一个被跟踪调用的结束。内联代码本身没有进入和返回事件，所以真实的开始可能更早、结束可能更晚，这一点在提示和详情里都有说明。

内联帧的悬停提示和详情面板显示：

| 字段 | 含义 |
|---|---|
| traced extent | 第一个到最后一个被跟踪调用的跨度 |
| start / end | 开始和结束时间 |
| inlined into | 内联到哪个函数的哪个文件哪一行 |
| defined in | 这个内联函数定义在哪个文件 |
| traced calls | 里面有多少次被跟踪的调用，合计多少时间 |
| between them | 跨度减去这些调用的时间：内联函数自身的代码、没被跟踪的调用和中断 |
| Inline chain at the call site | 完整的内联链，当前这一层加粗 |
| Traced calls inside | 里面的每一次调用，点击可以跳过去 |

两种视图下，调用栈都会列出内联帧。

### 6.9 键盘和鼠标

| 操作 | 作用 |
|---|---|
| `W` / `S`，或 Ctrl + 滚轮 | 以鼠标位置为中心放大 / 缩小 |
| `A` / `D`、← / →、拖动、Shift + 滚轮 | 左右平移 |
| 滚轮 | 上下滚动轨道 |
| `0` 或 Home | 显示全部 |
| `F` | 放大到选中的切片或框选的范围 |
| 双击切片 / off-CPU 段 | 放大到这个切片 / 这段时间 |
| 点击切片 | 选中并打开详情面板 |
| 点击 off-CPU 段 | 打开等待链（7.5 节） |
| 点击轨道标题 | 收起或展开这条轨道 |
| Shift + 拖动 | 在当前轨道内框选一段时间并汇总（7.1 节） |
| Shift + Alt + 拖动 | 在所有轨道上框选并汇总 |
| ↑ / ↓ | 选中父调用 / 第一个子调用 |
| `[` / `]` | 选中上一个 / 下一个兄弟调用 |
| `E` / Shift + `E` | 下一个 / 上一个错误源头 |
| `/`、`N` / Shift + `N` | 搜索、下一个 / 上一个匹配 |
| `H` | 选中函数的耗时分布 |
| `O` | 实测耗时 / 扣除开销 |
| `I` | 隐藏 / 显示内联帧 |
| Enter | 在文本视图中打开选中的调用 |
| Esc | 关闭帮助；取消选中和框选 |
| `?` | 帮助和图例 |

## 7. 分析面板

分析面板都显示在时间轴下方的详情区，会跟随过滤条件和 Overhead 开关更新。表头可以点击排序（当前排序列带下划线），表格行可以点击跳转到时间轴。

### 7.1 范围汇总（Shift + 拖动）

在一条轨道上按住 Shift 拖动，可以框选一段时间；同时按住 Shift 和 Alt 则框选所有轨道。框选后显示：

- 标题标签：范围长度、轨道、调用次数，以及各上下文的自身耗时（task / softirq / hardirq / NMI）和 off-CPU 时间。
- 表格：每个函数的 calls（在范围内开始的调用次数）、self（自身耗时）、inclusive（总耗时）、max（最长的一次）、errors（错误源头次数）。和范围部分重叠的调用按重叠比例计入。点击一行，会在搜索框中填入这个函数名并高亮它。

### 7.2 耗时分布（H）

选中一个切片后按 `H`，或点详情面板里的 **Duration distribution**：

- 左侧：这个函数所有调用的 min、p50、p90、p99、max 和 mean。
- **直方图**：横轴是耗时（对数刻度），纵轴是调用次数（对数刻度）。蓝色竖线是 p50，琥珀色竖线是 p99；包含错误源头的柱子画成红色。点击一个柱子，下方的表格会列出这个区间里的调用。
- **最慢的调用**：耗时、自身耗时、任务、开始时间、调用点，`×p50` 表示是中位数的多少倍。点击可以跳到这次调用。
- **By call site**：按调用点分组，每个调用点的调用次数和 p50/p99/max。同一个函数从不同的地方调用，耗时可能差别很大。点击某一行，就只看这个调用点的分布，再点一次取消。
- **Slow vs fast** 按钮：把最慢的 10% 和最快的一半调用分成两组，比较两组在子调用中每个函数平均的调用次数和自身耗时，按差值从大到小排列。另外还有几行特殊项：`(own code)`、`(interrupts / softirq)`、`(blocked off-CPU)`、`(preempted)`。差值大的行就是"慢的那些调用多做了什么"。例如，慢的 `kmem_cache_alloc` 调用都多出了 `___slab_alloc`，也就是走进了分配器的慢路径。

### 7.3 临界区（Sections）

列出进程上下文中每一段**关中断**、**关抢占**、**关软中断**的区间，用顶部的按钮在三类之间切换，按钮上显示各类的数量：

| 列 | 含义 |
|---|---|
| duration | 区间时长，超过 100 us 的标红 |
| start / task | 开始时间和任务 |
| started in / ended in | flag 变化时栈顶的被跟踪函数。`top level` 表示不在任何函数里；`switched out / end` 表示任务被换出或者采集结束；`interrupt` 表示中断进来了（说明中断其实已经在没被跟踪的代码里重新打开了） |
| max depth | 关抢占时 preempt 深度的最大值（只在 preempt off 这一类中显示） |
| top self time inside | 区间内自身耗时最多的 3 个函数，也就是持有临界区时在做什么 |

preempt 深度是相对基线计算的。trace 中进程上下文的最小 preempt 深度会被识别为基线（6.18 和 7.2 上都是 1），面板说明里会显示这个值。

精度说明：开关中断、开关抢占的操作通常是内联代码，不会出现在 trace 里，所以区间的起止只能定位到"flag 变化前后的两个被跟踪的调用"之间。点击一行会在时间轴上框选出这段区间。

### 7.4 错误汇总（Errors）

- 顶部（如果有）：**Sleeping functions called in atomic context**。列出在关中断、关抢占、关软中断或中断上下文中调用的可能睡眠的函数（`__might_sleep`、`mutex_lock*`、`down*`、`wait_for_completion*`、`msleep`、`schedule_timeout*` 等），以及原因、所在的父函数和次数。点击可以跳到对应的调用。
- 表格：把错误链按 **syscall / entry → origin → errno** 分组：

| 列 | 含义 |
|---|---|
| syscall / entry | 错误所在的系统调用（去掉了 `__x64_sys_` 等前缀）；不在系统调用里时显示最外层函数，并标 `no syscall` |
| origin | 错误源头：第一个返回这个错误码、且它的子调用都没有返回同一个错误码的函数 |
| errno | 错误码和名字 |
| count | 发生次数 |
| to user | 其中一直返回到用户态的次数（标红） |
| handled | 其中被某个调用者处理掉的次数 |
| tasks | 涉及几个任务 |
| chain | 传播链最长有几帧；`?` 表示其中有未经类型验证的帧 |

点击一行跳到第一次出现的位置，再次点击依次跳到下一次，提示显示 `occurrence k / n`。

**次要错误（minor）默认隐藏**：只有一帧、马上被调用者处理掉、并且这个函数的负返回值在整份 trace 里从来没有向上传播过的错误源头。这类通常是带符号但不是 errno 的数值（例如 `memcg_state_val_in_pages` 返回 -1 这样的页数增量），或者正常的探测路径（尝试各个 binfmt 处理器时 `load_script` 返回 -ENOEXEC）。它们不在切片上标记，不计入错误导航、概览条和对比。错误面板里勾选 *show N minor* 可以显示它们，`--export` 导出时标记为 `"minor": true`。

**错误是怎么判定的**：

1. 有 BTF（`--vmlinux` 的 `.BTF` 段、`--btf` 指定的文件，或者内核本身就按 BTF 格式化返回值）时，按函数的返回类型判断：
   - void、无符号、bool、枚举：不算错误；
   - 有符号整数：先截断到类型宽度，值在 [-4095, -1] 内算错误；
   - 指针：按 `IS_ERR` 判断。返回 NULL 不算错误，但会单独标记。
   - 名字里带 `cmp`/`compare` 的比较函数不算错误，因为它们返回的是差值。
2. 没有 BTF 时只能根据数值判断，void 函数的寄存器残值会造成误报。这类结果在报告里标为 unverified（未验证）。

错误导航按钮（工具栏上的 `◀ N errors · to user ▶`）：点中间的文字，可以在"只看返回到用户态的错误"和"全部错误"之间切换。

### 7.5 等待链（点击 off-CPU 段）

一行表示一次等待，从被点击的这段 off-CPU 开始：

| 列 | 含义 |
|---|---|
| task / waited | 任务和等待时长 |
| waiting in | 在哪个函数里等待（栈上第一个不属于调度器的函数），方括号里是换出时的状态 |
| woken by | 唤醒者；如果是在中断上下文中唤醒的，附上上下文标签 |
| waker was in | 唤醒者当时在执行什么：跳过了 `try_to_wake_up`、`__wake_up*` 等唤醒函数之后的第一帧，例如 `anon_pipe_write` |
| wake latency | 从被唤醒到真正开始运行的时间，超过 1 ms 的标红 |
| run again on | 在哪个 CPU 上恢复运行 |

如果唤醒者自己之前也在等待，下一行接着显示它在等什么，依此类推。唤醒来自 idle 或中断上下文时，链条到此结束。

唤醒者优先取 `sched_waking` 事件：它在唤醒者自己的上下文里触发。`sched_wakeup` 有时在目标 CPU 的 IPI 里触发，这时看到的唤醒者会变成 idle。所以建议采集时开启 `sched_waking`，`fgcapture.sh --sched` 默认就会开。

### 7.6 调度延迟（Sched latency）

列出每个 **need-resched 窗口**，也就是从设置 need-resched 标志到任务真正调度出去或者标志被清除的这段时间：

- 标题标签：窗口数量、总时长、最长的一个。下方的小标签汇总了所有窗口中自身耗时最多的函数，也就是"谁在拖延调度"。
- 表格列：latency（超过 100 us 的标红）、start、task、max preempt（窗口内的最大 preempt 深度）、irqs-off（窗口内关中断的时长）、top self time inside（窗口内自身耗时最多的函数）、ended by（flag cleared 标志被清除 / switched out 被换出 / trace end 采集结束）。
- 点击一行，会在时间轴上框选这个窗口。

需要 `latency-format` 和时间戳才能计算。

## 8. 对比视图

```bash
python3 funcgraph.py --compare before.txt --output cmp.html after.txt
```

当前 trace（这里是 `after.txt`）和基准 trace（`before.txt`）会按相同的方式重建调用、按函数汇总。预过滤参数会同时作用于两份 trace。不需要 `--timeline` 也能生成对比视图。

- **顶部标签**：traced time（最外层调用的总时长）、calls、error chains 三项的变化，`+` 表示增加、`-` 表示减少。变化小于 5% 显示为灰色，增加为红色，减少为绿色。两份 trace 的时间模式不同时（例如一份有时间戳一份没有），会显示黄色警告。
- **表格**：每个指标都有三列：基准值、当前值、差值 Δ。

| 指标 | 含义 |
|---|---|
| calls | 调用次数 |
| self time | 自身耗时（总耗时减去被跟踪的直接子调用） |
| inclusive | 总耗时 |
| mean incl | 平均每次调用的总耗时 |
| max | 最长的一次 |
| errors | 错误源头次数 |

- **Δ 列的颜色**：**红色**表示增加，**绿色**表示减少，**灰色**表示变化不到 5%。排序列旁边的横条长度表示变化幅度。函数名后的 **new** 表示只在当前 trace 里出现，**gone** 表示只在基准里出现。
- **控件**：函数名正则过滤；**changed only** 只显示有变化的函数；排序方式：`|Δ|`（变化的绝对值）、`Δ`、`Δ%`、`current`、`base`；点击表头切换排序的指标。两份 trace 都有开销估计值时，会出现 **subtract tracing overhead** 选项。

需要注意：两份 trace 的跟踪开销要相近（用相同的选项、在同一台机器上采集），差值才有意义。

## 9. 导出与自动化

`--export FILE.json` 或 `--export FILE.csv` 会在生成 HTML 的同时导出分析结果，不需要开 `--timeline`：

```bash
python3 funcgraph.py --export result.json --output report.html ftrace.txt
```

JSON 中的耗时单位都是 ns，时间都相对第一个事件：

| 键 | 内容 |
|---|---|
| `meta` | trace 名、时间模式、能力（caps）、开销估计值、调用总数 |
| `functions` | 每个函数的 `name, calls, total, self, max, p50, p90, p99, errors`，按 self 从大到小排序 |
| `errors` | 每条错误链：`origin, top, entry, errno, name, fate`（user 返回用户态 / handled 被处理 / escaped 传出采集范围）`, verified, frames, task, pid, start` |
| `sections` | 临界区：`kind`（irqs_off / preempt_off / bh_off）`, duration, task, pid, start, started_in, ended_in, max_depth` |
| `sched_latency` | need-resched 窗口：`task, pid, start, duration, max_preempt, irqs_off` |
| `atomic_sleep` | 原子上下文中调用的睡眠函数：`function, reasons, parent, task, pid, start` |
| `off_cpu` | off-CPU 段：`task, pid, start, duration, waiting_in, state, preempted, woken_by` |

CSV 只包含 `functions` 表。

用在 CI 中的例子：

```bash
python3 funcgraph.py --export r.json --no-text-view --timeline --output r.html ftrace.txt
python3 - <<'EOF'
import json, sys
d = json.load(open('r.json'))
f = {x['name']: x for x in d['functions']}
if f.get('vfs_read', {}).get('p99', 0) > 50_000:            # p99 超过 50 us
    sys.exit('vfs_read p99 regression')
if any(s['kind'] == 'irqs_off' and s['duration'] > 200_000 for s in d['sections']):
    sys.exit('irqs off for more than 200 us')
if d['atomic_sleep']:
    sys.exit('sleeping function called in atomic context')
EOF
```

## 10. 常见问题

**报告太大，浏览器打不开或者很慢**

文本视图为每一行生成 DOM。超过约 15 万行时，命令行会给出提示。可以：
- 用预过滤缩小范围：`--pid`、`--comm`、`--graph`、`--time-range`；
- 只生成时间轴：`--timeline --no-text-view`。实测 36 万行的 trace 生成的报告是 15.7 MB，约 1 秒打开。

**时间轴上没有空隙，横轴写着 compressed time**

采集时没有开 `funcgraph-abstime`。

**看不到中断、关中断、调度延迟**

没有开 `latency-format`。没有 flags 列时，上下文只能根据中断入口函数名推断，标为 `(inferred)`。

**有很多看起来不对的错误，比如 `do_raw_spin_unlock = -1`**

这是 void 函数返回值寄存器里的残留值。提供带 `.BTF` 段的 vmlinux（需要 `CONFIG_DEBUG_INFO_BTF=y`），或者用 `--btf` 指定从目标机复制的 `/sys/kernel/btf/vmlinux`，工具就会按返回类型过滤掉这类误报。使用带 BTF 感知 retval 的内核时，内核本身就不会为 void 函数打印 `ret=`。

**开了 `funcgraph-args` 但参数没有输出**

在 6.18 等内核上，开着 `funcgraph-retaddr` 时参数不会输出。用 `fgcapture.sh --args` 采集，它会自动处理。

**源码行看起来对不上**

源码版本和 vmlinux 不一致。看一下 ⓘ 信息面板里的 Source 一行，或者用 `--source-rev` 指定构建 vmlinux 时用的那个版本。

**全局视图里有些切片空白，放大后才出现**

旧版本绘制时有这个 bug，已经修复。重新生成报告即可。

**等待链里唤醒者是 `<idle>`**

那次唤醒是从中断里触发的，或者 trace 里只有 `sched_wakeup` 没有 `sched_waking`。采集时用 `--sched`，它会同时开启 `sched_waking`。

**每个 CPU 一份 trace 文件**

直接把它们都传给 funcgraph.py，会按时间戳合并。

**在没有内核源码的机器上看源码**

用 `--source-url` 加 `--source-rev` 从网上获取源码。生成报告时需要网络，下载的文件会缓存；生成的报告本身是单个离线文件。

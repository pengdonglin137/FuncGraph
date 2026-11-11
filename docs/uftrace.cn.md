# uftrace 用户态 / 内核态跟踪

## 当前状态（2026-11-11）

可选的原生采集 / 导出基础已实现，在 QEMU x86_64 Linux **7.4.0-rc2** 和
树莓派 4 Linux **6.18.56-v8-MY_CUSTOM_KERNEL+** 上完成实验。
FuncGraph 支持导入**仅用户态、仅内核态、混合**记录，不强制提供 `vmlinux`，
不需要原生 Python 扩展。现有 ftrace 文本和 trace-cmd 输入仍可使用。
负责保护和恢复 tracefs 状态的 `fgcapture.sh --user` 包装尚未实现。

真实业务示例见 [pigz + zlib 压缩实战](pigz.cn.md)，包含应用和依赖库一起编译的步骤。

## 混合示例：沿 `sendto` 进入内核

用浏览器直接打开 [sample_mixed.html](../sample_mixed.html)，也可以打开
[GitHub Pages 在线示例](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html)。
切到 **Timeline**，保留默认 **Context** 配色，沿
`main → run_round → exchange_loopback → send_frame → dispatch_datagram → sendto → __x64_sys_sendto → … → loopback_xmit` 查看。
点击 `dispatch_datagram`，详情会显示记录的函数地址、五个参数（其中长度为 `512`）和返回值
`512`。点击调用栈里的 `demo_mixed.c:25` 可打开源码。两次 `receive_frame` 分别返回
`512` 和 `-1`；后者在空队列上使用了 `MSG_DONTWAIT`，不要把用户态返回值当作内核原始返回值。

点击 `__x64_sys_sendto`，调用栈会在同一位置分隔两态，顶边细线仅覆盖这次内核调用的时段。
Text 在对应入口旁显示带下划线的 `K`，用户态入口行显示参数和地址，退出行显示返回值。
两种视图均可用 `space:user` 或 `space:kernel` 过滤。

[仓库内的 JSONL](../samples/sample_mixed.jsonl) 取自 2026 年 11 月 11 日在 x86_64 QEMU
Linux `7.4.0-rc2` 上对 [`demo_mixed.c`](../samples/demo_mixed.c) 的实际采集，包含 25 次用户态调用、
1,067 次内核态调用和 5 处匹配的跨态边界。截取时保留了原始调用记录，包括地址、参数和原始
返回值；没有纳入调度 / perf 事件。发布的报告使用了匹配的内核调试镜像：332 个不同的内核
函数均有入口源码位置、固定到 Linux `v7.4-rc2` 的链接和内嵌片段，与记录的用户态源码同时展示。
选中 `__x64_sys_sendto` 可看到 `net/socket.c:2286` 和 **Kernel function source**，也可展开
对应的 Text 行。这是函数入口位置，不是由原始返回地址解析出的调用点；匹配的 BTF 还可
校验非阻塞接收的错误路径。打开 HTML 无需内核源码树或 uftrace。在仓库根目录重建它时，
需要匹配且含调试信息的镜像及源码仓库：

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --func-links --fast --highlight-code \
    --vmlinux /vol_1t/Qemu/linux/x86_64/vmlinux --kernel-src /vol_1t/Qemu/linux \
    --source-rev 7475ea8b58616b33c04afb976cf0fbaad9dd63fb \
    --source-browser github --source-browser-rev v7.4-rc2 \
    --output sample_mixed.html samples/sample_mixed.jsonl
FG_CONFIG=/dev/null python3 samples/make_shots.py  # 重建截图，需要 Playwright + Chromium
```

本地路径请按匹配的构建调整。没有该镜像时，下面的命令仍能重建相同的调用记录、时序、
跨态边界和用户态源码，但无法解析内核源码或执行该镜像的 BTF 类型校验：

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --highlight-code \
    --output mixed-no-kernel-source.html samples/sample_mixed.jsonl
```

## 生成报告

```bash
funcgraph --uftrace CAPTURE --uftrace-bin build/uftrace-x86_64/bin/uftrace \
    --timeline --enable-fold --filter --user-src samples --output mixed.html
# 或读取已经导出的文件，不需要 uftrace 可执行文件：
funcgraph capture.jsonl --timeline --enable-fold --filter --output mixed.html
```

目录输入调用带补丁的 uftrace（默认从 `PATH` 查找），执行 `dump --fgraph --srcline`。
JSONL 按头部自动识别，也可用 `--input-format uftrace` 显式指定，支持 stdin 和压缩输入。
一份报告读取一份记录目录或 JSONL。`--compare BASE` 支持 JSONL 或旧格式 trace；
Compare 与 `--export` 分开统计用户态、内核态的同名函数。

- **混合展示**：Timeline 默认 Context 配色区分两态。分界线画在匹配的内核根调用
  顶边，长度就是该调用的记录时段，深度随实际调用关系变化，不横贯整条轨道。
  Text 用 `U` / `K` 标记，分界处加下划线，调用栈也标出转换。单态报告不画分界线。
- **内核上下文**：跨态挂接不改变内核记录的 flags，也不把它们赋给用户态。Timeline 详情
  根据记录显示关中断、BH 禁用、hardirq / softirq / NMI、调度请求、抢占及迁移计数；
  内核调用的色块取入口状态，Text 按每条记录过滤，例如 `space:kernel AND irqs:d`。
  缺少上下文字段时不补猜。`0x02` 在不同内核版本中含义不同，按保存的内核版本解释。
- **匹配规则**：按线程、地址空间、原生深度及地址配对进出记录；只在核实共用时钟、
  同线程且完整的内核根调用落在记录的用户调用内时合并。这证明时间上的包含关系，
  不证明用户态具体哪条指令进入内核。排序和匹配不使用内核图内部计时器。没有匹配
  的调用保留；时间范围冲突的根调用放在独立轨道。时钟无法核实或不一致时分开展示
  并提示，不应跨态对比时间。
- **源码**：展示记录中已有的用户态文件和行号，绝不套用内核的 `--base-url`，即使函数同名。
  `--user-src DIR` 只嵌入解析后仍位于该目录内的源码；点击调用栈位置或展开 Text 行查看。
  找不到源码或路径越出该目录时只显示记录的位置，不生成外链。使用
  `--no-text-view --keep-source` 可省略 Text DOM，但保留片段。
  内核函数使用 `--vmlinux`、`--kernel-src` 和 `--func-links` 解析入口位置，在 Text 和
  Timeline 内嵌源码片段；指定 `--source-rev` / `--source-url` 时，片段也使用选定的源码提供器。
  内核链接使用对应的浏览器和版本，不混用用户态位置；原始 caller 地址仍按原样保留，
  不伪造调用点或内联链。缺少调试位置或文件时，源码保持不可用。
- **过滤**：启用 `--filter` 后，Text 与 Timeline 共用的 Filter 支持 `space:user` 和
  `space:kernel`；sidecar 报告不必先加载 Text。命令行的调用树预过滤暂不适用于 uftrace。
- **数据缺口**：缺失进出记录或丢失事件会标记，不补造实测帧。用户态 CPU 未知，
  用户态仅在存在支持的标量参数规格时解码，否则保留原始字节；内核返回值展示原始比特，只在 BTF 类型已知时做 errno
  分析。调度分析使用内核 tracepoint，不与 perf 事件重复计入。未投影到报告中的原生
  字段仍保留在 JSONL 中。

## 可复现构建

从 Git checkout 构建，不是在已安装的 Python wheel / sdist 中构建。

```bash
git submodule update --init third_party/uftrace
scripts/uftrace.sh check
JOBS=4 scripts/uftrace.sh build
build/uftrace-x86_64/bin/uftrace --version

CROSS_COMPILE=aarch64-linux-gnu- scripts/uftrace.sh build --arch arm64
```

依赖：编译器、make、pkg-config；解码内核记录需要 libtraceevent 开发头文件和库；
用户态 DWARF 源码行需要 libelf / libdw 开发包。其它可选功能取决于上游检测到的库。
交叉构建除了编译器，还需要正确的**目标架构**库和 pkg-config 配置。
`arm64` 会规范化成上游的 `aarch64`，输出目录使用后者。

脚本检查暂存区中的 submodule 固定版本，从干净版本导出源码，按 `series` 顺序
打补丁，构建目录为 `build/uftrace-src-ARCH`，安装前缀为 `build/uftrace-ARCH`。
两个目录中的任何一个已存在都会拒绝覆盖，重新构建前需要自行移走或删除。
构建时隔离外层 Git 仓库，避免把 FuncGraph 的版本误用作 uftrace 的版本。
安装前缀同时保存上游 pin 和补丁 SHA-256 清单。不进行系统安装或修改 ldconfig。

补丁维护：

```bash
git -C third_party/uftrace worktree add --detach "$PWD/build/uftrace-dev" HEAD
# 编辑前先在 worktree 中用 git am 应用现有 series。
# 按逻辑提交修改，再重新生成整个补丁序列：
scripts/uftrace.sh refresh
scripts/uftrace.sh check
```

`refresh` 要求 worktree 干净、历史线性且基于固定的上游版本；替换旧序列前，
会在另一份干净源码中验证新序列。补丁目的、上游提交状态及移除条件记录在
`patches/uftrace/README.md`。uftrace 是 GPL-2.0；FuncGraph 不链接其代码，Python
发行包也不带它的二进制。分发 uftrace 二进制时仍需履行相应源码和许可证义务，
包括实际使用的补丁；本次集成不擅自给整个项目选择许可证。

## 原生导出格式

```bash
build/uftrace-x86_64/bin/uftrace dump --fgraph --srcline -d CAPTURE > capture.jsonl
# 等价写法：uftrace dump --format=fgraph --srcline -d CAPTURE
```

输出是 JSONL，**不是 ftrace 文本**。首条记录声明
`schema=funcgraph.uftrace.v1`、整数纳秒时间、`order=streams` 和
`depth_basis=address-space`。每个用户线程、每个内核 CPU 保持各自的记录顺序和
原始深度。后续导入器需要按整数时间合并、稳定处理同时间戳，并将同任务的内核
调用接到当时活跃的用户态帧下面。没有对应用户态帧的内核活动保留为根，不能
强行挂到 main；导出器没有时间对齐修正或伪造入口 / 出口补全。

- 函数记录：`type=entry|exit`、`space=user|kernel`、`time_ns`、`tid`、`cpu`、
  `depth`、十六进制 `address` 和 `name`；用户态还带 `pid` / `comm`，有 DWARF
  位置时带 `source={file,line}`。不知道的用户态 CPU 是 `null`。
- 内核上下文：`common_flags`、`common_preempt_count`；可选的十六进制
  `retaddr`、`retval`、`args` 数组，以及整数 `calltime_ns`、`rettime_ns`、
  `overrun`。不存在的字段不输出；空数组与缺失数组不同。固定、柔性、dynamic、
  relative 参数数组均按保存的 format 和字节序校验，畸形数组导致导出失败，
  不会静默降级为“没有参数”。
  graph 内部 call / return 计数器不保证与记录时间戳有相同的时钟原点；不能用它们
  对混合记录排序或对齐。排序依据是记录的 `time_ns`，两种内部计数器单独保留。
- `retval` 是**未经类型解释的原始位模式**，不等于已确认的 errno、有符号整数
  或指针。字段存在不代表采集开关生效，必须结合 `kernel_settings` 的实际读回值；
  不伪造 BTF 类型或返回值来源。
- 内核 tracepoint 的 `event` 保存原始 **common PID**、事件名、格式化 payload
  和 flags，包括在其它任务上下文发生的调度事件。存在用户事件 / 参数 payload
  时，以 `payload_hex` 保存原始字节。支持的 8/16/32/64 位有符号、无符号、十六进制和指针
  规格会附加 `user_args`（入口字符串列表）或 `user_retval`（出口字符串）。使用字符串保留
  64 位精度；规格不受支持或不完整时保留原始 payload，不猜测。
- perf `event` 标记 `origin=perf`、`space=null`、未知 `cpu=null`，并带不透明的
  stream 索引及可用的调度、task、comm 字段。导入器不能将 perf 调度和等价的
  内核调度事件重复计数。
- `lost` 保留丢失数量。内核页丢失属于 CPU，`tid=null`，不能归因于该 CPU 随后
  恰好出现的任务。
- `clock` 来自新采集生成的 `funcgraph-clock`；旧数据缺少此文件时为 `null`。
  `kernel_settings` 只来自保存的 `kernel-funcgraph.info`，绝不读取导出机器当前
  内核来补猜。`trace_clock` 中用方括号标记实际使用的内核时钟。
- 正常完成时最后一条是 `{"type":"end","complete":true}`。结束记录缺失或
  为 false 不能当作完整导出；内核解码错误返回失败。即使所有已保存的字节都
  导出成功，采集时已经发生的丢失仍然需要展示。

## 采集安全与限制

`uftrace record --kernel-full-info` 尝试启用可用的 args / retval / retaddr
选项和 sched_switch、sched_waking、sched_wakeup；缺失能力会如实标记，不做模拟。
上游的 reset 逻辑恢复的是默认值，**不是采集前的 tracefs 设置**。
在保留状态的采集封装完成前，只能在空闲的专用实验环境中使用，并明确快照 / 恢复
原设置；不要打断正在工作的跟踪会话。恢复设置也无法恢复已经消耗的 trace buffer。

样例程序用 `-pg -g -O0 -fno-omit-frame-pointer` 动态编译 `samples/demo_io.c`，
使用私有输入输出文件、统一的 `--clock=mono`、合适的内核 buffer 和对应架构的
syscall 根过滤。范围过宽、深度过大的采集可能丢失。当前可连接的 Pi 不存在旧的
`/vol_1t/raspi/linux` 路径，本次从本仓库复制样例源码进行原生构建。

混合示例改为用相同参数编译 `samples/demo_mixed.c`。空闲实验环境中对每个 syscall 根
分别使用精确名称，并通过 `-A`/`-R` 指定用户态参数、返回值规格。完整采集命令见
[英文版示例](uftrace.md#capture-safety-and-current-limits)；压缩实战的完整命令见
[pigz 指南](pigz.cn.md)。

QEMU 7.4 上 args 和 retaddr 可以一起记录，不能据此外推老内核；Pi 6.18 的实际
格式 / 能力单独验证。导出保留原始深度和 CPU，不宣称全局排序或有界内存。

## 验证

```bash
FG_UFTRACE_REQUIRED=1 PYTHONPATH=tests python3 -m unittest \
    test_uftrace_build test_uftrace_export -v
python3 tests/verify_uftrace_records.py CAPTURE capture.jsonl
```

自动化测试无需 root / tracefs：采集本机插桩程序的用户态记录，再注入独立生成的
内核页，覆盖两种字节序、32 / 64 位字长、普通 / retaddr 入口、四种参数布局、
缺失返回值、稀疏 CPU、common PID、损坏参数、未知事件和显式丢失。
CI 构建固定版本及补丁，并强制运行这些测试；普通 Python 测试环境可跳过可选
原生程序。

离线核对脚本独立解码同一次采集保存的 ring-buffer 页，逐条比较导出记录的
时间、CPU / PID、类型、深度、完整地址、flags、preempt count 和可选原始字段。
不是在 uftrace 已经读空 buffer 后拿 `cat trace` 冒充同次采集的基准；
tracefs 设置恢复核对和记录字节核对也分别进行，不混为一个结论。

### 真实采集证据（2026-11-11）

| 环境 | 独立逐条核对的内核记录 | 实际上下文 | 恢复设置 |
| --- | ---: | --- | ---: |
| QEMU x86_64 / 7.4.0-rc2 | 79,118：39,539 entry + 39,539 exit + 40 event | args + retaddr + retval | 74 / 74 |
| Pi 4 aarch64 / 6.18.56-v8-MY_CUSTOM_KERNEL+ | 317,693：158,761 entry + 158,695 exit + 237 event | retaddr + retval；实际记录无 args | 68 / 68 |

两份数据均使用 mono，各有 15 个用户态入口和 15 个出口，main 定位到
`demo_io.c:22`。main 内十条 syscall 根入口 / 出口均位于各自的用户态
read / write / sendto / recvfrom 包装函数之内，没有进行时钟修正；调度及 perf
事件也保留。这两次限定范围的采集中未观察到显式页丢失或非零 graph overrun，
不代表宽范围采集不会丢失。设置恢复核对针对已完成的实验采集，正式封装的失败 /
取消恢复仍待实现。

Pi 的原始记录有 66 个额外内核入口，简单的单栈核对还会遇到中断上下文不连续。
字节级完全一致**不代表调用树完整**，导入器必须保留不完整和中断上下文差异，
不能伪造已测量的返回。老内核虽然读回 args=1，retaddr 格式记录中却没有参数；
展示能力必须以实际记录为准，不能只看请求选项或选项读回值。

实验还发现上游 kallsyms 问题：较低地址的模块组出现在核心符号后面，输入顺序
计算的函数区间发生下溢，导致 `__arm64_sys_read` 被错误标记成
`vc4_cec_irq_handler_rx_bare`。补丁 0004 在地址排序后重算区间；对应回归测试在
修复前失败、修复后通过。原始函数地址保持不变，始终是核对依据。

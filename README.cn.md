# FuncGraph - 专业 Linux ftrace 可视化工具

![FuncGraph 可视化示例](sample.png)

## FuncGraph 是什么？

FuncGraph 将 `function_graph` 的 ftrace 输出转换为交互式、可过滤的 HTML 报告，适用于内核开发与性能排查：支持源码跳转、高速地址解析、参数/耗时过滤与键盘友好折叠。

## 速查（Cheat Sheet）

- 常用选项：`--fast`、`--filter`、`--func-links`、`--highlight-code` 🔧
- 过滤语法：`cpu:3 AND duration:>10 sort:desc` ⏱️
- 调用树过滤：`graph:funcname`（显示调用树）、`notrace:funcname`（隐藏调用树）🌲
- Latency 过滤：`irqs:d AND hirq:h`（仅限 latency 模式 trace）🔎
- 键盘：Tab 聚焦折叠图标；Enter 切换折叠；Esc 清除选择 ⌨️

## 主要特性

- 交互式 ftrace 可视化与折叠/展开
- 可点击源码链接（在线或本地），支持高亮选项
- 高性能地址解析（`fastfaddr2line.py`）
- **统一过滤器**：`field:value` 语法，支持 AND/OR/NOT 逻辑运算，字段按钮下拉选择
- **调用树过滤**：`graph:func` / `notrace:func` 显示/隐藏完整调用树（等效 ftrace 的 `set_graph_function` / `set_graph_notrace`）
- **Latency 字段支持**：解析 irqs-off、need-resched、hardirq/softirq、preempt-depth
- 键盘友好导航与可访问折叠控制
- **时间轴标签页**（`--timeline`）：按任务的时序火焰图，按上下文、关中断、调度延迟、off-CPU 和错误路径配色

---

<!-- Installation & Usage continues below -->

## 安装与使用

### 需求

- Python 3.6+
- addr2line（来自 binutils）
- pyelftools（`--fast` 需要）
- 可选：Pygments（用于语法高亮）

### 基本用法

```bash
python3 funcgraph.py trace.txt \
    --vmlinux /path/to/vmlinux \
    --kernel-src /path/to/kernel/src \
    --module-dirs /path/to/modules \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --filter \
    --fast \
    --highlight-code \
    --output result.html
```

### 参数说明

下表文档了 `funcgraph.py` 支持的每个命令行选项，包括类型、默认值和示例。

| 参数 | 类型 / 默认值 | 说明 | 示例 |
|------|----------------|------|------|
| `ftrace_file` | 路径（必需） | 要解析和可视化的 ftrace 输出文件路径 | `trace.txt` |
| `--vmlinux` | 路径（必需） | vmlinux 二进制文件路径（用于地址 -> 源码解析） | `--vmlinux /path/to/vmlinux` |
| `--kernel-src` | 路径 | 内核源码树根目录（用于本地源码链接和语法高亮） | `--kernel-src /usr/src/linux` |
| `--module-dirs` | 路径... | 搜索内核模块的目录（可指定多个） | `--module-dirs /lib/modules /usr/lib/modules` |
| `--module-srcs` | 路径... | 模块源码目录用于模块特定的源码链接 | `--module-srcs /path/to/module/src` |
| `--base-url` | URL | 在线源码链接的基础 URL（如 Bootlin） | `--base-url https://elixir.bootlin.com/linux/v6.18/source` |
| `--module-url` | url:mods（可追加） | 将特定模块映射到源码 URL；格式：`url:mod1,mod2`。可指定多次。 | `--module-url https://url1.com:modA,modB` |
| `--output` | 路径（默认：`ftrace_viz.html`） | 生成的 HTML 输出文件路径 | `--output result.html` |
| `--auto-search` | 标志 | 自动搜索常见模块目录（添加常见的 `/lib/modules` 路径） | `--auto-search` |
| `--verbose` | 标志 | 启用详细日志用于调试和诊断 | `--verbose` |
| `--fast` | 标志 | 使用捆绑的 `fastfaddr2line.py` 更快处理 vmlinux | `--fast` |
| `--use-external` | 标志 | 强制使用外部 `faddr2line`/`addr2line`（与 `--fast` 互斥） | `--use-external` |
| `--highlight-code` | 标志 | 启用 HTML 中 C 源码语法高亮（需要 Pygments） | `--highlight-code` |
| `--path-prefix` | 路径... | 在将 addr2line 路径映射到 kernel-src 时要删除/替换的路径前缀 | `--path-prefix /home/user/build/kernel` |
| `--filter` | 标志 | 在 HTML 中包含统一过滤器（支持 `field:value` 查询与 AND/OR/NOT） | `--filter` |
| `--func-links` | 标志 | 为函数名添加可点击的源码链接（添加一些处理开销） | `--func-links` |
| `--entry-offset` | int（默认：`0`） | 要添加到函数入口地址的偏移（对打补丁的函数有用） | `--entry-offset 8` |
| `--enable-fold` | 标志 | 启用函数调用折叠 UI（折叠/展开调用块） | `--enable-fold` |
| `--timeline` | 标志 | 增加 Timeline 标签页（按任务的时序火焰图，见下文） | `--timeline` |
| `--btf` | 路径... | 用于判断返回类型的 BTF 来源：`/sys/kernel/btf/*` 的副本或 ELF 文件（默认用 vmlinux/模块的 `.BTF` 段） | `--btf vmlinux.btf` |
| `--no-btf` | 标志 | 判断错误路径时不使用 BTF | `--no-btf` |

**说明：**
- `--fast` 和 `--use-external` 不应同时使用；如果都指定了，`--use-external` 会被忽略。
- `--module-url` 支持纯 URL（对所有模块默认）或 `url:mod1,mod2` 映射；重复该标志以添加多个映射。

### 模块 URL 示例

```bash
# 为不同的模块设置不同的 URL
python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --module-url https://url1.com:mod1,mod2 \
    --module-url https://url2.com:mod3,mod4 \
    --module-url https://default.com \
    --filter --fast
```

规则：
- `mod1,mod2` → 使用 `https://url1.com`
- `mod3,mod4` → 使用 `https://url2.com`
- 其他模块 → 使用 `https://default.com`
- 无默认 URL → 使用 `--base-url`

### 路径前缀处理

```bash
# addr2line 返回: /home/user/build/kernel/fs/open.c
# 内核源码路径: /home/user/linux/fs/open.c

python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --kernel-src /home/user/linux \
    --path-prefix /home/user/build/kernel \
    --filter --fast
```

### 交叉编译与 LLVM

**交叉编译：**
```bash
export CROSS_COMPILE=aarch64-linux-gnu-
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

**LLVM 工具链：**
```bash
export LLVM=1
# 或 export LLVM=/usr/bin/
# 或 export LLVM=-10
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

## 时间轴视图（`--timeline`）

`--timeline` 会在文本视图旁增加一个 **Timeline** 标签页。每个任务一条轨道，轨道内横轴是时间、纵轴是调用深度，切片宽度即耗时（类似 Android Studio 的 method trace 或 Perfetto，数据来自内核 function_graph）。数据内嵌在同一个 HTML 中，用 Canvas 绘制，十几万次调用也能流畅缩放。

显示内容：
- **上下文配色**（来自 latency 标志）：任务（蓝）、softirq（绿）、hardirq（橙）、NMI（紫）。斜线表示关中断；琥珀色横条/角标表示 need-resched 已置位但尚未调度（调度延迟窗口）；可选显示 preempt 深度条。
- **Off-CPU 时段**：根据上下文切换标记生成，标注任务在哪个函数里等待（如 `mutex_lock`），以及是被抢占还是阻塞。
- **错误路径**（需 `funcgraph-retval`）：错误源头（第一个返回 errno 的函数）若一直传到用户态（链上有 `*_sys_*`）显示为实心红色，被调用者处理掉的显示为橙色；传播帧加红色描边。返回类型从 BTF 读取，因此 void 函数（寄存器残值）、无符号和 bool 返回值不会误报，指针返回值按 `IS_ERR` 语义判断。
- **调用点**（需 `funcgraph-retaddr`）；紫色角标表示调用者与父切片不一致（中间有未跟踪、内联或汇编帧）。
- 配色模式：上下文、函数名、自身耗时、CPU、模块。

交互：`W`/`S` 或 Ctrl+滚轮缩放，`A`/`D` 或拖动平移，双击放大到切片，`E`/`Shift+E` 下一个/上一个错误，`/` 搜索，`Shift`+拖动汇总一段时间（按函数统计自身/总耗时、上下文占比、off-CPU 时间），`Enter` 在文本视图中打开选中调用，在文本行上 `Alt`+点击可跳到时间轴。在时间轴中按 `?` 查看完整说明。

工具会根据 trace 实际开启的选项自动降级，并在页面上提示缺了什么：

| 缺少的选项 | 对时间轴的影响 |
|---|---|
| `funcgraph-abstime` | 没有真实时间轴，切片按耗时首尾相接（压缩时间） |
| `latency-format` | 上下文根据中断/软中断入口函数名推断；没有关中断、need-resched、preempt 叠加层 |
| `funcgraph-proc` | 根据上下文切换标记重建任务，否则每个 CPU 一条轨道 |
| `funcgraph-retval` | 没有错误路径 |
| `funcgraph-retaddr` | 没有调用点 |
| `funcgraph-duration` | 耗时取自下一行时间戳（标记为估算） |

BTF：默认读取 `--vmlinux`（需 `CONFIG_DEBUG_INFO_BTF=y`）以及 `--module-dirs` 中找到的模块的 `.BTF` 段。如果 vmlinux 没有 `.BTF`，可从目标机复制 `/sys/kernel/btf/vmlinux` 并用 `--btf vmlinux.btf` 指定。`--no-btf` 关闭检查（此时只能根据返回值猜测错误）。

### 为时间轴采集 trace

```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
echo 1 > options/funcgraph-abstime    # 真实时间轴（显示空隙和 off-CPU 必需）
echo 1 > options/latency-format       # irqs-off / need-resched / hardirq / softirq / preempt 标志
echo 1 > options/funcgraph-proc
echo 1 > options/funcgraph-retval
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-tail       # 每个 '}' 后打印函数名（配对更可靠）
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
```

注意：
- 开启 `funcgraph-retaddr` 后，6.18 等内核即使设置了 `funcgraph-args` 也不会打印参数。需要参数时另外采一次，不开 `funcgraph-retaddr`。
- 在没有合入 BTF 打印优化的内核上（如 6.18），开启 `funcgraph-args` 后读取 `trace` 非常慢（树莓派 4 实测约 36 ms/行）。务必先用 `set_graph_function`、`set_ftrace_filter` 或 `set_ftrace_pid` 缩小范围。
- 可选开启 `events/sched/sched_switch` 和 `sched_wakeup`，时间轴会显示换出时的任务状态和唤醒者。
- 出现 `LOST EVENTS` 时请增大 `buffer_size_kb`。

## 抓取 Trace

### 推荐设置

```bash
cd /sys/kernel/tracing

# 停止当前追踪
echo 0 > tracing_on

# 启用推荐选项
echo 1 > options/funcgraph-retaddr    # 返回地址（必需）
echo 1 > options/funcgraph-proc       # 进程名和 PID
echo 1 > options/funcgraph-retval     # 返回值
echo 1 > options/funcgraph-args       # 函数参数

# 设置 tracer
echo function_graph > current_tracer

# 启动追踪（1 秒）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# 保存结果
cat trace > ~/ftrace.txt
```

### 选项说明

| 选项 | 用途 | 推荐 |
|------|------|------|
| `funcgraph-retaddr` | 提供源码映射的返回地址 | ⭐⭐⭐⭐⭐ |
| `funcgraph-proc` | 显示进程名和 PID 用于过滤 | ⭐⭐⭐⭐ |
| `funcgraph-retval` | 显示函数返回值用于调试 | ⭐⭐⭐⭐ |
| `funcgraph-args` | 显示函数参数用于分析 | ⭐⭐⭐⭐ |

## Fastfaddr2line 工具

### 独立使用

```bash
# 显示帮助
python3 fastfaddr2line.py -h

# 解析单个地址
python3 fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-f, --functions` | 显示函数名 |
| `-s, --basenames` | 仅显示文件名（无路径） |
| `-i, --inlines` | 显示内联函数 |
| `-p, --pretty-print` | 美化输出 |
| `-C, --demangle` | C++ 符号解析 |
| `--path-prefix` | 路径前缀替换 |
| `--module-srcs` | 模块源码目录 |
| `--entry-offset` | 入口地址偏移 |

## 使用示例

### 1. 抓取 Trace
```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-proc
echo 1 > options/funcgraph-retval
echo 1 > options/funcgraph-args
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
```

### 2. 生成 HTML
```bash
cd /vol_1t/Qemu/x86_64/funcgraph_visualization

python3 funcgraph.py ~/ftrace.txt \
    --vmlinx /path/to/vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --filter \
    --fast \
    --output result.html
```

### 3. 在浏览器中

打开 `result.html` 使用统一过滤器：
- **查找最慢的函数**：`duration:>0 sort:desc`
- **查找异常**：`duration:>100 OR duration:<0.1`
- **特定进程**：`pid:1234 OR pid:5678`，`comm:nginx OR comm:bash`
- **组合过滤**：`cpu:0 AND pid:1234 AND duration:>5`
- **调用树过滤**：`graph:schedule`（显示 schedule 的调用树）、`notrace:rcu_read_lock`（隐藏 rcu 调用树）
- **函数名匹配**：`func:mutex`（仅匹配函数名，不含子调用树）
- **组合过滤**：`graph:vfs_read AND cpu:0`（调用树过滤 + 其他条件）
- **Latency 模式**：`irqs:d AND hirq:h`（仅限 latency 模式 trace）
- **鼠标操作**：点击字段按钮（cpu、pid、graph、notrace…）打开下拉选择器
- **折叠调用**：点击函数入口行前的 `▶` 图标可折叠/展开调用块

#### 支持的过滤字段

| 字段 | 属性 | 运算符 | 示例 |
|------|------|--------|------|
| `cpu` | data-cpu | 正则 | `cpu:0`，`cpu:0\|1` |
| `pid` | data-pid | 正则 | `pid:1234` |
| `comm` | data-comm | 正则 | `comm:bash` |
| `ret` | data-ret | 正则，`all` | `ret:all`，`ret:-22` |
| `params` | data-params | 正则 | `params:folio` |
| `duration` | data-duration | `>`、`<`、`>=`、`<=` | `duration:>10` |
| `func` | data-func | 正则 | `func:schedule` |
| `graph` | data-func + fold 层次 | 正则 | `graph:vfs_read`（显示调用树） |
| `notrace` | data-func + fold 层次 | 正则 | `notrace:rcu_read_lock`（隐藏调用树） |
| `irqs` | data-irqs | 字符 | `irqs:d`（disabled） |
| `resched` | data-resched | 字符 | `resched:N` |
| `hirq` | data-hirq | 字符 | `hirq:h`（hardirq） |
| `preempt` | data-preempt | `>`、`<`、`>=`、`<=` | `preempt:>1` |


## 项目结构

```
funcgraph_visualization/
├── README.md                    # 英文说明文档
├── README.cn.md                 # 中文说明文档
├── funcgraph.py                 # 主程序
├── fastfaddr2line.py            # 地址解析工具
├── timeline.py                  # 时间轴模型（按任务重建调用切片、错误路径分析）
├── btf.py                       # BTF 解析（函数返回类型）
├── assets/                      # 时间轴 JS/CSS，生成时内联进 HTML
├── tests/test_timeline.py       # 时间轴不变量测试
├── ftrace.txt                   # 示例数据
└── sample.html                  # 示例截图
```

## 参考资料

- [ftrace 可视化工具](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace 可视化工具（续）](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [写了个 ftrace 可视化工具](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## 许可证

开源项目，欢迎贡献！

---

**版本**: v0.7
**最后更新**: 2026-03-08
**状态**: ✅ 生产就绪

# 二进制 raw 输入

FuncGraph 现支持 **trace-cmd 的 `trace.dat` 容器**，按文件魔数而非扩展名识别。容器包含解码所需的事件格式、字节序、字长、时钟和符号元数据。**不支持独立的 `trace_pipe_raw` 裸页流**：改名为 `.dat` 不会补齐这些信息。`trace-cmd report -R` 的逐事件字段文本、perf.data 和 Perfetto protobuf 也不是 function_graph 文本输入。

English：[raw-input.md](raw-input.md)。

## 使用方法

采集端可通过 trace-cmd 保存 function_graph 数据。例如，在没有其它 tracing 会话的实验环境中以 root 运行：

```bash
trace-cmd record -p function_graph -C mono -g vfs_read \
  -e sched:sched_switch -e sched:sched_waking -o trace.dat -- cat /etc/hostname
```

采集命令会修改 tracefs；已有 tracing 会话时不要直接覆盖，应使用隔离 instance 的采集流程。FuncGraph 本身只读取文件，不需要 root，也不修改 tracefs。

分析端安装下述可选依赖后：

```bash
funcgraph trace.dat --timeline --enable-fold --output trace.html
funcgraph trace.dat --timeline --pid 1234 --export analysis.json --output trace.html
funcgraph trace.dat --timeline --compare baseline.dat --output compare.html
funcgraph trace.dat.gz --timeline --output trace.html
cat trace.dat | funcgraph - --timeline --output trace.html
```

默认 `--input-format auto` 自动识别；`raw` 强制要求 trace-cmd 容器，`text` 遇到二进制容器则明确报错。`--compare` 的文件独立自动识别，可以混合对比 raw 和文本。

默认读取顶层 buffer。`--raw-instance NAME` 选择一个命名 buffer，也用于 raw 格式的比较基线，不会静默合并多个 instance。当前 buffer 没有调用或调度事件时，错误会列出可选实例。

支持 `.gz/.xz/.lzma/.bz2` 和二进制标准输入。压缩容器与 stdin 会先写入私有临时文件，以满足 libtracecmd 的随机访问要求；多输入按纳秒时间戳合并。`fgreport trace.dat -- --raw-instance NAME` 可透传参数；它不会把二进制中的换行字节当作文本行数。大型 raw 报告请显式预过滤或使用 `--no-text-view`。

## 可选依赖

普通文本输入不需要新增依赖。raw 分析端需要：

- 本机 C 编译器（`CC`，默认 `cc`）、`pkg-config`。
- libtracecmd **>= 1.5.0**、libtraceevent、libtracefs 的开发头文件及共享库。
- 系统动态链接器或 `LD_LIBRARY_PATH` 能找到对应共享库。

```bash
pkg-config --modversion libtracecmd libtraceevent libtracefs
```

每个进程首次读取 raw 时，在私有临时目录编译随包提供的 `fglib/native/raw.c` 小桥接器；Compare 复用同一进程内的程序，退出时清理。不会自动联网下载依赖，也不写持久可执行缓存。wheel/sdist 包含 C 源码，不携带绑定架构的二进制或上游库。即使通过 `CROSS_COMPILE` 分析 ARM trace，桥接器仍应使用**分析主机的编译器**。

缺依赖、缺编译器、不支持的格式、损坏数据或解码进程失败，会在发布 HTML 前明确报错；不会把部分解码输出当作成功，也不会覆盖已有 HTML。子进程隔离不是安全沙箱，应使用可信采集文件和维护中的库。打开容器时禁用 trace-cmd/libtraceevent 插件加载。

### 使用固定版本 submodule

仓库固定到官方上游、未修改的提交：

| 子模块 | 版本 | 提交 |
|---|---|---|
| `third_party/libtraceevent` | 1.9.0 | `13701b5532e0c3295bf5670361692b0d0044228d` |
| `third_party/libtracefs` | 1.8.3 | `6fad6a14ba0d4c4b437d9e4eed7098d4bb07b4fc` |
| `third_party/trace-cmd` | trace-cmd 3.4 / libtracecmd 1.5.5 | `3ce20923b3efa60d417da7acc8a327615fbd1419` |

各库保留自身 `LICENSES`、`COPYING` 或 `COPYING.LIB` 中的许可。Python 发布包不内置这些库。文本用户不必初始化子模块；通过 sdist 安装后若想构建这些依赖，需要另取 Git checkout，sdist 不包含子模块源码。

Ubuntu 24.04 上的依赖与构建示例：

```bash
sudo apt-get install build-essential pkg-config flex bison linux-libc-dev libzstd-dev zlib1g-dev
git submodule update --init --recursive
bash third_party/build.sh "$HOME/.local/funcgraph-raw"
export PKG_CONFIG_PATH="$HOME/.local/funcgraph-raw/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$HOME/.local/funcgraph-raw/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

构建脚本只向指定绝对路径安装头文件/库，不使用 sudo，并禁用上游安装中的系统 ldconfig 修改。libtraceevent 1.9 要求包含 `struct btf_enum64` 的较新、**已导出** Linux UAPI 头文件；旧系统可更新 linux-libc-dev，或构建时设置 `EXTRA_CFLAGS=-I/已导出头文件/include`。不能直接把未经清理的内核 `include/uapi` 当作完整用户态头文件。库构建时的 zstd/zlib 支持决定可读的内部压缩容器类型。

桥接器通过 libtraceevent 按字段读取，不用 ctypes 猜测结构体 ABI。时钟检查使用 libtracecmd 导出的 `tracecmd_get_trace_clock`：固定版本的已安装公共头文件未声明该接口，因此桥接器保留其函数声明。升级子模块时必须重新核对该接口并运行原生测试。

## 保留的信息与边界

- `funcgraph_entry`、`fgraph_retaddr_entry`、`funcgraph_exit` 归一化为现有入口/出口文本，接入 Text、Timeline、预过滤、Compare 和导出。源码链接仍需要匹配的 `--vmlinux`/模块文件。保留符号和模块归属；没有符号时显示 `unresolved_0x地址`，不丢弃调用。
- 时间戳保留**纳秒**精度，原六位微秒文本不变。调用 duration 使用已记录的 `rettime - calltime`；采集若排除了睡眠时间，它不一定等于墙钟跨度。按元数据处理 32/64 位字长和大小端。
- 参数为 `arg0=0x...` 等原始寄存器值，不冒充 BTF 格式化的 C 参数，也不解引用字符串；支持尾随数组和 data-location 数组。返回值为未类型化的十六进制位模式；缺少字段时不会伪造 args/retval 能力。判断错误码、指针、符号性或 void 返回仍需要匹配的 BTF/vmlinux。
- `retaddr` 是记录的**返回 PC**，caller offset 使用该 PC；符号长度来自下一条 kallsyms 地址边界，不是重新测量的代码长度。不臆造架构相关的调用指令偏移修正。
- 调度切换/唤醒保留任务、CPU、状态及时间戳。额外的 switch-in 上下文行使恢复边界等于真实切换时间，而不是下一条函数事件。丢事件保留计数，未知数量仍为 `?`/JSON null；graph overrun 以明确注释显示。
- 当前支持 local/global/mono/mono_raw/boot/tai/uptime/perf 纳秒时钟；没有保存时钟的旧容器按 reader 的 local 约定处理。counter/cycle 时钟（含 x86-tsc）暂不支持，建议采集时用 `-C mono`。local 时钟原有的跨 CPU 同步限制仍存在。
- 其它事件保留为可读注释，不代表都有专门分析能力。这是**完整文件解码/归一化**，不是实时读取 trace_pipe_raw、常量内存处理、Text 虚拟化或 sidecar。Text 行号指生成的事件行，不是二进制文件偏移。

CLI 使用的 `inputs.read_lines()` 直接保留原生解码器生成的归一化行列表，压缩输入、stdin 以及混合多输入中的每个 raw 文件均走此路径，不再把整份列表拼成完整 trace 字符串再重新拆行。公开的 `inputs.read_text()` API 在调用者明确需要字符串时，仍为 raw 输入返回以换行符结尾的文本。这只消除加载时的冗余复制；完整归一化 trace 和分析模型仍保存在内存中。

## 验证

`PYTHONPATH=tests FG_RAW_REQUIRED=1 python3 -m unittest test_raw -v` 强制要求原生依赖存在，不允许跳过。离线合成容器覆盖大小端/字长、亚微秒 duration、参数、返回值、retaddr、模块归属、调度、丢事件、非法时钟/字段和截断；CLI 覆盖比较/导出。安装态 smoke 测试支持 `--with-raw`。CI 在 Ubuntu 24.04 构建固定版本库，测试 wheel/sdist 后才允许后续打包/Release。

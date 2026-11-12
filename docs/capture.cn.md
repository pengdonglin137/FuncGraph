# 统一采集入口：fgcapture

`fgcapture` 保留原来的纯内核文本采集，并通过同一个入口提供仅用户态和混合采集。
`fgcapture.sh` 保持兼容，也接受 `--user` / `--mixed`（需要安装 Python 包）。
不再需要为常规混合采集手工拼接 uftrace 的时钟、调用点、参数、调度和生命周期选项。

只想先体验已有记录，无需准备采集环境：见[离线样例、预设、对比与大报告案例](examples.cn.md)。

## 选择安装包与采集模式

从 GitHub Release 下载与采集机架构匹配的 wheel，通过本地文件安装；本项目的发布
流程不上传 PyPI。将下例的 `<version>` 替换为实际下载版本：

```bash
uname -m
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fgcapture --help
fguftrace --version
```

| 安装包 / 场景 | 选择与依赖 |
| --- | --- |
| x86_64 Linux 采集机 | `linux_x86_64.whl`，内含带补丁的 uftrace 和五种 libmcount |
| ARM64 Linux 采集机 | `linux_aarch64.whl`，同样内含采集程序和库 |
| Python-only wheel / 源码包 | 不含编译后的 uftrace；用户 / 混合采集需另行构建或提供带补丁的版本 |
| 只读取已导出的 JSONL | 不需要 uftrace；只解析调用关系也不要求 vmlinux |

原生 wheel 不是完全静态程序；Ubuntu 上需要 `libtraceevent1`、`libelf1`、`libdw1`、
`liblzma5`、`libstdc++6` 和兼容 glibc。详细要求见
[安装与架构说明](uftrace.cn.md#安装和选择采集程序)。`[all]` 安装 Python 可选依赖，
不会额外安装系统共享库。

| 想观察什么 | 命令入口 | 采集要求 | 输出 |
| --- | --- | --- | --- |
| 已有程序的内核调用，不重新编译程序 | `sudo fgcapture --kernel ... -- CMD` | Linux、root、Bash、tracefs；不需要 uftrace | 文本文件 |
| 应用及已插桩库的用户函数 | `fgcapture --user ... -- CMD` | 带补丁的 uftrace、插桩及调试信息；无需 root / tracefs | uftrace 目录 |
| 同次运行的用户函数、内核调用及生命周期 | `sudo fgcapture --mixed ... -- CMD` | 用户模式要求，加 root 和空闲专用 tracefs | uftrace 目录及旁边的恢复证据 |

`fgcapture`、`fguftrace` 和 `funcgraph --uftrace DIR` 优先使用内置采集程序及配套库；
`--uftrace-bin PATH` 仅为手动覆盖。下面的命令从 FuncGraph 仓库根目录执行；只安装
wheel 时先取得本仓库的 `samples/demo_mixed.c`，wheel 不会向当前目录生成样例源码。

## 最短流程

安装带内置 uftrace 的 Linux wheel；Python-only 包需要 PATH 上带补丁的 uftrace。
默认使用内置程序及对应 libmcount，`--uftrace-bin PATH` 只是可选覆盖。

```bash
# x86_64；ARM64 将 -pg 替换为 -fpatchable-function-entry=2
cc -pg -g -O0 -fno-omit-frame-pointer -o demo_mixed samples/demo_mixed.c
printf 'FuncGraph demo\n' > demo-input.txt
: > demo-output.txt

# 不需要 root，也不访问 tracefs
fgcapture --user -o user.data -- ./demo_mixed demo-input.txt demo-output.txt

# 同一次运行的用户态 + 内核态；只在空闲的专用实验机执行
sudo "$(command -v fgcapture)" --mixed -o mixed.data -- ./demo_mixed demo-input.txt demo-output.txt

# 离线 HTML；不需要额外传 uftrace 路径
funcgraph --no-config --uftrace mixed.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output mixed.html
```

`demo_mixed` 的输出文件必须预先存在：程序以 `O_WRONLY` 打开它，不会自行创建。
一次运行完成后用 `cmp demo-input.txt demo-output.txt` 核对文件复制结果。重新采集请
使用新目录，并在重用输出文件前再次执行 `: > demo-output.txt`，避免残留旧文件尾部。

用户源码根要对应**导出记录中的路径**，不是固定指向源码文件的父目录。本例记录的是
`samples/demo_mixed.c`，所以在仓库根目录使用 `--user-src "$PWD"`；传 `samples`
会错误查找 `samples/samples/demo_mixed.c`。即使编译时传绝对路径，uftrace 也可能
将它保存为相对路径。已有 `sample_mixed.jsonl` 中的路径是 `demo_mixed.c`，重建它时
则继续用 `--user-src samples`。绝对记录路径必须位于允许的源码根内；复制目录不会
自动重写它们。最终以 JSONL 的 `source.file` / 调用点源码路径为准。

## 案例一：从用户态文件复制到 UDP 调用

最短流程中的程序同时包含文件复制和 UDP 回环，能展示多层用户调用：

```text
main → run_round → transfer_file → read_block / write_block
                 → exchange_loopback → send_frame → dispatch_datagram → sendto
                                     → receive_frame → recvfrom
```

先仅执行上面的 `fgcapture --user`，然后生成用户态报告：

```bash
funcgraph --no-config --uftrace user.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output user.html
# 需要保存可搬运的文本导出时：
fguftrace dump --fgraph --srcline -d user.data > user.jsonl
```

打开 `user.html`，在 Timeline 查找 `dispatch_datagram`：详情应能展示 `fd`、`buffer`、
`length`、`peer`、`peer_size` 五个参数，调用点为 `send_frame+0x…/0x…`，而不是把
被调函数入口当作调用点。参数名来自可用的用户 DWARF / 采集规格，不是内核 BTF。
选中两次 `receive_frame`，应看到不同调用行；第二次非阻塞接收预期返回 `-1`，
`errno` 为 `EAGAIN`，不代表整个示例失败。`void`、无参或无法解码的 ABI 不会凭空补值。
打开 Text 的源码行，或详情中的源码片段，检查用户态路径没有链接到内核仓库。

## 案例二：同次运行的 exec → 用户态 → 内核态 → exit

先完成编译和输入准备。在空闲的专用 VM 上选择较小的内核范围：

```bash
case "$(uname -m)" in
    x86_64) syscall_prefix=__x64_sys ;;
    aarch64|arm64) syscall_prefix=__arm64_sys ;;
    *) printf '本示例只覆盖 x86_64 / ARM64\n' >&2; exit 1 ;;
esac
: > demo-output.txt
sudo "$(command -v fgcapture)" --mixed --kernel-depth 4 \
    -g "${syscall_prefix}_sendto,${syscall_prefix}_recvfrom,do_execveat_common,do_exit" \
    -o mixed-example.data -- ./demo_mixed demo-input.txt demo-output.txt
cmp demo-input.txt demo-output.txt
```

这是有意限定的网络 / exec / exit 案例；文件复制仍会执行，但未选入的内核 I/O 函数
不会补画。函数名需存在于目标内核的 `available_filter_functions` 中；内核版本可能
改变名称。若不用 `-g`，封装默认选择本机架构 syscall 根和 8 层深度，而非全内核函数。

无需匹配内核镜像即可先查看结构：

```bash
funcgraph --no-config --uftrace mixed-example.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output mixed-example.html
fguftrace dump --fgraph --srcline -d mixed-example.data > mixed-example.jsonl
```

Timeline 中依次核对：**Fit trace** 覆盖整个已记录时间范围；**Tools → Exec / exit**
列出实际生命周期事件；用户 / 内核分界线位于匹配的 syscall 顶边；**Call site**
使用符号加偏移；调度、唤醒和未配对调用按实际记录展示。不要求 `main` 与 CPU 上
任务开始时间相同，也不把缺失端点补齐。

需要内核参数名 / 位宽、返回类型和可点击源码时，分别配置两种源码。以下为已验证
QEMU 镜像的值；其它环境必须替换为**采集时运行的内核**对应镜像、源码和提交：

```bash
KERNEL_SRC=/vol_1t/Qemu/linux
VMLINUX="$KERNEL_SRC/x86_64/vmlinux"
KERNEL_REV=7475ea8b58616b33c04afb976cf0fbaad9dd63fb
funcgraph --no-config --uftrace mixed-example.data --timeline --enable-fold --filter --fast \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_REV" \
    --func-links --highlight-code --output mixed-source.html
```

用户源码仍由 `--user-src` 提供，内核网页版本也不使用 `master` / `latest`。
`--fast` 使用 `[all]` 已安装的 pyelftools，避免大量调用点逐个经过 shell 解析的等待。
BTF 只在存在匹配类型及已记录参数时修正内核参数，不恢复未采集的字节。
更完整的真实结果及状态恢复核对见 [采集验收记录](capture-validation.md)；
真实业务依赖库案例见 [pigz + zlib](pigz.cn.md)。

## 案例三：不重新编译应用，只跟踪内核

```bash
sudo "$(command -v fgcapture)" --kernel --sched -g vfs_read,vfs_write \
    -o kernel.txt -- cat samples/demo_mixed.c > /dev/null
funcgraph --no-config kernel.txt --timeline --enable-fold --filter --output kernel.html
```

这会跟踪命令的内核侧，不会显示 `cat` 的用户函数。文本输入不需要 uftrace；
内核源码参数与前例相同。不要把这个文本文件传给仅接收 uftrace **目录**的 `--uftrace`。

要解析内核参数名、位宽和源码，生成报告时再传匹配的 `--vmlinux`、`--kernel-src`、
`--func-links --highlight-code`，以及固定的源码浏览器版本。用户源码始终通过
`--user-src` 选择，不能套用内核链接。完整命令见 [README](../README.cn.md#仅用户态与混合模式)。

## 默认行为

- 纯内核：原有 `-d/-p/-g/-n/--calibrate/--keep` 等选项仍交给 `fgcapture.sh`。
  参数改为自动探测：单函数探测快速、且 args 与 retaddr 同时存在才自动开启。
  老内核或探测不确定时保留调用点并解释降级原因；`--args` 显式要求参数，慢内核仍需
  `-g/-p` 或命令限定；`--no-args` 完全跳过探测。
- 用户态：自动启用 `--auto-args`、DWARF 源码、用户调用点和 `mono` 时钟。
  ARM64 自动增加 `-P .`；程序及希望跟踪的库仍需正确插桩、保留调试信息。
- 混合：保持同一时钟，包含用户函数之外的内核启动 / 退出阶段，启用可用的内核 args、
  retval、retaddr，记录调度事件和 `sched_process_exec` / `sched_process_exit`。
  默认只跟踪对应架构 syscall 根下最多 **8 层**内核调用，16 MiB/CPU，避免无意间
  采集数百万内核调用。完整时间覆盖不等于记录所有内核函数，缺失或被深度限制的调用不补造。
  exec 观测点是映像安装，exit 是线程正在退出，不是最终回收。
- 原生 perf 调度流默认关闭，避免和混合模式的内核 tracepoint 重复。
  用户模式可用 `--sched` 开启 perf 来源；混合模式的 `--no-sched` 仅关闭三类调度事件，
  保留 exec/exit。不同于直接使用上游 `uftrace --no-sched` 的语义。

## 常用调整

| 参数 | 用户 / 混合模式作用 |
| --- | --- |
| `-o DIR` | 新采集目录；已有目录拒绝覆盖 |
| `--no-args` | 关闭自动用户参数 / 返回值采集；混合模式还关闭内核 args，不关闭内核 retval / retaddr |
| `-A SPEC` / `-R SPEC` | 额外的原生参数 / 返回值规格；用于自动 DWARF 无法覆盖的 ABI 等情况 |
| `-g FUNC[,FUNC]` | 替换默认内核 syscall 根；支持 uftrace 正则，例如 `-g '.*'` 显式跟踪所有函数 |
| `-n FUNC[,FUNC]` | 排除部分内核调用图 |
| `--kernel-depth N` | 增大或减小内核深度；默认 8，不是无限深度 |
| `-b KB` | 每 CPU 内核 buffer，默认 16384 KiB |
| `--uftrace-bin PATH` | 手动指定采集程序，不是必传项 |

例如只跟踪网络 syscall，保留深一些的内核调用：

```bash
sudo "$(command -v fgcapture)" --mixed -g '__x64_sys_sendto,__x64_sys_recvfrom' \
    --kernel-depth 30 -b 32768 -o network.data -- ./demo_mixed demo-input.txt demo-output.txt
```

## 状态保护与限制

混合模式要求 root、空闲 tracefs、无已启用事件、无 PID / 函数过滤；不接管其它会话。
采集前持有与纯内核脚本共用的锁，保存 tracer、时钟、选项、过滤、事件、深度、CPU mask
和逐 CPU buffer 大小。完成、失败、SIGINT / SIGTERM 均先停止自己启动的进程组，再尝试
恢复并逐项读回核对。恢复失败返回非零并保存差异，不能冒充成功。

相邻的 `mixed.data.fgcapture/` 保存 `command.json`、`tracefs-before.json`、
`tracefs-after.json`、`tracefs-restore.json`；失败产生的原始采集也保留。
**SIGKILL、断电、内核崩溃、绕过此锁的外部 tracer，以及已经消费的 trace buffer 不在恢复保证内。**
采集器不保证无丢事件；buffer 太小、范围 / 深度过大或中断均可能留下不完整记录。
生成报告后应检查丢失和不完整调用提示，不以 exporter 的 `complete:true` 冒充无丢失。

直接使用 `fguftrace record ...` 的专家接口仍保留，不自动套用这里的状态保护。
详见 [uftrace 导出与可视化契约](uftrace.cn.md)。

## 保存、共享与排查

目录名的 `.data` 只是约定；`capture` 和 `mixed.data` 都可以。区分输入格式时以采集
模式和命令为准，不要仅凭扩展名判断。

| 产物 | 保存或传递方式 |
| --- | --- |
| `*.data/` | 完整保留目录，不只复制某个 `*.dat`；包括符号、映射、内核格式及 clock / 参数元数据 |
| `*.data.fgcapture/` | 与采集目录一起保留，用于核对实际命令、tracefs 读回及恢复差异 |
| `*.jsonl` | 可独立导入，不依赖 uftrace；生成源码片段时仍需对应源码 |
| 单文件 `*.html` | 可以离线打开和直接分享，已内嵌的片段不再依赖原源码树 |
| sidecar 报告目录 | 完整复制目录，用 `fgview DIR --open` 通过 HTTP 打开，不只分享 `index.html` |

报告过大时在生成命令中改用 `--report-mode sidecar --output report-dir`；只需要
Timeline 时可加 `--no-text-view --keep-source`。二者都不是只分析当前可见行，sidecar
也不是完整 Text DOM 内存上限，详见[查看器指南](viewer.cn.md)。

- 看不到用户函数：核对程序和依赖库是否重新插桩、保留 `-g`；系统 `libc` 并不会
  因为应用加了 `-pg` 就自动变为完整可跟踪库。需要额外库选择规则时用专家接口
  `fguftrace`，不要把原生 `-P/-F/-T` 参数直接塞给简化封装。
- 看不到参数名 / 调用点：先检查原 JSONL 是否已记录对应数据和规格。用户 DWARF 与
  内核 BTF 分别处理；升级可视化不能恢复旧采集中丢失的地址、参数或映射路径。
- 找不到 uftrace：确认安装的是匹配架构的原生 wheel，而非 `py3-none-any`；
  显式无效的 `--uftrace-bin` 或损坏内置程序会报错，不会静默换用其它版本。
- 混合采集被拒绝：先检查是否有其它 tracer / 已启用事件或过滤器，不要盲目清空它们。
  发生中断后先读 `.fgcapture/tracefs-restore.json`，恢复失败须人工核对，不应只执行
  `echo nop` 就认为 tracefs 已恢复。
- 原始采集包含地址、参数和真实路径；发布 HTML 或 JSONL 前自行检查是否适合公开，
  工具不会自动将它们脱敏。诊断插桩及跟踪会改变耗时，案例不是性能基准。

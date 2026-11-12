# 实战：追踪 pigz 和 zlib 压缩订单 CSV

用 pigz v2.8 压缩一份合成的订单 CSV，同时跟踪它依赖的 zlib v1.3.2。第一部分不需要
root、`vmlinux` 或 tracefs，支持 x86-64 / ARM64 Linux。准备 Git、GCC、Make、binutils、
Python 和[内含采集程序的 FuncGraph 平台 wheel](uftrace.cn.md#安装和选择采集程序)，
安装后提供 `fgcapture` 和 `fguftrace`。也可以显式选择带补丁的开发构建。
先读[统一采集案例](capture.cn.md)可快速区分纯内核、用户态和混合模式。

维护的 uftrace 补丁已支持可执行文件、库和源码路径中的空格，脚本也会正确引用路径。
如果旧采集程序已经将保存的映射路径截断，请用当前版本重新采集；仅重新导出旧记录，
无法恢复丢失的路径、函数名、类型化参数和源码信息。

## 1. 修改编译选项并构建

在 FuncGraph 仓库根目录运行，工作目录须不存在：

```bash
bash samples/pigz_case.sh /tmp/fg-pigz
# 可选：换一个全新工作目录，显式使用开发版本：
bash samples/pigz_case.sh /tmp/fg-pigz-dev build/uftrace-x86_64/bin/uftrace
```

脚本固定两个项目的源码提交，并自动选择架构的插桩方式。如果在已有源码树中手工操作，
从干净的构建目录重新编译**两者**；下面是 x86-64 的选项：

| 项目 | 版本 | 固定提交（脚本校验，不随分支更新） |
| --- | --- | --- |
| pigz | v2.8 | `fe4894f57739e3039a2ffc2a2a360d35e19bacbe` |
| zlib | v1.3.2 | `da607da739fa6047df13e66a2af6b8bec7c2a498` |

```bash
flags='-pg -g -O0 -fno-omit-frame-pointer'
(cd zlib && CFLAGS="$flags" ./configure --static && make -j2 libz.a)
make -C pigz CFLAGS="$flags -I../zlib" LDFLAGS=-pg LIBS='../zlib/libz.a -lm -pthread'
```

`-pg` 在函数入口插桩，链接可执行文件时也要带上；`-g` 保留文件和行号；`-O0` 与
`-fno-omit-frame-pointer` 让调用链更容易跟踪。这是诊断构建，不用于评估压缩吞吐。
显式链接新编译的 `libz.a`，不要只写 `-lz`。脚本会确认 `deflate` 定义在可执行文件内，
且没有动态依赖 `libz.so`。

ARM64 把上述 `flags` 中的 `-pg` 和 `LDFLAGS` 改成 `-fpatchable-function-entry=2`，
直接使用 `fguftrace record` 时增加 `-P .`；统一 `fgcapture` 入口会自动选择。
脚本已使用此入口。ARM64/GCC `-pg` 会在钩子之前破坏首参，
不能把其记录的 `z_stream` 指针当作可信数据。维护的双 NOP 入口补丁不依赖 Capstone，
但不支持任意未插桩函数的动态跟踪，详见[架构说明](uftrace.cn.md#安装和选择采集程序)。

## 2. 操作并查看报告

脚本采集 `pigz -p 1 -6 -c orders.csv`，再解压并逐字节比对原 CSV，最后生成
`/tmp/fg-pigz/report.html`。切到 **Timeline**，搜索 `deflate`，可以沿 pigz 的
`process → single_compress` 进入 zlib 的 `deflate → deflate_slow → _tr_flush_block`。
脚本通过 `fgcapture --user` 自动记录参数、返回值和调用点，`-A` / `-R` 另外明确
`deflate` 的格式：`z_stream` 指针以十六进制展示但不解引用，`flush = 4` 是
`Z_FINISH`，返回 `1` 是 `Z_STREAM_END`。**Call site** 用调用者符号加偏移表示，
不要和参数中的指针混淆；Text 在入口和退出行展示参数与结果。
`-p 1` 用于简化调用链，不演示多线程压缩。
点击调用栈中的 `pigz.c:2266`、`deflate.c:981` 或 `trees.c:997` 可打开内嵌源码片段；
Text 里也可以展开对应的入口行。用户态源码不链接到内核仓库。

构建完成后可在同一目录手动再采集，不必重复下载、编译或传采集程序路径：

```bash
cd /tmp/fg-pigz
fgcapture --user -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -o capture-user-again -- ./pigz/pigz -p 1 -6 -c orders.csv > orders-again.csv.gz
./pigz/pigz -dc orders-again.csv.gz | cmp - orders.csv
funcgraph --no-config --uftrace capture-user-again --timeline --enable-fold --filter \
    --user-src "$PWD" --output user-again.html
```

`cmp` 无输出且退出码为 0 才表示内容一致。每次更换采集目录；不要删除旧证据来覆盖重录。

| 产物（脚本工作目录下） | 用途 |
| --- | --- |
| `pigz/`、`zlib/`、`pigz.c` | 固定提交的源码、插桩二进制 / 静态库，以及源码路径映射 |
| `orders.csv`、`orders.csv.gz` | 原输入与采集时生成的压缩结果 |
| `capture/`、`capture.fgcapture/` | 原始用户态记录及实际采集命令 |
| `capture.jsonl` | 可以脱离 uftrace 导入的文本导出 |
| `report.html` | 可离线打开、含用户源码的 Text + Timeline |
| `dynamic.txt`、`symbols.txt` | 未依赖系统 libz、实际包含 `deflate` 的构建核对结果 |

## 3. 从 exec 跟踪到进程退出

仅在**空闲、具备 tracefs 快照和恢复的专用实验环境**执行，先阅读[采集安全说明](uftrace.cn.md#采集安全与限制)。
在 VM 内执行前述构建，进入包含 `orders.csv` 的同一工作目录。统一入口负责常规
状态快照、恢复及读回核对，不再要求手工拼接原生 uftrace 的时钟、参数和事件选项：

```bash
cd /tmp/fg-pigz
case "$(uname -m)" in
    x86_64) syscall_prefix=__x64_sys ;;
    aarch64|arm64) syscall_prefix=__arm64_sys ;;
    *) printf '本示例只覆盖 x86_64 / ARM64\n' >&2; exit 1 ;;
esac
sudo "$(command -v fgcapture)" --mixed --kernel-depth 4 \
    -g "${syscall_prefix}_read,${syscall_prefix}_write,do_execveat_common,do_exit" \
    -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -o capture-mixed -- ./pigz/pigz -p 1 -6 -c orders.csv > orders-mixed.csv.gz
./pigz/pigz -dc orders-mixed.csv.gz | cmp - orders.csv
fguftrace dump --fgraph --srcline -d capture-mixed > capture-mixed.jsonl
```

仅需要指定开发版本时，在 `fgcapture` 命令中增加 `--uftrace-bin /absolute/path/to/uftrace`，
并用同一程序导出；默认的采集和导出均选择安装包内置程序。

这里显式选取 read / write、exec / exit 路径和 4 层内核深度，避免小工作负载产生
巨量调用；默认不指定范围时是本机 syscall 根、8 层。完整进程时间覆盖不等于记录
了所有内核函数。`fgcapture --mixed` 默认记录可用的 switch / waking / wakeup 及
exec / exit；封装的 `--no-sched` 会关闭前三种调度事件，但保留生命周期。它不同于
原生 uftrace 同名参数仅关闭 perf 来源的含义。提取样例时仍需保留实际事件；检查方法和限制见
[记录任务切换](uftrace.cn.md#记录任务切换)。

生成报告时，**用户态和内核态源码需要分别配置**。以下是本案例已验证的 QEMU
Linux `7.4.0-rc2` 构建；如果换了内核，镜像、源码提交和网页版本必须一起替换为
**采集时运行的内核**，不能直接用当前主机的 `uname -r` 或源码仓库的最新提交：

```bash
KERNEL_SRC=/vol_1t/Qemu/linux
VMLINUX="$KERNEL_SRC/x86_64/vmlinux"
KERNEL_REV=7475ea8b58616b33c04afb976cf0fbaad9dd63fb

funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter --fast \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --func-links --highlight-code \
    --source-browser github --source-browser-rev "$KERNEL_REV" \
    --output mixed.html
```

- `--user-src "$PWD"`：内嵌 pigz / zlib 的用户态源码，不使用内核网页地址。
- `--vmlinux`：用匹配的 DWARF 调试信息解析内核函数入口位置；只有源码目录不够。
- `--kernel-src` / `--source-rev`：从指定源码仓库的固定提交提取内核源码片段。
- `--func-links`：让 Text 的内核函数名可点击；Timeline 调用栈中的路径也有链接。
- `--source-browser` / `--source-browser-rev`：控制内核网页链接，与内嵌源码内容的选择分开。
  这里显式使用 GitHub 的固定提交，避免继承旧配置；使用 Elixir 时改为
  `--source-browser elixir --source-browser-rev v7.4-rc2`。定制内核应链接到对应仓库，
  而不是把定制源码的行号指向 Torvalds 仓库。

也可以把内核配置保存为预设，后续只指定用户态源码目录：

```bash
fgreport init qemu-pigz --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_REV"
fgreport -p qemu-pigz capture-mixed.jsonl -- --user-src "$PWD"
# 也可用安装包内的采集程序直接读取原始记录目录：
fgreport -p qemu-pigz --uftrace capture-mixed -- --user-src "$PWD"
```

如果只想查看调用关系，或手头没有匹配的内核调试镜像，可以省略内核相关参数：
`funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter --user-src "$PWD" --output mixed-no-kernel-source.html`。
这仍能显示混合调用栈和跨态分界线，但**不会生成内核源码位置、链接或片段**。
仅用户态采集使用前两节的命令，不需要任何内核源码参数。

切到 Timeline，点击 **Fit trace**，再打开 **Tools ▾ → Exec / exit**。面板可跳到 Text 里的原始记录；
**Markers** 在任务轨道标记事件时间。`sched_process_exec` 表示新程序镜像已装载，
不是 `execve` 系统调用入口；`sched_process_exit` 表示任务退出，不是父进程回收。
没有采到的端点不会补画。

此前使用原生专家命令、30 层深度的 QEMU Linux `7.4.0-rc2` 采集中，从 exec 事件到退出
约 **277 毫秒**，包含跟踪开销和
程序启停，不是压缩性能基准。主要工作区间匹配了 4 个读和 4 个写的跨态边界，压缩包
解压后与原 CSV 逐字节一致。这是历史验收结果，不是上述较浅范围新采集的固定耗时或调用数。

跟踪持续时间短不等于报告小。该历史单文件报告约 89 MiB；如想先看 Timeline，再按需打开
Text，在生成命令中用 `--report-mode sidecar --output mixed-report` 替换 `--output mixed.html`，
然后运行 `fgview mixed-report --open`（见[查看器说明](viewer.cn.md)）。

在 pigz 路径里找 `read` 或 `write`，分界线在匹配的内核调用顶边，不在 `deflate` 上：
压缩发生在用户态，文件 I/O 则通过系统调用进入内核。输出重定向到文件，因此写入路径不一定
与管道或终端相同。若要内嵌源码，请保留原构建目录；将采集文件复制到其他主机时，已记录的
绝对源码路径不会自动重定位。

共享时完整保留 `capture-mixed/` 和 `capture-mixed.fgcapture/`；只有 JSONL / HTML 不能
重新验证原始 raw 记录及恢复状态。目录、压缩文件和报告均留在所选工作目录，本脚本不
自动上传，也不将它们加入 Git；检查真实地址、参数和路径是否适合公开后再分享。

## 4. 本地复现实测（2026-11-12）

更新后的脚本在包含空格的目录中完成固定源码下载、两库诊断构建、用户态采集和
HTML 生成；QEMU Linux `7.4.0-rc2` 另以相同二进制验证上面的 4 层混合命令：

- 用户态报告：1,108 次调用，`deflate` 展示 `strm`、`flush=4`、返回 `1`，调用点为
  `single_compress+0xbff/0x1077`，源码落在 `pigz.c:2457`。
- 混合报告：3,801 次调用（1,108 用户 + 2,693 内核）、36 处匹配的跨态边界。
  原始内核页独立解码，与导出中的 5,409 条内核记录逐条核对。
- 压缩包还原结果一致，81 项 tracefs 设置读回相同；保留真实 exec / exit、调度和唤醒。
  未发现显式丢失记录或非零 overrun；2 个内核入口没有退出记录，报告保留不完整标记，
  不以此宣称所有内核函数或整个调用树完整。
- Chromium 实际检查命名参数、返回值、符号偏移、用户源码、固定提交的内核链接及
  BTF 参数；390px 触屏明暗主题无横向页面溢出或 JavaScript 错误。

本地原始记录、恢复证据、用户 / 混合 HTML 和 PNG 保存在
`build/captures/20261112-pigz/`，不提交大二进制到 Git，也不自动替换线上样例。
详细测试依据见[采集验收记录](capture-validation.md)。这些数量和地址是本次实测，
不是每台机器都应相同的验收常量。

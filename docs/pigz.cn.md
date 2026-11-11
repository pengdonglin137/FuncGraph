# 实战：追踪 pigz 和 zlib 压缩订单 CSV

用 pigz v2.8 压缩一份合成的订单 CSV，同时跟踪它依赖的 zlib v1.3.2。第一部分不需要
root、`vmlinux` 或 tracefs。环境为 x86-64 Linux，准备 Git、GCC、Make、binutils、
Python 和[带补丁的 uftrace](uftrace.cn.md)。

## 1. 修改编译选项并构建

在 FuncGraph 仓库根目录运行，工作目录须不存在：

```bash
bash samples/pigz_case.sh /tmp/fg-pigz build/uftrace-x86_64/bin/uftrace
```

脚本固定两个项目的源码提交。如果在已有源码树中手工操作，从干净的构建目录重新编译**两者**：

```bash
flags='-pg -g -O0 -fno-omit-frame-pointer'
(cd zlib && CFLAGS="$flags" ./configure --static && make -j2 libz.a)
make -C pigz CFLAGS="$flags -I../zlib" LDFLAGS=-pg LIBS='../zlib/libz.a -lm -pthread'
```

`-pg` 在函数入口插桩，链接可执行文件时也要带上；`-g` 保留文件和行号；`-O0` 与
`-fno-omit-frame-pointer` 让调用链更容易跟踪。这是诊断构建，不用于评估压缩吞吐。
显式链接新编译的 `libz.a`，不要只写 `-lz`。脚本会确认 `deflate` 定义在可执行文件内，
且没有动态依赖 `libz.so`。

## 2. 操作并查看报告

脚本采集 `pigz -p 1 -6 -c orders.csv`，再解压并逐字节比对原 CSV，最后生成
`/tmp/fg-pigz/report.html`。切到 **Timeline**，搜索 `deflate`，可以沿 pigz 的
`process → single_compress` 进入 zlib 的 `deflate → deflate_slow → _tr_flush_block`。
`-A` 和 `-R` 指定了 `deflate` 的采集格式：`z_stream` 指针以十六进制展示但不解引用，
`flush = 4` 是 `Z_FINISH`，返回 `1` 是 `Z_STREAM_END`。详情分别显示函数记录地址和
参数中的指针；Text 在入口和退出行展示参数与结果。`-p 1` 用于简化调用链，不演示多线程压缩。
点击调用栈中的 `pigz.c:2266`、`deflate.c:981` 或 `trees.c:997` 可打开内嵌源码片段；
Text 里也可以展开对应的入口行。用户态源码不链接到内核仓库。

## 3. 从 exec 跟踪到进程退出

仅在**空闲、具备 tracefs 快照和恢复的专用实验环境**执行，先阅读[采集安全说明](uftrace.cn.md#采集安全与限制)。
在 VM 内执行前述构建，进入包含 `orders.csv` 的同一工作目录，在已保护的 x86-64 采集会话中执行：

```bash
uftrace record -K 30 --clock=mono --kernel-full-info --no-sched --srcline \
    -F '__x64_sys_read@kernel;__x64_sys_write@kernel' \
    -E 'sched:sched_process_exec@kernel;sched:sched_process_exit@kernel' \
    -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -d capture-mixed ./pigz/pigz -p 1 -6 -c orders.csv > orders-mixed.csv.gz
./pigz/pigz -dc orders-mixed.csv.gz | cmp - orders.csv
uftrace dump --fgraph --srcline -d capture-mixed > capture-mixed.jsonl
```

生成报告时，**用户态和内核态源码需要分别配置**。以下是本案例已验证的 QEMU
Linux `7.4.0-rc2` 构建；如果换了内核，镜像、源码提交和网页版本必须一起替换为
**采集时运行的内核**，不能直接用当前主机的 `uname -r` 或源码仓库的最新提交：

```bash
KERNEL_SRC=/vol_1t/Qemu/linux
VMLINUX="$KERNEL_SRC/x86_64/vmlinux"
KERNEL_REV=7475ea8b58616b33c04afb976cf0fbaad9dd63fb

funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter \
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
```

如果只想查看调用关系，或手头没有匹配的内核调试镜像，可以省略内核相关参数：
`funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter --user-src "$PWD" --output mixed-no-kernel-source.html`。
这仍能显示混合调用栈和跨态分界线，但**不会生成内核源码位置、链接或片段**。
仅用户态采集使用前两节的命令，不需要任何内核源码参数。

切到 Timeline，点击 **Fit**，再点 **Exec / exit**。面板可跳到 Text 里的原始记录；
**Markers** 在任务轨道标记事件时间。`sched_process_exec` 表示新程序镜像已装载，
不是 `execve` 系统调用入口；`sched_process_exit` 表示任务退出，不是父进程回收。
没有采到的端点不会补画。

在 QEMU Linux `7.4.0-rc2` 上，这次从 exec 事件到退出约 **277 毫秒**，包含跟踪开销和
程序启停，不是压缩性能基准。主要工作区间匹配了 4 个读和 4 个写的跨态边界，压缩包
解压后与原 CSV 逐字节一致。

跟踪持续时间短不等于报告小。这次单文件报告约 89 MiB；如想先看 Timeline，再按需打开
Text，在生成命令中用 `--report-mode sidecar --output mixed-report` 替换 `--output mixed.html`，
然后运行 `fgview mixed-report --open`（见[查看器说明](viewer.cn.md)）。

在 pigz 路径里找 `read` 或 `write`，分界线在匹配的内核调用顶边，不在 `deflate` 上：
压缩发生在用户态，文件 I/O 则通过系统调用进入内核。输出重定向到文件，因此写入路径不一定
与管道或终端相同。若要内嵌源码，请保留原构建目录；将采集文件复制到其他主机时，已记录的
绝对源码路径不会自动重定位。

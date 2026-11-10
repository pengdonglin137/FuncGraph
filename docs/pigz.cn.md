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
funcgraph capture-mixed.jsonl --timeline --enable-fold --filter --user-src "$PWD" --output mixed.html
```

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

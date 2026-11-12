# 统一采集入口：fgcapture

`fgcapture` 保留原来的纯内核文本采集，并通过同一个入口提供仅用户态和混合采集。
`fgcapture.sh` 保持兼容，也接受 `--user` / `--mixed`（需要安装 Python 包）。
不再需要为常规混合采集手工拼接 uftrace 的时钟、调用点、参数、调度和生命周期选项。

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
    --user-src samples --output mixed.html
```

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

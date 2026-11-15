# 在现有 Timeline 查看 Host QEMU / KVM

English：[kvm.md](kvm.md).

FuncGraph 直接在自己的 **Timeline** 中展示 Host KVM 事件，不需要独立查看器，
也不强制要求采集 `function_graph`。事件与真实调用分开建模：
只有事件的报告仍然准确显示 **0 个函数调用**。

## 打开已有记录

安装 [raw 解码依赖](raw-input.cn.md) 后：

```bash
funcgraph --no-config host.dat --timeline --enable-fold --filter --output host.html
```

如果采集到命名 tracefs instance，请选择对应 buffer：

```bash
funcgraph --no-config host.dat --raw-instance funcgraph-kvm \
  --timeline --enable-fold --filter --export host-analysis.json --output host.html
```

不同 buffer 不会自动混合。大报告可使用 `--report-mode sidecar --output host-report`，
再通过 `fgview host-report` 打开。单文件、sidecar、`--no-text-view` 和补丁版 uftrace
JSONL 内核事件复用同一套 Timeline。普通 `trace-cmd report` 文本不能替代二进制容器；
包含 KVM 注释事件的 function_graph 文本也可使用。

## 短时事件采集

在**独立实验 Host** 中抓取一秒钟，并在窗口内运行简短 Guest 负载：

```bash
instance="funcgraph-kvm-$(date +%s)"
sudo trace-cmd record -B "$instance" -C mono \
  -e kvm:kvm_entry -e kvm:kvm_exit \
  -e sched:sched_switch -e sched:sched_waking -e sched:sched_wakeup \
  -o host.dat -- sleep 1
funcgraph --no-config host.dat --raw-instance "$instance" \
  --timeline --filter --output host.html
```

示例抓取所有匹配 Host 事件，未限定某台 VM。繁忙 Host 应按目标 QEMU 线程过滤 KVM
的 `common_pid`、调度的 `prev_pid / next_pid`、唤醒目标的 `pid`。
只过滤发出事件的线程会遗漏其它线程的唤醒。需要辅助证据时，用 `-e kvm` 代替两个
边界事件。请检查实际内核支持的事件及丢失计数。

采集会修改 tracefs。使用新 instance 名，已有同名 instance 可能被重置。
有其它 tracing 会话时，遵守自己的隔离与前后状态验证流程；分析阶段只读文件。
此处刻意不要求 `function_graph`，它是否能在 instance 中使用取决于采集端内核。

### 需要真实内核函数调用栈时

使用 `fgcapture` 同时采集函数图与事件，不传 `-g / -n` 就不按函数名筛选：

```bash
sudo fgcapture --kernel --sched -p "$VCPU_TIDS" -e kvm -d 0.3 -o host-functions.txt
funcgraph --no-config host-functions.txt --timeline --enable-fold --filter --output host-functions.html
```

在专用、空闲的 Host 上执行；如果采集器提示已有 tracing 会话，不要强行关闭。
`VCPU_TIDS` 为目标 Host vCPU 线程列表；它不自动过滤事件，所以先用短窗口并检查丢失计数。
这会增加真实 Host 内核函数行和 CALL STACK，但不是 QEMU 用户态或 Guest 内部函数。
深度、参数、返回地址等仍取决于采集端内核能力。详见 [额外事件与 Text 显隐](capture.cn.md#函数调用与额外事件一起采集)。

## 页面使用

- 标题显示观测到的 **vCPU ID 和 Host TID**。不同 Host 线程即使 vCPU ID 相同，
  也不会被误合并为同一个 VM 或同一条轨道。
- KVM 行使用克制的绿、蓝灰配色：**Guest 窗口**是 `kvm_entry → kvm_exit`，
  **Host 窗口**是 `kvm_exit → 下一个 kvm_entry`；真实函数行和 off-CPU 标记保持原义。
- 悬浮查看阶段、墙钟耗时、退出原因。点击后在原有详情区查看原始事件、payload、
  CPU、flags、纳秒时间戳，并通过 trace 行链接跳转 Text。
- 详情提供前后窗口导航、Fit window、证据分页，以及当前范围**全部事件**的 CSV 导出，
  不仅导出当前页。JSON 分析导出也保留事件及类型明确的 KVM 区间。
- 函数搜索及调用过滤不会删除 KVM 证据；可折叠任务减少干扰。
  不含 KVM 的报告不会新增相关控件。
- Text 顶部的 **Events: shown / hidden** 可隐藏事件行以专注调用，事件仍完整保留在 Timeline 和导出中。

## 准确性边界

这些是**墙钟边界窗口，不是函数调用，也不等于 CPU 执行时间**。
Guest 窗口可能包含 Host 调度延迟；Host 窗口可能包含 KVM、QEMU、抢占、睡眠、等待，
不能断言已经返回 QEMU 用户态。Guest RIP 不会套用 Host 内核源码链接。

raw 解码器无法格式化某个辅助 KVM 事件时，仍保留原始降级文本（包括
`[FAILED TO PARSE]`），不会将其当作已成功解析的字段。用于配对窗口的 entry / exit
边界必须具备有效的事件格式。

只配对同一个已知 Host 线程内相邻、相反的边界。重复边界、vCPU 身份变化、倒序时间戳、
时钟未验证的跨 CPU 迁移均保留缺口。遇到丢失标记时，保守取消所有未闭合配对；
原始事件仍保留。不补造开头缺失的 entry。末尾未闭合窗口用虚线及
**end not recorded** 明示：共享时钟下只延伸到 trace 最后观测时间，
不伪造完成时刻；没有共享时钟时保留为未闭合边界点。

建议采集时使用 `mono` 等 Host 共享时钟。`local` 或未知时钟不能证明跨 CPU 对齐，
没有时钟元数据的文本也不能。原点保留十进制字符串，避免 JavaScript 丢失大时间戳精度。

目前**没有**采集 Guest 内部函数、自动同步 Host / Guest 时钟或推导跨机器因果关系。
Guest 内部调用需额外记录及可靠对齐。Host 函数栈需要真实 Host `function_graph` 记录；
源码解析应使用匹配的 **Host** `vmlinux / 源码`，不能套用 Guest 内核镜像。

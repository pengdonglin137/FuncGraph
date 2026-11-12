# uftrace 用户态 / 内核态跟踪

## 当前状态（2026-11-12）

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
`512`。点击调用栈 `send_frame` 右侧的 `demo_mixed.c:33` 可打开真实调用点；
`dispatch_datagram` 的函数入口仍单独保留为第 25 行。两次 `receive_frame` 分别返回
`512` 和 `-1`；后者在空队列上使用了 `MSG_DONTWAIT`，不要把用户态返回值当作内核原始返回值。

点击 `__x64_sys_sendto`，调用栈会在同一位置分隔两态，顶边细线仅覆盖这次内核调用的时段。
Text 在对应入口旁显示带下划线的 `K`，用户态入口行显示参数和地址，退出行显示返回值。
两种视图均可用 `space:user` 或 `space:kernel` 过滤。

[仓库内的 JSONL](../samples/sample_mixed.jsonl) 取自 2026 年 11 月 11 日在 x86_64 QEMU
Linux `7.4.0-rc2` 上对 [`demo_mixed.c`](../samples/demo_mixed.c) 的实际采集，使用
`--auto-args --srcline -T '.@callsite'`，包含 25 次用户态调用、689 次内核态调用、25 个用户态调用点事件
和 5 处匹配的跨态边界。截取时保留了原始调用记录和调用点事件，包括地址、参数和原始
返回值；没有纳入调度 / perf 事件。19 次用户调用带有名称的可读参数、19 次带可读返回值，覆盖
`main`、`run_round`、文件传输和 UDP 路径的多层函数。原始采集已用新版导出器重新导出：
`open` 显示 `pathname="/tmp/…/input.bin", flags=0`，输出文件对应 `flags=1`，不再只显示十六进制 payload。
这两个名字来自 uftrace 内置原型，而非内核 BTF。应用自身的参数名通过原始 ELF
（build ID `cd90ee161534e786e7bdd7dc67f8d400bfce81df`）恢复，其符号偏移、源码行及参数 / 返回值规格
与保存的调试元数据逐项一致。25 个用户调用点均带真实符号范围，以 `函数名+偏移/长度` 显示；
第一个 `open` 的调用点为 `main+0x73/0x196`。原有时间戳、地址、payload、已解码标量、源码位置
及全部 714 次调用均保持不变。
发布的报告使用了匹配的内核调试镜像：306 个不同的内核
函数均保留调用栈入口位置，另外恢复了 465 个不同内核调用点的源码位置，其中 282 个含内联链，
链接固定到 Linux `v7.4-rc2`。选中 `__x64_sys_sendto`，调用栈中的函数入口仍是
`net/socket.c:2286`；**Call site source** 展示 `do_syscall_x64` 中的
`arch/x86/entry/syscall_64.c:61`。原始 PC `0xffffffff82129117` 与
`do_syscall_64+0xb7/0x580` 分开展示；展开 Text 行可看同一调用点的内联源码链。匹配的 BTF 还可
校验非阻塞接收的错误路径，为 280 个内核函数提供 ABI 安全的参数名前缀，并为 292 个函数提供类型修正信息。
选中 `__sys_sendto` 可看 `fd`、`buff`、`len`、`flags`、`addr`、`addr_len`；展开
**Raw argument slots** 可核对未经改写的原始参数槽。分别选中两次 `receive_frame`，
源码应落在 `demo_mixed.c:47` 和 `demo_mixed.c:49`，而非共同的第 36 行函数入口；
caller site 使用 `函数名+0x偏移/0x长度`；原始 return PC 保留在元数据中，详情不再重复展示。
选中 `__x64_sys_sendto`，**args (BTF)** 只显示实际的一个 `pt_regs` 指针，不再冒充六个参数；其余原始槽位仍可展开。
缺失的 libc 源码如实显示不可用，不会套用内核链接。完整导出已与保存的 112,287 条内核记录独立逐条核对，75 项 tracefs
设置均精确恢复。打开 HTML 无需内核源码树或 uftrace。在仓库根目录重建它时，
需要匹配且含调试信息的镜像及源码仓库：

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --func-links --fast --highlight-code \
    --vmlinux /vol_1t/Qemu/linux/x86_64/vmlinux --kernel-src /vol_1t/Qemu/linux \
    --source-rev 7475ea8b58616b33c04afb976cf0fbaad9dd63fb \
    --source-browser github --source-browser-rev v7.4-rc2 \
    --output sample_mixed.html samples/sample_mixed.jsonl
FG_CONFIG=/dev/null python3 samples/make_shots.py --mixed-only  # 需要 Playwright + Chromium
```

本地路径请按匹配的构建调整。没有该镜像时，下面的命令仍能重建相同的调用记录、时序、
跨态边界和用户态源码，但无法解析内核源码或执行该镜像的 BTF 类型校验：

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --highlight-code \
    --output mixed-no-kernel-source.html samples/sample_mixed.jsonl
```

## 安装和选择采集程序

Release 的 Linux x86_64 / aarch64 wheel 包含带补丁的 uftrace 及五种 libmcount
采集库。请选择匹配机器架构的包；`py3-none-any` wheel 和 Python 源码发布包不包含
原生二进制。平台包以 Ubuntu 22.04 为构建基线，不标记为 manylinux。Ubuntu 上先安装
采集程序需要的系统运行库，再安装 wheel：

```bash
sudo apt-get install libtraceevent1 libelf1 libdw1 liblzma5 libstdc++6
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fguftrace --version
```

ARM64 请替换为 `linux_aarch64`。`fguftrace record ...` 和 `fguftrace dump ...`
自动运行内置程序，并使用安装后的 libmcount 路径。
`funcgraph --uftrace DIR`、`fgreport --uftrace DIR` 的查找顺序为：显式指定的
`--uftrace-bin PATH` → 内置 uftrace → `PATH` 中带补丁的 uftrace。覆盖参数不是必传项；
无效覆盖路径或不完整的内置程序会直接报错，不会静默回退。未打补丁的上游 uftrace
不支持 `--fgraph` 导出。读取已保存的 JSONL 不需要上述原生程序及系统运行库。
`fgcapture.sh` 仍然是仅内核态的 tracefs 采集脚本，不支持 uftrace 用户态 / 混合采集选项。

工作负载的可执行文件 / 共享库、记录目录和用户源码路径均支持空格；shell 参数需要加引号。
保存的映射名还会保留内部制表符与尾部空格。移走原程序或共享库后，仍可使用已保存的
符号和源码元数据离线导出。旧记录如果已经截断映射路径，需要重新采集；重新导出或
生成 HTML 不能恢复采集时已经丢失的数据。

**ARM64 参数采集：**树莓派 GCC 14.2.0 使用 `-pg` 时，在 `_mcount` 之前
会覆盖 `x0`，该方式记录的 `arg1` 并非原始首参。不要尝试从旧记录中猜测恢复；
请将所有需要跟踪的对象和库改用 `-fpatchable-function-entry=2` 编译，不再使用 `-pg`，
并在采集时增加 `-P .`。两个 NOP 必须位于函数入口，不要使用带非零第二参数的变体。
维护的补丁允许发布程序在没有 Capstone 时跟踪这种入口，并拒绝改写普通指令，
不代表支持任意未插桩函数的动态跟踪。树莓派原生导出 24 项回归全部通过，包括
首参、8 个整数参数、PIE / 非 PIE、`-O0` / `-O2`、递归及共享库路径场景。

```bash
cc -fpatchable-function-entry=2 -g -O0 -fno-omit-frame-pointer \
    -o demo_mixed samples/demo_mixed.c
fguftrace record -P . --no-sched --srcline -T '.@callsite' -d user.data \
    ./demo_mixed demo-input.txt demo-output.txt
```

输入 / 输出文件按 README 示例准备。动态共享库也要用上述编译选项重新构建，
并额外指定 `-P '.@libexample.so'`；仅 `-P .` 不会自动选择所有库。
混合采集同样保留 `-P .`，并选择本机架构的内核函数。后文的 QEMU 采集命令是
x86_64 示例，不应把 `__x64_sys_*` 原样用于 ARM64。

## 记录任务切换

需要区分两种相互独立的调度事件来源：

- 上游 uftrace 默认尝试采集 perf 调度事件，受内核能力和权限限制；`--no-sched`
  关闭的是这一路。FuncGraph 在 Text 中保留这些事件，但原生导出的 `cpu=null`，
  不能把不透明的 perf stream 编号当成 CPU 编号，也不会据此推算 CPU 运行区间。
- **内置补丁版**使用 `-K` 进行内核采集时，`--kernel-full-info` 还会尝试启用
  `sched:sched_switch`、`sched:sched_waking`、`sched:sched_wakeup`。
  FuncGraph 使用这些带 CPU 编号的记录进行调度和唤醒分析。
  **`--no-sched` 不会关闭这三类内核 tracepoint。** 混合示例保留这个参数，是为了
  不再额外保存一份 perf 来源；删除它也不会关闭内核调度采集。

混合采集请使用[采集安全与限制](#采集安全与限制)中的完整示例，保留
`-K 30 --clock=mono --kernel-full-info`，并保留对应架构的内核过滤和参数 / 返回值规格。
不需要给 uftrace 增加 `--sched`：`fgcapture.sh --sched` 是另一个只采集内核的命令，
不是 uftrace 用户态 / 混合采集封装。内核采集需要相应的 tracefs 访问权限，且只能
在空闲、已保护原有跟踪状态的专用实验环境执行。

应检查实际保存的事件，而不只是 `enable=1`：

```bash
fguftrace dump --fgraph --srcline -d CAPTURE > capture.jsonl
rg -c '"name"\s*:\s*"sched:sched_(switch|waking|wakeup)"' capture.jsonl
```

没有匹配表示导出中不存在这些记录，不能据此证明任务从未切换。提取或过滤记录时，也应
保留所需的调度事件。当前公开混合样例特意未纳入它们：重新导出原始采集目录，已核实
有 14 条 switch、28 条 waking 和 7 条 wakeup，但 `samples/sample_mixed.jsonl`
不含这些事件。因此不能把该样例的 CPU 观测区间直接向前补到 `main`，详见
[CPU 覆盖说明](cpu-coverage.md)。真实 switch-in 可能早于 `main`，它也不能替代
函数本身的入口时间。

## 生成报告

```bash
funcgraph --uftrace CAPTURE \
    --timeline --enable-fold --filter --user-src samples --output mixed.html
fgreport -P --uftrace CAPTURE -- --user-src samples
# 或读取已经导出的文件，不需要 uftrace 可执行文件：
funcgraph capture.jsonl --timeline --enable-fold --filter --output mixed.html
```

目录输入调用按上述顺序选出的 uftrace，执行 `dump --fgraph --srcline`。
开发构建可按需增加 `--uftrace-bin build/uftrace-x86_64/bin/uftrace`。
JSONL 按头部自动识别，也可用 `--input-format uftrace` 显式指定，支持 stdin 和压缩输入。
一份报告读取一份记录目录或 JSONL。`--compare BASE` 支持 JSONL 或旧格式 trace；
Compare 与 `--export` 分开统计用户态、内核态的同名函数。

- **生命周期里程碑**：记录中存在 `sched_process_exec` / `sched_process_exit` 时，Timeline
  的 **Exec / exit** 面板列出所有已记录事件，每页最多 100 条；**Previous page** / **Next page**
  替换当前页，不累积行节点。点击 **Trace time** 中的时间可在当前缩放尺度内定位到该事件，
  不依赖 Text 视图，也不会改动过滤条件或伪造函数调用；靠近报告边界时视窗按边界约束移动。
  面板不受调用过滤影响；内核轨道已隐藏时定位仍保留隐藏状态。
  **Export CSV** 导出全部事件（含未浏览页）、任务身份、trace 行号、相对纳秒时间及采集时钟状态。
  绝对 `timestamp_ns` 用原点字符串加安全整数偏移计算，不经过浮点原点；偏移超出安全整数范围时留空。
  `exec` 是新映像已安装的观测点，`exit` 是线程正在退出，不是最终回收时间；缺失事件不补推生命周期。
  未验证跨态时钟时，用户态和内核态仍保持分离，CSV 明确标记 `clock_aligned=false`。
  轨道上的生命周期细标记只绘制可见范围，同一轨道同一像素合并绘制，不删除原始事件。
  分页限制的是面板 DOM，不是完整报告或 CSV 导出的内存；原始事件和索引仍保留。
  合成十万事件的重现方法与测量边界见[生命周期性能记录](performance-lifecycle.md)。
- **只有事件、没有调用**：有时间戳的调度观测，或 uftrace 的 exec / exit 里程碑，即使没有
  函数记录也可生成 Timeline，包括 single / sidecar / auto 交付及 `--no-text-view`。
  例如 `funcgraph events.jsonl --timeline --no-text-view --output events.html`；不需要为这些
  事件提供 vmlinux 或源码。页面明确提示没有已记录函数调用，Tasks、等待链和事件 CSV
  仍可使用；单个里程碑只代表一个观测点，不补造调用、CPU 时间或进程寿命。
  此时 Search 没有调用可匹配，**Hide unmatched** 禁用，共享 Filter 只过滤 Text 行，
  不隐藏或过滤事件上下文。空数据、只有丢失记录、未知事件或缺少可用时间信息不会凭空
  生成 Timeline。只有内核调度事件而没有内核函数时，仍检查跨态时钟，不合并未验证的任务身份。
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
  内核链接使用对应的浏览器和版本，不混用用户态位置；有已解析的采集调用点时优先展示调用点，
  否则明确回退到函数入口。缺少调试位置或文件时，源码保持不可用。
- **内核调用点**：新版原生导出在入口记录中增加可选的 `retaddr_symbol`，仅使用采集目录保存的
  `kallsyms`，不读取分析主机的实时符号表，也不猜测 KASLR 偏移。Text 和 Timeline 恢复
  `函数名+0x偏移/0x范围 [模块]`；原始返回 PC 仍保存在 JSONL 和报告元数据中，详情不再单独展示。
  范围截止到下一条不同地址的已保存符号，不冒充 ELF 测得的函数长度。用 `PC - 1` 选择所属符号，
  正确处理返回地址恰好落在下一符号入口的边界，但保存的偏移仍使用原始 PC；源码解析沿用原流程，
  只减一次 1。模块调用点保留模块归属，必须使用匹配的模块镜像。未知、脱敏、非代码或没有结束
  边界的符号保持原始地址。旧版只有裸地址的 JSONL 仍兼容；需用更新后的补丁版程序重新导出
  原始采集目录才能获得符号信息，不会仅凭任意一个 vmlinux 强行解析裸地址。
- **用户态调用点**：`recorded address` 是被调函数的记录地址，不能据此推出调用者中的调用行。
  采集时增加 `-T '.@callsite' --srcline`，保存 uftrace 原生的调用点事件，例如：

  ```bash
  fguftrace record --srcline -T '.@callsite' -d capture.data ./app
  funcgraph --uftrace capture.data --user-src "$PWD" --timeline --enable-fold --output report.html
  ```

  更新后的补丁版导出保留事件原始字节和返回 PC，使用采集会话的模块映射、匹配的带调试信息镜像
  解析 `PC - 1`。只把事件与同线程恰好晚 1 ns 的下一条用户态入口配对；缺失、丢失或不匹配时不补猜。
  Text 和详情只展示一条 **caller site**，格式与内核一致：`函数名+0x偏移/0x长度`。
  偏移基于 `PC - 1`，并正确处理 PIE、共享库和 dlopen 的模块重定位；原始返回 PC 仍保留在
  JSONL 和报告元数据中，不再单列展示。**recorded address** 仍是被调函数入口地址。
  旧 JSONL 缺少符号边界时明确显示 `offset unavailable`，不会猜偏移；使用新版内置 uftrace
  重新导出原采集目录即可补充。无法解析符号时仅回退到调用点 PC，并明确标记。
  这些地址不相互替代，也不把 `PC - 1` 伪称为调用指令起始地址。
  Text 和详情源码优先展示 **User call site source**；调用栈只在保存的调用者符号与父帧匹配时，
  将该位置放到父帧右侧。没采集调用点或缺少匹配的 DWARF 时，明确标注函数入口回退，仍保留原始地址。
  旧采集若没有调用点事件，需要重新采集；仅重新生成 HTML 无法找回从未记录的调用地址。
- **内核参数和位宽**：提供匹配的 `--vmlinux` 或 `--btf` 后，详情的 **args (BTF)** 和 Text
  按可信原型裁剪参数数量，显示 `参数名=类型修正值`；每个整数只取 BTF 声明的低位，再按有符号性解释。
  例如 `int` 槽位为 `0xdeadbeefffffffea` 时显示 `-22`，`unsigned char` 显示 `0xea`，不会把高位残值当作参数。
  指针按已知目标字长保留精确十六进制，不解引用；64 位数据不经过 JavaScript 浮点数转换。
  完整原型为零参时不展示残余槽；声明参数多于实采槽时以 `…` 标出缺失，不补值。
  **return (BTF)** 同样修正返回值的位宽和符号；布尔返回值取 bit 0，显示 `true/false`，`void` 不伪造返回值。
  参数和返回寄存器的原始位模式仍分别保存在 **Raw argument slots** 和 **Original return bits**，JSONL 和模型原值不变。
  只解释 ABI 对应关系已确认的整数/指针前缀；聚合参数、浮点、可变参数、原型冲突、编译器克隆、
  未知指针字长等不能确认的布局不强行裁剪，其余槽位保持 `slotN`。不额外做 DWARF 查询。
  无匹配 BTF 时仍显示 **raw args** / **raw return**，不承诺与所有内核版本的 `cat trace` 逐字一致。
- **BTF 解析依赖**：报告端的 `fglib/btf.py` 仅使用 Python 标准库，不要求安装 libbpf 或
  bpftool。它读取匹配目标内核的原始 BTF、ELF 的 `.BTF` 段及有基准 BTF 的模块 split BTF，
  提取本项目所需的原型与标量类型；不是 libbpf 的完整替代实现，也不负责加载 eBPF 程序。
  原生采集程序的构建依赖与这条报告端解析路径分开，见下方可复现构建说明。
- **用户态参数**：应用应带调试信息编译，采集时使用 `--auto-args`，或以 `-A` / `-R` 明确规格。
  例如 `fguftrace record --auto-args --srcline -T '.@callsite' -d capture.data ./app`。
  自动标量按 uftrace replay 的数值规则导出。已保存长度的 C / C++ 字符串显示为带引号的值，
  引号、反斜杠及控制 / 非 ASCII 字节做转义；枚举保留数值。记录的 `"NULL"` 仍显示为字符串，
  不猜测它是原始字符串还是 uftrace 的空字符串指针标记；原始字节仍可展开查看。
  浮点、聚合及其他不支持的布局保留完整 payload，不输出错位的半份参数列表。
  PLT 调用仅在保存的参数序号、ABI 位置、格式、位宽和枚举标识均匹配时使用 uftrace 内置原型中的参数名，
  并明确区分其来源；已记录的用户 DWARF 优先，无法匹配的值使用 `slotN`。
  `open` 的可变参数 mode 没有采集时不会补造，缺失的规格也不会猜测。
  内核 BTF 仅解释内核调用，不能给用户态函数裁参数或命名；已有采集若没有参数 payload，需要重新采集。
- **过滤**：启用 `--filter` 后，Text 与 Timeline 共用的 Filter 支持 `space:user` 和
  `space:kernel`；sidecar 报告不必先加载 Text。命令行的调用树预过滤暂不适用于 uftrace。
- **数据缺口**：缺失进出记录或丢失事件会标记，不补造实测帧。用户态 CPU 未知，
  用户态仅在存在支持的标量参数规格时解码，否则保留原始字节；内核返回值在匹配 BTF 可用时展示类型修正值，并保留原始比特，只在 BTF 类型已知时做 errno
  分析。调度分析使用内核 tracepoint，不与 perf 事件重复计入。未投影到报告中的原生
  字段仍保留在 JSONL 中。

## 可复现构建

上游基线固定为 `842ed731248a9daded38774d8fb26c7b09474461`（v0.20）。
[`third_party/versions.json`](../third_party/versions.json) 同时记录 uftrace 和三个
raw 解码库当前使用的完整 commit SHA。构建前检查全部 gitlink 与锁文件一致，
并要求 uftrace 的 HEAD 匹配、已跟踪源码干净，再应用补丁；不要使用
`git submodule update --remote`。有意升级时请按[依赖版本锁说明](../third_party/README.md)
同时更新 gitlink、锁文件和补丁，并重新测试。

构建或更新原生程序需要 Git checkout，不是在已安装的 Python wheel / sdist 中构建；
直接安装原生 Release wheel 不需要本机编译器。

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
`patches/uftrace/README.md`。

构建发布程序时，为构建脚本设置 `FG_UFTRACE_RELEASE=1`，禁用可选的 Python/Lua、
curses、unwind 和 Capstone 功能，但保留 ARM64 的双 NOP 入口采集，版本信息中标记为
`patchable`（不是任意指令的 `dynamic`）。构建平台 wheel 时设置
`FG_UFTRACE_PREFIX="$PWD/build/uftrace-x86_64"`（ARM64 使用 `aarch64`）。
包内包括 uftrace、全部五种采集库、GPL-2.0 `COPYING`、固定的上游与补丁哈希，
以及确定性生成、已应用全部补丁的完整对应源码 `source.tar.gz`；不打包操作系统共享库。
同一对应源码还会单独作为 Release 附件。此集成不擅自给整个项目选择许可证。
构建、搬迁安装验证命令见[发布清单](releasing.cn.md)。

## 原生导出格式

```bash
fguftrace dump --fgraph --srcline -d CAPTURE > capture.jsonl
# 开发版本等价命令：build/uftrace-x86_64/bin/uftrace dump --fgraph --srcline -d CAPTURE
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
  时，以 `payload_hex` 保存原始字节。支持的 8/16/32/64 位自动、有符号、无符号、十六进制和指针
  规格会附加 `user_args`（入口字符串列表）或 `user_retval`（出口字符串）。使用字符串保留
  64 位精度；规格不受支持或不完整时保留原始 payload，不猜测。
- 新版采集库还会把可用的用户态 DWARF 参数名保存到采集目录的 `.dbg` 文件中。
  应用与相关库需要使用 `-g` 编译；`--auto-args` 和显式 `-A` 规格都可以保留参数名。
  导出时增加与 `user_args` 逐项对应的可选 `user_arg_names`，无法匹配的位置为 `null`。
  匹配依据是采集 session、映射模块、函数地址及整数 / 浮点 / 寄存器 / 栈位置，
  不是全局同名函数匹配，更不会使用内核 BTF 为用户函数命名。实际采集的值和位宽仍由
  保存的采集规格决定；参数名不会增删参数或重新解释参数值。
  Text 与 Timeline 对已知项显示 `count=3`，部分名称缺失时显示 `slotN=value`；
  Timeline 的 **Original argument bytes** 可展开核对原始字节。旧记录缺少参数名时继续
  仅显示数值；只更新导出程序无法补回未采集的数据，需要同时使用新版可执行程序与采集库
  重新采集。DWARF 被剥离、元数据冲突、编译器改写签名的克隆函数、自定义位置不能核对时均保持未命名。
  复杂 / 尚不支持的格式仍保留原始字节，不伪造值或参数名；现有公开混合样例早于该元数据，
  不会依据同名源码为它补写参数名。
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

### 原生安装包本地验收（2026-11-11）

- x86_64 的完整原生 / 浏览器测试运行 673 项：670 项通过，3 项可选跳过。
- x86_64 与树莓派 ARM64 均通过 24 项原生导出回归。ARM64 从完整对应源码干净构建，
  另通过 2 项双 NOP 安全单测与 2 项映射路径单测；未放宽任何参数断言。
- 两种架构均构建平台 wheel，通过 Twine、基础安装及 `[all]` 安装测试。每轮安装验证
  都先移走原始原生构建前缀，从仓库外运行；增强模式的 13 项中 12 项通过，
  仅跳过未启用的 raw 安装集成。ARM64 未安装 `gawk`，源码片段和尾调用断言仍通过。
- 两种 wheel 内的 9 补丁对应源码逐字节相同。4 个上游 gitlink 与版本锁均未变更；
  构建、运行时和依赖锁的 36 项独立测试在两种架构均通过。
- 最后将列表源码测试夹具与主机 `gawk` 能力隔离后，92 项相关源码 / 浏览器回归通过，
  树莓派额外的 18 项源码解析回归也全部通过；真实无 `gawk` 安装测试不使用该夹具。
- 树莓派使用已安装的内置采集程序完成 pigz + zlib 用户态实测：1,108 次调用，
  `deflate` 的两个参数保留为非零原始指针和 `4`，返回值为 `1`，压缩往返逐字节一致。
  浏览器离线验证了详情和 `deflate.c:981` 的点击源码，无额外请求或 JavaScript 错误。

这是本地构建与安装验收，不代表已推送、GitHub Actions 已通过或已发布 Release；
本轮 pigz ARM64 记录为仅用户态，不能替代下表的真实内核采集与逐条核对。

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

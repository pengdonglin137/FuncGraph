# 用户态到内核的全流程跟踪：计划

> 状态：原生采集 / 导出基础已落地；FuncGraph 导入和可视化尚未实现。计划日期 2026-11-09；更新 2026-11-11。
> 主方案：修改 [uftrace](https://github.com/namhyung/uftrace)（作为 submodule，补丁单独维护），一次 `uftrace record` 同时采集用户态和内核，内核部分带上 FuncGraph 需要的全部信息。
> [magic-trace](https://github.com/janestreet/magic-trace) 依赖 Intel PT 硬件，不通用，暂不考虑；它的"环形缓冲 + 触发快照"思路放在 P4，用 ftrace 自己的机制实现。

## 1. 目标

补充验收要求（2026-11-11）：用户态-only、内核态-only、用户态+内核态使用同一条导入和展示流程；不能因为缺少另一态的数据而报错、制造缺失帧或要求无关的 vmlinux/源码。地址空间来自输入的显式标记，不从函数名推断。混合视图在真实的用户态→内核态边界显示细而清晰的分界线，并配合图例和详情中的地址空间标签；分界只覆盖实际内核子树的时间范围，不画成整条轨道的固定水平线（不同调用的边界深度可能不同）。Text 视图也应有低干扰的态标签/边界，单态报告不显示多余分界。

一份报告里看到一个请求从用户态函数一路进入内核再返回的完整过程：

```
main → handle_request → read_config → read@plt ── 用户态
                                         └─ __x64_sys_read → ksys_read → vfs_read … ── 内核
```

- 时间轴：同一个任务的轨道里，用户态帧在上，系统调用下面接着内核调用树；用户态帧用单独的颜色。
- 文本视图：用户态的行和内核的行按时间排列，用户态函数也能链接到源码。
- 现有分析（耗时拆分、错误路径、临界区、off-CPU、等待链、对比、导出）对合并后的调用树同样有效。例如 *Where the time went* 能分出用户态自身代码、系统调用、被抢占或阻塞。
- 只有内核数据时（不用 uftrace），FuncGraph 的现有用法完全不变。

不做：Intel PT / ARM CoreSight 等硬件跟踪；Python 等解释型语言的用户态跟踪（uftrace 支持，但不在第一版范围）。

## 2. 为什么改 uftrace，而不是两个工具分别采集

uftrace 的 `-k`/`-K` 已经能采内核（`utils/kernel.c`、`utils/kernel-parser.c`）：

- 它自己设置 tracefs：`current_tracer=function_graph`，用 `set_ftrace_pid` 加 `function-fork` 跟着被跟踪的进程，`set_graph_function`、`max_graph_depth`，`trace_clock` 和用户态用同一个时钟（默认 `mono`）。
- 采集时直接读每个 CPU 的 `trace_pipe_raw` 二进制数据并原样保存，同时保存 `funcgraph_entry`/`funcgraph_exit` 的 format 文件；回放时用 libtraceevent 解析。
- 但解析时只取了 `func`、`depth` 和时间戳。

内核的二进制记录（7.2 的 `kernel/trace/trace_entries.h`）里已经有 FuncGraph 需要的大部分信息：

| FuncGraph 需要 | 二进制记录中的来源 | uftrace 现状 |
|---|---|---|
| irqs-off / need-resched / hardirq / softirq | 每条记录的 `common_flags` | 没取 |
| preempt 深度、migrate-disable | `common_preempt_count` | 没取 |
| 返回值 | `funcgraph_exit.retval`（`CONFIG_FUNCTION_GRAPH_RETVAL`） | 没取 |
| 参数 | `funcgraph_entry.args[]` 动态数组（`funcgraph-args` 打开时） | 没取 |
| 调用点 | `fgraph_retaddr_entry.retaddr`（另一种事件类型） | 不认识：打开 retaddr 后内核入口记录会丢失 |
| 调度事件 | sched_switch / sched_waking / sched_wakeup tracepoint | `-E` 已支持打开内核事件 |

所以补齐解析和输出后，一条命令就够了，好处是：

- 只有一个时钟、一套 tid，不需要时间对齐和"过滤跟踪工具自身线程"。
- 读二进制数据不经过内核的文本格式化：6.18 上 `funcgraph-args` 让 `cat trace` 每行慢约 36 ms 的问题不存在了。6.18 上 args 与 retaddr 不能同时输出是记录本身的限制，读二进制也绕不过。
- 内核部分可以只跟踪被跟踪进程的上下文，数据量比全系统小。

两个工具分别采集再合并的方案保留为备选，见第 9 节。

## 3. uftrace 作为 submodule

### 3.1 仓库布局

```
third_party/uftrace/          git submodule，固定在一个发布标签，只读，不直接修改
patches/uftrace/
    README.md                 每个补丁的目的、上游状态（未提交 / 已提交 / 已合入）、可以删除的条件
    series                    应用顺序
    0001-xxx.patch            git format-patch 格式，带完整的提交说明
scripts/uftrace.sh            build / refresh / check（见 3.2）
build/uftrace-<arch>/         构建产物（.gitignore）：uftrace 和 libmcount
```

### 3.2 补丁的维护流程

- **构建**（`scripts/uftrace.sh build [--arch arm64]`）：`git -C third_party/uftrace archive HEAD` 导出干净的源码到 `build/uftrace-src-<arch>/`，按 `series` 顺序 `git apply`，再 `./configure && make`。submodule 始终保持干净。交叉编译沿用 `CROSS_COMPILE`（Pi 4 用 `aarch64-linux-gnu-`）。
- **修改补丁**（`scripts/uftrace.sh refresh`）：在开发用的 worktree（`build/uftrace-dev`，基于 submodule 当前标签）上提交修改，用 `git format-patch` 重新生成 `patches/uftrace/`。补丁只从 worktree 生成，不手工编辑。
- **检查**（`scripts/uftrace.sh check`）：只验证补丁能干净地应用，在 CI 里运行。
- **升级 uftrace**：更新 submodule 标签 → `check` → 在 worktree 里 rebase 有冲突的补丁 → `refresh` → 删除上游已合入的补丁。
- **原则**：补丁越少越好；通用的改进（`fgraph_retaddr_entry`、retval、flags 的解析）尽量提交上游，只为 FuncGraph 服务的部分（输出格式）留在本地。

### 3.3 许可证

uftrace 是 GPL-2.0。FuncGraph 只调用它的可执行文件、读取它的输出，不链接它的代码。发布构建好的 uftrace 二进制时，要附上对应的源码和补丁。FuncGraph 仓库目前没有 LICENSE 文件，建议在引入 submodule 之前补上。

## 4. 对 uftrace 的修改

| 补丁 | 位置 | 内容 |
|---|---|---|
| 1. 内核采集选项 | `utils/kernel.c`、`cmds/record.c` | 新增 `--kernel-full-info`：打开 `funcgraph-retval`、`funcgraph-args`、`funcgraph-retaddr`、sched_switch/waking/wakeup；每个选项先探测是否存在，不支持的跳过并写进 `info`；记录 `trace_clock`、内核版本、各选项是否生效 |
| 2. 解析新增字段 | `utils/kernel-parser.c`、记录结构 | 处理 `fgraph_retaddr_entry`；取出 `common_flags`、`common_preempt_count`、`retval`、`args[]`、`retaddr`；字段不存在时（旧内核、选项未开）留空而不是报错 |
| 3. 输出格式 | `cmds/dump.c` | 新增 `uftrace dump --fgraph`（名字待定）：用户态和内核的记录按 tid 合并、按时间排序，每条带上述字段，供 FuncGraph 读取（格式见第 5 节） |
| 4. 开销标定（可选） | `cmds/record.c` | 用户态插桩开销的测量，写进输出头部，对应 FuncGraph 的 *Overhead* 开关 |

估计几百行 C 代码。补丁 1 和 2 尽量写成上游能接受的形式。

## 5. 输出格式（补丁 3）

两种候选，P0 阶段定：

| 方案 | 形式 | 优点 | 缺点 |
|---|---|---|---|
| A（倾向） | ftrace function_graph 文本格式（带 abstime、proc、latency 列、`ret=`、`<-retaddr`、参数），用户态的行加一个标记（例如函数名前缀或额外一列） | FuncGraph 现有的解析、文本视图、时间轴、分析基本不用改；人也能直接读 | 要和内核各版本的文本格式保持一致；用户态行的标记方式要定 |
| B | 逐行 JSON（每条记录一个对象） | 字段明确，扩展方便 | FuncGraph 要新写一套导入，文本视图要自己合成行 |

要求：时间戳是纳秒整数或高精度小数；每行都带 tid、CPU、深度；用户态和内核的深度连续（内核的深度接在用户态深度后面）；头部记录采集参数、时钟、内核选项是否生效、开销标定结果。

## 6. FuncGraph 侧的修改

- **输入**：`funcgraph.py --uftrace DIR`，内部调用构建好的 `uftrace dump --fgraph`；也可以直接读取已经导出的文本文件。
- **用户态帧**：新增标志位 `F_USER`。`fglib/timeline.py` 的 `F_*` 和 `assets/timeline/model.js` 的 `FL` 一起改（见 design.md 6.7）。
- **上下文配色**：增加 user 一种颜色，和 task/softirq/hardirq/NMI 并列。
- **耗时拆分**：用户态帧拆成用户态自身代码、系统调用（内核）、被抢占或阻塞（off-CPU）。
- **源码**：用户态地址由 uftrace 提供符号和源码行（`--srcline`）；新增 `--user-src DIR` 指定用户程序源码目录，源码块的显示方式和内核一样。
- **过滤**：统一过滤器增加 `space:user|kernel`。
- **采集脚本**：`fgcapture.sh --user -- ./app` 包装 `uftrace record -k --kernel-full-info`，沿用现有的能力探测、开销标定和结束后恢复 tracefs；不加 `--user` 时行为不变。

## 7. 分阶段

2026-11-11 进展：固定 uftrace v0.20（842ed731248a），完成可复现 build / check /
refresh 和四份维护补丁（含 Pi 实测发现的 kallsyms 模块符号区间错误修复）；QEMU 实际是 7.4.0-rc2，Pi 是 6.18.56-v8-MY_CUSTOM_KERNEL+。
采用方案 B（版本化 JSONL），原始导出明确声明分流顺序和分地址空间深度，P2 导入器
负责稳定合并及连续深度。已加入无 root 的跨字节序 / 字长 / 参数布局 / 损坏数据测试
及 CI；真实采集通过独立 ring-page 解码核对，不用已读空的 cat trace 伪装同次基准。
尚未交付 `--uftrace`、`--user-src`、F_USER / 分界线和 `fgcapture.sh --user`，
P1 的端到端可视化验收与 P2 / P3 仍待完成。详见 `docs/uftrace.cn.md`。

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **P0 验证** | 加入 submodule 和 `scripts/uftrace.sh`，在 QEMU（7.2）和 Pi 4（6.18）上构建未打补丁的 uftrace；用 `-pg` 编译 `samples/demo_io.c`，运行 `uftrace record -k`，确认内核记录正常、时钟是 `mono`；写一个原型补丁读出 flags、retval、args、retaddr | 同一次运行中，原型补丁读出的字段和 `cat trace` 的文本结果逐条一致；定下第 5 节的输出格式 |
| **P1 uftrace 补丁** | 补丁 1–3 | `uftrace dump --fgraph` 的输出被 FuncGraph 现有解析器读入，内核部分的时间轴和现在用 fgcapture 采集的一致（错误路径、临界区、off-CPU 都在） |
| **P2 FuncGraph 导入** | 第 6 节的 `--uftrace`、`F_USER`、配色、文本视图、`space:` 过滤 | demo_io 的报告里 `sendto` 能从 `main` 一直展开到 `loopback_xmit`；单元测试和新的 golden 配置 |
| **P3 采集与分析完善** | `fgcapture.sh --user`、用户态开销标定（补丁 4）、耗时拆分区分用户态和内核、Compare 和导出包含用户态、用户态源码 | 一条命令得到报告；使用指南新增"用户态"一节并配截图；sample 换成带用户态的版本 |
| **P4 触发快照** | 借鉴 magic-trace：持续采集到环形缓冲，指定函数被调用或延迟超过阈值时保存（uftrace trigger + ftrace snapshot） | 能抓到偶发的一次慢请求及其之前的若干毫秒 |

另外有一个随时可以做的独立功能：导出 Perfetto / Chrome trace JSON。

## 8. 测试和 CI

- 夹具：在 QEMU 上采一份小的数据，把 `uftrace dump --fgraph` 的输出放进 `tests/data/`，单元测试不需要 uftrace 可执行文件。
- 单元测试：用户态行的识别、用户态与内核的父子关系和深度、`F_USER` 前后端一致；时间轴不变量（design.md 第 9 节）对合并后的数据同样成立。
- CI：
  - 运行 `scripts/uftrace.sh check`，验证补丁能干净地应用。
  - 补丁或 submodule 有变化时再构建 uftrace，并跑一次真实的 `uftrace record` 冒烟测试（GitHub 的 runner 上需要 root 和 tracefs，可行性待确认）。

## 9. 备选方案：两个工具分别采集

uftrace 补丁无法使用时（例如目标机上不能编译 uftrace，或补丁跟不上 uftrace 升级），退回到同一次运行中分别采集：

- uftrace 只采用户态（不加 `-k`），`fgcapture.sh` 照现在的方式采内核；两边的时钟都设为 `mono`，按 tid 和时间合并。
- 校验对齐：每个内核系统调用都应落在同一 tid 上某个用户态帧（libc 包装函数或 `xxx@plt`）的时间范围内；有固定偏移时用这个关系估计并修正。
- ftrace 只跟踪这个程序的两种办法：跟踪 uftrace 进程并打开 `function-fork`，转换时按 `task.txt` 去掉 uftrace 自己的记录线程；或者用 uftrace 的 `--keep-pid`，让程序保留启动时的 pid，提前写进 `set_ftrace_pid`（多线程时要确认新线程也被跟踪）。

## 10. 风险和待定问题

1. **目标程序的范围**：自己编译的 C/C++ 程序（`-pg` / `-fpatchable-function-entry`）最简单。没有源码的二进制需要动态插桩（`-P.`，依赖 capstone），在 arm64 上的支持程度要在 P0 确认。
2. **数据量**：用户态每个函数都插桩，数据量和开销都大。默认要限制范围（`-F` 入口函数、`-D` 深度、`--time-filter`）。
3. **多线程、fork、exec**：uftrace 有 session 机制（`sid-*.map`），exec 后符号会变，要验证。
4. **内核版本差异**：args、retaddr、BTF-aware retval 的记录格式在 6.18 和 7.2 之间不同，补丁 2 要按 format 文件里实际存在的字段来解析。
5. **补丁维护成本**：补丁集中在 uftrace 的内核采集部分，升级时可能需要 rebase。尽量提交上游。
6. **QEMU 7.2 内核的 `trace_clock` 是否支持 `mono`**：本次没能连上虚拟机，待确认。
7. **uftrace 版本**：建议固定在最新的发布标签。
8. **许可证**：见 3.3 节。

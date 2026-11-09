# 用户态到内核的全流程跟踪：计划

> 状态：计划，尚未实施。日期 2026-11-09。
> 借鉴 [uftrace](https://github.com/namhyung/uftrace)（用户态函数跟踪）。[magic-trace](https://github.com/janestreet/magic-trace) 依赖 Intel PT 硬件，不通用，暂不考虑；它的"环形缓冲 + 触发快照"思路可以用 ftrace 自己实现，放在后面的阶段。

## 1. 目标

一份报告里看到一个请求从用户态函数一路进入内核再返回的完整过程：

```
main → handle_request → read_config → read@plt ── 用户态（uftrace）
                                         └─ __x64_sys_read → ksys_read → vfs_read … ── 内核（ftrace function_graph）
```

- 时间轴：同一个任务的轨道里，用户态帧在上，系统调用下面接着内核调用树；用户态帧用单独的颜色。
- 文本视图：用户态的行和内核的行按时间穿插，用户态函数也能链接到源码。
- 现有的分析（耗时拆分、错误路径、临界区、off-CPU、对比、导出）对用户态帧同样有效：例如 *Where the time went* 能分出"用户态自身 / 系统调用 / 被抢占"。

不做：

- 不用 `uftrace -k` 采内核。它会自己设置 tracefs，拿不到我们依赖的 retval、args、retaddr、latency-format、调度事件这些选项；内核部分继续由 `fgcapture.sh` 采集。
- 不做 Intel PT / ARM CoreSight 等硬件跟踪。
- 不做 Python 等解释型语言的用户态跟踪（uftrace 支持，但不在第一版范围）。

## 2. uftrace 作为 submodule

### 2.1 仓库布局

```
third_party/uftrace/          git submodule，固定在一个发布标签（例如 v0.17），只读，不直接修改
patches/uftrace/
    README.md                 每个补丁的目的、上游状态（未提交 / 已提交 / 已合入）、可以删除的条件
    series                    应用顺序
    0001-xxx.patch            git format-patch 格式，带完整的提交说明
scripts/uftrace.sh            build / refresh / check 三个子命令（见 2.2）
build/uftrace-<arch>/         构建产物（.gitignore），uftrace 可执行文件和 libmcount
```

### 2.2 补丁的维护流程

- **构建**（`scripts/uftrace.sh build [--arch arm64]`）：用 `git -C third_party/uftrace archive HEAD` 导出一份干净的源码到 `build/uftrace-src-<arch>/`，按 `series` 顺序 `git apply` 补丁，再 `./configure && make`。submodule 本身始终保持干净，`git status` 不会显示它被修改。交叉编译沿用项目的 `CROSS_COMPILE` 约定（Pi 4 用 `aarch64-linux-gnu-`）。
- **修改补丁**（`scripts/uftrace.sh refresh`）：在一个开发用的 worktree（`build/uftrace-dev`，基于 submodule 当前标签）上提交修改，用 `git format-patch` 重新生成 `patches/uftrace/`。补丁只从这个 worktree 生成，不手工编辑。
- **检查**（`scripts/uftrace.sh check`）：只验证补丁能干净地应用到当前标签，CI 里运行。
- **升级 uftrace**：更新 submodule 到新标签 → `check` → 有冲突的补丁在开发 worktree 里 rebase → `refresh` → 删除上游已合入的补丁。
- **原则**：补丁越少越好，每个都写明为什么需要、能否提交上游。能在 FuncGraph 这边解决的（例如解析格式差异）不改 uftrace。

### 2.3 许可证

uftrace 是 GPL-2.0。FuncGraph 只是调用它的可执行文件、读取它的输出，不链接它的代码，这种用法没有问题。如果以后发布构建好的 uftrace 二进制，要附上对应的源码和补丁。另外，FuncGraph 仓库目前没有 LICENSE 文件，建议在引入 submodule 之前补上。

## 3. 数据流

```
采集（目标机）                                      转换（主机）
fgcapture.sh --user -- ./app
 ├─ ftrace: trace_clock=mono, function_graph,      funcgraph.py --uftrace uftrace.data trace.txt
 │          set_ftrace_pid=<app 的 pid>, …           ├─ 解析 ftrace（现有）
 │          → trace.txt                              ├─ 读取 uftrace 数据（uftrace dump 或直接读数据目录）
 └─ uftrace record --clock=mono -d uftrace.data      ├─ 按 tid + 时间合并
                                                     └─ 时间轴 / 文本视图 / 分析
```

### 3.1 时钟对齐

- ftrace 的 `trace_clock` 设为 `mono`，uftrace 用 `--clock=mono`（这也是它的默认值）。已确认 Pi 4 的 6.18 内核支持 `mono`；QEMU 的 7.2 内核待确认。
- 校验：每个内核系统调用（`__x64_sys_*` / `__arm64_sys_*`）都应该落在同一个 tid 上某个用户态帧（通常是 libc 包装函数或 `xxx@plt`）的时间范围内。统计偏差，超过阈值就在报告里给出警告。
- 补救：如果两边有固定偏移（例如时钟不同），用"PLT 调用包住系统调用"这个关系估计偏移并修正，同时在提示条里注明。

### 3.2 任务对齐

- 两边都有 tid。uftrace 数据目录的 `task.txt` 记录了进程和线程的关系，用来给报告里的轨道命名。
- 风险：`fgcapture.sh` 若用 `set_ftrace_pid` 加 `function-fork` 跟随子进程，会把 uftrace 自己的记录线程也跟踪进去。转换时只保留 uftrace 记录过的那些 tid，其他的作为"跟踪工具自身"丢弃，并统计行数。

### 3.3 读取 uftrace 数据

两种方案，P0 阶段比较后选定：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A | 调用构建好的 `uftrace dump`（或 `replay`），解析文本输出 | 不依赖二进制格式，uftrace 升级时影响小 | 主机上需要能运行 uftrace；文本格式不一定稳定 |
| B | 直接读数据目录（`info`、`task.txt`、`*.map`、`*.sym`、每个 tid 的 `*.dat`） | 不需要 uftrace 可执行文件，速度快 | 要跟着 uftrace 的格式变化走 |

如果选 A 但现有的文本输出缺字段（例如纳秒时间戳、tid、深度、参数或返回值），可以加一个补丁，增加稳定的机器可读输出。这是第 2 节补丁机制可能的第一个用途。

## 4. 合并模型

- 用户态调用成为普通切片，新增标志位 `F_USER`。`fglib/timeline.py` 的 `F_*` 和 `assets/timeline/model.js` 的 `FL` 要一起改（见 design.md 6.7）。
- 内核调用树挂在同一时刻、同一 tid 上最内层的用户态帧下面。系统调用入口是拼接点，深度在用户态深度的基础上继续往下排。
- 上下文配色：增加 user 一种颜色，和现有的 task/softirq/hardirq/NMI 并列。
- 耗时拆分：用户态帧的耗时可以拆成用户态自身代码、系统调用（内核）、被抢占或阻塞（off-CPU）。
- 用户态源码：uftrace 用 `--srcline` 记录源码行，或者在主机上用二进制文件和调试信息自己解析。新增 `--user-src DIR` 指定用户程序的源码目录，源码块的显示方式和内核一样。
- 文本视图：用户态的行由 uftrace 的记录合成，按时间插入 ftrace 的行之间，样式上和内核的行区分开；统一过滤器增加 `space:user|kernel` 字段。
- 开销：uftrace 插桩也有开销，用户态的耗时同样偏大。仿照 `--calibrate` 测量用户态的插桩开销，写进头部，复用现有的 *Overhead* 开关。

## 5. 分阶段

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **P0 验证** | 加入 submodule 和构建脚本，在 QEMU 和 Pi 4 上构建 uftrace；用 `-pg` 编译 `samples/demo_io.c`，手动同时采集 uftrace 和 ftrace（都用 `mono` 时钟）；写一个原型脚本合并后打印调用树 | 每个系统调用都能找到包住它的用户态帧，时间偏差在 1 us 以内；选定 3.3 节的方案 A 或 B；列出需要的 uftrace 补丁（可能为零） |
| **P1 导入** | `--uftrace DIR` 输入，合并模型（第 4 节），时间轴和详情面板显示用户态帧，文本视图穿插用户态的行 | 用 demo_io 生成一份报告，`sendto` 能从 `main` 一直展开到 `loopback_xmit`；单元测试覆盖合并逻辑和时钟偏差检查；新增 golden 配置 |
| **P2 采集一体化** | `fgcapture.sh --user`：设置 `trace_clock`，用 uftrace 启动命令，过滤 uftrace 自身线程，测量用户态插桩开销 | 一条命令得到两份数据；跟踪结束后 tracefs 恢复原样（现有的 restore 机制） |
| **P3 分析扩展** | 耗时拆分区分用户态和内核，Compare 支持用户态函数，导出包含用户态帧，`space:` 过滤字段，用户态源码 | 指南新增"用户态"一节并配截图；sample 换成带用户态的版本 |
| **P4 触发快照** | 借鉴 magic-trace：ftrace 用环形缓冲持续采集，指定函数被调用或延迟超过阈值时保存快照（ftrace `snapshot` trigger）；uftrace 侧用它的 trigger 配合 | 能抓到偶发的一次慢请求及其之前的若干毫秒 |

另外有一个随时可以做的独立功能：导出 Perfetto / Chrome trace JSON，便于和 uftrace、magic-trace 的结果在同一个查看器里对比。

## 6. 测试和 CI

- 夹具：在 QEMU 上采一份小的 uftrace 数据和 ftrace 数据，放进 `tests/data/`。只放转换需要的部分；如果选方案 A，就放 `uftrace dump` 的文本输出，这样单元测试不需要 uftrace 可执行文件。
- 单元测试：合并（拼接点、深度、父子关系）、时钟偏差检查、跟踪工具自身线程的过滤、`F_USER` 在前后端一致。
- 时间轴不变量（design.md 第 9 节）对合并后的数据同样成立。
- CI：增加一个任务，运行 `scripts/uftrace.sh check` 验证补丁能干净地应用；构建 uftrace 比较慢，可以只在补丁或 submodule 变化时构建。

## 7. 风险和待定问题

1. **目标程序的范围**：自己编译的 C/C++ 程序（`-pg` / `-fpatchable-function-entry`）最简单。没有源码的二进制需要 uftrace 的动态插桩（`-P.`，依赖 capstone），在 arm64 上的支持程度要在 P0 确认。
2. **数据量**：用户态的每个函数都插桩，数据量和开销都会很大。默认应限制范围，例如 `-F` 指定入口函数、`-D` 限制深度，或者 `--time-filter`。
3. **多线程和 fork**：要正确处理线程创建、exec 后符号变化（uftrace 有 session 机制 `sid-*.map`）。
4. **QEMU 7.2 内核的 `trace_clock` 是否支持 `mono`**：本次没能连上虚拟机，需要确认。
5. **uftrace 版本**：固定在哪个标签？建议用最新的发布版，升级流程见 2.2。
6. **许可证**：见 2.3 节。

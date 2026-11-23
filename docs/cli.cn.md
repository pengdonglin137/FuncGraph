# 命令行参数参考

[English](cli.md) · [返回 README](../README.cn.md#快速开始)

日常用 `fgreport` 读已有记录，缺记录时才用 `fgcapture`。下表覆盖 FuncGraph 自己实现的命令行及内核采集脚本；`fguftrace` 透传的原生参数单独说明。所有 Python 入口都接受 `-h` / `--help`（`fguftrace` 有采集器时转给采集器）。同一个短选项在不同工具中不一定同义，例如 `fgreport -n` 是预览命令，`fgcapture -n` 是排除内核调用树。

## fgreport

`fgreport [选项] TRACE ...` 或 `fgreport [选项] --uftrace DIR`。默认启用 Timeline、折叠和交互筛选。设有 `vmlinux` 时还会启用源码链接，并按依赖和配置选择快速解析及源码高亮。遇到 `--` 就结束 `fgreport` 的选项解析，后面的选项直接传给 `funcgraph`，例如 `fgreport -o task.html trace.txt -- --pid 1234`。

| 参数 | 作用与默认值 |
| --- | --- |
| `TRACE ...` | 一个或多个原始输入文件；`-` 表示 stdin。与 `--uftrace` 二选一。 |
| `--uftrace DIR` | 读取 uftrace 采集目录。已经导出的 JSONL 当普通文件传入。 |
| `--uftrace-bin PATH` | 指定补丁版 uftrace，覆盖自动选择。 |
| `-p NAME` / `--preset NAME` | 选用指定预设；未指定时，包装器看 `FG_PRESET`，其次看配置中的 `[fgreport] preset`。 |
| `-P` / `--no-preset` | 不由包装器选择预设；仍可手动传参，见 [README 示例](../README.cn.md#不用预设直接传参)。要让转换器忽略配置，再透传 `-- --no-config`。 |
| `-o PATH` / `--output PATH` | 输出 HTML 或报告目录。默认由首个输入取名，通常在输入旁生成 `.html` 或 `.report`；stdin 默认用 `stdin` 做文件名。 |
| `--open` | 生成后尝试打开浏览器。目录模式启动 `fgview`，用 `Ctrl+C` 停止；只生成产物时省略。 |
| `--report-mode MODE` | 选择 `single`、`single-windowed`、`sidecar` 或 `auto`，区别见 [funcgraph](#funcgraph)。省略且无配置覆盖时优先离线 HTML。 |
| `--windowed-compression none/gzip` | 窗口化单 HTML 的内嵌数据压缩方式。 |
| `--sidecar-threshold BYTES` | `auto` 切换到目录资源的字节阈值；必须显式指定。 |
| `--lite` | 不自动添加源码链接、快速解析或高亮选项。不是忽略配置或强制关闭符号解析。 |
| `--text` | 显式 `single` 下，阻止包装器因首份文本输入超过 150,000 行而关闭 Text；不会覆盖显式 `--no-text-view`。 |
| `-c BASE` / `--compare BASE` | 添加 Compare；位置参数是当前记录，`BASE` 是基线文件。 |
| `-n` / `--dry-run` | 打印将执行的 `funcgraph` 命令并退出，不生成报告或打开浏览器。 |
| `-h` / `--help` | 显示帮助。 |

子命令：

| 参数 | 作用 |
| --- | --- |
| `init NAME` | 创建或更新指定预设；默认写入 `~/.config/funcgraph/config.ini`，可用 `FG_CONFIG` 指定位置。只更新给出的键。 |
| `init --default` | 将该预设写为包装器的默认预设。 |
| `init --vmlinux`、`--kernel-src`、`--module-dirs`、`--module-srcs`、`--path-prefix`、`--btf` | 保存 [funcgraph](#funcgraph) 对应的符号、路径和类型选项；`init` 中每个选项接收一个值，多路径用引号包在同一个参数里。 |
| `init --base-url`、`--source-browser`、`--source-browser-rev`、`--source-url`、`--source-rev`、`--overhead` | 保存对应的源码、版本与开销选项。设置 `--source-browser` 且不传 `--base-url` 时，会清除该预设原有的自定义链接地址。 |
| `init --report-mode`、`--sidecar-threshold`、`--windowed-compression` | 保存对应的输出选项。 |
| `init --cross-compile PREFIX` | 在预设中保存 `CROSS_COMPILE` 工具链前缀；已设置的环境变量优先。 |
| `init --llvm VALUE` | 在预设中保存 `LLVM` 工具链选择；已设置的环境变量优先。 |
| `init -h` / `init --help` | 显示预设编辑选项。 |
| `presets` | 列出预设，`*` 标出包装器默认选择；无需选项。 |

## fgcapture

`fgcapture [模式] [选项] [-- COMMAND ARGS...]`。默认内核模式把参数交给 `fgcapture.sh`。**内核和混合采集需要 root 和可用的 tracefs**；用户态采集需要匹配的插桩和 uftrace，不访问 tracefs。建议用短、可重复的工作负载，从少量事件开始。完整例子见[采集指南](capture.cn.md)。

内核和混合模式在 `tracing_on` 不为 `0` 时拒绝采集，不接管已有会话。

| 参数 | 模式与作用 |
| --- | --- |
| `--kernel` | 可省略；不传任何模式选项时就是内核采集。输出内核函数图文本，无需重编译目标应用。 |
| `--user` | 只录用户态，输出 uftrace 目录。 |
| `--mixed` | 同一次运行采集用户态、内核函数和可用的调度、exec/exit 事件，输出 uftrace 目录。 |
| `-- COMMAND ARGS...` | 按给定参数执行目标；用户/混合必须提供。内核模式定位该命令及其子进程的函数记录，额外事件并不因此自动限于这些 PID。 |
| `-o FILE` | 内核输出文本，默认 `./ftrace.txt`。内核模式只支持短选项。 |
| `-o DIR` / `--output DIR` | 用户/混合输出新目录，默认 `user.data` / `mixed.data`，拒绝覆盖已有路径。另存采集证据目录 `.fgcapture`（追加在输出目录名之后）。 |
| `-d SECONDS` | 内核专用。不运行命令时录制这段时间，默认 1 秒。 |
| `-p PID[,PID]` | 内核专用。按 PID 限定函数记录，不能把它当成额外事件的 PID 过滤器。 |
| `-g FUNC[,FUNC]` / `--graph LIST` | 选择内核调用树的根。内核模式用 `-g`；混合可用长选项，默认选架构对应的 syscall 根。 |
| `-n FUNC[,FUNC]` / `--notrace LIST` | 排除这些内核调用树。内核模式用 `-n`；混合可用长选项。 |
| `-b KB` | 内核、混合的**每 CPU** 缓冲区大小，默认 16384 KiB。用户模式不使用。 |
| `--kernel-depth N` | 混合内核调用最大深度，默认 8；内核脚本不接受此选项。 |
| `--args` / `--no-args` | 默认请求参数。关闭时内核仍可记录 retval/retaddr；用户/混合关闭自动用户参数和返回值，混合也关内核 args。 |
| `--sched` | 内核：启用可用的调度与 exec/exit 事件；混合默认已启用；用户态可请求原生 perf 调度记录。 |
| `--no-sched` | 用户/混合：不录调度；混合仍保留可用的 exec/exit。内核脚本省略 `--sched` 即可。 |
| `-e SELECTOR` / `--event SELECTOR` | 内核/混合：增加 trace event，可重复。用 `SYSTEM:EVENT`、整个 `SYSTEM` 或带引号的 glob（例如 `'kvm:kvm_*'`）。 |
| `--uftrace-bin PATH` | 仅用户/混合；覆盖 uftrace 可执行文件的自动选择。 |
| `-A SPEC` / `--argument SPEC` | 用户/混合：转给 uftrace 的额外参数规格，可重复。 |
| `-R SPEC` / `--retval SPEC` | 用户/混合：转给 uftrace 的额外返回值规格，可重复。 |
| `--calibrate` | 内核专用；先运行开销标定，把结果写进文本头，报告可据此切换修正耗时。 |
| `--keep` | 内核专用；不恢复原 tracefs 设置。常规采集不要用。 |
| `-h` / `--help` | 通用入口显示 Python 帮助。内核参数用 `fgcapture.sh --help` 查看。 |

`fgcapture.sh` 保留上述内核接口，也接受 `-e` / `--event`（包括 `--event=SELECTOR`），并把 `--user` / `--mixed` 委派给 `fgcapture`。内核脚本可用 `FG_TRACEFS` 指定 tracefs 路径；混合模式使用自己探测到的 tracefs。

## funcgraph

底层转换器；没有 `fgreport` 的隐式 Timeline、折叠和筛选选项。`fgreport` 后的 `--` 适合传递下列选项，不必换入口。

| 输入与配置 | 作用与默认值 |
| --- | --- |
| `TRACE ...` | ftrace 文本、trace-cmd 容器或 FuncGraph JSONL；支持 `.gz`、`.xz`、`.bz2` 和一次 `-`（stdin）。多个文本输入可按时间合并。 |
| `--input-format auto/text/raw/uftrace` | 默认 `auto` 自动识别；也可明确指定解码类型。`uftrace` 文件指 FuncGraph JSONL，不是采集目录。 |
| `--uftrace DIR` | 用补丁版 uftrace 解出目录记录，不能与位置输入混用。 |
| `--uftrace-bin PATH` | 覆盖 uftrace 可执行文件。默认先找安装包内置版本，再找 PATH。 |
| `--raw-instance NAME` | 选择 trace-cmd 命名缓冲区，而非顶层缓冲区；也用于读 Compare 的基线。 |
| `--config FILE` | 指定 INI；否则依次查当前目录的 `funcgraph.ini` 和 `~/.config/funcgraph/config.ini`。 |
| `--preset NAME` | 加载 INI 的 `[preset NAME]`；显式命令参数优先于预设值。 |
| `--no-config` | 不加载任何配置文件。 |
| `--verbose` | 打印更详细的解析与诊断信息。 |
| `-h` / `--help` | 显示帮助。 |

| 输出与视图 | 作用与默认值 |
| --- | --- |
| `--output PATH` | 输出 HTML 或专用报告目录；默认 `ftrace_viz.html`。父目录应已存在。 |
| `--report-mode MODE` | `single`：完整 Text 单 HTML；`single-windowed`：按需绘制 Text 的离线单 HTML；`sidecar`：分块目录；`auto`：按阈值决定是否用分块资源。非 `single` 的显式模式需要 Timeline。省略时输出离线 HTML，有 Timeline 且 Text 达到 75,000 行时自动用 gzip 窗口化。 |
| `--windowed-compression none/gzip` | 内嵌 Text／源码的压缩方式；显式 `single-windowed` 默认 `none`，gzip 需要浏览器支持 `DecompressionStream`。 |
| `--sidecar-threshold BYTES` | `auto` 必填；完整未压缩 UTF-8 HTML **大于**这个非负字节数才拆分。不是输入文件大小。 |
| `--timeline` | 生成按任务／CPU 分析的时间轴。 |
| `--enable-fold` | 在 Text 中折叠匹配成对的调用。 |
| `--filter` | 启用交互式筛选（包括 CPU、PID、事件类型等）；不缩小报告数据。 |
| `--compare BASE_TRACE` | 用已有基线文件生成函数级 Compare。 |
| `--export FILE.json/FILE.csv` | 同时导出分析；JSON 含多类指标，CSV 是每函数统计。 |
| `--no-text-view` | 不生成逐行 Text；需要 Timeline 或 Compare。通常也跳过源码解析。 |
| `--keep-source` | 与 `--timeline --no-text-view` 一起保留解析出的 Timeline 源码和内联信息。 |
| `--overhead CHILD_NS[,SELF_NS]` | 设置每个子调用和自身的开销修正量（纳秒）；没有显式值时可用采集头的标定值。不改写记录值。 |

| 生成前筛选 | 作用 |
| --- | --- |
| `--pid PID[,PID]` | 按 PID 留下指定任务。 |
| `--comm RE[,RE]` | 按任务名 `comm` 的正则表达式选任务。 |
| `--cpu N[,N]` | 按 CPU 选择调用树的入口；为保留完整调用树，后续迁移到其他 CPU 的子调用或退出行也可能保留。 |
| `--time-range START:END` | 按调用起点选区间。可用绝对秒数或相对首个时间戳的偏移，如 `+2ms:+5ms`；任一端可留空。 |
| `--graph RE[,RE]` | 保留匹配函数为根的调用树。 |
| `--notrace RE[,RE]` | 排除匹配函数为根的调用树。 |

生成前筛选适用于 ftrace 文本和解码后的 trace-cmd 输入，不支持 uftrace 目录或 JSONL；Compare 使用相同条件筛选两侧。被裁掉的记录不能通过浏览器清除筛选来恢复。只想看看某个 CPU 或事件时，优先用 Text 下拉筛选。

| 源码、符号与类型 | 作用与默认值 |
| --- | --- |
| `--vmlinux FILE` | 匹配采集内核的 ELF，用于内核源码、链接和默认 BTF；普通重放可以不提供。 |
| `--kernel-src DIR` | 本地内核源码树根目录。 |
| `--user-src DIR` | 用户态记录对应的本地源码根目录。 |
| `--module-dirs DIR ...` | 查找内核 `.ko` 的目录。 |
| `--module-srcs DIR ...` | 内核模块源码根目录。 |
| `--auto-search` | 在常见位置自动查找模块目录。 |
| `--path-prefix PREFIX ...` | 处理编译机的路径前缀，使记录中的源码路径能对应本地文件。 |
| `--entry-offset N` | 函数入口加指定字节偏移；默认 0，用于匹配插桩后的入口地址。 |
| `--fast` | 使用 `fastfaddr2line` 实现解析 `vmlinux`；需要 `pyelftools`。 |
| `--use-external` | 强制用内核树的外部 `faddr2line`；和 `--fast` 同时指定时以后者为准。 |
| `--highlight-code` | 对嵌入的 C 源码做语法高亮；需要 Pygments。 |
| `--func-links` | 将可解析的函数名关联到源码位置。 |
| `--source-browser github/elixir` | 选择在线源码链接生成规则，默认 GitHub。 |
| `--source-browser-rev REV` | 在线链接使用的 tag、分支或提交；默认 GitHub `master` / Elixir `latest`。不改变嵌入源码。 |
| `--base-url URL` | 自定义在线源码链接基址，替代预置浏览器地址。 |
| `--module-url URL:MOD1,MOD2` | 按模块指定在线基址，可重复。 |
| `--source-url TEMPLATE` | 从含 `{path}`、可选 `{rev}` 的 URL 模板获取嵌入源码。 |
| `--source-rev REV` | 填充 URL 模板的版本；无 `--source-url` 时用本地 `--kernel-src` 仓库的 `git show REV:path`。 |
| `--source-cache DIR` | 远程源码文件的缓存目录，默认 `~/.cache/funcgraph/src`。 |
| `--tail-calls` | 需要 `--timeline`、`--vmlinux`、`pyelftools` 和 objdump；补充静态尾跳转证据，不代表 trace 确认执行了跳转。 |
| `--btf FILE ...` | 提供原始 BTF 或带 BTF 的 ELF；省略时可用已解析的内核、模块中的 BTF。 |
| `--no-btf` | 禁用 BTF，返回值分析退回数值启发式。 |

## fgview

| 参数 | 作用 |
| --- | --- |
| `REPORT` | 已生成的单 HTML 或含 `index.html` 的专用报告目录。一般直接打开单 HTML，目录报告用此查看器。 |
| `--port PORT` | 本机 loopback 监听端口。默认 `0`，自动选择可用端口。 |
| `--open` | 打开默认浏览器；否则只打印 URL。 |
| `--version` | 打印 FuncGraph 版本。 |
| `-h` / `--help` | 显示帮助。 |

只服务报告路径，不要把整个工作区或源码树作为服务根目录。结束时按 `Ctrl+C`。

## fguftrace

`fguftrace [COMMAND OPTIONS...]` 不是另一套参数：它优先运行安装包内的 uftrace，否则找 PATH 上的版本，原样透传参数。用 `fguftrace --help` 看总帮助，`fguftrace record --help`、`fguftrace dump --help` 等查安装版本的具体选项，`fguftrace --version` 查版本。FuncGraph 的补丁导出参数（例如 `dump --fgraph --srcline -d DIR`）见 [uftrace 集成](uftrace.cn.md)，普通报告生成不需要手工执行导出。`--uftrace-bin PATH` 是 `fgcapture`、`fgreport` 和 `funcgraph` 的选项，不是这个包装器的选项。

## fastfaddr2line

独立的源码地址解析工具；大部分用户只通过 `funcgraph --fast` 或 `fgreport` 间接使用。

| 参数 | 作用 |
| --- | --- |
| `EXECUTABLE` | 被解析的 ELF，默认 `vmlinux`。 |
| `ADDRESS ...` | 查询项：函数名、`函数名+0x偏移` 或 `函数名+0x偏移/0x长度`，例如 `vfs_read+0x10/0x100`。 |
| `-e FILE` / `--exe FILE` | 可在 `<ELF> <查询项...>` 之后覆盖 ELF；不能替代开头的 ELF 位置参数，因为首个位置参数仍会被当作 ELF。通常只传位置参数即可。 |
| `-f` / `--functions` | 兼容选项；默认就显示函数名。 |
| `-s` / `--basenames` | 只显示文件基名。 |
| `-i` / `--inlines` | 兼容选项；默认就请求内联帧。 |
| `-p` / `--pretty-print` | 兼容选项；默认就采用易读格式。 |
| `-l` / `--list` | 用源码上下文代替普通位置列表。 |
| `-C` / `--demangle` | 可被解析，但当前不会启用 C++ 名称还原。 |
| `-v` / `--verbose` | 打印详细日志。 |
| `--path-prefix PREFIX` | 映射编译机的源码前缀；可重复。 |
| `--module-srcs DIR` | 追加模块源码根目录；可重复。 |
| `--entry-offset N` | 对函数入口加指定字节偏移，默认 0。 |
| `-h` / `--help` | 显示帮助。 |

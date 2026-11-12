# 按场景使用 FuncGraph

先用已有记录熟悉报告，再决定是否重新采集。下列离线案例不需要 root、uftrace、
QEMU 或匹配的 `vmlinux`；重新采集时才需要相应的目标机、插桩程序和采集权限。
English: [examples.md](examples.md)。完整字段说明见[使用指南](guide.cn.md)。

## 选择一个起点

| 目标 | 使用案例 | 需要什么 |
| --- | --- | --- |
| 不采集，立即查看内核与混合调用 | [离线复现](#离线复现) | 安装 Python 包和本仓库的样例文件 |
| 不重新编译程序，只跟踪内核 | [纯内核案例](capture.cn.md#案例三不重新编译应用只跟踪内核) | root、空闲 tracefs |
| 查看多层用户函数、参数、返回值和调用点 | [用户态文件复制与 UDP](capture.cn.md#案例一从用户态文件复制到-udp-调用) | 插桩及调试信息、带补丁的 uftrace |
| 同次运行观察 exec → 用户态 → 内核态 → exit | [混合采集案例](capture.cn.md#案例二同次运行的-exec--用户态--内核态--exit) | 用户模式要求，加 root 和专用 tracefs |
| 跟踪真实工具及其依赖库 | [pigz + zlib](pigz.cn.md) | Git、GCC、Make、binutils；脚本固定两个上游提交 |
| 同一内核反复生成源码报告 | [复用内核预设](#复用内核预设) | 与采集时内核匹配的镜像和源码 |
| 延后加载大报告的 Text | [大报告与本地查看器](#大报告与本地查看器) | 完整报告目录、`fgview` |
| 分析 trace-cmd 的 `trace.dat` | [raw 输入指南](raw-input.cn.md) | 可选原生库和 C 编译器；不是裸 `trace_pipe_raw` 页 |

### 几个命令各负责什么

- `fgcapture`：推荐的采集入口；默认纯内核，`--user` / `--mixed` 选择模式。
- `fguftrace`：直接调用带补丁的 uftrace，用于原生导出和高级采集；不自动提供
  `fgcapture --mixed` 的 tracefs 状态保护。
- `funcgraph`：输入文本、raw 容器、JSONL 或 `--uftrace DIR`，生成报告。
- `fgreport`：给 `funcgraph` 加常用选项与内核预设；不负责采集。
- `fgview`：通过本地只读 HTTP 服务查看报告目录；不重新采集或转换记录。

用户 / 混合采集优先使用安装包内置的 uftrace，不必每次传路径。只有 Linux
x86_64 / aarch64 原生 wheel 带采集程序和 libmcount；Python-only wheel / 源码包
不带。架构、运行库和手动覆盖方法见[安装说明](uftrace.cn.md#安装和选择采集程序)。

## 离线复现

先安装发布包，取得本仓库，并在仓库根目录运行。安装 wheel 本身不会把 `samples/`
放入当前目录；只有查看已有 `sample.html` / `sample_mixed.html` 时连 Python 都不需要。

```bash
mkdir -p build/examples
funcgraph --no-config samples/sample_io.txt --timeline --enable-fold --filter \
    --output build/examples/kernel.html
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --output build/examples/mixed.html
```

直接在浏览器打开两个 HTML。`--no-config` 避免个人默认配置引用其它内核。这两条命令
不会重建公开 HTML 中的内核源码和 BTF 类型信息；缺少匹配镜像时不猜测源码位置。
混合 JSONL 已保存参数和调用点元数据，因此无需本机 uftrace 即可读取。

| 报告 | 建议操作 | 核对内容 |
| --- | --- | --- |
| `kernel.html` | 搜索 `__x64_sys_recvfrom`，查看返回值 | 空队列的非阻塞接收返回 `-EAGAIN`，不是整次运行失败 |
| `mixed.html` | 选中 `dispatch_datagram` | 五个参数有名字和值，Call site 是 `send_frame+偏移/大小` |
| `mixed.html` | 分别选中两次 `receive_frame` | 调用行分别为 `demo_mixed.c:47` 和 `:49`，而不是函数入口 |
| `mixed.html` | 进入 `__x64_sys_sendto` | 匹配的用户 / 内核边界有分界标识，调用栈区分两种地址空间 |

这里的 `--user-src samples` **只适用于已有公开 JSONL**，其路径是 `demo_mixed.c`。
按[编译与采集案例](capture.cn.md#最短流程)新抓的记录通常保存 `samples/demo_mixed.c`，
应在仓库根目录传 `--user-src "$PWD"`。以记录中的路径为准；源码根不会重写路径。

### 用 fgreport 缩短命令与添加对比

```bash
fgreport -P samples/sample_mixed.jsonl -o build/examples/mixed-wrapper.html -- \
    --no-config --user-src "$PWD/samples"
fgreport -P -c samples/sample_io.txt samples/sample_io_slow.txt \
    -o build/examples/compare.html -- --no-config
```

`-P` 禁用内核预设；`--` 后是传给 `funcgraph` 的参数，这里的 `--no-config` 同时
禁用个人配置。不要把 `--user-src` 放到 `fgreport` 自身参数区。Compare 的基准是
`sample_io.txt`，当前记录是 `sample_io_slow.txt`；两份记录的内核与采集开销不同，
这里只演示交互，不应据此认定某次提交造成性能回退。

## 复用内核预设

先完成[混合采集案例](capture.cn.md)，得到 `mixed.data/`。将下面变量设为**采集时**
内核对应的值，而不是分析机的当前内核。网页版本必须在所选仓库中真实存在；定制内核
可将 `KERNEL_WEB_REV` 设为上游基线，但应注明它和定制源码可能存在行号差异。

```bash
fgreport init target-kernel --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_WEB_REV"
fgreport presets
fgreport -n -p target-kernel --uftrace mixed.data -o mixed-source.html -- \
    --user-src "$PWD"
fgreport -p target-kernel --uftrace mixed.data -o mixed-source.html -- \
    --user-src "$PWD"
```

`VMLINUX` 是带调试信息和所需 BTF 的匹配镜像；`KERNEL_SRC` 是对应源码目录；
`KERNEL_REV` 固定内嵌内核源码，`KERNEL_WEB_REV` 固定 GitHub 链接。
先用 `-n` 检查实际命令，再去掉 `-n` 执行。此例显式选择预设，不改变默认预设。
`--user-src` 仍单独传入，绝不借用内核源码根或内核网页链接。
只需要结构时用前面的无预设流程，不要凑一个不匹配的 `vmlinux`。

## 大报告与本地查看器

仍可用小样例演练目录报告，再换成自己的大 trace：

```bash
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --report-mode sidecar --output build/examples/mixed.report
fgview build/examples/mixed.report --open
```

用终端打印的本地 HTTP 地址打开，按 Ctrl+C 停止服务。共享时复制**整个目录**，
不要只复制或以 `file://` 打开 `index.html`。Timeline 默认先打开，已保存的 Text 偏好或
行号锚点可覆盖初始视图。Text 在首次访问时加载；进入后完整模型与 DOM 仍常驻，
sidecar 不是窗口化或全局内存上限。

也可只在完整 HTML 超过明确的字节阈值时切换；下面的 64 MiB 是示例，不是默认值：

```bash
fgreport -P --report-mode auto --sidecar-threshold 67108864 \
    samples/sample_io.txt -o build/examples/kernel-auto.report -- --no-config
```

小于或等于阈值时仍在该独立目录里生成单文件 `index.html`。目录输出不要与输入 trace
或源码混放。更多加载、取消、重试及只保留 Timeline 的选项见[查看器指南](viewer.cn.md)。

## 自己采集后保留什么

三种模式的编译、执行、核对及状态恢复步骤已集中在[统一采集指南](capture.cn.md)，
无需从不同文档拼接原生选项。pigz 案例还会验证解压后的内容与输入相同。

| 阶段 | 应保留与核对的内容 |
| --- | --- |
| 运行前 | 插桩及编译选项、应用 / 库提交、采集内核版本、匹配镜像与源码版本 |
| 采集后 | 完整 `*.data/` 和相邻 `*.data.fgcapture/`；核对恢复结果、退出码及丢失记录 |
| 离线分析前 | 用 `fguftrace dump --fgraph --srcline -d mixed.data > mixed.jsonl` 导出；保留源码根对应关系 |
| 分享报告 | 单文件 HTML 可直接分享；sidecar 必须完整复制；报告不能代替原始采集用于重新导出 |

新报告不会恢复旧记录中缺失的参数、调用点、丢失事件或源码映射。`complete:true` 仅表示
导出流结束，不证明没有丢失；时间范围覆盖 exec 到 exit 也不意味着记录了所有函数。
疑难情况先查[排查清单](capture.cn.md#保存共享与排查)，不要直接清空其它 tracefs 会话。

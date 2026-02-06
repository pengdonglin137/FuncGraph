# FuncGraph - 专业 Linux ftrace 可视化工具

![FuncGraph 可视化示例](sample.png)

## FuncGraph 是什么？

FuncGraph 将 `function_graph` 的 ftrace 输出转换为交互式、可过滤的 HTML 报告，适用于内核开发与性能排查：支持源码跳转、高速地址解析、参数/耗时过滤与键盘友好折叠。

## 速查（Cheat Sheet）

- 常用选项：`--fast`、`--filter`、`--func-links`、`--highlight-code` 🔧
- 耗时过滤：`>10`、`<5&&>2`、`sort:desc` ⏱️
- 参数过滤示例：`skb=...`、`do_xxx(arg=1)` 🔎
- 键盘：Tab 聚焦折叠图标；Enter 切换折叠；Esc 清除选择 ⌨️

## 主要特性

- 交互式 ftrace 可视化与折叠/展开
- 可点击源码链接（在线或本地），支持高亮选项
- 高性能地址解析（`fastfaddr2line.py`）
- 多维度过滤（CPU/PID/参数/耗时）与候选建议
- 键盘友好导航与可访问折叠控制

---

<!-- Installation & Usage continues below -->

## 安装与使用

### 需求

- Python 3.6+
- addr2line（来自 binutils）
- 可选：Pygments（用于语法高亮）

### 基本用法

```bash
python3 funcgraph.py trace.txt \
    --vmlinux /path/to/vmlinux \
    --kernel-src /path/to/kernel/src \
    --module-dirs /path/to/modules \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --filter \
    --fast \
    --highlight-code \
    --output result.html
```

### 参数说明

下表文档了 `funcgraph.py` 支持的每个命令行选项，包括类型、默认值和示例。

| 参数 | 类型 / 默认值 | 说明 | 示例 |
|------|----------------|------|------|
| `ftrace_file` | 路径（必需） | 要解析和可视化的 ftrace 输出文件路径 | `trace.txt` |
| `--vmlinux` | 路径（必需） | vmlinux 二进制文件路径（用于地址 -> 源码解析） | `--vmlinux /path/to/vmlinux` |
| `--kernel-src` | 路径 | 内核源码树根目录（用于本地源码链接和语法高亮） | `--kernel-src /usr/src/linux` |
| `--module-dirs` | 路径... | 搜索内核模块的目录（可指定多个） | `--module-dirs /lib/modules /usr/lib/modules` |
| `--module-srcs` | 路径... | 模块源码目录用于模块特定的源码链接 | `--module-srcs /path/to/module/src` |
| `--base-url` | URL | 在线源码链接的基础 URL（如 Bootlin） | `--base-url https://elixir.bootlin.com/linux/v6.18/source` |
| `--module-url` | url:mods（可追加） | 将特定模块映射到源码 URL；格式：`url:mod1,mod2`。可指定多次。 | `--module-url https://url1.com:modA,modB` |
| `--output` | 路径（默认：`ftrace_viz.html`） | 生成的 HTML 输出文件路径 | `--output result.html` |
| `--auto-search` | 标志 | 自动搜索常见模块目录（添加常见的 `/lib/modules` 路径） | `--auto-search` |
| `--verbose` | 标志 | 启用详细日志用于调试和诊断 | `--verbose` |
| `--fast` | 标志 | 使用捆绑的 `fastfaddr2line.py` 更快处理 vmlinux | `--fast` |
| `--use-external` | 标志 | 强制使用外部 `faddr2line`/`addr2line`（与 `--fast` 互斥） | `--use-external` |
| `--highlight-code` | 标志 | 启用 HTML 中 C 源码语法高亮（需要 Pygments） | `--highlight-code` |
| `--path-prefix` | 路径... | 在将 addr2line 路径映射到 kernel-src 时要删除/替换的路径前缀 | `--path-prefix /home/user/build/kernel` |
| `--filter` | 标志 | 在 HTML 中包含交互式过滤框（CPU/PID/参数/耗时） | `--filter` |
| `--func-links` | 标志 | 为函数名添加可点击的源码链接（添加一些处理开销） | `--func-links` |
| `--entry-offset` | int（默认：`0`） | 要添加到函数入口地址的偏移（对打补丁的函数有用） | `--entry-offset 8` |
| `--enable-fold` | 标志 | 启用函数调用折叠 UI（折叠/展开调用块） | `--enable-fold` |

**说明：**
- `--fast` 和 `--use-external` 不应同时使用；如果都指定了，`--use-external` 会被忽略。
- `--module-url` 支持纯 URL（对所有模块默认）或 `url:mod1,mod2` 映射；重复该标志以添加多个映射。

### 模块 URL 示例

```bash
# 为不同的模块设置不同的 URL
python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --module-url https://url1.com:mod1,mod2 \
    --module-url https://url2.com:mod3,mod4 \
    --module-url https://default.com \
    --filter --fast
```

规则：
- `mod1,mod2` → 使用 `https://url1.com`
- `mod3,mod4` → 使用 `https://url2.com`
- 其他模块 → 使用 `https://default.com`
- 无默认 URL → 使用 `--base-url`

### 路径前缀处理

```bash
# addr2line 返回: /home/user/build/kernel/fs/open.c
# 内核源码路径: /home/user/linux/fs/open.c

python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --kernel-src /home/user/linux \
    --path-prefix /home/user/build/kernel \
    --filter --fast
```

### 交叉编译与 LLVM

**交叉编译：**
```bash
export CROSS_COMPILE=aarch64-linux-gnu-
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

**LLVM 工具链：**
```bash
export LLVM=1
# 或 export LLVM=/usr/bin/
# 或 export LLVM=-10
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

## 抓取 Trace

### 推荐设置

```bash
cd /sys/kernel/tracing

# 停止当前追踪
echo 0 > tracing_on

# 启用推荐选项
echo 1 > options/funcgraph-retaddr    # 返回地址（必需）
echo 1 > options/funcgraph-proc       # 进程名和 PID
echo 1 > options/funcgraph-retval     # 返回值
echo 1 > options/funcgraph-args       # 函数参数

# 设置 tracer
echo function_graph > current_tracer

# 启动追踪（1 秒）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# 保存结果
cat trace > ~/ftrace.txt
```

### 选项说明

| 选项 | 用途 | 推荐 |
|------|------|------|
| `funcgraph-retaddr` | 提供源码映射的返回地址 | ⭐⭐⭐⭐⭐ |
| `funcgraph-proc` | 显示进程名和 PID 用于过滤 | ⭐⭐⭐⭐ |
| `funcgraph-retval` | 显示函数返回值用于调试 | ⭐⭐⭐⭐ |
| `funcgraph-args` | 显示函数参数用于分析 | ⭐⭐⭐⭐ |

## Fastfaddr2line 工具

### 独立使用

```bash
# 显示帮助
python3 fastfaddr2line.py -h

# 解析单个地址
python3 fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-f, --functions` | 显示函数名 |
| `-s, --basenames` | 仅显示文件名（无路径） |
| `-i, --inlines` | 显示内联函数 |
| `-p, --pretty-print` | 美化输出 |
| `-C, --demangle` | C++ 符号解析 |
| `--path-prefix` | 路径前缀替换 |
| `--module-srcs` | 模块源码目录 |
| `--entry-offset` | 入口地址偏移 |

## 使用示例

### 1. 抓取 Trace
```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-proc
echo 1 > options/funcgraph-retval
echo 1 > options/funcgraph-args
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
```

### 2. 生成 HTML
```bash
cd /vol_1t/Qemu/x86_64/funcgraph_visualization

python3 funcgraph.py ~/ftrace.txt \
    --vmlinx /path/to/vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --filter \
    --fast \
    --output result.html
```

### 3. 在浏览器中

打开 `result.html` 并使用过滤功能：
- **查找最慢的函数**：在耗时输入框输入 `sort:desc`
- **查找异常**：在耗时输入框输入 `>100||<0.1 sort:desc`
- **特定进程**：在 PID 输入框输入 `1234|5678`，在进程名输入框输入 `nginx|bash`
- **组合过滤**：CPU `0|1`，PID `1234`，耗时 `>5&&<50 sort:desc`
- **折叠调用**：点击函数入口行前的 `▶` 图标可折叠/展开调用块


## 项目结构

```
funcgraph_visualization/
├── README.md                    # 英文说明文档
├── README.cn.md                 # 中文说明文档
├── funcgraph.py                 # 主程序
├── fastfaddr2line.py            # 地址解析工具
├── ftrace.txt                   # 示例数据
└── sample.html                  # 示例截图
```

## 参考资料

- [ftrace 可视化工具](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace 可视化工具（续）](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [写了个 ftrace 可视化工具](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## 许可证

开源项目，欢迎贡献！

---

**版本**: v0.6
**最后更新**: 2026-02-06
**状态**: ✅ 生产就绪

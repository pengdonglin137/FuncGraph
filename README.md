

# FuncGraph

![FuncGraph可视化示例](sample.png)

## 介绍

FuncGraph是一个用 **AI** 开发的 ftrace 可视化工具，主要用于：

1. **可视化 funcgraph-retaddr 输出**：将 Linux 内核的 function_graph tracer 输出转换为交互式 HTML 格式，大幅提高通过 trace 直接定位代码行的效率
2. **快速 faddr2line 实现**：使用 Python 重写了 Linux 内核中的 faddr2line 功能，处理性能得到数量级的提升

## 功能特点

- **交互式 HTML 输出**：点击函数即可跳转到对应源代码位置
- **支持内核模块**：可解析内核模块的符号信息
- **源代码链接**：支持设置 base-url，直接链接到在线代码仓库（如 bootlin）
- **多模块 URL 支持**：支持为不同模块设置不同的源代码 URL
- **高性能处理**：fastfaddr2line.py 比传统 addr2line 方式快几个数量级
- **灵活的参数配置**：支持指定 vmlinux、内核源码、模块目录等
- **交叉编译和 LLVM 支持**：支持 CROSS_COMPILE 和 LLVM 环境变量，适配各种工具链
- **处理统计**：显示解析时间、总耗时等性能统计信息
- **过滤功能**：支持按 CPU、PID、进程名过滤 trace 行
- **实时进度显示**：Expand/Collapse 操作显示进度百分比

## 环境要求

- Python 3.6+
- gawk（用于某些数据处理功能）
- addr2line 工具（通常随 binutils 一起安装）
- ELF 分析库（可选，用于 fastfaddr2line.py）

## 安装

```bash
# 克隆仓库
git clone https://gitee.com/pengdonglin137/funcgraph_visualization.git
cd funcgraph_visualization

# 确保脚本有执行权限
chmod +x *.py
```

## 使用方法

### 参数说明

```bash
./funcgraph.py -h
```

参数说明：
- `ftrace_file`：ftrace 输出文件路径（必需）
- `--vmlinux VMLINUX`：vmlinux 文件路径
- `--kernel-src KERNEL_SRC`：内核源码根目录
- `--module-dirs [MODULE_DIRS ...]`：内核模块搜索目录
- `--module-srcs [MODULE_SRCS ...]`：模块源码根目录（可指定多个路径）
- `--base-url BASE_URL`：源代码链接的基础 URL
- `--module-url MODULE_URL`：模块 URL 映射（可多次指定，格式：url:mod1,mod2）
- `--output OUTPUT`：输出 HTML 文件路径
- `--auto-search`：自动搜索常见模块目录
- `--verbose`：启用详细调试输出
- `--fast`：使用 fastfaddr2line.py 处理 vmlinux
- `--use-external`：强制使用外部 faddr2line
- `--highlight-code`：启用 C 源代码语法高亮（需要 Pygments）
- `--path-prefix [PATH_PREFIX ...]`：剔除 addr2line 返回的源码路径中的前缀，得到相对路径（可指定多个路径）
- `--filter`：在 HTML 中启用过滤窗口（自动启用 --fast 模式）

### 用法示例

#### 基本用法

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --module-dirs /home/pengdl/work/linux-6.18/modules_install/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

#### 使用多个模块 URL

为不同模块设置不同的源代码 URL：

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --module-url https://url1.com:mod1,mod2 \
    --module-url https://url2.com:mod3,mod4 \
    --module-url https://default.com \
    --output output.html ftrace.txt
```

说明：
- `mod1,mod2` 使用 `https://url1.com`
- `mod3,mod4` 使用 `https://url2.com`
- 其他模块使用 `https://default.com`
- 如果没有指定默认 URL，则使用 `--base-url`

#### 启用语法高亮

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --highlight-code \
    --output output.html ftrace.txt
```

#### 单独使用 fastfaddr2line

```bash
# 查看帮助
./fastfaddr2line.py -h

# 解析单个地址
./fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

#### 使用交叉编译和 LLVM 工具链

对于交叉编译或使用 LLVM 工具链的内核，可以通过环境变量配置：

**交叉编译：**
```bash
# 设置交叉编译前缀
export CROSS_COMPILE=aarch64-linux-gnu-

# 使用 funcgraph.py 生成 HTML
./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

**LLVM 工具链：**
```bash
# 使用 LLVM 工具链（自动使用 llvm- 前缀）
export LLVM=1

# 或者指定 LLVM 路径前缀
export LLVM=/usr/bin/

# 或者使用 LLVM 版本后缀
export LLVM=-10

# 使用 funcgraph.py 生成 HTML
./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

**工具链说明：**
- `CROSS_COMPILE`：交叉编译前缀（如 `arm-linux-gnueabi-`）
- `LLVM`：LLVM 工具链配置
  - `LLVM=1`：使用 `llvm-` 前缀
  - `LLVM=/usr/bin/`：使用 `/usr/bin/llvm-` 前缀
  - `LLVM=-10`：使用 `llvm-` 前缀 + `-10` 后缀
- **注意**：faddr2line 只存在于内核源码的 `scripts/` 目录下，不受 LLVM/CROSS_COMPILE 影响
- 工具链信息会在 verbose 模式下显示，为未来扩展做准备

#### 使用 path-prefix 剔除路径前缀

当 addr2line 返回的源码路径与内核源码路径不一致时，使用 `--path-prefix` 剔除前缀：

```bash
# addr2line 返回：/home/user/build/kernel/fs/open.c
# 内核源码路径：/home/user/linux/fs/open.c
# 使用 path-prefix 剔除差异部分

./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --path-prefix /home/user/build/kernel \
    --output output.html ftrace.txt
```

**path-prefix 说明：**
- **主要作用**：剔除 addr2line 返回的源码路径中的前缀，得到相对路径
- **使用场景**：编译路径与源码路径不一致时
- **多个路径**：可以指定多个备选前缀，脚本会尝试匹配
- **结果**：生成的 HTML 中源码链接使用相对路径，更简洁

#### 使用过滤功能

启用 `--filter` 选项可以在 HTML 页面中添加过滤窗口，支持按 CPU、PID 和进程名过滤 trace 行：

```bash
# 启用过滤功能（自动启用 --fast 模式）
./funcgraph.py --filter --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --output output.html ftrace.txt
```

**过滤功能说明：**
- **自动启用 fast 模式**：`--filter` 会自动启用 `--fast` 模式
- **实时过滤**：在 HTML 页面中输入 CPU、PID 或进程名，实时过滤显示的行
- **Summary Bar 更新**：过滤后显示 "Filtered: X" 统计
- **Expand/Collapse 优化**：只对当前可见的行进行展开/折叠操作
- **过滤条件**：支持多个条件组合过滤

**使用场景：**
```bash
# 1. 生成带过滤功能的 HTML
./funcgraph.py --filter --vmlinux vmlinux --kernel-src /path/to/kernel --output result.html trace.txt

# 2. 在浏览器中打开 result.html
# 3. 在过滤窗口中输入：
#    - CPU: 0,1,2
#    - PID: 1234,5678
#    - Comm: "nginx"
# 4. 点击 Expand All 只会展开过滤后的行
```

## 抓取 trace 的方法

在 Linux 系统上执行以下命令来抓取 function_graph trace：

```bash
# 进入 tracing 目录
cd /sys/kernel/tracing

# 停止当前追踪
echo 0 > tracing_on

# 推荐配置：启用所有有用的 trace 选项
echo 1 > options/funcgraph-retaddr    # 函数返回地址（**必选**）
echo 1 > options/funcgraph-proc       # 显示进程名和 PID（**推荐**）
echo 1 > options/funcgraph-retval     # 函数返回值（**推荐**）
echo 1 > options/funcgraph-args       # 函数参数（**推荐**）

# 设置 function_graph tracer
echo function_graph > current_tracer

# 开始追踪（运行 1 秒后停止）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# 保存 trace 结果
cat trace > ~/ftrace.txt
```

**推荐选项说明：**
- `funcgraph-retaddr`：**必选** - 提供函数返回地址，用于定位源代码行
- `funcgraph-proc`：**推荐** - 显示进程名和 PID，便于过滤和分析
- `funcgraph-retval`：**推荐** - 显示函数返回值，便于调试
- `funcgraph-args`：**推荐** - 显示函数参数，便于分析调用关系

**注意：** 启用更多选项会增加 trace 文件大小，但提供更丰富的调试信息。FuncGraph 支持按 CPU、PID、进程名过滤，因此推荐启用这些选项。

## 项目结构

```
funcgraph_visualization/
├── README.md                    # 中文说明文档
├── README.en.md                 # 英文说明文档
├── funcgraph.py                 # 主程序：将 ftrace 转换为 HTML
├── fastfaddr2line.py            # 高性能地址解析工具
├── ftrace.txt                   # trace 数据示例
├── sample.png                   # 输出效果截图
└── sample.html                  # HTML 输出示例
```

## 工作原理

1. **解析 ftrace 输出**：funcgraph.py 解析 function_graph 格式的 trace 数据
2. **提取函数地址**：从 trace 中获取每个函数的返回地址
3. **符号解析**：使用 fastfaddr2line 或 addr2line 将地址转换为源代码位置
4. **生成 HTML**：构建交互式 HTML 页面，显示函数调用关系和源代码

## 参考文章

- [ftrace可视化工具迎来重大升级](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace可视化工具(续)](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [写了一个ftrace可视化工具，支持点击跳转](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## 许可证

本项目遵循开源许可证，具体信息请查看仓库中的 LICENSE 文件。

## 贡献

欢迎提交 Issue 和 Pull Request 来改进这个项目。
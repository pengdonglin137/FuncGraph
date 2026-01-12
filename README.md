

# funcgraph_visualization

![Funcgraph_retaddr可视化示例](sample.png)

## 介绍

funcgraph_visualization 是一个强大的 ftrace 可视化工具，主要用于：

1. **可视化 funcgraph-retaddr 输出**：将 Linux 内核的 function_graph tracer 输出转换为交互式 HTML 格式，大幅提高通过 trace 直接定位代码行的效率
2. **快速 faddr2line 实现**：使用 Python 重写了 Linux 内核中的 faddr2line 功能，处理性能得到数量级的提升

## 功能特点

- **交互式 HTML 输出**：点击函数即可跳转到对应源代码位置
- **支持内核模块**：可解析内核模块的符号信息
- **源代码链接**：支持设置 base-url，直接链接到在线代码仓库（如 bootlin）
- **高性能处理**：fastfaddr2line.py 比传统 addr2line 方式快几个数量级
- **灵活的参数配置**：支持指定 vmlinux、内核源码、模块目录等

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
./funcgraph_to_html.py -h
```

参数说明：
- `ftrace_file`：ftrace 输出文件路径（必需）
- `--vmlinux VMLINUX`：vmlinux 文件路径
- `--kernel-src KERNEL_SRC`：内核源码根目录
- `--module-dirs [MODULE_DIRS ...]`：内核模块搜索目录
- `--base-url BASE_URL`：源代码链接的基础 URL
- `--output OUTPUT`：输出 HTML 文件路径
- `--auto-search`：自动搜索常见模块目录
- `--verbose`：启用详细调试输出
- `--fast`：使用 fastfaddr2line.py 处理 vmlinux
- `--use-external`：强制使用外部 faddr2line

### 用法

#### 使用 fastfaddr2line

```bash
./funcgraph_to_html.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --module-dirs /home/pengl/work/linux-6.18/modules_install/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

#### 单独使用 fastfaddr2line

```bash
# 查看帮助
./fastfaddr2line.py -h

# 解析单个地址
./fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

## 抓取 trace 的方法

在 Linux 系统上执行以下命令来抓取 function_graph trace：

```bash
# 进入 tracing 目录
cd /sys/kernel/tracing

# 停止当前追踪
echo 0 > tracing_on

# 启动函数返回地址跟踪 （**必选**）
echo 1 > options/funcgraph-retaddr
# 启动函数返回值跟踪（可选）
echo 1 > options/funcgraph-retval
# 启动函数参数跟踪（可选）
echo 1 > options/funcgraph-args

# 设置 function_graph tracer
echo function_graph > current_tracer

# 开始追踪（运行 1 秒后停止）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# 保存 trace 结果
cat trace > ~/ftrace.txt
```

## 项目结构

```
funcgraph_visualization/
├── README.md                    # 中文说明文档
├── README.en.md                 # 英文说明文档
├── funcgraph_to_html.py         # 主程序：将 ftrace 转换为 HTML
├── fastfaddr2line.py            # 高性能地址解析工具
├── ftrace.txt                   # trace 数据示例
├── sample.png                   # 输出效果截图
└── sample.html                  # HTML 输出示例
```

## 工作原理

1. **解析 ftrace 输出**：funcgraph_to_html.py 解析 function_graph 格式的 trace 数据
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
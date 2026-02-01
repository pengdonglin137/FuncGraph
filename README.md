# FuncGraph - ftrace 可视化工具

![FuncGraph可视化示例](sample.png)

## 项目介绍

FuncGraph 是一个功能完整的 ftrace 可视化工具，主要功能：

1. **ftrace 可视化**：将 Linux 内核的 function_graph tracer 输出转换为交互式 HTML
2. **源码链接**：点击函数即可跳转到对应源代码位置
3. **高性能解析**：使用 Python 实现的快速地址解析工具
4. **智能过滤**：支持多维度实时过滤和排序
5. **函数调用折叠**：支持折叠/展开函数调用块，简化复杂调用栈的查看

## 核心功能

### 🎯 过滤系统

#### 支持的过滤类型
- **CPU 过滤**：正则表达式匹配 CPU 编号
- **PID 过滤**：正则表达式匹配进程 ID
- **进程名过滤**：正则表达式匹配进程名
- **返回值过滤**：支持数字、宏名和 `all` 选项
- **参数过滤**：字符串匹配函数参数
- **耗时过滤**：支持比较运算符和排序

#### 智能显示
- 自动检测 trace 数据类型
- 只显示有数据的过滤输入框
- 无数据时不显示过滤窗口

#### 悬停提示
- 鼠标悬停显示完整使用说明
- 自动定位，不遮挡输入
- 无需查看文档即可使用

#### 耗时过滤和排序
```javascript
// 基本过滤
>10              // 显示>10μs
<5&&>2           // 显示2-5μs
>100||<0.1       // 显示异常值

// 排序
sort:desc        // 从大到小排序
sort:asc         // 从小到大排序

// 组合使用
>10 sort:desc    // 显示>10μs，按从大到小排序
<5&&>2 sort:asc  // 显示2-5μs，按从小到大排序
```

#### 上拉菜单（Suggestions）
- 点击输入框显示候选词列表
- 输入时实时过滤候选词
- 键盘导航（上下箭头选择）
- 回车或点击选择

#### 前缀问题修复
```javascript
// 带前缀的耗时也能正确过滤
!145.859 us      // 显示值：145.859，实际值：245.859
>100 && <200     // 正确显示（使用显示值过滤）
sort:desc        // 正确排序（使用实际值排序）
```

### 📊 HTML 交互功能

#### 展开/折叠
- 点击 `+` / `-` 展开/折叠单个函数
- **Expand All**：展开所有可见行
- **Collapse All**：折叠所有可见行
- **进度显示**：操作时显示进度百分比

#### 函数调用折叠
- **折叠图标**：在可折叠的函数入口行前显示 `▶` 图标
- **折叠操作**：点击图标折叠/展开函数调用块
- **嵌套支持**：正确处理嵌套函数调用的折叠
- **状态保存**：折叠状态自动保存到 localStorage
- **图标切换**：折叠时显示 `▶`，展开时显示 `▼`

**折叠特性**：
- ✅ 只在函数入口行显示折叠图标
- ✅ 折叠时隐藏函数入口和出口之间的所有行
- ✅ 支持嵌套函数调用的折叠
- ✅ 折叠状态自动保存和恢复
- ✅ 图标状态实时切换

#### 过滤操作
- **Filter**：应用当前过滤条件
- **Clear**：清除所有过滤条件
- **实时统计**：显示 "Filtered: X / Total: Y"

#### 键盘导航
- `↑` / `↓` 或 `j` / `k`：在可展开行间移动（焦点跟随）
- `Enter`：展开/折叠选中行（在链接上按 Enter 会打开链接）
- `Esc`：清除所有选中状态（键盘选中、文本选择高亮、文本选择、Tab焦点）

**键盘导航特性**：
- ✅ 焦点自动跟随选中行
- ✅ Tab选中链接后，↑↓选新行会覆盖焦点
- ✅ 在链接上按 Enter 正常打开链接
- ✅ 在行上按 Enter 展开/折叠
- ✅ Esc 清除所有选中状态

#### 主题切换
- 浅色/深色模式
- 自动保存用户偏好

### 🔗 源码链接系统

#### 支持的配置
- **基础 URL**：设置源码仓库根路径
- **模块 URL**：为不同模块设置不同的源码 URL
- **路径前缀**：处理编译路径与源码路径不一致的情况

#### 链接类型
- **函数名链接**：点击函数名跳转到源码（需 `--func-links`）
- **返回地址链接**：点击返回地址跳转到源码
- **源码高亮**：支持语法高亮（需 Pygments）

### 🚀 性能优化

#### 编译器优化后缀处理
自动去除编译器优化后缀，显示原始函数名：

**支持的后缀**：
- `.isra.0`, `.constprop.0`, `.lto.0`, `.part.0`
- `.cold.0`, `.cold`, `.plt`, `.ifunc`
- `.llvm.0`, `.clone.0`, `.unk.0`

**示例**：
```
finish_task_switch.isra.0+0x150/0x4a8
↓
finish_task_switch+0x150/0x4a8
```

#### 高性能解析
- **fastfaddr2line.py**：Python 实现，比传统工具快数倍
- **外部工具支持**：可强制使用系统 faddr2line
- **批量处理**：优化的地址解析流程

## 安装和使用

### 环境要求
- Python 3.6+
- addr2line（binutils 包含）
- 可选：Pygments（语法高亮）

### 基本用法

```bash
# 最小配置
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast

# 完整配置
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

| 参数 | 说明 |
|------|------|
| `ftrace_file` | ftrace 输出文件（必需） |
| `--vmlinux` | vmlinux 文件路径（必需） |
| `--kernel-src` | 内核源码根目录 |
| `--module-dirs` | 内核模块搜索目录（可多个） |
| `--module-srcs` | 模块源码根目录（可多个） |
| `--base-url` | 源码链接基础 URL |
| `--module-url` | 模块 URL 映射（可多次指定） |
| `--output` | 输出 HTML 文件 |
| `--auto-search` | 自动搜索常见模块目录 |
| `--verbose` | 详细调试输出 |
| `--fast` | 使用 fastfaddr2line.py |
| `--use-external` | 强制使用外部 faddr2line |
| `--highlight-code` | 启用语法高亮 |
| `--path-prefix` | 路径前缀替换（可多个） |
| `--filter` | 启用过滤窗口 |
| `--func-links` | 函数名源码链接 |
| `--entry-offset` | 函数入口地址偏移 |

### 模块 URL 配置示例

```bash
# 为不同模块设置不同 URL
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
# addr2line 返回：/home/user/build/kernel/fs/open.c
# 内核源码路径：/home/user/linux/fs/open.c

python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --kernel-src /home/user/linux \
    --path-prefix /home/user/build/kernel \
    --filter --fast
```

### 交叉编译和 LLVM

**交叉编译**：
```bash
export CROSS_COMPILE=aarch64-linux-gnu-
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

**LLVM 工具链**：
```bash
export LLVM=1
# 或 export LLVM=/usr/bin/
# 或 export LLVM=-10
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

## 抓取 Trace

### 推荐配置

```bash
cd /sys/kernel/tracing

# 停止当前追踪
echo 0 > tracing_on

# 启用推荐选项
echo 1 > options/funcgraph-retaddr    # 返回地址（必选）
echo 1 > options/funcgraph-proc       # 进程名和 PID
echo 1 > options/funcgraph-retval     # 返回值
echo 1 > options/funcgraph-args       # 函数参数

# 设置 tracer
echo function_graph > current_tracer

# 开始追踪（1秒）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# 保存结果
cat trace > ~/ftrace.txt
```

### 选项说明

| 选项 | 作用 | 推荐度 |
|------|------|--------|
| `funcgraph-retaddr` | 提供返回地址，用于源码定位 | ⭐⭐⭐⭐⭐ |
| `funcgraph-proc` | 显示进程名和 PID，便于过滤 | ⭐⭐⭐⭐ |
| `funcgraph-retval` | 显示函数返回值，便于调试 | ⭐⭐⭐⭐ |
| `funcgraph-args` | 显示函数参数，便于分析 | ⭐⭐⭐⭐ |

## Fastfaddr2line 工具

### 独立使用

```bash
# 查看帮助
python3 fastfaddr2line.py -h

# 解析单个地址
python3 fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8

# 完整功能
python3 fastfaddr2line.py vmlinux \
    --functions \
    --basenames \
    --inlines \
    --pretty-print \
    arch_stack_walk+0x150/0x4a8
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-f, --functions` | 显示函数名 |
| `-s, --basenames` | 仅显示文件名（不含路径） |
| `-i, --inlines` | 显示内联函数 |
| `-p, --pretty-print` | 格式化输出 |
| `-C, --demangle` | C++ 符号解混淆 |
| `--path-prefix` | 路径前缀替换 |
| `--module-srcs` | 模块源码目录 |
| `--entry-offset` | 入口地址偏移 |

## 使用流程示例

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
    --vmlinux /path/to/vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --filter \
    --fast \
    --output result.html
```

### 3. 在浏览器中使用

打开 `result.html`，使用过滤功能：
- **找最慢函数**：耗时输入 `sort:desc`
- **找异常值**：耗时输入 `>100||<0.1 sort:desc`
- **特定进程**：PID 输入 `1234|5678`，进程名输入 `nginx|bash`
- **组合过滤**：CPU `0|1`，PID `1234`，耗时 `>5&&<50 sort:desc`
- **折叠函数调用**：点击函数入口行前的 `▶` 图标折叠/展开

## 项目结构

```
funcgraph_visualization/
├── README.md                    # 本文件
├── funcgraph.py                 # 主程序
├── fastfaddr2line.py            # 地址解析工具
├── ftrace.txt                   # 示例数据
└── sample.png                   # 效果截图
```

## 参考资料

- [ftrace可视化工具](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace可视化工具(续)](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [写了一个ftrace可视化工具](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## 许可证

开源项目，欢迎贡献！

---

**版本**: v0.6
**最后更新**: 2026-01-30
**状态**: ✅ 完整可用

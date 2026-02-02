# FuncGraph - 专业 Linux ftrace 可视化工具

![FuncGraph 可视化示例](sample.png)

## FuncGraph 是什么？

FuncGraph 将 `function_graph` 的 ftrace 输出转换为交互式、可过滤的 HTML 报告，适用于内核开发与性能排查：支持源码跳转、高速地址解析、参数/耗时过滤与键盘友好折叠。

## 快速开始

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

## 过滤系统（示例与语法）

- 基本比较：`>10`, `<5` 等
- 组合：`>5&&<50`（同时满足）
- 逻辑或：`>100||<0.1`
- 排序：`sort:desc` 或 `sort:asc`

示例：
```javascript
// 显示耗时 >10μs，并按从大到小排序
>10 sort:desc

// 显示耗时 2-5μs 并按从小到大排序
<5&&>2 sort:asc
```

过滤输入框包含候选建议、键盘导航（↑/↓）、回车选择与悬停提示。

---

## HTML 交互功能

- 折叠/展开：点击 `▶` / `▼` 或使用键盘 Enter，可折叠函数调用块
- Expand All / Collapse All：对当前视图中可见行操作
- 折叠状态保存至 `localStorage`，页面刷新后恢复
- 键盘导航：`↑`/`↓` 或 `j`/`k` 移动行，`Enter` 折叠/展开，`Esc` 清除选中状态

可访问性改进：
- 折叠图标可被 Tab 聚焦，按 Enter 与鼠标点击行为一致
- 非可折叠行不参与 Tab 顺序

---

## 源码跳转与路径映射

- `--base-url`：设置在线仓库基础 URL（如 Bootlin）
- `--module-url url:mod1,mod2`：为特定模块指定源码 URL
- `--kernel-src`：本地源码树，用于本地路径映射与高亮
- `--path-prefix`：路径前缀替换，用于修复 build 路径与源码路径不一致

示例：
```bash
python3 funcgraph.py trace.txt \
  --vmlinux vmlinux \
  --base-url https://elixir.bootlin.com/linux/v6.18/source \
  --module-url https://url1.com:mod1,mod2 \
  --module-url https://url2.com:mod3,mod4
```

---

## 性能与兼容性

- 自动去除后缀（`.isra.0`、`.constprop.0` 等）以还原函数名
- 内置 `fastfaddr2line.py` 支持批量与高并发解析
- 支持交叉编译和 LLVM 工具链

---

## 安装要求

- Python 3.6+
- `addr2line`（binutils）
- 可选：Pygments（语法高亮）

---

## 使用示例

最小调用：
```bash
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

完整示例：
```bash
python3 funcgraph.py trace.txt \
  --vmlinux /path/to/vmlinux \
  --kernel-src /path/to/kernel/src \
  --module-dirs /path/to/modules \
  --base-url https://elixir.bootlin.com/linux/v6.18/source \
  --filter --fast --highlight-code --output result.html
```

### 常用参数一览

| 参数 | 类型 / 默认 | 说明 |
|------|-------------|------|
| `ftrace_file` | 路径（必需） | 要解析的 ftrace 文件 |
| `--vmlinux` | 路径（必需） | vmlinux 文件路径，用于源码映射 |
| `--kernel-src` | 路径 | 本地内核源码树 |
| `--module-dirs` | 路径... | 模块二进制搜索目录（可重复） |
| `--module-srcs` | 路径... | 模块源码路径（可重复） |
| `--base-url` | URL | 在线源码基础 URL（如 Bootlin） |
| `--module-url` | url:mods（可重复） | 指定模块源码 URL 映射 |
| `--output` | 路径（默认：`ftrace_viz.html`） | 输出 HTML 文件 |
| `--fast` | 标志 | 使用 `fastfaddr2line.py` 提速 |
| `--use-external` | 标志 | 使用系统 `addr2line`（与 `--fast` 互斥） |
| `--highlight-code` | 标志 | 启用源码高亮（需 Pygments） |
| `--path-prefix` | 路径... | 地址到源码路径的前缀替换 |
| `--filter` | 标志 | 在 HTML 中包含交互式过滤框 |
| `--func-links` | 标志 | 为函数名添加源码链接 |
| `--entry-offset` | int（默认：0） | 对入口地址应用偏移 |
| `--enable-fold` | 标志 | 启用函数折叠功能 |

说明：`--fast` 与 `--use-external` 不应同时使用；若同时指定，`--use-external` 会被忽略。

---

## 抓取 Trace 建议

建议的 tracer 配置：
```bash
cd /sys/kernel/tracing
# 停止追踪
echo 0 > tracing_on
# 推荐选项
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-proc
echo 1 > options/funcgraph-retval
echo 1 > options/funcgraph-args
# 设置 tracer
echo function_graph > current_tracer
# 抓取（示例：1 秒）
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
# 保存
cat trace > ~/ftrace.txt
```

---

## fastfaddr2line（独立工具）

```bash
# 帮助
python3 fastfaddr2line.py -h
# 解析示例
python3 fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

---

## 项目结构

```
funcgraph_visualization/
├── README.md
├── README.cn.md
├── funcgraph.py
├── fastfaddr2line.py
├── ftrace.txt
└── sample.png
```

---

## 参考资料

- https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q
- https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw
- https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA

---

**版本**: v0.6  
**最后更新**: 2026-01-30

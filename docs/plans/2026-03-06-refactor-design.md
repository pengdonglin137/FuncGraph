# FuncGraph 重构设计文档

> **历史文档**：这是 2026-03-06 那次重构的计划，内容已过时（例如 `test_refactor.sh` 已被 `tests/run.sh` 取代，代码已拆分到 `fglib/`）。当前设计见 [docs/design.md](../design.md)。

> **状态**: 已完成（2026-03-07）— 含 P0/P1/P2 网页优化

## 1. 项目概述

FuncGraph (v0.7) 将 Linux 内核 ftrace `function_graph` 输出转换为交互式、自包含的 HTML 可视化页面。由两个 Python 文件组成：

| 文件 | 重构前行数 | 重构后行数 | 职责 |
|------|-----------|-----------|------|
| `fastfaddr2line.py` | 1,016 | 903 | ELF 符号解析 + addr2line 地址解析引擎 |
| `funcgraph.py` | 6,948 | 7,044 | ftrace 解析 + 地址解析调度 + HTML/CSS/JS 生成 |

**重构目标**：提高代码可读性和可维护性，同时**保持只有 2 个 Python 文件**的使用方式不变，**所有现有功能完整保留**。

**重构成果**: 引入 4 个核心类（`FtraceLine`, `FtraceParser`, `AddressResolver`, `HtmlGenerator`），最长函数从 ~4400 行降至 ~500 行，消除了重复代码，HTML 输出 byte-identical。

---

## 2. 现有功能完整清单

### 2.1 命令行参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `ftrace_file` | 位置参数 | ftrace 输出文件路径 |
| `--vmlinux` | 必需 | vmlinux ELF 文件路径 |
| `--kernel-src` | 可选 | 内核源码根目录 |
| `--module-dirs` | 多值 | .ko 模块搜索目录 |
| `--module-srcs` / `--module-src` | 多值 | 模块源码根目录 |
| `--base-url` | 可选 | 内核源码在线链接基础 URL（如 Elixir Bootlin） |
| `--module-url` | 可追加 | 模块专用 URL 映射（格式: `url:mod1,mod2`） |
| `--output` | 默认 `ftrace_viz.html` | 输出 HTML 文件路径 |
| `--auto-search` | 开关 | 自动搜索 `/lib/modules/` 下的模块目录 |
| `--verbose` | 开关 | 调试输出 |
| `--fast` | 开关 | 使用 `fastfaddr2line.py` 替代内核 faddr2line |
| `--use-external` | 开关 | 强制使用外部 faddr2line（与 --fast 互斥） |
| `--highlight-code` | 开关 | C 源码语法高亮（基于 Pygments 或内置正则） |
| `--path-prefix` | 多值 | 需要从文件路径中去除的路径前缀 |
| `--filter` | 开关 | 启用过滤框 UI |
| `--func-links` | 开关 | 将所有函数名解析为源码超链接 |
| `--entry-offset` | 整数，默认 0 | 函数入口偏移调整（配合 `-fpatchable-function-entry`） |
| `--enable-fold` | 开关 | 启用函数调用块折叠 |

### 2.2 数据处理管线

```
ftrace.txt
    │
    ▼
parse_ftrace_file()          ← 解析 ftrace 多种格式变体
    │
    ├── identify_fold_blocks()  ← 识别 { } 配对折叠块
    │
    ▼
call_faddr2line_batch()      ← 批量解析地址 → 源码位置
    │
    ├── vmlinux 函数            ← 直接解析
    ├── 模块函数                ← 查找 .ko → 分组解析
    │
    ▼
[可选] call_faddr2line_for_func_names()  ← --func-links 模式
    │
    ▼
generate_html()              ← 生成自包含 HTML（内联 CSS/JS）
    │
    ▼
output.html
```

### 2.3 Ftrace 解析支持的格式

- **CPU 格式**: `3)`, 带时间戳 `1324242.786252 | 5)`, 带行号 `84 3)`
- **PID/Comm 格式**: `pid/num`, `comm/pid`, `comm-pid`
- **函数调用格式**:
  - `func() {` — 函数入口
  - `func(args);` — 带参数调用
  - `func [module]() {` — 模块函数调用
  - `} /* func */` — 函数出口
  - `} /* func ret=xxx */` — 带返回值出口
  - `/* <-func+offset/length */` — 返回地址注释
  - `/* func+offset/length [module] */` — 无 `<-` 的地址注释
- **耗时格式**: `0.208 us`，带前缀 `$`/`@`/`#`/`!`/`+`（表示量级）
- **返回值**: `ret=xxx`，支持十进制、十六进制、`true`/`false`
- **Latency 模式**: 支持状态字符字段

### 2.4 地址解析

- **ELF 符号解析**: 通过 pyelftools 解析 `.symtab`，建立符号索引（按 section 分组，二分查找）
- **模糊匹配**: 去除编译器后缀（`.isra.N`, `.constprop.N`, `.lto.N`, `.llvm.N` 等）
- **addr2line 集成**: 持久子进程 + 哨兵值协议，逐地址解析
- **内核源码根目录自动检测**: 通过 `start_kernel` 符号的 addr2line 输出推断
- **路径清理**: 多层路径前缀匹配（绝对/相对路径、模块源码路径、通用前缀）
- **模块支持**: 自动查找 `.ko`/`.ko.debug`/`.ko.gz` 等，支持 `-`/`_` 互换
- **交叉编译**: `CROSS_COMPILE` 和 `LLVM` 环境变量
- **偏移量调整**: `adjust_function_offset()` 将非零偏移量减 1

### 2.5 HTML 生成的功能

#### 2.5.1 主题系统
- 浅色/深色两套完整主题（30+ CSS 自定义属性）
- localStorage 持久化主题选择
- 一键切换按钮

#### 2.5.2 布局结构
- 渐变标题（FuncGraph + 版本/作者徽章）
- 统计摘要栏（总行数、可展开行数、内核函数数、模块数、过滤计数）
- 可折叠信息面板（环境信息、处理统计、命令行参数）
- 固定顶部滚动进度条
- 浮动可拖拽菜单按钮（☰）
- 响应式布局（移动端适配 `@media max-width: 768px`）

#### 2.5.3 行渲染
- 行号（可点击锚点链接，点击复制 URL）
- ftrace 语法高亮（CPU、时间、函数名、地址、注释、符号、返回值 — 含错误码注解）
- 可展开内容面板（源码位置链接 + 内嵌源代码）
- C 源码语法高亮（关键字、字符串、注释、数字、内核类型/属性/宏）
- 函数名超链接（`--func-links` 模式）
- 折叠标记图标（▼/▶）

#### 2.5.4 源码链接
- 支持 Elixir Bootlin 格式（`#L{line}`）
- 支持 OpenGrok 格式（`#{line}`）
- 模块函数专用 URL 映射
- 路径自动清理（去除内核源码根、模块源码根、自定义前缀）

#### 2.5.5 折叠系统（Fold/Unfold）
- 基于 `{`/`}` 配对的函数调用块折叠
- 用栈跟踪：按 CPU + PID + 函数名 + 缩进级别匹配
- 折叠时隐藏所有内部行和 `}` 行，保留 `{` 行
- 折叠/展开状态持久化（localStorage）
- 支持嵌套折叠（子折叠状态在父展开时恢复）
- 视觉区分：入口行绿色底、出口行红色底、内部行蓝色底；折叠时入口行变红色
- **层级控制**: 数字键 1-9 折叠深度 ≥ N 的调用，0 展开全部
- **折叠摘要**: 折叠时显示子调用数、总耗时、返回值错误码

#### 2.5.5a 耗时热力着色
- 基于 ftrace 耗时前缀字符（`$@*#!+` 和空格）分 7 级背景色渐变
- ≤10us(绿) → >10us(浅绿) → >100us(黄) → >1ms(琥珀) → >10ms(橙) → >100ms(红) → >1s(深红)
- Python 生成时预计算 `data-heat`，浏览器端零开销
- Heatmap 按钮切换开关 + 固定阈值图例，状态持久化

#### 2.5.6 过滤系统
- **CPU 过滤**: 正则表达式（如 `0|1|2`、`[0-2]`）
- **PID 过滤**: 正则表达式
- **进程名过滤**: 正则表达式（如 `bash|python`、`^idle`）
- **返回值过滤**: 数字或错误码宏名（如 `-22` 或 `EINVAL`），`all` 显示所有已知错误码
- **函数参数过滤**: 大小写不敏感的正则匹配
- **耗时过滤**: 支持比较运算符（`>`, `>=`, `<`, `<=`）、逻辑运算符（`&&`, `||`）、排序（`sort:desc`, `sort:asc` 物理重排 DOM）
- 自动补全下拉菜单（拉上式显示）
- 悬停提示信息
- 过滤框显示/隐藏切换
- 过滤计数实时更新

#### 2.5.7 键盘导航
| 按键 | 功能 |
|------|------|
| `j` / `↓` | 下一个可展开行 |
| `k` / `↑` | 上一个可展开行 |
| `h` | 容器左滚 100px |
| `l` | 容器右滚 100px |
| `Enter` | 展开/折叠当前行内容面板 |
| `Space` | 向下滚动 80% 视口 |
| `Escape` | 清除所有选择状态 |
| `g` / `Home` | 跳到第一个可展开行 |
| `G` / `End` | 跳到最后一个可展开行 |
| `PageDown` | 向下滚动 90% 视口 |
| `PageUp` | 向上滚动 90% 视口 |
| `?` | 显示快捷键面板 |
| `Ctrl+A` | 全选行内容文本 |
| `1`-`9` | 折叠深度 ≥ N 的函数调用块 |
| `0` | 展开所有折叠块 |

#### 2.5.8 交互功能
- **单击**: 展开/折叠内容面板（200ms 延迟区分双击）
- **双击**: 三态循环选择（词 → 整行 → 重置）
- **全部展开/折叠**: 异步批处理（每批 50 行），显示进度百分比，可取消
- **复制可见内容**: 复制所有可见行的 trace 内容（不含行号）
- **锚点导航**: URL hash 定位 + 脉冲高亮动画
- **滚动进度条**: 顶部 3px 绿色进度指示

#### 2.5.9 状态持久化（localStorage）
- 主题选择
- 展开行状态
- 折叠块状态
- 过滤框可见性
- 信息面板状态
- 浮动按钮位置 (x/y)
- 快捷键提示显示状态
- 滚动位置
- 热力着色开关状态

### 2.6 错误码映射

- 完整的 Linux errno 映射表（-1 到 -531，共 140+ 个错误码）
- 含内核重启和 NFS 相关错误码（-512 到 -531）
- 正反双向映射（数字 ↔ 宏名）

### 2.7 fastfaddr2line.py 功能

- **参数**: `-e`, `-f`, `-s`, `-i`, `-p`, `-l`, `-C`, `-v`, `--path-prefix`, `--module-srcs`, `--entry-offset`
- **ELF 加载**: pyelftools 解析 `.symtab`，按 section 分组，预计算地址列表用于二分查找
- **符号查找**: 精确匹配 → 清理编译器后缀 → 宽松模糊匹配
- **长度验证**: 优先精确长度匹配，fallback 到无长度验证
- **addr2line 集成**: 持久子进程，哨兵值 `,` 终止协议
- **内核源码根自动检测**: 通过 `start_kernel` 的 addr2line 输出
- **源码上下文**: `--list` 模式显示目标行 ±5 行上下文
- **路径缩短**: 多层前缀匹配（内核根、路径前缀列表、模块源码目录）
- **输入格式**: `func+offset/length`、`func+offset`、纯函数名

---

## 3. 现有代码的问题分析

### 3.1 结构问题
- `generate_html()` 单函数约 4400 行，CSS/JS/HTML 全部以字符串拼接内联
- `parse_ftrace_file()` 单函数约 450 行，包含 6 处重复的 `line_data` 字典构造
- faddr2line 参数构建逻辑重复 4 次
- 路径清理/缩短逻辑在两个文件中各自实现，存在部分重复

### 3.2 可维护性问题
- CSS/JS 嵌入 Python 字符串中，无法获得 IDE 语法高亮和 lint
- 没有类型提示
- 变量命名不一致（`module_src` vs `module_srcs` vs `module_src_list`）
- 部分函数参数过多（`generate_html` 有 20+ 个参数）

### 3.3 健壮性问题
- `locals()` 检查（如 `'ret_value' in locals()`）不可靠
- 某些代码路径可能导致未初始化变量

---

## 4. 重构方案

### 4.1 约束
- **仍然只有 2 个 Python 文件**（`fastfaddr2line.py` + `funcgraph.py`）
- **所有现有功能 100% 保留**，不新增也不删减功能
- **命令行参数完全兼容**
- **生成的 HTML 仍为完全自包含**（零外部依赖）
- **Python 3.6+ 兼容性**

### 4.2 重构策略

#### 4.2.1 `fastfaddr2line.py` 重构

**当前结构**（自顶向下）：
```
Symbol, Section          — 数据类
parse_arguments()        — CLI 参数
log()                    — 日志
shorten_path()           — 路径缩短
load_elf_data()          — ELF 加载
find_matching_symbol()   — 符号查找
start_addr2line()        — addr2line 子进程
get_kernel_src_root()    — 源码根检测
resolve_addresses()      — 地址解析
print_source_code()      — 源码上下文（大量重复代码）
main()                   — 入口
```

**重构方向**：
1. 引入 `ElfResolver` 类，封装 ELF 加载、符号查找、addr2line 通信
2. 将 `print_source_code()` 中两处几乎相同的 200 行代码提取为 `_find_source_file()` 和 `_print_code_context()` 辅助方法
3. `shorten_path()` 逻辑整理，减少嵌套层级
4. 添加基本类型提示
5. 消除重复的路径搜索逻辑

**实际行数变化**: 1,016 行 → 903 行（减少 11%）

#### 4.2.2 `funcgraph.py` 重构

**当前结构**：
```
常量/配置                 ~180 行   (ERROR_CODE_MAP 等)
辅助函数                  ~430 行   (escape, highlight, path, url 等)
parse_ftrace_file()       ~450 行   (ftrace 解析)
identify_fold_blocks()    ~130 行   (折叠识别)
adjust_function_offset()  ~30 行
parse_list_output()       ~140 行   (faddr2line --list 输出解析)
call_faddr2line_batch()   ~290 行   (地址解析调度)
辅助函数                  ~500 行   (环境信息、参数格式化、模块URL解析、源码链接等)
generate_html()           ~4400 行  (CSS + JS + HTML 生成)
main()                    ~320 行   (CLI 入口)
```

**重构方向**：

将 `funcgraph.py` 内部按功能域组织为清晰的**区块**（使用注释分隔的逻辑模块），在单文件内实现良好的关注点分离：

```
# ============================================================
# 第0部分: 常量与配置 (~180行)
# ============================================================
APP_VERSION, APP_AUTHOR, APP_TITLE
ERROR_CODE_MAP, ERROR_CODE_NAME_MAP

# ============================================================
# 第1部分: 数据模型 (~50行, 新增)
# ============================================================
class FtraceLine:
    """ftrace 解析行的数据类，替代当前的 dict"""
    __slots__ = ['raw_line', 'expandable', 'func_info', 'raw_func_name',
                 'display_func_name', 'module_name', 'cpu', 'pid', 'comm',
                 'func_name_info', 'ret_value', 'params', 'duration',
                 'sort_duration', 'duration_raw', 'fold_id', 'fold_type',
                 'fold_ancestors', 'call_depth']

class ModuleInfo:
    """模块信息"""
    __slots__ = ['name', 'path', 'url']

# ============================================================
# 第2部分: 工具函数 (~200行, 精简)
# ============================================================
escape_html_preserve_spaces()
escape_for_pre()
clean_file_path()        # 统一路径清理
build_source_url()       # URL 构建
remove_compiler_suffix() # 编译器后缀去除
find_module_path()       # 模块文件查找

# ============================================================
# 第3部分: Ftrace 解析器 (~350行, 精简)
# ============================================================
class FtraceParser:
    """解析 ftrace 文件"""
    def parse(self, file_path, enable_fold=False) -> list[FtraceLine]
    def _parse_line(self, line) -> FtraceLine
    def _parse_cpu(self, line) -> int|None
    def _parse_pid_comm(self, line, func_start) -> tuple
    def _parse_duration(self, prefix) -> tuple
    def _parse_func_info(self, line) -> tuple
    def _identify_fold_blocks(self, lines)

# ============================================================
# 第4部分: 地址解析调度 (~300行)
# ============================================================
class AddressResolver:
    """调用 faddr2line 批量解析地址"""
    def resolve_batch(self, func_infos, target, use_list=False) -> dict
    def resolve_func_names(self, func_names) -> dict
    def _build_faddr2line_cmd(self, target, use_list) -> list
    def _parse_batch_output(self, output) -> dict
    def _parse_list_output(self, output) -> str  # HTML

# ============================================================
# 第5部分: 语法高亮 (~200行)
# ============================================================
def highlight_ftrace_line(text) -> str
def highlight_c_code(text) -> str

# ============================================================
# 第6部分: HTML 生成器 (~4000行, 内部拆分)
# ============================================================
class HtmlGenerator:
    def generate(self, parsed_lines, ...) -> str
    def _generate_css(self) -> str           # ~500行
    def _generate_header(self) -> str        # ~100行
    def _generate_summary(self, stats) -> str # ~50行
    def _generate_info_panel(self) -> str    # ~100行
    def _generate_filter_ui(self, data) -> str # ~200行
    def _generate_controls(self) -> str      # ~50行
    def _generate_content(self, lines) -> str # ~600行
    def _generate_line(self, line, idx) -> str # ~100行
    def _generate_expanded_content(self, line) -> str
    def _generate_javascript(self) -> str    # ~2200行
    def _js_state_management(self) -> str
    def _js_theme(self) -> str
    def _js_expand_collapse(self) -> str
    def _js_fold_unfold(self) -> str
    def _js_keyboard_nav(self) -> str
    def _js_filter(self) -> str
    def _js_autocomplete(self) -> str
    def _js_floating_controls(self) -> str
    def _js_misc(self) -> str               # 进度条、锚点、复制、双击等

# ============================================================
# 第7部分: 主入口 (~250行, 精简)
# ============================================================
def parse_arguments() -> argparse.Namespace
def main()
```

**核心改进点**：

1. **引入 `FtraceLine` 数据类**: 用 `__slots__` 替代 dict，消除 6 处重复的字典构造，提供类型安全和自动补全
2. **FtraceParser 类**: 将 450 行的 `parse_ftrace_file()` 拆分为多个职责单一的小方法
3. **AddressResolver 类**: 封装 faddr2line 调用逻辑，消除 4 处重复的命令构建
4. **HtmlGenerator 类**: 将 4400 行的 `generate_html()` 拆分为多个子方法，每个方法职责清晰
5. **统一路径工具函数**: 合并 `clean_file_path`、`get_relative_path`、`shorten_path` 的重复逻辑
6. **消除 `locals()` 检查**: 在 `_parse_line()` 中统一初始化所有字段

### 4.3 不变的部分

- **生成的 HTML 输出**：功能和外观完全不变
- **命令行接口**：所有参数名称、默认值、行为完全兼容
- **文件结构**：仍然是 2 个独立 Python 文件
- **依赖**：pyelftools、addr2line、pygments（可选）
- **Python 版本兼容性**：3.6+

---

## 5. 关键设计决策

### 5.1 为什么用类而不是继续用函数？

当前 `generate_html()` 有 20+ 个参数，内部大量使用闭包变量。用类封装后：
- 共享状态成为实例属性，减少参数传递
- 方法可以自由拆分而不需要传递大量上下文
- 但**不会过度设计**——每个类只在真正需要共享状态时才使用

### 5.2 为什么不把 CSS/JS 分离到外部文件？

**约束**：生成的 HTML 必须是自包含的（零外部依赖）。即使把 CSS/JS 写到单独文件然后在构建时读入，也会增加部署复杂度。当前用 Python 字符串模板是合理的，重构只是**将其组织到更小的方法中**，而不是改变内联策略。

### 5.3 为什么不加 dataclass 装饰器？

为保持 Python 3.6 兼容性，使用 `__slots__` 手动定义而非 `@dataclass`（需要 3.7+）。如果未来放弃 3.6 支持，可以迁移到 dataclass。

---

## 6. 实际成果

| 指标 | 重构前 | 重构后 | 说明 |
|------|--------|--------|------|
| 文件数 | 2 | 2 | 不变 |
| funcgraph.py 行数 | 6,948 | 7,187 | +239 行（类定义 + 可视化优化 + bug 修复） |
| fastfaddr2line.py 行数 | 1,016 | 903 | -113 行（-11%，消除重复） |
| 最长函数 | ~4,400 行 | ~500 行 | `_prepare_data()` 506 行 |
| 类 | 0 | 4 | FtraceLine, FtraceParser, AddressResolver, HtmlGenerator |
| 重复 dict 构造 | 7 处 | 0 | 统一为 FtraceLine() |
| 重复 cmd 构建 | 4 处 | 1 | 统一为 `_build_cmd()` |
| 重复源码搜索 | 2 处 (~200行) | 1 | 统一为 `_find_source_file()` |
| `locals()` 检查 | 3 处 | 0 | 改为变量预初始化 |
| 功能检查 | 21 项 | 24 项 | 100% 通过（新增 3 项：heatmap/call-depth/fold-summary） |
| HTML 内容对比 | — | IDENTICAL | 去除动态时间戳后 byte-identical |
| CLI 兼容性 | — | 100% | 所有原有公共函数保留为 wrapper |

### 提交历史

| # | Commit | 改动 |
|---|--------|------|
| 1 | `3b71a74` | 自测脚本 + 设计文档 |
| 2 | `b93e01d` | fastfaddr2line.py: 消除 ~200 行重复源码搜索 |
| 3 | `5642295` | FtraceLine 数据类，消除 7 处重复 dict 构造 |
| 4 | `6d5c173` | FtraceParser 类，拆分解析逻辑为小方法 |
| 5 | `69ebc4c` | AddressResolver 类，统一 faddr2line 命令构建 |
| 6 | `2b14401` | HtmlGenerator 类 + CSS 分离 |
| 7 | `79a1ef8` | JavaScript 分离 |
| 8 | `87fe0f7` | HTML 结构分离（_prepare_data, _generate_content_lines） |
| 9 | `aae43f9` | 清理：移除变量别名，添加区块注释 |

---

## 7. 网页可视化优化（2026-03-07）

重构完成后新增 3 项交互优化，均在 `funcgraph.py` 单文件内实现。

### 7.1 P0: 耗时热力着色

基于 ftrace 内置的耗时前缀字符（`$@*#!+` 和空格）将行分为 7 个热力等级，在 Python 生成时直接写入 `data-heat="1-7"` 属性，浏览器端零计算开销。

| 等级 | 前缀 | 含义 | 颜色 |
|------|------|------|------|
| 1 | ` ` | ≤10us | 绿 |
| 2 | `+` | >10us | 浅绿 |
| 3 | `!` | >100us | 黄 |
| 4 | `#` | >1ms | 琥珀 |
| 5 | `*` | >10ms | 橙 |
| 6 | `@` | >100ms | 红 |
| 7 | `$` | >1s | 深红 |

| 组件 | 改动 |
|------|------|
| Python | `_generate_content_lines()` 从 `duration_raw` 提取前缀字符，写入 `data-heat` |
| CSS | 7 级热力背景色 + 深色主题变体 + `heatmap-off` 关闭态样式 |
| JS | `applyDurationHeatmap()` 仅构建图例 + `toggleHeatmap()` 开关 |
| HTML | 工具栏中 "Heatmap" 按钮 + 固定阈值图例 |
| 持久化 | `lsGet/lsSet('heatmapEnabled')` — 按文件命名空间 |

### 7.2 P1: 折叠层级控制

按数字键 `1-9` 折叠深度 ≥ N 的所有函数调用块，`0` 键展开全部。

| 组件 | 改动 |
|------|------|
| Python | `_generate_content_lines()` 输出 `data-call-depth` 属性 |
| JS | `foldToDepth(maxDepth)` 遍历所有 entry 按深度折叠/展开 |
| JS | `unfoldAllBlocks()` 展开所有已折叠块 |
| 键盘 | keydown handler 中添加 `0-9` 数字键处理 |
| HTML | keyboard-hint 面板新增说明 |

### 7.3 P2: 折叠摘要信息

折叠时在 `{` 行末尾显示 `[5 calls, 12.3 us, ret=-22 EINVAL]` 格式的摘要。

| 组件 | 改动 |
|------|------|
| CSS | `.fold-summary` 及子 span（count/time/ret）着色 |
| JS | `addFoldSummary(foldId)` 统计子调用数、总耗时、返回值 |
| JS | `removeFoldSummary(foldId)` 展开时移除摘要 |
| JS | `_setFolded()` 中集成：折叠时添加、展开时移除 |
| JS | 页面加载时为已持久化的折叠块恢复摘要 |

### 7.4 UI 优化（2026-03-07）

| 改动 | 说明 |
|------|------|
| 紧凑页头 | 合并 h1 + summary + theme 按钮为单行 `.page-header`，节省 ~88px 高度 |
| 悬浮工具栏 | 按钮从竖排改为水平分组（导航 \| 展开/折叠 \| 工具），语义 CSS class |
| localStorage 命名空间 | 按文件 title hash 前缀，防止跨文件状态污染 |
| 耗时 tooltip | `title` 属性显示原始耗时值 |
| `prefers-color-scheme` | 首次访问自动检测系统深色/浅色模式 |
| 去除 console.log | 移除 21 条 debug 日志，保留 13 条 catch 内 warn |
| CSS 清理 | 修复 4 个未定义变量，删除 8 处重复声明 |

### 7.5 折叠系统优化（2026-03-07）

| 改动 | 说明 |
|------|------|
| `_setFolded()` | 原子折叠操作，不写 localStorage，所有折叠 API 委托给它 |
| 性能 | 合并重复 DOM 查询，展开后只恢复子 fold，批量 localStorage 写入 |
| 递归折叠 | Shift+Click 递归折叠/展开含所有子块 |
| CSS 图标动画 | ▶ + `rotate(90deg)` 替代 ▼/▶ 字符切换，0.15s 过渡 |
| 折叠引导线 | `.fold-guide` + `align-self:stretch` 实现全高连续竖线；entry 行 ▶ 图标底部向下延伸；exit 行 └ 折线收口 |
| 图标增大 | 20×20px 居中 + hover 背景，提升点击体验 |

### 7.6 解析与过滤 Bug 修复（2026-03-07）

| Bug | 修复 |
|-----|------|
| B1 | `ret_value` 不再被 Format 3/4 无条件覆盖，统一正则含 `true\|false` |
| B2 | PID/comm 解析优先匹配 `comm-PID`，`tgid/pid` 取第二个数字 |
| B3 | JS 用 `BigInt` 解析 64 位 hex 返回值，避免精度丢失 |
| B5 | CPU/PID/comm 为空的行在过滤时被隐藏 |
| B6 | `ret=true/false` 存为 `data-ret-bool` 属性 |
| B10 | `comm-PID` 正则限制 comm 必须含非数字字符 |
| B11 | `hasFilter` 用编译后的 regex 对象判断 |
| B12 | `duration_raw` 写入 title 属性前 HTML 转义 |

---

## 8. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| HTML 输出不一致 | 用现有测试数据对比重构前后的 HTML 输出 |
| 性能回退 | 类的开销极小，关键热路径（正则匹配、I/O）不变 |
| 重构范围蔓延 | 严格约束"不增不减功能" |
| 正则表达式行为变化 | 保留所有正则表达式不变，只移动到对应的方法中 |

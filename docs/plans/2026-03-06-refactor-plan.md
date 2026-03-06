# FuncGraph 重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重构 funcgraph.py 和 fastfaddr2line.py 的内部结构，保持 2 文件形态和 100% 功能/输出兼容。

**Architecture:** 将大函数拆分为类+小方法（FtraceLine 数据类、FtraceParser、AddressResolver、HtmlGenerator），消除重复代码，保持生成的 HTML 完全一致。

**Tech Stack:** Python 3.6+, pyelftools, addr2line, pygments（可选）

**自测策略:** 使用 `to_html_v2.sh` 同等参数生成 HTML，与重构前的参考输出对比。参考文件: `/tmp/funcgraph_reference.html` (md5: `cd7713563575b84e2613fc7f57a91277`)

---

### Task 0: 创建自测脚本

**Files:**
- Create: `test_refactor.sh`

**Step 1: 编写自测脚本**

```bash
#!/bin/bash
# 重构自测脚本 - 对比重构前后的 HTML 输出
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

REFERENCE="/tmp/funcgraph_reference.html"
OUTPUT="/tmp/funcgraph_test_output.html"
FTRACE="ftrace.txt"
KERNEL_DIR="/vol_1t/Qemu/x86_64/linux-6.18"

echo "=== FuncGraph Refactor Self-Test ==="

# 检查参考文件
if [ ! -f "$REFERENCE" ]; then
    echo "ERROR: Reference file not found: $REFERENCE"
    echo "Please generate it first with the original code."
    exit 1
fi

# 运行重构后的代码
echo "[1/4] Generating HTML with refactored code..."
python3 funcgraph.py --enable-fold --func-links --fast --filter --highlight-code \
    --vmlinux "$KERNEL_DIR/x86_64/vmlinux" \
    --kernel-src "$KERNEL_DIR" \
    --module-dirs "$KERNEL_DIR/x86_64/modules_install/" /vol_1t/Qemu/x86_64/modules_/ \
    --module-src /vol_1t/Qemu/x86_64/modules_/ "$KERNEL_DIR" \
    --path-prefix /vol_1t/Qemu/x86_64/modules/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --entry-offset 0 \
    --output "$OUTPUT" "$FTRACE" 2>&1

# 检查输出文件
if [ ! -f "$OUTPUT" ]; then
    echo "FAIL: Output file not generated"
    exit 1
fi

# 对比文件大小
echo "[2/4] Comparing file sizes..."
REF_SIZE=$(wc -c < "$REFERENCE")
OUT_SIZE=$(wc -c < "$OUTPUT")
SIZE_DIFF=$((OUT_SIZE - REF_SIZE))
SIZE_DIFF_ABS=${SIZE_DIFF#-}

echo "  Reference: $REF_SIZE bytes"
echo "  Output:    $OUT_SIZE bytes"
echo "  Diff:      $SIZE_DIFF bytes"

# 允许因时间戳等微小差异（<1KB）
if [ "$SIZE_DIFF_ABS" -gt 1024 ]; then
    echo "WARNING: Size difference > 1KB, checking content..."
fi

# 提取关键内容区域对比（去除时间戳等动态内容）
echo "[3/4] Comparing key content sections..."

# 提取 <div id="content"> 到对应的 </div> 之间的内容（主体数据区）
extract_content() {
    # 去除 Generated At 行（时间戳每次不同）
    # 去除 Processing Stats 行（耗时每次不同）
    grep -v "Generated At" "$1" | \
    grep -v "processing time" | \
    grep -v "Trace file parsing" | \
    grep -v "Vmlinux resolution" | \
    grep -v "Modules resolution" | \
    grep -v "Total processing" > "$1.cleaned"
}

extract_content "$REFERENCE"
extract_content "$OUTPUT"

if diff -q "$REFERENCE.cleaned" "$OUTPUT.cleaned" > /dev/null 2>&1; then
    echo "  Content: IDENTICAL"
    CONTENT_MATCH=true
else
    echo "  Content: DIFFERENT"
    echo "  First 20 differences:"
    diff "$REFERENCE.cleaned" "$OUTPUT.cleaned" | head -40
    CONTENT_MATCH=false
fi

# 功能完整性检查
echo "[4/4] Checking feature completeness..."
CHECKS_PASSED=0
CHECKS_TOTAL=0

check_feature() {
    local name="$1"
    local pattern="$2"
    CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
    if grep -q "$pattern" "$OUTPUT"; then
        echo "  ✓ $name"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))
    else
        echo "  ✗ $name (pattern not found: $pattern)"
    fi
}

check_feature "Theme toggle"        "toggleTheme"
check_feature "Fold/unfold"         "toggleFold"
check_feature "Keyboard navigation" "handleKeyboardNavigation"
check_feature "Filter system"       "applyFilter"
check_feature "Autocomplete"        "initAutocomplete"
check_feature "Expand all"          "expandAll"
check_feature "Collapse all"        "collapseAll"
check_feature "Copy visible"        "copyVisibleContent"
check_feature "Progress bar"        "updateProgressBar"
check_feature "Floating toggle"     "floatingToggle"
check_feature "Anchor links"        "updateAnchor"
check_feature "Double click"        "handleDoubleClick"
check_feature "Syntax highlight"    "hl-func"
check_feature "C code highlight"    "c-keyword"
check_feature "Error code map"      "EINVAL"
check_feature "Fold entry class"    "fold-entry"
check_feature "Dark theme"          'data-theme="dark"'
check_feature "Source links"        "location-link"
check_feature "Func name links"     "func-name-link"
check_feature "Duration filter"     "durationFilter"
check_feature "Keyboard hint"       "keyboardHint"

echo ""
echo "=== Results ==="
echo "Features: $CHECKS_PASSED/$CHECKS_TOTAL passed"
if [ "$CONTENT_MATCH" = true ]; then
    echo "Content: MATCH"
else
    echo "Content: MISMATCH (check diff above)"
fi

# 清理
rm -f "$REFERENCE.cleaned" "$OUTPUT.cleaned"

if [ "$CONTENT_MATCH" = true ] && [ "$CHECKS_PASSED" -eq "$CHECKS_TOTAL" ]; then
    echo ""
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo ""
    echo "=== SOME TESTS FAILED ==="
    exit 1
fi
```

**Step 2: 验证自测脚本可用**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED（用原始代码运行应当全部通过）

**Step 3: Commit**

```bash
git add test_refactor.sh
git commit -m "test: add refactor self-test script"
```

---

### Task 1: 重构 fastfaddr2line.py — 消除重复代码

**Files:**
- Modify: `fastfaddr2line.py`

**核心改动:**

1. 提取 `_find_source_file(file_path, kernel_src_root, options)` 辅助函数（约 80 行），从 `print_source_code()` 中两处约 200 行的重复搜索逻辑合并
2. 提取 `_print_code_context(full_path, line_num)` 辅助函数（约 15 行）
3. 整理 `shorten_path()` 减少嵌套层级
4. 添加关键函数的类型提示

**Step 1: 重构 fastfaddr2line.py**

关键：`print_source_code()` 中的"普通函数行"和"内联函数行"两个分支有 ~200 行几乎相同的代码，仅在输出前缀 `(inlined by)` 上有差异。提取为公共函数。

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: 额外验证 fastfaddr2line.py 独立运行**

Run:
```bash
python3 fastfaddr2line.py /vol_1t/Qemu/x86_64/linux-6.18/x86_64/vmlinux \
    "pick_next_task+0x154/0x700" "__schedule+0x154/0x700"
```
Expected: 输出函数源码位置（与重构前一致）

**Step 4: Commit**

```bash
git add fastfaddr2line.py
git commit -m "refactor: fastfaddr2line.py - extract shared source file search logic"
```

---

### Task 2: 重构 funcgraph.py — 引入 FtraceLine 数据类

**Files:**
- Modify: `funcgraph.py` (第 0-1 部分)

**核心改动:**

1. 在常量区之后添加 `FtraceLine` 类（`__slots__` 数据类）
2. 将 `parse_ftrace_file()` 中 6 处重复的 dict 构造替换为 `FtraceLine()` 实例化
3. 同步更新 `identify_fold_blocks()` 和 `generate_html()` 中的属性访问方式（`.get()` → 属性访问）

**注意:** `FtraceLine` 需要同时支持属性访问和 `.get()` 方法（通过添加 `get` 方法），以便分阶段迁移，避免一次性修改所有消费者。

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: introduce FtraceLine data class, eliminate repeated dict construction"
```

---

### Task 3: 重构 funcgraph.py — FtraceParser 类

**Files:**
- Modify: `funcgraph.py` (第 3 部分)

**核心改动:**

1. 将 `parse_ftrace_file()` 提取为 `FtraceParser` 类
2. 拆分为: `parse()`, `_parse_line()`, `_parse_cpu()`, `_parse_pid_comm()`, `_parse_duration()`, `_parse_func_info()`
3. 将 `identify_fold_blocks()` 移入为 `_identify_fold_blocks()` 方法
4. 保留 `parse_ftrace_file()` 作为兼容包装函数（内部委托给 FtraceParser）

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: extract FtraceParser class from parse_ftrace_file()"
```

---

### Task 4: 重构 funcgraph.py — AddressResolver 类

**Files:**
- Modify: `funcgraph.py` (第 4 部分)

**核心改动:**

1. 将 `call_faddr2line_batch()`, `call_faddr2line_for_func_names()`, `_call_faddr2line_for_functions()` 提取为 `AddressResolver` 类
2. 公共配置（faddr2line_path, path_prefix, module_srcs, entry_offset, verbose）成为实例属性
3. 消除 4 处重复的 faddr2line 命令构建逻辑，统一为 `_build_cmd()` 方法
4. 将 `parse_list_output()`, `adjust_function_offset()` 移入类中

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: extract AddressResolver class, unify faddr2line cmd building"
```

---

### Task 5: 重构 funcgraph.py — HtmlGenerator 类（CSS 部分）

**Files:**
- Modify: `funcgraph.py` (第 6 部分开始)

**核心改动:**

1. 创建 `HtmlGenerator` 类框架
2. 将 `generate_html()` 中的 CSS 字符串提取到 `_generate_css()` 方法
3. `generate_html()` 的其余部分暂时保持在 `generate()` 方法中

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: extract HtmlGenerator class, separate CSS generation"
```

---

### Task 6: 重构 funcgraph.py — HtmlGenerator（JS 部分）

**Files:**
- Modify: `funcgraph.py`

**核心改动:**

1. 将 `generate()` 中的 JavaScript 字符串提取到 `_generate_javascript()` 方法
2. 进一步拆分为: `_js_state_management()`, `_js_theme()`, `_js_expand_collapse()`, `_js_fold_unfold()`, `_js_keyboard_nav()`, `_js_filter()`, `_js_autocomplete()`, `_js_floating_controls()`, `_js_misc()`

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: separate JavaScript generation into focused methods"
```

---

### Task 7: 重构 funcgraph.py — HtmlGenerator（HTML 结构部分）

**Files:**
- Modify: `funcgraph.py`

**核心改动:**

1. 提取: `_generate_header()`, `_generate_summary()`, `_generate_info_panel()`, `_generate_filter_ui()`, `_generate_controls()`
2. 提取: `_generate_content()`, `_generate_line()`, `_generate_expanded_content()`
3. `generate()` 方法变为组装框架，调用各子方法

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: separate HTML structure generation into focused methods"
```

---

### Task 8: 统一工具函数 + 清理

**Files:**
- Modify: `funcgraph.py`

**核心改动:**

1. 合并 `clean_file_path()` 和 `get_relative_path()` 的重叠逻辑
2. 统一参数命名（`module_src` vs `module_srcs`）
3. 消除 `locals()` 检查
4. 整理 `main()` 函数（使用新的类接口）
5. 添加关键函数的类型提示

**Step 1: 实施改动**

**Step 2: 运行自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 3: Commit**

```bash
git add funcgraph.py
git commit -m "refactor: unify path utilities, clean up main(), add type hints"
```

---

### Task 9: 最终验证

**Step 1: 完整自测**

Run: `bash test_refactor.sh`
Expected: ALL TESTS PASSED

**Step 2: 代码统计对比**

Run:
```bash
echo "=== Line counts ==="
wc -l funcgraph.py fastfaddr2line.py
echo ""
echo "=== Longest function (approx) ==="
grep -c "def \|class " funcgraph.py
```

**Step 3: 手动浏览器验证**

打开 `/tmp/funcgraph_test_output.html`，人工检查:
- [ ] 主题切换正常
- [ ] 折叠/展开正常
- [ ] 过滤器正常（CPU/PID/进程名/返回值/耗时）
- [ ] 键盘导航正常（j/k/Enter/Space/g/G）
- [ ] 函数名链接可点击
- [ ] 源码面板可展开
- [ ] 浮动按钮可拖拽

**Step 4: Final commit**

```bash
git add -A
git commit -m "refactor: FuncGraph v0.6 code restructuring complete"
```

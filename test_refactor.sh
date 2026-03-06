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

# 去除动态内容行后对比
extract_content() {
    grep -v "Generated At" "$1" | \
    grep -v "processing time" | \
    grep -v "Trace file parsing" | \
    grep -v "Vmlinux resolution" | \
    grep -v "Modules resolution" | \
    grep -v "Total processing" | \
    grep -v "Vmlinux Time" | \
    grep -v "Module Time" | \
    grep -v '<div class="info-item"><div class="info-label">Output:' > "$1.cleaned"
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
check_feature "Keyboard navigation" "keydown"
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

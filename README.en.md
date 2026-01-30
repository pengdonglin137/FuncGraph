# FuncGraph

![Funcgraph_retaddr Visualization Example](sample.png)

**Version**: v0.6 | **Last Updated**: 2026-01-30 | **Status**: ✅ Production Ready

## Introduction

FuncGraph is a powerful ftrace visualization tool developed with **AI assistance**, primarily designed for:

1. **Visualizing funcgraph-retaddr output**: Converts the output of the Linux kernel's function_graph tracer into an interactive HTML format, significantly improving the efficiency of locating code lines directly through traces.
2. **Fast faddr2line implementation**: Rewrote the faddr2line functionality in the Linux kernel using Python, achieving an order-of-magnitude improvement in processing performance.
3. **Function call folding**: Supports folding/expanding function call blocks to simplify viewing of complex call stacks.

## What's New in v0.6 (2026-01-30)

### 🎯 Function Call Folding Feature

- ✅ **Function call folding/expanding**: Click the `▶` icon before function entry lines to fold/expand function call blocks
- ✅ **Fold icon display**: Only shows fold icon on function entry lines (not on return lines)
- ✅ **Nested function support**: Correctly handles folding of nested function calls
- ✅ **State persistence**: Fold state automatically saved to localStorage
- ✅ **Icon state switching**: Real-time icon state switching (`▶` ↔ `▼`)

### ⌨️ Enhanced Keyboard Navigation

- **↑↓j/k**: Navigate between expandable lines (focus follows selection)
- **Enter**: Expand/collapse lines (or open links when focused)
- **Esc**: Clear all selections (keyboard, text highlight, Tab focus)
- **Tab**: Navigate to links (works with keyboard navigation)

### 🛠️ Technical Improvements

- **Tabindex Support**: Lines are now focusable elements
- **Focus Management**: Automatic focus movement during keyboard navigation
- **Event Handling**: Smart distinction between link clicks and line interactions
- **Performance**: Optimized event processing

## Features

### Core Features
- **Interactive HTML output**: Click on functions to jump to the corresponding source code location
- **Kernel module support**: Parses symbol information of kernel modules
- **Source code linking**: Supports setting a base-url to directly link to online code repositories (e.g., bootlin)
- **Multiple module URL support**: Supports setting different source code URLs for different modules
- **High-performance processing**: fastfaddr2line.py is several orders of magnitude faster than the traditional addr2line approach
- **Flexible parameter configuration**: Supports specifying vmlinux, kernel source code, module directories, etc.
- **Cross-compilation and LLVM support**: Supports CROSS_COMPILE and LLVM environment variables for various toolchains
- **Processing statistics**: Displays parsing time, total duration, and other performance metrics

### Function Call Folding
- **Fold icon**: Displays `▶` icon before function entry lines
- **Fold operation**: Click icon to fold/expand function call blocks
- **Nested support**: Correctly handles folding of nested function calls
- **State persistence**: Fold state automatically saved to localStorage
- **Icon switching**: `▶` when folded, `▼` when expanded

**Folding features**:
- ✅ Only shows fold icon on function entry lines
- ✅ Hides all lines between function entry and exit when folded
- ✅ Supports nested function call folding
- ✅ Automatic fold state saving and restoration
- ✅ Real-time icon state switching

### Filtering & Navigation
- **Filter window**: Real-time filtering by CPU, PID, process name, and duration
- **Autocomplete suggestions**: Smart suggestions for filter inputs (CPU/PID/Process/Return)
- **Keyboard navigation**: ↑↓j/k navigation, Enter to expand/collapse, Esc to clear
- **Tab navigation**: Full keyboard support for links and interactive elements
- **Expand/Collapse**: Click or keyboard to show/hide function details

### UI/UX Enhancements
- **Professional title design**: Gradient colors, shadows, rounded badges, decorative divider
- **Theme switching**: Light/dark mode with automatic preference saving
- **Hover hints**: Contextual help for filter inputs
- **Duration filtering**: Advanced filtering with >, <, >=, <=, ==, != operators
- **Sorting support**: Sort trace lines by duration (asc/desc)
- **Compiler suffix removal**: Automatically removes optimization suffixes (`.isra.0`, `.constprop.0`, etc.)
- **Real-time progress**: Expand/Collapse operations show progress percentage

## Environment Requirements

- Python 3.6+
- gawk (used for certain data processing functions)
- addr2line tool (usually installed with binutils)
- ELF analysis library (optional, for fastfaddr2line.py)

## Installation

```bash
# Clone the repository
git clone https://gitee.com/pengdonglin137/funcgraph_visualization.git
cd funcgraph_visualization

# Ensure scripts have executable permissions
chmod +x *.py
```

## Usage

### Parameter Description

```bash
./funcgraph.py -h
```

Parameter explanation:
- `ftrace_file`: Path to the ftrace output file (required)
- `--vmlinux VMLINUX`: Path to the vmlinux file
- `--kernel-src KERNEL_SRC`: Root directory of the kernel source code
- `--module-dirs [MODULE_DIRS ...]`: Kernel module search directories
- `--module-srcs [MODULE_SRCS ...]`: Module source code root directories (can specify multiple paths)
- `--base-url BASE_URL`: Base URL for source code links
- `--module-url MODULE_URL`: Module URL mapping (can be specified multiple times, format: url:mod1,mod2)
- `--output OUTPUT`: Path to the output HTML file
- `--auto-search`: Automatically search common module directories
- `--verbose`: Enable detailed debug output
- `--fast`: Use fastfaddr2line.py to process vmlinux
- `--use-external`: Force use of external faddr2line
- `--highlight-code`: Enable C source code syntax highlighting (requires Pygments)
- `--path-prefix [PATH_PREFIX ...]`: Remove path prefixes from addr2line output to get relative paths (can specify multiple paths)
- `--filter`: Enable filter window in HTML (automatically enables --fast mode)

### Usage Examples

#### Basic Usage

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --module-dirs /home/pengdl/work/linux-6.18/modules_install/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

#### Using Multiple Module URLs

Set different source code URLs for different modules:

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --module-url https://url1.com:mod1,mod2 \
    --module-url https://url2.com:mod3,mod4 \
    --module-url https://default.com \
    --output output.html ftrace.txt
```

Explanation:
- `mod1,mod2` use `https://url1.com`
- `mod3,mod4` use `https://url2.com`
- Other modules use `https://default.com`
- If no default URL is specified, `--base-url` will be used

#### Enable Syntax Highlighting

```bash
./funcgraph.py --fast --vmlinux /home/pengdl/work/linux-6.18/vmlinux \
    --kernel-src /home/pengdl/work/linux-6.18 \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --highlight-code \
    --output output.html ftrace.txt
```

#### Using fastfaddr2line Independently

```bash
# View help
./fastfaddr2line.py -h

# Parse a single address
./fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

#### Using Cross-Compilation and LLVM Toolchains

For cross-compiled kernels or those using LLVM toolchains, you can configure through environment variables:

**Cross-compilation:**
```bash
# Set cross-compilation prefix
export CROSS_COMPILE=aarch64-linux-gnu-

# Use funcgraph.py to generate HTML
./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

**LLVM toolchain:**
```bash
# Use LLVM toolchain (automatically uses llvm- prefix)
export LLVM=1

# Or specify LLVM path prefix
export LLVM=/usr/bin/

# Or use LLVM version suffix
export LLVM=-10

# Use funcgraph.py to generate HTML
./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

**Toolchain explanation:**
- `CROSS_COMPILE`: Cross-compilation prefix (e.g., `arm-linux-gnueabi-`)
- `LLVM`: LLVM toolchain configuration
  - `LLVM=1`: Uses `llvm-` prefix
  - `LLVM=/usr/bin/`: Uses `/usr/bin/llvm-` prefix
  - `LLVM=-10`: Uses `llvm-` prefix + `-10` suffix
- **Note**: faddr2line only exists in the kernel source's `scripts/` directory and is not affected by LLVM/CROSS_COMPILE
- Toolchain information is displayed in verbose mode for future extensions

#### Using path-prefix to Remove Path Prefixes

When the source code paths returned by addr2line are inconsistent with the kernel source paths, use `--path-prefix` to remove prefixes:

```bash
# addr2line returns: /home/user/build/kernel/fs/open.c
# Kernel source path: /home/user/linux/fs/open.c
# Use path-prefix to remove the difference

./funcgraph.py --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --path-prefix /home/user/build/kernel \
    --output output.html ftrace.txt
```

**path-prefix explanation:**
- **Main purpose**: Remove path prefixes from addr2line output to get relative paths
- **Use case**: When compilation paths differ from source code paths
- **Multiple paths**: Can specify multiple alternative prefixes, script will try to match
- **Result**: Source code links in HTML use relative paths, more concise

#### Compiler Suffix Removal

FuncGraph automatically removes compiler optimization suffixes to display original function names, making trace analysis and syntax highlighting easier:

**Supported compilers:**
- ✅ GCC
- ✅ LLVM/Clang

**Supported suffix types:**

Common to GCC and LLVM:
- `.isra.0`, `.isra.1`, `.isra.N` - Function inlining optimization
- `.constprop.0`, `.constprop.1`, `.constprop.N` - Constant propagation optimization
- `.lto.0`, `.lto.1`, `.lto.N` - Link-time optimization
- `.part.0`, `.part.1`, `.part.N` - Partial inlining
- `.cold.0`, `.cold.1`, `.cold.N` - Cold path optimization
- `.cold` - Cold path (without number)
- `.plt` - PLT entry
- `.ifunc` - Indirect function
- `.const` - Constant function
- `.pure` - Pure function

LLVM/Clang specific:
- `.llvm.0`, `.llvm.1`, `.llvm.N` - LLVM-specific optimization
- `.clone.0`, `.clone.1`, `.clone.N` - Function cloning
- `.unk.0`, `.unk.1`, `.unk.N` - Unknown optimization

**Supports complex scenarios:**
- Multiple suffixes: `func.isra.0.constprop.1` → `func`
- With offset/length: `func.llvm.123+0x100/0x200` → `func+0x100/0x200`

**Processing examples:**
```
Original trace:  3)   0.208 us |  } /* finish_task_switch.isra.0+0x150/0x4a8 */
Display result:  3)   0.208 us |  } /* finish_task_switch+0x150/0x4a8 */

Original trace:  1)   0.123 us |  unwind_find_stack.constprop.0+0x20/0x50
Display result:  1)   0.123 us |  unwind_find_stack+0x20/0x50
```

**Benefits:**
- ✅ Clearer function names for better readability
- ✅ More accurate syntax highlighting
- ✅ Easier grep and text searching
- ✅ Preserves offset and length information

#### Using Filter Function

Enable the `--filter` option to add a filter window in the HTML page, supporting filtering trace lines by CPU, PID, process name, and duration:

```bash
# Enable filter function (automatically enables --fast mode)
./funcgraph.py --filter --fast --vmlinux vmlinux \
    --kernel-src /path/to/kernel \
    --output output.html ftrace.txt
```

**Filter function explanation:**
- **Auto-enables fast mode**: `--filter` automatically enables `--fast` mode
- **Real-time filtering**: Enter CPU, PID, process name, or duration in the HTML page
- **Autocomplete suggestions**: Smart suggestions appear as you type
- **Duration filtering**: Use operators like `>10`, `<5`, `>=100`, `<=200`, `==50`, `!=0`
- **Sorting**: Use `sort:asc` or `sort:desc` in duration filter to sort by duration
- **Combined filters**: Use `&&` to combine multiple conditions (e.g., `>10 sort:desc && cpu:0`)
- **Summary Bar update**: Shows "Filtered: X / Total: Y" statistics after filtering
- **Expand/Collapse optimization**: Only expands/collapses currently visible lines

**Usage scenario:**
```bash
# 1. Generate HTML with filter function
./funcgraph.py --filter --vmlinux vmlinux --kernel-src /path/to/kernel --output result.html trace.txt

# 2. Open result.html in browser
# 3. Use filter boxes:
#    - CPU: 0|1|2 or [0-2]          # Matches CPU 0, 1, 2
#    - PID: 1234|5678 or 0-100      # Matches PID 1234 or 5678
#    - Comm: nginx|bash or ^nginx    # Matches process name nginx or bash
#    - Duration: >10 sort:desc      # Show functions >10us, sorted descending
#    - Return: 0 or -22             # Filter by return value
# 4. Click Expand All to only expand filtered lines
# 5. Press Esc to clear all selections
# 6. Use Tab to navigate to links, Enter to open
# 7. Click ▶ icons to fold/expand function call blocks
```

**Advanced filter examples:**
```bash
# Duration > 10us, sorted descending, CPU 0 only
>10 sort:desc && cpu:0

# Duration between 5 and 100us, PID 1234 or 5678
>5 && <100 && pid:1234,5678

# Return value 0, process name starting with bash
return:0 && comm:^bash

# All functions > 100us, sorted ascending
>100 sort:asc
```

**Regular expression explanation:**
- `|`: OR operation, e.g., `0|1|2` matches 0, 1, or 2
- `[0-9]`: Character set, e.g., `[0-2]` matches 0, 1, or 2
- `^`: Start anchor, e.g., `^nginx` matches process names starting with nginx
- `$`: End anchor, e.g., `bash$` matches process names ending with bash
- `*`: Zero or more, e.g., `.*` matches any character

**Keyboard shortcuts in HTML:**
- `↑` / `↓` or `j` / `k`: Navigate between expandable lines
- `Enter`: Expand/collapse line (or open link if focused on link)
- `Esc`: Clear all selections (keyboard, text highlight, Tab focus)
- `Tab`: Navigate to interactive elements (links, buttons)
- `Shift+Tab`: Navigate backwards

## Method to Capture Traces

Execute the following commands on a Linux system to capture function_graph traces:

```bash
# Enter the tracing directory
cd /sys/kernel/tracing

# Stop current tracing
echo 0 > tracing_on

# Recommended configuration: Enable all useful trace options
echo 1 > options/funcgraph-retaddr    # Function return address (**required**)
echo 1 > options/funcgraph-proc       # Show process name and PID (**recommended**)
echo 1 > options/funcgraph-retval     # Function return value (**recommended**)
echo 1 > options/funcgraph-args       # Function arguments (**recommended**)

# Set the function_graph tracer
echo function_graph > current_tracer

# Start tracing (stop after running for 1 second)
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# Save the trace results
cat trace > ~/ftrace.txt
```

**Recommended Options:**
- `funcgraph-retaddr`: **Required** - Provides function return address for source code location
- `funcgraph-proc`: **Recommended** - Shows process name and PID for filtering and analysis
- `funcgraph-retval`: **Recommended** - Shows function return value for debugging
- `funcgraph-args`: **Recommended** - Shows function arguments for call relationship analysis

**Note:** Enabling more options will increase trace file size but provide richer debugging information. FuncGraph supports filtering by CPU, PID, and process name, so these options are recommended.

## Project Structure

```
funcgraph_visualization/
├── README.md                    # Chinese documentation
├── README.en.md                 # English documentation
├── funcgraph.py                 # Main program: convert ftrace to HTML
├── fastfaddr2line.py            # High-performance address parsing tool
├── ftrace.txt                   # Example trace data
├── sample.png                   # Screenshot of output effect
└── sample.html                  # Example HTML output
```

## Working Principle

1. **Parse ftrace output**: funcgraph.py parses trace data in function_graph format
2. **Extract function addresses**: Retrieve the return address of each function from the trace
3. **Symbol resolution**: Convert addresses to source code locations using fastfaddr2line or addr2line
4. **Generate HTML**: Build an interactive HTML page showing function call relationships and source code

## FAQ

### Q: Keyboard navigation doesn't work properly
A: This was fixed in v0.5. Make sure you're using the latest version. The HTML should include `tabindex="0"` on line containers and `focus()` calls in keyboard navigation.

### Q: Enter key doesn't open links
A: This was fixed in v0.5. Enter key now correctly distinguishes between links (opens link) and lines (expands/collapses). Update to the latest version.

### Q: Esc key doesn't clear selection
A: This was fixed in v0.5. Esc now clears all selection states including keyboard selection, text highlight, and Tab focus.

### Q: Autocomplete doesn't show suggestions
A: This was fixed in v0.5. All 4 input boxes (CPU/PID/Process/Return) now support autocomplete. Ensure you're using the latest version.

### Q: Title looks plain
A: This was upgraded in v0.5. The new title features gradient colors, shadows, rounded badges, and a decorative divider. Update to see the professional design.

### Q: How to use duration filtering?
A: Use operators in the duration filter box: `>10`, `<5`, `>=100`, `<=200`, `==50`, `!=0`. Combine with `sort:asc` or `sort:desc` for sorting.

### Q: How to combine multiple filters?
A: Use `&&` to combine conditions: `>10 sort:desc && cpu:0` or `>5 && <100 && pid:1234`

### Q: Function call folding icon doesn't show
A: Ensure trace data contains function entry and exit information. Folding feature requires complete function_graph data.

### Q: Icon doesn't switch after folding
A: This was fixed in v0.6. Icon should show `▶` when folded and `▼` when expanded. If still having issues, please update to the latest version.

### Q: Folding feature doesn't work
A: Check browser console for error messages. Folding feature requires complete function_graph data support.

## Reference Articles

- [Ftrace Visualization Tool Gets Major Upgrade](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [Ftrace Visualization Tool (Continued)](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [Wrote an ftrace Visualization Tool with Click-to-Jump Support](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## License

This project is licensed under an open-source license. For detailed information, please refer to the LICENSE file in the repository.

## Contribution

Issues and Pull Requests are welcome to improve this project.

---

**Version**: v0.6 | **Last Updated**: 2026-01-30 | **Status**: ✅ Production Ready

## 🔧 Latest Fixes (2026-01-30)

### Function Call Folding Feature
- ✅ Implemented function call folding/expanding feature
- ✅ Fold icon only displayed on function entry lines
- ✅ Supports nested function call folding
- ✅ Fold state automatically saved to localStorage
- ✅ Icon state real-time switching (▶ ↔ ▼)

### Keyboard Navigation Focus Management
- ✅ Keyboard navigation with automatic focus following
- ✅ Enter key distinguishes between links and lines
- ✅ Esc key clears all selection states (including Tab focus)
- ✅ Tab and keyboard navigation work seamlessly together

### Autocomplete Menu Optimization
- ✅ Fixed autocomplete menu functionality
- ✅ Fixed menu overlap with hints
- ✅ Supports all 4 input boxes (CPU/PID/Process/Return)

**Detailed fix information**: See `FIX_SUMMARY.md` and `FINAL_STATUS.md`
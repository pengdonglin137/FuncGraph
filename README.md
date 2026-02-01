# FuncGraph - Specialized Linux ftrace Visualization Tool

![FuncGraph Visualization Example](sample.png)

## Project Overview

FuncGraph is a feature-rich ftrace visualization tool for Linux kernel development, performance analysis, and debugging. Key features include:

1. **ftrace Visualization**: Converts Linux kernel function_graph tracer output into interactive HTML
2. **Source Code Linking**: Click function names to jump to corresponding source code locations
3. **High-Performance Parsing**: Fast Python-based address resolution
4. **Intelligent Filtering**: Multi-dimensional real-time filtering and sorting
5. **Call Stack Folding**: Fold/expand function call blocks to simplify complex call stacks

## Core Features

### 🎯 Filtering System

#### Supported Filter Types
- **CPU Filter**: Regex match for CPU number
- **PID Filter**: Regex match for process ID
- **Process Name Filter**: Regex match for process name
- **Return Value Filter**: Supports numbers, macro names, and `all` option
- **Parameter Filter**: String match for function parameters
- **Duration Filter**: Supports comparison operators and sorting

#### Smart Display
- Auto-detects trace data type
- Shows filter input boxes only when data is available
- Hides filter window when no data

#### Hover Hints
- Mouse hover shows full usage instructions
- Auto-positioning, does not block input
- No need to read docs to use

#### Duration Filtering & Sorting
```javascript
// Basic filtering
>10              // Show >10μs
<5&&>2           // Show 2-5μs
>100||<0.1       // Show outliers

// Sorting
sort:desc        // Sort descending
sort:asc         // Sort ascending

// Combined usage
>10 sort:desc    // Show >10μs, sort descending
<5&&>2 sort:asc  // Show 2-5μs, sort ascending
```

#### Suggestions Dropdown
- Click input box to show candidate list
- Real-time filtering of candidates as you type
- Keyboard navigation (up/down arrows)
- Enter or click to select

#### Prefix Handling Fixes
```javascript
// Duration with prefix is correctly filtered
!145.859 us      // Displayed: 145.859, actual: 245.859
>100 && <200     // Correct display (filter by shown value)
sort:desc        // Correct sorting (by actual value)
```

### 📊 HTML Interactive Features

#### Expand/Collapse
- Click `+` / `-` to expand/collapse a single function
- **Expand All**: Expand all visible lines
- **Collapse All**: Collapse all visible lines
- **Progress Display**: Shows progress percentage during operations

#### Call Stack Folding
- **Fold Icon**: Shows `▶` icon before foldable function entry lines
- **Fold Operation**: Click icon to fold/expand function call blocks
- **Nested Support**: Correctly handles folding of nested function calls
- **State Saving**: Fold state auto-saved to localStorage
- **Icon Switching**: `▶` when folded, `▼` when expanded

**Folding Features:**
- ✅ Fold icon only on function entry lines
- ✅ Hides all lines between entry and exit when folded
- ✅ Supports nested function call folding
- ✅ Fold state auto-saved and restored
- ✅ Real-time icon state switching

#### Filtering Actions
- **Filter**: Apply current filter conditions
- **Clear**: Clear all filter conditions
- **Live Stats**: Shows "Filtered: X / Total: Y"

#### Keyboard Navigation
- `↑` / `↓` or `j` / `k`: Move between expandable lines (focus follows selection)
- `Enter`: Expand/collapse selected line (Enter on a link opens the link)
- `Esc`: Clear all selection states (keyboard, text highlight, Tab focus)

**Keyboard Navigation Features:**
- ✅ Focus automatically follows selected line
- ✅ Tab to link, then ↑↓ selects new line and overrides focus
- ✅ Enter on link opens link
- ✅ Enter on line expands/collapses
- ✅ Esc clears all selection states

#### Theme Switching
- Light/Dark mode
- Auto-save user preference

### 🔗 Source Code Linking System

#### Supported Configurations
- **Base URL**: Set root path for source repository
- **Module URL**: Set different source URLs for different modules
- **Path Prefix**: Handle mismatches between build and source paths

#### Link Types
- **Function Name Link**: Click function name to jump to source (requires `--func-links`)
- **Return Address Link**: Click return address to jump to source
- **Source Highlighting**: Syntax highlighting supported (requires Pygments)

### 🚀 Performance Optimization

#### Compiler Suffix Handling
Automatically removes compiler optimization suffixes, showing original function names:

**Supported Suffixes:**
- `.isra.0`, `.constprop.0`, `.lto.0`, `.part.0`
- `.cold.0`, `.cold`, `.plt`, `.ifunc`
- `.llvm.0`, `.clone.0`, `.unk.0`

**Example:**
```
finish_task_switch.isra.0+0x150/0x4a8
↓
finish_task_switch+0x150/0x4a8
```

#### High-Performance Parsing
- **fastfaddr2line.py**: Python implementation, much faster than traditional tools
- **External Tool Support**: Can force use of system faddr2line
- **Batch Processing**: Optimized address resolution flow

## Installation & Usage

### Requirements
- Python 3.6+
- addr2line (from binutils)
- Optional: Pygments (for syntax highlighting)

### Basic Usage

```bash
# Minimal config
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast

# Full config
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

### Parameter Description

| Parameter | Description |
|-----------|-------------|
| `ftrace_file` | ftrace output file (required) |
| `--vmlinux` | Path to vmlinux file (required) |
| `--kernel-src` | Kernel source root |
| `--module-dirs` | Kernel module search directories (multiple allowed) |
| `--module-srcs` | Module source root directories (multiple allowed) |
| `--base-url` | Base URL for source links |
| `--module-url` | Module URL mapping (can specify multiple times) |
| `--output` | Output HTML file |
| `--auto-search` | Auto-search common module directories |
| `--verbose` | Verbose debug output |
| `--fast` | Use fastfaddr2line.py |
| `--use-external` | Force use of external faddr2line |
| `--highlight-code` | Enable syntax highlighting |
| `--path-prefix` | Path prefix replacement (multiple allowed) |
| `--filter` | Enable filter window |
| `--func-links` | Function name source links |
| `--entry-offset` | Function entry address offset |

### Module URL Example

```bash
# Set different URLs for different modules
python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --module-url https://url1.com:mod1,mod2 \
    --module-url https://url2.com:mod3,mod4 \
    --module-url https://default.com \
    --filter --fast
```

Rules:
- `mod1,mod2` → use `https://url1.com`
- `mod3,mod4` → use `https://url2.com`
- other modules → use `https://default.com`
- no default URL → use `--base-url`

### Path Prefix Handling

```bash
# addr2line returns: /home/user/build/kernel/fs/open.c
# Kernel source path: /home/user/linux/fs/open.c

python3 funcgraph.py trace.txt \
    --vmlinux vmlinux \
    --kernel-src /home/user/linux \
    --path-prefix /home/user/build/kernel \
    --filter --fast
```

### Cross-Compilation & LLVM

**Cross-compilation:**
```bash
export CROSS_COMPILE=aarch64-linux-gnu-
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

**LLVM Toolchain:**
```bash
export LLVM=1
# or export LLVM=/usr/bin/
# or export LLVM=-10
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```

## Capturing Trace

### Recommended Settings

```bash
cd /sys/kernel/tracing

# Stop current tracing
echo 0 > tracing_on

# Enable recommended options
echo 1 > options/funcgraph-retaddr    # Return address (required)
echo 1 > options/funcgraph-proc       # Process name and PID
echo 1 > options/funcgraph-retval     # Return value
echo 1 > options/funcgraph-args       # Function parameters

# Set tracer
echo function_graph > current_tracer

# Start tracing (1 second)
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on

# Save result
cat trace > ~/ftrace.txt
```

### Option Description

| Option | Purpose | Recommended |
|--------|---------|-------------|
| `funcgraph-retaddr` | Provides return address for source mapping | ⭐⭐⭐⭐⭐ |
| `funcgraph-proc` | Shows process name and PID for filtering | ⭐⭐⭐⭐ |
| `funcgraph-retval` | Shows function return value for debugging | ⭐⭐⭐⭐ |
| `funcgraph-args` | Shows function parameters for analysis | ⭐⭐⭐⭐ |

## Fastfaddr2line Tool

### Standalone Usage

```bash
# Show help
python3 fastfaddr2line.py -h

# Parse a single address
python3 fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8

# Full features
python3 fastfaddr2line.py vmlinux \
    --functions \
    --basenames \
    --inlines \
    --pretty-print \
    arch_stack_walk+0x150/0x4a8
```

### Parameter Description

| Parameter | Description |
|-----------|-------------|
| `-f, --functions` | Show function names |
| `-s, --basenames` | Show only file names (no path) |
| `-i, --inlines` | Show inline functions |
| `-p, --pretty-print` | Pretty print output |
| `-C, --demangle` | C++ symbol demangling |
| `--path-prefix` | Path prefix replacement |
| `--module-srcs` | Module source directories |
| `--entry-offset` | Entry address offset |

## Usage Example

### 1. Capture Trace
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

### 2. Generate HTML
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

### 3. In the Browser

Open `result.html` and use the filter features:
- **Find slowest functions**: Enter `sort:desc` in duration
- **Find outliers**: Enter `>100||<0.1 sort:desc` in duration
- **Specific process**: Enter `1234|5678` in PID, `nginx|bash` in process name
- **Combined filtering**: CPU `0|1`, PID `1234`, duration `>5&&<50 sort:desc`
- **Fold calls**: Click the `▶` icon before function entry lines to fold/expand

## Project Structure

```
funcgraph_visualization/
├── README.md                    # This file
├── funcgraph.py                 # Main program
├── fastfaddr2line.py            # Address resolution tool
├── ftrace.txt                   # Example data
└── sample.png                   # Screenshot
```

## References

- [ftrace visualization tool](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace visualization tool (continued)](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [Wrote an ftrace visualization tool](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## License

Open source project, contributions welcome!

---

**Version**: v0.6
**Last Updated**: 2026-01-30
**Status**: ✅ Production Ready
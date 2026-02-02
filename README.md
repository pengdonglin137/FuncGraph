# FuncGraph - Specialized Linux ftrace Visualization Tool

![FuncGraph Visualization Example](sample.png)

## What is FuncGraph?

FuncGraph converts `function_graph` ftrace output into an interactive, filterable HTML report for kernel developers and performance engineers. It is optimized for fast triage: source linking, high-speed address resolution, parameter and duration filtering, and keyboard-friendly call folding.

## Quick Start

Minimal:
```bash
python3 funcgraph.py trace.txt --vmlinux vmlinux --filter --fast
```
Full:
```bash
python3 funcgraph.py trace.txt \
  --vmlinux /path/to/vmlinux \
  --kernel-src /path/to/kernel \
  --filter --fast --highlight-code --output result.html
```

## Cheat Sheet

- Common flags: `--fast`, `--filter`, `--func-links`, `--highlight-code` 🔧
- Duration filters: `>10`, `<5&&>2`, `sort:desc` ⏱️
- Parameter filters examples: `skb=...`, `do_xxx(arg=1)` 🔎
- Keyboard: Tab focuses fold icon; Enter toggles fold; `Esc` clears selection ⌨️

## Key features

- Interactive ftrace visualization with fold/unfold call blocks
- Clickable source links (local or online) and optional syntax highlighting
- High-performance address resolution (`fastfaddr2line.py`)
- Multi-dimensional filtering (CPU/PID/params/duration) with suggestions
- Keyboard-friendly navigation and accessible fold controls

---

<!-- Installation & Usage continues below -->

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

The following table documents each command-line option supported by `funcgraph.py`, including types, defaults, and examples.

| Parameter | Type / Default | Description | Example |
|-----------|----------------|-------------|---------|
| `ftrace_file` | path (required) | Path to ftrace output file to parse and visualize | `trace.txt` |
| `--vmlinux` | path (required) | Path to the vmlinux binary (used for address -> source resolution) | `--vmlinux /path/to/vmlinux` |
| `--kernel-src` | path | Kernel source tree root (used for local source linking and syntax highlighting) | `--kernel-src /usr/src/linux` |
| `--module-dirs` | paths... | Directories to search for kernel modules (can specify multiple) | `--module-dirs /lib/modules /usr/lib/modules` |
| `--module-srcs` | paths... | Module source directories for module-specific source linking | `--module-srcs /path/to/module/src` |
| `--base-url` | URL | Base URL for online source links (e.g., Bootlin) | `--base-url https://elixir.bootlin.com/linux/v6.18/source` |
| `--module-url` | url:mods (appendable) | Map specific modules to a source URL; format: `url:mod1,mod2`. Can be specified multiple times. | `--module-url https://url1.com:modA,modB` |
| `--output` | path (default: `ftrace_viz.html`) | Output file path for generated HTML | `--output result.html` |
| `--auto-search` | flag | Auto-search common module directories (adds common `/lib/modules` paths) | `--auto-search` |
| `--verbose` | flag | Enable verbose logging for debugging and diagnostics | `--verbose` |
| `--fast` | flag | Use bundled `fastfaddr2line.py` for faster vmlinux processing | `--fast` |
| `--use-external` | flag | Force use of external `faddr2line`/`addr2line` (mutually exclusive with `--fast`) | `--use-external` |
| `--highlight-code` | flag | Enable C source syntax highlighting in HTML (requires Pygments) | `--highlight-code` |
| `--path-prefix` | paths... | Path prefixes to strip/replace when mapping addr2line paths to kernel-src | `--path-prefix /home/user/build/kernel` |
| `--filter` | flag | Include the interactive filter box (CPU/PID/params/duration) in the HTML | `--filter` |
| `--func-links` | flag | Add clickable source links to function names (adds some processing overhead) | `--func-links` |
| `--entry-offset` | int (default: `0`) | Offset to add to function entry addresses (useful for patched functions) | `--entry-offset 8` |
| `--enable-fold` | flag | Enable function call folding UI (collapse/expand call blocks) | `--enable-fold` |

**Notes:**
- `--fast` and `--use-external` should not be used together; if both are specified, `--use-external` will be ignored.
- `--module-url` supports either a bare URL (default for all modules) or `url:mod1,mod2` mappings; repeat the flag to add multiple mappings.

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
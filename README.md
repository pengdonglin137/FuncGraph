# FuncGraph - Specialized Linux ftrace Visualization Tool

![FuncGraph Visualization Example](sample.png)

## What is FuncGraph?

FuncGraph converts `function_graph` ftrace output into an interactive, filterable HTML report for kernel developers and performance engineers. It is optimized for fast triage: source linking, high-speed address resolution, parameter and duration filtering, and keyboard-friendly call folding.

## Cheat Sheet

- Common flags: `--fast`, `--filter`, `--func-links`, `--highlight-code` 🔧
- Filter syntax: `cpu:3 AND duration:>10 sort:desc` ⏱️
- Graph filter: `graph:funcname` (show call tree), `notrace:funcname` (hide call tree) 🌲
- Latency filter: `irqs:d AND hirq:h` (latency-mode traces only) 🔎
- Keyboard: Tab focuses fold icon; Enter toggles fold; `Esc` clears selection ⌨️

## Key features

- Interactive ftrace visualization with fold/unfold call blocks
- Clickable source links (local or online) and optional syntax highlighting
- High-performance address resolution (`fastfaddr2line.py`)
- **Unified filter** with `field:value` syntax, AND/OR/NOT logic, field-selector buttons with dropdown menus
- **Graph filtering**: `graph:func` / `notrace:func` to show/hide entire call trees (like ftrace's `set_graph_function` / `set_graph_notrace`)
- **Latency field support**: parses irqs-off, need-resched, hardirq/softirq, preempt-depth from latency-mode traces
- Keyboard-friendly navigation and accessible fold controls
- **Timeline tab** (`--timeline`): per-task flame chart over time with context, irqs-off, scheduling-delay, off-CPU and error-path coloring

---

<!-- Installation & Usage continues below -->

## Installation & Usage

### Requirements
- Python 3.6+
- addr2line (from binutils)
- pyelftools (for `--fast`)
- Optional: Pygments (for syntax highlighting)

### Basic Usage

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
| `--filter` | flag | Include the unified filter UI (supports `field:value` queries with AND/OR/NOT) | `--filter` |
| `--func-links` | flag | Add clickable source links to function names (adds some processing overhead) | `--func-links` |
| `--entry-offset` | int (default: `0`) | Offset to add to function entry addresses (useful for patched functions) | `--entry-offset 8` |
| `--enable-fold` | flag | Enable function call folding UI (collapse/expand call blocks) | `--enable-fold` |
| `--timeline` | flag | Add the Timeline tab (per-task flame chart over time, see below) | `--timeline` |
| `--btf` | paths... | BTF sources for return-type checks: raw `/sys/kernel/btf/*` copies or ELF files (default: `.BTF` of vmlinux/modules) | `--btf vmlinux.btf` |
| `--no-btf` | flag | Do not use BTF for error-path detection | `--no-btf` |

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

## Timeline View (`--timeline`)

`--timeline` adds a **Timeline** tab next to the text view. Each task gets a track; inside a track, x is time and y is call depth, so a slice's width is its duration (similar to Android Studio method traces or Perfetto, but for kernel function_graph data). The data is embedded in the same HTML file and drawn on a canvas, so 100k+ calls stay interactive.

What it shows:
- **Context colors** from the latency flags: task (blue), softirq (green), hardirq (orange), NMI (purple). Hatching marks irqs-off; an amber band/corner marks a pending need-resched (scheduling delay window); an optional bar shows preempt depth.
- **Off-CPU periods** from the context-switch markers, labeled with the function the task was waiting in (e.g. `mutex_lock`) and whether it was preempted or blocked.
- **Error paths** from `funcgraph-retval`: the origin (first function returning an errno) is solid red when the error reaches user space (`*_sys_*` in the chain) and orange when a caller handled it; propagating frames get a red outline. Return types are read from BTF, so void functions (leftover register values) and unsigned/bool returns are not reported, and pointer returns are checked with `IS_ERR` semantics.
- **Call sites** from `funcgraph-retaddr`; a purple corner marks calls whose caller is not the parent slice (untraced, inlined or assembly frames in between).
- Color modes: context, function name, self time, CPU, module.

Interaction: `W`/`S` or Ctrl+wheel zoom, `A`/`D` or drag to pan, double-click to zoom into a slice, `E`/`Shift+E` next/previous error, `/` search, `Shift`+drag to summarize a time range (self/inclusive time per function, context breakdown, off-CPU time), `Enter` opens the selected call in the text view, and `Alt`+click on a text line shows it in the timeline. Press `?` in the timeline for the full list.

The tool adapts to the options the trace was captured with and tells you what is missing:

| Missing option | Effect in the timeline |
|---|---|
| `funcgraph-abstime` | No real time axis: slices are packed back-to-back by duration ("compressed time") |
| `latency-format` | Context is inferred from IRQ/softirq entry function names; no irqs-off / need-resched / preempt overlays |
| `funcgraph-proc` | Tasks are reconstructed from context-switch markers, otherwise one track per CPU |
| `funcgraph-retval` | No error paths |
| `funcgraph-retaddr` | No call sites |
| `funcgraph-duration` | Durations are taken from the next timestamp (marked as estimated) |

BTF: by default the `.BTF` section of `--vmlinux` (needs `CONFIG_DEBUG_INFO_BTF=y`) and of the modules found via `--module-dirs` is used. If your vmlinux has no `.BTF`, copy `/sys/kernel/btf/vmlinux` from the target and pass `--btf vmlinux.btf`. `--no-btf` disables the check (errors are then guessed from the value alone).

### Capturing for the timeline

```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
echo 1 > options/funcgraph-abstime    # real time axis (required for gaps and off-CPU)
echo 1 > options/latency-format       # irqs-off / need-resched / hardirq / softirq / preempt flags
echo 1 > options/funcgraph-proc
echo 1 > options/funcgraph-retval
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-tail       # function name on every '}' (better pairing)
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
```

Notes:
- With `funcgraph-retaddr` on, kernels such as 6.18 do not print arguments even if `funcgraph-args` is set. Capture arguments in a separate run without `funcgraph-retaddr`.
- On kernels without the BTF printing optimizations (e.g. 6.18), `funcgraph-args` makes reading `trace` very slow (measured ~36 ms per line on a Raspberry Pi 4). Always narrow it with `set_graph_function`, `set_ftrace_filter` or `set_ftrace_pid` first.
- Optionally enable `events/sched/sched_switch` and `sched_wakeup`: the timeline then shows the task state at switch-out and who woke the task.
- Use a larger `buffer_size_kb` if you see `LOST EVENTS`.

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

Open `result.html` and use the unified filter:
- **Find slowest functions**: `duration:>0 sort:desc`
- **Find outliers**: `duration:>100 OR duration:<0.1`
- **Specific process**: `pid:1234 OR pid:5678`, `comm:nginx OR comm:bash`
- **Combined filtering**: `cpu:0 AND pid:1234 AND duration:>5`
- **Graph filter**: `graph:schedule` (show schedule's call tree), `notrace:rcu_read_lock` (hide rcu call trees)
- **Function match**: `func:mutex` (match function name only, no subtree)
- **Combined graph**: `graph:vfs_read AND cpu:0` (graph filter + other conditions)
- **Latency mode**: `irqs:d AND hirq:h` (only for latency-mode traces)
- **Mouse workflow**: Click field buttons (cpu, pid, graph, notrace...) to open dropdown selectors
- **Fold calls**: Click the `▶` icon before function entry lines to fold/expand

#### Supported Filter Fields

| Field | Attribute | Operators | Example |
|-------|-----------|-----------|---------|
| `cpu` | data-cpu | regex | `cpu:0`, `cpu:0\|1` |
| `pid` | data-pid | regex | `pid:1234` |
| `comm` | data-comm | regex | `comm:bash` |
| `ret` | data-ret | regex, `all` | `ret:all`, `ret:-22` |
| `params` | data-params | regex | `params:folio` |
| `duration` | data-duration | `>`, `<`, `>=`, `<=` | `duration:>10` |
| `func` | data-func | regex | `func:schedule` |
| `graph` | data-func + fold hierarchy | regex | `graph:vfs_read` (show call tree) |
| `notrace` | data-func + fold hierarchy | regex | `notrace:rcu_read_lock` (hide call tree) |
| `irqs` | data-irqs | char | `irqs:d` (disabled) |
| `resched` | data-resched | char | `resched:N` |
| `hirq` | data-hirq | char | `hirq:h` (hardirq) |
| `preempt` | data-preempt | `>`, `<`, `>=`, `<=` | `preempt:>1` |

## Project Structure

```
funcgraph_visualization/
├── README.md                    # This file
├── funcgraph.py                 # Main program
├── fastfaddr2line.py            # Address resolution tool
├── timeline.py                  # Timeline model (per-task call slices, error paths)
├── btf.py                       # BTF reader (function return types)
├── assets/                      # Timeline JS/CSS, inlined into the HTML
├── tests/test_timeline.py       # Timeline invariant tests
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

**Version**: v0.7
**Last Updated**: 2026-03-08
**Status**: ✅ Production Ready
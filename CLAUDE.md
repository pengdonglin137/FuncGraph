# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FuncGraph (v0.6) converts Linux kernel ftrace `function_graph` output into interactive, self-contained HTML visualizations. It resolves kernel function addresses to source file:line locations and embeds clickable source code panels in the output.

## Running the Tool

```bash
# Basic usage
python3 funcgraph.py <ftrace_file> --vmlinux <path_to_vmlinux>

# Full-featured x86_64 example (see /vol_1t/Qemu/x86_64/to_html_v2.sh)
funcgraph.py --enable-fold --func-links --fast --filter --highlight-code \
    --vmlinux ./linux-6.18/x86_64/vmlinux \
    --kernel-src $PWD/linux-6.18 \
    --module-dirs ./linux-6.18/x86_64/modules_install/ ./modules_/ \
    --module-src ./modules_/ $PWD/linux-6.18 \
    --path-prefix /vol_1t/Qemu/x86_64/modules/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --entry-offset 0 \
    --output x86_64_ftrace.txt.html ftrace.txt

# Standalone address resolver
python3 fastfaddr2line.py <vmlinux> <func+offset/length>

# Cross-compilation (ARM64 example)
CROSS_COMPILE=aarch64-linux-gnu- python3 funcgraph.py ftrace.txt --vmlinux vmlinux
```

Key flags: `--enable-fold` (collapsible call blocks), `--func-links` (resolve all function names to source links), `--fast` (use `fastfaddr2line.py` instead of kernel's `faddr2line`), `--filter` (enable filtering UI), `--highlight-code` (Pygments-based C highlighting), `--module-src` (source dirs for out-of-tree modules), `--path-prefix` (strip prefix from module source paths), `--entry-offset` (adjust trace entry point).

There is no build system, test suite, or CI/CD. The project is two standalone Python scripts.

## Dependencies

- **Python 3.6+** (standard library only for `funcgraph.py`)
- **pyelftools** (`pip install pyelftools`) — required by `fastfaddr2line.py`
- **addr2line** (from binutils) — required for address resolution
- **pygments** — optional, for C source syntax highlighting (`--highlight-code`)

## Architecture

Two Python files with a pipeline architecture:

### `fastfaddr2line.py` (1,016 lines) — Address Resolution Engine
Reimplements the kernel's `scripts/faddr2line` in Python. Parses ELF symbol tables via pyelftools, resolves `func+offset/length` notation to source file:line using a persistent `addr2line` subprocess. Supports fuzzy matching for compiler-mangled names (`.isra`, `.constprop`, `.llvm` suffixes).

### `funcgraph.py` (6,948 lines) — Main Application
Single-file application with these major subsystems (in pipeline order):

1. **Ftrace Parser** (`parse_ftrace_file`, ~line 785): Parses raw ftrace output handling multiple format variants (CPU, PID, timestamp, latency formats). Extracts function calls, durations, return values, module names, and caller info (`/* <-func+offset/length */`).

2. **Address Resolution** (`call_faddr2line_batch`, ~line 1398): Batch-resolves function addresses to source locations via `fastfaddr2line.py` or the external kernel `faddr2line` script. Handles both vmlinux and `.ko` module resolution.

3. **HTML Generator** (`generate_html`, ~line 2173, ~4,400 lines): Constructs a fully self-contained HTML document with inline CSS/JS. Features include: light/dark themes, fold/unfold of call blocks, keyboard navigation, multi-dimensional filtering (CPU/PID/process/return-value/duration/function-params), floating action buttons, and inline source code panels.

### Data Flow
```
ftrace.txt → parse_ftrace_file() → call_faddr2line_batch() → generate_html() → output.html
```

### Key Constants
- `ERROR_CODE_MAP` (top of `funcgraph.py`): Maps Linux errno values (-1 to -531) to symbolic names (e.g., `-22 → EINVAL`), used for annotating return values in the visualization.

## Code Conventions

- All HTML/CSS/JS is generated inline within Python string concatenation in `generate_html()` — there are no separate template files.
- The generated HTML is fully self-contained with zero external dependencies.
- Environment variables `CROSS_COMPILE` and `LLVM` control toolchain selection for cross-architecture support.
- `identify_fold_blocks()` pairs `{`/`}` markers in parsed ftrace for the fold/unfold UI feature.

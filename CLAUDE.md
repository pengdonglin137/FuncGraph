# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FuncGraph (v0.7) converts Linux kernel ftrace `function_graph` output into interactive, self-contained HTML visualizations. It resolves kernel function addresses to source file:line locations and embeds clickable source code panels in the output.

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

## Testing

```bash
# Self-test: compares refactored output against reference HTML
# Checks 24 HTML features + byte-level content diff
bash test_refactor.sh
```

Requires a pre-generated reference file at `/tmp/funcgraph_reference.html`. Generate it before any code changes with the same parameters as in `test_refactor.sh`.

## Dependencies

- **Python 3.6+** (standard library only for `funcgraph.py`)
- **pyelftools** (`pip install pyelftools`) — required by `fastfaddr2line.py`
- **addr2line** (from binutils) — required for address resolution
- **pygments** — optional, for C source syntax highlighting (`--highlight-code`)

## Architecture

Two Python files with a pipeline architecture:

### `fastfaddr2line.py` (903 lines) — Address Resolution Engine

Reimplements the kernel's `scripts/faddr2line` in Python. Parses ELF symbol tables via pyelftools, resolves `func+offset/length` notation to source file:line using a persistent `addr2line` subprocess. Supports fuzzy matching for compiler-mangled names (`.isra`, `.constprop`, `.llvm` suffixes).

Key components:
- `Symbol`, `Section` — data classes for ELF symbols
- `load_elf_data()` — parses `.symtab`, builds per-section sorted symbol index
- `find_matching_symbol()` — binary search + fuzzy matching for symbol lookup
- `start_addr2line()` — launches persistent addr2line subprocess
- `get_kernel_src_root()` — auto-detects kernel source root from `start_kernel`
- `resolve_addresses()` — resolves addresses via addr2line sentinel protocol
- `_find_source_file()` — shared file search logic (kernel root, path prefixes, module dirs)
- `_print_code_context()` — prints source code context around a line

### `funcgraph.py` (7,187 lines) — Main Application

Organized into 8 sections with 4 core classes:

```
# Constants and Configuration     (~180 lines)  APP_VERSION, ERROR_CODE_MAP
# Data Model                      (~50 lines)   FtraceLine
# Utility Functions               (~200 lines)  path cleanup, URL building, module search
# Syntax Highlighting             (~200 lines)  ftrace + C code highlighting
# Ftrace Parser                   (~560 lines)  FtraceParser class
# Address Resolver                (~530 lines)  AddressResolver class
# HTML Generator                  (~5200 lines) HtmlGenerator class
# Main Entry Point                (~300 lines)  CLI argument parsing, main()
```

#### Core Classes

**`FtraceLine`** — Data class (`__slots__`) for parsed ftrace lines. 20 fields: `raw_line`, `expandable`, `func_info`, `raw_func_name`, `display_func_name`, `module_name`, `cpu`, `pid`, `comm`, `func_name_info`, `ret_value`, `params`, `duration`, `sort_duration`, `duration_raw`, `fold_id`, `fold_type`, `fold_ancestors`, `call_depth`. Dict-compatible `get()`/`__setitem__` for backward compatibility.

**`FtraceParser`** — Parses raw ftrace output. Methods:
- `parse(file_path, enable_fold)` → `list[FtraceLine]`
- `_parse_cpu(line)` → CPU number
- `_parse_pid_comm(search_area)` → (pid, comm)
- `_parse_duration(prefix)` → (duration, sort_duration, duration_raw)
- `_identify_fold_blocks(parsed_lines)` — pairs `{`/`}` for fold/unfold

**`AddressResolver`** — Batch-resolves addresses to source locations. Methods:
- `resolve_batch(func_infos, target, use_list)` — batch addr→source via faddr2line
- `resolve_func_names(func_names, func_module_map, module_paths)` — function name→source
- `_build_cmd(target, use_list, extra_args)` — unified faddr2line command construction
- `_resolve_func_list(target_path, func_names, include_module_srcs)` — per-target resolution
- `adjust_offset(func_info)` — offset adjustment (static)
- `parse_list_output(output, ...)` — faddr2line `--list` output to HTML (static)

**`HtmlGenerator`** — Generates self-contained HTML. Methods:
- `generate()` → (html_str, vmlinux_time, module_time) — assembly method (~130 lines)
- `_prepare_data()` — pre-processing: func links, filter data, module URLs
- `_generate_content_lines()` — main loop: line containers, fold markers, syntax highlighting
- `_generate_css()` — all CSS styles (~1050 lines) including light/dark themes, heatmap, fold guide lines, fold summary
- `_generate_javascript()` — all JavaScript (~2400 lines) including keyboard nav, filtering, fold/unfold, heatmap, fold-to-depth, recursive fold, fold summary
- Static helpers: `_adjust_func_info`, `_extract_line_number`, `_extract_file_path`

#### Backward-Compatible Wrappers

All original public functions are preserved as thin wrappers delegating to the classes:
- `parse_ftrace_file()` → `FtraceParser.parse()`
- `identify_fold_blocks()` → `FtraceParser._identify_fold_blocks()`
- `call_faddr2line_batch()` → `AddressResolver.resolve_batch()`
- `call_faddr2line_for_func_names()` → `AddressResolver.resolve_func_names()`
- `adjust_function_offset()` → `AddressResolver.adjust_offset()`
- `parse_list_output()` → `AddressResolver.parse_list_output()`
- `generate_html()` → `HtmlGenerator.generate()`

### Data Flow

```
ftrace.txt → FtraceParser.parse() → AddressResolver.resolve_batch() → HtmlGenerator.generate() → output.html
```

### Key Constants

- `ERROR_CODE_MAP` (top of `funcgraph.py`): Maps Linux errno values (-1 to -531, 140+ entries) to symbolic names (e.g., `-22 → EINVAL`), used for annotating return values in the visualization.

## Code Conventions

- All HTML/CSS/JS is generated inline within `HtmlGenerator` methods — there are no separate template files.
- The generated HTML is fully self-contained with zero external dependencies.
- Environment variables `CROSS_COMPILE` and `LLVM` control toolchain selection for cross-architecture support.
- File sections are delimited by `# ====` separator comments.
- `FtraceLine` uses `__slots__` for memory efficiency with dict-compatible accessors for gradual migration.
- localStorage keys are namespaced per-file (hash of document title) to prevent cross-file state corruption; `theme` is global.
- Fold system uses `_setFolded()` as atomic unit — `toggleFold`, `foldToDepth`, `toggleFoldRecursive` all delegate to it, batching localStorage writes.
- Fold icons use CSS `transform: rotate(90deg)` with transition instead of character switching (GPU-composited).

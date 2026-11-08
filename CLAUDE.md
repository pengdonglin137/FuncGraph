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
tests/run.sh                         # unit + end-to-end + browser smoke (hermetic, ~8 s)
python3 tests/golden.py --update     # BEFORE changing output-affecting code: record baseline
tests/run.sh --golden                # afterwards: byte-for-byte comparison on real kernels
```

- The unittest suite needs no kernel tree: `tests/helpers.FakeKernel` provides an empty vmlinux and a stub `scripts/faddr2line`. Fixtures in `tests/data/` are real Raspberry Pi 4 traces captured with different ftrace option sets.
- Optional env: `FG_VMLINUX`/`FG_KERNEL_SRC` (real BTF + agreement with the kernel's faddr2line), `FG_TRACES` (extra traces for timeline invariants). Browser tests need Playwright + Chromium and are skipped otherwise; JS syntax tests need `node`.
- `golden.py` configs use `/vol_1t/Qemu/x86_64/linux-6.18` and `/vol_1t/raspi/linux` (override with `FG_X86_KERNEL`/`FG_PI4_KERNEL`); `FG_ROOT` runs another checkout (e.g. a `git worktree` of the old version) to record the baseline.
- Refactors must keep golden output identical; intended output changes must be explained in the commit.

## Dependencies

- **Python 3.6+** (standard library only for the main program, including the BTF reader)
- **pyelftools** — required by `fastfaddr2line.py` (`--fast`)
- **addr2line** (binutils, or `llvm-addr2line` with `LLVM=1`)
- **pygments** — optional, `--highlight-code`

## Architecture

Full design: **docs/design.md** (module layout, ftrace grammar, timeline model, data format, tests, extension guide).

```
funcgraph.py          entry point (re-exports fglib)
fastfaddr2line.py     standalone resolver, run as a subprocess (pyelftools + persistent addr2line)
fglib/constants.py    PROJECT_DIR, APP_*, ERROR_CODE_MAP
fglib/model.py        FtraceLine (__slots__, dict-compatible accessors)
fglib/util.py         paths, URLs, module lookup, info-panel data
fglib/highlight.py    ftrace line + C source highlighting
fglib/parser.py       FtraceParser: text -> list[FtraceLine], fold pairing
fglib/resolver.py     AddressResolver: func+off/len -> source via (fast)faddr2line
fglib/report.py       HtmlGenerator: content lines, info panel, embedded data
fglib/assets.py       reads assets/ (report + timeline JS/CSS)
fglib/timeline.py     TimelineBuilder: per-task slices, off-CPU, windows, error chains -> tlData
fglib/btf.py          BTF reader: function return-type classes
fglib/cli.py          build_parser / validate_args / collect_module_dirs / find_faddr2line / main
```

Data flow: `FtraceParser.parse()` → `AddressResolver.resolve_batch()` → (optional `build_timeline_data()`) → `HtmlGenerator.generate()` → HTML.

## Code Conventions

- Front-end code lives in `assets/` (`report/` for the text view, `timeline/` for the Timeline tab) and is inlined by `fglib/assets.py` (read with `newline=''` so output stays byte-stable). `HtmlGenerator` only emits markup and data.
- The generated HTML is fully self-contained with zero external dependencies.
- Environment variables `CROSS_COMPILE` and `LLVM` control toolchain selection for cross-architecture support.
- Python code lives in the `fglib` package; `funcgraph.py` is a thin entry point that re-exports the public names. Dependencies between modules are one-way (see docs/design.md §2).
- Use `PROJECT_DIR` (fglib/constants.py) for repo-relative paths, never `__file__` of a module.
- `FtraceLine` uses `__slots__` for memory efficiency with dict-compatible accessors for gradual migration.
- localStorage keys are namespaced per-file (hash of document title) to prevent cross-file state corruption; `theme` is global.
- Fold system uses `_setFolded()` as atomic unit — `toggleFold`, `foldToDepth`, `toggleFoldRecursive` all delegate to it, batching localStorage writes.
- Fold icons use CSS `transform: rotate(90deg)` with transition instead of character switching (GPU-composited).
- Unified filter uses a single text input with `field:value` syntax, parsed by a recursive descent expression parser (`tokenizeFilter` → `parseFilterExpr` → `evalExpr`) supporting AND/OR/NOT and parentheses. Field buttons generate dropdown selectors (checkboxes for enumerable fields, text input for free-form). Dropdowns are mounted on `document.body` with fixed positioning (viewport-clamped) to avoid interactive-element-nesting issues with `<button>`.
- Latency fields (`irqs_off`, `need_resched`, `hardirq_softirq`, `preempt_depth`) are parsed from the pipe-delimited area between the two `|` separators in latency-mode ftrace output, and output as `data-irqs`, `data-resched`, `data-hirq`, `data-preempt` HTML attributes. Latency field buttons only appear when the trace contains latency data.
- Graph filter fields (`func:`, `graph:`, `notrace:`) leverage the fold system's hierarchy data. `data-func` attribute is added to every line with a function name. `graph:funcname` matches the target function's entire call tree by precomputing matching fold-ids from entry lines (`precomputeGraphSets`), then checking each line's `data-fold-id` or `data-fold-ancestors` against the set. `notrace:funcname` inverts the logic. `func:funcname` does simple regex match on `data-func` only.
- Source blocks are de-duplicated: each `.expanded-content` div is empty with `data-src="N"`, the HTML lives in `<script id="fgSrc">` (JSON array) and `fgFill(el)` fills it before any `content.style.display = 'block'`. Any new code that shows an expanded block must call `fgFill` first.
- Fold ids are `f<base36 entry line index>`; `data-fold-ancestors` is a space-separated list of them.
- Container uses `width: fit-content; min-width: 100%` to allow long trace lines to expand beyond the viewport, triggering the browser's native horizontal scrollbar.
- Timeline `flags` bit layout is shared by `fglib/timeline.py` (`F_*`) and `assets/timeline/model.js` (`FL`); change both together.
- Timeline JS files `model/render/interact/panels.js` are concatenated into one closure `__tlBoot()` (order in `fglib/assets.py`); `boot.js` is global.
- Text-view filter → timeline: `applyFilter`/`clearFilter` call `fgNotifyFilter(text, Set<lineNumber>|null)`; the timeline registers `window.fgOnFilter`.

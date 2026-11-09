# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FuncGraph (v0.9.1) converts Linux kernel ftrace `function_graph` output into interactive, self-contained HTML visualizations. It resolves kernel function addresses to source file:line locations and embeds clickable source code panels in the output.

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

`fgreport` (repo root) wraps funcgraph.py: all features on, kernel paths in presets of `~/.config/funcgraph/config.ini` (`fgreport init NAME --vmlinux ... --default`, `[fgreport] preset` marks the default; funcgraph.py ignores that section), output next to the trace, `--no-text-view` above 150k lines. `FG_CONFIG` overrides the config path (tests use it).

## Testing

```bash
tests/run.sh                         # unit + end-to-end + browser smoke (hermetic, ~8 s)
python3 tests/golden.py --update     # BEFORE changing output-affecting code: record baseline
tests/run.sh --golden                # afterwards: byte-for-byte comparison on real kernels
```

- The unittest suite needs no kernel tree: `tests/helpers.FakeKernel` provides an empty vmlinux and a stub `scripts/faddr2line`. Fixtures in `tests/data/` are real Raspberry Pi 4 traces captured with different ftrace option sets.
- Optional env: `FG_VMLINUX`/`FG_KERNEL_SRC` (real BTF + agreement with the kernel's faddr2line), `FG_TRACES` (extra traces for timeline invariants). Browser tests need Playwright + Chromium and are skipped otherwise; JS syntax tests need `node`.
- `golden.py` configs use `/vol_1t/Qemu/x86_64/linux-6.18` and `/vol_1t/raspi/linux` (override with `FG_X86_KERNEL`/`FG_PI4_KERNEL`); `FG_ROOT` runs another checkout (e.g. a `git worktree` of the old version) to record the baseline.
- Refactors must keep golden output identical; intended output changes must be explained in the commit. `FG_GOLDEN_KEEP=1` keeps the current outputs in `$FG_GOLDEN_DIR/current` for field-level comparison.
- CI (`.github/workflows/ci.yml`) reuses `test-and-package.yml` to test and package on Python 3.8 and 3.12 (browser tests on 3.12 only), including fresh-environment wheel/sdist installation checks. Successful `master` pushes also publish `site/index.html` + `sample.html` to GitHub Pages. Kernel-tree tests (golden, `FG_VMLINUX`) only run locally.
- Browser tests and debugging: `fgView('timeline').probe()` exposes the model read-only (`N`, `NA`, `SYN`, `sStart`, `nameOf`, `isGhost`, `select`, `zoomTo`, ...). Headless Chromium hides scrollbars by default; layout tests need `ignore_default_args=['--hide-scrollbars']`.
- Guide screenshots (`docs/img/*.png`) come from `python3 docs/img/make_shots.py [names]` (uses sample.html and repo fixtures); rerun after UI changes.
- After output-affecting changes regenerate `sample.html` from `samples/sample_io.txt` (command in README "Sample") and retake `sample.png` / `samples/sample_text.png`.

## Releasing

- Follow **docs/releasing.cn.md** (Chinese) or **docs/releasing.md** (English) for the release checklist, verification, troubleshooting, and the verified v0.9.1 record.
- `fglib/constants.py:APP_VERSION` is the single version source. A pushed tag must exactly equal `v` + `APP_VERSION`; `release.yml` publishes wheel, sdist, and `SHA256SUMS` to GitHub Releases, not PyPI.
- A branch push is not a release request: do not create a version tag unless a release is requested. Only `master` pushes trigger branch CI; feature branches need a PR or manual CI run.
- Check the actual CI/release runs and downloaded attachments before declaring success. No triggered run is not a passing run. Never move a published version tag; reruns use the original tagged commit, so source fixes need a new version/tag.

## Dependencies

- **Python 3.8+** (standard library only for the main program, including the BTF reader)
- **pyelftools** — required by `fastfaddr2line.py` (`--fast`)
- **addr2line** (binutils, or `llvm-addr2line` with `LLVM=1`)
- **pygments** — optional, `--highlight-code`

## Architecture

User guide (fields, colors, panels): **docs/guide.en.md** + **docs/guide.cn.md** - update both when UI semantics change. Full design: **docs/design.md** (module layout, ftrace grammar, timeline model, data format, tests, extension guide).

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
fglib/prefilter.py    --pid/--comm/--cpu/--time-range/--graph/--notrace on raw lines (KEEP/DROP/PENDING stack)
fglib/compare.py      --compare BASE: per-function stats and deltas (cmpData)
fglib/inline.py       inline chains from faddr2line output -> tlData.inline
fglib/sources.py      --source-url / --source-rev providers, context regeneration, version check
fglib/config.py       funcgraph.ini defaults + presets (applied as argparse defaults)
fglib/inputs.py       stdin / compressed / multi-file (merged by timestamp) input
fglib/export.py       --export JSON/CSV of the analyses
fgcapture.sh          target-side capture helper (--sched, --args, --calibrate, -- CMD)
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
- Only `#content` uses `width: fit-content; min-width: 100%` so long trace lines retain native horizontal scrolling without widening the page header. Keep the outer container viewport-sized.
- Timeline height is viewport-bounded; flex layout reserves at least 160 px for the stage and 60 px for an open detail panel. Toolbar and notes shrink into independently scrollable areas in compact windows; do not restore unbounded control groups or size the stage from its old top offset.
- Timeline `flags` bit layout is shared by `fglib/timeline.py` (`F_*`) and `assets/timeline/model.js` (`FL`); change both together.
- Timeline JS files `model/render/interact/analysis/panels.js` are concatenated into one closure `__tlBoot()` (order in `fglib/assets.py`). Extra tabs register in `window.fgViews` and are wired by the global `assets/views/boot.js`; add a view in `HtmlGenerator._views()`.
- Sections/atomic checks live in `TimelineBuilder._track_lat` / `_on_call` (task context only, preempt baseline from `_preempt_baseline`); breakdown and slow-vs-fast are computed in the browser from slices (`analysis.js`: `subtree`, `breakdown`, `slowFast`).
- Timeline synthetic slices have index >= N: inline frames (`FL.INL`, Inline view only) and untraced frames filling depth gaps (`FL.GHOST`, `isGhost(i)`, always shown). A slice's parent is the innermost open frame with a smaller depth (depth may jump by more than one). Use `rowOf(i)` for vertical position and `rowsT`/`sDepth` for visual navigation; use the real-parent index for subtrees and child statistics. Loops `for (i < N)` must stay over real slices only.
- Preempt depth: function_graph adds one level to every record; `preempt_base` (0 or 1) is subtracted everywhere in the timeline (`preemptOff(f)` in JS). The text view keeps the raw digit.
- Hidden views use classes on the elements themselves (`.fg-off`, `#content` via `content-visibility`), never `body.cls descendant` selectors: on 1M-element reports a body class change restyles the whole tree.
- Test environments: Pi 4 (6.18 arm64, `ssh pi4`, sources /vol_1t/raspi/linux) and a QEMU x86_64 VM with the 7.2 BTF-aware retval kernel (`ssh pengdl@localhost -p 8766`, sources /vol_1t/Qemu/trim_retval, vmlinux in x86_64/); both sudo passwords are a single space.
- Tasks lazily caches raw identity facts from real calls and scheduling/window records, with matched-call counts separately keyed by textHits. Query is literal; pagination appends 100 and CSV exports all sorted scoped identities. Span includes gaps, not CPU time/lifetime; OPEN_OFF remains a lower bound, empty boundaries stay unknown and count/packed bases remain explicit. Include all event ends before filling open waits; reveal scheduling-only tracks on demand, without fake slices. CPU navigation selects the deepest real overlapping call or clears stale details and opens Tasks without changing the time range.
- Direct recorded children retain the compact unfiltered top-eight preview but expose complete scoped groups/calls with 100-row append pagination and full CSV. Cache membership and both duration bases only for the current parent; filter matches children, not the parent, and query/sort reuse aggregates. Preview and Longest select the actual maximum-duration occurrence with original-index ties. Show highlights without replacing the explorer. Nearest recorded parent is not a complete runtime callee inventory, and inclusive sums are not a parent partition; exclude synthetic frames/grandchildren, preserve depth gaps and truthful count/packed/adjusted bases.
- In timeline JS, displayed durations go through `durOf(i)` (measured or overhead-corrected, toggled by `setAdjusted`); geometry always uses `sStart/sEnd`.
- New CLI options that change nothing when unused must be added to the hidden-when-default list in `util.format_args_info` to keep golden output stable.
- Text-view filter → timeline: `applyFilter`/`clearFilter` call `fgNotifyFilter(text, Set<lineNumber>|null)`; the timeline registers `window.fgOnFilter`.
- Timeline function search caches unique real function-name indices and the matching slice list per normalized query. Navigation uses binary search in task/start order; filter changes rebuild only the eligible match list. Invalid expressions clear stale matches, and Enter cancels the pending debounce. Keep the position counter synchronized with selection, and preserve native Enter/Space activation on focused controls.
- Range summaries cache one aggregate by range bounds/track, `textHits` identity and `sDur` identity; sorting reuses it and Load more appends 300 rows. CSV exports all sorted rows with proportional self/inclusive estimates, whole-call max, overlapping error origins and filter-independent scoped off-CPU. Count-mode columns use call-order units, never nanoseconds. Use `setSelection` for highlights that must preserve the active analysis panel; `select` opens or closes slice details.
- Duration distributions cache one function's descending real-slice population and per-site groups by name/filter/duration identity. Quantiles use the ascending sample at `round(p * (n - 1))`. Bucket selection filters the paginated call list/CSV, not the population statistics or Slow vs fast. Keep site resets reachable in empty states. Histogram repainting is RAF-coalesced and reuses prepared buckets; resizing/theme changes must not rescan the trace. Timeline CSV downloads share quoting/formula protection and truthful count-mode units.
- Latency/Sections use per-track, per-depth real-slice indexes with prefix maximum ends (ends need not be monotonic). Filter each window by actual matching-call overlap; unfiltered untraced intervals remain. Cache by `textHits`/`sDur` identity; adjusted self estimates never move or resize recorded windows. Latency overview aggregates every function, not only each window's top three. Load more appends 300 windows / 200 sections; CSV exports all sorted rows with original indexes, explicit self/window bases and truthful units. Do not spread arbitrarily many windows into Math.max or include synthetic ghost/inline frames in statistics.
- Errors cache aggregates by `textHits`/`showMinor`, atomic groups separately by `textHits`; Overhead must not invalidate counts or occurrence cursors. Retain all original chain/slice indexes, order occurrences by start/index and never truncate navigation at 50. Group by actual syscall status/label, origin and errno; atomic groups distinguish function/reasons/caller and render without errno chains. Export all sorted groups/occurrences/calls with all three fates and explicit count-mode units. Pagination appends 200 error / 100 atomic groups without replacing existing rows or the focused Load more button.
- Wait chains distinguish recorded wakeups from temporal predecessor candidates (gap <= 50 ms), with explicit termination reasons and cycle detection. Index completed waits lazily by task/end, retain original first-record ties, and exclude `OPEN_OFF` from predecessors/known resumes. Cache the selected root's incremental traversal independently of filter/Overhead; append 100 rows and export the entire chain without expanding the DOM. Escape every task label; use native task buttons for keyboard navigation and truthful lower-bound/count-mode fields.
- Subtree/breakdown/child statistics use the lazy real-parent `first/next` index, never visual rows or synthetic frames; iterative traversal has no depth-256 cutoff. Raw breakdown caches are bounded and filter/Overhead-independent; disclose missing/overlapping partitions rather than silently showing invalid percentages. Slow vs fast caches by root population/duration identity, uses disjoint deterministic cohorts, profiles full real descendants (including recursive ancestry), paginates 100 rows, exports complete profiles/cohorts and keeps native example navigation in the distribution panel. Self follows Overhead; scheduling context retains its separate geometry basis. Recalculate sticky offsets after dynamically adding comparison headers. The comparison wrapper uses `display:contents` so a viewport-width nested containing block cannot clip horizontally sticky controls.
- Compact error/window heads stay horizontally sticky (`left:0`) but use `top:auto` so they scroll vertically; table-header offsets must be zero in that state and recalculated on viewport resize even if the head's height did not change. Keep these selectors local to the panel, not body/ancestor state classes.
- Slice detail context caches only the current selection's parent paths and raw synthetic-frame facts. Do not truncate real stacks/error paths at 128/64 or synthetic children at 40. Typed lazy rows page by 100; full CSV uses a separate iterator without expanding the DOM or changing the visible cursor. Only traced rows have measured columns; preserve source-only/untraced/outside distinctions, true window boundaries and inconsistent-chain status. Refresh retains prepared pages and applies current Overhead values without rescanning parent paths/child extents. Synthetic filter selection uses real child hits, never a synthetic index into textHits. Keep native frame buttons, two-line names and compact-window title scrolling.

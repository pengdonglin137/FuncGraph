# Symbol display consistency / 符号展示一致性

## Scope

Canvas labels and detail titles already simplified recognized trailing compiler
suffixes. Caller-site values, CALL STACK rows and hover previews still exposed
those suffixes, making the same recorded function look different across views.

The shared display rules now cover recorded, inline and untraced hover headings,
their containing-function labels, inline paths, detail symbols and call sites.
No new control is introduced. Known trailing GCC/LLVM clones are simplified;
unknown or nontrailing suffixes, `.plt`/`.ifunc` and meaningful dots remain.

`symbolSiteLabel()` separates the symbol from an existing `+offset/size` or
`(offset unavailable)` suffix before applying the existing cached label rule.
Offsets, sizes, capitalization and module trailers are preserved verbatim.
`detailSymbolHtml()` retains separate symbol/offset colors and escapes both
visible labels and raw native-title attributes. Absolute addresses stay raw.

This is display-only: `nameOf()`, strings, recorded callers, compiler-variant
identities, source positions, arguments and Timeline geometry do not change.
The complete original symbol remains in native title attributes and raw data;
context CSV retains the exact original function and kernel call-site strings.
Offsets still refer to the original clone symbol, not another function sharing
its short label. Manual binary resolution must use the full recorded name.
Regex search, exact filters, function grouping, colors and source/type lookup
still distinguish compiler variants. Short labels do not merge recorded calls.

源码、地址、偏移、参数和原始符号不因简名展示被重写；同名显示不表示合并函数。
模块名不做后缀清理，未知后缀也不擅自删除。原始身份可通过原生悬停提示和导出核对。

## Reproduce

```bash
GMON_OUT_PREFIX=/tmp/funcgraph-symbol-display PYTHONPATH=tests \
  python3 -m unittest test_timeline_names -v
```

The thirteen naming test methods include five Node-based label checks and eight
Chromium methods covering thirty desktop browser configurations. They exercise:

- Known GCC chains, LLVM hashes and demangled clone annotations, plus unknown
  suffixes and meaningful symbol punctuation.
- Embedded/offline and HTTP sidecar delivery, light/dark themes, recorded/inline/
  untraced names, kernel/user calls and missing user caller offsets.
- Exact caller offsets and module trailers, complete data-payload equality,
  original call-site/function CSV values, clone-specific regex search and
  independent function-distribution counts.
- Literal rendering of HTML-like symbols in titles, CALL STACK and hover, without
  creating injected image elements or executing their handlers.

An additional direct token test checks escaped raw titles and symbol/offset
colors, loading the same shared render helpers as the application.

The old implementation fails fifteen subcase assertions across five methods;
that intentional baseline is not a passing regression run. The remaining old
checks already pass. Final logs and report/screenshot fidelity observations are
retained locally under `build/symbol-display-audit/`.

## Completed validation

The final three batches pass **494 distinct tests with no skips**: 136 shared
UI/hover/detail/source checks, 301 data/raw/parser/sidecar checks and 57 source,
entry-fallback, public-sample and documentation checks. The raw batch uses
`FG_RAW_REQUIRED=1` and includes the real local `write4`, host-QEMU-functions
and kernel-default captures through `FG_TRACES`. Earlier focused reruns are
not added to this total. The first isolated Node token harness lacked the new
shared-helper dependency; its diagnostic run is superseded by the final batch
that loads the actual render helpers, not a stubbed implementation.

Both public reports are regenerated with matching kernel source commit
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb` and source links pinned to
`v7.4-rc2`. All five embedded JSON payloads remain identical apart from Timeline
build timing. Every complete Text row is byte-identical: 739 rows / 485 calls
in `sample.html`, 1,504 rows / 714 calls in `sample_mixed.html`. Raw trace inputs
remain unchanged, and both reports embed the current render/interaction/detail
assets. Of 32 regenerated public/guide PNGs, only `sample.png`, `tl_dense.png`
and `tl_untraced.png` change; the other 29 remain byte-identical.

Four additional light/dark detail and hover captures confirm that raw data stays
unchanged and popovers remain within the viewport. These are visual evidence,
not extra unit-test identities. `summary.json` records unique test identities,
final log hashes, source/image digests and excluded diagnostics.

本轮 494 项不同测试通过、无跳过；重复和中间诊断不计入通过数。两份样例保持全部
调用及 Text 行不变，仅更新展示代码和对应截图，没有新增按钮或额外常驻信息。

## Boundaries

Native title attributes retain originals without adding a persistent raw-symbol
row. Touch-only devices do not have a native mouse-hover affordance; raw report
data and CSV remain available. Focused browser cases use Chromium desktop, not
every browser or physical device. Existing hover/layout/delivery tests provide
additional bounded regression coverage, not a claim of universal compatibility.

The label cache is reused, but no wall-clock performance improvement is claimed
without a separate benchmark. No kernel/uftrace parsing, BTF matching, binary
resolution, trace filtering or duration calculations are changed.

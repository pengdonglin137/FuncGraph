# Bounded error and atomic-group browsing

Errors previously appended 200 error groups or 100 atomic-sleep groups per
action. Visiting a large population left every visited group attached, even
though the complete diagnostic aggregation already existed separately.

## Behavior and invariants

- Error groups retain at most 200 table rows; atomic groups retain at most 100.
  They have independent Previous/Next controls and live interval/total statuses.
  Single-page populations omit paging controls, including atomic-only scopes.
- Page replacement does not discard occurrence navigation. A group still
  contains every original occurrence, including beyond 50; its current position
  survives leaving and returning to a page, panel refresh/reopen and Text round
  trips. Endpoint focus moves to a usable navigation button.
- Sorting resets the error-group page only. Changing minor scope rebuilds error
  groups and resets their page/occurrence positions, without resetting atomic
  groups. Changing the shared filter resets both scopes. Overhead preserves
  both pages and occurrence positions, changes call durations only and does not
  alter original start/end coordinates.
- Full aggregates, stable group order, outcome/verification counts, original
  chain/slice/trace-line identities and all three complete CSV exports stay
  independent of the page. Export does not advance occurrence positions or
  display additional rows. Quoting, formula protection and canonical time-mode
  metadata stay intact.
- Compact headers and paging/export regions remain horizontally anchored when
  tables overflow. Touch buttons are at least 44px high, and occurrence arrows
  are also at least 44px wide. Short panes release sticky table headers. Footers
  remain in normal vertical flow: the two independent table footers cannot
  overlap as competing bottom-sticky bars.

This bounds retained table DOM, not total report memory. Full group populations,
occurrence indices, cached entry classification and CSV storage still scale
with trace size. It does not defer initial error aggregation or promise faster
complete paints.

## Reproduce

Generate identical single-file templates from `1ff3461bf2f4` and this change,
using `--timeline --no-text-view` and the same trace/options, then run:

```bash
python3 tests/benchmark_errors.py before.html after.html \
    --groups 10000 --rounds 3 --output error-benchmark.json

PYTHONPATH=tests:. python3 -m unittest test_error_paging -v
```

The synthetic benchmark uses one occurrence per distinct group and measures
error-only and atomic-only scopes separately. Each round opens fresh pages in
one Chromium process; before/after order alternates. Both sides visit every
original group with equal initial page sizes and equal action counts. It records
handler time, two-animation-frame intervals, retained rows and final panel
elements. Programmatic clicks exclude reading/scrolling time; two-rAF intervals
are not complete paint/TTI measurements. Startup, CSV and peak memory are not
measured. These fixtures are not hardware captures.

## Measured result

Chromium `145.0.7632.6`, 1500 × 900, 10,000 groups per scope, three alternating
rounds. Values are medians, not performance guarantees.

| Metric | Errors before | Errors after | Atomic before | Atomic after |
| --- | ---: | ---: | ---: | ---: |
| Initial attached rows | 200 | 200 | 100 | 100 |
| Maximum attached rows | 10,000 | 200 | 10,000 | 100 |
| Final attached rows | 10,000 | 200 | 10,000 | 100 |
| Distinct groups visited | 10,000 | 10,000 | 10,000 | 10,000 |
| Final panel elements | 150,040 | 3,042 | 90,030 | 932 |
| Open handler | 31.2 ms | 30.4 ms | 11.9 ms | 11.8 ms |
| Summed paging handlers | 109.4 ms | 166.6 ms | 87.6 ms | 128.1 ms |
| Summed two-rAF intervals | 3,090.0 ms | 1,635.4 ms | 3,792.0 ms | 3,276.3 ms |
| Actions after the initial page | 49 | 49 | 99 | 99 |

Paging handlers take longer in this measurement. The supported gain is bounded
retained table DOM and reversible navigation, not a general traversal speedup;
the lower observed frame-interval totals do not prove faster complete paints.

## Regression coverage

Ten new browser tests pass in 16.094 seconds. They cover exact 100/200-row
boundaries, 5,003-group traversal without repeated sorting/aggregation, separate
pages with simultaneous error and atomic populations, every original occurrence,
position retention through page replacement/reopen/Text round trips, keyboard
focus, sort/minor/filter invalidation, unchanged complete safely quoted CSV,
Overhead versus recorded geometry, canonical time modes, empty/atomic-only scopes,
both themes and 320px/390px portrait plus 844px touch landscape.

A valid synthetic function-graph trace also goes through the actual parser and
CLI sidecar generator: all 405 original syscall error chains and trace-line
identities remain reachable/exportable. This is not a hardware capture.

An isolated parent-plus-change source tree, excluding the pending Text work,
passes 39 tests in 57.684 seconds: the ten new tests, 21 window/range paging tests
and eight existing error-summary browser regressions.

The required-native full audit runs with:

```bash
FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

It runs 934 tests in 548.848 seconds: 930 pass, three skip and one fails. The
failure remains the previously documented pending Text test
`test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
Chromium's `containIntrinsicBlockSize` contains the `auto` prefix while its
assertion expects bare pixel values. This is not an all-green full-suite result;
that unrelated staged/unstaged work is preserved separately, not fixed or
included here. JavaScript syntax checks, Python compilation and diff checks pass.

## Real-report compatibility

Before/after reports from `tests/data/pi4_sched.txt` and
`samples/sample_mixed.jsonl` cover 24 configurations: single-file and actual CLI
sidecar output, both themes and 1400 × 860 / 390 × 844 / 844 × 390 viewports.
Each compares four scopes: default, minor enabled, a matching origin function
and an empty filter, for 96 panel-scope comparisons.

- Models match exactly after excluding only `stats.build_time`. Header counts,
  ordered rows and bytes of every available CSV match before/after.
- Text round trips preserve rows and CSV, including sidecar reports filtered
  through the actual shared filter field. Compact cases open More to reach that
  field; manually injected filter bitmaps are not substituted for the UI state.
- Fresh browser contexts per independent scope avoid download-rate state
  contaminating later CSV comparisons. No JavaScript errors or document-wide
  horizontal overflow occur; export/close remain horizontally reachable and
  touch export controls are at least 44px high.
- Pi4 has 1,014 calls and 46 chains, including 44 minor chains: two groups by
  default and six with minor enabled. Mixed has 714 calls and no error chains.
  Neither capture has atomic warnings. These are single-page/error-empty
  compatibility checks, not evidence of large real-trace or real atomic-warning
  performance. Large and atomic-positive coverage comes separately from the
  synthetic benchmark and browser tests.

## Documentation image

`docs/img/an_errors.png` uses the final single-file Pi4 report with all 1,500
original Text lines and 1,014 calls. Show minor is enabled, Notes is collapsed,
and the detail split is manually resized at a 1400 × 860 viewport to keep both
chart and table visible. Capture waits for rendering and populated canvas pixels;
this is an arranged documentation view, not the default saved pane height.

No dependency pin, native payload, public capture, public sample HTML, version,
push or release change is part of this iteration.

## Pager layout follow-up (2026-11-19)

A mixed synthetic report checks 101 atomic, 201 user API, and 205 kernel error
groups in a 320 × 700 touch viewport. With the pane scrolled to its right edge,
the test positions each table at the start of its rows and taps the measured
button coordinates without letting Playwright scroll to the button. Previously
all six first-page taps were unreachable (three sections, short and expanded
panes). Each now turns only its own page and moves focus to the visible
Previous button when Next becomes disabled. This checks scroll reachability,
not wall-clock paging performance. Single-page sections still omit pagination;
CSV coverage and retained occurrence cursors remain independent of paging.

## Shared navigation anchor

Previous / Next error and the `E` shortcuts no longer continue from a stale
occurrence after a different call is selected. Without an anchored occurrence,
they use the current call's start (or the window start if no call is selected)
and wrap at either end. Jumping from a kernel or user API occurrence in the
Errors table anchors that exact occurrence if it is in the toolbar scope.
Equal timestamps keep their original order: kernel chains first, then user API
hints in call order. This does not change table grouping or exports.

The initial seek uses binary search on the existing chronological list. In a
Chromium fixture of 8,192 chains, jumping forward and backward from the middle
previously read 4,100 and 4,099 origin entries respectively; the regression
now bounds each handler to fewer than 24 reads, including detail selection.
These are work counts, not wall-clock or rendering benchmarks. Logs:
`build/validation/20261119-error-navigation-anchor/`.

## Cooperative CSV export (2026-11-20)

Errors exports below 2,048 rows still download synchronously. Larger exports use
the shared Timeline CSV scheduler to encode batches of 128 rows and yield between
work chunks. Progress appears next to the active export button, which becomes
**Cancel CSV**. One export runs at a time. Paging and occurrence jumps preserve
the export; Text pauses it and Timeline resumes it. Replacing or closing the
panel cancels pending work, including on sort, filter, minor or Overhead changes.
A download starts only after the entire CSV is ready.

Three interleaved A/B rounds in headless Chromium 145 at 4× CPU throttling,
with 50,000 rows per export (synthetic, mixed kernel/user input). Figures are
medians in ms after the panel is open; file-ready stops at the download trigger,
not disk persistence. Maximum frame gap is the largest consecutive animation
frame interval per run, including the interval from click to first frame.

| Export | Click handler | Maximum frame gap | File ready |
| --- | ---: | ---: | ---: |
| Kernel groups | 261.5 → 2.0 | 263.3 → 45.6 | 261.5 → 423.2 |
| Kernel occurrences | 345.1 → 2.2 | 347.4 → 25.1 | 344.7 → 511.7 |
| Atomic calls | 279.6 → 2.1 | 282.5 → 26.7 | 279.5 → 399.7 |
| User API hints | 277.5 → 2.3 | 287.6 → 25.1 | 277.4 → 397.0 |

The large files take longer to become ready, but the UI can respond while
they are prepared. All four exports matched by byte length and SHA-256.
On the real offline `ping3` capture, the two available exports (kernel groups
and occurrences) stayed immediate and also matched by SHA-256. Browser tests
cover all four full row sequences, pause/resume after progress, scope changes,
cancel, and a 320px touch viewport with the table scrolled right.

Reproduce with `python3 tests/benchmark_error_csv.py before.html after.html`
(add `--rows 0` for the original capture). Local measurements are in
`build/benchmarks/20261120-error-csv/`.

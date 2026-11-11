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

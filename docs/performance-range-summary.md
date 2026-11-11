# Bounded range-summary browsing

The range summary originally started with 300 functions and appended another
300 for every **Load more** action. All previously visited rows stayed attached:
fully browsing a range with 10,000 distinct functions retained 10,000 table rows.
The current implementation replaces a reversible 300-function page instead.

## Scope and invariants

- Aggregation remains complete. The range, selected track, shared-filter result
  identity and duration-array identity determine the statistics cache. Changing
  the sort reorders that cached population; paging neither reaggregates nor sorts.
- At most 300 function rows stay attached. Previous/Next visits every row without
  truncation, preserves the prepared order, and shows the current interval and
  total. For 300 or fewer functions, no paging buttons appear.
- Ordinary refresh, reopening the same range and switching Text/Timeline preserve
  the page. A different range, track, shared filter, Overhead or sort starts at
  page one. Native Enter/Space actions retain usable focus at either endpoint.
- Self/inclusive overlap estimates, half-open call counting, whole-call maxima,
  overlapping error origins, context totals and independent off-CPU accounting
  are unchanged. Same-named user/kernel functions retain their separate model
  identities; no new grouping or filtering semantics are introduced.
- CSV traverses the complete sorted population regardless of the page, retaining
  its exact schema, quoting, formula protection and time bases. For uftrace
  inputs the existing `space` column still distinguishes user and kernel rows.
- The footer pins inside the detail pane, not the document. Very short panes use
  normal scrolling; narrow headers scroll out of the way instead of covering
  the table. Header/footer controls remain horizontally pinned when a wide table
  scrolls, including at 320px width. Touch paging/export controls are at least
  44px high. The existing
  explicit detail expansion remains available; the saved split is unchanged.

## Reproduce

Generate single-file Timeline templates with identical input/options, including
`--timeline --no-text-view`: one from `2637deaca57e`, one with this change. The
benchmark substitutes identical non-overlapping synthetic calls, each with a
distinct function name. This fixture is not a new capture.

```bash
python3 tests/benchmark_range.py before.html after.html \
    --functions 10000 --rounds 3 --output range-benchmark.json

PYTHONPATH=tests:. python3 -m unittest test_range_paging -v
```

A fresh page is used for each measurement in one Chromium process, alternating
before/after order between rounds. Both versions show 300 initial rows and need
33 subsequent actions. Every original function identity must be visited. Each
action waits for two animation frames. These intervals do not measure complete
paint or time to interaction; programmatic clicks do not model reading time or
scrolling to the old offscreen control. Startup, CSV cost and peak memory are
outside this benchmark.

## Measured result

Chromium `145.0.7632.6`, 1500 × 900 viewport, 10,000 synthetic distinct functions,
three alternating before/after rounds. Values are medians, not latency guarantees.

| Metric | Before: append rows | After: replace pages |
| --- | ---: | ---: |
| Initial attached function rows | 300 | 300 |
| Maximum attached function rows | 10,000 | 300 |
| Final attached function rows | 10,000 | 100 |
| Distinct functions visited | 10,000 | 10,000 |
| Final elements inside the detail panel | 80,032 | 834 |
| Initial open handler | 15.4 ms | 15.5 ms |
| All paging handlers, summed | 56.9 ms | 72.3 ms |
| All two-rAF paging intervals, summed | 1,311.2 ms | 1,090.1 ms |
| Paging actions after the initial page | 33 | 33 |

The verified improvement is bounded retained function DOM and reversible
navigation. Summed handler time increases; this is **not a general latency or
memory improvement claim**. The lower observed frame-interval total does not
establish a complete-paint speedup. Final element counts compare the complete
old table against the new last page of 100 rows, not two 300-row pages. Aggregate
storage still scales with distinct functions; complete CSV still scales with
the full exported population.

## Real reports and regression coverage

Regenerated reports from `tests/data/pi4_sched.txt` (1,014 calls, 114 distinct
functions) and `samples/sample_mixed.jsonl` (714 calls, 326 distinct functions),
both before and after the change. Their Timeline models match after removing
only measured `stats.build_time`; no public sample HTML or capture was replaced.

Checked single-file and actual CLI-generated sidecar reports, both themes, and
desktop (1400 × 860), touch portrait (390 × 844) and touch landscape (844 × 390):
24 report configurations. The mixed example exercises a real second page; the
Pi4 example verifies that small ranges need no disabled paging controls. Full
CSV bytes, header statistics and the complete ordered sequence of row contents
match the old reports. Native Enter/Space navigation reaches every row and
returns to the original first page. CSV bytes and the active page survive Text
round trips; no page errors or document-wide horizontal overflow were observed.
Touch buttons meet the 44px minimum. Landscape checks explicitly expand details;
they are not claims that the default short pane fits everything at once.

`docs/img/an_range.png` uses the real mixed report with its default detail height,
generated from an isolated parent-plus-change source tree, excluding pending
Text work. It shows the 300-of-326 interval and both address-space badges. The
capture waits for two animation frames and actual opaque main-canvas pixels.

Ten dedicated browser tests cover 1/300/301/600/601-function boundaries,
10,003-function complete traversal without aggregation/sorting during paging,
stable tied ordering, keyboard endpoint focus, first-row/footer geometry,
reopen/refresh/Text round trips, range/track/filter/Overhead/sort invalidation,
empty filters, safe full CSV across all four time bases, row search semantics,
same-named user/kernel rows in actual sidecars, 320/390px touch portrait and
844px landscape in both themes, and control bounds after horizontal scrolling.
Rapid multi-mode CSV checks use fresh browser pages per mode, avoiding
browser download-rate state leaking between independent cases.

## Validation outcome

- Final isolated parent-plus-change source passes all ten new tests and all
  seven existing `BrowserSmokeTest.test_range*` regressions (17 total), without
  the pending staged or unstaged Text changes. The final 24 real-report
  configurations described above also complete successfully.
- The required-native audit uses
  `FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh`:
  913 tests in 473.895 seconds, 909 passed, three skipped, one known failure.
  This is **not an all-green full-suite result**. The failure remains
  `test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
  the pending Text implementation reports `containIntrinsicBlockSize` as
  `auto …px`, while its assertion expects bare `…px`.
- The final 320px horizontal-header follow-up was made while that audit was
  running, after its range templates were generated. It is covered by the fresh
  final 17-test isolated run and the repeated 24-configuration real-report check,
  rather than claiming the earlier audit retested that follow-up.
- Existing Text work is preserved separately. No dependency pin, native payload,
  public capture, public sample HTML or version change is part of this work.

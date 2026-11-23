# Bounded range-summary browsing

The range summary originally started with 300 functions and appended another
300 for every **Load more** action. All previously visited rows stayed attached:
fully browsing a range with 10,000 distinct functions retained 10,000 table rows.
Reversible paging originally replaced that list with one 300-function page.
The current pager renders up to 100 functions and accepts direct page numbers.
Earlier measurements below describe the original paging change.

## Scope and invariants

- A clipped layer above the tracks shows the active time range without capturing
  input. During a drag, changing only the endpoints refreshes that layer and the
  ruler, not the slice canvas. Starting or clearing the selection, changing
  tracks, and moving the viewport can still redraw tracks; range statistics
  are computed when the drag ends.
- On large selections, the detail pane shows a status while call aggregation and
  ordering run in batches. It exposes paging and CSV only when the complete
  summary is ready. Switching away from Timeline pauses preparation; closing or
  replacing the pane prevents an older result from appearing. Small and cached
  selections still open synchronously. After giving the status a chance to
  paint, a message queue schedules later batches; timers are the fallback when
  the channel is not available.
- When changing the sort on a large summary, the current rows and keyboard
  focus stay in place. The header reports the pending order and only the last
  requested sort takes effect. Paging and export resume once the new order is
  ready; sorting reuses the existing aggregates.
- Aggregation remains complete. The range, selected track, shared-filter result
  identity and duration-array identity determine the statistics cache. Changing
  the sort reorders that cached population; paging neither reaggregates nor sorts.
- At most 100 function rows stay attached. Previous/Next and the Page field
  preserve the prepared order and show the current interval and total. For 100
  or fewer functions, no pager appears.
- Ordinary refresh, reopening the same range and switching Text/Timeline preserve
  the page. A different range, track, matching population, Overhead or sort starts
  at page one. Native Enter/Space actions retain usable focus at either endpoint.
- Function names are buttons: Tab and Enter or Space start the same exact-name
  search as clicking a row, without changing the range, page, or CSV output.
- Self/inclusive overlap estimates, half-open call counting, whole-call maxima,
  overlapping error origins, context totals and independent off-CPU accounting
  are unchanged. Same-named user/kernel functions retain their separate model
  identities; no new grouping or filtering semantics are introduced.
- CSV traverses the complete sorted population regardless of the page, retaining
  its exact schema, quoting, formula protection and time bases. For uftrace
  inputs the existing `space` column still distinguishes user and kernel rows.
  Large exports build and encode batches while the table remains usable. The
  export button becomes **Cancel CSV** until the complete download is ready.
  Switching to Text pauses preparation; changing the sort or replacing the
  summary cancels it without saving a partial file.
- The pager pins inside the detail pane, not the document. In very short panes,
  it moves before the table and scrolls with the content. Narrow headers scroll
  out of the way rather than cover the table. Header and pager controls remain horizontally pinned when a wide table
  scrolls, including at 320px width. Touch paging/export controls are at least
  44px high. Turning a page brings the first row into view when it fits alongside
  the paging buttons; in smaller panes the buttons take priority. If scrolled
  to the far right of a wide table, changing pages keeps that edge visible.
  The existing
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

## Canonical-mode follow-up

A later window-panel audit found that the range paging test used `relative`
instead of the parser's actual `rel` mode. The fixture now exercises the canonical
`abs`, `rel`, `compressed` and `count` values. The window CSV regression likewise
replaces its synthetic `packed` label with `compressed`. This strengthens the
validation inputs without changing the model or report format.

## Narrow selections on large tracks

Timeline preparation records the earliest start and latest measured end in each
block of 128 calls, with 16 bytes per block and no array for shorter tracks.
Range lookup skips blocks starting at or beyond the exclusive right boundary
or ending strictly before the left boundary, even when timestamps are out of
order. The strict left check keeps zero-duration calls at that edge; the maximum
end still finds a parent that started many blocks earlier. Calls are aggregated
in the original order, using the same overlap estimates, filter and duration
basis. Off-CPU totals reuse the existing scheduling interval index and remain
independent of the call filter.

`tests/benchmark_range.py --functions 750000 --short-selections 32 --rounds 5`
compares fresh Timeline pages with identical synthetic calls in Chromium
145.0.7632.6. Each distinct selection contains one child and overlaps the same
early spanning parent. Median selection-handler time fell from 3.2 to 1.0 ms;
the median first handler went from 5.9 to 3.0 ms. Both versions took about
33.3 ms to reach the second animation frame, so this does not establish a
paint speedup. In the 10,000-function full-range paging control, the initial
handler remained near 20 ms and both versions visited all 10,000 functions.

The browser regression compares short-range CSV statistics with an independent
full scan. It covers out-of-order call timestamps, a spanning parent,
zero-duration boundary calls, error origins, repeated function names, track
scope, filtering, and ordered, out-of-order and open off-CPU intervals. A
preparation regression cancels partway through the bounds scan and reopens the
same report without changing its source data.

In the real Pi4 scheduling report (`tests/data/pi4_sched.txt`), the full range,
a narrow all-track range, and a narrow single-track range with and without a
shared filter produced byte-identical CSV and identical panel headings before
and after the change.

## Ranges with many off-CPU records

The range preparation threshold also counts candidate off-CPU records, even
when the drawing overlay is off. For ordered tracks, two binary searches find
the candidate span. For unordered tracks, only blocks whose time bounds overlap
the range are counted and scanned. The pointer event does not walk the records
to estimate the work. Above the batching threshold, the existing job queue can
yield within a single dense track and initially shows **Summarizing off-CPU
time…** when waiting records cross the threshold. Narrow selections still render
synchronously when their candidate count is small.

The sum still uses the stored interval order with half-open clipping, adds each
overlapping record separately, and includes open intervals through their
model-supplied end. It respects the selected track, not the function filter or
the overlay toggle. A browser regression covers fractional and unordered data,
an open interval, a second track, filter and overhead changes, view suspension,
replacement by a narrow range, and closing before the job finishes.

Run `python3 tests/benchmark_range_offcpu.py BEFORE.html AFTER.html` on matched
single-file reports. The benchmark dispatches Timeline's normal range drag and
times its mouseup handler, the longest frame gap until two frames after the
results arrive, and the time to the result DOM being ready. Medians of three
alternating Chromium 145.0.7632.6 runs at 4× CPU throttling, with the overlay
off to isolate aggregation (milliseconds):

| Selection | Mouseup handler | Longest frame gap | Results ready |
| --- | ---: | ---: | ---: |
| 200,000 waits, wide range | 17.0 → 7.5 | 34.0 → 23.7 | 17.0 → 55.5 |
| 750,000 waits, wide range | 28.5 → 8.1 | 45.5 → 29.0 | 28.5 → 62.4 |
| 750,000 waits, narrow range | 5.1 → 4.3 | 19.5 → 19.1 | 5.1 → 4.3 |
| Real `ping3` (129 waits, 58,539 calls) | 8.6 → 7.8 | 73.1 → 73.3 | 129.7 → 125.7 |

Panel headings and CSV bytes match before and after, including with the overlay
on. With the overlay on, the wide synthetic selections are still dominated
by painting (about 1.7 s at 200,000 waits and over 6 s at 750,000 waits under
throttling). Delaying the summary until after a paint lets that redraw delay
results. This change improves responsiveness during aggregation, not painting
or the total time to results. The separate [dense marker optimization](performance-timeline-overview.md#group-dense-off-cpu-markers-at-wide-zoom-levels)
addresses the redraw. Local results: `build/benchmarks/20261120-range-offcpu/`.

## Keep the table when every call still matches

Applying a Text filter that matches every recorded call does not change the
range statistics. Timeline keeps the table, sort, page and focused control
when switching between that result and no filter. The filter badge and new
CSV metadata still describe the active filter, and an in-progress CSV is
canceled if it would use stale metadata. The optimization is scoped to range
statistics: other views still see an active filter, including analyses that
distinguish traced from untraced intervals.

On `ping3` (58,539 calls; 181 distinct functions in the measured range), four
alternating Chromium runs at 4× CPU throttling measured the synchronous
Timeline update, excluding Text query preparation. Applying an all-call
filter to the unfiltered range took **36.4 → 5.5 ms**; clearing that filter took
**31.6 → 0.7 ms**. The same first row remained mounted in both cases. A clear
that actually restored calls (after an empty result) remained about **29 ms**.
These are not full paint latencies. Raw comparison and layout traces are in
`build/benchmarks/20261121-range-filter/`.

### Refresh changed results without rebuilding the pane

When a filter changes the rows of the same range and the summary fits the
synchronous path, keep the range header, sort buttons, table and footer.
Replace the rows and metric badges, and add or remove the pager only as
needed. A different matching population still resets to the first page and
cancels the old CSV. If the focused row or pager disappears, or Export becomes
disabled, focus moves to the active sort button. Large pending summaries
still use the existing preparation view.

On the same `ping3` range, four alternating Chromium rounds at 4× CPU
throttling measured **30.2 → 5.6 ms** synchronously when Clear restored rows
after an empty result, and **34.7 → 10.8 ms** when applying the dense filter
after a sparse result. The layout trace no longer shows a forced layout inside
these handlers. The browser still lays out the new table rows: the median
two-frame interval for Clear was **51.0 → 50.2 ms**, not a meaningful reduction
in visual completion time. Raw results and the layout trace are in
`build/benchmarks/20261121-range-refresh/`.

### Limit the rendered page to 100 functions

The Page field accepts Enter or **Go** and reports invalid page numbers without
changing the current results. On narrow screens Previous/Next use compact arrows
and the four pager controls retain 44px touch targets. CSV still traverses the
entire range, and the table does not aggregate or sort again on a page jump.

On `ping3`, the measured range still contains 181 functions, but Clear mounts
100 rather than 181 rows. In four alternating Chromium rounds at 4× CPU
throttling, the median two-frame interval was **50.2 → 36.4 ms** when clearing
an empty filter and **56.7 → 47.7 ms** when applying the dense filter after a
sparse result. These intervals include the browser's two animation-frame waits;
they exclude Text filter preparation and do not measure physical presentation.
Applying a filter that leaves the already mounted rows intact stayed near 31 ms.
Fixed columns and row containment did not give comparable gains in local
experiments and were not kept. Raw samples: `build/benchmarks/20261121-range-layout/`.

## Large CSV exports

At 32,768 or more function rows, range export builds UTF-8 Blob parts across
short main-thread tasks instead of assembling and encoding a single large
string on click. This changes preparation, not the file schema or order. A
browser regression covers cancellation, paging and pause/resume on a 320px
viewport. A second regression disables `MessageChannel` and checks that sorting,
closing the panel, changing the filter or selecting a different range discards
the old download. The normal export path remains synchronous below the threshold.

Run `python3 tests/benchmark_range_csv.py BEFORE.html AFTER.html` with matched
single-file reports. The harness uses Chromium and records the click handler,
the longest frame gap from click until two frames after download initiation,
and the time to initiate the download. It checks byte length and SHA-256 without
including file I/O or digest calculation in the timing. Timings below are
medians of three paired Chromium 145.0.7632.6 runs (milliseconds):

| Range | Click handler | Longest frame gap | Download ready |
|---|---:|---:|---:|
| Real `ping3` (58,539 calls) | 3.9 → 3.9 | 16.7 → 16.6 | 3.8 → 3.8 |
| 300,000 distinct functions | 262.2 → 0.6 | 264.5 → 22.1 | 262.2 → 288.2 |
| 750,000 distinct functions | 626.0 → 0.6 | 626.8 → 25.6 | 625.9 → 680.3 |

All three exports are byte-identical. The larger downloads take slightly longer
to become ready, in exchange for keeping the interface responsive. The real
trace has fewer than 32,768 distinct functions and remains on the small-export
path. These are frame-gap observations, not a guarantee that every device can
finish each task within the target budget or a measurement of peak memory.

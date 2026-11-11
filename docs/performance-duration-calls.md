# Bounded duration-call browsing

November 12, 2026. This follow-up targets the **Slowest calls** list in Timeline's
**Duration distribution**, not report startup or the complete large-trace model.
Regenerate existing HTML to get the new controls.

## Behavior and evidence semantics

- **Previous / Next** replace pages of at most 50 calls. Attached call rows do
  not accumulate as the user visits more pages. The status reports the current
  range and full matching count, in descending duration / original-index order.
- Ordinary refreshes and reopening retain the page. A changed function, call
  site, shared-filter population, duration bucket or Overhead population starts
  at page one. Keyboard endpoints transfer focus to an enabled direction;
  unchanged-population refreshes restore the focused control.
- The pager stays within the call-browser section, not over the later call-site
  or Slow vs fast tables. Very short panels scroll it normally. Touch paging,
  bucket and export controls have 44px minimum heights. The temporary expansion
  control remains available for cramped detail panes.
- Initial pages now contain up to 50 calls in both the whole-population and
  bucket views. Previously, the initial whole-population list had 15 calls and
  each **Load more** appended 50; bucket views started with 50.
- Full function/call-site percentiles, totals and histogram/cohort populations
  do not become page statistics. Selecting a duration bucket still changes the
  call list and export, not those overall statistics or Slow vs fast cohorts.
- CSV exports every call in the current function/shared-filter/site/bucket
  scope, not just the current page. Quoting, formula protection, numeric bucket
  bounds, raw/adjusted durations, recorded positions and time bases are unchanged.
  A row click still highlights its real call without closing the distribution.

## Reproduce the comparison

Use single-file Timeline templates generated with the same input and CLI
options, including `--timeline --no-text-view`: one from `7af143b3b6a4` and one
with this change. The benchmark substitutes identical non-overlapping synthetic
calls of one function from one call site into both templates. The original
template models match except for measured `stats.build_time`.

```bash
python3 tests/benchmark_distcalls.py before.html after.html \
    --calls 5000 --rounds 3 --output duration-call-benchmark.json

PYTHONPATH=tests:. python3 -m unittest test_dist_paging -v
```

The browser opens a fresh page per measurement, in one Chromium process, and
alternates before/after order between rounds. It opens the distribution and
traverses the complete call list, asserting that every original identity is
visited. Each paging action waits for two animation frames. Handler times and
two-rAF intervals are not complete paint/TTI measurements; programmatic clicks
do not model reading time or scrolling to an offscreen old control. The fixture
is not a new capture. Startup, CSV cost and peak memory are not measured.

## Measured result

Chromium `145.0.7632.6`, a 1500 × 900 viewport, 5,000 synthetic calls and three
alternating before/after rounds. Both templates have identical Timeline models
apart from generation time. These are medians, not latency guarantees.

| Metric | Before: append rows | After: replace pages |
| --- | ---: | ---: |
| Initial attached call rows | 15 | 50 |
| Maximum / final attached call rows | 5,000 | 50 |
| Distinct calls visited | 5,000 | 5,000 |
| Final elements inside the distribution panel | 35,075 | 429 |
| Initial open handler | 10.3 ms | 7.4 ms |
| All paging handlers, summed | 30.2 ms | 45.6 ms |
| All two-rAF paging intervals, summed | 3,208.2 ms | 3,285.2 ms |
| Paging actions after the initial list | 100 | 99 |

The verified gain is the retained call-DOM bound and reversible navigation,
**not faster complete traversal**: this run has higher summed paging-handler
cost and slightly longer frame intervals. The new handler also aligns the list
under its header. Initial rendering and action counts differ because the first
page grows from 15 to 50 calls. Opening times vary; the lower observed open time
does not establish a general latency improvement or an overall memory bound.

## Real-report verification

Regenerated reports from the existing `tests/data/pi4_sched.txt` (1,014 calls)
and `samples/sample_mixed.jsonl` (714 calls), before and after this change. Their
Timeline models match except for measured `stats.build_time`; no public capture
or sample HTML was replaced.

The representative distributions are `unwind_find_stack.constprop.0` (115 calls,
three pages) and `preempt_count_sub` (55 calls, two pages). Each was checked in
single-file and actual CLI-generated sidecar reports, both themes, and desktop
(1400 × 860), touch portrait (390 × 844) and touch landscape (844 × 390):
24 report configurations. Every call was reached using native Enter/Space
paging; returning to page one restored its original call identities. CSV bytes
before, during and after paging match the old report exactly, and all displayed
min/p50/p90/p99/max/mean values remain unchanged. No page errors or document-wide
horizontal overflow were observed. Touch controls meet the 44px minimum; short
landscape checks use the explicit temporary detail expansion.

`docs/img/an_dist.png` is refreshed from the real Pi4 report. The screenshot uses
a manually enlarged detail pane and collapsed notes, rather than claiming that
the default pane fits the histogram, every control and call rows simultaneously.
The pager is scoped to the call section and does not cover subsequent call-site
or comparison tables.

## Validation

- Ten dedicated browser tests cover 1/50/51/103-call boundaries, every identity
  in 5,207 calls, stable tie order without sorting during paging, native keyboard
  endpoint focus, bucket/site/shared filters, empty populations, refresh/reopen,
  Overhead, all four time bases, full CSV and touch sidecar/expansion behavior.
- The focused distribution, cohort, task-paging, detail-expansion and lifecycle
  selection passes all 80 tests. Exporting only this change over its parent,
  without the pending Text work, independently passes the ten new tests and all
  15 existing distribution/cohort regressions.
- The 24 real-report configurations above preserve complete CSV bytes and
  displayed distribution statistics, rather than only checking row counts.
- `FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh`
  runs 897 tests: 893 pass, three skip and one fails in the preserved, uncommitted
  Text work. The failing
  `test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`
  expects bare pixel lengths, while Chromium's computed
  `containIntrinsicBlockSize` includes the `auto` prefix. This is not an
  all-green full audit. That Text test and its associated source/index changes
  are neither modified nor included in this duration-call change.

## Remaining limits

Only attached rows in the main slowest-call list are bounded. Full Timeline
arrays, sorted populations, per-site groups, histogram buckets and complete CSV
remain in memory. Initial aggregation and sorting still scale with the recording.
Call-site rows, Slow vs fast profile rows and other analysis panels retain their
own independent browsing behavior; this is not a bound on the whole panel's DOM.
There is no worker, out-of-core loading or overall large-trace speedup claim.

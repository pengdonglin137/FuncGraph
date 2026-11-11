# Bounded critical-section and scheduling-window pages

Sections previously appended 200 rows at a time; Scheduling latency appended
300. Browsing all 10,000 records kept 10,000 rows attached in either panel.
Both now use reversible pages, keeping their original initial sizes.

## Behavior and limits

- Sections keeps at most 200 rows and Scheduling latency at most 300. Native
  Previous/Next buttons replace the table body and report the current interval
  and total. One-page populations need no disabled navigation clutter.
- Each section kind remembers its own page. Reopening, ordinary refresh and
  Text round trips preserve the page; changing the sort, shared filter or
  Overhead starts at page one. Refresh retains usable paging/export focus.
- Full aggregates and prepared ordering are reused. New section rows still
  compute their overlap profiles on first visit; subsequent visits reuse them.
  This does not claim paging is allocation-free or that all aggregation becomes
  lazy: Scheduling latency prepares every profile for its full-population top-8
  overview, and sorting by profile data or full CSV visits the needed records.
- Full CSV scope, byte representation, quoting, original record indices,
  statistics, source trace lines, time bases and formula protection stay intact.
  Window positions and lengths stay recorded; Overhead only changes self-time
  estimates. Clicking a row still zooms to that window; paging does not change
  this chart position or narrow the export to the selected window.
- Footers pin inside sufficiently tall panes. Short panes and narrow headers
  scroll vertically; horizontal anchoring keeps controls visible over wide
  tables, including 320px windows. Touch paging, kind, sorting and export controls
  are at least 44px high. The existing temporary detail expansion does not change
  the saved split.

The memory improvement is specifically **bounded retained table DOM**, not an
overall constant-memory claim. Cached populations, profiles and complete CSV
storage still scale with the number of records.

## Reproduce

Generate Timeline templates with identical input/options, including
`--timeline --no-text-view`, one from `5ebfca271299` and one with this change:

```bash
python3 tests/benchmark_windows.py before.html after.html \
    --windows 10000 --rounds 3 --output window-benchmark.json

PYTHONPATH=tests:. python3 -m unittest test_window_paging -v
```

The benchmark substitutes identical synthetic non-overlapping calls/windows,
one function per window and all three section kinds. These are not hardware
captures. A fresh page is used for every measurement in one Chromium process;
before/after order alternates between rounds within each panel. Both sides
visit every original record with equal initial sizes and equal action counts.

Actions wait for two animation frames. Handler times and two-rAF intervals are
not complete paint/TTI measurements; programmatic clicks do not include reading
time or scrolling to an offscreen old control. Startup, CSV and peak memory are
not measured. Final latency element counts compare 10,000 old rows against the
new last page of 100 rows, not two 300-row pages.

## Measured result

Chromium `145.0.7632.6`, 1500 × 900, 10,000 windows per panel, three alternating
rounds. Values are medians, not performance guarantees.

| Metric | Sections before | Sections after | Latency before | Latency after |
| --- | ---: | ---: | ---: | ---: |
| Initial attached rows | 200 | 200 | 300 | 300 |
| Maximum attached rows | 10,000 | 200 | 10,000 | 300 |
| Final attached rows | 10,000 | 200 | 10,000 | 100 |
| Distinct records visited | 10,000 | 10,000 | 10,000 | 10,000 |
| Final panel elements | 80,033 | 1,635 | 90,051 | 953 |
| Open handler | 14.8 ms | 15.4 ms | 23.4 ms | 23.7 ms |
| Summed paging handlers | 86.9 ms | 95.5 ms | 77.6 ms | 97.1 ms |
| Summed two-rAF intervals | 1,788.2 ms | 1,621.2 ms | 1,447.5 ms | 1,092.2 ms |
| Actions after the initial page | 49 | 49 | 33 | 33 |

Summed paging handlers increase. The supported benefit is the retained-row
bound and reversible navigation, **not a general traversal speedup**. Lower
observed frame-interval totals do not establish faster complete paints.

## Regression coverage

The focused run passes 36 tests in 64.061 seconds: 11 new window-paging tests,
10 range-paging tests and 15 existing window/range browser regressions. An
isolated parent-plus-change source tree, excluding the pending Text work, also
passes the same 36 tests in 68.135 seconds.

The new tests cover:

- Exact boundaries at 1/200/201/400/401 sections and 1/300/301/600/601 windows;
  traversal of 5,003 original records without growing the retained DOM.
- All three section kinds and their original indices, stable ordering, reuse of
  warmed profiles, independent pages and full byte-identical, safely quoted CSV.
- Keyboard paging, endpoint focus, refresh/export focus, reopening, Text round
  trips and invalidation by sorting, filtering or Overhead, including empty scopes.
- Canonical `abs`, `rel`, `compressed` and `count` modes, preserving recorded
  geometry separately from self-time estimates, row navigation and chart zoom.
- Valid synthetic function-graph text through the real parser and CLI sidecar
  generator, rather than only direct browser-model injection.
- Both themes, 390px/320px touch portrait and 844px landscape, horizontal control
  anchoring, 44px touch controls and short-pane fallback.

The required-native audit runs with:

```bash
FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

It runs 924 tests in 507.062 seconds: 920 pass, three skip and one fails. The
failure is the already-recorded, unrelated pending Text regression
`test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
Chromium reports `containIntrinsicBlockSize` as `auto …px`, while the assertion
expects bare `…px`. This is not an all-green full-suite result. That work remains
untouched and is not included in the window-paging commit.

## Real-report compatibility

Before/after reports generated from `tests/data/pi4_sched.txt` and
`samples/sample_mixed.jsonl` cover 24 report configurations and 96 panel cases:
two fixtures, single-file and actual CLI sidecar output, both themes, and
1400 × 860 / 390 × 844 / 844 × 390 viewports. Every configuration checks all
three section kinds plus Scheduling latency.

- Parsed models match exactly after excluding only `stats.build_time`.
- Full CSV bytes, ordered rows and header statistics match before/after; Text
  round trips preserve rows and exports. The small scopes omit paging buttons.
- No JavaScript errors or document-wide horizontal overflow occur. Export and
  close stay horizontally reachable; touch export controls are at least 44px high.
- Pi4 has 1,014 calls, section populations 10/28/0 and nine latency windows.
  Mixed has 714 calls, section populations 14/44/5 and no latency windows.

These real captures exercise single-page compatibility, empty states and layout,
not large-record pagination. Large-population bounds are established separately
by the synthetic benchmark and browser/parser tests; the captures are not
presented as evidence of a large-hardware-trace speedup.

## Documentation screenshots

`docs/img/an_sections.png` and `docs/img/an_lat.png` use the final single-file
Pi4 report with its full Text view, 1,500 original trace lines and 1,014 calls.
They are captured at 1400 × 860 after collapsing Notes and manually resizing
the detail split to leave both the timeline and table visible. Capture waits
for rendering and populated canvas pixels. This is an intentionally arranged
documentation view, not a claim that the default split displays every panel
without scrolling.

No dependency pin, native payload, public capture, public sample HTML or version
change is part of this work.

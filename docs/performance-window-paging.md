# Bounded critical-section and scheduling-window pages

Sections previously appended 200 rows at a time; Scheduling latency appended
300. Browsing all 10,000 records kept 10,000 rows attached in either panel.
Reversible pages initially kept those page sizes. Both panels now use 100
rows per page to reduce the cost of opening the table.

## Behavior and limits

- Both panels keep at most 100 rows. Previous/Next or a **Page** number
  submitted with Enter or **Go** replaces the table body and reports the
  current interval and total. Invalid entries leave results unchanged and
  report the valid range. Single-page results omit navigation controls.
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
- Pagers pin inside sufficiently tall panes. In very short panes they move
  before the table and scroll with the content; horizontal anchoring keeps controls visible over wide
  tables, including 320px windows. Touch paging, kind, sorting and export controls
  are at least 44px high. The existing temporary detail expansion does not change
  the saved split.

The memory improvement is specifically **bounded retained table DOM**, not an
overall constant-memory claim. Cached populations, profiles and complete CSV
storage still scale with the number of records.

### Short-pane interaction check (2026-11-19)

At 320 × 700 in Chromium's touch emulation, reach the start of a wide table
with the pane scrolled horizontally to the right. A coordinate tap works on
Next in Critical sections, Scheduling latency and the Range summary, without
automatically scrolling to the button or past the table body. All three
checks failed before the layout change. At the last page focus moves to
Previous, and Enter returns to the first page. Resizing across the split-height
threshold moves the existing pager without losing focus or rebuilding the
current rows. The chart position is unchanged by pagination. This verifies
reachability, not rendering speed or support on every touch device.

Turning a page also brings its first row into view when the short pane can fit
it alongside the buttons. The buttons remain reachable when there is not enough
height for both. The Chromium regression checks 320 × 700 with a touch tap and
subsequent Enter presses, including a one-row last page and the return to a
wider page. The rightmost edge stays in view as the table width changes; an
interior horizontal offset stays put when it still fits.

## Smaller pages and direct navigation (2026-11-20)

On the same `ping3` capture (58,539 calls), Chromium `145.0.7632.6` at
1500 × 900, with 4× CPU throttling, measured five alternating before/after
rounds on fresh pages after Timeline was ready. Values are medians in ms.

| Interaction | Attached rows before → after | Handler before → after | Two-frame interval before → after |
| --- | ---: | ---: | ---: |
| Open Critical sections (376 sections in the initial kind) | 200 → 100 | 90.4 → 69.6 | 143.8 → 114.0 |
| Next in Critical sections | 176 → 100 | 47.9 → 31.0 | 83.4 → 50.5 |
| Open Scheduling latency (135 windows) | 135 → 100 | 55.0 → 50.6 | 100.5 → 90.1 |
| Next in Scheduling latency | — → 35 | — → 10.9 | — → 33.2 |

The older latency table fit on one page; the new version moves its last 35
rows to page two. Direct return to page one took 49.9 ms for Sections and
33.1 ms for latency by the two-frame measure. These are click-to-frame
proxies, not full paint timings. They do not establish a speedup for reading
every record, which may take more page turns. Full CSV SHA-256 matched for
each panel across versions; the page controls do not change export scope.

The compact layout uses arrow buttons with accessible Previous/Next names and
44px targets, keeping page entry beside them. The touch regression checks
portrait/landscape, both themes, and a 320px short pane scrolled horizontally.
The keyboard regression checks invalid input, direct navigation to a partial
last page, and unchanged chart and export data.

```bash
python3 tests/benchmark_window_entry.py before.html after.html \
    --rounds 5 --cpu-rate 4 --output window-entry.json
```

Generate both inputs from the same trace and report options before comparing.

### In-place sorting (2026-11-20)

Changing the sort replaces only the result rows, reuses the existing panel,
header, and page controls, and returns to page one. The activated column stays
focused and visible in a horizontally scrolled or short pane. Activating the
already sorted column keeps the current page and does not rebuild any rows.
The chart position, aggregates, and complete sorted CSV are unchanged.

On the same `ping3` capture and Chromium setup, seven alternating runs per
version observed these medians in ms:

| Interaction | Handler before → after | Two-frame interval before → after |
| --- | ---: | ---: |
| Sections: change sort to top self estimate | 33.6 → 31.6 | 49.8 → 50.0 |
| Sections: activate the same sort again | 28.2 → 0.1 | 50.2 → 33.5 |
| Latency: change sort to end reason | 25.1 → 22.5 | 49.7 → 33.3 |
| Latency: activate the same sort again | 23.5 → 0.1 | 33.7 → 33.4 |

The primary gain is eliminating work on repeated activation; changing
columns still orders the full population and redraws its first page. The
sorted CSV SHA-256 matched for each panel. Reproduce with `--sort --rounds 7`
on the command above, using the 100-row page-jump preview as the baseline.

### Cooperative full CSV export (2026-11-20)

The scope remains the entire sorted, filtered population, not the current
page. Exports below 2,048 rows take the immediate path. Larger exports build
Blob parts in batches with progress and **Cancel CSV**. Paging keeps the job;
switching views pauses it until Timeline resumes. Changes to sort, kind,
filter or Overhead, replacing the panel, and closing it discard partial work.
The browser only receives the completed download. Chunking improves input
responsiveness, not time-to-file or memory scaling. The full Blob still
scales with output size, and a single unusually costly window is not
preempted mid-row.

Five alternating runs on synthetic, disjoint windows in Chromium
`145.0.7632.6`, 1500 × 900, 4× CPU throttling. Times are medians in ms.
The frame column is the maximum observed gap from click through file creation
for each run, then the median across runs; it is not an end-to-end paint metric.

| Results | Panel | Click handler before → after | Longest frame gap before → after | File ready before → after |
| ---: | --- | ---: | ---: | ---: |
| 4,096 | Sections | 41.3 → 3.3 | 41.9 → 20.8 | 41.3 → 75.5 |
| 4,096 | Latency | 34.1 → 2.9 | 34.6 → 20.1 | 34.0 → 66.3 |
| 50,000 | Sections | 422.9 → 3.1 | 424.6 → 24.6 | 422.6 → 530.8 |
| 50,000 | Latency | 325.6 → 3.0 | 326.7 → 22.8 | 325.6 → 422.8 |

The CSV length and SHA-256 match before/after for every sample. Regression
tests exercise progress at 320px, pause after partial preparation, Resume,
cancellation and scope changes, stable ordering, escaping, and full bytes
across page turns. Synthetic windows are not measurements from a real kernel.

```bash
python3 tests/benchmark_window_csv.py before.html after.html \
    --windows 4096 50000 --rounds 5 --cpu-rate 4
```

## Original append-to-page comparison

Generate Timeline templates with identical input/options, including
`--timeline --no-text-view`, one from `5ebfca271299` and one with the original
200/300-row pagination:

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

### Measured result

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

### Original regression coverage

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

## Select top contributors without sorting every function (2026-11-19)

Per-window profiles retain only the top three `(function, self estimate)`
pairs; the Scheduling latency overview retains the top eight. The ranking
step no longer copies and sorts all contributions. It keeps the same descending
estimate order and breaks ties by original symbol index. The overview still
adds contributions from every function across all matching windows, including
those that never appear in a window's top three. This does not bound the
accumulation maps or avoid visiting the underlying calls.

At 1500 × 900 in headless Chromium 145.0.7632.6, cold panel-open handler
medians were:

| Synthetic fixture | Critical sections | Scheduling latency |
| --- | ---: | ---: |
| 24 windows × 8,192 calls, five alternating pairs | 35.4 → 23.7 ms | 62.7 → 36.8 ms |
| 10,000 windows × one call, three alternating pairs | 15.9 → 15.2 ms | 24.1 → 23.5 ms |

Timing starts after the Timeline is ready and excludes rasterization and CSV
export. Both versions visit every window with the same page sizes. The
browser regression additionally verifies tied CSV ranks and eight overview
winners that never make a per-window top three. The multi-call benchmark is
reproducible with `python3 tests/benchmark_windows.py BEFORE.html AFTER.html
--windows 24 --calls-per-window 8192 --rounds 5`. Raw results and regression
logs are in `build/validation/20261119-window-top-contributors/`.

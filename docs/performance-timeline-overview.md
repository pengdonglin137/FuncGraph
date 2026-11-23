# Timeline overview density reuse

## Scope and root cause

The November 18, 2026 change avoids repeating the same full-trace overview
calculation. It does not change the overview's values or add another UI control.

Previously every Timeline `resize()` invalidated the density array, including
notifications that left the track width unchanged. Theme changes invalidated it
too. Resizing the detail panel or returning from Text could therefore traverse
the recorded root rows again even though the overview's inputs were unchanged.

`buildOverview()` uses the original root-call rows, KVM intervals, complete time
extent and CSS track width. The first three are immutable within a Timeline
instance. The cache now records the width used for its existing `Float32Array`:
the first paint builds it, and a changed width rebuilds it. There is no extra
density array, observer, sampling, truncation or change to the accumulation
arithmetic. A device-pixel-ratio change still repaints the canvas backing store
but does not change its CSS-pixel density input.

The ruler, theme colors, error markers, visible-range frame and other overlays
still repaint. Search, shared filters, inline visibility and track collapse
retain the full-trace overview rather than silently changing its population.
The blue bars represent relative root-row/KVM interval coverage, potentially
including waits; they are not a measurement of CPU utilization.

## Measured comparison

The baseline is `6de06f6fbe27`. A real kernel-only `write4.log` capture supplied
38,938 input rows and 29,586 calls. Its comparison report was generated with
`--no-btf --timeline --no-text-view --report-mode single`, without source
resolution. The original large source-rich report was not modified or timed.
The baseline counterpart replaces only the embedded `render.js` and `panels.js`
with that commit's versions; reversing the replacement is byte-exact.

Two deliberately synthetic cases exercise different overview costs: 100,000
sequential roots in one task, and 10,000 fully overlapping roots in 10,000
tasks. The latter is a stress case, not a representative capture or a claim
about ordinary workloads.

Each side ran three times in fresh Chromium 145.0.7632.6 contexts at 1500 × 900,
alternating execution order. Fonts and two animation frames settled before
measurement. Each run requested eight identical-size resizes and four theme
switches. The table reports the median total time spent inside
`buildOverview()` for each group, not total interaction or frame latency.

| Input | Calls / tasks | Eight resizes: before → after | Four theme switches: before → after |
| --- | ---: | ---: | ---: |
| Captured kernel trace | 29,586 / 1 | 0.5 ms → 0 ms | 0.2 ms → 0 ms |
| Synthetic sequential roots | 100,000 / 1 | 11.8 ms → 0 ms | 4.4 ms → 0 ms |
| Synthetic overlapping roots | 10,000 / 10,000 | 509.5 ms → 0 ms | 211.8 ms → 0 ms |

The deterministic result is **eight rebuilds → zero** for unchanged-width
resizes and **four rebuilds → zero** for theme switches. Zero means the builder
was not called; painting and event handling still take time. Other local browser
tests ran during measurement, so timing differences are illustrative, not a
controlled performance guarantee. The expensive initial stress-case build is
unchanged. Two-animation-frame feedback is not physical presentation latency;
these measurements do not establish startup, general FPS or peak-memory gains.

## Faster rebuilds when the width changes

Rasterization now calculates fractional coverage only for the endpoints of
each root or KVM span. Pixels fully covered by the span receive an increment
of one. It still accumulates the same `Float32` values in the same order; the
cache and input population are unchanged.

Three alternating before/after pairs in Chromium 145 at 1500 × 900 kept all
overview values, exact ruler PNGs and the complete inspected Timeline model
identical. Across the six width changes per variant, median time inside the
builder was:

| Input | Before | After |
| --- | ---: | ---: |
| Real `ping3`, 58,539 calls | 0.20 ms | 0.10 ms |
| Synthetic 100,000 short roots | 2.10 ms | 1.75 ms |
| Synthetic 10,000 overlapping roots | 47.55 ms | 22.10 ms |

The parallel stress case shows the avoided work, not typical `ping3` drag
latency. The algorithm still visits covered pixels, and residual costs can
exceed a frame budget on highly parallel traces. Raw measurements are in
`build/benchmarks/20261120-overview-span/`, comparing `ping3-dense-opacity.html`
and `ping3-overview-span.html`. The benchmark probe now times the body of
`buildOverview()` only, not the ruler helpers that follow it.

## Fidelity checks

`tests/benchmark_overview.py` instruments temporary report copies only. It checks
the complete input payloads except generated `stats.build_time`, all inspected
frame columns, strings, synthetic frames and task-row ordering. It rereads the
live model before and after interactions. Every overview density value, maximum
and exact ruler PNG must match between versions, including after changing the
viewport width to 1240 and back to 1500. A mismatch fails the comparison rather
than producing timing results for different data.

`tests/test_timeline_overview.py` independently computes pixel/interval overlap
and covers:

- Kernel calls with source/inline information, CPU-only records, mixed
  user/kernel calls, KVM-only events, combined calls/KVM and lifecycle-only data.
- Single-file and sidecar reports, light/dark themes and device pixel ratios
  one and two, with live ratio transitions through 1.25 and two.
- Real width changes, identical-width resizes, pointer/keyboard detail resizing
  and Text/Timeline round trips.
- Inline visibility, search, shared filtering and collapsed tracks, retaining
  the complete recorded model and full-trace density.

The broader hover regression also checks single-frame pointer coalescing and
selection/source preservation across view changes. Its write counter targets
the tooltip body, not the outer container introduced before this change.
View-switch scrolling settles before sending the measured pointer burst; the
pointer burst itself still has the original one-frame assertion.

Public kernel and mixed HTML samples embed the new runtime. Their five JSON
payloads are unchanged except generated build time, as are the complete Text
chunks and row attributes. No recorded timing, address, flag, source, argument
or return value is removed.

The final relevant Timeline/browser regression run passed 520 tests with no
skips, including the five new overview tests. Thirteen documentation/example
tests also passed. Regenerated `sample.png`, `sample_mixed.png`,
`samples/sample_text.png`, `docs/img/tl_detail.png` and `docs/img/tl_help.png`
match their previous PNGs byte for byte, so no screenshot changes are needed.

## Reproduce

Keep reports generated before and after the change, with the same trace and
generation options. For the checked-in sample and baseline:

```bash
git show 6de06f6fbe27:sample.html > /tmp/overview-before.html
python3 tests/benchmark_overview.py /tmp/overview-before.html sample.html \
  --output build/overview-sample.json
python3 tests/benchmark_overview.py /tmp/overview-before.html sample.html \
  --scenario flat --count 100000 --output build/overview-flat.json
python3 tests/benchmark_overview.py /tmp/overview-before.html sample.html \
  --scenario parallel --count 10000 --output build/overview-parallel.json
PYTHONPATH=tests python3 -m unittest \
  test_timeline_overview test_timeline_hover test_tooltip test_timeline_resize \
  test_detail_resize test_event_only test_kvm -v
```

Playwright and its Chromium browser must be installed. The benchmark JSONs and
comparison logs from this run are retained locally in `build/overview-audit/`;
they are ignored validation artifacts, not files included in the package.
Regenerate existing HTML reports to embed the optimization. Related details:
[resize integrity](performance-timeline-resize.md) and
[bounded hover previews](performance-tooltips.md).

## Keep the ruler painted during track-only interactions

Hover highlights and vertical track scrolling repaint the track canvas, not
the ruler or overview. A zoom, selection, range, palette or layout change can
still invalidate both layers. Changing the ruler backing size always repaints
it in the same animation frame. Cursor and tooltip updates remain separate;
this does not add another canvas or cache any rendered pixels.

On the `pi4_sched` fixture at 1280 × 720, three alternating Chromium
145.0.7632.6 runs per version measured **24 → 0 ruler paints** for 24 hover
transitions and again for 24 vertical scroll updates. Six zoom changes still
painted the ruler six times. The sampled track and ruler images matched
byte-for-byte before and after, and both reports had the same logical input.
The baseline spent a median 1.7 ms in the ruler painter over the hover sequence
and 1.5 ms over the scroll sequence. These are painter-call timings after
initial layout settles, not physical input latency or frame-rate gains.
Evidence: `build/validation/20261119-timeline-ruler/`.

## Skip rows outside the vertical canvas

Expanded task tracks and the CPU strip compute their first visible row from
the scroll offset, then stop when the next row starts below the canvas. They
still include partial rows and the same exact-edge cases as the previous
linear traversal. This bounds the call-depth and CPU row loops, not the
scheduling and error overlays or the size of the underlying trace model.

At 1280 × 720, Chromium stress reports with a 16,000-level call stack and a
4,096-row CPU strip reduced visits per tested paint from 16,000 or 4,096 to
**24–27**. Canvas images matched byte-for-byte at the top, on and between row
boundaries, and at the end of the stack, at DPR 1, 1.25 and 2. This synthetic
test measures loop work and pixel fidelity, not complete frame time on a real
trace. Probe and paired results: `build/validation/20261119-timeline-rows/`.

## Reuse the ruler backdrop during pan and zoom

The ruler keeps one backing-size bitmap of the background, density bars and
error markers. That same bitmap holds the ticks and labels for the current
time window. Panning or zooming repaints only the lower axis area; selecting
another call at the same scale and offset reuses the axis pixels and draws the
current selection and status on top. Size, pixel ratio, palette, density, and
the visible error scope invalidate the bitmap. Marker positions, draw order
and translucency are unchanged.

In Chromium 145.0.7632.6 at 1500 × 900 and DPR 1, a synthetic report with
45,000 visible error chains spent a median **221.6 → 1.5 ms** in the ruler
function across sixteen successive zooms, after the initial paint (three
alternating before/after pairs). Sampled ruler images matched across zoom,
selection, theme, error-overlay toggles and resize at DPR 1, 1.25 and 2.
These timings exclude initial paint, backdrop rebuilds, compositor work and
end-to-end input latency. Probe and results:
`build/validation/20261119-ruler-backdrop/`.

On the real offline `ping3` report, four selections at a fixed time window
went from **40 to 0 tick label draws** at each of DPR 1, 1.25 and 2. The ruler
still repaints the selection and duration on each change. All 24 sampled ruler
images matched pixel for pixel across selection, zoom, pan and theme changes
in the before/after reports. This counts drawing work, not input-to-paint time.
The probe and results are in `build/benchmarks/20261119-trace-jump/stable-axis.jsonl`.

## Skip offscreen error origins on tracks

The track canvas uses binary search when error origins are in chronological
order. For disordered origins it skips non-intersecting blocks of 128 without
reordering the remaining markers. It keeps the existing six-pixel margin,
scope, row clipping and width rules. The regression checks the original draw
sequence, including tied starts and disordered chains with overlapping markers.

On Chromium 145.0.7632.6 at 1500 × 900 and DPR 1, a synthetic track with
200,000 kernel error chains spent a median **39.4 → 0.9 ms** in the kernel
origin overlay across 20 narrow views (three alternating before/after pairs).
Sampled track images matched across zoom, theme and error scope at DPR 1,
1.25 and 2. These timings exclude preparation, other canvas layers and
end-to-end latency. Probe and results: `build/validation/20261119-track-error-culling/`.
## Dense filtered pixels in narrow views (2026-11-19)

Each filtered row builds 1,024-entry matching-prefix blocks only when a dense
pixel needs them. Painting and hit testing share those blocks. The first
matching frame and exact match count still span all of the pixel's blocks;
when there is no match, the first frame remains the representative. A new
search or filter mask invalidates the blocks. Panning and cosmetic redraws
reuse blocks they have already visited.

A browser regression uses a row of 60,000 frames, including a 2,000-frame
burst at one timestamp. For a narrow view of the burst the old code reads
60,053 row entries on the first filtered paint. The new test bounds the
first paint at fewer than 4,000 reads and a repeated paint at fewer than 300,
and checks that clicking the pixel selects the first matching frame.

On Chromium 145.0.7632.6 at 1500 × 900, three alternating before/after
pairs with 12 measured redraws per phase use the same 400,000-call row.
Canvas images match byte-for-byte in both views. Median callback times:

| View | First draw with new mask (before → after) | Cached redraw (before → after) |
| --- | ---: | ---: |
| Narrow | 2.40 → 0.15 ms | 0.20 → 0.10 ms |
| Full | 2.50 → 2.80 ms | 0.60 → 0.70 ms |

The full view touches most blocks and incurs some extra indexing cost; this
change targets cold, narrow views rather than improving all redraws. These
measurements exclude building the filter mask, browser rasterization and input
latency. Run `python3 tests/benchmark_dense_matches.py BEFORE.html AFTER.html`
with single-file reports built from the two runtimes. Local logs and raw samples
are under `build/validation/20261119-dense-match-blocks/`.

## Skip wakeup blocks above or below the viewport

Each existing 128-wakeup time block also records the first and last endpoint
track in layout order. Painting reads those tracks' current positions, so
scrolling, folding and hiding tracks need no wakeup reindex. A block is skipped
only if its entire track span is on one side of the viewport. Arrows with
endpoints on opposite sides still cross the canvas; blocks without reliable
track bounds keep the original per-wakeup checks. The optimization does not
reorder wakeups or change dense-arrow counts and hover priority.

In the browser regression, a middle viewport visits **3 instead of 259**
wakeups and retains both crossing directions. At 1500 × 900 in Chromium 145,
three alternating pairs over 256,000 synthetic wakeups and 128 tracks gave
the following median totals across 18 full-window redraw callbacks:

| Endpoint distribution | Before | After |
| --- | ---: | ---: |
| Adjacent tracks, grouped by time block | 53.1 ms | 18.4 ms |
| Interleaved across all tracks in each block | 978.7 ms | 982.1 ms |

Sampled track canvases matched byte for byte at six scroll positions for both
distributions. The interleaved case cannot skip these blocks. The timings do
not measure initial model preparation, compositor work or input latency.
Probe and logs: `build/validation/20261119-track-wake-culling/`.

### Skip off-CPU records outside the visible stack rows

Each 128-record off-CPU block now records its shallowest and deepest rows
during the existing time-index pass. Inline rows have separate bounds when
present. Painting and hit testing skip blocks that cannot intersect the
requested rows; if the full track is in view, the time-only path is used.
Records, overlap order, first-hit priority, and time-only analysis are unchanged.
The depth index uses eight bytes per block per distinct row layout.

In three alternating Chromium 145 runs at 4× CPU throttling, with 200,000
synthetic off-CPU intervals and a deep track scrolled past most of their rows,
the wide-view redraw callback median decreased from **10.8 to 4.9 ms**.
Narrow redraws stayed below 1 ms; Timeline startup measured 289.4 vs 288.0 ms.
Real `ping3` redraws were 4.15 vs 4.25 ms. Track canvas PNG digests matched in
all cases. These are callback durations, not end-to-end input latency.
Reproduce with `python3 tests/benchmark_offcpu_viewport.py before.html after.html`;
pass `--intervals 0` to use the original captures. Local results:
`build/benchmarks/20261120-offcpu-row-culling/`.

### Group dense off-CPU markers at wide zoom levels

For an ordered track with at least 1,024 waits, four or more consecutive waits
no wider than one CSS pixel starting in the same pixel and stack row share one
hatched marker. Mixed blocked/preempted groups retain an amber accent. Other
waits and unordered tracks retain the per-record drawing path. Groups are
computed from visible rows and do not change the scheduling model, statistics,
or CSV. Exact-time hit testing still takes priority; clicking a gap inside a
dense marker opens the first original wait in the group and labels the choice
in the tooltip and details. Zooming in shows the individual intervals again.

`python3 tests/benchmark_range_offcpu.py BEFORE.html AFTER.html` uses the usual
Timeline drag and reads the panel and CSV. With the off-CPU overlay on, at 4×
CPU throttling in Chromium 145.0.7632.6, three alternating runs produced these
medians on wide ranges with two calls and a single track:

| Waits | Longest frame gap | Range results ready |
| --- | ---: | ---: |
| 200,000 | 1,684.1 → 37.2 ms | 1,704.1 → 70.5 ms |
| 750,000 | 5,915.8 → 63.5 ms | 5,966.3 → 107.4 ms |

With the overlay off or on the real `ping3` trace (129 waits), the metrics
were broadly unchanged. All measured range headings and CSV digests matched.
The narrow synthetic views and the real capture retain pixel equality; the
wide dense markers intentionally change the image, not the captured data.
Local results: `build/benchmarks/20261120-offcpu-dense/`.

### Reuse the wakeup paint plan on an unchanged viewport

Track repaints now keep one wakeup paint plan for the current time window,
scale, scroll position, pane size, track layout and focused task set. Changes
to any of those inputs, inline rows or the priority hover wakeup rebuild the
plan. Turning off the overlay drops it. The cache retains at most 256 KiB of
arrowhead buckets, plus the existing bounded list of individual curves; it
does not retain a new entry for every wakeup. Painting still composites the
arrows in the original order with the current theme and hover accent.

Three alternating Chromium 145 comparisons on the 256,000-wakeup fixture
measured the following median callback totals for 18 warm redraws:

| Distribution | Before | After |
| --- | ---: | ---: |
| Adjacent tracks, grouped by time block | 20.7 ms | 6.9 ms |
| Interleaved across all tracks in each block | 1,042.2 ms | 963.4 ms |

The track pixels matched at six scroll positions in both distributions.
The real `ping3` report matched at six states covering selection, zoom,
scroll and theme at 844×390 and DPR 2; warm redraws did not reread wakeup
records. These results exclude cold plan builds and do not measure input
latency or browser raster time separately.

## Reuse pixel boundaries within one Timeline view

The track canvas still repaints selection, hover, filters and wakeup arrows.
For the same time window and horizontal scale, it reuses the first row
position and the end of each group of subpixel slices. Entries are keyed by
the immutable row, so switching to inline rows cannot reuse a different row's
groups. A time or scale change discards the previous cache. At most 32,768
group ends are retained; any uncached tail uses the original binary search.
Filter representatives and hit testing are unchanged.

In `ping3` (58,539 calls; Chromium 145; 1500 × 900), four slice changes at
the same full view no longer execute the 14,672 pixel-boundary searches
seen before. The track canvas matched at all 36 checked combinations of
selection, zoom/filter state and device-pixel ratio (1, 1.25 and 2). Five
alternating pairs with 20 warm callbacks per state measured median callback
times of 1.4 → 1.2 ms across the full trace, 1.0 → 0.9 ms in a fixed zoom,
and 1.6 → 1.3 ms in the filtered zoom. These times exclude selection detail
updates, browser rasterization and input-to-paint latency.

The 400,000-call synthetic filter benchmark matched canvas output in narrow,
sparse and full views. The full-view warm redraw median went from 0.6 to
0.3 ms; narrow and sparse warm medians were unchanged at the measured
precision. Local measurements: `build/benchmarks/20261119-trace-jump/`.
